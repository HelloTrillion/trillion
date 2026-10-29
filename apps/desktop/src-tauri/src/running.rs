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

/// What a running server noted about itself.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Note {
    pub pid: i32,
    pub port: u16,
    /// When it wrote the note, in milliseconds since the epoch (`trillion.build`).
    pub started_at: i64,
}

/// The note, or None when it does not make sense.
pub fn parse(text: &str) -> Option<Note> {
    let note: serde_json::Value = serde_json::from_str(text).ok()?;
    let pid = i32::try_from(note.get("pid")?.as_i64()?).ok()?;
    let port = u16::try_from(note.get("port")?.as_u64()?).ok()?;
    let started_at = note.get("started_at")?.as_i64()?;
    // 0 and negative ids name process groups to kill(2), not a process.
    (pid > 0).then_some(Note {
        pid,
        port,
        started_at,
    })
}

/// The note in this data folder, if `live` says its server is still running.
pub fn elsewhere(home: &Path, live: &dyn Fn(&Note) -> bool) -> Option<Note> {
    let text = std::fs::read_to_string(home.join(NOTE_NAME)).ok()?;
    let note = parse(&text)?;
    live(&note).then_some(note)
}

/// How long after its process starts a server may take to write its note: imports,
/// opening the database, a slow disk.
const NOTE_WITHIN_S: i64 = 300;

/// Whether a process that started at `start_s` (seconds since the epoch) can be the
/// one that wrote a note at `started_at_ms`. A pid reused after that server died
/// started after its note, so it cannot.
pub fn started_near(start_s: i64, started_at_ms: i64) -> bool {
    let noted_s = started_at_ms.div_euclid(1000);
    // The process start is in whole seconds: allow the second it falls in.
    start_s <= noted_s + 1 && noted_s - start_s <= NOTE_WITHIN_S
}

/// When a process started, in seconds since the epoch, or None if it cannot be read.
pub fn start_time(pid: i32) -> Option<i64> {
    if pid <= 0 {
        return None;
    }
    let mut info: libc::proc_bsdinfo = unsafe { std::mem::zeroed() };
    let size = std::mem::size_of::<libc::proc_bsdinfo>() as libc::c_int;
    // SAFETY: proc_pidinfo writes at most `size` bytes into `info`.
    let got = unsafe {
        libc::proc_pidinfo(
            pid,
            libc::PROC_PIDTBSDINFO,
            0,
            (&mut info as *mut libc::proc_bsdinfo).cast(),
            size,
        )
    };
    (got == size).then_some(info.pbi_start_tvsec as i64)
}

/// The process group a process is in, if it exists.
pub fn group_of(pid: i32) -> Option<i32> {
    // SAFETY: only asks.
    let group = unsafe { libc::getpgid(pid) };
    (pid > 0 && group > 0).then_some(group)
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
    fn a_dead_server_is_none() {
        let dir = scratch("dead");
        fs::write(dir.join("running.json"), NOTE).unwrap();
        assert_eq!(elsewhere(&dir, &|_| false), None);
    }

    #[test]
    fn a_live_server_is_elsewhere_on_its_port() {
        let dir = scratch("live");
        fs::write(dir.join("running.json"), NOTE).unwrap();
        let note = elsewhere(&dir, &|n| n.pid == 4242).unwrap();
        assert_eq!(
            note,
            Note {
                pid: 4242,
                port: 7790,
                started_at: 1
            }
        );
    }

    #[test]
    fn garbage_is_none() {
        for text in [
            "",
            "not json",
            "[]",
            r#"{"pid": "x", "port": 7790, "started_at": 1}"#,
            r#"{"pid": 4242}"#,
            r#"{"pid": 4242, "port": 7790}"#,
            r#"{"pid": 4242, "port": 99999, "started_at": 1}"#,
            r#"{"pid": -1, "port": 7790, "started_at": 1}"#,
            r#"{"pid": 0, "port": 7790, "started_at": 1}"#,
        ] {
            assert_eq!(parse(text), None, "{text}");
        }
        let dir = scratch("garbage");
        fs::write(dir.join("running.json"), "{oops").unwrap();
        assert_eq!(elsewhere(&dir, &|_| true), None);
    }

    #[test]
    fn a_note_written_after_its_process_started_matches() {
        let start = 1_800_000_000;
        assert!(started_near(start, start * 1000 + 2_500));
        assert!(started_near(start, start * 1000 + 290_000));
        assert!(started_near(start, start * 1000)); // the same second
        assert!(started_near(start + 1, start * 1000 + 999)); // whole-second rounding
    }

    #[test]
    fn a_reused_pid_does_not_match() {
        let noted = 1_800_000_000_000; // ms
                                       // Started after the note was written: a new process with the old pid.
        assert!(!started_near(1_800_000_005, noted));
        // Started long before: not the server that wrote it moments after starting.
        assert!(!started_near(1_799_999_000, noted));
    }

    #[test]
    fn this_process_start_time_is_readable() {
        let me = start_time(std::process::id() as i32).unwrap();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;
        assert!(me <= now && now - me < 3600);
        assert_eq!(start_time(99_999_999), None);
        let me = std::process::id() as i32;
        assert_eq!(group_of(me), Some(unsafe { libc::getpgrp() }));
        assert_eq!(group_of(99_999_999), None);
    }

    #[test]
    fn this_process_is_alive_and_a_huge_pid_is_not() {
        assert!(pid_alive(std::process::id() as i32));
        assert!(!pid_alive(99_999_999));
    }
}
