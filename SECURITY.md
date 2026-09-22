# Security Policy

Trillion runs on your machine, holds your API keys, and can take actions on
your behalf. We take reports seriously and we would rather hear from you
early than read about it later.

## Reporting a vulnerability

**Do not open a public issue.**

Report privately, whichever you prefer:

1. **GitHub private advisories** — the Security tab of this repository,
   "Report a vulnerability". This is the fastest route and keeps the whole
   discussion in one place.
2. **Email** — me@hellotrillion.ai. Use the subject line `SECURITY`.

Please include what you found, how to reproduce it, what an attacker gains,
and the version or commit. A proof of concept helps enormously.

## What to expect

| | |
|---|---|
| First response | within 3 business days |
| Assessment and severity | within 7 days |
| Fix target, critical | 14 days |
| Fix target, everything else | 30 days |

We will credit you in the advisory unless you ask us not to. This is a
pre-1.0 project maintained by a small team: we have no bug bounty, and we
will tell you honestly if a fix is going to take longer.

## Scope

**In scope, and especially interesting:**

- Any way to make a tool with risk `irreversible` or `spend` execute
  without the user confirming it, or with arguments other than the ones
  frozen when it was proposed.
- Escaping the deny floor or the file sandbox.
- Prompt injection from tool output, memory, a web page, an email or a
  channel message that causes an action the user did not ask for.
- Reaching the local API or WebSocket without the session token, or from
  another origin.
- Secrets leaking into logs, telemetry, prompts or the UI.
- Malicious skills, MCP servers or plugins escaping the spine's gating.

**Out of scope:**

- Attacks that need an attacker who already has local access as your user.
- Whatever the configured model provider chooses to say. We defend against
  *actions*, not against wrong answers.
- Denial of service against your own machine by your own configuration.
- Vulnerabilities in third-party providers themselves — report those to them.

## Hardening we already assume

The listener binds to `127.0.0.1`, every request carries a session token,
there is no shell tool, secrets live in the OS keychain, and telemetry is
off unless you turn it on. If you find a configuration where one of these
is not true, that is a bug and we want the report.
