//! Trillion as a Mac app: starts the user's `trillion run` with a token it made, shows
//! a splash until the server answers, then the server's page. Lives in the menu bar;
//! closing the window hides it, quitting stops the server.

mod locate;
mod orb;
mod port;
mod record;
mod running;
mod server;
mod shell;
mod talk;
mod token;
mod tray;
#[cfg(target_os = "macos")]
mod webkit;

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::ipc::CapabilityBuilder;
use tauri::utils::config::BackgroundThrottlingPolicy;
use tauri::webview::{PermissionKind, PermissionResponse};
use tauri::{
    AppHandle, Emitter, Manager, RunEvent, State, Url, Webview, WebviewUrl, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_opener::OpenerExt;

use locate::Located;
use record::Record;
use running::Note;
use server::{var, Server};

pub(crate) const MAIN: &str = "main";
/// How long the server has to answer before the splash says it couldn't start.
const START_LIMIT: Duration = Duration::from_secs(30);
const POLL: Duration = Duration::from_millis(250);
/// The longest caption passed on to the orb (the page sends at most 120).
const CAPTION_MAX: usize = 200;

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

/// The current start and the server it made. One lock, so a start that has been
/// overtaken (by Retry or Quit) can never leave its server in the slot.
#[derive(Default)]
struct Run {
    attempt: u64,
    server: Option<Arc<Server>>,
}

/// The app's state: what the splash shows and the server it started.
#[derive(Default)]
struct Shell {
    splash: Mutex<Splash>,
    run: Mutex<Run>,
    /// The port of the server that proved it is ours: the only page the window may
    /// show and the only origin that gets the microphone.
    page_port: Mutex<Option<u16>>,
    /// Where the bundled splash is, to go back to it.
    splash_url: Mutex<Option<Url>>,
    /// Ports whose page has been given its capability (they cannot be taken back).
    capable: Mutex<HashSet<u16>>,
    /// The talk shortcut registered for the page, and how it is used.
    talk: Mutex<TalkShortcut>,
    /// What the page last reported, for the orb and the menu-bar icon.
    reported: Mutex<Reported>,
}

#[derive(Default)]
struct TalkShortcut {
    accelerator: Option<String>,
    toggle: bool,
}

#[derive(Default)]
struct Reported {
    state: String,
    /// When she went idle, to hide the orb a little after.
    idle_since: Option<Instant>,
    icon: Option<orb::Icon>,
    orb_shown: bool,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as i64)
}

#[tauri::command]
fn splash_state(shell: State<'_, Shell>) -> Splash {
    lock(&shell.splash).clone()
}

#[tauri::command]
fn retry(app: AppHandle, shell: State<'_, Shell>) {
    // Only from a screen that offers it: a start already under way is left alone.
    let phase = lock(&shell.splash).state;
    if matches!(
        phase,
        Phase::Elsewhere | Phase::Missing | Phase::Failed | Phase::Stopped
    ) {
        start(&app);
    }
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

pub(crate) fn show_main_window(app: &AppHandle) {
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
    let shell = app.state::<Shell>();
    *lock(&shell.page_port) = None;
    {
        let mut r = lock(&shell.reported);
        r.state = "idle".into();
        r.idle_since = None;
        r.orb_shown = false;
    }
    orb::hide(app);
    tray::set_icon(app, orb::Icon::Idle);
    forget_shortcut(app);
    let Some(window) = app.get_webview_window(MAIN) else {
        return;
    };
    let Some(splash_url) = lock(&shell.splash_url).clone() else {
        return;
    };
    if window.url().ok().as_ref() != Some(&splash_url) {
        let _ = window.navigate(splash_url);
    }
}

fn record_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_data_dir().ok().map(|d| d.join("child.json"))
}

/// Stops a server this app started, and forgets it.
fn finish(app: &AppHandle, server: &Server) {
    server.stop();
    if let Some(path) = record_path(app) {
        if record::read(&path).is_some_and(|r| r.pgid == server.pgid) {
            record::remove(&path);
        }
    }
}

/// Starts (or restarts) the server. Returns at once; the work runs on its own thread.
fn start(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let (attempt, old) = {
        let mut run = lock(&shell.run);
        run.attempt += 1;
        (run.attempt, run.server.take())
    };
    set_splash(app, Splash::of(Phase::Starting));
    show_splash(app);
    let app = app.clone();
    std::thread::spawn(move || {
        if let Some(old) = old {
            finish(&app, &old);
        }
        if let Some(splash) = attempt_start(&app, attempt) {
            if current(&app, attempt) {
                set_splash(&app, splash);
                show_splash(&app);
            }
        }
    });
}

