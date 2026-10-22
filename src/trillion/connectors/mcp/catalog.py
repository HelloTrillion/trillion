"""The featured connections in Settings → Connected tools: each one a service's own
MCP server, checked by hand, so adding one is a tap and a sign-in (CON-5).

Every entry points at the service's official server: an address on the service's
own domain that signs in through the browser, or a package the service publishes,
pinned to a version. A server added from here is treated exactly like one added by
hand: every tool asks first until the user says otherwise.

To propose an entry: it must be the service's own server (listed by the service,
or under its namespace in the MCP registry), and an address must let a new app
register to sign in (OAuth dynamic client registration) or take a key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

How = Literal["sign_in", "key", "google", "slack", "telegram"]


@dataclass(frozen=True, kw_only=True)
class Field:
    """Something the user gives when connecting: a key, or a setting."""

    id: str
    label: str
    secret: bool = True
    placeholder: str = ""
    # Where to get it, and what that page is called.
    link: str = ""
    link_label: str = ""


@dataclass(frozen=True, kw_only=True)
class Entry:
    id: str
    title: str
    blurb: str
    category: str
    # The tile: the service's colour and one or two letters. No logos are bundled.
    color: str
    glyph: str
    how: How
    url: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    # Values may name a field as {id}: it is filled in, and a secret one becomes a
    # keychain reference, never the key itself.
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    fields: tuple[Field, ...] = ()
    homepage: str = ""
    note: str = ""
    # For Google: which built-in service (connectors/google), not an MCP server.
    service: str = ""


_GOOGLE_NOTE = "Built into Trillion: Google signs you in, and nothing else is installed."

ENTRIES: tuple[Entry, ...] = (
    # ── Google: built in (CON-4, connectors/google), not MCP ─────────────
    Entry(
        id="gmail",
        title="Gmail",
        blurb="Read, search and draft email",
        category="Email and calendar",
        color="#EA4335",
        glyph="M",
        how="google",
        service="gmail",
        note=_GOOGLE_NOTE,
    ),
    Entry(
        id="google-calendar",
        title="Google Calendar",
        blurb="See your day, find time, add events",
        category="Email and calendar",
        color="#4285F4",
        glyph="31",
        how="google",
        service="calendar",
        note=_GOOGLE_NOTE,
    ),
    Entry(
        id="google-drive",
        title="Google Drive",
        blurb="Find and read your docs and files",
        category="Docs and files",
        color="#1FA463",
        glyph="D",
        how="google",
        service="drive",
        note=_GOOGLE_NOTE,
    ),
    # ── Chat: built in (connectors/slack, channels/), not MCP ────────────
    Entry(
        id="slack",
        title="Slack",
        blurb="Search, read and post, and talk to her there",
        category="Chat and messages",
        color="#4A154B",
        glyph="S",
        how="slack",
        homepage="https://slack.com",
    ),
    Entry(
        id="telegram",
        title="Telegram",
        blurb="Talk to her from your phone",
        category="Chat and messages",
        color="#229ED9",
        glyph="T",
        how="telegram",
        homepage="https://telegram.org",
    ),
    # ── Docs and files ───────────────────────────────────────────────────
    Entry(
        id="notion",
        title="Notion",
        blurb="Search, read and write your pages",
        category="Docs and files",
        color="#191919",
        glyph="N",
        how="sign_in",
        url="https://mcp.notion.com/mcp",
        homepage="https://www.notion.so",
    ),
    Entry(
        id="dropbox",
        title="Dropbox",
        blurb="Find and read your files",
        category="Docs and files",
        color="#0061FE",
        glyph="Db",
        how="sign_in",
        url="https://mcp.dropbox.com/mcp",
        homepage="https://www.dropbox.com",
    ),
    Entry(
        id="atlassian",
        title="Jira and Confluence",
        blurb="Issues, pages and spaces in Atlassian",
        category="Docs and files",
        color="#0052CC",
        glyph="A",
        how="sign_in",
        url="https://mcp.atlassian.com/v1/mcp",
        homepage="https://www.atlassian.com",
    ),
    Entry(
        id="miro",
        title="Miro",
        blurb="Read and add to your boards",
        category="Docs and files",
        color="#FFD02F",
        glyph="Mi",
        how="sign_in",
        url="https://mcp.miro.com/",
        homepage="https://miro.com",
    ),
    # ── Tasks and projects ───────────────────────────────────────────────
    Entry(
        id="todoist",
        title="Todoist",
        blurb="Your tasks, projects and due dates",
        category="Tasks and projects",
        color="#E44332",
        glyph="T",
        how="sign_in",
        url="https://ai.todoist.net/mcp",
        homepage="https://todoist.com",
    ),
    Entry(
        id="linear",
        title="Linear",
        blurb="Issues, projects and cycles",
        category="Tasks and projects",
        color="#5E6AD2",
        glyph="L",
        how="sign_in",
        url="https://mcp.linear.app/mcp",
        homepage="https://linear.app",
    ),
    Entry(
        id="clickup",
        title="ClickUp",
        blurb="Tasks, docs and goals",
        category="Tasks and projects",
        color="#7B68EE",
        glyph="Cu",
        how="sign_in",
        url="https://mcp.clickup.com/mcp",
        homepage="https://clickup.com",
    ),
    Entry(
        id="monday",
        title="monday.com",
        blurb="Boards, items and updates",
        category="Tasks and projects",
        color="#FF3D57",
        glyph="m",
        how="sign_in",
        url="https://mcp.monday.com/mcp",
        homepage="https://monday.com",
    ),
    Entry(
        id="airtable",
        title="Airtable",
        blurb="Read and update your bases",
        category="Tasks and projects",
        color="#18BFFF",
        glyph="At",
        how="sign_in",
        url="https://mcp.airtable.com/mcp",
        homepage="https://airtable.com",
    ),
    # ── Design and websites ──────────────────────────────────────────────
    Entry(
        id="canva",
        title="Canva",
        blurb="Find, create and export designs",
        category="Design and websites",
        color="#00C4CC",
        glyph="C",
        how="sign_in",
        url="https://mcp.canva.com/mcp",
        homepage="https://www.canva.com",
    ),
    Entry(
        id="webflow",
        title="Webflow",
        blurb="Your sites, pages and CMS",
        category="Design and websites",
        color="#146EF5",
        glyph="W",
        how="sign_in",
        url="https://mcp.webflow.com/mcp",
        homepage="https://webflow.com",
    ),
    Entry(
        id="wix",
        title="Wix",
        blurb="Your site, store and bookings",
        category="Design and websites",
        color="#0C0C0C",
        glyph="Wx",
        how="sign_in",
        url="https://mcp.wix.com/mcp",
        homepage="https://www.wix.com",
    ),
    # ── Money and customers ──────────────────────────────────────────────
    Entry(
        id="hubspot",
        title="HubSpot",
        blurb="Contacts, companies, deals and notes",
        category="Money and customers",
        color="#FF5C35",
        glyph="H",
        how="key",
        command="npx",
        args=("-y", "@hubspot/mcp-server@0.4.0"),
        env={"PRIVATE_APP_ACCESS_TOKEN": "{token}"},
        fields=(
            Field(
                id="token",
                label="Private app access token",
                placeholder="pat-…",
                link="https://developers.hubspot.com/docs/apps/legacy-apps/private-apps/overview",
                link_label="How to make one in HubSpot",
            ),
        ),
        homepage="https://www.hubspot.com",
        note="Choose only the scopes she needs when you make the token; read-only is a good start.",
    ),
    Entry(
        id="stripe",
        title="Stripe",
        blurb="Payments, customers and invoices",
        category="Money and customers",
        color="#635BFF",
        glyph="S",
        how="sign_in",
        url="https://mcp.stripe.com",
        homepage="https://stripe.com",
        note="Anything that charges, refunds or changes a payment always asks you first.",
    ),
    Entry(
        id="paypal",
        title="PayPal",
        blurb="Invoices, orders and transactions",
        category="Money and customers",
        color="#003087",
        glyph="P",
        how="sign_in",
        url="https://mcp.paypal.com/mcp",
        homepage="https://www.paypal.com",
        note="Anything that moves money always asks you first.",
    ),
    Entry(
        id="square",
        title="Square",
        blurb="Sales, catalog and customers",
        category="Money and customers",
        color="#3E4348",
        glyph="Sq",
        how="sign_in",
        url="https://mcp.squareup.com/mcp",
        homepage="https://squareup.com",
        note="Anything that moves money always asks you first.",
    ),
    Entry(
        id="intercom",
        title="Intercom",
        blurb="Conversations and contacts",
        category="Money and customers",
        color="#286EFA",
        glyph="I",
        how="sign_in",
        url="https://mcp.intercom.com/mcp",
        homepage="https://www.intercom.com",
    ),
    # ── Automation and the web ───────────────────────────────────────────
    Entry(
        id="zapier",
        title="Zapier",
        blurb="Thousands of apps through your Zaps",
        category="Automation and search",
        color="#FF4F00",
        glyph="Z",
        how="sign_in",
        url="https://mcp.zapier.com/api/v1/connect",
        homepage="https://zapier.com",
    ),
    Entry(
        id="brave-search",
        title="Brave Search",
        blurb="Search the web, news and images",
        category="Automation and search",
        color="#FB542B",
        glyph="B",
        how="key",
        command="npx",
        args=("-y", "@brave/brave-search-mcp-server@2.0.10"),
        env={"BRAVE_API_KEY": "{key}"},
        fields=(
            Field(
                id="key",
                label="Brave Search API key",
                link="https://brave.com/search/api/",
                link_label="Get a free key",
            ),
        ),
        homepage="https://brave.com/search/api/",
    ),
    # ── Sending email ────────────────────────────────────────────────────
    Entry(
        id="resend",
        title="Resend",
        blurb="Send email, and manage domains and audiences",
        category="Sending email",
        color="#000000",
        glyph="R",
        how="sign_in",
        url="https://mcp.resend.com/mcp",
        homepage="https://resend.com",
        note="Anything that sends an email always asks you first.",
    ),
    Entry(
        id="nitrosend",
        title="Nitrosend",
        blurb="Email campaigns, flows and contacts",
        category="Sending email",
        color="#FF5100",
        glyph="N",
        how="sign_in",
        url="https://api.nitrosend.com/mcp",
        homepage="https://nitrosend.com",
        note="Anything that sends an email always asks you first.",
    ),
    # ── For developers ───────────────────────────────────────────────────
    Entry(
        id="clerk",
        title="Clerk",
        blurb="Your auth apps, users and SDK snippets",
        category="For developers",
        color="#6C47FF",
        glyph="C",
        how="sign_in",
        url="https://mcp.clerk.com/mcp",
        homepage="https://clerk.com",
    ),
    Entry(
        id="github",
        title="GitHub",
        blurb="Repositories, issues and pull requests",
        category="For developers",
        color="#24292F",
        glyph="GH",
        how="key",
        url="https://api.githubcopilot.com/mcp/",
        headers={"Authorization": "Bearer {token}"},
        fields=(
            Field(
                id="token",
                label="Personal access token",
                placeholder="github_pat_…",
                link="https://github.com/settings/personal-access-tokens/new",
                link_label="Make a token on GitHub",
            ),
        ),
        homepage="https://github.com",
    ),
    Entry(
        id="sentry",
        title="Sentry",
        blurb="Errors, issues and releases",
        category="For developers",
        color="#362D59",
        glyph="Se",
        how="sign_in",
        url="https://mcp.sentry.dev/mcp",
        homepage="https://sentry.io",
    ),
    Entry(
        id="vercel",
        title="Vercel",
        blurb="Projects, deployments and logs",
        category="For developers",
        color="#111111",
        glyph="V",
        how="sign_in",
        url="https://mcp.vercel.com",
        homepage="https://vercel.com",
    ),
    Entry(
        id="supabase",
        title="Supabase",
        blurb="Databases, tables and functions",
        category="For developers",
        color="#3ECF8E",
        glyph="Sb",
        how="sign_in",
        url="https://mcp.supabase.com/mcp",
        homepage="https://supabase.com",
    ),
)

BY_ID = {e.id: e for e in ENTRIES}
_FIELD = re.compile(r"\{([a-z_]+)\}")


class CatalogError(ValueError):
    """What the user gave does not fit the entry. Safe to show."""


def ref(server: str, field_id: str) -> str:
    """The keychain name a server's secret is saved under."""
    return f"mcp/{server}/{field_id}"


