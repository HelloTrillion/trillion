"""The widget kit's samples: one of each widget as she would write it, and one card per
block (the widget design system spec, "Style guide").

Pure data. `scripts/gen_kit_samples.py` checks and expands these into
`apps/web/src/kit/samples.json`, which the `#kit` style guide draws, so the guide can
only show what the kit accepts. They are also the look she is taught: titles that say
what they show, numbers with units and a comparison, a source wherever a connector
would have supplied the facts, and an ask only where the next question is obvious.

Generic names only. Times without an offset are the user's local time.
"""

from __future__ import annotations

from typing import Any

Sample = dict[str, Any]

SAMPLES: list[Sample] = [
    {
        "type": "stat",
        "title": "October spend",
        "label": "Spent so far",
        "value": "$3,840",
        "delta": "+12% vs September",
        "hint": "With 6 days to go, on course for about $4,500",
        "tone": "attention",
        "source": "Bank",
        "asks": ["Where did the extra go?"],
    },
    {
        "type": "chart",
        "title": "Spend by week, October",
        "kind": "bar",
        "x": ["Sep 28", "Oct 5", "Oct 12", "Oct 19"],
        "series": [
            {"name": "This year", "values": [910, 760, 1240, 930]},
            {"name": "Last year", "values": [820, 790, 880, 850]},
        ],
        "unit": "$",
        "source": "Bank",
    },
    {
        "type": "list",
        "title": "Before the trip to Maple Park",
        "items": [
            {"title": "Book the train", "subtitle": "Friday 8:10 am, two seats", "done": True},
            {"title": "Ask Jo to water the plants", "meta": "Thu"},
            {"title": "Pick up the rain jackets", "subtitle": "From the Elm Street office"},
            {"title": "Print the cabin booking", "meta": "Fri"},
        ],
    },
    {
        "type": "table",
        "title": "Unpaid invoices",
        "columns": ["Client", "Amount", "Due", "Days late"],
        "rows": [
            ["Maple Park Dental", "$1,200", "Oct 10", 15],
            ["Alex Rivera Design", "$850", "Oct 18", 7],
            ["Elm Street Bakery", "$420", "Oct 31", 0],
        ],
        "source": "Invoices",
        "asks": ["Draft a reminder to Maple Park"],
    },
    {
        "type": "note",
        "title": "What we agreed with Sam",
        "text": (
            "Sam takes the **client call** on Tuesdays from now on.\n"
            "- Jo writes the weekly summary\n"
            "- Alex owns the budget sheet\n"
            "Review it all again on Nov 30."
        ),
    },
    {
        "type": "timeline",
        "title": "Monday, Oct 26",
        "events": [
            {"title": "Standup", "start": "09:00", "end": "09:15"},
            {"title": "Design review with Sam", "start": "10:30", "end": "11:30", "meta": "Room 2"},
            {"title": "Lunch with Jo", "start": "12:30", "end": "13:30", "meta": "Maple Park"},
            {"title": "Budget call", "start": "15:00", "end": "15:30"},
        ],
        "source": "Calendar",
    },
    {
        "type": "compare",
        "title": "Two flats near Maple Park",
        "options": [
            {
                "name": "Elm Street, 2 bed",
                "metrics": [
                    {"label": "Price", "score": 6},
                    {"label": "Light", "score": 9},
                    {"label": "Commute", "score": 7},
                ],
                "tags": ["$1,950 a month", "Top floor"],
                "note": "No lift, but a balcony.",
            },
            {
                "name": "Park Road, 2 bed",
                "metrics": [
                    {"label": "Price", "score": 8},
                    {"label": "Light", "score": 5},
                    {"label": "Commute", "score": 9},
                ],
                "tags": ["$1,780 a month", "Ground floor"],
                "note": "Six minutes to the station.",
            },
        ],
        "pick": "Park Road, 2 bed",
        "reason": "It is $170 a month cheaper and the shorter commute matters every day.",
        "asks": ["Book a viewing"],
    },
    {
        "type": "next_up",
        "title": "Next up",
        "event": "Design review",
        "start": "2026-10-26T10:30",
        "end": "2026-10-26T11:30",
        "where": "Maple Park room 2",
        "people": ["Sam", "Jo", "Alex"],
        "note": "Sam wants a decision on the new header.",
        "source": "Calendar",
        "asks": ["Prep me for this"],
    },
    {
        "type": "free_time",
        "title": "Free for an hour or more this week",
        "slots": [
            {"start": "2026-10-27T09:00", "end": "2026-10-27T10:30"},
            {"start": "2026-10-27T14:00", "end": "2026-10-27T15:00"},
            {"start": "2026-10-28T11:00", "end": "2026-10-28T12:30"},
            {"start": "2026-10-30T13:00", "end": "2026-10-30T17:00"},
        ],
        "source": "Calendar",
        "asks": ["Hold Friday afternoon"],
    },
    {
        "type": "inbox",
        "title": "Email since this morning",
        "unread": 14,
        "needs_reply": 3,
        "threads": [
            {
                "from": "Sam",
                "subject": "Header options for Monday",
                "when": "2026-10-25T08:12",
                "flag": "attention",
            },
            {
                "from": "Jo",
                "subject": "Invoice 214 is overdue",
                "when": "2026-10-25T07:40",
                "flag": "bad",
            },
            {"from": "Alex", "subject": "Slides for Thursday", "when": "2026-10-24T18:05"},
        ],
        "source": "Email",
    },
    {
        "type": "thread",
        "title": "Sam needs an answer",
        "from": "Sam",
        "subject": "Header options for Monday",
        "request": "Pick one of the two header designs so the build can start Monday.",
        "due": "2026-10-26",
        "when": "2026-10-25T08:12",
        "excerpt": (
            "Both are in the shared folder. I lean towards B, but it needs a new photo"
            " and that adds a day."
        ),
        "source": "Email",
        "asks": ["Draft a reply"],
    },
    {
        "type": "mentions",
        "title": "Mentions since Friday",
        "groups": [
            {
                "channel": "#launch",
                "items": [
                    {
                        "from": "Jo",
                        "text": "Can you sign off the launch post by Tuesday?",
                        "when": "2026-10-24T16:20",
                    },
                    {
                        "from": "Alex",
                        "text": "Numbers for the Elm Street office are in the sheet.",
                        "when": "2026-10-24T17:02",
                    },
                ],
            },
            {
                "channel": "#design",
                "items": [
                    {
                        "from": "Sam",
                        "text": "Header B needs your call before Monday.",
                        "when": "2026-10-25T08:15",
                    }
                ],
            },
        ],
        "source": "Chat",
    },
    {
        "type": "files",
        "title": "Edited this week",
        "files": [
            {"name": "Q4 budget", "kind": "Sheet", "edited": "2026-10-24T17:00", "by": "Alex"},
            {
                "name": "Launch post draft",
                "kind": "Doc",
                "edited": "2026-10-24T11:30",
                "by": "Jo",
            },
            {"name": "Header options", "kind": "Slides", "edited": "2026-10-23T15:45", "by": "Sam"},
        ],
        "source": "Drive",
    },
    {
        "type": "quote",
        "title": "What the client said",
        "text": "The new checkout is the first one our customers have praised without being asked.",
        "by": "Maple Park Dental",
        "when": "2026-10-22",
        "source": "Email",
    },
    {
        "type": "plan",
        "title": "Move to the Elm Street office",
        "steps": [
            {"title": "Sign the lease", "done": True},
            {"title": "Book the movers", "done": True, "detail": "Nov 14, 8:00 am"},
            {"title": "Tell the bank the new address"},
            {"title": "Set up the internet", "detail": "Needs someone in on Nov 16"},
            {"title": "Update the website footer"},
        ],
        "asks": ["Who is free on Nov 16?"],
    },
    {
        "type": "decision",
        "title": "Which venue for the team day",
        "options": ["Maple Park Hall", "Elm Street loft", "The Boathouse"],
        "criteria": ["Price", "Space", "Getting there"],
        "scores": [[7, 8, 9], [9, 6, 7], [5, 9, 6]],
        "pick": "Maple Park Hall",
        "reason": "Best total, and everyone can walk there from the station.",
    },
    {
        "type": "facts",
        "title": "Flight to the conference",
        "items": [
            {"label": "Departs", "value": "Thu Nov 19, 7:45 am"},
            {"label": "Flight", "value": "TR 214"},
            {"label": "Seat", "value": "14C, aisle"},
            {"label": "Booking", "value": "K7Q2LM"},
        ],
        "source": "Email",
    },
    {
        "type": "callout",
        "title": "Rent goes up in December",
        "text": (
            "From Dec 1 the Elm Street rent is $2,050, up $100."
            " The current direct debit will fall short."
        ),
        "tone": "attention",
        "source": "Email",
        "asks": ["Update the direct debit"],
    },
    {
        "type": "goal",
        "title": "Holiday fund",
        "label": "Saved",
        "value": 4200,
        "target": 6000,
        "unit": "$",
        "by": "2026-11-30",
        "source": "Bank",
    },
    {
        "type": "sources",
        "title": "Where the venue figures came from",
        "items": [
            {
                "title": "Maple Park Hall prices",
                "site": "maplepark.example",
                "note": "Weekday rate, up to 40 people",
            },
            {"title": "Elm Street loft booking", "site": "elmstreet.example"},
            {
                "title": "The Boathouse events",
                "site": "boathouse.example",
                "note": "Prices from 2025, may be out of date",
            },
        ],
    },
    {
        "type": "custom",
        "title": "Who's on call this week",
        "blocks": [
            {
                "type": "rows",
                "items": [
                    {
                        "title": "Sam",
                        "person": "Sam",
                        "detail": "Mon to Wed, until 6:00 pm",
                        "chip": "Primary",
                    },
                    {"title": "Jo", "person": "Jo", "detail": "Thu and Fri", "chip": "Primary"},
                    {"title": "Alex", "person": "Alex", "detail": "All week", "chip": "Backup"},
                ],
            },
            {"type": "status", "tone": "good", "text": "No open incidents"},
        ],
        "source": "Rota",
    },
    {
        "type": "custom",
        "title": "Two offices, October",
        "blocks": [
            {
                "type": "columns",
                "columns": [
                    [
                        {"type": "value", "value": "$12,400", "label": "Elm Street office"},
                        {"type": "sparkline", "values": [9800, 10400, 11100, 12400]},
                        {"type": "facts", "items": [{"label": "vs September", "value": "+12%"}]},
                    ],
                    [
                        {"type": "value", "value": "$9,100", "label": "Maple Park"},
                        {"type": "sparkline", "values": [9900, 9600, 9300, 9100], "tone": "bad"},
                        {"type": "facts", "items": [{"label": "vs September", "value": "-2%"}]},
                    ],
                ],
            },
            {
                "type": "text",
                "text": "Elm Street has grown four months running; Maple Park has slipped.",
            },
        ],
        "source": "Sales",
        "asks": ["Why is Maple Park down?"],
    },
]