fn current(app: &AppHandle, attempt: u64) -> bool {
    lock(&app.state::<Shell>().run).attempt == attempt
}

/// Whether the server a note names is still the one that wrote it. A pid reused by
/// another process started after the note; if its start cannot be read, the port
/// must answer.
fn live(note: &Note) -> bool {
    match running::start_time(note.pid) {
        Some(start) => running::started_near(start, note.started_at),
        None => running::pid_alive(note.pid) && server::answers(note.port),
    }
}

/// A server a previous run of this app started and could not stop (a crash, a force
/// quit) is stopped now, rather than taken for one started from a terminal.
fn stop_left_behind(path: &Path, data_home: &Path) {
    let Some(rec) = record::read(path) else {
        return;
    };
    let note = running::elsewhere(data_home, &live);
    let mine = note.is_some_and(|note| {
        record::ours(
            &rec,
            &note,
            data_home,
            &running::group_of,
            &running::start_time,
        )
    });
    if mine {
        // Not this process's child: whoever adopted it reaps it.
        server::stop_group(rec.pgid, server::GRACE, &mut || true);
        record::remove(path);
    } else if server::group_gone(rec.pgid) {
        record::remove(path);
    }
}

fn failed(line: String) -> Option<Splash> {
    Some(Splash::with_lines(Phase::Failed, vec![line]))
}

