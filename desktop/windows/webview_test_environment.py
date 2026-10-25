"""Scoped WebView2 test policies for elevated GitHub Actions runners only."""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

POLICY_ROOT = r"SOFTWARE\Policies\Microsoft\Edge\WebView2"
APP_IDS = ("QQPet-Steward.exe", "ai.qqpet.dashboard.desktop")
_MISSING = object()


def _is_elevated() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    security.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    security.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        elevated, size = wintypes.DWORD(), wintypes.DWORD()
        if not security.GetTokenInformation(
            token, 20, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(size),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(elevated.value)
    finally:
        kernel.CloseHandle(token)


def _registry():
    import winreg
    return winreg


def _ensure_key(registry, path, access, created):
    parts = path.split("\\")
    for end in range(1, len(parts) + 1):
        current = "\\".join(parts[:end])
        try:
            key = registry.OpenKey(registry.HKEY_LOCAL_MACHINE, current, 0, access)
        except FileNotFoundError:
            key = registry.CreateKeyEx(registry.HKEY_LOCAL_MACHINE, current, 0, access)
            created.append(current)
        registry.CloseKey(key)
    return registry.OpenKey(registry.HKEY_LOCAL_MACHINE, path, 0, access)


@contextmanager
def webview_test_environment(
    executable: Path,
    user_data_folder: Path,
    browser_arguments: str,
    *,
    environ=None,
):
    """Restore every prior policy value after the verifier's processes exit."""
    environment = os.environ if environ is None else environ
    enabled = str(environment.get("GITHUB_ACTIONS", "")).casefold() == "true"
    elevated = _is_elevated()
    evidence = {"policyApplied": False, "elevated": elevated, "githubActions": enabled}
    if not enabled or not elevated:
        yield evidence
        return
    if Path(executable).name.casefold() != APP_IDS[0].casefold():
        raise ValueError("scoped WebView2 test policy requires QQPet-Steward.exe")
    if not Path(user_data_folder).is_absolute() or "\0" in str(user_data_folder):
        raise ValueError("the isolated WebView2 profile must be an absolute path")
    if not isinstance(browser_arguments, str) or "\0" in browser_arguments:
        raise ValueError("invalid WebView2 test browser arguments")
    registry = _registry()
    access = (registry.KEY_QUERY_VALUE | registry.KEY_SET_VALUE
              | registry.KEY_CREATE_SUB_KEY | registry.KEY_ENUMERATE_SUB_KEYS
              | registry.KEY_WOW64_64KEY)
    created, changes = [], []
    evidence["policyScope"] = list(APP_IDS)
    try:
        for name, value in (
            ("AdditionalBrowserArguments", browser_arguments),
            ("UserDataFolder", str(user_data_folder)),
        ):
            path = POLICY_ROOT + "\\" + name
            key = _ensure_key(registry, path, access, created)
            try:
                for app_id in APP_IDS:
                    try:
                        previous = registry.QueryValueEx(key, app_id)
                    except FileNotFoundError:
                        previous = _MISSING
                    changes.append((path, app_id, previous))
                    registry.SetValueEx(key, app_id, 0, registry.REG_SZ, value)
            finally:
                registry.CloseKey(key)
        evidence["policyApplied"] = True
        yield evidence
    finally:
        errors = []
        for path, app_id, previous in reversed(changes):
            try:
                key = registry.OpenKey(registry.HKEY_LOCAL_MACHINE, path, 0, access)
                try:
                    if previous is _MISSING:
                        try:
                            registry.DeleteValue(key, app_id)
                        except FileNotFoundError:
                            pass
                    else:
                        value, kind = previous
                        registry.SetValueEx(key, app_id, 0, kind, value)
                finally:
                    registry.CloseKey(key)
            except FileNotFoundError:
                if previous is not _MISSING:
                    errors.append(path)
            except OSError:
                errors.append(path)
        for path in reversed(created):
            try:
                key = registry.OpenKey(registry.HKEY_LOCAL_MACHINE, path, 0, access)
                try:
                    children, values, _modified = registry.QueryInfoKey(key)
                finally:
                    registry.CloseKey(key)
                if not children and not values:
                    registry.DeleteKeyEx(registry.HKEY_LOCAL_MACHINE, path,
                                         registry.KEY_WOW64_64KEY, 0)
            except FileNotFoundError:
                pass
            except OSError:
                errors.append(path)
        evidence["policyRestored"] = not errors
        if errors:
            raise RuntimeError("could not restore scoped WebView2 test policies")
