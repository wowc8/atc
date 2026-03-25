// Tauri 2 desktop shell — spawns Python backend sidecar on startup.

use tauri::Manager;
use tauri_plugin_shell::ShellExt;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            #[cfg(desktop)]
            {
                app.handle().plugin(tauri_plugin_process::init())?;
                app.handle().plugin(tauri_plugin_shell::init())?;

                // Spawn Python backend sidecar
                let sidecar_command = app.shell().sidecar("atc-server").unwrap();
                let (_rx, _child) = sidecar_command.spawn().unwrap();
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
