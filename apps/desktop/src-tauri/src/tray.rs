//! The menu-bar icon: Open Trillion, Talk, Open at login, Quit Trillion.

use tauri::image::Image;
use tauri::menu::{CheckMenuItem, Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{App, AppHandle, Wry};
use tauri_plugin_autostart::ManagerExt;

const OPEN: &str = "open";
const TALK: &str = "talk";
const LOGIN: &str = "login";
const QUIT: &str = "quit";

/// A template image: macOS draws it in the menu bar's own colour.
const IDLE: &[u8] = include_bytes!("../icons/tray-idle.png");

pub fn build(app: &App) -> tauri::Result<()> {
    let at_login = app.autolaunch().is_enabled().unwrap_or(false);
    let open = MenuItem::with_id(app, OPEN, "Open Trillion", true, None::<&str>)?;
    // The talk shortcut is the page's; the menu names it once the page reports it.
    let talk = MenuItem::with_id(app, TALK, "Talk", false, None::<&str>)?;
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
    TrayIconBuilder::with_id("main")
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
        OPEN => crate::show_main(app),
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
