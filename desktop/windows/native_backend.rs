//! Install an immutable backend cache and launch it independently of the GUI.
use std::fs::{self, File, Metadata};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

const DASHBOARD: &str = "QQPetCopilot.exe";
const SCHEDULER: &str = "QQPetCopilotScheduler.exe";
const FINGERPRINT_FILE: &str = ".build-fingerprint";
const READY_FILE: &str = ".ready";
const READY_VERSION: &str = "qqpet-windows-backend-v1";
static UNIQUE: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, PartialEq, Eq)]
struct Entry {
    relative: PathBuf,
    directory: bool,
    size: u64,
}

fn describe(action: &str, path: &Path, error: impl std::fmt::Display) -> String {
    format!("{action} {}: {error}", path.display())
}

fn checked_metadata(path: &Path) -> Result<Metadata, String> {
    let metadata =
        fs::symlink_metadata(path).map_err(|error| describe("cannot inspect", path, error))?;
    if metadata.file_type().is_symlink() || is_reparse_point(&metadata) {
        return Err(format!(
            "backend paths must not contain links or reparse points: {}",
            path.display()
        ));
    }
    if !metadata.is_file() && !metadata.is_dir() {
        return Err(format!("unsupported backend file type: {}", path.display()));
    }
    Ok(metadata)
}

#[cfg(windows)]
fn is_reparse_point(metadata: &Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    metadata.file_attributes() & 0x400 != 0
}

#[cfg(not(windows))]
fn is_reparse_point(_metadata: &Metadata) -> bool {
    false
}

fn tree(root: &Path, allow_ready: bool) -> Result<Vec<Entry>, String> {
    if !checked_metadata(root)?.is_dir() {
        return Err(format!(
            "backend root is not a directory: {}",
            root.display()
        ));
    }
    fn visit(root: &Path, directory: &Path, entries: &mut Vec<Entry>) -> Result<(), String> {
        let children = fs::read_dir(directory)
            .map_err(|error| describe("cannot read backend directory", directory, error))?;
        for child in children {
            let path = child
                .map_err(|error| describe("cannot read backend entry", directory, error))?
                .path();
            let metadata = checked_metadata(&path)?;
            let relative = path
                .strip_prefix(root)
                .map_err(|error| describe("backend entry escaped its root", &path, error))?
                .to_path_buf();
            entries.push(Entry {
                relative,
                directory: metadata.is_dir(),
                size: if metadata.is_file() {
                    metadata.len()
                } else {
                    0
                },
            });
            if metadata.is_dir() {
                visit(root, &path, entries)?;
            }
        }
        Ok(())
    }
    let mut entries = Vec::new();
    visit(root, root, &mut entries)?;
    if entries
        .iter()
        .any(|entry| entry.relative == Path::new(READY_FILE))
    {
        if !allow_ready {
            return Err("bundled backend contains the reserved .ready marker".into());
        }
        entries.retain(|entry| entry.relative != Path::new(READY_FILE));
    }
    entries.sort_by(|left, right| left.relative.cmp(&right.relative));
    Ok(entries)
}

fn read_fingerprint(bundled: &Path) -> Result<String, String> {
    let path = bundled.join(FINGERPRINT_FILE);
    let metadata = checked_metadata(&path)?;
    if !metadata.is_file() || metadata.len() > 128 {
        return Err("backend fingerprint is not a regular file".into());
    }
    let contents = fs::read_to_string(&path)
        .map_err(|error| describe("cannot read fingerprint", &path, error))?;
    let fingerprint = contents.trim();
    if fingerprint.len() != 64 || !fingerprint.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("backend fingerprint must contain exactly 64 hexadecimal characters".into());
    }
    Ok(fingerprint.to_ascii_lowercase())
}

fn validate_bundle(entries: &[Entry]) -> Result<(), String> {
    for name in [DASHBOARD, SCHEDULER, FINGERPRINT_FILE] {
        if !entries
            .iter()
            .any(|entry| entry.relative == Path::new(name) && !entry.directory && entry.size != 0)
        {
            return Err(format!("bundled backend is missing {name}"));
        }
    }
    if !entries
        .iter()
        .any(|entry| entry.directory && entry.relative == Path::new("_internal"))
        || !entries
            .iter()
            .any(|entry| !entry.directory && entry.relative.starts_with("_internal"))
    {
        return Err("bundled backend is missing its _internal runtime files".into());
    }
    Ok(())
}

