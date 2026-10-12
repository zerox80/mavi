use std::fs::{self, File};
use std::io::{self, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use tracing_subscriber::filter::EnvFilter;
use tracing_subscriber::fmt::MakeWriter;

const MAX_LOG_BYTES: u64 = 10 * 1024 * 1024;
const LOG_DACL: &str = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)";

#[derive(Clone)]
struct SharedLogFile {
    state: Arc<Mutex<SharedLogState>>,
    console: bool,
}

struct SharedLogState {
    path: PathBuf,
    file: Option<File>,
    // Keep every ancestor pinned while writing and while rotating/reopening.
    _directories: Vec<File>,
}

struct SharedLogWriter {
    state: Arc<Mutex<SharedLogState>>,
    console: bool,
}

impl<'a> MakeWriter<'a> for SharedLogFile {
    type Writer = SharedLogWriter;

    fn make_writer(&'a self) -> Self::Writer {
        SharedLogWriter {
            state: Arc::clone(&self.state),
            console: self.console,
        }
    }
}

impl Write for SharedLogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| io::Error::other("log file lock poisoned"))?;
        state.rotate_if_needed()?;
        let written = state.file_mut()?.write(buf)?;
        if self.console {
            let _ = io::stderr().write_all(buf);
        }
        Ok(written)
    }

    fn flush(&mut self) -> io::Result<()> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| io::Error::other("log file lock poisoned"))?;
        state.file_mut()?.flush()?;
        if self.console {
            let _ = io::stderr().flush();
        }
        Ok(())
    }
}

impl SharedLogState {
    fn file_mut(&mut self) -> io::Result<&mut File> {
        self.file
            .as_mut()
            .ok_or_else(|| io::Error::other("log file is temporarily unavailable"))
    }

    fn rotate_if_needed(&mut self) -> io::Result<()> {
        if self.file_mut()?.metadata()?.len() < MAX_LOG_BYTES {
            return Ok(());
        }

        if let Some(mut file) = self.file.take() {
            file.flush()?;
        }
        rotate_log_file(&self.path)?;
        self.file = Some(open_append_file(&self.path)?);
        Ok(())
    }
}

pub fn init_service_logging(console: bool) -> Option<PathBuf> {
    let env_filter = default_env_filter();
    let log_path = service_log_dir().join("mavi-vpn-service.log");

    match open_log_file(&log_path) {
        Ok(state) => {
            let writer = SharedLogFile {
                state: Arc::new(Mutex::new(state)),
                console,
            };
            if tracing_subscriber::fmt()
                .with_env_filter(env_filter)
                .with_ansi(false)
                .with_target(true)
                .with_thread_ids(true)
                .with_line_number(true)
                .with_writer(writer)
                .try_init()
                .is_ok()
            {
                Some(log_path)
            } else {
                None
            }
        }
        Err(error) => {
            eprintln!(
                "Failed to open service log file at {}: {error}",
                log_path.display()
            );
            let _ = tracing_subscriber::fmt()
                .with_env_filter(env_filter)
                .with_target(true)
                .with_thread_ids(true)
                .with_line_number(true)
                .try_init();
            None
        }
    }
}

pub fn init_console_logging() {
    let _ = tracing_subscriber::fmt()
        .with_env_filter(default_env_filter())
        .with_target(true)
        .with_thread_ids(true)
        .with_line_number(true)
        .try_init();
}

fn default_env_filter() -> EnvFilter {
    std::env::var("RUST_LOG").map_or_else(
        |_| {
            EnvFilter::new(
                "info,mavi_vpn_service=debug,windows_vpn=debug,shared=debug,wintun=off,hyper=warn,h2=warn,reqwest=warn,rustls=warn",
            )
        },
        EnvFilter::new,
    )
}

fn service_log_dir() -> PathBuf {
    std::env::var_os("PROGRAMDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\ProgramData"))
        .join("mavi-vpn")
        .join("logs")
}

fn open_log_file(path: &Path) -> io::Result<SharedLogState> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("Service log path has no parent"))?;
    let directories =
        crate::secure_path::lock_directory_tree(parent, true).map_err(io::Error::other)?;
    crate::secure_path::replace_dacl(parent, LOG_DACL).map_err(io::Error::other)?;
    let mut state = SharedLogState {
        path: path.to_owned(),
        file: Some(open_append_file(path)?),
        _directories: directories,
    };
    state.rotate_if_needed()?;
    Ok(state)
}

fn open_append_file(path: &Path) -> io::Result<File> {
    // Validate the same handle that will be written: no following reparse
    // points, no hard links, no untrusted owners, and no concurrent writers.
    let mut file = crate::secure_path::lock_writable_file(path).map_err(io::Error::other)?;
    crate::secure_path::ensure_trusted_owner(&file).map_err(io::Error::other)?;
    crate::secure_path::replace_dacl(path, LOG_DACL).map_err(io::Error::other)?;
    file.seek(SeekFrom::End(0))?;
    Ok(file)
}

fn rotate_log_file(path: &Path) -> io::Result<()> {
    // The validated, hardened parent is still pinned after the file handle
    // closes, so an unprivileged process cannot redirect either operation.
    let old_path = path.with_extension("log.old");
    match fs::remove_file(&old_path) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    fs::rename(path, old_path)
}

#[cfg(test)]
#[path = "logging/tests.rs"]
mod tests;
