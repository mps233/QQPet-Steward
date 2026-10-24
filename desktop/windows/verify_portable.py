"""Verify a frozen Windows portable bundle without using real user data."""
from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


EXECUTABLES = ("QQPetCopilot.exe", "QQPetCopilotScheduler.exe")


class VerificationError(RuntimeError):
    """A release verification failure that is safe to include in the report."""


def check_pe(path: Path) -> dict:
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise VerificationError(f"{path.name}: missing DOS executable header")
        offset = struct.unpack_from("<I", header, 60)[0]
        if offset < 64 or offset > path.stat().st_size - 26:
            raise VerificationError(f"{path.name}: invalid PE header offset")
        stream.seek(offset)
        pe = stream.read(26)
    if pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664:
        raise VerificationError(f"{path.name}: executable is not AMD64 PE")
    if struct.unpack_from("<H", pe, 24)[0] != 0x20B:
        raise VerificationError(f"{path.name}: executable is not PE32+")
    return {"executable": path.name, "machine": "AMD64", "format": "PE32+"}


def check_package(directory: Path) -> dict:
    if not directory.is_dir():
        raise VerificationError("portable bundle directory does not exist")
    file_count = 0
    for path in directory.rglob("*"):
        relative = path.relative_to(directory)
        parts = tuple(part.casefold() for part in relative.parts)
        if path.is_symlink():
            raise VerificationError(f"unexpected bundle symlink: {relative.as_posix()}")
        if not path.is_file():
            continue
        file_count += 1
        if ("runs" in parts or path.suffix.casefold() == ".token"
                or (path.name.casefold() in {"config.yaml", "config.yml"}
                    and parts != ("_internal", "rapidocr", "config.yaml"))):
            raise VerificationError(f"user data found in bundle: {relative.as_posix()}")
    return {"files": file_count, "userDataIncluded": False}


def child_environment(data_root: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items()
           if not name.upper().startswith("QQPET_")
           and name.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}}
    env.update({
        "QQPET_DATA_DIR": str(data_root),
        "QQPET_PORTABLE": "1",
        "QQPET_DISABLE_TG": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "NO_PROXY": "127.0.0.1,localhost",
    })
    return env


def check_self_test(executable: Path, sandbox: Path) -> dict:
    sandbox.mkdir()
    data_root = sandbox / "data"
    data_root.mkdir()
    report_file = sandbox / "self-test.json"
    result = subprocess.run(
        [str(executable), "--self-test", "--self-test-output", str(report_file)],
        cwd=data_root, env=child_environment(data_root),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180,
        check=False,
    )
    if result.returncode:
        raise VerificationError(f"{executable.name}: offline self-test exited {result.returncode}")
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise VerificationError(f"{executable.name}: offline self-test report is missing or invalid")
    expected = {"ok": True, "frozen": True, "deviceConnected": False, "telegramStarted": False}
    if (not isinstance(report, dict)
            or any(report.get(name) is not value for name, value in expected.items())):
        raise VerificationError(f"{executable.name}: offline self-test safety checks failed")
    return {"executable": executable.name, "exitCode": 0, **expected}


def _request(opener, base: str, path: str, payload: dict | None = None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(base + path, data=body)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
        request.add_header("Origin", base)
    try:
        response = opener.open(request, timeout=5)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read()


def _json(body: bytes, route: str) -> dict:
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise VerificationError(f"{route}: response is not JSON")
    if not isinstance(value, dict):
        raise VerificationError(f"{route}: response is not an object")
    return value


def _stop_process(process) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=8)