/// One start, as the spec's Starting section. Returns what the splash should say if
/// the page could not be shown.
fn attempt_start(app: &AppHandle, attempt: u64) -> Option<Splash> {
    // The login shell first: the data folder and binary may be set there.
    let probe = shell::probe().unwrap_or_default();
    let env = server::child_env(probe.env, std::env::vars());
    let user_home = var(&env, "HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(PathBuf::from));
    let data = running::data_home(
        var(&env, "TRILLION_HOME"),
        user_home.as_deref().unwrap_or(Path::new("/")),
    );
    let records = record_path(app);
    if let Some(path) = &records {
        stop_left_behind(path, &data);
    }
    if let Some(note) = running::elsewhere(&data, &live) {
        return Some(Splash {
            state: Phase::Elsewhere,
            port: Some(note.port),
            lines: vec![],
        });
    }

    let bin = match locate::locate(
        var(&env, "TRILLION_BIN"),
        probe.bin.as_deref(),
        user_home.as_deref(),
        &locate::is_executable,
    ) {
        Located::Found(bin) => bin,
        Located::NotRunnable(bin) => {
            return failed(format!(
                "TRILLION_BIN is set to {}, which is not a program that can be run.",
                bin.display()
            ));
        }
        Located::Missing => return Some(Splash::of(Phase::Missing)),
    };
    let Some(port) = port::pick(&port::is_free) else {
        return failed(format!(
            "No free port from {} to {}.",
            port::FIRST,
            port::LAST
        ));
    };

    let local_bin = user_home.as_deref().map(|h| h.join(".local/bin"));
    let mut first: Vec<&Path> = bin.parent().into_iter().collect();
    first.extend(local_bin.as_deref());
    let env = server::with_path(env, &first);
    let token = token::new_token();
    let server = match Server::start(&bin, port, &token, env, user_home.as_deref()) {
        Ok(server) => Arc::new(server),
        Err(e) => return failed(format!("Couldn't run {}: {e}", bin.display())),
    };
    {
        let shell = app.state::<Shell>();
        let mut run = lock(&shell.run);
        if run.attempt != attempt {
            // Overtaken while it was starting: nobody else knows of this one.
            drop(run);
            server.stop();
            return None;
        }
        run.server = Some(server.clone());
    }
    if let Some(path) = &records {
        let note = Record {
            pgid: server.pgid,
            leader_start: running::start_time(server.pgid),
            launched_at_ms: now_ms(),
            data_home: data.clone(),
            port,
        };
        let _ = record::write(path, &note);
    }

    let deadline = Instant::now() + START_LIMIT;
    loop {
        // Overtaken: whoever overtook it took the server from the slot and stops it.
        if !current(app, attempt) {
            return None;
        }
        if server.exited() {
            finish(app, &server);
            return Some(Splash::with_lines(Phase::Failed, server.lines()));
        }
        if server::answers(port) {
            break;
        }
        if Instant::now() >= deadline {
            finish(app, &server);
            let mut lines = server.lines();
            lines.push(format!(
                "No answer on port {port} in {} seconds.",
                START_LIMIT.as_secs()
            ));
            return Some(Splash::with_lines(Phase::Failed, lines));
        }
        std::thread::sleep(POLL);
    }

    // Something answered; only the server holding the token can answer the challenge.
    // Another program that took the port in the meantime is never shown the token.
    let nonce = token::new_token();
    let proved =
        server::hello(port, &nonce).is_some_and(|mac| server::verify(&token, &nonce, &mac));
    if !proved || server.exited() {
        finish(app, &server);
        let mut lines = server.lines();
        lines.push(format!(
            "Something other than the Trillion this app started answered on port {port}."
        ));
        return Some(Splash::with_lines(Phase::Failed, lines));
    }

    // The token rides in the fragment, which the page reads and then removes.
    let Ok(page) = Url::parse(&format!("http://127.0.0.1:{port}/#token={token}")) else {
        finish(app, &server);
        return failed(format!("Couldn't make the page's address for port {port}."));
    };
    drop(token);
    allow_page(app, port);
    {
        // Under the run lock, so a Retry or Quit cannot slip in between.
        let shell = app.state::<Shell>();
        let run = lock(&shell.run);
        if run.attempt != attempt {
            return None;
        }
        *lock(&shell.page_port) = Some(port);
    }
    // Outside the lock: making a window waits on the main thread, which may want it.
    let page_port = |app: &AppHandle| *lock(&app.state::<Shell>().page_port);
    if let Err(e) = orb::point_at(app, port, page_port) {
        eprintln!("trillion: could not make the floating orb: {e}");
    }
    {
        let shell = app.state::<Shell>();
        let run = lock(&shell.run);
        if run.attempt != attempt {
            return None;
        }
        // Sent, not waited on, so holding the lock here is safe.
        if let Some(window) = app.get_webview_window(MAIN) {
            let _ = window.navigate(page);
        }
    }
    watch(app, attempt, &server);
    None
}

/// The capabilities for the server's page and the orb, for their exact origin and each
/// its own window: the bridge's commands each needs and listening for events, nothing
/// else. Added once per port: a
/// capability cannot be taken back, so a later start on another port leaves the
/// earlier port's in place. That one cannot be used: neither window will load that
/// origin again (`may_navigate`, the orb's own check), only the proven port's.
fn allow_page(app: &AppHandle, port: u16) {
    let shell = app.state::<Shell>();
    if !lock(&shell.capable).insert(port) {
        return;
    }
    let origin = format!("http://127.0.0.1:{port}/*");
    let page = CapabilityBuilder::new(format!("server-page-{port}"))
        .remote(origin.clone())
        .window(MAIN)
        .permission("core:event:allow-listen")
        .permission("core:event:allow-unlisten")
        .permission("allow-set-talk-shortcut")
        .permission("allow-report-state")
        .permission("allow-open-external");
    let orb = CapabilityBuilder::new(format!("server-orb-{port}"))
        .remote(origin)
        .window(orb::LABEL)
        .permission("core:event:allow-listen")
        .permission("core:event:allow-unlisten")
        .permission("allow-show-main");
    for capability in [page, orb] {
        if let Err(e) = app.add_capability(capability) {
            eprintln!("trillion: could not set what the page may do: {e}");
        }
    }
}

/// Waits for the server to end. If it ends on its own (not by Quit or a newer start),
/// the window goes back to the splash, stopped, with Restart.
fn watch(app: &AppHandle, attempt: u64, server: &Arc<Server>) {
    loop {
        std::thread::sleep(POLL);
        if !server.exited() {
            if !current(app, attempt) {
                return;
            }
            continue;
        }
        {
            let shell = app.state::<Shell>();
            let mut run = lock(&shell.run);
            if run.attempt != attempt {
                return;
            }
            if run.server.as_ref().is_some_and(|s| Arc::ptr_eq(s, server)) {
                run.server = None;
            }
        }
        // What it started may still be running; and its record goes.
        finish(app, server);
        set_splash(app, Splash::with_lines(Phase::Stopped, server.lines()));
        show_splash(app);
        return;
    }
}

/// The microphone is for the page of the server this app started, and nothing else.
/// This checks the window's top-level address: the server's page loads no frames from
/// anywhere else (its policy is `default-src 'self'`), and Tauri does not say which
/// frame asked.
fn grant(webview: Webview, kind: PermissionKind) -> PermissionResponse {
    let port = *lock(&webview.app_handle().state::<Shell>().page_port);
    let ours = match (webview.url(), port) {
        (Ok(url), Some(port)) => is_page(&url, port),
        _ => false,
    };
    match kind {
        PermissionKind::Microphone if ours => PermissionResponse::Allow,
        _ => PermissionResponse::Deny,
    }
}

fn is_page(url: &Url, port: u16) -> bool {
    url.scheme() == "http" && url.host_str() == Some("127.0.0.1") && url.port() == Some(port)
}

/// Where the main window may go: the bundled splash and the page of the server this
/// app started. Any other web address opens in the user's browser instead.
fn may_navigate(app: &AppHandle, url: &Url) -> bool {
    let shell = app.state::<Shell>();
    let splash = lock(&shell.splash_url).clone();
    let Some(splash) = splash else {
        // The window's first load, which is the splash, comes before its address is known.
        return true;
    };
    // Compared by parts: `tauri://localhost` has an opaque origin, never equal to itself.
    let same_place = url.scheme() == splash.scheme()
        && url.host_str() == splash.host_str()
        && url.port_or_known_default() == splash.port_or_known_default();
    if same_place || url.scheme() == "about" {
        return true;
    }
    if let Some(port) = *lock(&shell.page_port) {
        if is_page(url, port) {
            return true;
        }
    }
    // A page address with the token is never handed to another program.
    if matches!(url.scheme(), "http" | "https") && !carries_token(url) {
        let _ = app.opener().open_url(url.as_str(), None::<&str>);
    }
    false
}

fn carries_token(url: &Url) -> bool {
    url.fragment().is_some_and(|f| f.contains("token="))
}

#[derive(Serialize)]
struct Registered {
    global: bool,
}

/// The page's talk shortcut, made to work from any app. Each call replaces the last;
/// null takes it away. Anything D6 refuses is not registered (`global: false`).
#[tauri::command]
fn set_talk_shortcut(
    app: AppHandle,
    shell: State<'_, Shell>,
    accelerator: Option<String>,
    mode: String,
) -> Registered {
    let shortcuts = app.global_shortcut();
    // Replace, not add: the old one goes first, so a new one that fails to register
    // leaves none, and the page is told it is not global.
    let _ = shortcuts.unregister_all();
    let mut talk = lock(&shell.talk);
    talk.toggle = mode == "toggle";
    talk.accelerator = None;
    let global = match accelerator.as_deref() {
        Some(a) if talk::may_be_global(a) && talk::known_mode(&mode) => {
            shortcuts.register(a).is_ok()
        }
        _ => false,
    };
    if global {
        talk.accelerator = accelerator;
    }
    tray::set_talk_label(
        &app,
        talk.accelerator.as_deref().map(talk::label).as_deref(),
    );
    Registered { global }
}

#[derive(Clone, Serialize, Deserialize)]
struct OrbReport {
    state: String,
    caption: String,
}

/// The page's orb state and caption: to the orb window as they are, the menu-bar icon
/// when the state changes, and the orb shown or hidden by D5.
#[tauri::command]
fn report_state(app: AppHandle, shell: State<'_, Shell>, state: String, caption: String) {
    if !talk::known_state(&state) {
        return;
    }
    let caption = talk::clip(&caption, CAPTION_MAX).to_string();
    let (icon_changed, linger) = {
        let mut r = lock(&shell.reported);
        let (since, starts) = orb::idle_mark(&r.state, r.idle_since, &state, Instant::now());
        r.idle_since = since;
        r.state = state.clone();
        let icon = orb::icon_for(&state);
        let changed = r.icon != Some(icon);
        r.icon = Some(icon);
        (changed.then_some(icon), since.filter(|_| starts))
    };
    if let Some(icon) = icon_changed {
        tray::set_icon(&app, icon);
    }
    let _ = app.emit_to(orb::LABEL, "orb", OrbReport { state, caption });
    place_orb(&app);
    if let Some(since) = linger {
        // One look, once she has been idle long enough for it to go; only if she is
        // still in this same idle.
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(orb::LINGER + Duration::from_millis(50));
            if lock(&app.state::<Shell>().reported).idle_since == Some(since) {
                place_orb(&app);
            }
        });
    }
}

