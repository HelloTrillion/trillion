// A windowed program on Windows, so no console window opens beside it (OPS-3).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    trillion_desktop_lib::run()
}
