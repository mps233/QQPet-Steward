from __future__ import annotations

import sys

from src.build_info import build_fingerprint


def test_source_mode_has_no_fingerprint() -> None:
    assert build_fingerprint() == ""


def test_reads_bundled_fingerprint(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "QQPetRuntime"
    executable.write_bytes(b"")
    fingerprint = "a" * 64
    (tmp_path / ".build-fingerprint").write_text(fingerprint + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(executable))

    assert build_fingerprint() == fingerprint


def test_rejects_malformed_fingerprint(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "QQPetRuntime"
    executable.write_bytes(b"")
    (tmp_path / ".build-fingerprint").write_text("not-a-fingerprint\n", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(executable))

    assert build_fingerprint() == ""


def test_source_version_matches_release_config() -> None:
    import json
    from pathlib import Path

    from src.build_info import app_version

    config = json.loads(
        (Path(__file__).resolve().parents[1] / "desktop/macos/tauri.conf.json").read_text(
            encoding="utf-8"
        )
    )
    assert app_version() == config["version"]


def test_reads_bundled_version(tmp_path, monkeypatch) -> None:
    from src.build_info import app_version

    executable = tmp_path / "QQPetRuntime"
    executable.write_bytes(b"")
    (tmp_path / ".build-version").write_text("1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(executable))

    assert app_version() == "1.2.3"


def test_rejects_malformed_bundled_version(tmp_path, monkeypatch) -> None:
    from src.build_info import app_version

    executable = tmp_path / "QQPetRuntime"
    executable.write_bytes(b"")
    (tmp_path / ".build-version").write_text("nightly\n", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert app_version() == ""
