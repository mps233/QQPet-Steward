#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{Read, Write},
    net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};

const DEFAULT_SERVER: &str = "http://127.0.0.1:8787/";
const DASHBOARD_LABEL: &str = "dashboard";
const DASHBOARD_WINDOW_TITLE: &str = "QQ Pet Dashboard";
const LAUNCHER_LABEL: &str = "launcher";
const PANEL_LABEL: &str = "menu-panel";
const TRAY_LABEL: &str = "qqpet";
const MAX_REDIRECTS: usize = 5;
const TRAY_ICON_BYTES: &[u8] = include_bytes!("../icons/tray-icon.png");
const WINDOW_CHROME_SCRIPT: &str = include_str!("../app/window-chrome.js");
const BACKGROUND_LABEL: &str = "ai.qqpet.dashboard.app";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StartupStatus {
    phase: String,
    message: String,
}

struct ShellState {
    server: String,
    data_root: PathBuf,
    runtime: PathBuf,
    runtime_store: PathBuf,
    launch_agent: PathBuf,
    transient: bool,
    startup_status: Mutex<StartupStatus>,
    open_lock: tauri::async_runtime::Mutex<()>,
    startup_lock: Mutex<()>,
    connection_error: Mutex<Option<String>>,
    dashboard_origin: Arc<Mutex<Option<tauri::Url>>>,
    dashboard_child: Mutex<Option<u32>>,
    exit_requested: Mutex<bool>,
}

fn find_project_root(candidates: impl IntoIterator<Item = PathBuf>) -> Option<PathBuf> {
    candidates.into_iter().find_map(|path| {
        (path.join("dashboard.py").is_file() && path.join("scenarios/runner.py").is_file())
            .then(|| path.canonicalize().ok())
            .flatten()
    })
}

fn project_root() -> Option<PathBuf> {
    if let Some(root) = std::env::var_os("QQPET_SOURCE_ROOT") {
        return find_project_root([PathBuf::from(root)]);
    }
    let mut candidates = Vec::new();
    if let Ok(executable) = std::env::current_exe() {
        candidates.extend(executable.ancestors().skip(2).map(Path::to_path_buf));
    }
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd);
    }
    // Used only to preserve an existing installation's data during transition.
    candidates.push(PathBuf::from(env!("QQPET_BUILD_SOURCE_ROOT")));
    find_project_root(candidates)
}

fn local_backend_url(server: &str) -> Result<tauri::Url, String> {
    let url = base_url(server)?;
    let host = url.host_str().unwrap_or_default();
    let loopback = host == "localhost"
        || host
            .trim_matches(['[', ']'])
            .parse::<IpAddr>()
            .is_ok_and(|address| address.is_loopback());
    if url.scheme() != "http" || !loopback || url.path() != "/" {
        return Err("菜单栏托管控制仅支持本机 HTTP 地址；请在主界面操作远程仪表盘".to_owned());
    }
    Ok(url)
}

fn local_startup_port(url: &tauri::Url) -> Result<u16, String> {
    let port = url
        .port_or_known_default()
        .ok_or_else(|| "仪表盘地址缺少端口".to_owned())?;
    if url
        .host_str()
        .unwrap_or_default()
        .trim_matches(['[', ']'])
        .parse::<IpAddr>()
        .is_ok_and(|address| address.is_ipv6())
    {
        return Err(format!(
            "本机托管程序使用 IPv4；请将地址改为 http://127.0.0.1:{port}/ 后重试"
        ));
    }
    Ok(port)
}

fn port_open(server: &str) -> bool {
    let Ok(url) = base_url(server) else {
        return false;
    };
    let Some(host) = url.host_str() else {
        return false;
    };
    let Some(port) = url.port_or_known_default() else {
        return false;
    };
    (host.trim_matches(['[', ']']), port)
        .to_socket_addrs()
        .ok()
        .is_some_and(|mut addresses| {
            addresses.any(|address| {
                TcpStream::connect_timeout(&address, Duration::from_millis(700)).is_ok()
            })
        })
}

fn spawn_dashboard(app: &tauri::AppHandle, server: &str) -> Result<u32, String> {
    let state = app
        .try_state::<ShellState>()
        .ok_or_else(|| "客户端状态未初始化".to_owned())?;
    if !state.runtime.is_file() {
        return Err("App 内的后台组件缺失，请重新构建或重新安装完整应用".to_owned());
    }
    let url = local_backend_url(server)?;
    let port = local_startup_port(&url)?;
    if port_open(server) {
        return Err("仪表盘端口已被其他实例占用；不会重复启动".to_owned());
    }
    let log = state.data_root.join("runs/dashboard_console.log");
    if let Some(parent) = log.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("无法创建日志目录：{error}"))?;
    }
    let stdout = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log)
        .map_err(|error| format!("无法打开仪表盘日志：{error}"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|error| format!("无法复制日志句柄：{error}"))?;
    let child = Command::new(&state.runtime)
        .arg("dashboard")
        .arg("--port")
        .arg(port.to_string())
        .arg("--bind")
        .arg("127.0.0.1")
        .current_dir(&state.data_root)
        .env("QQPET_DATA_DIR", &state.data_root)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env(
            "PATH",
            "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        )
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()
        .map_err(|error| format!("启动仪表盘失败：{error}"))?;
    let pid = child.id();
    *state
        .dashboard_child
        .lock()
        .map_err(|_| "客户端进程状态已损坏".to_owned())? = Some(pid);
    Ok(pid)
}

fn integrated_server() -> Result<String, String> {
    let port = match std::env::var("QQPET_PORT") {
        Ok(value) => value
            .parse::<u16>()
            .ok()
            .filter(|port| *port > 0)
            .ok_or_else(|| "本机后台端口配置无效".to_owned())?,
        Err(_) => 8787,
    };
    Ok(format!("http://127.0.0.1:{port}/"))
}

fn existing_data_root(server: &str) -> Option<PathBuf> {
    let root = project_root()?;
    let token = fs::read_to_string(root.join("runs/desktop_control.token")).ok()?;
    request_json(server, "GET", "/api/data", None, Some(token.trim()))
        .ok()
        .map(|_| root)
}

fn xml_text(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn launch_agent_plist(runtime: &Path, data_root: &Path, port: u16) -> String {
    let runtime = xml_text(&runtime.to_string_lossy());
    let data = xml_text(&data_root.to_string_lossy());
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{BACKGROUND_LABEL}</string>
<key>ProgramArguments</key><array><string>{runtime}</string><string>dashboard</string><string>--port</string><string>{port}</string><string>--bind</string><string>127.0.0.1</string></array>
<key>WorkingDirectory</key><string>{data}</string>
<key>EnvironmentVariables</key><dict>
<key>QQPET_DATA_DIR</key><string>{data}</string>
<key>PYTHONUNBUFFERED</key><string>1</string>
<key>PYTHONIOENCODING</key><string>utf-8</string>
<key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
</dict>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>
<key>StandardOutPath</key><string>{data}/runs/dashboard_console.log</string>
<key>StandardErrorPath</key><string>{data}/runs/dashboard_console.log</string>
</dict></plist>
"#
    )
}

fn install_background_runtime(state: &ShellState) -> Result<PathBuf, String> {
    let bundled = state
        .runtime
        .parent()
        .ok_or_else(|| "应用后台组件路径无效".to_owned())?;
    let fingerprint = fs::read_to_string(bundled.join(".build-fingerprint"))
        .map_err(|_| "应用后台组件缺少版本信息，请重新构建完整应用".to_owned())?;
    let fingerprint = fingerprint.trim();
    if fingerprint.len() != 64 || !fingerprint.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("应用后台组件版本信息无效".to_owned());
    }
    let installed = state.runtime_store.join(fingerprint);
    let executable = installed.join("QQPetRuntime");
    if !installed.join(".ready").is_file() {
        fs::create_dir_all(&state.runtime_store)
            .map_err(|error| format!("无法创建应用后台目录：{error}"))?;
        let status = Command::new("/usr/bin/ditto")
            .arg(bundled)
            .arg(&installed)
            .status()
            .map_err(|error| format!("无法准备应用后台组件：{error}"))?;
        if !status.success() || !executable.is_file() {
            return Err("无法准备应用后台组件，请重试".to_owned());
        }
        fs::write(installed.join(".ready"), b"ready\n")
            .map_err(|error| format!("无法保存后台组件状态：{error}"))?;
    }
    Ok(executable)
}

