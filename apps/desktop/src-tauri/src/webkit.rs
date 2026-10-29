//! One WebKit setting the voice needs while the window is hidden.
//!
//! The page reports each sentence when it reaches the speaker with `setTimeout`
//! (voice.ts), and WebKit clamps timers in a hidden page to one second. Turning off
//! background throttling keeps audio and the socket running but not the timers; this
//! does. It is a private WebKit preference, so it is checked before use: without it the
//! page still works, its reports just come up to a second late.

use tauri::WebviewWindow;

pub fn keep_timers_when_hidden(window: &WebviewWindow) {
    let _ = window.with_webview(|webview| {
        use objc2::runtime::{AnyObject, Bool};
        use objc2::{msg_send, sel};
        let setter = sel!(_setHiddenPageDOMTimerThrottlingEnabled:);
        // SAFETY: `inner` is the live WKWebView; the setter is called only if it exists.
        unsafe {
            let wk: *mut AnyObject = webview.inner().cast();
            if wk.is_null() {
                return;
            }
            // `configuration` hands back a copy, but the copy shares the live
            // WKPreferences object, so a change to it reaches this webview.
            let config: *mut AnyObject = msg_send![wk, configuration];
            if config.is_null() {
                return;
            }
            let prefs: *mut AnyObject = msg_send![config, preferences];
            if prefs.is_null() {
                return;
            }
            let known: Bool = msg_send![prefs, respondsToSelector: setter];
            if !known.as_bool() {
                eprintln!("trillion: this WebKit cannot keep timers running in a hidden window; voice reports may be up to 1 s late");
                return;
            }
            let _: () = msg_send![prefs, _setHiddenPageDOMTimerThrottlingEnabled: Bool::NO];
        }
    });
}
