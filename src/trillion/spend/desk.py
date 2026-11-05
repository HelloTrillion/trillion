"""Your conversation at the caps (BRN-7, BRN-8): the spend card, the switch that lasts
until the 1st, and what is said once a month.

**The spend card.** At a hard cap the session sends one line from the app and then a
card with the choices that cap allows. It is the app's card, not a pending action. A
choice comes as a `spend.choice` message on the page's own socket, like a tap on an
approval card, and is accepted only from the conversation it was sent to and only by a
tap (SPN-14): it is about money. One is answered at a time, and against the choices
the card would offer now. Cards are kept in the preference `spend_cards`, so a reload,
or a restart, shows a card still waiting, or one put off with "stop for now", from the
banner. A card closes when it is answered by raising or switching, and is no longer
shown once its provider is not capped.

**The switch.** "Use X until Dec 1" is the preference `spend_switch`, `{provider_id:
{leg, to, label, month, until_ms, settings}}`. It holds for the month it was made in,
in the user's timezone; `until_ms`, the next 1st, is for the banner. The runtime
applies it whenever it picks a model, voice or hearing, so it holds after a restart,
and follows a switch to a provider that was switched in turn. Once the month is
another it lapses, and her next turn carries a host note saying she is back on the
usual one (PER-11), once.

**Once a month.** The preference `spend_told` records, per provider and month, that
the warning notice was given (`warned`), that the soft cap was mentioned
(`over_soft`) and that background work was said to be paused (`paused`), and holds
the lapse notes still to be said (`back`) and any other note to be said once
(`once`, by name: the caps put on at the upgrade).

**Paused work.** At a hard cap, background work on that provider is refused (the
spend caps spec, decision 7): it never moves to another model, a switch included.
Each kind asks the desk before it starts (`held`), and one refused mid-way is told to
it by the gate (`pause`). `paused` lists the kinds of background work set up now
(`background`, from the runtime) while the provider they run on is capped, so it
holds across a restart. Once the cap is raised, or the month turns, the provider is
not capped, the list is empty, and the work runs again on its own next turn. The
first pause of a month gives one notice through the one door, fixed words of the
app's, since the capped model can't word it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from trillion.spend.gate import DEFAULT_CHARS, DEFAULT_SECONDS, DEFAULT_TOKENS, PREF, Caps, Gate
from trillion.spend.types import Work
from trillion.zones import month_start, next_month_start

log = logging.getLogger(__name__)

SWITCH_PREF = "spend_switch"
CARDS_PREF = "spend_cards"
TOLD_PREF = "spend_told"
# The model last chosen on each provider, so a card can offer one already set up.
KNOWN_PREF = "models_known"

Leg = Literal["model", "voice", "hearing"]
Status = Literal["open", "stopped", "raised", "switched"]
# A card still to be shown: waiting for a choice, or put off from the banner.
_STANDING: frozenset[str] = frozenset({"open", "stopped"})
# What each leg's card offers besides raising and switching.
_STOP: dict[Leg, str] = {"model": "stop", "voice": "text", "hearing": "type"}


# ── Words ────────────────────────────────────────────────────────────────

_NAMES = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "elevenlabs": "ElevenLabs",
    "deepgram": "Deepgram",
    "local": "this computer",
}
_HOSTS = {"openrouter.ai": "OpenRouter", "api.deepseek.com": "DeepSeek", "api.openai.com": "OpenAI"}


def friendly(provider_id: str) -> str:
    """A provider as the user knows it: its name, or the host of a custom address."""
    if provider_id in _NAMES:
        return _NAMES[provider_id]
    _kind, _, host = provider_id.partition(":")
    return _HOSTS.get(host, host) if host else provider_id


def _number(n: float, word: str, plural: bool) -> str:
    s = "s" if plural and n != 1 else ""
    if n >= 1_000_000 and round(n / 1_000_000, 1) * 1_000_000 == round(n):
        return f"{n / 1_000_000:g} million {word}{s}"
    return f"{n:,.0f} {word}{s}"


def _words(n: float, unit: str, *, plural: bool) -> str:
    if unit == "dollars":
        return f"${n:,.0f}" if float(n).is_integer() else f"${n:,.2f}"
    if unit == "seconds":
        return _number(n / 60, "minute", plural)
    return _number(n, "character" if unit == "chars" else "token", plural)


def cap_words(cap: float, unit: str) -> str:
    """A cap as it is named before the word cap: "$50", "12 million token"."""
    return _words(cap, unit, plural=False)


def amount_words(n: float, unit: str) -> str:
    """An amount spent: "$16.50", "4.2 million tokens"."""
    if unit == "dollars":
        return f"${n:,.2f}"
    return _words(n, unit, plural=True)


# The step a raise suggests: $25, and for a cap counted in units, half that unit's
# default hard cap, which is the same $25 at the rate the default assumes.
_STEP = {
    "dollars": 25.0,
    "tokens": float(DEFAULT_TOKENS.hard or 0) / 2,
    "chars": float(DEFAULT_CHARS.hard or 0) / 2,
    "seconds": float(DEFAULT_SECONDS.hard or 0) / 2,
}


# The highest a raise may go, so a slip of the finger is refused, not written.
MOST = {
    "dollars": 10_000.0,
    "tokens": float(DEFAULT_TOKENS.hard or 0) * 100,
    "chars": float(DEFAULT_CHARS.hard or 0) * 100,
    "seconds": float(DEFAULT_SECONDS.hard or 0) * 100,
}


def next_step(cap: float, unit: str) -> float:
    """The next round step above `cap`: $50 suggests $75, $60 suggests $75 too."""
    step = _STEP.get(unit, 25.0)
    return (math.floor(cap / step + 1e-9) + 1) * step


def until_words(until_ms: int, zone: str) -> str:
    """The day a switch ends, as the banner says it: "Dec 1"."""
    at = datetime.fromtimestamp(until_ms / 1000, ZoneInfo(zone) if zone else None)
    return f"{at:%b} {at.day}"


def using_line(label: str, until_ms: int, zone: str) -> str:
    return f"Using {label} until {until_words(until_ms, zone)}"


# ── What is kept ─────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class Switch:
    leg: Leg
    # The target as the card named it: "local/gemma", "system", "whisper".
    to: str
    label: str
    # The month it is for ("2026-11"): it lapses once the month is another. `until_ms`
    # is when that is expected, for the banner only.
    month: str
    until_ms: int
    # What the runtime builds: a model's settings, or Whisper's model.
    settings: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: Any) -> Switch | None:
        if not isinstance(raw, dict):
            return None
        leg, to, label, until = (raw.get(k) for k in ("leg", "to", "label", "until_ms"))
        month, settings = raw.get("month"), raw.get("settings")
        if leg not in _STOP or not isinstance(to, str) or not isinstance(label, str):
            return None
        if isinstance(until, bool) or not isinstance(until, int) or not isinstance(month, str):
            return None
        if settings is not None and not isinstance(settings, dict):
            return None
        return cls(leg=leg, to=to, label=label, month=month, until_ms=until, settings=settings)


@dataclass(frozen=True, kw_only=True)
class Card:
    id: str
    conversation_id: str
    leg: Leg
    provider_id: str
    cap: float
    unit: str
    line: str
    month: str
    status: Status = "open"
    options: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_page(self) -> dict[str, Any]:
        """The `spend.card` message's body. A switch's settings stay in the core."""
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "leg": self.leg,
            "cap": self.cap,
            "unit": self.unit,
            "cap_text": cap_words(self.cap, self.unit),
            "line": self.line,
            "status": self.status,
            "options": [{k: v for k, v in o.items() if k != "settings"} for o in self.options],
        }

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["options"] = list(self.options)
        return out

    @classmethod
    def from_json(cls, raw: Any) -> Card | None:
        try:
            return cls(
                id=str(raw["id"]),
                conversation_id=str(raw["conversation_id"]),
                leg=raw["leg"],
                provider_id=str(raw["provider_id"]),
                cap=float(raw["cap"]),
                unit=str(raw["unit"]),
                line=str(raw["line"]),
                month=str(raw["month"]),
                status=raw["status"],
                options=tuple(o for o in raw["options"] if isinstance(o, dict)),
            )
        except (KeyError, TypeError, ValueError):
            return None


class Refused(Exception):
    """A card's choice refused. `code` is safe to show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Prefs(Protocol):
    async def get(self, owner_id: str, key: str) -> Any: ...

    async def put(self, owner_id: str, key: str, value: Any, now_ms: int) -> None: ...


