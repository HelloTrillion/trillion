//! What the app writes down about the server it started, so a launch after a crash or
//! a force quit can stop that server instead of taking it for one started elsewhere.
//! Holds no token (SPN-11a).

use std::io::Write;
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::running::Note;

/// How soon after the app launched it a server of ours writes its note.
const NOTE_WITHIN_MS: i64 = 60_000;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Record {
    /// The server's process group, which is its leader's pid.
    pub pgid: i32,
    /// When the leader started, in seconds since the epoch, if it could be read.
    pub leader_start: Option<i64>,
    /// When the app launched it, in milliseconds since the epoch.
    pub launched_at_ms: i64,
    pub data_home: PathBuf,
    pub port: u16,
}

/// Owner-only, and whole or not at all. On Windows the app's data folder is already
/// the user's own (under their profile).
pub fn write(path: &Path, record: &Record) -> std::io::Result<()> {
    if let Some(folder) = path.parent() {
        std::fs::create_dir_all(folder)?;
    }
    let temporary = path.with_extension(format!("json.{}.tmp", std::process::id()));
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options.open(&temporary)?;
    file.write_all(&serde_json::to_vec(record)?)?;
    file.sync_all()?;
    std::fs::rename(&temporary, path)
}

pub fn read(path: &Path) -> Option<Record> {
    serde_json::from_slice(&std::fs::read(path).ok()?).ok()
}

pub fn remove(path: &Path) {
    let _ = std::fs::remove_file(path);
}

/// Whether the server a note names is the one this record describes: in the same
/// process group, on the same data folder, and either its leader is the very process
/// recorded or, the leader gone, the note was written soon after the launch.
pub fn ours(
    record: &Record,
    note: &Note,
    data_home: &Path,
    group_of: &dyn Fn(i32) -> Option<i32>,
    start_of: &dyn Fn(i32) -> Option<i64>,
) -> bool {
    if group_of(note.pid) != Some(record.pgid) || record.data_home != data_home {
        return false;
    }
    match start_of(record.pgid) {
        Some(start) => record.leader_start == Some(start),
        None => {
            note.started_at >= record.launched_at_ms
                && note.started_at - record.launched_at_ms <= NOTE_WITHIN_MS
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record() -> Record {
        Record {
            pgid: 500,
            leader_start: Some(1_800_000_000),
            launched_at_ms: 1_800_000_000_000,
            data_home: PathBuf::from("/Users/u/.trillion"),
            port: 7778,
        }
    }

    fn note(pid: i32, started_at: i64) -> Note {
        Note {
            pid,
            port: 7778,
            started_at,
        }
    }

    const HOME: &str = "/Users/u/.trillion";

    #[test]
    fn the_same_live_leader_is_ours() {
        let group = |pid| (pid == 501 || pid == 500).then_some(500);
        let start = |pid| (pid == 500).then_some(1_800_000_000);
        let n = note(501, 1_800_000_004_000);
        assert!(ours(&record(), &n, Path::new(HOME), &group, &start));
    }

    #[test]
    fn a_leader_with_another_start_is_not() {
        let group = |_| Some(500);
        let start = |_| Some(1_800_000_999);
        let n = note(501, 1_800_000_004_000);
        assert!(!ours(&record(), &n, Path::new(HOME), &group, &start));
    }

    #[test]
    fn another_group_or_folder_is_not() {
        let start = |_| Some(1_800_000_000);
        let n = note(501, 1_800_000_004_000);
        assert!(!ours(&record(), &n, Path::new(HOME), &|_| Some(9), &start));
        assert!(!ours(&record(), &n, Path::new(HOME), &|_| None, &start));
        assert!(!ours(
            &record(),
            &n,
            Path::new("/elsewhere"),
            &|_| Some(500),
            &start
        ));
    }

    #[test]
    fn the_leader_gone_needs_a_note_soon_after_the_launch() {
        let group = |_| Some(500);
        let gone = |_| None;
        let r = record();
        let soon = note(501, r.launched_at_ms + 8_000);
        let late = note(501, r.launched_at_ms + 61_000);
        let before = note(501, r.launched_at_ms - 1);
        assert!(ours(&r, &soon, Path::new(HOME), &group, &gone));
        assert!(!ours(&r, &late, Path::new(HOME), &group, &gone));
        assert!(!ours(&r, &before, Path::new(HOME), &group, &gone));
    }

    #[test]
    fn writes_owner_only_and_reads_back() {
        let dir =
            std::env::temp_dir().join(format!("trillion-desktop-record-{}", std::process::id()));
        let path = dir.join("child.json");
        write(&path, &record()).unwrap();
        assert_eq!(read(&path), Some(record()));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o600);
        }
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(!text.to_lowercase().contains("token"));
        remove(&path);
        assert_eq!(read(&path), None);
    }

    #[cfg(unix)]
    #[test]
    fn a_real_process_group_is_recognised() {
        use crate::running::{group_of, start_time};
        use std::os::unix::process::CommandExt;
        let mut child = std::process::Command::new("/bin/sh")
            .args(["-c", "sleep 30 & wait"])
            .process_group(0)
            .spawn()
            .unwrap();
        let pgid = child.id() as i32;
        std::thread::sleep(std::time::Duration::from_millis(100));
        let r = Record {
            pgid,
            leader_start: start_time(pgid),
            launched_at_ms: 0,
            data_home: PathBuf::from(HOME),
            port: 7778,
        };
        let n = note(pgid, 0);
        assert!(ours(&r, &n, Path::new(HOME), &group_of, &start_time));
        let other = Record {
            leader_start: r.leader_start.map(|s| s - 100),
            ..r.clone()
        };
        assert!(!ours(&other, &n, Path::new(HOME), &group_of, &start_time));
        // SAFETY: ends the group this test started.
        unsafe { libc::kill(-pgid, libc::SIGKILL) };
        let _ = child.wait();
    }
}
