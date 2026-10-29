//! Trillion as a Mac app: starts the user's `trillion run` with a token it made, shows
//! a splash until the server answers, then the server's page. Lives in the menu bar;
//! closing the window hides it, quitting stops the server.

mod locate;
mod port;
mod running;
mod server;
mod shell;
mod token;
mod tray;
#[cfg(target_os = "macos")]
mod webkit;

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::utils::config::BackgroundThrottlingPolicy;
use tauri::webview::{PermissionKind, PermissionResponse};
use tauri::{
    AppHandle, Emitter, Manager, RunEvent, State, Url, Webview, WebviewUrl, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_opener::OpenerExt;

use server::Server;

pub(crate) const MAIN: &str = "main";
/// How long the server has to answer before the splash says it couldn't start.
const START_LIMIT: Duration = Duration::from_secs(30);
const POLL: Duration = Duration::from_millis(250);

/// What the splash shows.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum Phase {
    #[default]
    Starting,
    /// A server started elsewhere (a terminal) is running: D3.
    Elsewhere,
    /// No `trillion` to run.
    Missing,
    /// It exited or did not answer while starting.
    Failed,
    /// It died after it had started.
    Stopped,
}

#[derive(Debug, Clone, Default, Serialize)]
struct Splash {
    state: Phase,
    #[serde(skip_serializing_if = "Option::is_none")]
    port: Option<u16>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    lines: Vec<String>,
}

impl Splash {
    fn of(state: Phase) -> Self {
        Splash {
            state,
            ..Default::default()
        }
    }

    fn with_lines(state: Phase, lines: Vec<String>) -> Self {
        Splash {
            state,
            lines,
            ..Default::default()
        }
    }
}

