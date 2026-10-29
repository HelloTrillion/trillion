//! The menu-bar icon: Open Trillion, Talk, Open at login, Quit Trillion.

use tauri::image::Image;
use tauri::menu::{CheckMenuItem, Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{App, AppHandle, Manager, Wry};
use tauri_plugin_autostart::ManagerExt;

const OPEN: &str = "open";
const TALK: &str = "talk";
const LOGIN: &str = "login";
const QUIT: &str = "quit";

/// Template images: macOS draws them in the menu bar's own colour.
const IDLE: &[u8] = include_bytes!("../icons/tray-idle.png");
const LISTENING: &[u8] = include_bytes!("../icons/tray-listening.png");
const SPEAKING: &[u8] = include_bytes!("../icons/tray-speaking.png");
const TRAY: &str = "main";

/// The Talk item, kept to name the shortcut on it.
struct TalkItem(MenuItem<Wry>);

pub fn build(app: &App) -> tauri::Result<()> {
    let at_login = app.autolaunch().is_enabled().unwrap_or(false);
    let open = MenuItem::with_id(app, OPEN, "Open Trillion", true, None::<&str>)?;
    // The talk shortcut is the page's; the item names it once the page registers it.
    let talk = MenuItem::with_id(app, TALK, "Talk", true, None::<&str>)?;
    app.manage(TalkItem(talk.clone()));
    let login = CheckMenuItem::with_id(app, LOGIN, "Open at login", true, at_login, None::<&str>)?;
    let quit = MenuItem::with_id(app, QUIT, "Quit Trillion", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &open,
            &talk,
            &PredefinedMenuItem::separator(app)?,
            &login,
            &PredefinedMenuItem::separator(app)?,
            &quit,
        ],
    )?;
    TrayIconBuilder::with_id(TRAY)
        .icon(Image::from_bytes(IDLE)?)
        .icon_as_template(true)
        .tooltip("Trillion")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(move |app, event| on_menu(app, &event, &login))
        .build(app)?;
    Ok(())
}

fn on_menu(app: &AppHandle, event: &MenuEvent, login: &CheckMenuItem<Wry>) {
    match event.id().as_ref() {
        OPEN => crate::show_main_window(app),
        TALK => crate::talk_from_menu(app),
        LOGIN => {
            let launcher = app.autolaunch();
            let on = launcher.is_enabled().unwrap_or(false);
            let _ = if on {
                launcher.disable()
            } else {
                launcher.enable()
            };
            // Show what is true now, whether or not the change took.
            let _ = login.set_checked(launcher.is_enabled().unwrap_or(on));
        }
        QUIT => app.exit(0),
        _ => {}
    }
}

/// Names the shortcut on the Talk item, or none.
pub fn set_talk_label(app: &AppHandle, shortcut: Option<&str>) {
    let text = match shortcut {
        Some(label) => format!("Talk ({label})"),
        None => "Talk".to_string(),
    };
    if let Some(item) = app.try_state::<TalkItem>() {
        let _ = item.0.set_text(text);
    }
}

pub fn set_icon(app: &AppHandle, icon: crate::orb::Icon) {
    let bytes = match icon {
        crate::orb::Icon::Idle => IDLE,
        crate::orb::Icon::Listening => LISTENING,
        crate::orb::Icon::Speaking => SPEAKING,
    };
    if let (Some(tray), Ok(image)) = (app.tray_by_id(TRAY), Image::from_bytes(bytes)) {
        let _ = tray.set_icon_with_as_template(Some(image), true);
    }
}
