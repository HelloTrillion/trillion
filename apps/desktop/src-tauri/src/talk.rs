//! The talk shortcut from any app (D5, D6): which shortcuts may be global, how the
//! menu names one, and the pure checks on what the page sends.

/// The modifiers, in the only order the page writes them.
const MODIFIERS: [&str; 4] = ["Super", "Control", "Alt", "Shift"];

/// The keys a global shortcut may use, by `KeyboardEvent.code` (hotkey.ts `GLOBAL_KEY`).
fn global_key(code: &str) -> bool {
    let named = [
        "Space",
        "Enter",
        "Tab",
        "Escape",
        "Backspace",
        "Delete",
        "Insert",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "Backquote",
        "Minus",
        "Equal",
        "BracketLeft",
        "BracketRight",
        "Backslash",
        "Semicolon",
        "Quote",
        "Comma",
        "Period",
        "Slash",
        "IntlBackslash",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
    ];
    if named.contains(&code) {
        return true;
    }
    let one = |prefix: &str, ok: fn(char) -> bool| {
        code.strip_prefix(prefix)
            .is_some_and(|rest| rest.chars().count() == 1 && rest.chars().all(ok))
    };
    if one("Key", |c| c.is_ascii_uppercase())
        || one("Digit", |c| c.is_ascii_digit())
        || one("Numpad", |c| c.is_ascii_digit())
    {
        return true;
    }
    code.strip_prefix('F')
        .and_then(|n| (!n.starts_with('0')).then_some(n))
        .and_then(|n| n.parse::<u8>().ok())
        .is_some_and(|n| (1..=20).contains(&n))
}

/// The D6 rule, as hotkey.ts `globalAccelerator` writes it. Anything the page should
/// never send is refused too: unknown or repeated modifiers, another order.
///
///   1. Command (Super), Control or Option (Alt); Shift may be added.
///   2. The key is one `global_key` knows.
///   3. Not Command or Control alone (no Option, no Shift) with a letter, Space, Tab
///      or Backquote: every app uses those.
///   4. Not Command or Control alone, with or without Shift, with K, \ or , (the
///      page's own shortcuts).
pub fn may_be_global(accelerator: &str) -> bool {
    let parts: Vec<&str> = accelerator.split('+').collect();
    let Some((code, mods)) = parts.split_last() else {
        return false;
    };
    let mut last = None;
    for m in mods {
        let Some(at) = MODIFIERS.iter().position(|x| x == m) else {
            return false;
        };
        if last.is_some_and(|l| at <= l) {
            return false;
        }
        last = Some(at);
    }
    let has = |m: &str| mods.contains(&m);
    let (sup, ctrl, alt, shift) = (has("Super"), has("Control"), has("Alt"), has("Shift"));
    if !(sup || ctrl || alt) || !global_key(code) {
        return false;
    }
    let primary_only = sup != ctrl && !alt;
    let common = code.starts_with("Key") || ["Space", "Tab", "Backquote"].contains(code);
    if primary_only && !shift && common {
        return false;
    }
    !(primary_only && ["KeyK", "Backslash", "Comma"].contains(code))
}

/// How the menu shows a shortcut, the Mac way: ⌃⌥⇧⌘ then the key.
pub fn label(accelerator: &str) -> String {
    let parts: Vec<&str> = accelerator.split('+').collect();
    let Some((code, mods)) = parts.split_last() else {
        return String::new();
    };
    let mut out = String::new();
    for (name, sign) in [
        ("Control", "⌃"),
        ("Alt", "⌥"),
        ("Shift", "⇧"),
        ("Super", "⌘"),
    ] {
        if mods.contains(&name) {
            out.push_str(sign);
        }
    }
    let key = match *code {
        "ArrowUp" => "↑".to_string(),
        "ArrowDown" => "↓".to_string(),
        "ArrowLeft" => "←".to_string(),
        "ArrowRight" => "→".to_string(),
        "Backquote" => "`".to_string(),
        "Minus" => "-".to_string(),
        "Equal" => "=".to_string(),
        "BracketLeft" => "[".to_string(),
        "BracketRight" => "]".to_string(),
        "Backslash" => "\\".to_string(),
        "Semicolon" => ";".to_string(),
        "Quote" => "'".to_string(),
        "Comma" => ",".to_string(),
        "Period" => ".".to_string(),
        "Slash" => "/".to_string(),
        c => {
            if let Some(k) = c.strip_prefix("Key").or_else(|| c.strip_prefix("Digit")) {
                k.to_string()
            } else if let Some(n) = c.strip_prefix("Numpad") {
                format!("Num {n}")
            } else {
                c.to_string()
            }
        }
    };
    out.push_str(&key);
    out
}

