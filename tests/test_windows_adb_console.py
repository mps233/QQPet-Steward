"""Exercise real Windows console creation through isolated dashboard APIs."""
from __future__ import annotations

import base64
from http.client import HTTPConnection
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from PIL import Image
import pytest
import yaml


REPO = Path(__file__).resolve().parents[1]
SERIAL = "FAKE-ADB-NO-DEVICE"
ADDRESS = "127.0.0.1:5555"

FAKE_ADB_SOURCE = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

class FakeAdb {
    [DllImport("kernel32.dll")]
    static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr hwnd);

    static int Main(string[] args) {
        IntPtr hwnd = GetConsoleWindow();
        string log = Environment.GetEnvironmentVariable("QQPET_FAKE_ADB_LOG");
        File.AppendAllText(log,
            Process.GetCurrentProcess().Id + "\t" + hwnd.ToInt64() + "\t" +
            IsWindowVisible(hwnd) + "\t" + String.Join(" ", args) + "\n");
        if (Array.IndexOf(args, "devices") >= 0) {
            Console.WriteLine("List of devices attached");
            Console.WriteLine("FAKE-ADB-NO-DEVICE\tdevice product:fake model:Test_Phone transport_id:1");
        } else if (Array.IndexOf(args, "connect") >= 0) {
            Console.WriteLine("connected to 127.0.0.1:5555");
        } else if (Array.IndexOf(args, "screencap") >= 0) {
            byte[] image = Convert.FromBase64String("PNG_BASE64");
            Stream output = Console.OpenStandardOutput();
            output.Write(image, 0, image.Length);
            output.Flush();
        }
        return 0;
    }
}
"""


def _read_calls(path: Path) -> list[dict]:
    calls = []
    if path.exists():
        for line in path.read_text("utf-8").splitlines():
            pid, console, visible, command = line.split("\t", 3)
            calls.append({
                "pid": int(pid), "console": int(console),
                "visible": visible == "True", "command": command,
            })
    return calls


@pytest.mark.skipif(sys.platform != "win32", reason="requires actual Windows process creation")
def test_dashboard_adb_apis_create_no_console_from_a_windowed_backend(tmp_path):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    assert pythonw.is_file(), "Windows regression verification requires the venv pythonw.exe"
    compiler = (Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64"
                / "v4.0.30319" / "csc.exe")
    assert compiler.is_file(), "Windows regression verification requires the system C# compiler"
    no_window = subprocess.CREATE_NO_WINDOW
    image = io.BytesIO()
    Image.new("RGB", (2, 3), (248, 197, 41)).save(image, format="PNG")
    source = tmp_path / "fake-adb.cs"
    source.write_text(
        FAKE_ADB_SOURCE.replace("PNG_BASE64", base64.b64encode(image.getvalue()).decode("ascii")),
        encoding="ascii",
    )
    fake_adb = tmp_path / "fake-adb.exe"
    subprocess.run(
        [str(compiler), "/nologo", "/target:exe", "/platform:x64",
         "/out:" + str(fake_adb), str(source)],
        capture_output=True, check=True, timeout=30, creationflags=no_window,
    )

    data = tmp_path / "user-data"
    data.mkdir()
    (data / "config.yaml").write_text(yaml.safe_dump({
        "adb": {"path": str(fake_adb), "device_serial": SERIAL},
        "tgbot": {"enabled": False},
    }), encoding="utf-8")
    calls_file = tmp_path / "fake-adb-calls.tsv"
    env = {name: value for name, value in os.environ.items()
           if not name.upper().startswith("QQPET_")}
    env.update({
        "QQPET_DATA_DIR": str(data), "QQPET_DISABLE_TG": "1",
        "QQPET_FAKE_ADB_LOG": str(calls_file), "QQPET_PORTABLE": "1",
        "PYTHONPATH": str(REPO), "PYTHONUTF8": "1",
    })
    # A console executable launched without flags by a GUI parent must expose
    # a console HWND. This proves that the later zero-HWND checks are sensitive.
    control = (
        "import subprocess\n"
        f"subprocess.run([{str(fake_adb)!r}, 'positive-control'], "
        "capture_output=True, check=True, timeout=15)\n"
    )
    subprocess.run(
        [str(pythonw), "-c", control], cwd=tmp_path, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=True, timeout=20,
    )
    positive = _read_calls(calls_file)
    assert len(positive) == 1 and positive[0]["command"] == "positive-control"
    assert positive[0]["console"] != 0, "unflagged positive control did not obtain a console"

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    code = f"""
import sys
sys.frozen = True
sys._MEIPASS = {str(REPO)!r}
sys.argv = ["QQPetCopilot.exe", "--bind", "127.0.0.1", "--port", {str(port)!r}]
from desktop.windows.dashboard_launcher import main
raise SystemExit(main())
"""
    with (tmp_path / "dashboard.log").open("wb") as log:
        backend = subprocess.Popen(
            [str(pythonw), "-c", code], cwd=tmp_path, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        )
        try:
            deadline = time.monotonic() + 30
            token_file = data / "runs" / "dashboard_access.token"
            while True:
                assert backend.poll() is None, "isolated windowed dashboard exited during startup"
                if token_file.exists():
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        pass
                assert time.monotonic() < deadline, "windowed dashboard startup timed out"
                time.sleep(0.05)
            key = token_file.read_text("utf-8").strip()

            def request(path, payload=None):
                connection = HTTPConnection("127.0.0.1", port, timeout=10)
                headers = {"Authorization": f"Bearer {key}",
                           "Origin": f"http://127.0.0.1:{port}"}
                body = None
                if payload is not None:
                    headers["Content-Type"] = "application/json"
                    body = json.dumps(payload).encode("utf-8")
                try:
                    connection.request("POST" if payload is not None else "GET",
                                       path, body=body, headers=headers)
                    response = connection.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    connection.close()

            status, _headers, body = request("/api/adb")
            assert status == 200
            devices = json.loads(body)
            assert not devices["error"]
            assert devices["devices"] == [{
                "serial": SERIAL, "state": "device", "product": "fake", "model": "Test Phone",
            }]
            status, _headers, body = request("/api/adb/connect", {"addr": ADDRESS})
            assert status == 200
            connected = json.loads(body)
            assert connected["ok"] is True and connected["addr"] == ADDRESS
            status, headers, body = request("/api/screenshot")
            assert status == 200 and headers["Content-Type"] == "image/jpeg"
            with Image.open(io.BytesIO(body)) as screenshot:
                assert screenshot.format == "JPEG" and screenshot.size == (2, 3)

            calls = _read_calls(calls_file)[1:]
            assert [call["command"] for call in calls] == [
                "devices -l", f"connect {ADDRESS}", f"-s {SERIAL} exec-out screencap -p",
            ]
            assert all(call["pid"] > 0 and call["console"] == 0 and not call["visible"]
                       for call in calls), "an ADB API call allocated a Windows console"
            assert not (data / "runs" / "runner.pid").exists()
        finally:
            if backend.poll() is None:
                backend.terminate()
                try:
                    backend.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    backend.kill()
                    backend.wait(timeout=5)
