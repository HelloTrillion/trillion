//! Finding the user's `trillion`. Apps opened from the Finder do not get the shell's
//! PATH, so the login shell is asked, then the places installers put it.

use std::path::{Path, PathBuf};

/// Where installers put it, in the order they are tried.
pub const FALLBACKS: [&str; 3] = [
    "~/.local/bin/trillion",
    "/opt/homebrew/bin/trillion",
    "/usr/local/bin/trillion",
];

/// `TRILLION_BIN` wins when set; else the login shell's answer, when it names an
/// executable file; else the first executable fallback.
pub fn locate(
    env_bin: Option<&str>,
    shell_answer: Option<&str>,
    home: Option<&Path>,
    is_exec: &dyn Fn(&Path) -> bool,
) -> Option<PathBuf> {
    if let Some(bin) = env_bin.filter(|b| !b.is_empty()) {
        // Set on purpose: a wrong path is reported, not quietly replaced.
        let bin = PathBuf::from(bin);
        return is_exec(&bin).then_some(bin);
    }
    // `command -v` can answer with an alias or a function: only a path counts.
    if let Some(answer) = shell_answer.map(str::trim).filter(|a| a.starts_with('/')) {
        let answer = PathBuf::from(answer);
        if is_exec(&answer) {
            return Some(answer);
        }
    }
    FALLBACKS
        .iter()
        .filter_map(|f| match f.strip_prefix("~/") {
            Some(rest) => home.map(|h| h.join(rest)),
            None => Some(PathBuf::from(f)),
        })
        .find(|p| is_exec(p))
}

/// A file someone may run.
pub fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(path)
        .map(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
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
        assert_eq!(found, Some(PathBuf::from("/x/trillion")));
    }

    #[test]
    fn an_env_var_naming_nothing_is_missing_not_a_fallback() {
        let exec = files(&["/opt/homebrew/bin/trillion"]);
        let found = locate(Some("/nope"), None, Some(Path::new(HOME)), &exec);
        assert_eq!(found, None);
    }

    #[test]
    fn an_empty_env_var_is_unset() {
        let exec = files(&["/opt/homebrew/bin/trillion"]);
        let found = locate(Some(""), None, Some(Path::new(HOME)), &exec);
        assert_eq!(found, Some(PathBuf::from("/opt/homebrew/bin/trillion")));
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
        assert_eq!(found, Some(PathBuf::from("/Users/u/.cargo/bin/trillion")));
    }

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
                Some(PathBuf::from("/usr/local/bin/trillion")),
                "{answer}"
            );
        }
    }

    #[test]
    fn the_fallbacks_in_order() {
        let all = files(&[
            "/Users/u/.local/bin/trillion",
            "/opt/homebrew/bin/trillion",
            "/usr/local/bin/trillion",
        ]);
        assert_eq!(
            locate(None, None, Some(Path::new(HOME)), &all),
            Some(PathBuf::from("/Users/u/.local/bin/trillion"))
        );
        let two = files(&["/opt/homebrew/bin/trillion", "/usr/local/bin/trillion"]);
        assert_eq!(
            locate(None, None, Some(Path::new(HOME)), &two),
            Some(PathBuf::from("/opt/homebrew/bin/trillion"))
        );
        let one = files(&["/usr/local/bin/trillion"]);
        assert_eq!(
            locate(None, None, Some(Path::new(HOME)), &one),
            Some(PathBuf::from("/usr/local/bin/trillion"))
        );
    }

    #[test]
    fn no_home_skips_the_home_fallback() {
        let exec = files(&["/Users/u/.local/bin/trillion"]);
        assert_eq!(locate(None, None, None, &exec), None);
    }

    #[test]
    fn nothing_is_missing() {
        let none = files(&[]);
        assert_eq!(locate(None, None, Some(Path::new(HOME)), &none), None);
    }
}
