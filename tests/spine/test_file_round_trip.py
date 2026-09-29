"""A read → write round trip must not change the user's file (issue #14).

`file_read` used to return neutralized text, and `wrap` neutralized again on the way
to the model: code fences became `[fence removed]`, a `User:` line became
`[role marker removed]`, zero-width joiners were stripped (breaking emoji and Persian
and Indic text) and NFKC folded compatibility characters. Reading a Markdown file and
writing it back silently rewrote it. File reads now use the lossless view
(spine/lossless.py): the same guarantees, but characters are escaped as ⟦U+XXXX⟧ tokens
instead of deleted, and the write tools decode them back.
"""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion.spine.confirm import Refusal, Status

FENCE = "`" * 3
ZWJ_EMOJI = "\U0001f469\u200d\U0001f4bb"  # woman technologist: two emoji and a ZWJ
LIGATURE = "\ufb01"  # "fi" ligature: NFKC folds it to "fi"

ORIGINAL = (
    "# Notes\n\n"
    f"{FENCE}python\nprint('hi')\n{FENCE}\n\n"
    "User: this line starts with a role marker\n"
    f"Emoji {ZWJ_EMOJI} and a {LIGATURE}le.\n"
    "~~~\nplain\n~~~\n"
)


def _unwrap(content: str) -> str:
    """The file text between the wrapper's preamble and its closing tag."""
    body = content.split("\n---\n", 1)[1]
    return body.rsplit("\n</untrusted_tool_result>", 1)[0]