fn same_file(left: &Path, right: &Path) -> Result<bool, String> {
    let mut left_file =
        File::open(left).map_err(|error| describe("cannot read bundled file", left, error))?;
    let mut right_file =
        File::open(right).map_err(|error| describe("cannot read cached file", right, error))?;
    let mut remaining = left_file
        .metadata()
        .map_err(|error| describe("cannot inspect bundled file", left, error))?
        .len();
    if right_file
        .metadata()
        .map_err(|error| describe("cannot inspect cached file", right, error))?
        .len()
        != remaining
    {
        return Ok(false);
    }
    let mut left_buffer = [0u8; 64 * 1024];
    let mut right_buffer = [0u8; 64 * 1024];
    while remaining != 0 {
        let count = remaining.min(left_buffer.len() as u64) as usize;
        left_file
            .read_exact(&mut left_buffer[..count])
            .map_err(|error| describe("cannot read bundled file", left, error))?;
        right_file
            .read_exact(&mut right_buffer[..count])
            .map_err(|error| describe("cannot read cached file", right, error))?;
        if left_buffer[..count] != right_buffer[..count] {
            return Ok(false);
        }
        remaining -= count as u64;
    }
    Ok(true)
}

fn ready_contents(fingerprint: &str) -> String {
    format!("{READY_VERSION}\n{fingerprint}\n")
}

fn installation_matches(
    bundled: &Path,
    installed: &Path,
    entries: &[Entry],
    fingerprint: &str,
) -> Result<bool, String> {
    match fs::symlink_metadata(installed) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(describe("cannot inspect cached backend", installed, error)),
        Ok(_) => {}
    }
    // Inspect the whole cache before reading any marker or payload, including
    // Windows junctions, so an altered cache cannot redirect reads elsewhere.
    if tree(installed, true)? != entries {
        return Ok(false);
    }
    let ready = installed.join(READY_FILE);
    if !checked_metadata(&ready)
        .map(|metadata| {
            metadata.is_file() && metadata.len() == ready_contents(fingerprint).len() as u64
        })
        .unwrap_or(false)
    {
        return Ok(false);
    }
    match fs::read_to_string(&ready) {
        Ok(contents) if contents == ready_contents(fingerprint) => {}
        _ => return Ok(false),
    }
    for entry in entries.iter().filter(|entry| !entry.directory) {
        if !same_file(
            &bundled.join(&entry.relative),
            &installed.join(&entry.relative),
        )? {
            return Ok(false);
        }
    }
    Ok(true)
}

fn unique_path(store: &Path, prefix: &str, fingerprint: &str) -> PathBuf {
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    store.join(format!(
        ".{prefix}-{fingerprint}-{}-{stamp}-{}",
        std::process::id(),
        UNIQUE.fetch_add(1, Ordering::Relaxed)
    ))
}

struct Stage(PathBuf);

impl Drop for Stage {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn future_directory(path: &Path) -> Result<PathBuf, String> {
    if path
        .components()
        .any(|part| part == std::path::Component::ParentDir)
    {
        return Err("backend cache path must not contain parent-directory components".into());
    }
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|error| format!("cannot resolve the current directory: {error}"))?
            .join(path)
    };
    let mut ancestor = absolute.as_path();
    let mut missing = Vec::new();
    loop {
        match fs::symlink_metadata(ancestor) {
            Ok(_) => {
                let mut resolved = fs::canonicalize(ancestor)
                    .map_err(|error| describe("cannot resolve backend cache", ancestor, error))?;
                for name in missing.into_iter().rev() {
                    resolved.push(name);
                }
                return Ok(resolved);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                missing.push(
                    ancestor
                        .file_name()
                        .ok_or_else(|| "cannot resolve backend cache parent".to_string())?
                        .to_os_string(),
                );
                ancestor = ancestor
                    .parent()
                    .ok_or_else(|| "cannot resolve backend cache parent".to_string())?;
            }
            Err(error) => return Err(describe("cannot inspect backend cache", ancestor, error)),
        }
    }
}