def check_http(executable: Path, sandbox: Path, *, timeout: float = 60) -> dict:
    sandbox.mkdir()
    data_root = sandbox / "data"
    data_root.mkdir()
    # Reserve an ephemeral loopback port, then release it for the child server.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    with (sandbox / "dashboard.log").open("wb") as output:
        process = subprocess.Popen(
            [str(executable), "--bind", "127.0.0.1", "--port", str(port)],
            cwd=data_root, env=child_environment(data_root),
            stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + timeout
            while True:
                if process.poll() is not None:
                    raise VerificationError("dashboard exited before becoming ready")
                try:
                    status, headers, body = _request(opener, base, "/")
                    if status == 200:
                        break
                except (URLError, TimeoutError, OSError):
                    pass
                if time.monotonic() >= deadline:
                    raise VerificationError("dashboard did not become ready before timeout")
                time.sleep(0.1)

            html = body.decode("utf-8")
            assets = re.findall(r"""(?:src|href)=["'](/assets/[^"']+)["']""", html)
            if ("text/html" not in headers.get("Content-Type", "")
                    or not re.search(r"""id=["']root["']""", html)
                    or not any(name.endswith(".js") for name in assets)):
                raise VerificationError("root route did not serve the React application")
            for asset in assets:
                status, _, content = _request(opener, base, asset)
                if status != 200 or not content:
                    raise VerificationError("React application asset could not be served")

            status, _, body = _request(opener, base, "/api/client/meta")
            meta = _json(body, "/api/client/meta")
            if (status != 200 or meta.get("ok") is not True
                    or meta.get("protocol_version") != 1
                    or not isinstance(meta.get("capabilities"), list)):
                raise VerificationError("public client metadata is invalid")
            status, _, _ = _request(opener, base, "/api/data")
            if status != 401:
                raise VerificationError("dashboard data did not require authentication")

            token_file = data_root / "runs" / "dashboard_access.token"
            if not token_file.is_file() or not token_file.read_text(encoding="utf-8").strip():
                raise VerificationError("dashboard did not create its isolated access code")
            key = token_file.read_text(encoding="utf-8").strip()
            status, _, body = _request(opener, base, "/api/auth", {"key": key})
            if status != 200 or _json(body, "/api/auth").get("ok") is not True:
                raise VerificationError("dashboard access-code login failed")
            status, _, body = _request(opener, base, "/api/data")
            data = _json(body, "/api/data")
            if (status != 200 or not isinstance(data.get("now"), str)
                    or any(not isinstance(data.get(name), dict)
                           for name in ("scheduler", "status", "progress", "config", "editable"))
                    or data["scheduler"].get("alive") is not False
                    or data["scheduler"].get("pid") is not None):
                raise VerificationError("authenticated dashboard data is invalid or scheduler is running")
            if (data_root / "runs" / "runner.pid").exists():
                raise VerificationError("HTTP smoke test unexpectedly created a scheduler PID file")
            return {
                "reactPage": True, "reactAssets": len(assets), "clientProtocol": 1,
                "unauthenticatedDataStatus": 401, "cookieLogin": True,
                "authenticatedDataStatus": 200, "schedulerStarted": False,
            }
        finally:
            _stop_process(process)


def verify(directory: Path) -> dict:
    report = {"ok": False, "platform": sys.platform, "checks": []}

    def run(name, callback):
        try:
            details = callback()
        except (OSError, ValueError, VerificationError, subprocess.SubprocessError) as exc:
            # Child output and auth responses may contain credentials; keep
            # reports limited to our own messages and non-secret check values.
            error = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
            report["checks"].append({"name": name, "ok": False, "error": error})
            return False
        report["checks"].append({"name": name, "ok": True, **details})
        return True

    if not run("package-contents", lambda: check_package(directory)):
        return report
    for name in EXECUTABLES:
        if not run(f"pe-{name}", lambda name=name: check_pe(directory / name)):
            return report
    if sys.platform != "win32":
        report["checks"].append({
            "name": "native-windows", "ok": False,
            "error": "frozen executable verification must run on native Windows",
        })
        return report
    with tempfile.TemporaryDirectory(prefix="qqpet-portable-verify-") as temporary:
        sandbox = Path(temporary)
        for index, name in enumerate(EXECUTABLES):
            if not run(f"self-test-{name}", lambda index=index, name=name:
                       check_self_test(directory / name, sandbox / f"self-test-{index}")):
                return report
        if not run("dashboard-http", lambda:
                   check_http(directory / EXECUTABLES[0], sandbox / "http")):
            return report
    if not run("package-still-clean", lambda: check_package(directory)):
        return report
    report["ok"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    directory = args.directory.resolve()
    report_path = args.report.resolve()
    if report_path.is_relative_to(directory):
        parser.error("--report must be outside the portable bundle")
    report = verify(directory)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
