# Speaking up

**Date:** 2026-10-29 · **Requirements:** AGT-5 (attention engine), AGT-6, AGT-7, SUR-9 (the door), VOI-17, PER-11, MEM-5 · **Builds on:** `docs/specs/2026-10-24-reminders.md` (the door, notices), `docs/specs/2026-10-27-routines.md` (notes that feedback edits), `docs/specs/2026-10-28-desktop-app.md` (the floating orb)

She speaks up first. Watchers read the calendar, Gmail and Stripe on timers, and when
something needs the user she says so in one line: "Your 10:00 with Sam starts in five
minutes." She holds her voice when it would intrude: quiet hours, a meeting under way,
another app using the microphone, or the computer's Focus mode. Everything here runs in
the server, so it works in every version of the app on every operating system; the
few parts that ask the operating system something have one implementation per system
behind one interface, and say "can't tell" where a system can't answer.

## Decisions

| | Decision | Why |
|---|---|---|
| D1 | **Watchers fetch in code; the model only judges and words.** A watcher reads its source on a timer, drops what it has already seen, and hands only new candidates to one small model call with no tools. | Cheap and predictable: a full routine every ten minutes costs about fifty times more, and "already told them" would rest on the model remembering. |
| D2 | **Three watchers.** Meetings (Google Calendar), urgent email (Gmail), payments (Stripe). Meetings are on by default once Calendar is connected; email and payments are off until the user turns them on. | What the user chose. Turning email on sends mail previews (sender, subject, a short snippet) to the model provider, so it is the user's choice, said where they make it. |
| D3 | **Every spoken line is written by the model** from the find, wrapped as untrusted, with no tools offered. With no line (no model reachable, or no usable answer), the notice is shown by its generic title plus its detail on the page, and not spoken. The detail is a short line the host composes from the find's own data ("Planning with Sam at 10:00 am"): never spoken, never in the system notification, never saved as her message. | PER-11: no stock speech. No tools means nothing in an email or an invite can act. |
| D4 | **What she says from a watcher counts as read** (MEM-5), as a timed ask's answer does: a reminder or memory she makes on the strength of it is refused or kept as a claim. | An invite title or an email is someone else's words. |
| D5 | **Once.** Each find has a stable key (`meeting:<event id>:<start>`, `email:<message id>`, `payment:<object id>:<kind>`). A key is announced at most once, across restarts. Dismissing a find is a soft delete, kept for the dedup window. | AGT-5. Announcing twice trains the user to ignore her. |
| D6 | **Silence for nothing** (AGT-6): an email judged not urgent, or a check that finds nothing, writes a log line and no notice. | Unchanged from routines. |
| D7 | **Quiet holds the voice, never the notice.** When she should not speak, the notice still arrives in the conversation and as a silent system notification; it is not spoken. When quiet ends she does not read out what she held. | A held line read out an hour later is stale and startling. |
| D8 | **Four reasons to be quiet** (below), asked only at the moment she would speak. Quiet hours apply to watcher notices only; a reminder or timed ask the user set for a time is spoken at that time, since they chose it, but the other three reasons hold it too. | A 7:00 reminder inside 10 pm–8 am quiet hours was asked for at 7:00. Being on a call is a reason for everything. |
| D9 | **A watcher notice is spoken only while fresh**: a meeting heads-up until the meeting starts, an email or payment line for 30 minutes. Delivered later, it is shown, not spoken. | Speech that arrives after the fact is noise. |
| D10 | **Feedback edits the source** (AGT-7). Each watcher has notes, as a routine does. "Stop telling me about the bank newsletters" narrows at once; "also tell me when Sam emails" widens and asks first on a card. Settings shows and clears the notes. | The same rule and mechanism routines already use. |
| D11 | **Probes answer yes, no or can't tell.** "Can't tell" never holds her voice, and Settings says which checks work on this computer. A probe never raises and never blocks: each runs in a thread with a 2 s timeout and its answer is kept for 10 s. | A check that can't run on a system must not silence her there, and must not hang the door. |

## The watchers

All three run in one **attention runner** inside the server, beside the scheduler, on
a 15 s tick; each watcher has its own interval and is skipped while its connector is
not connected or its switch is off. A watcher call has a 20 s timeout; one that fails
logs its class name and tries again at its next interval (never sooner).

### Meetings

