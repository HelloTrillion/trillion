//! The port: 7777, else the first free one from 7778 to 7799.

use std::net::TcpListener;

pub const FIRST: u16 = 7777;
pub const LAST: u16 = 7799;

pub fn pick(is_free: &dyn Fn(u16) -> bool) -> Option<u16> {
    (FIRST..=LAST).find(|p| is_free(*p))
}

/// Whether the server could listen here. Std sets SO_REUSEADDR on Unix, as the
/// server does, so a port a stopped server left in TIME_WAIT counts as free.
pub fn is_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_usual_port_when_free() {
        assert_eq!(pick(&|_| true), Some(7777));
    }

    #[test]
    fn the_next_free_one_when_taken() {
        assert_eq!(pick(&|p| p != 7777), Some(7778));
        assert_eq!(pick(&|p| p >= 7790), Some(7790));
        assert_eq!(pick(&|p| p == 7799), Some(7799));
    }

    #[test]
    fn none_free_is_none() {
        assert_eq!(pick(&|p| !(FIRST..=LAST).contains(&p)), None);
    }

    #[test]
    fn a_port_in_use_is_not_free() {
        let held = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = held.local_addr().unwrap().port();
        assert!(!is_free(port));
        drop(held);
        assert!(is_free(port));
    }
}
