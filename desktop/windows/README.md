# QQPet-Steward for Windows

The Windows x64 edition is a complete native App. Install
`QQPet-Steward_<version>_windows_x64-setup.exe` and open **QQPet-Steward** from
the Start menu. It opens its own Tauri window, prepares the local backend and
signs in automatically. Python, ADB, OCR models and device-control resources
are included; no server URL or separate Python installation is required.

The installer runs for the current Windows user. If Microsoft WebView2 is
missing, the installer downloads and installs it. The release binaries are
not Authenticode-signed; Windows may show an unknown-publisher warning.

## Window, tray and background tasks

Closing the main window hides it and keeps the paw icon in the system tray.
Use the paw menu's **Open main window** action to return to the App. Opening
QQPet-Steward again also brings back the existing window.

The dashboard backend and scheduler run independently of the native window.
Closing the window or choosing **Exit App** leaves existing tasks running.
Use **Stop** in the dashboard to stop the scheduler.

## Data and bundled resources

The native App stores configuration and progress in:

```text
%LOCALAPPDATA%\QQPetCopilot\
  config.yaml
  runs\
    dashboard_access.token
    dashboard_console.log
    runner_console.log
    logs\
    models\rapidocr\
```

`QQPET_DATA_DIR` can select another writable data directory. When a compatible
local backend is already running, the App can continue using its data.
User data stays outside the installation and the read-only runtime resources.

The App copies its internal Python backend into a versioned runtime cache
before starting it. That cache uses a build fingerprint so a running backend
does not depend on files in the installer directory.

The runtime includes the React dashboard, room backgrounds, the public task
templates, exactly three verified OCR models, Windows ADB and both DLLs, four
Android minitouch binaries, two version-matched Frida server archives and the
Frida Java bridge. Real configuration, progress, access codes, logs and pairing
records are never build inputs.

## Build the native App

Build on **Windows x64** with Python 3.13, Node.js, Rust's MSVC toolchain and the
Visual Studio C++ build tools / Windows SDK available. The GitHub Actions
`windows-2025` runner provides the Windows build tools.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r desktop\windows\requirements-build.txt

npm install --global pnpm@10.18.0
pnpm --dir web install --frozen-lockfile
pnpm --dir web run build
pnpm --dir desktop/macos install --frozen-lockfile

rustup toolchain install stable --profile minimal
rustup default stable
.\.venv\Scripts\python.exe desktop\windows\build_native.py
```

The builder reads the release version from `desktop/macos/tauri.conf.json`.
The Tauri crate and App assets are shared with macOS; the Windows override
selects a native Windows window, a generated ICO and a per-user NSIS installer.
The primary outputs are:

```text
dist\QQPet-Steward_<version>_windows_x64-setup.exe
dist\QQPet-Steward_<version>_windows_x64-setup.exe.sha256
```

An auxiliary `QQPet-Steward_<version>_windows_x64-native.zip` contains the
native App executable and its internal backend. It is useful for validation
without installing. The installer is the normal distribution entry point.

`build_native.py` first builds and verifies the internal Python runtime, stages
it under `desktop/macos/target/runtime/QQPetRuntime`, writes its fingerprint,
generates the ICO from the existing App PNG, then builds Tauri and NSIS.
Downloads use the existing resource-fetch scripts; missing or corrupt
resources stop the build.

Useful options:

* `--check`: inspect source configuration and tool availability without writing;
* `--prepare-only`: build and verify Python, then stage resources and generate the icon;
* `--skip-runtime-build`: verify and reuse an existing Python build before building Tauri;
* `--no-fetch`: require already prepared resources;
* `--no-native-zip`: produce only the installer download and its checksum.

## Verification

The `Build Windows desktop App` workflow builds the frontend and internal
Python executables, runs their offline and HTTP checks, builds Tauri / NSIS and
tests the Rust helpers. It then silently installs the generated installer,
checks its App files and Start menu shortcut, and verifies the installed backend
and actual native window. Only a successful run uploads the installer and
verification reports.

For native GUI verification, install the Python Playwright package. A separate
browser download is not needed: the tool connects to the App's WebView2.

```powershell
.\.venv\Scripts\python.exe -m pip install "playwright>=1.50,<2"
.\.venv\Scripts\python.exe desktop\windows\verify_native.py `
  --executable desktop\macos\target\release\QQPet-Steward.exe `
  --report build\windows-native-verification.json `
  --screenshot build\windows-native-app.png
```

The verifier uses temporary data, runtime-cache and WebView2-profile
directories. It checks the native window, automatic local login, backend
startup, single-instance behavior and close / reopen behavior. It records
whether a real window screenshot was available. Reports and screenshots stay
outside the App bundle.

These checks do not run an Android device or exercise emulator recovery,
device input, firewall prompts or every Windows desktop version. Those paths
still need Windows device testing.

## Internal Python runtime

`build_portable.py --no-zip` is retained as the native builder's Python
freezing step. Its two executables are internal components:

* `QQPetCopilot.exe`: local dashboard backend;
* `QQPetCopilotScheduler.exe`: scheduler launched by that backend.

Both use the same dependency graph and share `_internal`. The builder verifies
identical shared files before merging, excludes unused OCR backends and large
wheel models, and includes only the required resource files. Browser startup
scripts from the development portable package are not staged into the native
App.

`src.runtime_process` uses the sibling scheduler executable in a frozen build
and the project venv in a source checkout. It starts the scheduler in its own
process group, writes `runs\runner_console.log`, and stops it with
`CTRL_BREAK_EVENT` before falling back to `taskkill /T` and `taskkill /T /F`.
