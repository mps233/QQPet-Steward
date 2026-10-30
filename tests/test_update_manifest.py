from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "make_update_manifest.py"
SPEC = importlib.util.spec_from_file_location("make_update_manifest", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_manifest_reads_signature_content(tmp_path) -> None:
    signature = tmp_path / "app.tar.gz.sig"
    signature.write_text("signed-bytes\n", encoding="utf-8")
    manifest = MODULE.build_manifest(
        "0.8.5",
        "notes",
        "2026-10-30T00:00:00Z",
        [("darwin-aarch64", "https://example.com/app.tar.gz", signature)],
    )
    assert manifest == {
        "version": "0.8.5",
        "notes": "notes",
        "pub_date": "2026-10-30T00:00:00Z",
        "platforms": {
            "darwin-aarch64": {
                "signature": "signed-bytes",
                "url": "https://example.com/app.tar.gz",
            }
        },
    }


def test_rejects_insecure_urls(tmp_path) -> None:
    signature = tmp_path / "app.sig"
    signature.write_text("sig", encoding="utf-8")
    try:
        MODULE.build_manifest(
            "0.8.5", "", "2026-10-30T00:00:00Z", [("windows-x86_64", "http://example.com/app.exe", signature)]
        )
    except ValueError as error:
        assert "https" in str(error)
    else:
        raise AssertionError("http URL was accepted")


def test_cli_writes_json(tmp_path) -> None:
    signature = tmp_path / "app.sig"
    signature.write_text("sig", encoding="utf-8")
    output = tmp_path / "latest.json"
    code = MODULE.main([
        "--version", "0.8.5",
        "--output", str(output),
        "--platform", "windows-x86_64", "https://example.com/app.exe", str(signature),
    ])
    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["version"] == "0.8.5"
    assert payload["platforms"]["windows-x86_64"]["signature"] == "sig"
