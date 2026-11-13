"""`trillion doctor`'s line for her browser."""

from __future__ import annotations

from pathlib import Path


def test_the_doctor_line_says_what_her_browser_is(tmp_path: Path, monkeypatch):
    from trillion.browser import runtime
    from trillion.browser.signins import SignIns
    from trillion.cli.doctor import check_browser

    off = check_browser(None, tmp_path)
    assert off.status == "info"
    assert "not set up" in off.detail or "not included" in off.detail
    monkeypatch.setattr(runtime, "playwright_available", lambda: True)
    monkeypatch.setattr(
        runtime.Install,
        "state",
        lambda self: runtime.Installed(version="158.0", revision="1", executable=tmp_path),
    )
    SignIns(tmp_path / "browser" / "signins").keep("library.org", {"cookies": [], "origins": []})
    on = check_browser({"act": True}, tmp_path)
    assert on.status == "ok"
    assert on.detail == "Chromium 158.0, act mode on, signed in to 1 site"
