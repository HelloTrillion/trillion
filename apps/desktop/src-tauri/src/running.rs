//! The note a running server leaves in the data folder (`running.json`, written by
//! `trillion.build`). A live one means a server started elsewhere: the app does not
//! start a second (one writer to the database).

use std::path::{Path, PathBuf};

const NOTE_NAME: &str = "running.json";

/// The data folder: `TRILLION_HOME` when set, else `~/.trillion` (as `trillion.paths`).
pub fn data_home(env_home: Option<&str>, user_home: &Path) -> PathBuf {
    match env_home.filter(|h| !h.is_empty()) {
        Some(h) => match h.strip_prefix("~/") {
            Some(rest) => user_home.join(rest),
            None => PathBuf::from(h),
        },
        None => user_home.join(".trillion"),
    }
}

/// The pid and port the note names, or None when it does not make sense.
pub fn parse(text: &str) -> Option<(i32, u16)> {
    let note: serde_json::Value = serde_json::from_str(text).ok()?;
    let pid = i32::try_from(note.get("pid")?.as_i64()?).ok()?;
    let port = u16::try_from(note.get("port")?.as_u64()?).ok()?;
    // 0 and negative ids name process groups to kill(2), not a process.
    (pid > 0).then_some((pid, port))
}

/// The port of a server already running from this data folder, if its process lives.
pub fn elsewhere(home: &Path, alive: &dyn Fn(i32) -> bool) -> Option<u16> {
    let text = std::fs::read_to_string(home.join(NOTE_NAME)).ok()?;
    let (pid, port) = parse(&text)?;
    alive(pid).then_some(port)
}

/// Whether a process with this id exists. Asking a process we may not signal still
/// says it exists.
pub fn pid_alive(pid: i32) -> bool {
    if pid <= 0 {
        return false;
    }
    // SAFETY: signal 0 only checks that the process exists; nothing is sent.
    let found = unsafe { libc::kill(pid, 0) } == 0;
    found || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "trillion-desktop-running-{name}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    const NOTE: &str =
        r#"{"pid": 4242, "port": 7790, "version": "0.0.1", "fingerprint": "abc", "started_at": 1}"#;

    #[test]
    fn the_data_folder() {
        let home = Path::new("/Users/u");
        assert_eq!(data_home(None, home), PathBuf::from("/Users/u/.trillion"));
        assert_eq!(
            data_home(Some(""), home),
            PathBuf::from("/Users/u/.trillion")
        );
        assert_eq!(data_home(Some("/tmp/t"), home), PathBuf::from("/tmp/t"));
        assert_eq!(data_home(Some("~/t"), home), PathBuf::from("/Users/u/t"));
    }

    #[test]
    fn no_file_is_none() {
        let dir = scratch("none");
        assert_eq!(elsewhere(&dir, &|_| true), None);
    }

    #[test]
    fn a_dead_pid_is_none() {
        let dir = scratch("dead");
        fs::write(dir.join("running.json"), NOTE).unwrap();
        assert_eq!(elsewhere(&dir, &|_| false), None);
    }

    #[test]
    fn a_live_pid_is_elsewhere_on_its_port() {
        let dir = scratch("live");
        fs::write(dir.join("running.json"), NOTE).unwrap();
        assert_eq!(elsewhere(&dir, &|pid| pid == 4242), Some(7790));
    }

    #[test]
    fn garbage_is_none() {
        for text in [
            "",
            "not json",
            "[]",
            r#"{"pid": "x", "port": 7790}"#,
            r#"{"pid": 4242}"#,
            r#"{"pid": 4242, "port": 99999}"#,
            r#"{"pid": -1, "port": 7790}"#,
            r#"{"pid": 0, "port": 7790}"#,
        ] {
            assert_eq!(parse(text), None, "{text}");
        }
        let dir = scratch("garbage");
        fs::write(dir.join("running.json"), "{oops").unwrap();
        assert_eq!(elsewhere(&dir, &|_| true), None);
    }

    #[test]
    fn this_process_is_alive_and_a_huge_pid_is_not() {
        assert!(pid_alive(std::process::id() as i32));
        assert!(!pid_alive(99_999_999));
    }
}