/// Shows or hides the orb for what she is doing and where the main window is.
fn place_orb(app: &AppHandle) {
    let main_away = app
        .get_webview_window(MAIN)
        .is_none_or(|w| !w.is_visible().unwrap_or(false) || !w.is_focused().unwrap_or(false));
    let shell = app.state::<Shell>();
    let mut r = lock(&shell.reported);
    let idle_for = r.idle_since.map(|t| t.elapsed());
    let show = orb::should_show(&r.state, main_away, r.orb_shown, idle_for);
    if show != r.orb_shown {
        r.orb_shown = show;
        drop(r);
        if show {
            orb::show(app);
        } else {
            orb::hide(app);
        }
    }
}

/// The orb was clicked: bring the main window forward.
#[tauri::command]
fn show_main(app: AppHandle) {
    show_main_window(&app);
}

/// A sign-in or a link: opened in the user's browser (D7), web addresses only.
#[tauri::command]
fn open_external(app: AppHandle, url: String) -> Result<(), String> {
    let url = talk::web_url(&url).ok_or("only web addresses open")?;
    // A page address with the token is never handed to another program.
    if carries_token(&url) {
        return Err("only web addresses open".into());
    }
    app.opener()
        .open_url(url.as_str(), None::<&str>)
        .map_err(|e| e.to_string())
}

