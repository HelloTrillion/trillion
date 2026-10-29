//! The server the app starts: `trillion run --no-open --port N` with the app's token,
//! the last lines of its output, whether it answers, and stopping it.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

/// How many lines of the server's output the splash can show.
pub const KEEP: usize = 40;

/// How the server is handed the app's token (SPN-11a).
pub const TOKEN_ENV: &str = "TRILLION_SESSION_TOKEN";

/// The last lines a process wrote.
#[derive(Debug, Default)]
pub struct Ring {
    lines: VecDeque<String>,
}

impl Ring {
    pub fn push(&mut self, line: String) {
        if self.lines.len() == KEEP {
            self.lines.pop_front();
        }
        self.lines.push_back(line);
    }

    pub fn lines(&self) -> Vec<String> {
        self.lines.iter().cloned().collect()
    }
}

/// How long a server has to stop on SIGTERM before it is killed.
pub const GRACE: Duration = Duration::from_secs(5);

/// A `trillion run` this app started.
pub struct Server {
    pub port: u16,
    child: Mutex<Child>,
    lines: Arc<Mutex<Ring>>,
}

impl Server {
    /// Runs `bin run --no-open --port N` in its own process group (so stopping it
    /// reaches the Python under `uv`), with exactly `env` plus the token.
    pub fn start(
        bin: &Path,
        port: u16,
        token: &str,
        env: Vec<(String, String)>,
    ) -> std::io::Result<Server> {
        let mut child = Command::new(bin)
            .args(["run", "--no-open", "--port", &port.to_string()])
            .env_clear()
            .envs(env)
            .env(TOKEN_ENV, token)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .spawn()?;
        let lines = Arc::new(Mutex::new(Ring::default()));
        if let Some(out) = child.stdout.take() {
            keep_lines(out, lines.clone());
        }
        if let Some(err) = child.stderr.take() {
            keep_lines(err, lines.clone());
        }
        Ok(Server {
            port,
            child: Mutex::new(child),
            lines,
        })
    }

    pub fn exited(&self) -> bool {
        matches!(lock(&self.child).try_wait(), Ok(Some(_)) | Err(_))
    }

    pub fn lines(&self) -> Vec<String> {
        lock(&self.lines).lines()
    }

    pub fn stop(&self) -> Stopped {
        stop(&mut lock(&self.child), GRACE)
    }
}

/// A poisoned lock still holds usable data here: a ring of lines, a child handle.
fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn keep_lines(stream: impl Read + Send + 'static, ring: Arc<Mutex<Ring>>) {
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stream);
        let mut buf = Vec::new();
        while matches!(reader.read_until(b'\n', &mut buf), Ok(n) if n > 0) {
            let line = String::from_utf8_lossy(&buf).trim_end().to_string();
            lock(&ring).push(line);
            buf.clear();
        }
    });
}

/// How a stop went.
#[derive(Debug, PartialEq, Eq)]
pub enum Stopped {
    /// It had already exited.
    Already,
    /// It stopped on SIGTERM.
    Term,
    /// It was still running after the grace period.
    Kill,
}