- Every 60 s, reads the primary calendar for the next 15 minutes (`events.list`,
  `singleEvents=true`, `orderBy=startTime`, `eventTypes=default`), through the Google
  client the calendar tools use, with a 15 s limit. Skips all-day events, cancelled
  ones, ones that are not plain events (focus time, out of office, working location),
  and one it can't read (the rest of the read stands).
- **Only events the user is going to count.** An event counts when the user's own
  answer is accepted or maybe (`tentative`), when they organised it
  (`organizer.self`), or when it has no guest entry for them and they made it on
  their own calendar. An invite they declined or have not answered (`needsAction`)
  never counts: no heads-up, no link, not a meeting. Anyone can put an invite on
  someone's calendar.
- A heads-up is due at **start − lead** (lead: 5 minutes, Settings: 2–30) and is
  taken up to one interval after the start, since reads land 60–75 s apart; past
  the start it is shown, not spoken (D9). The find carries the title, start, the
  names other guests gave (at most three; a guest with no name is not named, and
  their address is never sent), and a join link when the event has one
  (`hangoutLink`, else the first `video` entry point in `conferenceData`), kept only
  if it is plainly `https://` to a host (no user name or password, no spaces or
  control characters, at most 2000 characters).
- The line is written when the heads-up is due. The notice shows a **Join** button
  opening the link (in the desktop app, in the default browser).
- The notice's title is generic ("Meeting soon"); the specifics (title, time, people)
  are only in her line and in the page's detail ("<title> at 10:00 am"). Notification
  titles are generic for every watcher, since a lock screen is public and an invite
  title or a subject is someone else's words.
- **In a meeting now** (for D8) is true while an event that counts (above), timed and
  with someone else in it, is under way. Someone else is a guest who has not
  declined (named or not, a room is not), an organiser who is not the user, or a
  guest list Google left out (`attendeesOmitted`). The watcher keeps its last good
  read for this, so the quiet check costs no request: a read on its way or one that
  failed leaves it standing, and it answers only while it is three minutes fresh.
  With Calendar not connected it is "can't tell", and the read is forgotten.

### Urgent email

- Every 10 minutes, lists messages matching `is:unread is:important after:<last check>`
  plus one `from:` term per sender the notes ask to watch, at most 20, newest first.
  The first check after turning it on starts from that moment, never the backlog.
- For each new message: `From`, `Subject`, `Date` and Gmail's snippet (at most 200
  characters). Never the body, never attachments.
- One judging call per check, with the candidates and the email notes, wrapped as
  untrusted: it answers which are urgent and one line for all of them together, or
  nothing. Output that does not parse is treated as nothing (logged). At most one
  email notice per check.
