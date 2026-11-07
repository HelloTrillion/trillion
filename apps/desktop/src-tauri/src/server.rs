//! The server the app starts: `trillion run --no-open --port N` with the app's token,
//! the last lines of its output, whether it answers and is ours, and stopping it.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use hmac::{Hmac, Mac};
use sha2::Sha256;

/// How many lines of the server's output the splash can show.
pub const KEEP: usize = 40;

/// How the server is handed the app's token (SPN-11a).
pub const TOKEN_ENV: &str = "TRILLION_SESSION_TOKEN";

/// How long a server has to stop on SIGTERM before it is killed.
pub const GRACE: Duration = Duration::from_secs(5);

/// Windows: no console window for the server or anything it starts (OPS-3), and
/// held until it is in its Job Object (`winproc`).
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
#[cfg(windows)]
const CREATE_SUSPENDED: u32 = 0x0000_0004;

/// PATH's separator, and the PATH a bare environment gets.
#[cfg(windows)]
const PATH_SEP: char = ';';
#[cfg(not(windows))]
const PATH_SEP: char = ':';
#[cfg(windows)]
const BARE_PATH: &str = "";
#[cfg(not(windows))]
const BARE_PATH: &str = "/usr/bin:/bin:/usr/sbin:/sbin";

/// The server's answer to the app's challenge (`trillion.api.app.DESKTOP_HELLO`).
const HELLO: &str = "/api/desktop/hello";

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

/// A `trillion run` this app started.
pub struct Server {
    /// Its process group, which is its leader's pid. On Windows, its pid.
    pub pgid: i32,
    child: Mutex<Child>,
    /// The other end of the server's input. Never written: when this app goes, even
    /// by a force quit, it closes and the server stops itself.
    lifeline: Mutex<Option<ChildStdin>>,
    lines: Arc<Mutex<Ring>>,
    /// Windows: the job it runs in, which ends it with the app.
    #[cfg(windows)]
    job: crate::winproc::Job,
}

impl Server {
    /// Runs `bin run --no-open --port N` in its own process group (so stopping it
    /// reaches the Python under `uv`), in `home`, with exactly `env` plus the token.
    pub fn start(
        bin: &Path,
        port: u16,
        token: &str,
        env: Vec<(String, String)>,
        home: Option<&Path>,
    ) -> std::io::Result<Server> {
        let mut command = Command::new(bin);
        command
            .args(["run", "--no-open", "--port", &port.to_string()])
            .env_clear()
            .envs(env)
            .env(TOKEN_ENV, token)
            // Its lines reach the splash as they are written, not when a buffer fills.
            .env("PYTHONUNBUFFERED", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        command.process_group(0);
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
        // Not the app bundle's folder, which is wherever the app was launched from.
        if let Some(home) = home.filter(|h| h.is_dir()) {
            command.current_dir(home);
        }
        let mut child = command.spawn()?;
        #[cfg(windows)]
        let job = match crate::winproc::Job::adopt(&child) {
            Ok(job) => job,
            Err(e) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(e);
            }
        };
        let lines = Arc::new(Mutex::new(Ring::default()));
        if let Some(out) = child.stdout.take() {
            keep_lines(out, lines.clone());
        }
        if let Some(err) = child.stderr.take() {
            keep_lines(err, lines.clone());
        }
        Ok(Server {
            pgid: child.id() as i32,
            lifeline: Mutex::new(child.stdin.take()),
            child: Mutex::new(child),
            lines,
            #[cfg(windows)]
            job,
        })
    }

    pub fn exited(&self) -> bool {
        matches!(lock(&self.child).try_wait(), Ok(Some(_)) | Err(_))
    }

    pub fn lines(&self) -> Vec<String> {
        lock(&self.lines).lines()
    }

    #[cfg(unix)]
    pub fn stop(&self) -> Stopped {
        let mut child = lock(&self.child);
        let how = stop_group(self.pgid, GRACE, &mut || {
            matches!(child.try_wait(), Ok(Some(_)) | Err(_))
        });
        lock(&self.lifeline).take();
        how
    }