fn managed_backend_runtime(
    plist: &serde_json::Value,
    data_root: &Path,
    port: u16,
) -> Result<PathBuf, String> {
    let data = data_root.to_string_lossy();
    let arguments = plist["ProgramArguments"]
        .as_array()
        .ok_or_else(|| "现有 App 后台的启动配置无效，请检查应用日志".to_owned())?;
    let expected = [
        "dashboard".to_owned(),
        "--port".to_owned(),
        port.to_string(),
        "--bind".to_owned(),
        "127.0.0.1".to_owned(),
    ];
    if plist["Label"].as_str() != Some(BACKGROUND_LABEL)
        || plist["WorkingDirectory"].as_str() != Some(data.as_ref())
        || plist["EnvironmentVariables"]["QQPET_DATA_DIR"].as_str() != Some(data.as_ref())
        || arguments.len() != expected.len() + 1
        || !arguments[1..]
            .iter()
            .zip(expected.iter())
            .all(|(actual, expected)| actual.as_str() == Some(expected.as_str()))
    {
        return Err("现有 App 后台的数据目录或启动配置与本次启动不一致；请检查应用日志".to_owned());
    }
    arguments[0]
        .as_str()
        .map(PathBuf::from)
        .ok_or_else(|| "现有 App 后台的组件路径无效".to_owned())
}

fn start_managed_backend(app: &tauri::AppHandle, port: u16) -> Result<(), String> {
    let state = app
        .try_state::<ShellState>()
        .ok_or_else(|| "应用状态未初始化".to_owned())?;
    if !state.runtime.is_file() {
        return Err("App 内的后台组件缺失，请重新构建或重新安装完整应用".to_owned());
    }
    fs::create_dir_all(state.data_root.join("runs"))
        .map_err(|error| format!("无法创建应用数据目录：{error}"))?;
    let service = format!("gui/{}/{BACKGROUND_LABEL}", current_uid());
    let registered = Command::new("launchctl")
        .args(["print", &service])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success());
    if !registered {
        let runtime = install_background_runtime(&state)?;
        if let Some(parent) = state.launch_agent.parent() {
            fs::create_dir_all(parent).map_err(|error| format!("无法创建后台配置目录：{error}"))?;
        }
        fs::write(
            &state.launch_agent,
            launch_agent_plist(&runtime, &state.data_root, port),
        )
        .map_err(|error| format!("无法保存后台配置：{error}"))?;
        let output = Command::new("launchctl")
            .args(["bootstrap", &format!("gui/{}", current_uid())])
            .arg(&state.launch_agent)
            .output()
            .map_err(|error| format!("无法启动应用后台：{error}"))?;
        if !output.status.success() {
            return Err(format!(
                "无法启动应用后台：{}",
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
    } else {
        let output = Command::new("/usr/bin/plutil")
            .args(["-convert", "json", "-o", "-"])
            .arg(&state.launch_agent)
            .output()
            .map_err(|error| format!("无法读取现有 App 后台配置：{error}"))?;
        if !output.status.success() {
            return Err("找不到现有 App 后台的有效配置，请检查应用日志".to_owned());
        }
        let plist: serde_json::Value = serde_json::from_slice(&output.stdout)
            .map_err(|_| "现有 App 后台配置格式无效".to_owned())?;
        let runtime = managed_backend_runtime(&plist, &state.data_root, port)?;
        if !runtime.is_file() {
            return Err("现有 App 后台组件已被移动或删除，请检查应用日志".to_owned());
        }
        let status = Command::new("launchctl")
            .args(["kickstart", &service])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|error| format!("无法唤醒应用后台：{error}"))?;
        if !status.success() {
            return Err("无法唤醒应用后台，请重试".to_owned());
        }
    }
    Ok(())
}

fn normalize_server(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err("仪表盘地址不能为空".to_owned());
    }

    let candidate = if trimmed.contains("://") {
        trimmed.to_owned()
    } else {
        format!("http://{trimmed}")
    };
    let mut url = tauri::Url::parse(&candidate).map_err(|_| "仪表盘地址格式不正确".to_owned())?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err("仪表盘地址只支持 http 或 https".to_owned());
    }
    if url.host_str().is_none() {
        return Err("仪表盘地址缺少主机名".to_owned());
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err("仪表盘地址不能包含账号或密码".to_owned());
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err("仪表盘地址不能包含查询参数或锚点".to_owned());
    }
    let normalized_path = url.path().trim_end_matches('/').to_owned();
    url.set_path(if normalized_path.is_empty() {
        "/"
    } else {
        &normalized_path
    });
    Ok(url.to_string())
}

fn base_url(server: &str) -> Result<tauri::Url, String> {
    normalize_server(server)?
        .parse()
        .map_err(|_| "仪表盘地址格式不正确".to_owned())
}

fn origin_of(url: &tauri::Url) -> (String, Option<u16>) {
    (
        url.host_str().unwrap_or_default().to_owned(),
        url.port_or_known_default(),
    )
}

