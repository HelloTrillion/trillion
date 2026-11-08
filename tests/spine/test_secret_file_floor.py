"""SEC-10: no tool reads the encrypted key file or the temporary file a write makes,
even when the folder holding them was granted, and each attempt is in the audit log."""

from __future__ import annotations

from tests.spine.conftest import Spine
from trillion import paths
from trillion.spine.confirm import Refusal


async def test_the_key_file_and_its_temporary_file_are_refused_and_recorded(
    spine: Spine,
) -> None:
    home = paths.home()
    home.mkdir(parents=True, exist_ok=True)
    real = paths.secrets_path()
    real.write_bytes(b"TRLSEC")
    # The name a write gives its temporary file (`fallback.write_private`).
    temp = home / f".{real.name}.abc123.tmp"
    temp.write_bytes(b"TRLSEC")
    for granted in ((spine.granted,), (home,), (home.parent,)):
        for target in (real, temp):
            before = len(await spine.store.list_audit())
            r = await spine.call("file_read", {"path": str(target)}, granted_roots=granted)
            assert r.reason is Refusal.DENIED_BY_FLOOR, (target, granted)
            rows = (await spine.store.list_audit())[before:]
            assert [(row.tool, row.decision, row.outcome) for row in rows] == [
                ("file_read", "refused", "denied_by_floor")
            ], (target, granted)
    listed = await spine.call("file_list", {"path": str(home)}, granted_roots=(home.parent,))
    assert listed.reason is Refusal.DENIED_BY_FLOOR