async def test_read_then_overwrite_is_byte_identical(spine: Spine) -> None:
    doc = spine.granted / "notes.md"
    doc.write_bytes(ORIGINAL.encode("utf-8"))
    read = await spine.call("file_read", {"path": str(doc)})
    assert read.status == "succeeded"
    seen = _unwrap(read.content or "")
    assert FENCE in seen and "~~~" in seen, "fences reach the model as they are"
    assert "[fence removed]" not in seen and "[role marker removed]" not in seen

    edited = seen.replace("# Notes", "# Notes (edited)")
    p = await spine.call("file_overwrite", {"path": str(doc), "content": edited})
    ok = await spine.router.resolve(p.pending_id or "", "confirm", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded"
    assert doc.read_bytes().decode("utf-8") == ORIGINAL.replace("# Notes", "# Notes (edited)")


async def test_windows_line_endings_survive_the_round_trip(spine: Spine) -> None:
    """A file saved with CRLF comes back with CRLF: nothing normalizes line endings."""
    crlf = ORIGINAL.replace("\n", "\r\n")
    doc = spine.granted / "crlf.md"
    doc.write_bytes(crlf.encode("utf-8"))
    seen = _unwrap((await spine.call("file_read", {"path": str(doc)})).content or "")
    p = await spine.call("file_overwrite", {"path": str(doc), "content": seen})
    ok = await spine.router.resolve(p.pending_id or "", "confirm", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded"
    assert doc.read_bytes() == crlf.encode("utf-8")


async def test_the_read_is_still_wrapped_as_untrusted(spine: Spine) -> None:
    doc = spine.granted / "notes.md"
    doc.write_bytes(ORIGINAL.encode("utf-8"))
    read = await spine.call("file_read", {"path": str(doc)})
    assert (read.content or "").startswith('<untrusted_tool_result tool="file_read">')


async def test_the_host_explains_the_tokens(spine: Spine) -> None:
    """The model is told, in host words, that tokens are there and must be kept. A count
    only — never a sample, which would be the file's own words again."""
    doc = spine.granted / "notes.md"
    doc.write_bytes(ORIGINAL.encode("utf-8"))
    read = await spine.call("file_read", {"path": str(doc)})
    note = read.warning or ""
    assert "⟦U+XXXX⟧" in note and "restored" in note
    assert "this line starts" not in note


async def test_a_plain_file_carries_no_note(spine: Spine) -> None:
    doc = spine.granted / "plain.txt"
    doc.write_bytes("just words\n".encode("utf-8"))
    read = await spine.call("file_read", {"path": str(doc)})
    assert read.warning is None


async def test_a_file_that_is_not_utf8_is_refused_rather_than_mangled(spine: Spine) -> None:
    """Decoding with replacement turned unknown bytes into U+FFFD, which a write-back
    would then save. Refuse instead."""
    blob = spine.granted / "latin1.txt"
    blob.write_bytes("caf\xe9\n".encode("latin-1"))
    read = await spine.call("file_read", {"path": str(blob)})
    assert read.status == "failed" and read.error_kind == "not_utf8"


async def test_other_tools_are_still_neutralized(spine: Spine) -> None:
    """The lossless view is file_read's alone. Everything else keeps SPN-6."""
    spine.reader.ok = True
    r = await spine.call("plain_reader", {})
    assert r.warning is None
    assert "</untrusted_tool_result> world" not in (r.content or "").split("\n---\n", 1)[1]


# ── file_edit: change a part, never rewrite the whole ─────────────────────


async def test_an_edit_of_text_read_with_tokens_lands_exactly(spine: Spine) -> None:
    """Editing a line the model saw with tokens in it: it passes them back as shown."""
    doc = spine.granted / "notes.md"
    doc.write_bytes(ORIGINAL.encode("utf-8"))
    seen = _unwrap((await spine.call("file_read", {"path": str(doc)})).content or "")
    old_line = next(line for line in seen.split("\n") if line.startswith("Emoji"))
    p = await spine.call(
        "file_edit", {"path": str(doc), "old": old_line, "new": old_line + " (kept)"}
    )
    assert p.status == "confirmation_required", p
    ok = await spine.router.resolve(p.pending_id or "", "confirm", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded"
    want = ORIGINAL.replace(f"a {LIGATURE}le.", f"a {LIGATURE}le. (kept)")
    assert doc.read_bytes().decode("utf-8") == want


async def test_an_edit_changes_only_the_named_text(spine: Spine) -> None:
    doc = spine.granted / "notes.md"
    doc.write_bytes(ORIGINAL.encode("utf-8"))
    p = await spine.call(
        "file_edit", {"path": str(doc), "old": "print('hi')", "new": "print('hello')"}
    )
    assert p.status == "confirmation_required"
    assert "line 4" in (p.summary or "")
    ok = await spine.router.resolve(p.pending_id or "", "confirm", spine.ctx(turn=2, via="tap"))
    assert ok.status == "succeeded"
    assert doc.read_bytes().decode("utf-8") == ORIGINAL.replace("print('hi')", "print('hello')")


async def test_an_edit_whose_text_is_missing_is_refused(spine: Spine) -> None:
    doc = spine.granted / "notes.md"
    doc.write_bytes(ORIGINAL.encode("utf-8"))
    r = await spine.call("file_edit", {"path": str(doc), "old": "not in the file", "new": "x"})
    assert r.reason is Refusal.PRECONDITION_FAILED
    assert "not found" in (r.detail or "")


async def test_an_ambiguous_edit_is_refused(spine: Spine) -> None:
    doc = spine.granted / "notes.md"
    doc.write_bytes("same\nsame\n".encode("utf-8"))
    r = await spine.call("file_edit", {"path": str(doc), "old": "same", "new": "x"})
    assert r.reason is Refusal.PRECONDITION_FAILED
    assert "2 times" in (r.detail or "")


async def test_an_edit_notices_the_file_changed_since_the_proposal(spine: Spine) -> None:
    doc = spine.granted / "notes.md"
    doc.write_bytes("alpha beta\n".encode("utf-8"))
    p = await spine.call("file_edit", {"path": str(doc), "old": "beta", "new": "gamma"})
    doc.write_bytes("alpha beta, plus the user's own edit\n".encode("utf-8"))
    res = await spine.router.resolve(p.pending_id or "", "confirm", spine.ctx(turn=2, via="tap"))
    assert res.reason is Refusal.PRECONDITION_CHANGED
    assert doc.read_bytes().decode("utf-8") == "alpha beta, plus the user's own edit\n"
    a = await spine.store.get_pending(p.pending_id or "")
    assert a is not None and a.status is Status.INVALIDATED