    /// Windows has no SIGTERM: closing the server's input asks it to stop, as Ctrl+C
    /// does. Whatever is left in its job after the grace period is ended.
    #[cfg(windows)]
    pub fn stop(&self) -> Stopped {
        let mut child = lock(&self.child);
        let mut exited = || matches!(child.try_wait(), Ok(Some(_)) | Err(_));
        if exited() {
            self.job.terminate();
            lock(&self.lifeline).take();
            return Stopped::Already;
        }
        lock(&self.lifeline).take();
        let deadline = Instant::now() + GRACE;
        while Instant::now() < deadline {
            if exited() {
                // Anything it started and left behind goes too.
                self.job.terminate();
                return Stopped::Term;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        self.job.terminate();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !exited() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        Stopped::Kill
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
    /// Nothing of it was left.
    Already,
    /// It stopped on SIGTERM.
    Term,
    /// Something of it was still running after the grace period, and was killed.
    Kill,
}

/// Whether no process is left in this group.
#[cfg(unix)]
pub fn group_gone(pgid: i32) -> bool {
    // SAFETY: signal 0 only asks.
    let found = unsafe { libc::kill(-pgid, 0) } == 0;
    !found && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH)
}

/// Windows: whether the process is gone. A server's job ends it with the app that
/// started it, so one is left behind only if the app could not make the job.
#[cfg(windows)]
pub fn group_gone(pid: i32) -> bool {
    !crate::winproc::alive(pid)
}

/// Windows: ends the process; there is no signal to ask first.
#[cfg(windows)]
pub fn stop_group(pid: i32, _grace: Duration, _reaped: &mut dyn FnMut() -> bool) -> Stopped {
    if pid <= 0 || group_gone(pid) {
        return Stopped::Already;
    }
    crate::winproc::terminate(pid);
    Stopped::Kill
}

/// SIGTERM to the whole process group (`uv` and the Python under it, and anything they
/// started), then SIGKILL once `grace` has passed. Done when the leader is reaped
/// (`reaped`, which a parent must do itself) and no process is left in the group.
#[cfg(unix)]
pub fn stop_group(pgid: i32, grace: Duration, reaped: &mut dyn FnMut() -> bool) -> Stopped {
    let done = |reaped: &mut dyn FnMut() -> bool| reaped() && group_gone(pgid);
    if pgid <= 1 {
        return Stopped::Already;
    }
    if done(reaped) {
        return Stopped::Already;
    }
    // SAFETY: signals the group this app started; a group already gone is ESRCH.
    unsafe { libc::kill(-pgid, libc::SIGTERM) };
    let deadline = Instant::now() + grace;
    while Instant::now() < deadline {
        if done(reaped) {
            return Stopped::Term;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: as above.
    unsafe { libc::kill(-pgid, libc::SIGKILL) };
    let deadline = Instant::now() + Duration::from_secs(5);
    while !done(reaped) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(20));
    }
    Stopped::Kill
}

/// One loopback GET: the status and the body, or None if nothing answered.
fn get(port: u16, path: &str) -> Option<(u16, String)> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut conn = TcpStream::connect_timeout(&addr, Duration::from_millis(500)).ok()?;
    conn.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    conn.set_write_timeout(Some(Duration::from_secs(2))).ok()?;
    // The server only answers its own Host.
    let request =
        format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    conn.write_all(request.as_bytes()).ok()?;
    let mut raw = Vec::new();
    // A slow or endless answer still ends at the read timeout; what came is used.
    let _ = conn.take(64 * 1024).read_to_end(&mut raw);
    let text = String::from_utf8_lossy(&raw);
    let (head, body) = text.split_once("\r\n\r\n").unwrap_or((&text, ""));
    let status = head.strip_prefix("HTTP/1.")?.get(2..5)?.parse().ok()?;
    Some((status, body.to_string()))
}

/// Whether something answers HTTP on this loopback port.
pub fn answers(port: u16) -> bool {
    get(port, "/").is_some()
}

/// The server's answer to the challenge `nonce`, if it gave one.
pub fn hello(port: u16, nonce: &str) -> Option<String> {
    let (status, body) = get(port, &format!("{HELLO}?n={nonce}"))?;
    if status != 200 {
        return None;
    }
    let reply: serde_json::Value = serde_json::from_str(&body).ok()?;
    reply.get("mac")?.as_str().map(str::to_string)
}

/// Whether `mac` is HMAC-SHA256(token, nonce) in hex: only a server holding the token
/// can make it. Compared in constant time.
pub fn verify(token: &str, nonce: &str, mac: &str) -> bool {
    let Some(given) = unhex(mac) else {
        return false;
    };
    let Ok(mut want) = Hmac::<Sha256>::new_from_slice(token.as_bytes()) else {
        return false;
    };
    want.update(nonce.as_bytes());
    want.verify_slice(&given).is_ok()
}

fn unhex(text: &str) -> Option<Vec<u8>> {
    if !text.len().is_multiple_of(2) {
        return None;
    }
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(text.get(i..i + 2)?, 16).ok())
        .collect()
}

