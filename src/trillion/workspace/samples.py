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
    # Core, and then the packs: what a connector's numbers look like on her board.
    {
        "type": "kpis",
        "title": "October so far",
        "metrics": [
            {
                "label": "Revenue",
                "value": "$48,200",
                "delta": "+8% vs September",
                "tone": "good",
                "trend": [9800, 11200, 12900, 14300],
            },
            {
                "label": "New customers",
                "value": "37",
                "delta": "+5",
                "tone": "good",
                "trend": [7, 9, 10, 11],
            },
            {
                "label": "Churn",
                "value": "2.1%",
                "delta": "+0.4 pts",
                "tone": "bad",
                "trend": [1.6, 1.7, 1.9, 2.1],
            },
            {"label": "Refunds", "value": "$640", "delta": "3 this month"},
        ],
        "source": "Stripe",
        "asks": ["Who churned this month?"],
    },
    {
        "type": "tasks",
        "title": "Launch week",
        "tasks": [
            {"title": "Book the venue", "status": "Done", "owner": "Alex"},
            {
                "title": "Write the launch post",
                "status": "In progress",
                "due": "2026-10-27",
                "owner": "Jo",
                "priority": "High",
            },
            {
                "title": "New pricing page",
                "status": "Blocked",
                "due": "2026-10-28",
                "owner": "Sam",
                "priority": "High",
            },
            {"title": "Order name badges", "status": "To do", "due": "Friday", "owner": "Alex"},
        ],
        "source": "Linear",
        "asks": ["What is blocking pricing?"],
    },
    {
        "type": "transactions",
        "title": "Payments today",
        "items": [
            {
                "who": "Maple Park Dental",
                "amount": "$1,200",
                "what": "Annual plan, 10 seats",
                "status": "paid",
                "when": "2026-10-25T09:12",
            },
            {
                "who": "Elm Street Bakery",
                "amount": "$89",
                "what": "Monthly plan",
                "status": "paid",
                "when": "2026-10-25T08:40",
            },
            {
                "who": "Alex Rivera Design",
                "amount": "$240",
                "what": "Extra seats",
                "status": "pending",
                "when": "2026-10-25T08:05",
            },
            {
                "who": "Jo Park",
                "amount": "$89",
                "what": "Card declined",
                "status": "failed",
                "when": "2026-10-25T07:30",
            },
            {
                "who": "The Boathouse",
                "amount": "$45",
                "what": "Duplicate charge",
                "status": "refunded",
                "when": "2026-10-24T17:55",
            },
        ],
        "total": "$1,529",
        "source": "Stripe",
    },
    {
        "type": "invoices",
        "title": "Unpaid invoices",
        "items": [
            {
                "who": "Maple Park Dental",
                "amount": "$1,200",
                "due": "2026-10-10",
                "status": "overdue",
            },
            {
                "who": "Alex Rivera Design",
                "amount": "$850",
                "due": "2026-10-18",
                "status": "overdue",
            },
            {"who": "Elm Street Bakery", "amount": "$420", "due": "2026-10-31", "status": "due"},
            {"who": "The Boathouse", "amount": "$1,750", "due": "2026-11-15", "status": "due"},
        ],
        "source": "PayPal",
        "asks": ["Remind the two that are late"],
    },
    {
        "type": "breakdown",
        "title": "Sales by category, October",
        "items": [
            {"label": "Coffee", "value": 8420},
            {"label": "Pastries", "value": 5130},
            {"label": "Lunch", "value": 3960},
            {"label": "Retail beans", "value": 1870},
            {"label": "Gift cards", "value": 640},
        ],
        "unit": "$",
        "source": "Square",
    },
    {
        "type": "payouts",
        "title": "Next payouts",
        "items": [
            {"amount": "$3,420", "when": "2026-10-27", "note": "To the account ending 4410"},
            {"amount": "$2,180", "when": "2026-10-29", "note": "To the account ending 4410"},
            {"amount": "$1,280", "when": "2026-10-30", "note": "Held 2 days for a dispute"},
        ],
        "total": "$6,880",
        "source": "Stripe",
    },
    {
        "type": "pipeline",
        "title": "Q4 pipeline",
        "stages": [
            {"label": "Leads", "count": 48, "value": "$212,000"},
            {"label": "Qualified", "count": 21, "value": "$118,000"},
            {"label": "Proposal", "count": 9, "value": "$64,500"},
            {"label": "Won", "count": 4, "value": "$31,200"},
        ],
        "source": "HubSpot",
        "asks": ["Which proposals are stuck?"],
    },
    {
        "type": "deal",
        "name": "Maple Park Dental, 40 seats",
        "amount": "$18,000 a year",
        "stages": ["Lead", "Qualified", "Proposal", "Negotiation", "Won"],
        "current": "Proposal",
        "owner": "Sam",
        "next": "Send the revised quote",
        "close": "2026-11-20",
        "source": "HubSpot",
        "asks": ["Draft the revised quote"],
    },
    {
        "type": "contact",
        "title": "Jo Park",
        "name": "Jo Park",
        "role": "Head of operations",
        "company": "Maple Park Dental",
        "facts": [
            {"label": "Customer since", "value": "Mar 2024"},
            {"label": "Plan", "value": "Annual, 10 seats"},
            {"label": "Lifetime value", "value": "$4,800"},
        ],
        "activity": [
            {"title": "Opened the 40-seat proposal", "when": "2026-10-24T16:20"},
            {"title": "Call with Sam about onboarding", "when": "2026-10-22T11:00"},
            {"title": "Asked for a second admin login", "when": "2026-10-19T09:45"},
        ],
        "source": "HubSpot",
    },
    {
        "type": "campaign",
        "title": "October newsletter",
        "sent": 4200,
        "delivered": 4150,
        "opened": 1890,
        "clicked": 412,
        "source": "Resend",
    },
    {
        "type": "support_queue",
        "title": "Support",
        "open": 14,
        "waiting": [
            {"who": "Alex", "subject": "Can't add a second admin", "since": "2026-10-25T08:10"},
            {
                "who": "Jo Park",
                "subject": "Refund for a double charge",
                "since": "2026-10-24T15:30",
            },
            {"who": "Sam", "subject": "Export to a spreadsheet", "since": "2026-10-23T10:05"},
        ],
        "first_reply": "1h 40m",
        "source": "Intercom",
        "asks": ["Draft a reply to Sam"],
    },
    {
        "type": "checks",
        "title": "Checks on the new checkout",
        "items": [
            {"title": "Unit tests", "state": "passing", "when": "2026-10-25T09:40"},
            {"title": "Type check", "state": "passing", "when": "2026-10-25T09:38"},
            {"title": "End-to-end tests", "state": "failing", "when": "2026-10-25T09:44"},
            {"title": "Preview build", "state": "running"},
        ],
        "source": "GitHub",
        "asks": ["Why are the end-to-end tests failing?"],
    },
    {
        "type": "errors",
        "title": "Errors this week",
        "trend": [42, 38, 51, 47, 160, 210, 185],
        "issues": [
            {"title": "TypeError in checkout", "events": 412, "users": 96},
            {"title": "Timeout on search", "events": 138, "users": 41},
            {"title": "Image upload too large", "events": 54, "users": 22},
            {"title": "Missing avatar", "events": 12, "users": 9},
        ],
        "source": "Sentry",
    },
    {
        "type": "deploys",
        "title": "Recent deploys",
        "items": [
            {
                "title": "New checkout flow",
                "state": "building",
                "branch": "checkout",
                "when": "2026-10-25T09:44",
            },
            {
                "title": "Fix the header on small screens",
                "state": "ready",
                "branch": "main",
                "when": "2026-10-25T08:15",
            },
            {
                "title": "Try the new font",
                "state": "failed",
                "branch": "type",
                "when": "2026-10-24T16:30",
            },
            {
                "title": "Pricing copy",
                "state": "ready",
                "branch": "main",
                "when": "2026-10-24T11:05",
            },
        ],
        "source": "Vercel",
    },
    {
        "type": "services",
        "title": "Status right now",
        "items": [
            {"name": "Database", "state": "up"},
            {"name": "Auth", "state": "up"},
            {"name": "Storage", "state": "degraded", "note": "Slow uploads since 9:20"},
            {"name": "Realtime", "state": "up"},
            {"name": "Edge functions", "state": "up"},
        ],
        "source": "Supabase",
    },
    {
        "type": "kanban",
        "title": "Website refresh",
        "columns": [
            {"name": "To do", "count": 9, "items": ["Footer links", "Cookie notice", "404 page"]},
            {"name": "Doing", "items": ["Pricing page", "New header"]},
            {"name": "Review", "items": ["Team photos"]},
            {"name": "Done", "count": 14, "items": ["Logo", "Colour tokens", "Contact form"]},
        ],
        "source": "Linear",
    },
    {
        "type": "sprint",
        "name": "Sprint 42",
        "done": 12,
        "total": 20,
        "ends": "2026-10-30",
        "burndown": [20, 19, 17, 15, 14, 11, 8],
        "source": "Jira and Confluence",
        "asks": ["What won't make it?"],
    },
    {
        "type": "due_soon",
        "title": "Due this week",
        "items": [
            {"title": "Renew the domain", "due": "2026-10-24", "owner": "Alex"},
            {"title": "Send the quote to Maple Park", "due": "2026-10-25T17:00", "owner": "Sam"},
            {"title": "Pay the Elm Street rent", "due": "2026-10-26"},
            {"title": "Review the launch post", "due": "2026-10-26T15:00", "owner": "Jo"},
            {"title": "Book the movers", "due": "2026-10-29"},
            {"title": "File the quarterly taxes", "due": "2026-10-30"},
        ],
        "source": "Todoist",
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
            {
                "title": "Maple Park Dental",
                "detail": "Invoice 214",
                "amount": "$1,200",
                "amount_tone": "bad",
                "when": "2026-10-10",
            },
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
    {
        "type": "bars",
        "items": [
            {"label": "Rent", "value": 1950},
            {"label": "Groceries", "value": 612, "note": "Up $80 on September"},
            {"label": "Travel", "value": 245},
            {"label": "Eating out", "value": 180},
        ],
        "unit": "$",
    },
    {
        "type": "funnel",
        "stages": [
            {"label": "Leads", "count": 48, "value": "$212,000"},
            {"label": "Qualified", "count": 21, "value": "$118,000"},
            {"label": "Proposal", "count": 9, "value": "$64,500"},
            {"label": "Won", "count": 4, "value": "$31,200"},
        ],
    },
    {"type": "stages", "steps": ["Lead", "Qualified", "Proposal", "Won"], "current": "Proposal"},
]

BLOCK_SAMPLES: list[Sample] = [
    {"type": "custom", "title": block["type"], "blocks": [block]} for block in _BLOCKS
]
