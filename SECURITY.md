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

- **There is no shell tool.** The tools are the file tools (confined to
  folders the user granted), memory (`memory_write`, `memory_recall`,
  `memory_forget`; a memory written in a turn that read a page or a file is
  kept as something she read, not as the user's words, and cannot replace
  one), standing rules (`rule_keep` and `rule_drop` ask on a card;
  `rule_never` keeps at once only a rule the user just said that plainly
  narrows what she does, and never in a turn that read a page or a file;
  `rule_list` reads them), the decision
  record (`decision_record`, read-only), `web_fetch` and `describe_self`,
  plus the tools of any MCP server the user connected (below), and
  `skill_open` and `skill_file`, which read skills the user turned on.
- **Skills add instructions, never power.** A skill is a `SKILL.md` folder
  from the curated set or a folder the user added. Every skill starts off; the
  user turns one on after seeing what it says it does, and what they approve
  is a fingerprint of every file in it, so any change turns it off again. Each
  read checks that the bytes returned are the approved ones. Skill text is not
  marked untrusted, because the user approved it byte for byte, but it cannot
  change a gate: the gates are code. There is no tool that runs code, so a
  skill's scripts are only ever read. `skill_file` returns only files listed
  in the approved skill and refuses links, `..` and absolute paths. A skill
  that can't be used is reported to her without any of its file names.
- **Nothing that runs unattended can ask, or act where it would have asked.** A
  timed ask runs with a view of the tools that holds only those the router runs
  without a card: no irreversible or spend tool, no connected tool the user left
  asking first, no tool that approves or declines, and none that sets or cancels
  a schedule. The router also refuses every card, and every approval, to a call
  made by a timed ask (`via="schedule"`). Setting a timed ask shows a card, so a
  page or an email can't plant one. `reminder_set` is refused after she has read
  a page, a file or a message in the same turn, so nothing she read can make her
  say something later in her own voice, and a timed ask's answer counts as
  something she read. `schedule_cancel` is refused in the same way, so what she
  read can't undo a timed ask the user approved. A timed ask that was
  interrupted is reported failed and never run again: a tool it used may
  already have acted. A missed or failed notice is never spoken.
- **Caveat: a timed ask's answer can pass on what she read.** She runs it
  unattended, reads (an inbox, say) and reports in her own words, and that
  answer is shown and spoken like any of her messages. Text in an email can
  shape what she says there. The answer is marked as outside content
  afterwards, so the turn it arrives in and the next can't set or cancel a
  reminder on its strength, but the words themselves reach the user as she
  wrote them.
- **One door to a surface.** Only `delivery/door.py` can put a notice on the
  page or show a system notification; the scheduler imports neither, and a test
  checks it. System notifications run as argument lists, never through a shell,
  with the words passed as arguments after `--` (`osascript`, `notify-send`) or
  as environment values read by a fixed, encoded PowerShell script on Windows,
  never spliced into a script. A timed ask's notification never shows what she
  found.
- **MCP servers run only if the user connected them**, in Settings →
  Connected tools or by editing `mcp.json` in Trillion's own folder, which no
  tool can edit. The routes that add, change or remove a server need the
  page's session token and are not tools: nothing she can do adds a server or
  lets one run without asking. A local server's exact command, or a remote
  server's address, is shown before it is connected. The featured list in
  Settings points only at each service's own server, and a package it runs is
  pinned to a version.
  Every MCP tool asks first unless the user marked it to run without asking;
  a server's own claim that a tool only reads is shown, never trusted. What an
  MCP tool returns is wrapped as untrusted, and a turn that read it is treated
  like one that read a web page. An argument that may hold a path is checked
  by the deny floor wherever it points: keys, the keychain, `.ssh`, `.git`,
  `.env` files and Trillion's own folder are refused (SEC-10). Keys a server
  needs are read from the keychain by name (`keychain:NAME`), so none has to
  sit in the file; a key pasted into Settings goes straight to the keychain.
  Signing in to a remote server (OAuth with PKCE) happens in the user's
  browser, and the sign-in is kept in the keychain. The browser comes back to
  `/mcp/callback`, the one route that needs no session token: it only hands a
  code to a sign-in already waiting for that exact random state, which the MCP
  library checks again before using it. Removing a server deletes its keys and
  its sign-in. **The limit:** a local MCP server is a program running on
  your machine with your privileges. The spine controls what Trillion asks it
  to do, not what it does on its own (CON-6a). Connect only servers you trust,
  as you would install only apps you trust.
- **Google (Gmail, Calendar, Drive) and Slack are built-in tools under the same
  rules.** Sign-in to Google is OAuth with PKCE through the user's browser to
  Trillion's loopback address (`/google/callback`, which only completes a
  sign-in already waiting for its random state); the refresh token is kept in
  the keychain and revoked with Google when the account is removed. Each
  service asks only for the access its tools need. Reading asks first until the
  user lets it run; sending email, changing or deleting events and posting in
  Slack are `irreversible` and always ask, and the user cannot change that.
  Changing or deleting an event must name its title, checked against Google
  before the card is shown. Mail, events, documents and Slack messages are
  wrapped as untrusted, and a turn that read them is treated like one that read
  a web page.