/// Only web addresses go to the browser.
pub fn web_url(url: &str) -> Option<url::Url> {
    let parsed = url::Url::parse(url).ok()?;
    (matches!(parsed.scheme(), "http" | "https") && parsed.host_str().is_some()).then_some(parsed)
}

/// The states the page reports.
pub fn known_state(state: &str) -> bool {
    matches!(state, "idle" | "listening" | "thinking" | "speaking")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_d6_table() {
        let allowed = [
            "Alt+Space",
            "Control+Alt+KeyT",
            "Super+F5",
            "Alt+KeyA",
            "Super+Alt+KeyK",
            "Control+Shift+KeyT",
            "Super+Shift+Space",
            "Alt+Shift+Digit1",
            "Control+Numpad0",
            "Super+F20",
            "Alt+ArrowUp",
            "Control+Alt+Backslash",
            "Super+Digit1",
            "Alt+Comma",
            "Super+Alt+Shift+Slash",
        ];
        for a in allowed {
            assert!(may_be_global(a), "{a} should be allowed");
        }
        let refused = [
            "Super+KeyC",
            "Control+Space",
            "Super+Shift+KeyK",
            "Super+Comma",
            "Alt+F21",
            "Shift+KeyK",
            "Super+Tab",
            "Control+Backquote",
            "Control+Backslash",
            "Super+Shift+Comma",
            "Space",
            "KeyA",
            "",
            "Alt+",
            "Alt+F0",
            "Alt+F05",
            "Alt+Keya",
            "Alt+KeyAB",
            "Alt+Fn",
            "Alt+AltLeft",
            "Alt+Super+Space",
            "Alt+Alt+Space",
            "Option+Space",
            "Super+Control+KeyC+KeyD",
        ];
        for r in refused {
            assert!(!may_be_global(r), "{r} should be refused");
        }
    }

    #[test]
    fn both_command_and_control_with_a_letter_is_allowed() {
        // Not "one of them alone": rule 3 does not apply.
        assert!(may_be_global("Super+Control+KeyC"));
    }

    #[test]
    fn labels_the_mac_way() {
        assert_eq!(label("Alt+Space"), "⌥Space");
        assert_eq!(label("Control+Alt+KeyT"), "⌃⌥T");
        assert_eq!(label("Super+Shift+Digit5"), "⇧⌘5");
        assert_eq!(label("Super+Control+Alt+Shift+ArrowUp"), "⌃⌥⇧⌘↑");
        assert_eq!(label("Alt+Numpad3"), "⌥Num 3");
        assert_eq!(label("Super+F5"), "⌘F5");
        assert_eq!(label("Alt+Slash"), "⌥/");
    }

    #[test]
    fn only_web_addresses_open() {
        assert!(web_url("https://accounts.google.com/o/oauth2?x=1").is_some());
        assert!(web_url("http://127.0.0.1:7778/").is_some());
        for bad in [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "mailto:a@b.c",
            "tauri://localhost/",
            "x-apple.systempreferences:com.apple",
            "not a url",
            "https://",
            "",
        ] {
            assert!(web_url(bad).is_none(), "{bad}");
        }
    }

    #[test]
    fn the_four_states() {
        for s in ["idle", "listening", "thinking", "speaking"] {
            assert!(known_state(s));
        }
        assert!(!known_state("arming"));
        assert!(!known_state(""));
    }
}