/// The child's environment: the login shell's (an app opened from the Finder lacks
/// the shell's PATH), with the app's own `TRILLION_*` settings over it, name by name,
/// so a data folder or binary chosen for the app reaches its server. A token from
/// either is dropped: the app sets its own.
pub fn child_env(
    login: Vec<(String, String)>,
    app: impl IntoIterator<Item = (String, String)>,
) -> Vec<(String, String)> {
    let app: Vec<(String, String)> = app.into_iter().collect();
    let mut env = if login.is_empty() { app.clone() } else { login };
    for (name, value) in app.into_iter().filter(|(k, _)| k.starts_with("TRILLION_")) {
        match env.iter_mut().find(|(k, _)| same_name(k, &name)) {
            Some(slot) => slot.1 = value,
            None => env.push((name, value)),
        }
    }
    env.retain(|(k, _)| k != TOKEN_ENV);
    env
}

/// Whether two variable names are the same one: on Windows `Path` is `PATH`.
fn same_name(a: &str, b: &str) -> bool {
    if cfg!(windows) {
        a.eq_ignore_ascii_case(b)
    } else {
        a == b
    }
}

/// The value of `name` in an environment.
pub fn var<'a>(env: &'a [(String, String)], name: &str) -> Option<&'a str> {
    env.iter()
        .find(|(k, _)| same_name(k, name))
        .map(|(_, v)| v.as_str())
}