- **Slack and Telegram channels only answer (SUR-5, SPN-16).** A message is
  answered only if its sender is on the user's allow-list for that platform,
  which starts empty, and in a group only when it @mentions her. A channel turn
  calls the model with no tools at all, not even the spine's own, so nothing said
  in a channel can act, read anything private, or approve anything waiting for
  the user. Both connections are outbound (Slack Socket Mode, Telegram long
  polling): nothing on the user's machine is reachable from the internet.
- **`web_fetch` only opens an address the user gave, or a link on a page
  already read**, in the same conversation. She cannot compose an address,
  so nothing private can be sent out inside one. Every address a host
  resolves to must be public: this machine, its local network and other
  private, shared or reserved ranges are refused, checked when the connection
  opens and pinned for it, and again on every redirect. Only ports 80 and 443,
  no credentials in addresses, no cookies. Proven by `tests/unit/test_web.py`
  and `tests/integration/test_injection_web.py`.
- **Every file write asks first.** The file-writing tools — `file_create`,
  `file_overwrite`, `file_append` and `file_edit` — are all declared
  `irreversible`, so they are gated: the arguments are frozen when proposed,
  the confirmation carries no arguments of its own, and the target is
  re-checked before anything runs. (The spine lets a tool declared `write`
  run without asking. The shipped `write` tools are the spine's own
  `confirm_pending_action` and `cancel_pending_action`, and `memory_write`:
  she can save a memory without asking. Forgetting a memory and keeping or
  dropping a standing rule are `irreversible`, so they ask.)
- **The deny floor** refuses Trillion's own data folder (its database,
  secrets file and identity files), its source code, the operating system's
  credential stores, and `.git`, `.ssh`, `.aws`, `.gnupg` and `.env` files,
  on the normal path and again on the approved path. The credential stores
  are the macOS keychains, the Linux keyrings (Secret Service and KWallet),
  and Windows Credential Manager, the DPAPI master keys and the Vault. All of
  them are refused on every OS, including where `%APPDATA%`,
  `%LOCALAPPDATA%` or `$XDG_DATA_HOME` has been moved. Proven by
  `tests/spine/test_credential_stores.py`.
- **Logs never carry a credential** (SEC-5). Every log record is scrubbed
  before it is written: by the shapes providers issue (Anthropic, OpenAI,
  Slack, Google, Stripe, Telegram, GitHub, ElevenLabs, bearer headers), and
  by value. The keychain store notes every value it reads or writes, for
  every connector, including the secret fields inside a stored sign-in, so
  a key fetched late or with no known shape is scrubbed too, as are
  environment variables named like credentials and the session token.
  Proven by `tests/unit/test_secrets.py`.
- **A file tool never opens a path by name.** It applies the floor once more,
  then walks down from the granted folder one name at a time, each step opened
  relative to the last and refused if it is a link, so a folder swapped for a
  link after the check cannot carry a read or a write outside the grant. A
  file with a second hard link is not read or appended to. On Windows, which
  has no `dir_fd`, each step is checked for a link or a junction just before
  the file opens instead. Proven by `tests/spine/test_parent_swap.py`.
- **A card must have been on screen to be approved.** The page reports each
  card once it is really displayed; until then a tap or a spoken yes is
  refused, and the card stays open. Declining needs no such proof.
- **Two requests for the same file never both stay open.** A new one retires
  the old in the same transaction, and `Notes.txt` and `notes.txt` count as
  the same target.
- **The audit log keeps keyed hashes**, made with a key created for this
  install and kept owner-only, so a short argument such as a path cannot be
  guessed back from a row. A call for an owner that does not exist is refused
  before anything is done or recorded. Proven by `tests/spine/test_hardening.py`.
- **The local server listens on `127.0.0.1` only**, with no option to change
  it. Every request and socket needs the session token, which is issued
  fresh at each launch; another origin is refused even with the token, and
  an unauthenticated socket is told so and can do nothing else.
- **API keys live in the OS keychain.** On a machine with no keychain, a key
  is refused rather than written to disk.
- **Tool output is untrusted data.** Every result is wrapped and marked as
  data, not instructions; only first-party tools may opt out.
- **The database is owner-only** (folder `0700`, files `0600`) on macOS and
  Linux.
- **There is no telemetry.** No analytics or crash-reporting code exists.

## Not true yet — required before the first release

These are in the specification and are not built. Until they are, do not
rely on them:

- **The rest of the session-token lifecycle** (SPN-11, SPN-11a). The token
  is held in memory for one launch, not in the keychain, and there is no
  way to revoke it early. There is no remote access, and so no pairing
  tunnel.
- **An encrypted-file fallback for keys** (SEC-2), for machines with no
  keychain.
- **Database hardening on Windows** (SUR-3c). Windows relies on the home
  folder's own permissions for now.
- **Isolation for plugins.** In-process plugins share Trillion's privileges.
  Only first-party tools are loaded in Phase 0.

When one of these lands, it moves up to "What is true today" in the same
pull request, with the test that proves it.