/// Copy a complete backend into a fingerprinted cache without modifying its
/// installer resources. `.ready` is committed only after every file is copied.
pub fn install_runtime(bundled: &Path, store: &Path) -> Result<PathBuf, String> {
    checked_metadata(bundled)?;
    let fingerprint = read_fingerprint(bundled)?;
    let entries = tree(bundled, false)?;
    validate_bundle(&entries)?;

    let canonical_bundle = fs::canonicalize(bundled)
        .map_err(|error| describe("cannot resolve bundled backend", bundled, error))?;
    let canonical_store = future_directory(store)?;
    if canonical_store.starts_with(&canonical_bundle) {
        return Err("backend cache must be outside the installer resources".into());
    }
    fs::create_dir_all(store)
        .map_err(|error| describe("cannot create backend cache", store, error))?;
    if !checked_metadata(store)?.is_dir() {
        return Err("backend cache is not a directory".into());
    }

    let installed = store.join(&fingerprint);
    if installation_matches(bundled, &installed, &entries, &fingerprint)? {
        return Ok(installed.join(DASHBOARD));
    }
    let stage_path = unique_path(store, "staging", &fingerprint);
    fs::create_dir(&stage_path).map_err(|error| {
        describe(
            "cannot create backend staging directory",
            &stage_path,
            error,
        )
    })?;
    let stage = Stage(stage_path);
    for entry in &entries {
        let source = bundled.join(&entry.relative);
        let destination = stage.0.join(&entry.relative);
        let metadata = checked_metadata(&source)?;
        if metadata.is_dir() != entry.directory
            || (!entry.directory && metadata.len() != entry.size)
        {
            return Err(format!(
                "bundled backend changed while copying: {}",
                source.display()
            ));
        }
        if entry.directory {
            fs::create_dir(&destination)
                .map_err(|error| describe("cannot stage backend directory", &destination, error))?;
        } else {
            fs::copy(&source, &destination)
                .map_err(|error| describe("cannot stage backend file", &destination, error))?;
        }
    }
    let marker = stage.0.join(READY_FILE);
    let mut marker_file = File::create(&marker)
        .map_err(|error| describe("cannot create backend ready marker", &marker, error))?;
    marker_file
        .write_all(ready_contents(&fingerprint).as_bytes())
        .and_then(|_| marker_file.sync_all())
        .map_err(|error| describe("cannot write backend ready marker", &marker, error))?;
    drop(marker_file);
    if !installation_matches(bundled, &stage.0, &entries, &fingerprint)? {
        return Err("staged backend failed its completeness check".into());
    }

    let mut previous = None;
    if installed.exists() {
        // Keep an incomplete older copy for recovery; never delete DLLs that
        // an already-running backend might still have loaded.
        let backup = unique_path(store, "incomplete", &fingerprint);
        fs::rename(&installed, &backup)
            .map_err(|error| describe("cannot move incomplete backend aside", &installed, error))?;
        previous = Some(backup);
    }
    if let Err(error) = fs::rename(&stage.0, &installed) {
        if installation_matches(bundled, &installed, &entries, &fingerprint).unwrap_or(false) {
            return Ok(installed.join(DASHBOARD));
        }
        if let Some(backup) = previous {
            if !installed.exists() {
                let _ = fs::rename(backup, &installed);
            }
        }
        return Err(describe("cannot commit staged backend", &installed, error));
    }
    Ok(installed.join(DASHBOARD))
}

