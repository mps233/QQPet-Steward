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
