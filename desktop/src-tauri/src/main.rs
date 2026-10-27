// Prevents an extra console window on Windows in release. DO NOT REMOVE.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // 0.20.0: started by Chrome / Edge as the extension's native messaging
    // host (native_host.rs): no window, no single-instance, just the pipe.
    if let Some(origin) = vidgrab_desktop_lib::native_host_origin() {
        std::process::exit(vidgrab_desktop_lib::run_native_host(origin));
    }
    vidgrab_desktop_lib::run()
}
