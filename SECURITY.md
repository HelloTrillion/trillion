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
  confirmation card. Anything that reaches other people counts as one that
  can't be undone: sending an email, adding, changing or deleting a calendar
  event (Google emails its guests), posting in Slack, and every tool of a
  connected MCP server that asks first (Trillion can't know what it does).
- The card shows everything the action sends, exactly as it will go: every
  recipient, the subject and the whole text, beneath a one-line summary. A
  card that couldn't show all of it, or couldn't show it unmistakably (an
  invisible or direction-changing character, an address in another alphabet),
  is refused before it is shown. A tap or a yes approves what the card shows.
- A yes that isn't a tap is refused on a turn that read something from outside
  (a page, an email, a file), and for any card but the newest one on screen.
  Only the card's own tap approves then, so text she read can't say the yes,
  and can't point a yes at an older card.
- A spoken "yes" is **not** tied to who said it. Anyone in the room, or a
  recording, can confirm an action that is allowed to be confirmed by voice,
  unless you teach her your voice (next section).

If you find a way around the first four, that is a vulnerability. The last
is a known limit of this phase.

### Once you teach her your voice (§5.3a, optional)

Settings → Your voice records three short phrases and keeps a voiceprint in
`voiceprint.json` in Trillion's data folder (owner-only, never sent anywhere,
unreadable by any tool). It needs the optional `speaker` install and a model
the user downloads from that screen. Once a voice is enrolled:

- A spoken yes approves a card only if the turn's voice matched the enrolled
  voice. A voice that is not quite clear is asked to say it again, then to
  tap; another voice can't approve at all.
- A turn in another voice is **default-private**: no memory, file, folder,
  connected account, board, reminder, rule or helper tool runs for it (the
  router refuses them; only `web_fetch` and `stay_quiet` run), no memory or
  card wording rides with its words, and the shared folders are not named.
- A recording of something she already heard in the owner's voice (an
  enrolment phrase or a recent turn) is refused as a replay.
- Taps and typed messages are unchanged: they come from the session.
- If `voiceprint.json` is there but can't be read (damaged, or its permissions
  don't let Trillion open it), it **fails closed**: no voice can be checked,
  so every spoken turn is treated as anyone's. No spoken yes approves a card
  (a tap does), and nothing personal runs for a spoken turn. Settings → Your
  voice, a security notice and `trillion doctor` say so. The file is never
  deleted or rewritten on its own: teaching her again replaces it, and
  forgetting the voice deletes it.

What it does **not** protect against, by design:

- **A good voice clone.** The model scores how alike two voices sound. A
  synthetic voice that sounds enough like the owner passes. There is no
  liveness or anti-spoofing model.
- **A recording she never heard.** The replay check only knows sounds she
  heard herself. The owner's yes recorded elsewhere and played to her is
  judged like a live voice, and passes if it sounds like the owner.
- **The conversation so far.** Earlier turns stay in her context. On a
  stranger's turn she is told not to repeat them, but that is an instruction,
  not code.
- **Anyone at the keyboard.** A typed message and a tap are the session's,
  as in Phase 0.
- **A deleted voiceprint.** Someone who can delete `voiceprint.json` from
  Trillion's data folder turns the check off from the next start: a missing
  file is nobody enrolled. Whoever can do that can also replace the file.

If a voice that isn't enrolled gets a spoken yes through on a card, or a tool
outside the guest list runs on its turn, that is a vulnerability. So is any
spoken yes that approves while the voiceprint can't be read.

### Your own words as the yes (SPN-20, optional, off by default)

