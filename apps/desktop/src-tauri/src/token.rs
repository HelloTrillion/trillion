//! The session token the app makes and hands its server (SPN-11a): only the app and
//! its child know it, in memory. Never logged, never written down.

const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

/// 32 bytes from the OS, URL-safe base64 without padding: 43 characters, as the
/// server's own `token_urlsafe(32)`.
pub fn new_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes).expect("the OS random source");
    encode(&bytes)
}

fn encode(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let n = chunk
            .iter()
            .enumerate()
            .fold(0u32, |n, (i, b)| n | u32::from(*b) << (16 - 8 * i));
        // 1 byte gives 2 characters, 2 give 3, 3 give 4: no padding.
        for i in 0..=chunk.len() {
            out.push(char::from(ALPHABET[(n >> (18 - 6 * i) & 63) as usize]));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forty_three_url_safe_characters() {
        let t = new_token();
        assert_eq!(t.len(), 43);
        assert!(t
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'));
    }

    #[test]
    fn each_is_new() {
        assert_ne!(new_token(), new_token());
    }

    #[test]
    fn encodes_as_base64url() {
        assert_eq!(encode(b""), "");
        assert_eq!(encode(b"f"), "Zg");
        assert_eq!(encode(b"fo"), "Zm8");
        assert_eq!(encode(b"foo"), "Zm9v");
        assert_eq!(encode(&[0xfb, 0xff]), "-_8");
    }
}
