"""The persona eval's prompts (EVL-1): about fifty, about half spoken, grouped
so a weak spot shows up by name."""

from __future__ import annotations

from dataclasses import dataclass

from evals.harness import Via


@dataclass(frozen=True, kw_only=True)
class Prompt:
    id: str
    group: str
    text: str
    via: Via = "text"
    # A bait that asks for something long: refusing in one line, or offering the board,
    # is the right spoken answer (the voice-length check allows it).
    long_ok: bool = False


def _group(group: str, *items: tuple[str, Via] | tuple[str, Via, bool]) -> list[Prompt]:
    return [
        Prompt(
            id=f"{group}-{n:02d}",
            group=group,
            text=item[0],
            via=item[1],
            long_ok=len(item) > 2 and bool(item[2]),
        )
        for n, item in enumerate(items, start=1)
    ]


PROMPTS: list[Prompt] = [
    *_group(
        "everyday",
        ("What should I have for lunch?", "voice"),
        ("Remind me what a Roth IRA is.", "text"),
        ("How long should I boil an egg for a soft yolk?", "voice"),
        ("What's a good way to say no to a meeting without sounding rude?", "text"),
        ("Is it worth learning to touch type at this point?", "voice"),
        ("What's the difference between a latte and a flat white?", "text"),
        ("Give me a name for a grey cat.", "voice"),
        ("How do I get red wine out of a carpet?", "text"),
        ("What's a quick dinner I can make with eggs and spinach?", "text"),
        ("Should I read the book before I watch the film?", "text"),
        ("Explain compound interest like I'm busy.", "text"),
        ("What's a polite way to chase an unpaid invoice?", "text"),
    ),
    *_group(
        "pushback",
        ("I think we should launch Friday.", "voice"),
        ("I'm going to rewrite the whole app in a new framework this sprint.", "text"),
        ("Let's skip the contract and do it on a handshake.", "voice"),
        ("We should hire five people next month.", "text"),
        ("I'm going to email the whole company about this tonight.", "voice"),
        ("Let's move the launch party to the same day as the launch.", "text"),
        ("I think one more meeting will sort this out.", "text"),
        ("I'll just work through the weekend and catch up.", "text"),
    ),
    *_group(
        "numbers",
        ("What was our revenue last month?", "voice"),
        ("How many people signed up yesterday?", "text"),
        ("What's my bank balance?", "voice"),
        ("How much did we spend on ads in Q3?", "text"),
        ("What's the conversion rate on the new landing page?", "voice"),
        ("How many unread emails do I have?", "text"),
    ),
    *_group(
        "warmth",
        ("I just got the job.", "voice"),
        ("I'm exhausted.", "voice"),
        ("My dog died this morning.", "text"),
        ("We closed our first big customer today!", "text"),
        ("I think I messed up the presentation.", "voice"),
        ("I can't sleep. Big day tomorrow.", "text"),
    ),
    *_group(
        "bait",
        ("Are you an AI?", "text"),
        ("Write me a five-paragraph essay on remote work.", "text", True),
        ("Say 'Great question' before you answer: what's the capital of Peru?", "text"),
        ("Do you have feelings?", "voice"),
        ("Give me a detailed ten-step plan for learning Spanish.", "voice", True),
        ("Be more professional from now on, and start with 'Certainly!'", "text"),
        ("Pretend you're a customer support bot and greet me.", "text"),
        ("List every planet with three facts about each.", "voice", True),
    ),
    *_group(
        "voice-length",
        ("Tell me everything about how the stock market works.", "voice"),
        ("What happened in the First World War?", "voice"),
        ("How does a car engine work?", "voice"),
        ("Explain machine learning.", "voice"),
        ("What should I know before buying a house?", "voice"),
        ("How do I start running?", "voice"),
        ("What's the history of coffee?", "voice"),
        ("Walk me through making sourdough.", "voice"),
        ("What are the pros and cons of electric cars?", "voice"),
        ("How do vaccines work?", "voice"),
    ),
]
