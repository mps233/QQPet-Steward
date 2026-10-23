# Windows hosted edition (portable milestone)

This directory is the **Windows first milestone**: a portable, uninstalled
directory build.  MSI/installer, uninstall, startup registration, and
auto-start are the **second milestone** and are intentionally not implemented
here.

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
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install pyinstaller

# Frontend build; dashboard.py serves web\dist directly.
Push-Location web
corepack enable
pnpm install --frozen-lockfile
pnpm run build
Pop-Location

# Optional local resources; failures are reported but do not hide missing assets.
.\.venv\Scripts\python.exe tools\fetch_ocr_models.py
.\.venv\Scripts\python.exe tools\fetch_minitouch.py --arch x86_64 arm64-v8a
.\.venv\Scripts\python.exe tools\fetch_scrcpy.py

# Static validation always runs; this command builds only on Windows.
.\.venv\Scripts\python.exe desktop\windows\build_portable.py --check
.\.venv\Scripts\python.exe desktop\windows\build_portable.py
```

The default output is:

```text
dist\QQPetCopilot-Windows-x64\QQPetCopilot.exe
dist\QQPetCopilot-Windows-x64\QQPetCopilotScheduler.exe
dist\QQPetCopilot-Windows-x64.zip
```

Use `--no-fetch` to skip network downloads, `--clean` to remove `build\` and the
portable output directory before building, and `--no-zip` to keep only the
directory.

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
`resources\scrcpy-win64\adb.exe` is preferred when present.  The build script
copies `resources\` when it exists but does not fabricate or download ADB
silently; `fetch_scrcpy.py` is the explicit preparation step.

## Assets and models

The spec bundles:

* `web\dist\` (the React dashboard);
* `static\` (PWA icons and room backgrounds);
* `config.example.yaml`;
* `models\rapidocr\` when downloaded;
* optional `resources\` binaries.

`web\dist` is a build output and is not committed.  `models\rapidocr` and
`resources` are also intentionally outside Git; a package built without them
can still run the dashboard but OCR or simulator recovery may require a
network download or a manually supplied resource.

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

The helper and packaging script are syntax-checked and unit-tested on macOS.
The actual PyInstaller build, Windows process creation, firewall prompts,
device connection, and graceful-stop behavior require real Windows
verification; they are not claimed by the macOS checks.

The main `dashboard.py` already uses:

* `runtime_process.build_scheduler_argv` for venv/frozen selection;
* `runtime_process.spawn_detached_scheduler` for platform-specific detachment;
* `runtime_process.stop_process` for the platform-specific stop ladder;
* `runtime_process.scheduler_environment` to pass the writable data root.