def build(entry: Entry, name: str, values: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """The mcp.json entry for a featured server, and the secrets to save, by keychain
    name. Secrets go to the keychain; the file only names them."""
    if entry.how in ("google", "slack", "telegram"):
        raise CatalogError(f"{entry.title} is built in, not an MCP server")
    fields = {f.id: f for f in entry.fields}
    secrets: dict[str, str] = {}
    for f in entry.fields:
        value = values.get(f.id, "").strip()
        if not value:
            raise CatalogError(f"{f.label} is needed")
        if f.secret:
            secrets[ref(name, f.id)] = value

    def fill(text: str) -> str:
        def one(m: re.Match[str]) -> str:
            f = fields.get(m.group(1))
            if f is None:
                return m.group(0)
            return f"keychain:{ref(name, f.id)}" if f.secret else values[f.id].strip()

        return _FIELD.sub(one, text)

    raw: dict[str, Any] = {"title": entry.title, "catalog": entry.id}
    if entry.url:
        raw["url"] = entry.url
        if entry.headers:
            raw["headers"] = {k: fill(v) for k, v in entry.headers.items()}
    else:
        raw["command"] = entry.command
        raw["args"] = [fill(a) for a in entry.args]
        if entry.env:
            raw["env"] = {k: fill(v) for k, v in entry.env.items()}
    return raw, secrets


def to_json(entry: Entry) -> dict[str, Any]:
    """What Settings needs to draw a tile and its setup sheet."""
    return {
        "id": entry.id,
        "title": entry.title,
        "blurb": entry.blurb,
        "category": entry.category,
        "color": entry.color,
        "glyph": entry.glyph,
        "how": entry.how,
        "connects": entry.url or " ".join((entry.command, *entry.args)),
        "local": bool(entry.command),
        "fields": [
            {
                "id": f.id,
                "label": f.label,
                "secret": f.secret,
                "placeholder": f.placeholder,
                "link": f.link,
                "link_label": f.link_label,
            }
            for f in entry.fields
        ],
        "homepage": entry.homepage,
        "note": entry.note,
        "service": entry.service,
    }