/// Launch the cached dashboard independently of the desktop GUI.
#[cfg(windows)]
pub fn spawn_dashboard(runtime: &Path, data_root: &Path, port: u16) -> Result<u32, String> {
    use std::fs::OpenOptions;
    use std::os::windows::process::CommandExt;
    use std::process::{Command, Stdio};

    if port == 0 {
        return Err("dashboard port must be greater than zero".into());
    }
    if !checked_metadata(runtime)?.is_file() {
        return Err("dashboard runtime is not a regular file".into());
    }
    let executable = fs::canonicalize(runtime)
        .map_err(|error| describe("cannot resolve dashboard runtime", runtime, error))?;
    let prospective_data = future_directory(data_root)?;
    if executable
        .parent()
        .is_some_and(|directory| prospective_data.starts_with(directory))
    {
        return Err("dashboard user data must be outside its runtime directory".into());
    }
    fs::create_dir_all(data_root)
        .map_err(|error| describe("cannot create dashboard data directory", data_root, error))?;
    let data_root = fs::canonicalize(data_root)
        .map_err(|error| describe("cannot resolve dashboard data directory", data_root, error))?;
    if executable
        .parent()
        .is_some_and(|directory| data_root.starts_with(directory))
    {
        return Err("dashboard user data must be outside its runtime directory".into());
    }
    let runs = data_root.join("runs");
    fs::create_dir_all(&runs)
        .map_err(|error| describe("cannot create dashboard runs directory", &runs, error))?;
    let log_path = runs.join("dashboard_console.log");
    let output = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|error| describe("cannot open dashboard console log", &log_path, error))?;
    let errors = output
        .try_clone()
        .map_err(|error| describe("cannot duplicate dashboard console log", &log_path, error))?;

    // CREATE_NO_WINDOW is ignored with DETACHED_PROCESS. This valid pair
    // detaches console ownership and gives the backend its own process group.
    const DETACHED_PROCESS: u32 = 0x00000008;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
    let child = Command::new(executable)
        .args(["--bind", "127.0.0.1", "--port", &port.to_string()])
        .current_dir(&data_root)
        .env("QQPET_DATA_DIR", &data_root)
        .env("QQPET_PORTABLE", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::from(output))
        .stderr(Stdio::from(errors))
        .creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        .spawn()
        .map_err(|error| describe("cannot start dashboard runtime", runtime, error))?;
    // Dropping std::process::Child does not terminate the process.
    Ok(child.id())
}

