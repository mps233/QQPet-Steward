from __future__ import annotations

import subprocess

from src.adb import device as adb_device


def test_parse_device_list_keeps_duplicate_transport_metadata():
    rows = adb_device._parse_device_list(
        "List of devices attached\n"
        "1f584daf device usb:18087936X transport_id:2\n"
        "1f584daf device usb:17956864X product:OnePlus9Pro_CH "
        "model:LE2120 device:OnePlus9Pro transport_id:1\n"
    )

    assert [(r.serial, r.transport_id, r.model) for r in rows] == [
        ("1f584daf", 2, ""),
        ("1f584daf", 1, "LE2120"),
    ]


def test_repair_duplicate_serial_detaches_only_non_android_transport(monkeypatch):
    dev = adb_device.Device("/fake/adb", "1f584daf")
    phone = adb_device._DeviceEntry(
        "1f584daf", "device", transport_id=1, model="LE2120")
    data_card = adb_device._DeviceEntry(
        "1f584daf", "device", transport_id=2)
    monkeypatch.setattr(dev, "_is_android_entry", lambda row: row is phone)
    detached = []
    monkeypatch.setattr(dev, "_detach_transport", lambda transport_id: (
        detached.append(transport_id) or True))

    dev._repair_duplicate_serial([phone, data_card])

    assert detached == [2]


def test_repair_duplicate_serial_does_not_detach_two_android_transports(monkeypatch):
    dev = adb_device.Device("/fake/adb", "1f584daf")
    rows = [
        adb_device._DeviceEntry("1f584daf", "device", transport_id=1, model="LE2120"),
        adb_device._DeviceEntry("1f584daf", "device", transport_id=2, model="LE2120"),
    ]
    monkeypatch.setattr(dev, "_is_android_entry", lambda _row: True)
    detached = []
    monkeypatch.setattr(dev, "_detach_transport", lambda transport_id: (
        detached.append(transport_id) or True))

    dev._repair_duplicate_serial(rows)

    assert detached == []


def test_ensure_connected_repairs_duplicate_before_selecting(monkeypatch):
    dev = adb_device.Device("/fake/adb", "1f584daf")
    calls = []
    duplicate_rows = [
        adb_device._DeviceEntry("1f584daf", "device", transport_id=1, model="LE2120"),
        adb_device._DeviceEntry("1f584daf", "device", transport_id=2),
    ]
    monkeypatch.setattr(dev, "_run_server", lambda *args, **kwargs: subprocess.CompletedProcess(
        ["adb", *args], 0, b"", b""))
    monkeypatch.setattr(dev, "_device_entries", lambda: duplicate_rows)
    monkeypatch.setattr(dev, "_is_android_entry", lambda row: row.transport_id == 1)
    monkeypatch.setattr(dev, "_detach_transport", lambda transport_id: (
        calls.append(transport_id) or True))
    monkeypatch.setattr(dev, "online_devices", lambda: ["1f584daf"])

    assert dev.ensure_connected() == "1f584daf"
    assert calls == [2]


def test_ensure_connected_reports_unresolved_duplicate(monkeypatch):
    dev = adb_device.Device("/fake/adb", "1f584daf")
    monkeypatch.setattr(dev, "_run_server", lambda *args, **kwargs: subprocess.CompletedProcess(
        ["adb", *args], 0, b"", b""))
    monkeypatch.setattr(dev, "_repair_duplicate_serial", lambda: None)
    monkeypatch.setattr(dev, "online_devices", lambda: ["1f584daf", "1f584daf"])

    try:
        dev.ensure_connected()
    except adb_device.AdbError as e:
        assert "对应多条在线 ADB transport" in str(e)
    else:
        raise AssertionError("duplicate serial should not be silently accepted")
