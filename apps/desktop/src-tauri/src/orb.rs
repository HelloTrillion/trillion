//! The floating orb (D5): a small window over every app while she is talked to from
//! elsewhere. It never takes focus, so the app the user is in stays in front.

use std::time::{Duration, Instant};

use tauri::{AppHandle, LogicalPosition, Manager, Url, WebviewUrl, WebviewWindowBuilder};

pub const LABEL: &str = "orb";
const WIDTH: f64 = 220.0;
const HEIGHT: f64 = 120.0;
/// From the right edge of the screen and from the menu bar.
const MARGIN: f64 = 12.0;
/// How long the orb stays after she finishes.
pub const LINGER: Duration = Duration::from_secs(3);

/// Whether the orb should be showing. While she is busy, it shows if the main window
/// is hidden or not in front; once she is idle it stays `LINGER` and goes. It is never
/// shown just because she went idle.
pub fn should_show(state: &str, main_away: bool, shown: bool, idle_for: Option<Duration>) -> bool {
    if !main_away {
        return false;
    }
    if state != "idle" {
        return true;
    }
    shown && idle_for.is_some_and(|d| d < LINGER)
}

/// When she went idle, after a report of `next` that follows `prev`, and whether this
/// report starts the linger (so one later look hides the orb).
pub fn idle_mark(
    prev: &str,
    idle_since: Option<Instant>,
    next: &str,
    now: Instant,
) -> (Option<Instant>, bool) {
    if next != "idle" {
        return (None, false);
    }
    match idle_since {
        Some(since) if prev == "idle" => (Some(since), false),
        _ => (Some(now), true),
    }
}

/// The orb loads only the page of the server this app proved is its own.
pub fn may_load(url: &Url, page_port: Option<u16>) -> bool {
    page_port.is_some_and(|port| {
        url.scheme() == "http" && url.host_str() == Some("127.0.0.1") && url.port() == Some(port)
    })
}

/// Which of the three menu-bar icons a state uses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Icon {
    Idle,
    Listening,
    Speaking,
}

pub fn icon_for(state: &str) -> Icon {
    match state {
        // Thinking is part of answering what she heard.
        "listening" | "thinking" => Icon::Listening,
        "speaking" => Icon::Speaking,
        _ => Icon::Idle,
    }
}

/// Points the orb at the server's page on `port`: the one orb window, made hidden the
/// first time and moved to the new page after a restart. `page_port` must already be
/// set, since the window's own check reads it.
pub fn point_at(
    app: &AppHandle,
    port: u16,
    page_port: impl Fn(&AppHandle) -> Option<u16> + Send + 'static,
) -> tauri::Result<()> {
    let page = Url::parse(&format!("http://127.0.0.1:{port}/orb.html"))
        .map_err(|e| tauri::Error::AssetNotFound(e.to_string()))?;
    if let Some(window) = app.get_webview_window(LABEL) {
        let _ = window.hide();
        return window.navigate(page);
    }
    let handle = app.clone();
    let window = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::External(page))
        .title("Trillion")
        .inner_size(WIDTH, HEIGHT)
        .resizable(false)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .always_on_top(true)
        .visible_on_all_workspaces(true)
        .skip_taskbar(true)
        // After `transparent`, which asks for focus: this window never takes it, so
        // showing it leaves the user in the app they are using.
        .focused(false)
        .focusable(false)
        // A click goes to the orb at once, without first bringing the app forward.
        .accept_first_mouse(true)
        .visible(false)
        .on_navigation(move |url| may_load(url, page_port(&handle)))
        .build()?;
    // AppKit is touched on the main thread only.
    #[cfg(target_os = "macos")]
    {
        let on_main = window.clone();
        app.run_on_main_thread(move || over_full_screen(&on_main))?;
    }
    #[cfg(not(target_os = "macos"))]
    let _ = window;
    Ok(())
}

