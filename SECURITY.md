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
- Malicious skills, MCP servers or plugins escaping the spine's gating
  *through the tool interface*. (An in-process Python plugin runs with
  Trillion's own privileges, so routing its tools through the spine is not a
  sandbox — see "Not true yet" below. Reports that a plugin can bypass the
  spine by running its own code are known, and are welcome as input to the
  isolation design rather than as vulnerabilities.)

**Out of scope:**

- Attacks that need an attacker who already has local access as your user.
- Whatever the configured model provider chooses to say. We defend against
  *actions*, not against wrong answers.
- Denial of service against your own machine by your own configuration.
- Vulnerabilities in third-party providers themselves — report those to them.

## The threat model today (Phase 0)

Phase 0 assumes **one trusted owner at the machine**. Concretely:

- A pending action can only be closed from the session that raised it. A
  message on another channel never closes one.
- Actions that spend money, or that can't be undone, need a **tap** on the
  confirmation card.
- A spoken "yes" is **not** tied to who said it. Anyone in the room, or a
  recording, can confirm an action that is allowed to be confirmed by voice.
  Speaker verification is planned for Phase 1 (PRD §5.3a).

If you find a way around the first two, that is a vulnerability. The third
is a known limit of this phase.

## What is true today

- **There is no shell tool.** The only tools are the file tools, confined to
  folders the user granted.
- **Every file write asks first.** The file-writing tools — `file_create`,
  `file_overwrite`, `file_append` and `file_edit` — are all declared
  `irreversible`, so they are gated: the arguments are frozen when proposed,
  the confirmation carries no arguments of its own, and the target is
  re-checked before anything runs. (The spine lets a tool declared `write`
  run without asking. The only shipped `write` tools are the spine's own
  `confirm_pending_action` and `cancel_pending_action`.)
- **The deny floor** refuses Trillion's own data folder (its database,
  secrets file and identity files), its source code, the macOS keychains,
  and `.git`, `.ssh`, `.aws`, `.gnupg` and `.env` files — on the normal path
  and again on the approved path.
- **Tool output is untrusted data.** Every result is wrapped and marked as
  data, not instructions; only first-party tools may opt out.
- **The database is owner-only** (folder `0700`, files `0600`) on macOS and
  Linux.
- **There is no telemetry.** No analytics or crash-reporting code exists.

## Not true yet — required before the first release

These are in the specification and are not built. Until they are, do not
rely on them:

- **A network listener bound to `127.0.0.1`, with a session token on every
  request** (SPN-11, SPN-11a). There is no listener yet.
- **Secrets in the OS keychain** (SEC-1). There is no secret store yet; the
  encrypted-file fallback (SEC-2) is where the path points.
- **Database hardening on Windows** (SUR-3c). Windows relies on the home
  folder's own permissions for now.
- **Isolation for plugins.** In-process plugins share Trillion's privileges.
  Only first-party tools are loaded in Phase 0.

When one of these lands, it moves up to "What is true today" in the same
pull request, with the test that proves it.