# One card per block, titled with the block's name: the style guide's block section.
_BLOCKS: list[Sample] = [
    {
        "type": "value",
        "value": "$3,840",
        "label": "Spent in October",
        "delta": "+12%",
        "tone": "attention",
    },
    {"type": "sparkline", "values": [12, 14, 13, 17, 16, 19, 22]},
    {"type": "meter", "value": 3, "max": 5, "label": "3 of 5 done"},
    {"type": "time", "at": "2026-10-26T10:30", "end": "2026-10-26T11:30", "label": "Design review"},
    {
        "type": "chart",
        "kind": "line",
        "x": ["Jul", "Aug", "Sep", "Oct"],
        "series": [{"name": "Visitors", "values": [1200, 1350, 1310, 1620]}],
    },
    {
        "type": "table",
        "columns": ["Office", "October"],
        "rows": [["Elm Street", "$12,400"], ["Maple Park", "$9,100"]],
        "highlight": 0,
    },
    {
        "type": "timeline",
        "events": [
            {"title": "Standup", "start": "09:00", "end": "09:15"},
            {"title": "Lunch with Jo", "start": "12:30", "end": "13:30"},
        ],
    },
    {
        "type": "rows",
        "items": [
            {
                "title": "Sam",
                "person": "Sam",
                "detail": "Header options",
                "when": "2026-10-25T08:12",
            },
            {"title": "Book the movers", "status": "good", "meta": "Nov 14"},
            {"title": "Rain jackets", "chip": "Elm Street"},
        ],
    },
    {"type": "person", "name": "Sam", "detail": "Design lead"},
    {"type": "people", "names": ["Sam", "Jo", "Alex", "Maple Park team"]},
    {"type": "status", "tone": "attention", "text": "Two invoices are late"},
    {"type": "chips", "items": ["Top floor", "Balcony", "$1,950 a month"]},
    {"type": "text", "text": "Sam takes the **client call** on Tuesdays.\n- Jo writes the summary"},
    {"type": "quote", "text": "Best checkout we have used.", "by": "Maple Park Dental"},
    {
        "type": "facts",
        "items": [{"label": "Seat", "value": "14C"}, {"label": "Flight", "value": "TR 214"}],
    },
    {
        "type": "columns",
        "columns": [
            [{"type": "value", "value": "$12,400", "label": "Elm Street office"}],
            [{"type": "value", "value": "$9,100", "label": "Maple Park"}],
        ],
    },
]

BLOCK_SAMPLES: list[Sample] = [
    {"type": "custom", "title": block["type"], "blocks": [block]} for block in _BLOCKS
]