/// The app's state: what the splash shows and the server it started.
#[derive(Default)]
struct Shell {
    splash: Mutex<Splash>,
    server: Mutex<Option<Arc<Server>>>,
    /// Each start counts up; a start that is no longer the latest gives up.
    attempt: AtomicU64,
    /// Where the bundled splash is, to go back to it.
    splash_url: Mutex<Option<Url>>,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

#[tauri::command]
fn splash_state(shell: State<'_, Shell>) -> Splash {
    lock(&shell.splash).clone()
}

#[tauri::command]
fn retry(app: AppHandle) {
    start(&app);
}

#[tauri::command]
fn open_in_browser(app: AppHandle, shell: State<'_, Shell>) -> Result<(), String> {
    let splash = lock(&shell.splash).clone();
    match (splash.state, splash.port) {
        (Phase::Elsewhere, Some(port)) => app
            .opener()
            .open_url(format!("http://127.0.0.1:{port}/"), None::<&str>)
            .map_err(|e| e.to_string()),
        _ => Err("no server to open".into()),
    }
}

pub(crate) fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn set_splash(app: &AppHandle, splash: Splash) {
    *lock(&app.state::<Shell>().splash) = splash.clone();
    let _ = app.emit_to(MAIN, "splash", splash);
}

/// Puts the main window back on the bundled splash, if it is showing anything else.
fn show_splash(app: &AppHandle) {
    let Some(window) = app.get_webview_window(MAIN) else {
        return;
    };
    let Some(splash_url) = lock(&app.state::<Shell>().splash_url).clone() else {
        return;
    };
    if window.url().ok().as_ref() != Some(&splash_url) {
        let _ = window.navigate(splash_url);
    }
}

/// Starts (or restarts) the server. Returns at once; the work runs on its own thread.
fn start(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let attempt = shell.attempt.fetch_add(1, Ordering::SeqCst) + 1;
    set_splash(app, Splash::of(Phase::Starting));
    show_splash(app);
    let app = app.clone();
    std::thread::spawn(move || {
        let shell = app.state::<Shell>();
        let old = lock(&shell.server).take();
        if let Some(old) = old {
            old.stop();
        }
        if let Some(splash) = attempt_start(&app, attempt) {
            if current(&app, attempt) {
                set_splash(&app, splash);
            }
        }
    });
}

fn current(app: &AppHandle, attempt: u64) -> bool {
    app.state::<Shell>().attempt.load(Ordering::SeqCst) == attempt
}

/// One start, as the spec's Starting section. Returns what the splash should say if
/// the page could not be shown.
fn attempt_start(app: &AppHandle, attempt: u64) -> Option<Splash> {
    let user_home = std::env::var_os("HOME").map(PathBuf::from);
    let data = running::data_home(
        std::env::var("TRILLION_HOME").ok().as_deref(),
        user_home.as_deref().unwrap_or(std::path::Path::new("/")),
    );
    if let Some(port) = running::elsewhere(&data, &running::pid_alive) {
        return Some(Splash {
            state: Phase::Elsewhere,
            port: Some(port),
            lines: vec![],
        });
    }

    let answer: Option<String> =
        shell::login("command -v trillion").map(|b| String::from_utf8_lossy(&b).into());
    let bin = locate::locate(
        std::env::var("TRILLION_BIN").ok().as_deref(),
        answer.as_deref(),
        user_home.as_deref(),
        &locate::is_executable,
    );
    let Some(bin) = bin else {
        return Some(Splash::of(Phase::Missing));
    };
    let Some(port) = port::pick(&port::is_free) else {
        let line = format!("No free port from {} to {}.", port::FIRST, port::LAST);
        return Some(Splash::with_lines(Phase::Failed, vec![line]));
    };

    let login_env = shell::login("env -0")
        .map(|raw| server::parse_env0(&raw))
        .unwrap_or_default();
    let env = server::child_env(login_env, std::env::vars());
    let token = token::new_token();
    let server = match Server::start(&bin, port, &token, env) {
        Ok(server) => Arc::new(server),
        Err(e) => {
            let line = format!("Couldn't run {}: {e}", bin.display());
            return Some(Splash::with_lines(Phase::Failed, vec![line]));
        }
    };
    *lock(&app.state::<Shell>().server) = Some(server.clone());

    let deadline = Instant::now() + START_LIMIT;
    loop {
        if !current(app, attempt) {
            // A newer start took over: it stops what is in the slot; this one may not be there yet.
            server.stop();
            return None;
        }
        if server.exited() {
            return Some(Splash::with_lines(Phase::Failed, server.lines()));
        }
        if server::answers(port) {
            break;
        }
        if Instant::now() >= deadline {
            server.stop();
            let mut lines = server.lines();
            lines.push(format!(
                "No answer on port {port} in {} seconds.",
                START_LIMIT.as_secs()
            ));
            return Some(Splash::with_lines(Phase::Failed, lines));
        }
        std::thread::sleep(POLL);
    }

    // The token rides in the fragment, which the page reads and then removes.
    let page = Url::parse(&format!("http://127.0.0.1:{port}/#token={token}")).ok()?;
    drop(token);
    if let Some(window) = app.get_webview_window(MAIN) {
        let _ = window.navigate(page);
    }
    watch(app, attempt, &server);
    None
}

/// Waits for the server to end. If it ends on its own (not by Quit or a newer start),
/// the window goes back to the splash, stopped, with Restart.
fn watch(app: &AppHandle, attempt: u64, server: &Server) {
    loop {
        std::thread::sleep(POLL);
        if !current(app, attempt) {
            return;
        }
        if server.exited() {
            set_splash(app, Splash::with_lines(Phase::Stopped, server.lines()));
            show_splash(app);
            return;
        }
    }
}

/// The microphone is for the page of the server this app started, and nothing else.
fn grant(webview: Webview, kind: PermissionKind) -> PermissionResponse {
    let port = lock(&webview.app_handle().state::<Shell>().server)
        .as_ref()
        .map(|s| s.port);
    let ours = match (webview.url(), port) {
        (Ok(url), Some(port)) => {
            url.scheme() == "http"
                && url.host_str() == Some("127.0.0.1")
                && url.port() == Some(port)
        }
        _ => false,
    };
    match kind {
        PermissionKind::Microphone if ours => PermissionResponse::Allow,
        _ => PermissionResponse::Deny,
    }
}

fn stop_server(app: &AppHandle) {
    let shell = app.state::<Shell>();
    // Any start or watch in flight gives up.
    shell.attempt.fetch_add(1, Ordering::SeqCst);
    let server = lock(&shell.server).take();
    if let Some(server) = server {
        server.stop();
    }
}

/// A terminal's Ctrl+C or a logout's SIGTERM quits as Quit does, so the server stops
/// with the app. (A force quit cannot be caught: its server keeps running.)
fn quit_on_signals(app: &AppHandle) {
    use signal_hook::consts::{SIGHUP, SIGINT, SIGTERM};
    let Ok(mut signals) = signal_hook::iterator::Signals::new([SIGTERM, SIGINT, SIGHUP]) else {
        return;
    };
    let app = app.clone();
    std::thread::spawn(move || {
        if signals.forever().next().is_some() {
            app.exit(0);
        }
    });
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(
            // Size and place are remembered; whether it was hidden is not, so it opens shown.
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(
                    tauri_plugin_window_state::StateFlags::all()
                        - tauri_plugin_window_state::StateFlags::VISIBLE,
                )
                .build(),
        )
        .manage(Shell::default())
        .invoke_handler(tauri::generate_handler![
            splash_state,
            retry,
            open_in_browser
        ])
        .setup(|app| {
            let window =
                WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("splash.html".into()))
                    .title("Trillion")
                    .inner_size(1200.0, 800.0)
                    .min_inner_size(480.0, 360.0)
                    // Her voice runs in this page: hidden, it must keep its timers and audio.
                    .background_throttling(BackgroundThrottlingPolicy::Disabled)
                    .on_permission_request(grant)
                    .build()?;
            #[cfg(target_os = "macos")]
            webkit::keep_timers_when_hidden(&window);
            *lock(&app.state::<Shell>().splash_url) = window.url().ok();
            tray::build(app)?;
            quit_on_signals(app.handle());
            start(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing hides: she keeps running in the menu bar (D4).
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == MAIN {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("the Trillion app");
    app.run(|app, event| match event {
        RunEvent::Reopen { .. } => show_main(app),
        RunEvent::Exit => stop_server(app),
        _ => {}
    });
}