/// Puts these folders at the front of PATH when it lacks them: `trillion`'s own (so
/// what it runs, such as `uv`, is found beside it) and `~/.local/bin`, where uv puts
/// tools.
pub fn with_path(mut env: Vec<(String, String)>, first: &[&Path]) -> Vec<(String, String)> {
    let current = var(&env, "PATH").unwrap_or(BARE_PATH);
    let mut parts: Vec<String> = match current {
        "" => vec![],
        _ => current.split(PATH_SEP).map(str::to_string).collect(),
    };
    for dir in first.iter().rev() {
        let dir = dir.to_string_lossy().to_string();
        if !dir.is_empty() && !parts.contains(&dir) {
            parts.insert(0, dir);
        }
    }
    let path = parts.join(&PATH_SEP.to_string());
    match env.iter_mut().find(|(k, _)| same_name(k, "PATH")) {
        Some(slot) => slot.1 = path,
        None => env.push(("PATH".into(), path)),
    }
    env
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pairs(list: &[(&str, &str)]) -> Vec<(String, String)> {
        list.iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    #[test]
    fn the_login_env_with_the_apps_trillion_settings_over_it() {
        let login = pairs(&[
            ("PATH", "/opt/homebrew/bin:/usr/bin"),
            ("TRILLION_HOME", "/login/home"),
            ("TRILLION_MODEL", "login-model"),
        ]);
        let app = pairs(&[
            ("PATH", "/usr/bin"),
            ("TRILLION_HOME", "/app/home"),
            ("TRILLION_BIN", "/x"),
            ("OTHER", "app"),
        ]);
        let mut env = child_env(login, app);
        env.sort();
        assert_eq!(
            env,
            pairs(&[
                ("PATH", "/opt/homebrew/bin:/usr/bin"),
                ("TRILLION_BIN", "/x"),
                ("TRILLION_HOME", "/app/home"),
                ("TRILLION_MODEL", "login-model"),
            ])
        );
    }

    #[test]
    fn a_login_setting_the_app_lacks_is_kept() {
        let login = pairs(&[("TRILLION_HOME", "/login/home")]);
        let env = child_env(login, pairs(&[("PATH", "/usr/bin")]));
        assert_eq!(var(&env, "TRILLION_HOME"), Some("/login/home"));
    }

    #[test]
    fn no_login_env_keeps_the_apps() {
        let app = pairs(&[("PATH", "/usr/bin")]);
        assert_eq!(child_env(vec![], app.clone()), app);
    }

    #[test]
    fn a_session_token_from_outside_is_not_passed_on() {
        let login = pairs(&[(TOKEN_ENV, &"x".repeat(43))]);
        let app = pairs(&[(TOKEN_ENV, &"y".repeat(43))]);
        assert!(child_env(login, app).is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn path_gains_the_folders_it_lacks_in_front() {
        let env = pairs(&[("PATH", "/usr/bin:/Users/u/.local/bin")]);
        let env = with_path(
            env,
            &[Path::new("/opt/tools"), Path::new("/Users/u/.local/bin")],
        );
        assert_eq!(
            var(&env, "PATH"),
            Some("/opt/tools:/usr/bin:/Users/u/.local/bin")
        );
        let env = with_path(vec![], &[Path::new("/a"), Path::new("/b")]);
        assert_eq!(
            var(&env, "PATH"),
            Some("/a:/b:/usr/bin:/bin:/usr/sbin:/sbin")
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_path_is_found_whatever_its_case_and_split_by_semicolons() {
        let env = pairs(&[("Path", r"C:\Windows;C:\Users\u\.local\bin")]);
        assert_eq!(var(&env, "PATH"), Some(r"C:\Windows;C:\Users\u\.local\bin"));
        let env = with_path(
            env,
            &[Path::new(r"C:\tools"), Path::new(r"C:\Users\u\.local\bin")],
        );
        assert_eq!(env.len(), 1, "one Path, not a second PATH beside it");
        assert_eq!(
            var(&env, "Path"),
            Some(r"C:\tools;C:\Windows;C:\Users\u\.local\bin")
        );
        let env = with_path(vec![], &[Path::new(r"C:\a")]);
        assert_eq!(var(&env, "PATH"), Some(r"C:\a"));
    }

    #[cfg(windows)]
    #[test]
    fn a_windows_server_hears_its_input_close_and_is_ended_with_its_job() {
        // A batch file waits on its input, as `trillion run` does for the app.
        let dir = std::env::temp_dir().join(format!("trillion-desktop-win-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let bin = dir.join("waits.cmd");
        std::fs::write(&bin, "@echo waiting\r\n@more >nul\r\n@echo closed\r\n").unwrap();
        let env: Vec<(String, String)> = std::env::vars().collect();
        let server = Server::start(&bin, 7783, &"t".repeat(43), env, None).unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        while server.lines().is_empty() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(server.lines().iter().any(|l| l.contains("waiting")));
        assert!(!server.exited());
        assert_ne!(server.stop(), Stopped::Already);
        assert!(server.exited());
        assert!(group_gone(server.pgid));
    }

    #[test]
    fn verifies_the_servers_mac() {
        let token = "A".repeat(20) + "b_-" + &"9".repeat(20);
        let nonce = "n0nce-of_sixteen-plus";
        // As the Python test computes it: hmac.new(token, nonce, sha256).hexdigest().
        let mut mac = Hmac::<Sha256>::new_from_slice(token.as_bytes()).unwrap();
        mac.update(nonce.as_bytes());
        let hex: String = mac
            .finalize()
            .into_bytes()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert!(verify(&token, nonce, &hex));
        assert!(verify(&token, nonce, &hex.to_uppercase()));
        assert!(!verify(&token, "another-nonce-here", &hex));
        assert!(!verify(&("B".repeat(43)), nonce, &hex));
        assert!(!verify(&token, nonce, &hex[..62]));
        assert!(!verify(&token, nonce, "zz"));
        assert!(!verify(&token, nonce, ""));
    }

    #[test]
    fn a_known_mac() {
        // RFC 4231 test case 2.
        let mac = "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843";
        assert!(verify("Jefe", "what do ya want for nothing?", mac));
    }

    #[cfg(unix)]
    fn sh(script: &str) -> Child {
        Command::new("/bin/sh")
            .args(["-c", script])
            .process_group(0)
            .spawn()
            .unwrap()
    }

    #[cfg(unix)]
    fn stop(child: &mut Child, grace: Duration) -> Stopped {
        let pgid = child.id() as i32;
        stop_group(pgid, grace, &mut || matches!(child.try_wait(), Ok(Some(_))))
    }

    #[cfg(unix)]
    #[test]
    fn stop_ends_a_process_that_heeds_sigterm() {
        let mut child = sh("sleep 30");
        let t = Instant::now();
        assert_eq!(stop(&mut child, Duration::from_secs(5)), Stopped::Term);
        assert!(t.elapsed() < Duration::from_secs(2));
    }

    #[cfg(unix)]
    #[test]
    fn stop_kills_a_process_that_ignores_sigterm() {
        let mut child = sh("trap '' TERM; while :; do sleep 0.05; done");
        std::thread::sleep(Duration::from_millis(100)); // the trap is set
        let pgid = child.id() as i32;
        let t = Instant::now();
        assert_eq!(stop(&mut child, Duration::from_millis(300)), Stopped::Kill);
        assert!(t.elapsed() >= Duration::from_millis(300));
        assert!(child.try_wait().unwrap().is_some());
        assert!(group_gone(pgid));
    }

    #[cfg(unix)]
    #[test]
    fn stop_reaches_a_child_that_outlives_its_parent() {
        // The leader stops on SIGTERM; what it started ignores it and would be left.
        let mut child = sh(r#"sh -c "trap '' TERM; sleep 30" & wait"#);
        std::thread::sleep(Duration::from_millis(200));
        let pgid = child.id() as i32;
        assert!(!group_gone(pgid));
        assert_eq!(stop(&mut child, Duration::from_millis(400)), Stopped::Kill);
        assert!(group_gone(pgid));
    }

    #[cfg(unix)]
    #[test]
    fn stop_on_an_exited_process_is_already() {
        let mut child = sh("exit 0");
        std::thread::sleep(Duration::from_millis(200));
        assert_eq!(stop(&mut child, Duration::from_secs(5)), Stopped::Already);
    }

    #[cfg(unix)]
    fn fake_bin(name: &str, script: &str) -> std::path::PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let dir =
            std::env::temp_dir().join(format!("trillion-desktop-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let bin = dir.join("trillion");
        std::fs::write(&bin, script).unwrap();
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
        bin
    }

    #[cfg(unix)]
    fn wait_for(server: &Server, lines: usize) -> Vec<String> {
        let deadline = Instant::now() + Duration::from_secs(5);
        while server.lines().len() < lines && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        let mut got = server.lines();
        got.sort();
        got
    }

    #[cfg(unix)]
    #[test]
    fn start_runs_the_server_with_the_token_and_keeps_its_output() {
        let bin = fake_bin(
            "start",
            "#!/bin/sh\necho \"args: $*\"\necho \"token: ${#TRILLION_SESSION_TOKEN}\"\necho \"home: $TRILLION_HOME\" >&2\necho \"pwd: $(pwd -P)\"\necho \"unbuffered: $PYTHONUNBUFFERED\"\n",
        );
        let env = pairs(&[("TRILLION_HOME", "/scratch")]);
        let home = std::env::temp_dir().canonicalize().unwrap();
        let server = Server::start(&bin, 7781, &"t".repeat(43), env, Some(&home)).unwrap();
        assert_eq!(
            wait_for(&server, 5),
            vec![
                "args: run --no-open --port 7781".to_string(),
                "home: /scratch".to_string(),
                format!("pwd: {}", home.display()),
                "token: 43".to_string(),
                "unbuffered: 1".to_string(),
            ]
        );
        let deadline = Instant::now() + Duration::from_secs(5);
        while !server.exited() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(server.stop(), Stopped::Already);
    }

    #[cfg(unix)]
    #[test]
    fn the_server_sees_its_input_close_when_stopped() {
        // Waits for its input to end, as `trillion run` does when the app made its token.
        let bin = fake_bin(
            "lifeline",
            "#!/bin/sh\ntrap '' TERM\necho waiting\ncat >/dev/null\necho closed\n",
        );
        let server = Server::start(&bin, 7782, &"t".repeat(43), vec![], None).unwrap();
        assert_eq!(wait_for(&server, 1), vec!["waiting"]);
        assert!(!server.exited());
        // Dropping the lifeline alone ends it, without any signal getting through.
        lock(&server.lifeline).take();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !server.exited() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        assert!(server.exited());
        assert_eq!(wait_for(&server, 2), vec!["closed", "waiting"]);
        server.stop();
    }

    #[test]
    fn a_closed_port_does_not_answer() {
        let held = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = held.local_addr().unwrap().port();
        drop(held);
        assert!(!answers(port));
        assert_eq!(hello(port, "n0nce-of_sixteen-plus"), None);
    }

    fn serve_once(reply: &'static [u8]) -> (u16, std::thread::JoinHandle<String>) {
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let serve = std::thread::spawn(move || {
            let (mut conn, _) = listener.accept().unwrap();
            let mut buf = [0u8; 1024];
            let n = conn.read(&mut buf).unwrap();
            conn.write_all(reply).unwrap();
            String::from_utf8_lossy(&buf[..n]).to_string()
        });
        (port, serve)
    }

    #[test]
    fn an_http_server_answers() {
        let (port, serve) = serve_once(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n");
        assert!(answers(port));
        assert!(serve
            .join()
            .unwrap()
            .starts_with(&format!("GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n")));
    }

    #[test]
    fn hello_reads_the_mac() {
        let (port, serve) = serve_once(
            b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: 12\r\n\r\n{\"mac\":\"ab\"}",
        );
        assert_eq!(hello(port, "n0nce-of_sixteen-plus").as_deref(), Some("ab"));
        assert!(serve
            .join()
            .unwrap()
            .starts_with("GET /api/desktop/hello?n=n0nce-of_sixteen-plus HTTP/1.1\r\n"));
    }

    #[test]
    fn hello_from_something_else_is_none() {
        let (port, serve) = serve_once(b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\n\r\n");
        assert_eq!(hello(port, "n0nce-of_sixteen-plus"), None);
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