/// Lets the orb show over an app in full screen, as well as on every Space, at the
/// level of the menu bar's own status items. Checked before use, as webkit.rs does.
#[cfg(target_os = "macos")]
fn over_full_screen(window: &tauri::WebviewWindow) {
    use objc2::msg_send;
    use objc2::runtime::{AnyObject, Bool};
    use objc2::sel;
    // NSWindowCollectionBehavior: CanJoinAllSpaces | FullScreenAuxiliary.
    const ALL_SPACES: usize = 1 << 0;
    const FULL_SCREEN_AUXILIARY: usize = 1 << 8;
    // NSStatusWindowLevel.
    const STATUS_LEVEL: isize = 25;
    let Ok(ns) = window.ns_window() else {
        return;
    };
    let ns: *mut AnyObject = ns.cast();
    if ns.is_null() {
        return;
    }
    // SAFETY: `ns` is this window's live NSWindow; each selector is checked first.
    unsafe {
        let can: Bool = msg_send![ns, respondsToSelector: sel!(setCollectionBehavior:)];
        if can.as_bool() {
            let now: usize = msg_send![ns, collectionBehavior];
            let _: () =
                msg_send![ns, setCollectionBehavior: now | ALL_SPACES | FULL_SCREEN_AUXILIARY];
        }
        let can: Bool = msg_send![ns, respondsToSelector: sel!(setLevel:)];
        if can.as_bool() {
            let _: () = msg_send![ns, setLevel: STATUS_LEVEL];
        }
    }
}

/// Shows the orb at the top right of the main screen, under the menu bar.
pub fn show(app: &AppHandle) {
    let Some(window) = app.get_webview_window(LABEL) else {
        return;
    };
    if let Ok(Some(screen)) = app.primary_monitor() {
        let scale = screen.scale_factor();
        let area = screen.work_area();
        let x = (area.position.x as f64 + area.size.width as f64) / scale - WIDTH - MARGIN;
        let y = area.position.y as f64 / scale + MARGIN;
        let _ = window.set_position(LogicalPosition::new(x, y));
    }
    let _ = window.show();
}

pub fn hide(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(LABEL) {
        let _ = window.hide();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const S: Duration = Duration::from_secs(1);

    #[test]
    fn busy_with_the_main_window_away_shows() {
        for state in ["listening", "thinking", "speaking"] {
            assert!(should_show(state, true, false, None), "{state}");
            assert!(should_show(state, true, true, None), "{state}");
        }
    }

    #[test]
    fn the_main_window_in_front_never_shows_it() {
        assert!(!should_show("listening", false, true, None));
        assert!(!should_show("idle", false, true, Some(S)));
    }

    #[test]
    fn idle_lingers_then_goes() {
        assert!(should_show("idle", true, true, Some(Duration::ZERO)));
        assert!(should_show("idle", true, true, Some(2 * S)));
        assert!(!should_show("idle", true, true, Some(3 * S)));
        assert!(!should_show("idle", true, true, None));
    }

    #[test]
    fn idle_alone_never_brings_it_up() {
        assert!(!should_show("idle", true, false, Some(Duration::ZERO)));
    }

    #[test]
    fn the_orb_loads_only_the_proven_port() {
        let at = |u: &str| Url::parse(u).unwrap();
        assert!(may_load(&at("http://127.0.0.1:7778/orb.html"), Some(7778)));
        assert!(!may_load(&at("http://127.0.0.1:7779/orb.html"), Some(7778)));
        assert!(!may_load(&at("http://127.0.0.1:7778/orb.html"), None));
        assert!(!may_load(&at("https://example.com/"), Some(7778)));
        assert!(!may_load(&at("http://localhost:7778/orb.html"), Some(7778)));
    }

    #[test]
    fn a_new_idle_starts_the_linger_once() {
        let t0 = Instant::now();
        let t1 = t0 + S;
        // Into idle: marked now, one look later.
        assert_eq!(idle_mark("speaking", None, "idle", t1), (Some(t1), true));
        assert_eq!(idle_mark("", None, "idle", t1), (Some(t1), true));
        // Still idle (a caption-only report): the mark stays, no new look.
        assert_eq!(idle_mark("idle", Some(t0), "idle", t1), (Some(t0), false));
        // Busy again: no mark.
        assert_eq!(idle_mark("idle", Some(t0), "listening", t1), (None, false));
    }

    #[test]
    fn three_icons() {
        assert_eq!(icon_for("idle"), Icon::Idle);
        assert_eq!(icon_for("listening"), Icon::Listening);
        assert_eq!(icon_for("thinking"), Icon::Listening);
        assert_eq!(icon_for("speaking"), Icon::Speaking);
        assert_eq!(icon_for("other"), Icon::Idle);
    }
}