#[cfg(not(windows))]
pub fn spawn_dashboard(_runtime: &Path, _data_root: &Path, _port: u16) -> Result<u32, String> {
    Err("the native Windows dashboard backend requires Windows".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Sandbox(PathBuf);

    impl Sandbox {
        fn new() -> Self {
            let path = unique_path(&std::env::temp_dir(), "qqpet-backend-test", "sandbox");
            fs::create_dir(&path).unwrap();
            Self(path)
        }

        fn bundle(&self) -> PathBuf {
            let root = self.0.join("bundle");
            fs::create_dir_all(root.join("_internal/nested")).unwrap();
            fs::write(root.join(DASHBOARD), b"MZ dashboard").unwrap();
            fs::write(root.join(SCHEDULER), b"MZ scheduler").unwrap();
            fs::write(root.join(FINGERPRINT_FILE), format!("{}\n", "a".repeat(64))).unwrap();
            fs::write(
                root.join("_internal/nested/runtime.dll"),
                b"runtime version one",
            )
            .unwrap();
            root
        }
    }

    impl Drop for Sandbox {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn installs_complete_runtime_and_reuses_only_identical_cache() {
        let sandbox = Sandbox::new();
        let bundled = sandbox.bundle();
        let store = sandbox.0.join("cache/backends");
        let executable = install_runtime(&bundled, &store).unwrap();
        let installed = store.join("a".repeat(64));
        assert_eq!(executable, installed.join(DASHBOARD));
        assert_eq!(
            fs::read_to_string(installed.join(READY_FILE)).unwrap(),
            ready_contents(&"a".repeat(64))
        );
        assert_eq!(
            fs::read(installed.join("_internal/nested/runtime.dll")).unwrap(),
            b"runtime version one"
        );
        assert!(!bundled.join(READY_FILE).exists());
        assert_eq!(install_runtime(&bundled, &store).unwrap(), executable);
        assert_eq!(fs::read_dir(&store).unwrap().count(), 1);
    }

    #[test]
    fn repairs_same_length_payload_corruption_without_deleting_old_copy() {
        let sandbox = Sandbox::new();
        let bundled = sandbox.bundle();
        let store = sandbox.0.join("backends");
        let executable = install_runtime(&bundled, &store).unwrap();
        fs::write(
            executable
                .parent()
                .unwrap()
                .join("_internal/nested/runtime.dll"),
            b"runtime version two",
        )
        .unwrap();
        assert_eq!(install_runtime(&bundled, &store).unwrap(), executable);
        assert_eq!(
            fs::read(
                executable
                    .parent()
                    .unwrap()
                    .join("_internal/nested/runtime.dll")
            )
            .unwrap(),
            b"runtime version one"
        );
        assert!(fs::read_dir(store).unwrap().any(|entry| entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".incomplete-")));
    }

    #[test]
    fn repairs_missing_ready_marker_and_missing_required_file() {
        for missing in [READY_FILE, SCHEDULER] {
            let sandbox = Sandbox::new();
            let bundled = sandbox.bundle();
            let store = sandbox.0.join("backends");
            let executable = install_runtime(&bundled, &store).unwrap();
            fs::remove_file(executable.parent().unwrap().join(missing)).unwrap();
            assert_eq!(install_runtime(&bundled, &store).unwrap(), executable);
            assert!(executable.parent().unwrap().join(missing).is_file());
        }
    }

    #[test]
    fn rejects_invalid_fingerprint_before_creating_cache() {
        for fingerprint in ["../outside", &"g".repeat(64), &"a".repeat(63)] {
            let sandbox = Sandbox::new();
            let bundled = sandbox.bundle();
            fs::write(bundled.join(FINGERPRINT_FILE), fingerprint).unwrap();
            let store = sandbox.0.join("backends");
            assert!(install_runtime(&bundled, &store).is_err());
            assert!(!store.exists());
        }
    }

    #[test]
    fn rejects_incomplete_source_and_reserved_ready_marker() {
        for invalid in [SCHEDULER, "_internal"] {
            let sandbox = Sandbox::new();
            let bundled = sandbox.bundle();
            let path = bundled.join(invalid);
            if path.is_dir() {
                fs::remove_dir_all(path).unwrap();
            } else {
                fs::remove_file(path).unwrap();
            }
            assert!(install_runtime(&bundled, &sandbox.0.join("backends")).is_err());
        }
        let sandbox = Sandbox::new();
        let bundled = sandbox.bundle();
        fs::write(bundled.join(READY_FILE), b"untrusted marker").unwrap();
        assert!(install_runtime(&bundled, &sandbox.0.join("backends")).is_err());
    }

    #[test]
    fn rejects_cache_inside_installer_before_creating_directories() {
        let sandbox = Sandbox::new();
        let bundled = sandbox.bundle();
        let store = bundled.join("must-not-create/backends");
        assert!(install_runtime(&bundled, &store).is_err());
        assert!(!bundled.join("must-not-create").exists());
    }

    #[cfg(unix)]
    #[test]
    fn rejects_external_source_links_and_altered_cache_links() {
        use std::os::unix::fs::symlink;

        let sandbox = Sandbox::new();
        let bundled = sandbox.bundle();
        let outside = sandbox.0.join("outside");
        fs::write(&outside, b"private outside file").unwrap();
        let link = bundled.join("_internal/external-link");
        symlink(&outside, &link).unwrap();
        let store = sandbox.0.join("backends");
        assert!(install_runtime(&bundled, &store).is_err());
        assert!(!store.exists());
        fs::remove_file(link).unwrap();
        let executable = install_runtime(&bundled, &store).unwrap();
        symlink(&outside, executable.parent().unwrap().join("cache-link")).unwrap();
        assert!(install_runtime(&bundled, &store).is_err());
        assert_eq!(fs::read(outside).unwrap(), b"private outside file");
    }

    #[cfg(not(windows))]
    #[test]
    fn non_windows_spawn_does_not_create_user_data() {
        let sandbox = Sandbox::new();
        let data = sandbox.0.join("untouched-data");
        assert!(spawn_dashboard(&sandbox.0.join(DASHBOARD), &data, 8787).is_err());
        assert!(!data.exists());
    }
}