# Writes one notice through the one door, under a key that is never used twice.
Notify = Callable[[str, str], Awaitable[None]]


# ── Host notes (PER-11): what she is told, so she says it in her own words ──


def soft_note(name: str, soft: str, spent: str) -> str:
    return (
        f"[host] Spending on {name} this month has passed the {soft} soft cap the user "
        f"set: {spent} so far. Nothing has stopped. Mention it once, briefly, in your own "
        "words, and carry on with what they asked."
    )


def back_note(label: str, usual: str) -> str:
    return (
        f"[host] The switch to {label} ended with the new month: you are back on {usual}. "
        "Say so once, briefly, in your own words."
    )


def caps_on_note(caps: Sequence[tuple[str, Caps]]) -> str:
    """The one-time note at the upgrade: caps are on, what they are, where to change
    them. `caps` is (provider id, caps) for each provider given them."""
    parts = []
    for pid, c in caps:
        soft = f"a {cap_words(c.soft, c.unit)} soft cap" if c.soft is not None else "no soft cap"
        hard = f"a {cap_words(c.hard, c.unit)} hard cap" if c.hard is not None else "no hard cap"
        parts.append(f"{friendly(pid)}: {soft} (a warning at {c.warn_pct}%) and {hard} a month")
    return (
        "[host] Spending caps are now on, so a runaway routine or a long day can't run up "
        f"the user's bill unnoticed. {'; '.join(parts)}. At the soft cap nothing stops; "
        "at the hard cap you stop before spending more and they choose what happens. They "
        "can change or remove these in Settings, under Usage. Tell them once, briefly, in "
        "your own words, and carry on with what they asked."
    )


