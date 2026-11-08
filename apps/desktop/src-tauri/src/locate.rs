//! Finding the user's `trillion`. Apps opened from the Finder do not get the shell's
//! PATH, so the login shell is asked, then the places installers put it.

use std::path::{Path, PathBuf};

/// Where installers put it, in the order they are tried.
#[cfg(not(windows))]
pub const FALLBACKS: &[&str] = &[
    "~/.local/bin/trillion",
    "/opt/homebrew/bin/trillion",
    "/usr/local/bin/trillion",
];
/// Windows: uv's tool folder, where `install.ps1` puts it.
#[cfg(windows)]
pub const FALLBACKS: &[&str] = &["~/.local/bin/trillion.exe"];

/// The program's file name.
#[cfg(not(windows))]
const NAME: &str = "trillion";
#[cfg(windows)]
const NAME: &str = "trillion.exe";

/// What looking found.
#[derive(Debug, PartialEq, Eq)]
pub enum Located {
    Found(PathBuf),
    /// `TRILLION_BIN` names something that is not a program.
    NotRunnable(PathBuf),
    Missing,
}

/// `TRILLION_BIN` wins when set; else the login shell's answer, when it names an
/// executable file; else the first executable fallback.
pub fn locate(
    env_bin: Option<&str>,
    shell_answer: Option<&str>,
    home: Option<&Path>,
    is_exec: &dyn Fn(&Path) -> bool,
) -> Located {
    if let Some(bin) = env_bin.filter(|b| !b.is_empty()) {
        // Set on purpose: a wrong path is reported, not quietly replaced.
        let bin = PathBuf::from(bin);
        return if is_exec(&bin) {
            Located::Found(bin)
        } else {
            Located::NotRunnable(bin)
        };
    }
    // `command -v` can answer with an alias or a function: only a path counts.
    let is_path = |a: &&str| a.starts_with('/') || Path::new(a).is_absolute();
    if let Some(answer) = shell_answer.map(str::trim).filter(is_path) {
        let answer = PathBuf::from(answer);
        if is_exec(&answer) {
            return Located::Found(answer);
        }
    }
    FALLBACKS
        .iter()
        .filter_map(|f| match f.strip_prefix("~/") {
            Some(rest) => home.map(|h| h.join(rest)),
            None => Some(PathBuf::from(f)),
        })
        .find(|p| is_exec(p))
        .map_or(Located::Missing, Located::Found)
}

/// A file someone may run.
#[cfg(unix)]
pub fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path)
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

/// A file someone may run: on Windows, a program file.
#[cfg(not(unix))]
pub fn is_executable(path: &Path) -> bool {
    let program = path
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("exe") || e.eq_ignore_ascii_case("com"));
    program && path.is_file()
}

