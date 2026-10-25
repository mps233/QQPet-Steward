from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from desktop.windows import webview_test_environment as policy


class FakeRegistry:
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    KEY_CREATE_SUB_KEY = 4
    KEY_ENUMERATE_SUB_KEYS = 8
    KEY_WOW64_64KEY = 256
    REG_SZ = 1

    def __init__(self):
        self.keys = {}
        self.writes = []
        self.fail_once = False

    def OpenKey(self, root, path, reserved, access):
        assert root == "HKLM"
        assert access & self.KEY_WOW64_64KEY
        if path not in self.keys:
            raise FileNotFoundError(path)
        return path

    def CreateKeyEx(self, root, path, reserved, access):
        self.keys.setdefault(path, {})
        return path

    def CloseKey(self, key):
        pass

    def QueryValueEx(self, key, name):
        if name not in self.keys[key]:
            raise FileNotFoundError(name)
        return self.keys[key][name]

    def SetValueEx(self, key, name, reserved, kind, value):
        self.writes.append((key, name, kind, value))
        if self.fail_once and key.endswith("UserDataFolder"):
            self.fail_once = False
            raise PermissionError("test setup failure")
        self.keys[key][name] = value, kind

    def DeleteValue(self, key, name):
        if name not in self.keys[key]:
            raise FileNotFoundError(name)
        del self.keys[key][name]

    def QueryInfoKey(self, key):
        prefix = key + "\\"
        children = {path[len(prefix):].split("\\")[0] for path in self.keys
                    if path.startswith(prefix)}
        return len(children), len(self.keys[key]), 0

    def DeleteKeyEx(self, root, path, access, reserved):
        assert not self.QueryInfoKey(path)[0] and not self.keys[path]
        del self.keys[path]


@pytest.fixture
def fake_registry(monkeypatch):
    registry = FakeRegistry()
    monkeypatch.setattr(policy, "_registry", lambda: registry)
    monkeypatch.setattr(policy, "_is_elevated", lambda: True)
    return registry


def context(tmp_path, **kwargs):
    return policy.webview_test_environment(
        executable=tmp_path / "QQPet-Steward.exe",
        user_data_folder=tmp_path / "profile",
        browser_arguments="--remote-debugging-port=23123 --remote-debugging-address=127.0.0.1",
        environ=kwargs.pop("environ", {"GITHUB_ACTIONS": "true"}),
        **kwargs,
    )


@pytest.mark.parametrize("github,elevated", [(False, True), (True, False), (False, False)])
def test_normal_verification_never_touches_registry(tmp_path, monkeypatch, github, elevated):
    monkeypatch.setattr(policy, "_is_elevated", lambda: elevated)
    monkeypatch.setattr(policy, "_registry", lambda: pytest.fail("unexpected registry access"))
    with context(tmp_path, environ={"GITHUB_ACTIONS": str(github).lower()}) as evidence:
        assert evidence["policyApplied"] is False


def test_policies_are_scoped_and_new_empty_keys_are_removed(tmp_path, fake_registry):
    with context(tmp_path) as evidence:
        assert evidence["policyApplied"] is True
        for name in ("AdditionalBrowserArguments", "UserDataFolder"):
            values = fake_registry.keys[policy.POLICY_ROOT + "\\" + name]
            assert set(values) == set(policy.APP_IDS)
        assert all(name != "*" for _key, name, _kind, _value in fake_registry.writes)
    assert evidence["policyRestored"] is True
    assert fake_registry.keys == {}


def test_existing_value_types_and_unrelated_values_survive_body_failure(tmp_path, fake_registry):
    args = policy.POLICY_ROOT + r"\AdditionalBrowserArguments"
    profile = policy.POLICY_ROOT + r"\UserDataFolder"
    parts = policy.POLICY_ROOT.split("\\")
    for end in range(1, len(parts) + 1):
        fake_registry.keys["\\".join(parts[:end])] = {}
    fake_registry.keys[args] = {policy.APP_IDS[0]: ("original", 2), "another.exe": ("untouched", 1)}
    fake_registry.keys[profile] = {policy.APP_IDS[1]: (42, 4)}
    original = deepcopy(fake_registry.keys)
    with pytest.raises(ValueError, match="verification failed"):
        with context(tmp_path):
            raise ValueError("verification failed")
    assert fake_registry.keys == original


def test_partial_setup_failure_restores_every_previous_change(tmp_path, fake_registry):
    fake_registry.fail_once = True
    with pytest.raises(PermissionError):
        with context(tmp_path):
            pytest.fail("setup must not complete")
    assert fake_registry.keys == {}


def test_cleanup_preserves_new_unrelated_policy_values(tmp_path, fake_registry):
    with context(tmp_path):
        path = policy.POLICY_ROOT + r"\UserDataFolder"
        fake_registry.keys[path]["other-app.exe"] = "other-profile", 1
    assert fake_registry.keys[path] == {"other-app.exe": ("other-profile", 1)}


def test_rejects_other_executable_before_any_registry_access(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "_is_elevated", lambda: True)
    monkeypatch.setattr(policy, "_registry", lambda: pytest.fail("unexpected registry access"))
    with pytest.raises(ValueError, match="requires QQPet-Steward.exe"):
        with policy.webview_test_environment(
            executable=Path("another.exe"), user_data_folder=tmp_path / "profile",
            browser_arguments="--remote-debugging-port=23123", environ={"GITHUB_ACTIONS": "true"},
        ):
            pass