def warning_title(name: str, spent: str, soft: str) -> str:
    return f"{name} has used {spent} of its {soft} soft cap this month"


def paused_title(name: str, cap: str) -> str:
    """The one notice that background work paused: every kind of it runs on her model,
    so all of it waits, whichever was refused first."""
    return f"Routines, watchers and channel replies are paused: {name} is at its {cap} cap"


# The kinds of background work, in the order `paused` lists them.
WORK: tuple[Work, ...] = ("routines", "asks", "watchers", "channels", "titles", "board")


# ── The desk ─────────────────────────────────────────────────────────────


class SpendDesk:
    def __init__(
        self,
        *,
        prefs: Prefs,
        gate: Gate,
        clock: Callable[[], int],
        zone: Callable[[], str],
        owner: str,
        live: Callable[[str], bool],
        notify: Notify | None = None,
    ) -> None:
        self._prefs = prefs
        self._gate = gate
        self._clock = clock
        self._zone = zone
        self._owner = owner
        # Whether a conversation's page is open now.
        self._live = live
        # Public so the runtime can set it once the door exists.
        self.notify = notify
        self._switches: dict[str, Switch] = {}
        self._cards: dict[str, Card] = {}
        self._told: dict[str, Any] = {}
        # The provider background work runs on, and the kinds of it set up now. Public
        # so the runtime can set it once the work exists.
        self.background: Callable[[], Awaitable[tuple[str | None, frozenset[Work]]]] | None = None
        # Told after a choice changed a card, and after a switch began or lapsed: the
        # cards changed, and the legs whose switch changed.
        self.changed: Callable[[Sequence[Card], frozenset[Leg]], Awaitable[None]] | None = None
        # One choice at a time: a double tap is answered once.
        self._choosing = asyncio.Lock()

    def month(self) -> str:
        return month_start(self._clock(), self._zone())[0]

    async def load(self) -> None:
        raw = await self._prefs.get(self._owner, SWITCH_PREF)
        found = raw.items() if isinstance(raw, dict) else ()
        self._switches = {
            pid: s for pid, v in found if isinstance(pid, str) and (s := Switch.from_json(v))
        }
        raw = await self._prefs.get(self._owner, CARDS_PREF)
        cards = (Card.from_json(v) for v in raw.values()) if isinstance(raw, dict) else ()
        self._cards = {c.id: c for c in cards if c is not None}
        raw = await self._prefs.get(self._owner, TOLD_PREF)
        self._told = raw if isinstance(raw, dict) else {}

    # ── Switches ──

    def switch(self, provider_id: str | None) -> Switch | None:
        """The switch in force for a provider now, if any."""
        if provider_id is None:
            return None
        s = self._switches.get(provider_id)
        return s if s is not None and s.month == self.month() else None

    def banner(self) -> list[dict[str, Any]]:
        """The `spend.switch` message's switches: each says what is used, and until when."""
        zone = self._zone()
        return [
            {
                "provider_id": pid,
                "leg": s.leg,
                "to": s.to,
                "label": s.label,
                "until_ms": s.until_ms,
                "line": using_line(s.label, s.until_ms, zone),
            }
            for pid, s in sorted(self._switches.items())
            if self.switch(pid) is not None
        ]

    async def lapse(self, usual: Callable[[str, Switch], str]) -> bool:
        """End every switch made for another month, and keep a note for her to say she
        is back on `usual`. True when one ended."""
        month = self.month()
        ended = {pid: s for pid, s in self._switches.items() if s.month != month}
        if not ended:
            return False
        for pid in ended:
            del self._switches[pid]
        back = dict(self._told.get("back") or {})
        for pid, s in ended.items():
            back[pid] = back_note(s.label, usual(pid, s))
        self._told = {**self._told, "back": back}
        await self._save_switches()
        await self._save_told()
        if self.changed is not None:
            await self.changed((), frozenset(s.leg for s in ended.values()))
        return True

    async def _save_switches(self) -> None:
        value = {pid: s.to_json() for pid, s in self._switches.items()}
        await self._prefs.put(self._owner, SWITCH_PREF, value, self._clock())

    async def _save_told(self) -> None:
        await self._prefs.put(self._owner, TOLD_PREF, self._told, self._clock())

    # ── Cards ──

    def _shown(self, c: Card) -> bool:
        return (
            c.status in _STANDING
            and c.month == self.month()
            and self._gate.state(c.provider_id) == "capped"
        )

    def cards_for(self, conversation_id: str) -> list[Card]:
        """The cards a page on this conversation shows: those still to be answered."""
        return [
            c
            for c in self._cards.values()
            if c.conversation_id == conversation_id and self._shown(c)
        ]

    def standing(self, conversation_id: str, provider_id: str) -> Card | None:
        """This conversation's card for a capped provider, if it has one still shown."""
        return next(
            (c for c in self.cards_for(conversation_id) if c.provider_id == provider_id), None
        )

    async def card(
        self,
        conversation_id: str,
        leg: Leg,
        provider_id: str,
        line: str,
        options: Iterable[dict[str, Any]],
    ) -> Card:
        """The card for this cap here: the one already shown, with today's choices, or
        a new one."""
        caps = self._gate.caps(provider_id)
        cap = float(caps.hard or 0.0)
        found = self.standing(conversation_id, provider_id)
        chosen = tuple(options)
        if found is not None:
            card = replace(found, options=chosen, line=line, cap=cap)
        else:
            card = Card(
                id=uuid.uuid4().hex,
                conversation_id=conversation_id,
                leg=leg,
                provider_id=provider_id,
                cap=cap,
                unit=caps.unit,
                line=line,
                month=self.month(),
                options=chosen,
            )
        self._cards[card.id] = card
        await self._save_cards()
        return card

    def raise_option(self, provider_id: str) -> dict[str, Any]:
        caps = self._gate.caps(provider_id)
        hard = float(caps.hard or 0.0)
        amount = max(
            next_step(hard, caps.unit), next_step(self._gate.counted(provider_id), caps.unit)
        )
        return {"choice": "raise", "amount": amount, "unit": caps.unit}

    def until(self) -> int:
        return next_month_start(self._clock(), self._zone())

    def switch_option(
        self,
        to: str,
        label: str,
        settings: dict[str, Any] | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        """`target` is the provider id switched to, when it can be capped."""
        until = self.until()
        out: dict[str, Any] = {
            "choice": f"switch:{to}",
            "label": label,
            "until_ms": until,
            "until": until_words(until, self._zone()),
        }
        if settings is not None:
            out["settings"] = settings
        if target is not None:
            out["target"] = target
        return out

    @staticmethod
    def stop_option(leg: Leg) -> dict[str, Any]:
        return {"choice": _STOP[leg]}

    async def _save_cards(self) -> None:
        month = self.month()
        # Only this month's: an older card can no longer be answered.
        self._cards = {k: c for k, c in self._cards.items() if c.month == month}
        value = {k: c.to_json() for k, c in self._cards.items()}
        await self._prefs.put(self._owner, CARDS_PREF, value, self._clock())

    async def choose(
        self,
        card_id: str,
        choice: str,
        amount: float | None,
        session_id: str,
        via: str,
        options: Sequence[dict[str, Any]] | None = None,
    ) -> Card:
        """A tap on a card. Refused unless it comes by a tap, from the page the card was
        sent to, for a choice the card offered and still offers (`options`, made fresh
        by the caller), while the card is still shown. One at a time."""
        async with self._choosing:
            return await self._choose(card_id, choice, amount, session_id, via, options)

    async def _choose(
        self,
        card_id: str,
        choice: str,
        amount: float | None,
        session_id: str,
        via: str,
        options: Sequence[dict[str, Any]] | None,
    ) -> Card:
        card = self._cards.get(card_id)
        if card is None:
            raise Refused("unknown_card")
        if session_id != card.conversation_id or not self._live(session_id):
            raise Refused("wrong_session")
        if via != "tap":
            # Money is only ever moved by a tap on the owner's own screen (SPN-14).
            raise Refused("not_a_tap")
        if not self._shown(card):
            raise Refused("closed")
        offered = next((o for o in card.options if o.get("choice") == choice), None)
        if offered is None:
            raise Refused("unknown_choice")
        target = offered.get("target")
        if isinstance(target, str) and self._gate.state(target) == "capped":
            # It had budget when the card was shown, and has none now.
            raise Refused("target_capped")
        current = tuple(options) if options is not None else card.options
        option = next((o for o in current if o.get("choice") == choice), None)
        if option is None:
            raise Refused("unknown_choice")
        pid = card.provider_id
        status: Status
        legs: frozenset[Leg] = frozenset()
        if choice == "raise":
            await self._raise(pid, option.get("amount") if amount is None else amount)
            status = "raised"
        elif choice.startswith("switch:"):
            until = option.get("until_ms")
            self._switches[pid] = Switch(
                leg=card.leg,
                to=choice.removeprefix("switch:"),
                label=str(option.get("label") or ""),
                month=self.month(),
                until_ms=until if isinstance(until, int) else self.until(),
                settings=option.get("settings"),
            )
            await self._save_switches()
            status = "switched"
            legs = frozenset({card.leg})
        else:
            status = "stopped"
        changed = [replace(card, status=status, options=current)]
        if status != "stopped":
            # Every card for this cap is answered by it, in any conversation.
            changed += [
                replace(c, status=status)
                for c in self._cards.values()
                if c.provider_id == pid and c.id != card.id and c.status in _STANDING
            ]
        for c in changed:
            self._cards[c.id] = c
        await self._save_cards()
        if self.changed is not None:
            await self.changed(changed, legs)
        return changed[0]

    def most(self, unit: str) -> float:
        """The highest a raise may go: $10,000, or 100 times the unit's default."""
        return MOST.get(unit, MOST["dollars"])

    async def _raise(self, provider_id: str, amount: Any) -> None:
        caps = self._gate.caps(provider_id)
        if (
            amount is None
            or isinstance(amount, bool)
            or not isinstance(amount, int | float)
            or not math.isfinite(amount)
            or amount < 0
            or amount <= self._gate.counted(provider_id)
            or (caps.hard is not None and amount < caps.hard)
            or amount > self.most(caps.unit)
        ):
            raise Refused("bad_amount")
        raw = await self._prefs.get(self._owner, PREF)
        all_caps = dict(raw) if isinstance(raw, dict) else {}
        soft = caps.soft if caps.soft is None or caps.soft <= amount else float(amount)
        all_caps[provider_id] = {
            "soft": soft,
            "hard": float(amount),
            "warn_pct": caps.warn_pct,
            "unit": caps.unit,
        }
        await self._prefs.put(self._owner, PREF, all_caps, self._clock())
        await self._gate.reload_caps()

    # ── Once a month ──

    def _told_in(self, what: str) -> Mapping[str, Any]:
        found = self._told.get(what)
        return found if isinstance(found, dict) else {}

    def notes(self, provider_ids: Iterable[str | None]) -> list[tuple[str, str]]:
        """The host notes her next turn carries, as (key, text): a soft cap passed this
        month and not yet mentioned, a switch that lapsed, and each note kept by
        `tell_once`. Marked said by `said`."""
        month = self.month()
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        mentioned = self._told_in("over_soft")
        for pid in provider_ids:
            if pid is None or pid in seen:
                continue
            seen.add(pid)
            if mentioned.get(pid) == month or self._gate.state(pid) != "over_soft":
                continue
            caps = self._gate.caps(pid)
            if caps.soft is None:
                continue
            spent = amount_words(self._gate.counted(pid), caps.unit)
            note = soft_note(friendly(pid), cap_words(caps.soft, caps.unit), spent)
            out.append((f"over_soft:{pid}", note))
        for pid, note in sorted(self._told_in("back").items()):
            if isinstance(note, str):
                out.append((f"back:{pid}", note))
        for name, note in sorted(self._told_in("once").items()):
            if isinstance(note, str):
                out.append((f"once:{name}", note))
        return out

    async def tell_once(self, name: str, note: str) -> None:
        """Keep a host note for her next turn, in any conversation, said once (PER-11).
        A note already kept under `name` is replaced."""
        once = dict(self._told_in("once"))
        once[name] = note
        self._told = {**self._told, "once": once}
        await self._save_told()

    async def said(self, keys: Iterable[str]) -> None:
        """The notes under `keys` rode with a saved message: never again."""
        keys = list(keys)
        if not keys:
            return
        month = self.month()
        mentioned = dict(self._told_in("over_soft"))
        back = dict(self._told_in("back"))
        once = dict(self._told_in("once"))
        for key in keys:
            what, _, pid = key.partition(":")
            if what == "over_soft":
                mentioned[pid] = month
            elif what == "back":
                back.pop(pid, None)
            elif what == "once":
                once.pop(pid, None)
        self._told = {**self._told, "over_soft": mentioned, "back": back, "once": once}
        await self._save_told()

    async def warn(self, provider_ids: Iterable[str | None]) -> None:
        """One notice through the one door the first time a provider reaches its
        warning this month (SUR-9). Shown, not spoken: it has no words of hers."""
        if self.notify is None:
            return
        month = self.month()
        warned = dict(self._told_in("warned"))
        due: list[str] = []
        for pid in dict.fromkeys(p for p in provider_ids if p is not None):
            if warned.get(pid) == month or self._gate.state(pid) == "ok":
                continue
            caps = self._gate.caps(pid)
            if caps.soft is None:
                continue
            due.append(pid)
            warned[pid] = month
        if not due:
            return
        # Recorded first: a notice that fails to write is not tried every turn.
        self._told = {**self._told, "warned": warned}
        await self._save_told()
        for pid in due:
            caps = self._gate.caps(pid)
            title = warning_title(
                friendly(pid),
                amount_words(self._gate.counted(pid), caps.unit),
                cap_words(float(caps.soft or 0.0), caps.unit),
            )
            try:
                await self.notify(f"spend:warned:{pid}:{month}", title)
            except Exception as e:
                log.warning("spend warning not given: %s", type(e).__name__)

    # ── Background work at the hard cap ──

    async def held(self, work: Work, provider_id: str | None) -> bool:
        """Before background work starts: True when its provider is at its hard cap,
        recorded as paused (`pause`), so the work is not begun. Never raises."""
        if provider_id is None:
            return False
        try:
            await self._gate.refresh()
            capped = self._gate.state(provider_id) == "capped"
        except Exception as e:
            # Not known: the gate still refuses the call itself if it must.
            log.warning("spend state not read: %s", type(e).__name__)
            return False
        if capped:
            await self.pause(provider_id, work)
        return capped

    async def pause(self, provider_id: str, work: Work) -> None:
        """`work` was refused at this provider's hard cap. The first time this month,
        one notice through the one door (SUR-9), shown, not spoken. Never raises."""
        month = self.month()
        told = dict(self._told_in("paused"))
        if told.get(provider_id) == month:
            return
        told[provider_id] = month
        # Recorded first, with no await since the check: two kinds refused at once
        # give one notice, and one that fails to write is not tried again.
        self._told = {**self._told, "paused": told}
        log.info("background work paused at a spend cap (first: %s)", work)
        try:
            await self._save_told()
            caps = self._gate.caps(provider_id)
            title = paused_title(friendly(provider_id), cap_words(float(caps.hard or 0), caps.unit))
            if self.notify is not None:
                await self.notify(f"spend:paused:{provider_id}:{month}", title)
        except Exception as e:
            log.warning("spend pause notice not given: %s", type(e).__name__)

    async def paused(self, provider_id: str) -> list[Work]:
        """The kinds of background work paused now at this provider's hard cap: those
        set up, when they run on this provider and it is capped this month. Empty once
        the cap is raised or the month turns. In `WORK`'s order."""
        if self.background is None:
            return []
        try:
            await self._gate.refresh()
            if self._gate.state(provider_id) != "capped":
                return []
            usual, kinds = await self.background()
        except Exception as e:
            log.warning("paused work not read: %s", type(e).__name__)
            return []
        if usual != provider_id:
            return []
        return [w for w in WORK if w in kinds]