/// The first `trillion` in a PATH value, as a shell would find it. Windows has no
/// login shell to ask, so the app's own PATH, which the installer added uv's tool
/// folder to, stands in for it.
pub fn on_path(path: Option<&str>, is_exec: &dyn Fn(&Path) -> bool) -> Option<String> {
    let separator = if cfg!(windows) { ';' } else { ':' };
    path?
        .split(separator)
        .filter(|dir| !dir.is_empty())
        .map(|dir| Path::new(dir.trim_matches('"')).join(NAME))
        .find(|p| is_exec(p))
        .map(|p| p.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    fn files(set: &[&str]) -> impl Fn(&Path) -> bool {
        let set: HashSet<PathBuf> = set.iter().map(PathBuf::from).collect();
        move |p: &Path| set.contains(p)
    }

    const HOME: &str = "/Users/u";

    #[test]
    fn the_env_var_wins() {
        let exec = files(&["/x/trillion", "/opt/homebrew/bin/trillion"]);
        let found = locate(
            Some("/x/trillion"),
            Some("/opt/homebrew/bin/trillion"),
            Some(Path::new(HOME)),
            &exec,
        );
        assert_eq!(found, Located::Found(PathBuf::from("/x/trillion")));
    }

    #[test]
    fn an_env_var_naming_nothing_is_missing_not_a_fallback() {
        let exec = files(&["/opt/homebrew/bin/trillion"]);
        let found = locate(Some("/nope"), None, Some(Path::new(HOME)), &exec);
        assert_eq!(found, Located::NotRunnable(PathBuf::from("/nope")));
    }

    #[cfg(unix)]
    #[test]
    fn an_empty_env_var_is_unset() {
        let exec = files(&["/opt/homebrew/bin/trillion"]);
        let found = locate(Some(""), None, Some(Path::new(HOME)), &exec);
        assert_eq!(
            found,
            Located::Found(PathBuf::from("/opt/homebrew/bin/trillion"))
        );
    }

    #[test]
    fn the_shell_answer_is_used_when_it_names_an_executable() {
        let exec = files(&["/Users/u/.cargo/bin/trillion", "/usr/local/bin/trillion"]);
        let found = locate(
            None,
            Some("/Users/u/.cargo/bin/trillion\n"),
            Some(Path::new(HOME)),
            &exec,
        );
        assert_eq!(
            found,
            Located::Found(PathBuf::from("/Users/u/.cargo/bin/trillion"))
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_shell_alias_or_missing_file_falls_through() {
        let exec = files(&["/usr/local/bin/trillion"]);
        for answer in [
            "alias trillion='uv run trillion'",
            "/gone/trillion",
            "trillion",
            "",
        ] {
            let found = locate(None, Some(answer), Some(Path::new(HOME)), &exec);
            assert_eq!(
                found,
                Located::Found(PathBuf::from("/usr/local/bin/trillion")),
                "{answer}"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_finds_it_on_the_path_or_in_uvs_tool_folder() {
        let exec = files(&[r"C:\Users\u\.local\bin\trillion.exe"]);
        let path = r"C:\Windows;C:\Users\u\.local\bin";
        let found = on_path(Some(path), &exec);
        assert_eq!(
            found.as_deref(),
            Some(r"C:\Users\u\.local\bin\trillion.exe")
        );
        assert_eq!(
            locate(
                None,
                found.as_deref(),
                Some(Path::new(r"C:\Users\u")),
                &exec
            ),
            Located::Found(PathBuf::from(r"C:\Users\u\.local\bin\trillion.exe"))
        );
        let fallback = |p: &Path| p == Path::new(r"C:\Users\u\.local/bin/trillion.exe");
        assert_eq!(on_path(Some(r"C:\Windows"), &fallback), None);
        assert_eq!(
            locate(None, None, Some(Path::new(r"C:\Users\u")), &fallback),
            Located::Found(PathBuf::from(r"C:\Users\u\.local/bin/trillion.exe"))
        );
    }

    #[cfg(unix)]
    #[test]
    fn the_path_is_searched_in_order() {
        let exec = files(&["/b/trillion", "/c/trillion"]);
        assert_eq!(
            on_path(Some("/a::/b:/c"), &exec).as_deref(),
            Some("/b/trillion")
        );
        assert_eq!(on_path(None, &exec), None);
    }

    #[cfg(unix)]
    #[test]
    fn the_fallbacks_in_order() {
        let all = files(&[
            "/Users/u/.local/bin/trillion",
            "/opt/homebrew/bin/trillion",
            "/usr/local/bin/trillion",
        ]);
        assert_eq!(
            locate(None, None, Some(Path::new(HOME)), &all),
            Located::Found(PathBuf::from("/Users/u/.local/bin/trillion"))
        );
        let two = files(&["/opt/homebrew/bin/trillion", "/usr/local/bin/trillion"]);
        assert_eq!(
            locate(None, None, Some(Path::new(HOME)), &two),
            Located::Found(PathBuf::from("/opt/homebrew/bin/trillion"))
        );
        let one = files(&["/usr/local/bin/trillion"]);
        assert_eq!(
            locate(None, None, Some(Path::new(HOME)), &one),
            Located::Found(PathBuf::from("/usr/local/bin/trillion"))
        );
    }

    #[cfg(unix)]
    #[test]
    fn no_home_skips_the_home_fallback() {
        let exec = files(&["/Users/u/.local/bin/trillion"]);
        assert_eq!(locate(None, None, None, &exec), Located::Missing);
    }

    #[test]
    fn nothing_is_missing() {
        let none = files(&[]);
        assert_eq!(
            locate(None, None, Some(Path::new(HOME)), &none),
            Located::Missing
        );
    }
}