fn same_origin(left: &tauri::Url, right: &tauri::Url) -> bool {
    left.scheme() == right.scheme() && origin_of(left) == origin_of(right)
}

fn allowed_dashboard_url(url: &tauri::Url, server: &tauri::Url) -> bool {
    same_origin(url, server) || url.scheme() == "about" || url.as_str() == "about:blank"
}

fn dashboard_navigation_allowed(url: &tauri::Url, trusted: &Mutex<Option<tauri::Url>>) -> bool {
    trusted.lock().is_ok_and(|server| {
        server
            .as_ref()
            .is_some_and(|server| allowed_dashboard_url(url, server))
    })
}

fn find_dashboard(app: &tauri::AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window(DASHBOARD_LABEL)
}

fn show_launcher(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window(LAUNCHER_LABEL) {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn connection_error_script(error: &str) -> String {
    let message = serde_json::to_string(error).expect("strings always serialize");
    format!(
        "window.__qpetConnectionError = {message}; window.dispatchEvent(new CustomEvent('qpet-connection-error', {{ detail: window.__qpetConnectionError }}));"
    )
}

fn show_connection_error(app: &tauri::AppHandle, error: &str) {
    if let Some(state) = app.try_state::<ShellState>() {
        if let Ok(mut pending) = state.connection_error.lock() {
            *pending = Some(error.to_owned());
        }
        if let Ok(mut status) = state.startup_status.lock() {
            *status = StartupStatus {
                phase: "error".to_owned(),
                message: error.to_owned(),
            };
        }
    }
    if let Some(window) = app.get_webview_window(LAUNCHER_LABEL) {
        let _ = window.eval(connection_error_script(error));
    }
    show_launcher(app);
}

fn clear_connection_error(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<ShellState>() {
        if let Ok(mut pending) = state.connection_error.lock() {
            *pending = None;
        }
    }
}

fn set_startup_status(app: &tauri::AppHandle, phase: &str, message: &str) {
    if let Some(state) = app.try_state::<ShellState>() {
        if let Ok(mut status) = state.startup_status.lock() {
            *status = StartupStatus {
                phase: phase.to_owned(),
                message: message.to_owned(),
            };
        }
    }
}

fn request_app_exit(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<ShellState>() {
        if let Ok(mut requested) = state.exit_requested.lock() {
            *requested = true;
        }
    }
    app.exit(0);
}

fn ensure_panel(app: &tauri::AppHandle) -> Result<WebviewWindow, String> {
    if let Some(window) = app.get_webview_window(PANEL_LABEL) {
        return Ok(window);
    }
    let window = WebviewWindowBuilder::new(app, PANEL_LABEL, WebviewUrl::App("panel.html".into()))
        .title("QQ 宠物托管")
        .inner_size(1.0, 1.0)
        .resizable(false)
        .maximizable(false)
        .minimizable(false)
        .decorations(false)
        .always_on_top(true)
        .visible_on_all_workspaces(true)
        .skip_taskbar(true)
        .focused(false)
        // Avoid a native outline around the card's transparent CSS shadow.
        .shadow(false)
        .accept_first_mouse(true)
        .visible(false)
        .build()
        .map_err(|error| format!("无法创建菜单栏面板：{error}"))?;
    window
        .set_background_color(Some(tauri::window::Color(0, 0, 0, 0)))
        .map_err(|error| format!("无法设置面板透明背景：{error}"))?;
    Ok(window)
}

fn panel_anchor(rect: &tauri::Rect, scale: f64) -> (f64, f64) {
    let (rect_x, rect_y) = match rect.position {
        tauri::Position::Physical(position) => (position.x as f64, position.y as f64),
        tauri::Position::Logical(position) => (position.x * scale, position.y * scale),
    };
    let (rect_w, rect_h) = match rect.size {
        tauri::Size::Physical(size) => (size.width as f64, size.height as f64),
        tauri::Size::Logical(size) => (size.width * scale, size.height * scale),
    };
    (rect_x + rect_w / 2.0, rect_y + rect_h)
}

#[cfg(target_os = "macos")]
fn tray_scale(app: &tauri::AppHandle) -> Result<f64, String> {
    app.tray_by_id(TRAY_LABEL)
        .ok_or_else(|| "无法读取菜单栏图标".to_owned())?
        .with_inner_tray_icon(|tray| {
            let mtm = objc2::MainThreadMarker::new()?;
            let window = tray.ns_status_item()?.button(mtm)?.window()?;
            Some(window.backingScaleFactor())
        })
        .map_err(|error| format!("无法读取菜单栏图标缩放比例：{error}"))?
        .filter(|scale| scale.is_finite() && *scale > 0.0)
        .ok_or_else(|| "无法确定菜单栏图标所在显示器的缩放比例".to_owned())
}

#[cfg(not(target_os = "macos"))]
fn tray_scale(_app: &tauri::AppHandle) -> Result<f64, String> {
    Err("菜单栏面板仅支持 macOS".to_owned())
}

fn panel_monitor_matches(
    anchor: (f64, f64),
    tray_scale: f64,
    monitor_scale: f64,
    origin: &tauri::PhysicalPosition<i32>,
    size: &tauri::PhysicalSize<u32>,
) -> bool {
    // macOS scales each screen's global origin separately, so mixed-DPI bounds can overlap.
    (tray_scale - monitor_scale).abs() < 0.0001
        && anchor.0 >= origin.x as f64
        && anchor.0 < origin.x as f64 + size.width as f64
        && anchor.1 >= origin.y as f64
        && anchor.1 < origin.y as f64 + size.height as f64
}

fn position_panel(panel: &WebviewWindow, rect: tauri::Rect, tray_scale: f64) -> Result<(), String> {
    let (anchor_x, anchor_y) = panel_anchor(&rect, tray_scale);
    let monitor = panel
        .available_monitors()
        .map_err(|error| format!("无法读取显示器信息：{error}"))?
        .into_iter()
        .find(|monitor| {
            panel_monitor_matches(
                (anchor_x, anchor_y),
                tray_scale,
                monitor.scale_factor(),
                monitor.position(),
                monitor.size(),
            )
        })
        .ok_or_else(|| "无法定位菜单栏图标所在的显示器".to_owned())?;
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let x = area.position.x as f64;
    let y = area.position.y as f64;
    panel
        .set_size(tauri::Size::Logical(area.size.to_logical::<f64>(scale)))
        .map_err(|error| format!("无法调整面板大小：{error}"))?;
    panel
        .set_position(tauri::Position::Logical(
            area.position.to_logical::<f64>(scale),
        ))
        .map_err(|error| format!("无法定位菜单栏面板：{error}"))?;
    let anchor = tauri::PhysicalPosition::new(anchor_x - x, anchor_y - y);
    panel
        .eval(&format!(
            "window.__qpetAnchor = {{ x: {}, y: {}, scale: {} }}; window.__qpetPlace?.(); window.__qpetRefresh?.();",
            anchor.x.round(),
            anchor.y.round(),
            scale
        ))
        .map_err(|error| format!("无法通知面板定位：{error}"))
}

fn toggle_panel(app: &tauri::AppHandle, rect: Option<tauri::Rect>) {
    let panel = match ensure_panel(app) {
        Ok(panel) => panel,
        Err(error) => {
            eprintln!("[qqpet] {error}");
            return;
        }
    };
    if panel.is_visible().unwrap_or(false) {
        let _ = panel.hide();
        return;
    }
    let rect = rect.or_else(|| {
        app.tray_by_id(TRAY_LABEL)
            .and_then(|tray| tray.rect().ok().flatten())
    });
    let Some(rect) = rect else {
        eprintln!("[qqpet] 无法定位菜单栏图标");
        return;
    };
    let scale = match tray_scale(app) {
        Ok(scale) => scale,
        Err(error) => {
            eprintln!("[qqpet] {error}");
            return;
        }
    };
    if let Err(error) = position_panel(&panel, rect, scale) {
        eprintln!("[qqpet] {error}");
        return;
    }
    let _ = panel.show();
    let _ = panel.set_focus();
}

async fn open_local_dashboard(app: &tauri::AppHandle, query: Option<&str>) -> Result<(), String> {
    let state = app
        .try_state::<ShellState>()
        .ok_or_else(|| "应用状态未初始化".to_owned())?;
    let _opening = state.open_lock.lock().await;
    clear_connection_error(app);
    set_startup_status(app, "starting", "正在启动本机后台…");
    let server = state_server(app).ok_or_else(|| "应用状态未初始化".to_owned())?;
    let check = prepare_server(app, &server).await?;
    if !check.ok {
        return Err(check.message);
    }
    let session = desktop_session(app, &server).await?;
    ensure_dashboard(app, &server, true, &session)?;
    if let Some(query) = query {
        let mut target = base_url(&server)?;
        target.set_query(Some(query));
        if let Some(window) = find_dashboard(app) {
            window
                .navigate(target)
                .map_err(|error| format!("无法打开仪表盘页面：{error}"))?;
        }
    }
    set_startup_status(app, "ready", "主界面已就绪");
    if let Some(launcher) = app.get_webview_window(LAUNCHER_LABEL) {
        let _ = launcher.hide();
    }
    Ok(())
}

fn show_dashboard_from_panel(app: &tauri::AppHandle, query: Option<&str>) {
    if let Some(panel) = app.get_webview_window(PANEL_LABEL) {
        let _ = panel.hide();
    }
    let app = app.clone();
    let query = query.map(str::to_owned);
    tauri::async_runtime::spawn(async move {
        if let Err(error) = open_local_dashboard(&app, query.as_deref()).await {
            eprintln!("[qqpet] {error}");
            show_connection_error(&app, &error);
        }
    });
}

fn state_server(app: &tauri::AppHandle) -> Option<String> {
    let state = app.try_state::<ShellState>()?;
    Some(state.server.clone())
}

fn wait_for_dashboard(server: &str) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        if port_open(server) {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err("本机托管服务启动后未就绪；请查看项目 runs/dashboard_console.log 后重试".to_owned())
}

fn ensure_dashboard_running(app: &tauri::AppHandle, server: &str) -> Result<(), String> {
    let Ok(url) = local_backend_url(server) else {
        return Ok(());
    };
    let state = app
        .try_state::<ShellState>()
        .ok_or_else(|| "客户端状态未初始化".to_owned())?;
    let _startup = state
        .startup_lock
        .lock()
        .map_err(|_| "客户端启动状态已损坏".to_owned())?;
    if port_open(server) {
        let token = local_control_token(app)?;
        request_json(server, "GET", "/api/data", None, Some(&token)).map_err(|_| {
            "本机服务与应用数据不匹配，端口可能被其他程序占用；请重试或查看应用日志".to_owned()
        })?;
        return Ok(());
    }
    let port = local_startup_port(&url)?;
    if state.transient {
        spawn_dashboard(app, server)?;
    } else {
        start_managed_backend(app, port)?;
    }
    wait_for_dashboard(server)
}

async fn prepare_server(app: &tauri::AppHandle, server: &str) -> Result<CheckResult, String> {
    let app = app.clone();
    let server = server.to_owned();
    tauri::async_runtime::spawn_blocking(move || {
        ensure_dashboard_running(&app, &server)?;
        Ok(check_http(&server))
    })
    .await
    .map_err(|error| format!("连接任务失败：{error}"))?
}

fn current_uid() -> String {
    std::env::var("UID").unwrap_or_else(|_| {
        Command::new("id")
            .arg("-u")
            .output()
            .ok()
            .and_then(|output| String::from_utf8(output.stdout).ok())
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "501".to_owned())
    })
}

fn local_control_token(app: &tauri::AppHandle) -> Result<String, String> {
    let state = app
        .try_state::<ShellState>()
        .ok_or_else(|| "客户端状态未初始化".to_owned())?;
    let path = state.data_root.join("runs/desktop_control.token");
    let token =
        fs::read_to_string(path).map_err(|_| "找不到本机控制凭据；请先启动仪表盘".to_owned())?;
    let token = token.trim();
    if !token.is_empty() {
        return Ok(token.to_owned());
    }
    Err("本机控制凭据为空；请检查仪表盘日志".to_owned())
}

fn ensure_dashboard(
    app: &tauri::AppHandle,
    server: &str,
    foreground: bool,
    session: &DesktopSession,
) -> Result<(), String> {
    let base = base_url(server)?;
    let state = app
        .try_state::<ShellState>()
        .ok_or_else(|| "客户端状态未初始化".to_owned())?;
    let trusted = state.dashboard_origin.clone();
    let previous = trusted
        .lock()
        .map_err(|_| "仪表盘导航状态已损坏".to_owned())?
        .replace(base.clone());
    if let Some(window) = find_dashboard(app) {
        if let Ok(current) = window.url() {
            if !same_origin(&current, &base) {
                let _ = window.clear_all_browsing_data();
            }
        }
        install_session(&window, &base, session)?;
        if let Err(error) = window.navigate(base.clone()) {
            if let Ok(mut server) = trusted.lock() {
                if server.as_ref() == Some(&base) {
                    *server = previous;
                }
            }
            return Err(format!("无法刷新仪表盘窗口：{error}"));
        }
        if foreground {
            let _ = window.show();
            let _ = window.unminimize();
            let _ = window.set_focus();
        }
        return Ok(());
    }

    let window = WebviewWindowBuilder::new(
        app,
        DASHBOARD_LABEL,
        WebviewUrl::External(tauri::Url::parse("about:blank").unwrap()),
    )
    .title(DASHBOARD_WINDOW_TITLE)
    .title_bar_style(tauri::TitleBarStyle::Overlay)
    .hidden_title(true)
    .initialization_script(WINDOW_CHROME_SCRIPT)
    .incognito(state.transient)
    .inner_size(520.0, 900.0)
    .min_inner_size(390.0, 640.0)
    .visible(false)
    .center()
    .on_navigation(move |url| {
        if dashboard_navigation_allowed(url, &trusted) {
            true
        } else {
            let _ = open_external_url(url.as_str());
            false
        }
    })
    .on_new_window(move |url, _features| {
        let _ = open_external_url(url.as_str());
        tauri::webview::NewWindowResponse::Deny
    })
    .on_document_title_changed(|window, title| {
        let title = title.trim();
        if !title.is_empty() {
            let _ = window.set_title(title);
        }
    })
    .build()
    .map_err(|error| format!("无法创建仪表盘窗口：{error}"))?;

    install_session(&window, &base, session)?;
    window
        .navigate(base)
        .map_err(|error| format!("无法打开主界面：{error}"))?;
    if foreground {
        let _ = window.show();
        let _ = window.set_focus();
    }
    Ok(())
}

fn sync_window_chrome(app: &tauri::AppHandle, label: &str) {
    if let Some(window) = app.get_webview_window(label) {
        let fullscreen = window.is_fullscreen().unwrap_or(false);
        let _ = window.eval(format!("window.__qpetSetWindowFullscreen?.({fullscreen});"));
    }
}

fn open_external_url(url: &str) -> Result<(), String> {
    let parsed = tauri::Url::parse(url).map_err(|_| "外部链接格式不正确".to_owned())?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("只允许用浏览器打开 http 或 https 链接".to_owned());
    }
    open::that_detached(parsed.as_str()).map_err(|error| format!("无法打开浏览器：{error}"))
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct CheckResult {
    ok: bool,
    status: Option<u16>,
    authenticated: bool,
    message: String,
}

fn parse_http_status(response: &[u8]) -> Option<u16> {
    let first_line_end = response.windows(2).position(|pair| pair == b"\r\n")?;
    let first_line = std::str::from_utf8(&response[..first_line_end]).ok()?;
    first_line.split_whitespace().nth(1)?.parse().ok()
}

fn response_location(response: &[u8]) -> Option<String> {
    let text = String::from_utf8_lossy(response);
    let headers = text.split("\r\n\r\n").next()?;
    headers.lines().skip(1).find_map(|line| {
        let (name, value) = line.split_once(':')?;
        name.eq_ignore_ascii_case("location")
            .then(|| value.trim().to_owned())
    })
}

fn connect_server(url: &tauri::Url) -> Result<TcpStream, String> {
    let host = url.host_str().ok_or_else(|| "地址缺少主机名".to_owned())?;
    let port = url
        .port_or_known_default()
        .ok_or_else(|| "地址缺少端口".to_owned())?;
    let addresses: Vec<SocketAddr> = (host.trim_matches(['[', ']']), port)
        .to_socket_addrs()
        .map_err(|error| format!("无法解析主机名：{error}"))?
        .collect();
    let mut last_error = "没有可用的主机地址".to_owned();
    for address in addresses {
        match TcpStream::connect_timeout(&address, Duration::from_secs(2)) {
            Ok(stream) => return Ok(stream),
            Err(error) => last_error = format!("连接失败：{error}"),
        }
    }
    Err(last_error)
}

fn probe_once(url: &tauri::Url) -> Result<(u16, Option<String>), String> {
    if url.scheme() == "https" {
        let output = Command::new("/usr/bin/curl")
            .args([
                "--silent",
                "--show-error",
                "--suppress-connect-headers",
                "--connect-timeout",
                "2",
                "--max-time",
                "5",
                "--dump-header",
                "-",
                "--output",
                "/dev/null",
                "--",
                url.as_str(),
            ])
            .output()
            .map_err(|error| format!("无法检查 HTTPS 服务：{error}"))?;
        if !output.status.success() {
            return Err(format!(
                "HTTPS 连接失败：{}",
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
        let status = parse_http_status(&output.stdout)
            .ok_or_else(|| "服务器返回了无法识别的 HTTP 响应".to_owned())?;
        return Ok((status, response_location(&output.stdout)));
    }
    let host = url.host_str().ok_or_else(|| "地址缺少主机名".to_owned())?;
    let mut stream = connect_server(url)?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(3)));

    let host_header = match url.port() {
        Some(port) => format!("{host}:{port}"),
        None => host.to_owned(),
    };
    let path = format!(
        "{}{}",
        url.path(),
        url.query()
            .map(|query| format!("?{query}"))
            .unwrap_or_default()
    );
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {host_header}\r\nConnection: close\r\nAccept: text/html,*/*\r\nUser-Agent: QQPetDashboardShell/0.1\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("发送检查请求失败：{error}"))?;
    let mut response = Vec::with_capacity(4096);
    stream
        .read_to_end(&mut response)
        .map_err(|error| format!("读取检查响应失败：{error}"))?;
    let status = parse_http_status(&response)
        .ok_or_else(|| "服务器返回了无法识别的 HTTP 响应".to_owned())?;
    Ok((status, response_location(&response)))
}

fn check_http(server: &str) -> CheckResult {
    let Ok(mut url) = base_url(server) else {
        return CheckResult {
            ok: false,
            status: None,
            authenticated: false,
            message: "仪表盘地址格式不正确".to_owned(),
        };
    };
    for _ in 0..=MAX_REDIRECTS {
        match probe_once(&url) {
            Ok((status, location)) if (300..400).contains(&status) => {
                let Some(location) = location else {
                    return CheckResult {
                        ok: false,
                        status: Some(status),
                        authenticated: false,
                        message: "服务器重定向没有提供目标地址".to_owned(),
                    };
                };
                let next = url
                    .join(&location)
                    .map_err(|_| "服务器重定向地址无效".to_owned());
                let parsed = match next {
                    Ok(parsed) => parsed,
                    Err(message) => {
                        return CheckResult {
                            ok: false,
                            status: Some(status),
                            authenticated: false,
                            message,
                        };
                    }
                };
                let Ok(base) = base_url(server) else {
                    unreachable!("base URL was already validated");
                };
                if !same_origin(&parsed, &base) {
                    return CheckResult {
                        ok: false,
                        status: Some(status),
                        authenticated: false,
                        message: "服务器跳转到了其他域名，已停止检查".to_owned(),
                    };
                }
                url = parsed;
            }
            Ok((status, _)) => {
                let ok = (200..400).contains(&status) || status == 401;
                return CheckResult {
                    ok,
                    status: Some(status),
                    authenticated: status != 401,
                    message: if status == 401 {
                        "服务已启动，需要访问验证".to_owned()
                    } else if ok {
                        "服务可用".to_owned()
                    } else {
                        format!("服务返回 HTTP {status}")
                    },
                };
            }
            Err(message) => {
                return CheckResult {
                    ok: false,
                    status: None,
                    authenticated: false,
                    message,
                };
            }
        }
    }
    CheckResult {
        ok: false,
        status: None,
        authenticated: false,
        message: "服务器重定向次数过多".to_owned(),
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PanelSnapshot {
    paused: bool,
    data: serde_json::Value,
}

fn request_json(
    server: &str,
    method: &str,
    path: &str,
    cookie_header: Option<&str>,
    control_token: Option<&str>,
) -> Result<serde_json::Value, String> {
    let url = local_backend_url(server)?;
    let host = url.host_str().ok_or_else(|| "地址缺少主机名".to_owned())?;
    let mut stream = connect_server(&url)?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(4)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(4)));
    let host_header = match url.port() {
        Some(port) => format!("{host}:{port}"),
        None => host.to_owned(),
    };
    let prefix = url.path().trim_end_matches('/');
    let request_path = format!("{prefix}{path}");
    let cookie_line = cookie_header
        .filter(|value| !value.is_empty())
        .map(|value| format!("Cookie: {value}\r\n"))
        .unwrap_or_default();
    let control_line = control_token
        .filter(|value| !value.is_empty())
        .map(|value| format!("X-QPet-Control: {value}\r\n"))
        .unwrap_or_default();
    let request = format!(
        "{method} {request_path} HTTP/1.1\r\nHost: {host_header}\r\nConnection: close\r\nAccept: application/json\r\n{cookie_line}{control_line}Content-Length: 0\r\nUser-Agent: QQPetDashboardMenuBar/0.1\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("发送请求失败：{error}"))?;
    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|error| format!("读取响应失败：{error}"))?;
    let split = response
        .windows(4)
        .position(|pair| pair == b"\r\n\r\n")
        .ok_or_else(|| "仪表盘返回了不完整响应".to_owned())?;
    let status = parse_http_status(&response).ok_or_else(|| "无法识别 HTTP 状态".to_owned())?;
    let body = &response[split + 4..];
    if status == 401 {
        return Err("仪表盘需要访问验证；请先在主界面完成登录".to_owned());
    }
    if !(200..300).contains(&status) {
        return Err(format!("仪表盘返回 HTTP {status}"));
    }
    serde_json::from_slice(body).map_err(|error| format!("仪表盘返回的数据格式不正确：{error}"))
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DesktopSession {
    cookie_name: String,
    cookie_value: String,
}

async fn desktop_session(app: &tauri::AppHandle, server: &str) -> Result<DesktopSession, String> {
    let app = app.clone();
    let server = server.to_owned();
    tauri::async_runtime::spawn_blocking(move || {
        let state = app
            .try_state::<ShellState>()
            .ok_or_else(|| "应用状态未初始化".to_owned())?;
        let token = local_control_token(&app)?;
        let value = match request_json(&server, "POST", "/api/desktop/session", None, Some(&token))
        {
            Ok(value) => value,
            Err(_) => {
                // Older running services share the same access key. Derive
                // their session with the bundled runtime, never in page JS.
                let output = Command::new(&state.runtime)
                    .arg("session-cookie")
                    .env("QQPET_DATA_DIR", &state.data_root)
                    .stdin(Stdio::null())
                    .output()
                    .map_err(|_| "无法准备本机登录状态，请检查应用后台组件".to_owned())?;
                if !output.status.success() {
                    return Err("无法准备本机登录状态，请重试".to_owned());
                }
                serde_json::from_slice(&output.stdout)
                    .map_err(|_| "后台登录状态格式不正确".to_owned())?
            }
        };
        let session: DesktopSession =
            serde_json::from_value(value).map_err(|_| "后台登录状态格式不正确".to_owned())?;
        if session.cookie_name != "qqpet_session"
            || session.cookie_value.is_empty()
            || !session
                .cookie_value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric())
        {
            return Err("后台登录状态无效".to_owned());
        }
        Ok(session)
    })
    .await
    .map_err(|error| format!("准备本机登录失败：{error}"))?
}

fn install_session(
    window: &WebviewWindow,
    server: &tauri::Url,
    session: &DesktopSession,
) -> Result<(), String> {
    let cookie =
        tauri::webview::Cookie::build((session.cookie_name.clone(), session.cookie_value.clone()))
            .domain(server.host_str().unwrap_or("127.0.0.1").to_owned())
            .path("/")
            .http_only(true)
            .build();
    window
        .set_cookie(cookie)
        .map_err(|error| format!("无法设置本机登录状态：{error}"))
}

#[tauri::command]
async fn panel_snapshot(app: tauri::AppHandle) -> Result<PanelSnapshot, String> {
    let server = state_server(&app).unwrap_or_else(|| DEFAULT_SERVER.to_owned());
    let normalized = normalize_server(&server)?;
    local_backend_url(&normalized)?;
    let data = tauri::async_runtime::spawn_blocking(move || {
        ensure_dashboard_running(&app, &normalized)?;
        let control = local_control_token(&app)?;
        let data = request_json(&normalized, "GET", "/api/data", None, Some(&control))?;
        let paused = request_json(
            &normalized,
            "GET",
            "/api/runner/pause",
            None,
            Some(&control),
        )
        .ok()
        .and_then(|value| value.get("paused").and_then(|v| v.as_bool()))
        .unwrap_or(false);
        Ok::<_, String>(PanelSnapshot { paused, data })
    })
    .await
    .map_err(|error| format!("读取状态失败：{error}"))??;
    Ok(data)
}

async fn panel_runner_action(
    app: tauri::AppHandle,
    action: &'static str,
) -> Result<serde_json::Value, String> {
    let server = state_server(&app).unwrap_or_else(|| DEFAULT_SERVER.to_owned());
    let normalized = normalize_server(&server)?;
    local_backend_url(&normalized)?;
    tauri::async_runtime::spawn_blocking(move || {
        ensure_dashboard_running(&app, &normalized)?;
        let control = local_control_token(&app)?;
        request_json(
            &normalized,
            "POST",
            &format!("/api/runner/{action}"),
            None,
            Some(&control),
        )
    })
    .await
    .map_err(|error| format!("控制托管失败：{error}"))?
}

#[tauri::command]
async fn panel_start_runner(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    panel_runner_action(app, "start").await
}

#[tauri::command]
async fn panel_stop_runner(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    panel_runner_action(app, "stop").await
}

#[tauri::command]
async fn panel_pause_runner(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    panel_runner_action(app, "pause").await
}

#[tauri::command]
async fn panel_resume_runner(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    panel_runner_action(app, "resume").await
}

#[tauri::command]
fn panel_open_dashboard(app: tauri::AppHandle) {
    show_dashboard_from_panel(&app, None);
}

#[tauri::command]
fn panel_open_settings(app: tauri::AppHandle) {
    show_dashboard_from_panel(&app, Some("tab=set"));
}

#[tauri::command]
fn panel_hide(app: tauri::AppHandle) {
    if let Some(panel) = app.get_webview_window(PANEL_LABEL) {
        let _ = panel.hide();
    }
}

#[tauri::command]
fn panel_quit(app: tauri::AppHandle) -> Result<(), String> {
    request_app_exit(&app);
    Ok(())
}

#[tauri::command]
fn get_startup_status(state: tauri::State<ShellState>) -> Result<StartupStatus, String> {
    let status = state
        .startup_status
        .lock()
        .map_err(|_| "应用启动状态不可用".to_owned())?;
    Ok(status.clone())
}

#[tauri::command]
async fn retry_startup(app: tauri::AppHandle) -> Result<(), String> {
    if let Err(error) = open_local_dashboard(&app, None).await {
        show_connection_error(&app, &error);
        return Err(error);
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = find_dashboard(app) {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            } else {
                show_launcher(app);
            }
        }))
        .setup(|app| {
            let config_dir = app.path().app_config_dir()?;
            let server = integrated_server().unwrap_or_else(|_| DEFAULT_SERVER.to_owned());
            let override_root = std::env::var_os("QQPET_DATA_DIR").map(PathBuf::from);
            let saved_root = fs::read_to_string(config_dir.join("data-root.txt"))
                .ok()
                .map(|value| PathBuf::from(value.trim()))
                .filter(|path| path.join("config.yaml").is_file());
            let data_root = override_root
                .clone()
                .or_else(|| existing_data_root(&server))
                .or(saved_root)
                .unwrap_or(
                    app.path()
                        .home_dir()?
                        .join("Library/Application Support/QQPetCopilot"),
                );
            if override_root.is_none() {
                let _ = fs::create_dir_all(&config_dir);
                let _ = fs::write(
                    config_dir.join("data-root.txt"),
                    data_root.to_string_lossy().as_bytes(),
                );
            }
            let runtime = app.path().resource_dir()?.join("backend/QQPetRuntime");
            app.manage(ShellState {
                server,
                data_root,
                runtime,
                runtime_store: config_dir.join("backends"),
                launch_agent: app
                    .path()
                    .home_dir()?
                    .join("Library/LaunchAgents/ai.qqpet.dashboard.app.plist"),
                transient: std::env::var("QQPET_TRANSIENT").as_deref() == Ok("1"),
                startup_status: Mutex::new(StartupStatus {
                    phase: "starting".to_owned(),
                    message: "正在准备本机后台…".to_owned(),
                }),
                open_lock: tauri::async_runtime::Mutex::new(()),
                startup_lock: Mutex::new(()),
                connection_error: Mutex::new(None),
                dashboard_origin: Arc::new(Mutex::new(None)),
                dashboard_child: Mutex::new(None),
                exit_requested: Mutex::new(false),
            });
            let toggle = MenuItem::with_id(app, "toggle", "打开状态面板", true, None::<&str>)?;
            let open = MenuItem::with_id(app, "open", "打开主界面", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let quit = MenuItem::with_id(
                app,
                "quit",
                "退出 App（托管继续在后台）",
                true,
                None::<&str>,
            )?;
            let menu = Menu::with_items(app, &[&toggle, &open, &separator, &quit])?;
            let tray_icon = Image::from_bytes(TRAY_ICON_BYTES).map_err(std::io::Error::other)?;
            let tray = TrayIconBuilder::with_id(TRAY_LABEL)
                .icon(tray_icon)
                .icon_as_template(true)
                .tooltip("QQ 宠物托管")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "toggle" => toggle_panel(app, None),
                    "open" => show_dashboard_from_panel(app, None),
                    "quit" => request_app_exit(app),
                    _ => {}
                })
                .build(app)?;
            let _ = tray.set_visible(true);
            if let Err(error) = ensure_panel(app.handle()) {
                eprintln!("[qqpet] {error}");
            }
            show_launcher(app.handle());
            show_dashboard_from_panel(app.handle(), None);
            Ok(())
        })
        .on_page_load(|webview, payload| {
            if matches!(webview.label(), DASHBOARD_LABEL | LAUNCHER_LABEL)
                && payload.event() == tauri::webview::PageLoadEvent::Finished
            {
                sync_window_chrome(webview.app_handle(), webview.label());
            }
            if webview.label() == LAUNCHER_LABEL
                && payload.event() == tauri::webview::PageLoadEvent::Finished
            {
                let pending = webview
                    .try_state::<ShellState>()
                    .and_then(|state| state.connection_error.lock().ok()?.clone());
                if let Some(error) = pending {
                    let _ = webview.eval(connection_error_script(&error));
                }
            }
        })
        .on_tray_icon_event(|app, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                rect,
                ..
            } = event
            {
                toggle_panel(app, Some(rect));
            }
        })
        .on_window_event(|window, event| match event {
            WindowEvent::CloseRequested { api, .. }
                if matches!(
                    window.label(),
                    DASHBOARD_LABEL | LAUNCHER_LABEL | PANEL_LABEL
                ) =>
            {
                api.prevent_close();
                let _ = window.hide();
            }
            WindowEvent::Focused(false) if window.label() == PANEL_LABEL => {
                let _ = window.hide();
            }
            WindowEvent::Resized(_)
                if matches!(window.label(), DASHBOARD_LABEL | LAUNCHER_LABEL) =>
            {
                sync_window_chrome(window.app_handle(), window.label());
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![
            get_startup_status,
            retry_startup,
            panel_snapshot,
            panel_start_runner,
            panel_stop_runner,
            panel_pause_runner,
            panel_resume_runner,
            panel_open_dashboard,
            panel_open_settings,
            panel_hide,
            panel_quit
        ])
        .build(tauri::generate_context!())
        .expect("error while building QQ Pet Dashboard shell")
        .run(|app, event| {
            if let tauri::RunEvent::ExitRequested { api, .. } = event {
                let explicit = app
                    .try_state::<ShellState>()
                    .and_then(|state| state.exit_requested.lock().ok().map(|flag| *flag))
                    .unwrap_or(false);
                if !explicit {
                    api.prevent_exit();
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        net::TcpListener,
        sync::atomic::{AtomicUsize, Ordering},
    };

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "qqpet-startup-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn panel_monitor_match_rejects_overlapping_bounds_from_another_scale() {
        let anchor = (1928.0, 24.0);
        assert!(!panel_monitor_matches(
            anchor,
            1.0,
            2.0,
            &tauri::PhysicalPosition::new(0, 0),
            &tauri::PhysicalSize::new(3456, 2234),
        ));
        assert!(panel_monitor_matches(
            anchor,
            1.0,
            1.0,
            &tauri::PhysicalPosition::new(1728, 0),
            &tauri::PhysicalSize::new(1920, 1080),
        ));
    }

    #[test]
    fn panel_monitor_match_handles_adjacent_and_negative_origins() {
        let left_origin = tauri::PhysicalPosition::new(-1920, 0);
        let right_origin = tauri::PhysicalPosition::new(0, 0);
        let size = tauri::PhysicalSize::new(1920, 1080);
        assert!(panel_monitor_matches(
            (-20.0, 24.0),
            1.0,
            1.0,
            &left_origin,
            &size,
        ));
        assert!(!panel_monitor_matches(
            (0.0, 24.0),
            1.0,
            1.0,
            &left_origin,
            &size,
        ));
        assert!(panel_monitor_matches(
            (0.0, 24.0),
            1.0,
            1.0,
            &right_origin,
            &size,
        ));
    }

    #[test]
    fn missing_or_incomplete_source_is_optional() {
        let directory = TestDirectory::new();
        assert_eq!(find_project_root([directory.0.clone()]), None);
        fs::write(directory.0.join("dashboard.py"), "").unwrap();
        assert_eq!(find_project_root([directory.0.clone()]), None);
        fs::create_dir(directory.0.join("scenarios")).unwrap();
        fs::write(directory.0.join("scenarios/runner.py"), "").unwrap();
        assert_eq!(
            find_project_root([directory.0.clone()]),
            Some(directory.0.canonicalize().unwrap())
        );
    }

    #[test]
    fn moved_bundle_can_find_build_machine_source() {
        let unrelated = TestDirectory::new();
        let root = PathBuf::from(env!("QQPET_BUILD_SOURCE_ROOT"));
        assert_eq!(
            find_project_root([unrelated.0.clone(), root.clone()]),
            Some(root.canonicalize().unwrap())
        );
    }

    #[test]
    fn local_startup_and_control_reject_remote_https_and_proxy_paths() {
        for server in [
            "http://127.0.0.1:8787/",
            "http://localhost:8787/",
            "http://[::1]:8787/",
        ] {
            assert!(local_backend_url(server).is_ok(), "{server}");
        }
        for server in [
            "http://192.0.2.1:8787/",
            "http://127.0.0.1.example.com:8787/",
            "https://localhost:8787/",
            "http://localhost:8787/proxy/",
        ] {
            assert!(local_backend_url(server).is_err(), "{server}");
            assert!(
                request_json(server, "GET", "/api/data", None, Some("private-token")).is_err(),
                "{server}"
            );
        }
    }

    #[test]
    fn localhost_can_connect_to_ipv4_only_backend() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let server = format!(
            "http://localhost:{}/",
            listener.local_addr().unwrap().port()
        );
        assert!(port_open(&server));
        assert!(connect_server(&base_url(&server).unwrap()).is_ok());
    }

    #[test]
    fn local_startup_requires_the_backends_ipv4_listener() {
        let ipv4 = local_backend_url("http://127.0.0.1:8787/").unwrap();
        let localhost = local_backend_url("http://localhost:8899/").unwrap();
        let ipv6 = local_backend_url("http://[::1]:8787/").unwrap();
        assert_eq!(local_startup_port(&ipv4).unwrap(), 8787);
        assert_eq!(local_startup_port(&localhost).unwrap(), 8899);
        assert!(local_startup_port(&ipv6)
            .unwrap_err()
            .contains("http://127.0.0.1:8787/"));
    }

    #[test]
    fn switching_servers_updates_the_existing_navigation_guard() {
        let local = base_url(DEFAULT_SERVER).unwrap();
        let remote = base_url("https://pet.example.com/").unwrap();
        let trusted = Mutex::new(Some(local.clone()));
        assert!(dashboard_navigation_allowed(&local, &trusted));
        assert!(!dashboard_navigation_allowed(&remote, &trusted));
        *trusted.lock().unwrap() = Some(remote.clone());
        assert!(dashboard_navigation_allowed(&remote, &trusted));
        assert!(!dashboard_navigation_allowed(&local, &trusted));
    }

    #[test]
    fn managed_backend_uses_only_bundled_runtime_and_writable_data() {
        let plist = launch_agent_plist(
            Path::new("/Applications/QQ Pet & Copilot.app/Contents/Resources/backend/QQPetRuntime"),
            Path::new("/Users/example/Library/Application Support/QQPetCopilot"),
            8787,
        );
        assert!(plist.contains("QQ Pet &amp; Copilot.app"));
        assert!(plist.contains("<string>dashboard</string>"));
        assert!(plist.contains("<key>QQPET_DATA_DIR</key>"));
        assert!(plist.contains("<string>127.0.0.1</string>"));
        assert!(!plist.contains(".venv"));
        assert!(!plist.contains("dashboard.py"));
        assert!(!plist.contains("<string>scheduler</string>"));
    }

    #[test]
    fn registered_backend_must_match_the_selected_data_and_port() {
        let data = Path::new("/Users/example/Library/Application Support/QQPetCopilot");
        let runtime = "/Users/example/Library/Application Support/ai.qqpet.dashboard.desktop/backends/old-version/QQPetRuntime";
        let plist = serde_json::json!({
            "Label": BACKGROUND_LABEL,
            "WorkingDirectory": data,
            "EnvironmentVariables": { "QQPET_DATA_DIR": data },
            "ProgramArguments": [runtime, "dashboard", "--port", "8787", "--bind", "127.0.0.1"]
        });
        assert_eq!(
            managed_backend_runtime(&plist, data, 8787).unwrap(),
            PathBuf::from(runtime)
        );
        assert!(managed_backend_runtime(&plist, data, 8899).is_err());
        assert!(managed_backend_runtime(&plist, Path::new("/other-data"), 8787).is_err());
        let mut wrong_service = plist.clone();
        wrong_service["Label"] = serde_json::json!("ai.qqpet.dashboard");
        assert!(managed_backend_runtime(&wrong_service, data, 8787).is_err());
    }

    #[test]
    fn authentication_page_counts_as_an_available_service() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let server = format!("http://{}/", listener.local_addr().unwrap());
        let worker = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(3)))
                .unwrap();
            let mut request = [0; 4096];
            stream.read(&mut request).unwrap();
            stream
                .write_all(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
                .unwrap();
        });
        let result = check_http(&server);
        worker.join().unwrap();
        assert!(result.ok);
        assert_eq!(result.status, Some(401));
        assert!(!result.authenticated);
    }
}
