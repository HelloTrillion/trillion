"""Month-to-date API usage, read back for the pill and Settings → Usage (BRN-4, BRN-7).

Read only: every paid call (each model call, a sentence spoken, a stream heard)
already writes one `usage` row, through the metered providers (BRN-7). A call whose
provider has no prices keeps its cost NULL, and is counted here as unpriced units,
never as $0, so the bill can never look smaller than it is. `unpriced_turns` keeps
its old name for the API, and counts those calls.

`providers` lists each provider set up, with caps and where it stands: the model,
voice and hearing in use first, then the others that have caps, spend this month, or a
model known. Each says roughly what it costs and on what assumption (ONB-9), and one
on this computer says it costs nothing and has no cap (ONB-10).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from trillion.api.runtime import OWNER, Runtime
from trillion.spend import gate as spend_gate
from trillion.spend import prices
from trillion.spend.desk import Leg, friendly
from trillion.spend.gate import UNCAPPED
from trillion.spend.types import Caller
from trillion.zones import month_start

# A provider with no id runs on this computer, so it is named by what it does.
_ON_THIS_COMPUTER: dict[Leg, str] = {
    "model": "Your model",
    "voice": "This computer's voice",
    "hearing": "Listening on this computer",
}
_LEG_OF: dict[str, Leg] = {"tokens": "model", "chars": "voice", "seconds": "hearing"}


def leg_of(provider_id: str) -> Leg:
    """Whether a provider id is a model, a voice or hearing."""
    return _LEG_OF[prices.unit_of(provider_id)]


def _name(leg: Leg, provider_id: str | None) -> str:
    if provider_id is None:
        return _ON_THIS_COMPUTER[leg]
    if provider_id == "local":
        return "A local model"
    return friendly(provider_id)


def _round(n: float) -> float:
    return round(n, 6)


async def provider_row(
    rt: Runtime, leg: Leg, provider_id: str | None, model: str | None
) -> dict[str, Any]:
    """One provider as Settings → Usage shows it."""
    user = rt.meter.prices.rates()
    row: dict[str, Any] = {
        "provider_id": provider_id,
        "leg": leg,
        "name": _name(leg, provider_id),
        "model": model,
    }
    if provider_id is None or provider_id in UNCAPPED:
        return row | {
            "spent": 0.0,
            "unit": "dollars",
            "soft": None,
            "hard": None,
            "warn_pct": spend_gate.DEFAULT.warn_pct,
            "state": "ok",
            "split": {"conversation": 0.0, "background": 0.0},
            "paused": [],
            "switched": None,
            "unestimated": None,
            "estimate": prices.cost_line(None),
            "pricing_url": None,
        }
    caps = rt.gate.caps(provider_id)
    spent = rt.ledger.spent(provider_id)

    def share(caller: Caller) -> float:
        part = spent.by_caller.get(caller)
        if part is None:
            return 0.0
        if caps.unit != "dollars":
            # Every unit in the caps' own, priced or not, as the gate counts them.
            return part.all_units if spent.all_unit == caps.unit else 0.0
        extra = None
        if part.units and spent.unit is not None:
            extra = prices.estimate(provider_id, spent.unit, part.units)
        return _round(part.dollars + (extra or 0.0))

    switch = rt.spend.switch(provider_id)
    unestimated = rt.gate.unestimated(provider_id)
    counted = rt.gate.counted(provider_id)
    return row | {
        "spent": _round(counted) if caps.unit == "dollars" else counted,
        "unit": caps.unit,
        "soft": caps.soft,
        "hard": caps.hard,
        "warn_pct": caps.warn_pct,
        "state": rt.gate.state(provider_id),
        "split": {"conversation": share("conversation"), "background": share("background")},
        "paused": list(await rt.spend.paused(provider_id)),
        "switched": (
            {
                "to": switch.to,
                "label": switch.label,
                "until_ms": switch.until_ms,
                "line": rt.spend.line(provider_id),
            }
            if switch is not None
            else None
        ),
        "unestimated": (
            {"units": unestimated[0], "unit": unestimated[1]} if unestimated is not None else None
        ),
        "estimate": prices.cost_line(provider_id, model, user),
        "pricing_url": prices.pricing_url(provider_id),
    }


async def provider_rows(rt: Runtime, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every provider set up, in use first; `rows` are this month's usage rows."""
    await rt.gate.refresh()
    seen: dict[tuple[Leg, str | None] | str, tuple[Leg, str | None, str | None]] = {}
    used: dict[str, str] = {}
    for r in rows:
        pid = r.get("provider_id")
        if isinstance(pid, str):
            used.setdefault(pid, str(r["model"]))
    for leg, pid, model in await rt.spend_set_up():
        key: tuple[Leg, str | None] | str = pid if pid is not None else (leg, None)
        if key not in seen:
            seen[key] = (leg, pid, model or used.get(pid or ""))
    raw = await rt.preferences.get(OWNER, spend_gate.PREF)
    others = set(raw) if isinstance(raw, dict) else set()
    for pid in sorted((others | set(used)) - set(k for k in seen if isinstance(k, str))):
        if not isinstance(pid, str):
            continue
        seen[pid] = (leg_of(pid), pid, used.get(pid))
    return [await provider_row(rt, *found) for found in seen.values()]


def create_usage_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/usage")

    @router.get("")
    async def read() -> dict[str, Any]:
        # The same zone the spend ledger counts in, so the pill and the caps agree.
        month, since = month_start(rt.clock(), rt.zone() or "")
        rows = await rt.conversations.usage_since(OWNER, since)
        priced = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
        return {
            "month": month,
            "since_ms": since,
            "rows": rows,
            "total_cost_usd": round(sum(priced), 6) if priced else None,
            "unpriced_tokens": sum(r["unpriced_tokens"] for r in rows),
            "providers": await provider_rows(rt, rows),
        }

    return router


def create_spend_router(rt: Runtime) -> APIRouter:
    """What the page may do about a switch made at a cap (BRN-8). A card's own choice is
    a tap on the page's socket, never an HTTP call (SPN-14); ending a switch spends
    nothing more, so Settings and the banner may ask for it here, behind the session
    token and origin check like every API call (SPN-11)."""
    router = APIRouter(prefix="/api/spend")

    @router.post("/switch/{provider_id}/end")
    async def end_switch(provider_id: str) -> Any:
        if rt.spend.switch(provider_id) is None:
            return JSONResponse({"error": "no_switch"}, status_code=404)
        if rt.gate.state(provider_id) == "capped":
            # Going back would be refused at once: raise the cap first.
            return JSONResponse({"error": "still_capped"}, status_code=409)
        await rt.spend.end_switch(provider_id)
        return {"switches": rt.spend.banner()}

    return router