- The query is `is:unread is:important after:<s>`, or with senders to watch
  `is:unread after:<s> {is:important from:a@example.com …}` (`{}` is Gmail's or), at
  most 20 (`maxResults`). Each message is read with `format=metadata` and
  `metadataHeaders` From, Subject and Date. Its place is kept in the watcher's
  cursor and moves to five minutes before each check (never back), so mail Gmail
  files a moment late is still read; one already found is never told again (D5).
- The judge answers strict JSON, `{"urgent": [<ids>], "line": "<sentence>"}`. Ids it
  was not given are ignored; urgent ones with no usable line are shown by title and
  detail, unspoken (D3). With no model there is no judging, and nothing is said.
- The detail is "<sender's name, else address> — <subject>".

### Notes

Each watcher's notes are lines of the user's words, given to its model call beside
the finds, outside the untrusted part. Two tools edit them, as `routine_narrow` and
`routine_widen` edit a routine's, with the same test (`narrows_routine`):
`speak_up_narrow` adds a plain narrowing at once ("stop telling me about
newsletters"); `speak_up_widen` adds anything else, on a card. Both take `watcher`
(meetings, email or payments) and `text`, one line; the notes hold 1,000 characters.

- A line `watch: name@example.com` asks the email watcher to read that sender's
  unread mail too, important or not. Only a plain address is taken, at most ten, so
  a note can't add other search words. It can only come through a card.
- The turn after a heads-up counts as read (D4): there a narrowing does not stand at
  once either, it goes to a card, as a standing rule does after a read. So an email
  can't change what she watches through her own line.
- Never from a run nobody watches, a tapped ask, a refresh or a channel; neither tool
  is offered to an unattended run.

### Payments

- Every 10 minutes, through the connected Stripe server's `stripe_api_read` tool, as
  `stripe_revenue.py` calls it: charges created since the last check that failed;
  successful charges at or above the limit (Settings; off until set, in the limit's
  own currency, so a charge in another currency is not compared); disputes created
  since the last check.
- Two reads, `GetCharges` and `GetDisputes`, each with `created[gte]` and
  `limit` 100, newest first, following `starting_after` for at most three pages.
  Stripe's charge list can't filter by status, so failed and large ones are picked in
  code. Its place moves to five minutes before each check (never back, never more
  than a day back); the first check after turning it on starts from that moment.
- Only when that tool runs without asking. If the user set it to ask first, the
  watcher does not read and Settings says why (its last error is `AsksFirst`; an
  unattended run gets only tools that never ask, as in the reminders spec D5). It is
  found by the `stripe_revenue` companion, which exists only while a Stripe server
  from the catalogue is connected with `stripe_api_read`, and carries that tool's risk.
- The find (`payment:<object id>:<failed|large|dispute>`) carries kind, the amount in
  major units with its currency ("125.00 USD"), when it was created in the user's
  zone, the customer's name when Stripe has one on the charge, Stripe's failure code
  or dispute reason, and the charge's description with any address taken out.
  Never an email address (a name that holds one is not taken), a card's details or
  an address. The detail is "<Failed payment|Large payment|Dispute> <amount>", with
  " — <name>" when there is one. One line per check covering everything found.

## Staying quiet

The door asks one new question before it speaks a notice: `quiet(notice, now)`,
which returns the first reason that applies, or none. The reasons:

1. **Quiet hours.** Default on, 22:00–08:00 in the user's zone (`USER.md`, as
   reminders use); Settings changes or turns it off. Watcher notices only (D8).
2. **In a meeting.** From the meetings watcher's last read. Default on; Settings turns
   it off. A heads-up for the next meeting is still spoken.
3. **Another app has the microphone** (the mic probe, below).
4. **Focus or Do Not Disturb is on** (the Focus probe, below).

A quiet notice is delivered as text with `spoken: false` and its reason, and the
door shows the system notification for it (silent: no sound on any system) even with
a page open, since the user may not be looking at the page. Those notifications are
sent after the hand-over to the page, never holding it up. The conversation shows the reason beside it in
small text ("Held: quiet hours").

## Probes, per operating system

`src/trillion/attention/probes/`: `mic.py` and `focus.py`, each with one function per
system and one entry point that picks by `sys.platform`. Each takes its system calls
as injectable functions, so every system's logic is tested on every CI runner.

**Telling Trillion's own microphone apart.** The page reports where it runs in its
hello: `host` = `desktop` or the browser family (`chrome`, `edge`, `firefox`,
`safari`, `other`). The mic probe ignores the recording processes that belong to
that host (the desktop app and WebKit's helper processes; or that browser's
processes). So when Trillion and a call share one browser, the probe can't see the
call; the meeting rule covers that case, and Settings says so. The desktop and Safari
hosts both ignore every WebKit helper process, so a call in Safari while using the
desktop app (or in any WebKit-based app while using Safari) isn't seen either; the
meeting rule covers it too.

| Probe | macOS | Windows | Linux |
|---|---|---|---|
| **Mic in use by another app** | CoreAudio through `ctypes`: the process list (`kAudioHardwarePropertyProcessObjectList`), and per process its pid, bundle id and whether it is recording input. macOS 14.2+; older → can't tell. No permission prompt. | The registry the privacy indicator reads, `HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone` (packaged apps, and `NonPackaged` by exe path): an app with `LastUsedTimeStart` set and `LastUsedTimeStop` = 0 is recording now. `winreg`, no admin. | `pactl -f json list source-outputs` (PulseAudio, and PipeWire through `pipewire-pulse`), by `application.process.binary`; without `pactl`, `pw-dump` input streams; neither → can't tell. |
| **Focus / Do Not Disturb** | Best effort: the Focus assertions file under `~/Library/DoNotDisturb/DB/` if this process can read it without extra permission; otherwise can't tell. | Best effort: can't tell unless a documented, readable signal is found in the spike (Task 2). | GNOME: `gsettings get org.gnome.desktop.notifications show-banners` = `false` → on. Otherwise the `Inhibited` property of `org.freedesktop.Notifications` over `gdbus`, where the desktop has it (KDE, others). Neither → can't tell. |

Commands run as argument lists, never through a shell, with a 2 s timeout.

## Delivery changes

- Migration `015_attention.sql`:
  - `notices.source` gains `attention`.
  - `notices.kind` gains `heads_up`.
  - New columns `link` (https URL or NULL), `fresh_until_ms` and `detail` (the page-only line, D3).
  - New table `attention_finds` (`key` unique per owner, `watcher`, `first_seen_ms`, `notice_id`, `status`: `claimed` | `told` | `silent` | `dismissed`, `dismissed_ms`, `detail`). A find is `claimed` before anything is said; it becomes `told` or `silent`. One left `claimed` by a stop or a failure is never told again (D5): at the next start it is marked `told` if its notice was written, and otherwise Settings shows it as lost.
  - New table `attention_state` (per watcher: `last_check_ms`, `cursor`, `last_error`, `notes`).
- `Delivery` gains `spoken: bool`, `quiet: str | None` (the reason), `link` and `detail`. The
  session speaks only `spoken` ones. A `heads_up` is saved as her message, like a
  reminder, and its turn is marked read (D4).
- The page's notice shows the Join button for a link (`signInTab`-style: in the app,
  `openExternal`), and the quiet reason.