With a voice enrolled, the owner can turn on "Let my own words approve things
I can undo" in Settings → Your voice. Then keeping a standing rule, or dropping
a rule or forgetting a memory named by its words, runs in the turn it was said,
with no second turn, but only when code finds all of these: the turn was spoken
(not typed, a tap, a host note, a helper's chat or a routine); the voice was
judged the enrolled owner's; nothing from outside (a page, a file, a connected
account's mail) was read in that turn; the tool is one of those three, which
are Trillion's own and can be undone; every argument that says what it does
appears word for word in that turn's committed transcript; and no other action
took this path in that turn. It trusts two things the host supplies, never the
model: the speaker check above and the speech-to-text transcript. Beyond the
limits of the speaker check, it does not protect against **a speech-to-text
mistake that still makes sense** (a misheard word is kept in a rule as heard),
nor against **words said with another meaning** ("don't keep a rule that says
never book anything before nine" holds the rule word for word). Both are why
only things that can be undone qualify: the action is recorded as approved by
the owner's words, shown on a settled card, and undone by asking for its
opposite. Spending, sending, anything unrecoverable, and every connector, MCP
server, skill and plugin tool always keep the second turn; the registry refuses
any of them that asks for this path. If an action takes this path without all
of those holding, that is a vulnerability.

### Her browser (optional, off until set up)

Her browser is a headless Chromium of Trillion's own, fetched on request into
Trillion's data folder at the revision the locked Playwright release pins, and
launched only from there, with Chromium's sandbox on. It is never the user's
browser: not their Chrome, Edge or Safari, not a profile, not a cookie, not
the keychain (it runs with a mock keychain, a clean environment and a home of
its own). Each browsing task gets a fresh, empty context, closed when it ends.

What it protects against, enforced in code outside the page, never by the
model or a button's label:

- **Reaching this computer or its network.** Every connection goes through a
  proxy of the task's own that applies `web_fetch`'s public-address check after
  DNS and connects to the address it checked: subresources, redirects and
  loopback included. Only the standard web ports. WebRTC can't go around it.
- **Sending anything the user didn't approve.** In research mode every request
  that isn't a GET is refused, and a control that submits a form is refused
  before it is pressed. In act mode (off by default) a press holds what it
  sends inside Chromium; the card is written from those held requests (the
  destination normalised, the method, every field) and only a tap on it lets
  exactly those go.
- **Passwords, codes and card details.** The agent never types in such a
  field, and a form holding one is the user's to send. Checkouts and payment
  pages stop the task. TLS errors stop the task. Uploads and WebSockets are
  refused. Downloads go to an owner-only quarantine, never opened.
- **Private data leaving through a page.** Browsing runs as a helper with only
  the browser tools: no memories, files, accounts, board, rules or helpers.
  What it brings back reaches her wrapped as untrusted. A saved sign-in is
  used only when the user's request names its site, after a chip, and that
  task can reach only that site's own hosts.
- **Silent action.** Each task keeps a log of origins and actions, never page
  text or field values, for 30 days.

**The Brave Search API (optional, off unless chosen).** If the user picks
"Brave Search API (your key)" as her search engine and saves a key, her
searches go from Trillion's own process, not her browser, to one fixed
address, `https://api.search.brave.com/res/v1/web/search`, never one the
model chose. Each sends only the query (words from the user's request) and
the key, in a header. Saving a key sends one search of a fixed word to check
it. The key goes from Settings → Her browser straight into the system keychain
(`trillion/brave_search/api_key`); it is never written to a file, the database
or a log, never shown again (the app learns only that one is saved), never
returned to the model, and never in an error. It never enters her browser.
With no key, a refused key, the plan's monthly limit or no answer, the search
falls back once to Bing in her browser and the log says why; the API is not
retried. Nothing is sent to Brave unless the user chose this engine and saved
a key.

What it does **not** protect against in phase 1: **an exploit that escapes
Chromium's own sandbox** (this is a separate browser process and profile, not
a virtual machine yet), and **what a page shows the user** in the live picture
or while they have control (a page can still try to trick a person). A model
that is told by a page to search for something can put words from the user's
request into the query sent to the search engine the user chose; it has
nothing else of theirs to put there. The same holds for addresses: a public
site that search results or a page's links showed opens without asking, and
with "Let her open any site without asking" on (off by default), any public
site does, so words from the request can ride in an address there too. If her browser reaches a private address,
sends a request in research mode, or lets a press send anything other than
what its card showed, that is a vulnerability.

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
  Every MCP tool asks first unless the user marked it to run without asking,
  and one that asks is approved only by a tap on its card, never a spoken or
  typed yes; a server's own claim that a tool only reads is shown, never trusted. What an
  MCP tool returns is wrapped as untrusted, and a turn that read it is treated
  like one that read a web page. An argument that may hold a path is checked
  by the deny floor wherever it points: keys, the keychain, `.ssh`, `.git`,
  `.env` files and Trillion's own folder are refused (SEC-10). Keys a server
  needs are read from the keychain by name (`keychain:NAME`), so none has to
  sit in the file. A server may name only its own keys, saved under
  `mcp/<its name>/`: an entry that names another server's key or one of
  Trillion's own is refused, never started, and the key is never read. A key
  pasted into Settings goes straight to the keychain.
  Signing in to a remote server (OAuth with PKCE) happens in the user's
  browser, and the sign-in is kept in the keychain. The browser comes back to
  `/mcp/callback`, the one route that needs no session token: it only hands a
  code to a sign-in already waiting for that exact random state, which the MCP
  library checks again before using it. Removing a server deletes its own keys
  and its sign-in, never another's. **The limit:** a local MCP server is a program running on
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
  Slack are `irreversible` and always ask, and the user cannot change that;
  they reach other people, so only a tap on the card approves them, never a
  spoken or typed yes.
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
  `%LOCALAPPDATA%` or `$XDG_DATA_HOME` has been moved. On Windows a
  `\\?\` long path is read as its plain drive path first, trailing dots and
  spaces in names are ignored as Windows ignores them, and device paths
  (`\\.\`), volume paths, admin shares (`C$`) and shares of this computer
  itself are refused outright. This computer is recognised by any spelling:
  `localhost` names, loopback and unspecified addresses in any form
  (`2130706433`, `0x7f000001`, `127.1`, `[::1]`), and the machine's own host
  name, full name, NetBIOS name and addresses. Proven by
  `tests/spine/test_credential_stores.py` and
  `tests/unit/test_deny_floor_rules.py`.
- **Logs never carry a credential** (SEC-5). From the start of `trillion run`,
  every log record is scrubbed before it is written: its message, its
  traceback, its stack text and any `extra=` fields. Scrubbing works by the shapes providers issue
  (Anthropic, OpenAI, Slack, Google, Stripe, Telegram, GitHub, ElevenLabs,
  AWS, JWTs, bearer headers), and by value. The values currently stored in
  the keychain, for every connector, including the secret fields inside a
  stored sign-in, are held in memory only for log redaction: a value
  replaced or removed is dropped from that list. Environment variables named
  like credentials and the session token are scrubbed too. A key fetched
  late, or with no known shape, is still caught. Proven by
  `tests/unit/test_secrets.py`.
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
- **API keys live in the OS keychain**, or, only if the user chose it in
  `trillion setup`, in one encrypted file (SEC-2). On a machine with neither, a
  key is refused rather than written to disk. The file, `secrets.age` in
  Trillion's folder, is AES-256-GCM (Trillion's own documented container, not
  the age format) with a random data key, wrapped by a key kept in the keychain
  when one works, otherwise by one derived from the user's passphrase with
  Argon2id. It is written whole through a temporary file, is `0600`, and is
  refused if anyone else can read it or it is a link. A wrong passphrase or a
  changed byte is an error that leaves the file as it is, and a locked file
  starts the app without keys rather than not at all. Keys are never moved
  between the keychain and the file. Proven by `tests/unit/test_secret_file.py`
  and `tests/spine/test_secret_file_floor.py`.
- **`TRILLION_SECRETS_PASSPHRASE` is a risk you take on knowingly.** It unlocks
  the key file for unattended runs. The CLI removes it from its environment at
  once, so nothing Trillion starts inherits it, and it is never logged or
  stored; but while it is set, other programs running as you may read it, and
  it may sit in a service file. Prefer typing the passphrase, or a keychain.
- **Tool output is untrusted data.** Every result is wrapped and marked as
  data, not instructions; only first-party tools may opt out.
- **The database is owner-only**: folder `0700` and files `0600` on macOS and
  Linux; on Windows the data folder, the database and the key file get an
  access list of their own, for the user and SYSTEM only, with nothing
  inherited (SUR-3c). Proven by `tests/unit/test_windows_hardening.py` and
  `tests/unit/test_migrate.py` on Windows.
- **There is no telemetry.** No analytics or crash-reporting code exists.

## Not true yet — required before the first release

These are in the specification and are not built. Until they are, do not
rely on them:

- **The rest of the session-token lifecycle** (SPN-11, SPN-11a). The token
  lives in memory for one launch by design and is never written down, but
  there is no way to revoke it early. There is no remote access, and so no pairing
  tunnel.
- **Isolation for plugins.** In-process plugins share Trillion's privileges.
  What exists is a policy gate, not a sandbox: `ToolRegistry.discover` imports
  only the `trillion.tools` entry points of Trillion's own distribution, and
  skips any other installed package's before importing it, logging only its
  name (CON-6a, `tests/unit/test_registry.py`; `trillion doctor` counts them).
  A package installed into Trillion's environment can still run code in other
  ways (a `.pth` file, an import hook, or being imported by something else),
  so install nothing there you don't trust.

When one of these lands, it moves up to "What is true today" in the same
pull request, with the test that proves it.