/// SIGTERM to the child's process group (`uv` and the Python under it), then SIGKILL
/// once `grace` has passed. The child must have been started in its own group.
pub fn stop(child: &mut Child, grace: Duration) -> Stopped {
    if matches!(child.try_wait(), Ok(Some(_))) {
        return Stopped::Already;
    }
    let group = -(child.id() as i32);
    // SAFETY: signals the group this app started; no memory is touched.
    unsafe { libc::kill(group, libc::SIGTERM) };
    let deadline = Instant::now() + grace;
    while Instant::now() < deadline {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return Stopped::Term;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: as above.
    unsafe { libc::kill(group, libc::SIGKILL) };
    let _ = child.kill();
    let _ = child.wait();
    Stopped::Kill
}

/// Whether the server answers `GET /` on loopback.
pub fn answers(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let ask = || -> std::io::Result<bool> {
        let mut conn = TcpStream::connect_timeout(&addr, Duration::from_millis(500))?;
        conn.set_read_timeout(Some(Duration::from_secs(2)))?;
        conn.set_write_timeout(Some(Duration::from_secs(2)))?;
        // The server only answers its own Host.
        let request =
            format!("GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
        conn.write_all(request.as_bytes())?;
        let mut head = [0u8; 5];
        conn.read_exact(&mut head)?;
        Ok(&head == b"HTTP/")
    };
    ask().unwrap_or(false)
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

/// The child's environment: the login shell's (an app opened from the Finder lacks
/// the shell's PATH), with the app's own `TRILLION_*` settings on top, so a data
/// folder or binary chosen for the app reaches its server.
pub fn child_env(
    login: Vec<(String, String)>,
    app: impl IntoIterator<Item = (String, String)>,
) -> Vec<(String, String)> {
    let app: Vec<(String, String)> = app.into_iter().collect();
    let base = if login.is_empty() { app.clone() } else { login };
    let mut env: Vec<(String, String)> = base
        .into_iter()
        .filter(|(k, _)| !k.starts_with("TRILLION_"))
        .collect();
    env.extend(app.into_iter().filter(|(k, _)| k.starts_with("TRILLION_")));
    // The app sets its own token on the child; one from a shell is never passed on.
    env.retain(|(k, _)| k != TOKEN_ENV);
    env
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
    fn the_login_env_with_the_apps_trillion_settings() {
        let login = vec![
            ("PATH".to_string(), "/opt/homebrew/bin:/usr/bin".to_string()),
            ("TRILLION_HOME".to_string(), "/login/home".to_string()),
        ];
        let app = vec![
            ("PATH".to_string(), "/usr/bin".to_string()),
            ("TRILLION_HOME".to_string(), "/app/home".to_string()),
            ("TRILLION_BIN".to_string(), "/x".to_string()),
            ("OTHER".to_string(), "app".to_string()),
        ];
        let mut env = child_env(login, app);
        env.sort();
        assert_eq!(
            env,
            vec![
                ("PATH".to_string(), "/opt/homebrew/bin:/usr/bin".to_string()),
                ("TRILLION_BIN".to_string(), "/x".to_string()),
                ("TRILLION_HOME".to_string(), "/app/home".to_string()),
            ]
        );
    }

    #[test]
    fn no_login_env_keeps_the_apps() {
        let app = vec![("PATH".to_string(), "/usr/bin".to_string())];
        assert_eq!(child_env(vec![], app.clone()), app);
    }

    #[test]
    fn a_session_token_from_outside_is_not_passed_on() {
        let login = vec![("TRILLION_SESSION_TOKEN".to_string(), "x".repeat(43))];
        let app = vec![("TRILLION_SESSION_TOKEN".to_string(), "y".repeat(43))];
        assert!(child_env(login, app).is_empty());
    }

    fn sh(script: &str) -> std::process::Child {
        use std::os::unix::process::CommandExt;
        std::process::Command::new("/bin/sh")
            .args(["-c", script])
            .process_group(0)
            .spawn()
            .unwrap()
    }

    #[test]
    fn stop_ends_a_process_that_heeds_sigterm() {
        let mut child = sh("sleep 30");
        let t = std::time::Instant::now();
        let how = stop(&mut child, Duration::from_secs(5));
        assert_eq!(how, Stopped::Term);
        assert!(t.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn stop_kills_a_process_that_ignores_sigterm() {
        let mut child = sh("trap '' TERM; while :; do sleep 0.05; done");
        std::thread::sleep(Duration::from_millis(100)); // the trap is set
        let t = std::time::Instant::now();
        let how = stop(&mut child, Duration::from_millis(300));
        assert_eq!(how, Stopped::Kill);
        assert!(t.elapsed() >= Duration::from_millis(300));
        assert!(child.try_wait().unwrap().is_some());
    }

    #[test]
    fn stop_on_an_exited_process_is_already() {
        let mut child = sh("exit 0");
        std::thread::sleep(Duration::from_millis(200));
        assert_eq!(stop(&mut child, Duration::from_secs(5)), Stopped::Already);
    }

    #[test]
    fn start_runs_the_server_with_the_token_and_keeps_its_output() {
        use std::os::unix::fs::PermissionsExt;
        let dir =
            std::env::temp_dir().join(format!("trillion-desktop-start-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let bin = dir.join("trillion");
        std::fs::write(
            &bin,
            "#!/bin/sh\necho \"args: $*\"\necho \"token: ${#TRILLION_SESSION_TOKEN}\"\necho \"home: $TRILLION_HOME\" >&2\n",
        )
        .unwrap();
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
        let env = vec![("TRILLION_HOME".to_string(), "/scratch".to_string())];
        let server = Server::start(&bin, 7781, &"t".repeat(43), env).unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !(server.exited() && server.lines().len() == 3) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        let mut lines = server.lines();
        lines.sort();
        assert_eq!(
            lines,
            vec![
                "args: run --no-open --port 7781",
                "home: /scratch",
                "token: 43"
            ]
        );
        assert_eq!(server.stop(), Stopped::Already);
    }

    #[test]
    fn a_closed_port_does_not_answer() {
        let held = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = held.local_addr().unwrap().port();
        drop(held);
        assert!(!answers(port));
    }

    #[test]
    fn an_http_server_answers() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let serve = std::thread::spawn(move || {
            let (mut conn, _) = listener.accept().unwrap();
            let mut buf = [0u8; 512];
            let n = conn.read(&mut buf).unwrap();
            assert!(buf[..n].starts_with(b"GET / HTTP/1.1\r\n"));
            conn.write_all(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n")
                .unwrap();
        });
        assert!(answers(port));
        serve.join().unwrap();
    }

    #[test]
    fn keeps_the_last_forty_lines() {
        let mut ring = Ring::default();
        for n in 0..100 {
            ring.push(format!("line {n}"));
        }
        let lines = ring.lines();
        assert_eq!(lines.len(), KEEP);
        assert_eq!(lines[0], "line 60");
        assert_eq!(lines[KEEP - 1], "line 99");
    }

    #[test]
    fn fewer_lines_are_all_kept() {
        let mut ring = Ring::default();
        ring.push("one".into());
        ring.push("two".into());
        assert_eq!(ring.lines(), vec!["one", "two"]);
    }
}
