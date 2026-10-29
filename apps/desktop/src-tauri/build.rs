fn main() {
    // The app's own commands are allowed only where a capability names them, so the
    // server's page gets none until a later change lists them.
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["splash_state", "retry", "open_in_browser"]),
    ))
    .expect("tauri build script");
}
