//! Asking the user's login shell. An app opened from the Finder starts with a bare
//! PATH; the login shell knows where the user's tools are and what they have set.
//! Windows has no login shell: an app started from the Start menu already has the
//! user's environment, so there is nothing to ask.
#![cfg_attr(not(unix), allow(dead_code))]

#[cfg(unix)]
use std::io::Read;
#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(unix)]
use std::process::{Command, Stdio};
#[cfg(unix)]
use std::sync::mpsc;
#[cfg(unix)]
use std::time::{Duration, Instant};

/// A profile that waits on something must not hang the app's start.
#[cfg(unix)]
const LIMIT: Duration = Duration::from_secs(5);

const ENV_MARK: &[u8] = b"\0TRILLION-ENV\0";
const BIN_MARK: &[u8] = b"\0TRILLION-BIN\0";
/// One shell for both questions. The markers let a profile print what it likes first.
const PROBE: &str =
    r"printf '\0TRILLION-ENV\0'; env -0; printf '\0TRILLION-BIN\0'; command -v trillion";

/// What the login shell said.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Probe {
    /// Its environment.
    pub env: Vec<(String, String)>,
    /// Its answer to `command -v trillion`, if any.
    pub bin: Option<String>,
}

/// Asks the login shell; None if it failed, said nothing usable, or took too long.
#[cfg(unix)]
pub fn probe() -> Option<Probe> {
    login(PROBE).and_then(|out| parse_probe(&out))
}

/// Windows: no login shell to ask.
#[cfg(not(unix))]
pub fn probe() -> Option<Probe> {
    None
}

/// Reads the probe's output: only what follows the markers counts.
pub fn parse_probe(out: &[u8]) -> Option<Probe> {
    let start = find(out, ENV_MARK)? + ENV_MARK.len();
    let rest = &out[start..];
    let (env, bin) = match find(rest, BIN_MARK) {
        Some(at) => (&rest[..at], Some(&rest[at + BIN_MARK.len()..])),
        None => (rest, None),
    };
    let bin = bin
        .map(|b| String::from_utf8_lossy(b).trim().to_string())
        .filter(|b| !b.is_empty());
    Some(Probe {
        env: parse_env0(env),
        bin,
    })
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

/// `env -0` output: NUL-separated `NAME=value` pairs.
pub fn parse_env0(raw: &[u8]) -> Vec<(String, String)> {
    raw.split(|b| *b == 0)
        .filter_map(|pair| {
            let pair = String::from_utf8_lossy(pair);
            let (name, value) = pair.split_once('=')?;
            (!name.is_empty()).then(|| (name.to_string(), value.to_string()))
        })
        .collect()
}

/// What `$SHELL -lc script` printed, whatever its exit status; None if it could not
/// run or did not finish in time.
#[cfg(unix)]
fn login(script: &str) -> Option<Vec<u8>> {
    let shell = std::env::var("SHELL")
        .ok()
        .filter(|s| s.starts_with('/'))
        .unwrap_or_else(|| "/bin/zsh".into());
    let mut child = Command::new(shell)
        .args(["-lc", script])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
        .ok()?;
    let mut out = child.stdout.take()?;
    // Read on a thread: a daemon a profile starts can hold stdout open long after the
    // shell has gone, and a read on this thread would wait for it forever.
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut buf = Vec::new();
        let _ = out.read_to_end(&mut buf);
        let _ = tx.send(buf);
    });
    let deadline = Instant::now() + LIMIT;
    let got = rx.recv_timeout(deadline.saturating_duration_since(Instant::now()));
    // Whatever happened, nothing of this shell is left running.
    if !matches!(child.try_wait(), Ok(Some(_))) {
        // SAFETY: ends the group this function started.
        unsafe { libc::kill(-(child.id() as i32), libc::SIGKILL) };
    }
    let _ = child.wait();
    got.ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_env0() {
        let raw = b"PATH=/a:/b\0HOME=/Users/u\0EQ=x=y\0MULTI=one\ntwo\0junk\0";
        assert_eq!(
            parse_env0(raw),
            vec![
                ("PATH".into(), "/a:/b".into()),
                ("HOME".into(), "/Users/u".into()),
                ("EQ".into(), "x=y".into()),
                ("MULTI".into(), "one\ntwo".into()),
            ]
        );
        assert!(parse_env0(b"").is_empty());
    }

    #[test]
    fn reads_only_what_follows_the_markers() {
        let out = b"Welcome! FOO=noise\0\0TRILLION-ENV\0PATH=/opt/homebrew/bin\0HOME=/Users/u\0\0TRILLION-BIN\0/Users/u/.local/bin/trillion\n";
        assert_eq!(
            parse_probe(out),
            Some(Probe {
                env: vec![
                    ("PATH".into(), "/opt/homebrew/bin".into()),
                    ("HOME".into(), "/Users/u".into()),
                ],
                bin: Some("/Users/u/.local/bin/trillion".into()),
            })
        );
    }

    #[test]
    fn no_trillion_on_the_path_is_no_answer() {
        let out = b"\0TRILLION-ENV\0PATH=/usr/bin\0\0TRILLION-BIN\0";
        let probe = parse_probe(out).unwrap();
        assert_eq!(probe.bin, None);
        assert_eq!(probe.env, vec![("PATH".into(), "/usr/bin".into())]);
    }

    #[test]
    fn no_marker_is_nothing() {
        assert_eq!(parse_probe(b"PATH=/usr/bin\0"), None);
        assert_eq!(parse_probe(b""), None);
    }

    #[cfg(unix)]
    #[test]
    fn the_real_shell_answers() {
        let probe = probe().expect("the login shell");
        assert!(probe.env.iter().any(|(k, _)| k == "PATH"));
    }
}
