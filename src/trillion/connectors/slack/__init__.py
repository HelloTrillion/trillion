"""Slack, built in: her tools in the user's workspace, and talking to her from Slack.

One Slack app, made by the user from the manifest Trillion gives them, serves both:

- **Tools** (this package) use the app's *user* token: search, read and post as the
  user. Reading asks first until the user says otherwise; posting always asks.
- **Talking to her** (`trillion.channels.slack`) uses the app's *bot* token and an
  app-level token for Socket Mode: an outbound connection, so nothing on this
  computer is reachable from the internet. Answers only, to people the user allowed.

Every token is kept in the system keychain.
"""

from __future__ import annotations

import json
from urllib.parse import quote

USER_TOKEN = "slack/user"  # noqa: S105 - keychain names, not tokens
BOT_TOKEN = "slack/bot"  # noqa: S105
APP_TOKEN = "slack/app"  # noqa: S105

# The Slack app Trillion asks the user to create. Every scope is one a tool or the
# chat needs; nothing for admin, files or anything else.
MANIFEST: dict[str, object] = {
    "display_information": {
        "name": "Trillion",
        "description": "Your Trillion assistant, running on your own computer.",
        "background_color": "#0e0f13",
    },
    "features": {
        "bot_user": {"display_name": "Trillion", "always_online": False},
        "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False},
    },
    "oauth_config": {
        "scopes": {
            "user": [
                "search:read",
                "channels:read",
                "channels:history",
                "groups:read",
                "groups:history",
                "im:read",
                "im:history",
                "mpim:read",
                "mpim:history",
                "users:read",
                "chat:write",
            ],
            "bot": [
                "app_mentions:read",
                "chat:write",
                "im:history",
                "im:read",
                "users:read",
            ],
        }
    },
    "settings": {
        "event_subscriptions": {"bot_events": ["app_mention", "message.im"]},
        "interactivity": {"is_enabled": False},
        "org_deploy_enabled": False,
        "socket_mode_enabled": True,
        "token_rotation_enabled": False,
    },
}


def create_app_url() -> str:
    """Slack's own page for creating an app, filled in with Trillion's manifest."""
    return "https://api.slack.com/apps?new_app=1&manifest_json=" + quote(
        json.dumps(MANIFEST, separators=(",", ":"))
    )