/// The Talk item in the menu bar: in toggle mode it is the shortcut pressed and let
/// go; otherwise it brings the window forward to talk there.
pub(crate) fn talk_from_menu(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let toggle = {
        let talk = lock(&shell.talk);
        talk.toggle && talk.accelerator.is_some()
    };
    if toggle {
        send_talk(app, "down");
        send_talk(app, "up");
    } else {
        show_main_window(app);
    }
}

#[derive(Clone, Serialize)]
struct Talk {
    state: &'static str,
}

fn send_talk(app: &AppHandle, state: &'static str) {
    let _ = app.emit_to(MAIN, "talk", Talk { state });
}

/// The page that registered the talk shortcut is gone: so is the shortcut. The next
/// page registers its own.
fn forget_shortcut(app: &AppHandle) {
    let _ = app.global_shortcut().unregister_all();
    *lock(&app.state::<Shell>().talk) = TalkShortcut::default();
    tray::set_talk_label(app, None);
}

fn stop_server(app: &AppHandle) {
    forget_shortcut(app);
    let shell = app.state::<Shell>();
    let server = {
        // Any start or watch in flight gives up.
        let mut run = lock(&shell.run);
        run.attempt += 1;
        run.server.take()
    };
    if let Some(server) = server {
        finish(app, &server);
    }
}

/// A terminal's Ctrl+C or a logout's SIGTERM quits as Quit does, so the server stops
/// with the app. (A force quit cannot be caught: the server's lifeline and the record
/// of it see to that.)
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
        // The page opens its own links (desktop.ts `routeLinks`).
        .plugin(
            tauri_plugin_opener::Builder::new()
                .open_js_links_on_click(false)
                .build(),
        )
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    // Always, focused or not: while it is registered the page ignores the
                    // key itself, so this is its only source.
                    let state = match event.state {
                        ShortcutState::Pressed => "down",
                        ShortcutState::Released => "up",
                    };
                    send_talk(app, state);
                })
                .build(),
        )
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(
            // Size and place are remembered; whether it was hidden is not, so it opens shown.
            tauri_plugin_window_state::Builder::default()
                // The orb has its own place, under the menu bar.
                .with_denylist(&[orb::LABEL])
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
            open_in_browser,
            set_talk_shortcut,
            report_state,
            show_main,
            open_external
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let window =
                WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("splash.html".into()))
                    .title("Trillion")
                    .inner_size(1200.0, 800.0)
                    .min_inner_size(480.0, 360.0)
                    // Her voice runs in this page: hidden, it must keep its timers and audio.
                    .background_throttling(BackgroundThrottlingPolicy::Disabled)
                    .on_permission_request(grant)
                    .on_navigation(move |url| may_navigate(&handle, url))
                    .build()?;
            #[cfg(target_os = "macos")]
            webkit::keep_timers_when_hidden(&window);
            *lock(&app.state::<Shell>().splash_url) = window.url().ok();
            tray::build(app)?;
            quit_on_signals(app.handle());
            start(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| match event {
            // Closing hides: she keeps running in the menu bar (D4).
            WindowEvent::CloseRequested { api, .. } if window.label() == MAIN => {
                api.prevent_close();
                let _ = window.hide();
                place_orb(window.app_handle());
            }
            // The main window coming forward or going behind decides the orb.
            WindowEvent::Focused(_) if window.label() == MAIN => place_orb(window.app_handle()),
            _ => {}
        })
        .build(tauri::generate_context!())
        .expect("the Trillion app");
    app.run(|app, event| match event {
        RunEvent::Reopen { .. } => show_main_window(app),
        RunEvent::Exit => stop_server(app),
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_address_with_the_token_is_never_opened_elsewhere() {
        let at = |u: &str| Url::parse(u).unwrap();
        assert!(carries_token(&at("http://127.0.0.1:7779/#token=abc")));
        assert!(carries_token(&at("http://example.com/#x=1&token=abc")));
        assert!(!carries_token(&at(
            "https://accounts.google.com/o/oauth2?token=x"
        )));
        assert!(!carries_token(&at("https://example.com/#section")));
    }
}
