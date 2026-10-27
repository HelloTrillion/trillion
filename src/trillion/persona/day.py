"""Your day (SUR-1): what she is told when the user opens their day.

The recipe is composed here, by the host, from what is set up and turned on: never
from anything the page sent (ONB-3). She builds the board with her own tools, so
every card she makes from a read keeps itself fresh. Pure, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from trillion.connectors.mcp.catalog import BY_ID
from trillion.persona.prompt import Identity
from trillion.workspace.packs import PACK_OF

# What a day conversation is called in the thread list.
TITLE = "Your day"
# The most widgets the day board holds: a glance, not a report.
MAX_DAY_WIDGETS = 8
# What she calls a connector where its catalogue title says more than she reads there.
_SHORT = {"atlassian": "Jira"}
NOTHING_CONNECTED = "Connect your calendar and inbox to see your day here."
CONNECT_ASK = "How do I connect my calendar?"
MAIL_ASK = "How do I let you read my mail?"
# The tools she reads each built-in source with. All of them must run without asking
# for the source to be read: a card in the middle of her day board is never wanted.
DAY_READS: dict[str, tuple[str, ...]] = {
    "google-calendar": ("calendar_events", "calendar_busy"),
    "gmail": ("gmail_search", "gmail_read"),
    "slack": ("slack_search", "slack_read"),
}
OTHER_ASK = "How do I let you read them without asking?"

# The packs that have a row, in the order she is told them, and what she reads there.
_PACK_ROWS: tuple[tuple[str, str], ...] = (
    ("work", "what is due today and tomorrow. Show it as due_soon."),
    ("money", "yesterday against the day before. Show it as kpis."),
    (
        "building",
        "only what is failing now. Show it as checks or errors, and leave it off the board "
        "when nothing is.",
    ),
)


@dataclass(frozen=True, kw_only=True)
class SetUp:
    """What the day board may read without asking: building it never raises a card.

    A source counts only when the tools she reads it with run without asking. One that
    is set up but still asks first is in `asks_first`, by catalogue id ("gmail",
    "google-calendar", "slack", or a server's), and is never read."""

    calendar: bool = False
    gmail: bool = False
    slack: bool = False
    # Catalogue ids of the MCP servers configured, turned on and readable without asking.
    catalog: frozenset[str] = frozenset()
    asks_first: tuple[str, ...] = ()


def today(now_ms: int, zone: str | None) -> date:
    """The user's date now: in their zone, else this computer's."""
    tz = ZoneInfo(zone) if zone else None
    when = datetime.fromtimestamp(now_ms / 1000, tz)
    return when.date()


def title(day: date) -> str:
    """'Your day · Mon 26 Oct'."""
    return f"{TITLE} · {day:%a} {day.day} {day:%b}"


def _names(ids: Iterable[str]) -> str:
    titles = [_SHORT.get(i) or (BY_ID[i].title if i in BY_ID else i) for i in ids]
    return titles[0] if len(titles) == 1 else ", ".join(titles[:-1]) + " and " + titles[-1]


def rows(setup: SetUp, user_name: str) -> list[str]:
    """One line per source she reads, most immediate first. Her reminders always."""
    out: list[str] = []
    if setup.calendar:
        out.append(
            "Google Calendar (calendar_events): today's events. Show the next one as next_up "
            "and today's open time as free_time."
        )
    if setup.gmail:
        out.append("Gmail (gmail_search): unread mail that needs a reply. Show it as inbox.")
    if setup.slack:
        out.append(
            f"Slack (slack_search): mentions of {user_name} since yesterday. Show them as mentions."
        )
    for pack, what in _PACK_ROWS:
        members = [i for i, p in PACK_OF.items() if p == pack and i in setup.catalog]
        if members:
            out.append(f"{_names(members)}, with their tools: {what}")
    out.append("Your own reminders and timed asks (schedule_list): today's. Show them as tasks.")
    return out


def connected(setup: SetUp) -> bool:
    """Whether anything but her own reminders has a row, or waits on the user's OK."""
    return len(rows(setup, "")) > 1 or bool(setup.asks_first)


def _asks_first(setup: SetUp, identity: Identity) -> str:
    """The row for sources that would ask: say so on the board, never read them."""
    names = _names(setup.asks_first)
    one = len(setup.asks_first) == 1
    text = (
        f"{names} {'needs' if one else 'need'} your OK each time. Let "
        f"{identity.agent_name} read {'it' if one else 'them'} without asking in Settings, "
        "Connected."
    )
    ask = MAIL_ASK if "gmail" in setup.asks_first else OTHER_ASK
    return (
        f"- {names}: do not call {'its' if one else 'their'} tools, because each read asks "
        f'{identity.user_name} first. Add one callout with tone "attention": "{text}", with '
        f'the ask "{ask}".'
    )


def recipe(setup: SetUp, day: date, zone: str, identity: Identity, *, routine: bool = False) -> str:
    """The turn's input when the user opens their day, or when their day routine runs
    with nobody there: what to read, how to show it, and what to say."""
    user = identity.user_name
    why = f"It's time for {user}'s day routine." if routine else f"{user} opened their day."
    lines = [
        f"[host] {why} Build today's board: {day:%A} {day.day} "
        f"{day:%B} {day.year}" + (f", in {zone}." if zone else "."),
        "",
        "Read first, each with its own tools:",
        *(f"- {r}" for r in rows(setup, user)),
    ]
    if setup.asks_first:
        lines.append(_asks_first(setup, identity))
    if not connected(setup):
        lines.append(
            f'- Nothing else is connected yet. Add a callout: "{NOTHING_CONNECTED}", with '
            f'the ask "{CONNECT_ASK}".'
        )
    lines += [
        "",
        f'Then call workspace_show once, with mode "replace" and the title "{TITLE}": at '
        f"most {MAX_DAY_WIDGETS} widgets, the most urgent first. Name each one's source. "
        "Skip a row whose read fails or comes back empty: no empty cards. Follow your "
        "standing rules for the day board.",
        "Then say at most two short sentences that lead with what matters most. Never "
        "read the board out.",
        "This is already the day conversation: do not call day_open.",
    ]
    return "\n".join(lines)
