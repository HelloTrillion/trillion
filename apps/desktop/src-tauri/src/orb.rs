//! The floating orb (D5): a small window over every app while she is talked to from
//! elsewhere. It never takes focus, so the app the user is in stays in front.

use std::time::Duration;

use tauri::{
    AppHandle, LogicalPosition, Manager, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};

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

/// Makes the orb window for the server's page on `port`, hidden; replaces one made
/// for an earlier port.
pub fn create(app: &AppHandle, port: u16) -> tauri::Result<WebviewWindow> {
    if let Some(old) = app.get_webview_window(LABEL) {
        let _ = old.destroy();
    }
    let page = Url::parse(&format!("http://127.0.0.1:{port}/orb.html"))
        .map_err(|e| tauri::Error::AssetNotFound(e.to_string()))?;
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
        .on_navigation(move |url| {
            url.scheme() == "http"
                && url.host_str() == Some("127.0.0.1")
                && url.port() == Some(port)
        })
        .build()?;
    Ok(window)
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
    fn three_icons() {
        assert_eq!(icon_for("idle"), Icon::Idle);
        assert_eq!(icon_for("listening"), Icon::Listening);
        assert_eq!(icon_for("thinking"), Icon::Listening);
        assert_eq!(icon_for("speaking"), Icon::Speaking);
        assert_eq!(icon_for("other"), Icon::Idle);
    }
}