## Settings → Speaking up

- A switch per watcher, with what it reads and what leaves the machine ("Sends each
  important email's sender, subject and preview to your model provider").
- Meeting lead time; the payment limit; quiet hours (on/off, from, to).
- An "In a meeting" switch (on), and "Another app is using the microphone" and
  "Focus is on" switches (both on), the last two each with this computer's probe
  status: "Works here", the reason it can't tell, or that it
  didn't answer (it ran out of time or was still busy; it may answer next time).
- Each watcher's notes, with Clear; when it last checked, and its last error by kind.
- Recent finds (14 days): told, silent, dismissed or lost, each with its detail, with Dismiss.
- Turning a watcher on starts it from that moment (D2): its last check and place are
  forgotten, its notes kept.
- API: `GET/PUT /api/attention/settings`, `GET /api/attention/finds`,
  `POST /api/attention/finds/{key}/dismiss`, `DELETE /api/attention/notes/{watcher}`.

## Cost

Each judging or wording call is background work and is recorded in usage like any
other call. When the spending caps (BRN-7/8, not yet built) arrive, these calls are
background work under them: at the hard cap the email and payment watchers pause and
Settings says so. Until then the cost is bounded by the intervals: at most one call
per watcher per check, and none when nothing is new.

## Platforms

| Part | macOS | Windows | Linux | Browser tab (any OS) | Desktop app |
|---|---|---|---|---|---|
| Watchers, dedup, notes, writing | ✓ | ✓ | ✓ | ✓ (server-side) | ✓ |
| Quiet hours, in a meeting | ✓ | ✓ | ✓ | ✓ | ✓ |
| Spoken notice | ✓ | ✓ | ✓ | while a tab is open | ✓, with the floating orb (macOS app only) |
| System notification | ✓ | ✓ | ✓ | ✓ | ✓ |
| Mic probe | ✓ macOS 14.2+ | ✓ | ✓ with `pactl` or `pw-dump` | ✓, except a call in the same browser | ✓ |
| Focus probe | best effort | best effort | ✓ GNOME, KDE | as the OS | as the OS |

## Out of scope

A daily digest of smaller finds (a routine can do it), Slack and other mail services,
calendars other than the primary Google one, the desktop app on Windows and Linux
(planned in `docs/platforms.md`), the spending caps themselves.

## Testing

- **Python:**
  - Each watcher against a fake clock and fake Calendar, Gmail and Stripe.
  - Once, including after a restart (D5); silence for nothing (D6).
  - Each quiet reason holds speech but shows the notice and notifies (D7, D8).
  - Freshness (D9); notes narrowing stands at once, widening asks first (D10).
  - An email and an invite title carrying instructions can do nothing, and a reminder made from them is refused (D3, D4).
  - Every spoken string comes from the model (PER-11).
  - Probes: every system's parser on recorded outputs, run on every runner; the real probe on each CI runner returns an answer, never raises, within its timeout.
- **Web:** the Speaking up panel; the Join button and quiet reason on a notice; the page's `host` in its hello.
- **Manual** (`docs/TESTING.md`): a heads-up arrives and is spoken with the window hidden; on a Meet call she holds it; quiet hours; urgent email; a failed test-mode Stripe charge; on Windows and Linux, the mic probe status in Settings.
