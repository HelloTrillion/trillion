fn main() {
    // The app's own commands are allowed only where a capability names them; the
    // server's page gets only those `allow_page` grants it in lib.rs.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "splash_state",
            "retry",
            "open_in_browser",
            "set_talk_shortcut",
            "report_state",
            "show_main",
            "open_external",
            "pick_folder",
        ]),
    ))
    .expect("tauri build script");
}
