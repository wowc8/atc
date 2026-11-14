// Tauri 2 desktop shell — owns the Python backend sidecar.

use std::{fs::OpenOptions, io::Write, path::PathBuf, sync::Mutex};
use tauri::Manager;
use tauri_plugin_shell::{process::CommandChild, process::CommandEvent, ShellExt};

#[derive(Default)]
struct BackendState {
    error: Mutex<Option<String>>,
    child: Mutex<Option<CommandChild>>,
}

#[tauri::command]
fn backend_startup_error(state: tauri::State<'_, BackendState>) -> Option<String> {
    state.error.lock().unwrap().clone()
}

// Do not fall back to the inherited cwd (Finder can launch from /), or silently
// move existing ~/atc.db and config files to a new empty application data store.
fn runtime_home(home: PathBuf) -> std::io::Result<PathBuf> {
    if !home.is_absolute() || !home.is_dir() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "ATC requires an existing absolute home directory",
        ));
    }
    Ok(home)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(BackendState::default())
        .invoke_handler(tauri::generate_handler![backend_startup_error])
        .setup(|app| {
            #[cfg(desktop)]
            {
                app.handle().plugin(tauri_plugin_process::init())?;
                app.handle().plugin(tauri_plugin_shell::init())?;

                // Keep the window alive on setup failure so the startup gate can
                // show a useful error instead of an invisible crash/spinner.
                let start = || -> Result<(), Box<dyn std::error::Error>> {
                    let home = runtime_home(app.path().home_dir()?)?;
                    let logs = home.join(".atc").join("logs");
                    std::fs::create_dir_all(&logs)?;
                    let log_path = logs.join("desktop-backend.log");
                    let mut log = OpenOptions::new()
                        .create(true)
                        .truncate(true)
                        .write(true)
                        .open(&log_path)?;
                    writeln!(log, "Starting atc-server in {}", home.display())?;
                    let (mut rx, child) = app
                        .shell()
                        .sidecar("atc-server")?
                        .current_dir(home)
                        // PyInstaller's bootloader has a worker child; own the
                        // group so normal app exit does not leave that listener.
                        .set_process_group(true)
                        .spawn()?;
                    *app.state::<BackendState>().child.lock().unwrap() = Some(child);
                    let handle = app.handle().clone();
                    tauri::async_runtime::spawn(async move {
                        while let Some(event) = rx.recv().await {
                            let failure = match event {
                                CommandEvent::Stdout(bytes) | CommandEvent::Stderr(bytes) => {
                                    // Drain both pipes continuously; never discard the receiver.
                                    let _ = log.write_all(&bytes);
                                    let _ = log.write_all(b"\n");
                                    None
                                }
                                CommandEvent::Error(error) => {
                                    Some(format!("Backend process error: {error}"))
                                }
                                CommandEvent::Terminated(status) => Some(format!(
                                    "Backend exited (code {:?}, signal {:?})",
                                    status.code, status.signal
                                )),
                                _ => None,
                            };
                            if let Some(failure) = failure {
                                let _ = writeln!(log, "{failure}");
                                *handle.state::<BackendState>().error.lock().unwrap() =
                                    Some(format!("{failure}. See {}", log_path.display()));
                            }
                        }
                    });
                    Ok(())
                };
                if let Err(error) = start() {
                    *app.state::<BackendState>().error.lock().unwrap() =
                        Some(format!("Unable to start ATC backend: {error}"));
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");
    app.run(|handle, event| {
        if let tauri::RunEvent::Exit = event {
            // Kill only the child owned by this app, never an existing listener.
            if let Some(child) = handle.state::<BackendState>().child.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn desktop_uses_existing_absolute_home_without_moving_legacy_files() {
        let home = std::env::temp_dir();
        assert_eq!(runtime_home(home.clone()).unwrap(), home);
    }

    #[test]
    fn invalid_home_never_falls_back_to_launch_directory() {
        assert!(runtime_home(PathBuf::from("relative-home")).is_err());
        assert!(runtime_home(std::env::temp_dir().join("atc-nonexistent-home-346")).is_err());
    }
}
