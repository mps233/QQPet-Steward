# QQPet-Steward for Windows (portable)

This is the Windows x64 portable edition. It includes Python, the dashboard,
scheduler, ADB, OCR models, and device-control resources. Extract the complete
directory and run `start_portable.bat`; the interface is the local browser
dashboard. An MSI installer and a native desktop shell are not included.

## Product shape

The portable package contains two executables:

* `QQPetCopilot.exe` - the dashboard and local web UI;
* `QQPetCopilotScheduler.exe` - the scheduler entry point launched by the
  dashboard.

`src.runtime_process` chooses `.venv\Scripts\python.exe` in a source checkout
and the sibling scheduler executable in a frozen build.  It launches the
scheduler in a new process group without a console, writes stdout/stderr to
`runs\runner_console.log`, and stops it gracefully with `CTRL_BREAK_EVENT`
before falling back to `taskkill /T` and `taskkill /T /F`.

## Build a portable zip

Run these commands in a **Windows** checkout with Python 3.13 and Node.js
available:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r desktop\windows\requirements-build.txt

# Frontend build; dashboard.py serves web\dist directly.
Push-Location web
corepack enable
pnpm install --frozen-lockfile
pnpm run build
Pop-Location

# Download and verify every required resource, then freeze on Windows.
.\.venv\Scripts\python.exe desktop\windows\build_portable.py
```

The default output is:

```text
dist\QQPet-Steward_<version>_windows_x64\QQPetCopilot.exe
dist\QQPet-Steward_<version>_windows_x64\QQPetCopilotScheduler.exe
dist\QQPet-Steward_<version>_windows_x64.zip
dist\QQPet-Steward_<version>_windows_x64.zip.sha256
```

Use `--no-fetch` to skip network downloads, `--clean` to remove `build\` and the
portable output directory before building, and `--no-zip` to keep only the
directory. `--check` performs read-only validation of an already prepared
checkout. Missing resources and failed downloads block packaging.

The `Build Windows portable` GitHub Actions workflow builds on a Windows x64
runner, tests the helpers, then runs both frozen executables and local HTTP
checks before uploading a downloadable artifact.

## Start and stop

From the package root:

```bat
start_portable.bat
```

Or from PowerShell:

```powershell
.\start_portable.ps1
```

Open `http://127.0.0.1:8787` (or the machine's LAN address).  The first access
code is stored in `runs\dashboard_access.token`.  The dashboard's **Start** and
**Stop** buttons drive the sibling scheduler executable.

To stop the dashboard itself:

```powershell
.\stop_portable.ps1
```

The scripts stop only the PID recorded in `runs\dashboard.pid`; they do not
kill unrelated Python processes.

## Portable data and ADB

Portable mode keeps mutable data beside the executables:

```text
config.yaml
runs\
  dashboard_access.token
  dashboard.pid
  runner_console.log
  logs\
  models\rapidocr\
```

This is safe only in a writable user directory.  Do **not** place the portable
package under `C:\Program Files`; installed mode is the second milestone and
uses `%LOCALAPPDATA%\QQPetCopilot`.

The launcher sets `QQPET_DATA_DIR` before importing the dashboard and rebinds
every `runs\...` / `config.yaml` path to that directory.  This keeps
`web\dist`, `static`, and OCR models in the read-only bundle while progress,
logs, access tokens, and configuration remain writable and upgrade-safe.

ADB can be discovered from `PATH` or configured in `config.yaml`.  Bundled
`resources\platform-tools\adb.exe` inside the frozen resource directory is
preferred when present. The builder obtains the Windows ADB executable and its
two DLLs through the existing scrcpy resource downloader; it does not include
the unused scrcpy video tools in the portable package.

## Assets and models

The spec bundles:

* `web\dist\` (the React dashboard);
* `static\` (PWA icons and room backgrounds);
* `config.example.yaml`;
* exactly three verified OCR models under `models\rapidocr\`;
* ADB and its DLLs, four Android minitouch binaries, two version-matched Frida
  server archives, and the Frida Java bridge;
* the public task-image templates used by the scheduler.

`web\dist`, downloaded models, and `resources` stay outside Git. The builder
downloads OCR assets into an isolated build directory and copies only selected
files. It rejects incomplete resources instead of publishing a reduced package.
Real `config.yaml`, logs, progress, access codes, and pairing records are never
build inputs.

## Why two executables?

A frozen dashboard's `sys.executable` is `QQPetCopilot.exe`.  Appending
`scenarios\runner.py` to it would relaunch the dashboard, not the scheduler.
The scheduler executable is built from `scheduler_launcher.py`, which reads
`QQPET_DATA_DIR`, rebinds `src.config` before runner import, and executes
`scenarios.runner` with `run_name="__main__"`.  This preserves the no-argument
scheduler CLI while keeping the runner inside PyInstaller's PYZ.  Source
checkouts continue to use the project venv and `runner.py` directly.

It also preserves runner's command-line options (`--emulator`,
`--emulator-device`, `--skip-opener`, and `--test`) because `build_scheduler_argv`
passes `extra_args` after the frozen executable unchanged.

## Verification status

The native verifier is intended to run on Windows after the build:

```powershell
.\.venv\Scripts\python.exe desktop\windows\verify_portable.py `
  --directory dist\QQPet-Steward_<version>_windows_x64 `
  --report build\windows-verification.json
```

It checks AMD64 executables, rejects user data in the package, runs both frozen
offline self-tests, then starts an isolated local dashboard to check the React
page, assets, client protocol, authentication, and status response. Its data and
access code stay in temporary directories, outside the portable output.

This does not run an Android device or exercise emulator recovery, real device
input, firewall prompts, or all Windows desktop versions. The release binaries
are not Authenticode-signed; Windows may show an unknown-publisher warning.

The main `dashboard.py` already uses:

* `runtime_process.build_scheduler_argv` for venv/frozen selection;
* `runtime_process.spawn_detached_scheduler` for platform-specific detachment;
* `runtime_process.stop_process` for the platform-specific stop ladder;
* `runtime_process.scheduler_environment` to pass the writable data root.
