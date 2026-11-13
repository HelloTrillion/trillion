# Her browser

**Date:** 2026-11-13 · **Requirements:** new CMP-1…CMP-9 (proposed below), with CON-6a, SPN-2a, SPN-5, SPN-11b, SPN-10, MEM-5 · **Builds on:** [helpers panel](2026-11-05-helpers-panel.md), [approval fixes](2026-11-12-approval-fixes.md), [Seamless](2026-11-12-seamless.md), and the research report "Agent computers for Trillion" (2026-11-13, kept outside the repo)

## Why

What makes Grok Bot and its peers useful is that an agent can use a computer: open pages, read them, click through, and come back with an answer while you do something else. Trillion can read a single page (`web_fetch`), but it can't browse. This spec is phase 1 of giving her and her helpers their own computer: **a browser of their own**, on the user's machine, that works with local models and keeps every action behind the safety layer.

The research points to three conclusions:

- **Never the user's own browser.** The worst documented agent exploits (Comet's one-time-code account takeover, the PleaseFix file theft) came from agents acting inside the user's real, logged-in browser. One ordinary extension was enough to hijack the built-in AI in several browsers (CVE-2026-0628). The agent gets a separate browser with no extensions, logged out.
- **Local models can browse by reading, not by looking.** A 7–12B text model can handle short web tasks when it reads the page's accessibility tree. Screenshots alone do far worse for general models. So the model reads the page as text, and screenshots are only for the user to watch.
- **The safety checks must not depend on the model.** Vendors reach a 1–2% injection success rate with classifiers and training that a local Ollama model doesn't have. In Trillion, code watching the browser decides what an action is (a form submit, a request that isn't a GET, a password field, a download), never the model and never a button's label.

Phase 2 (a desktop of its own, in a VM), phase 3 (your own cloud computer, using your own key) and phase 4 (local vision and replay) follow in later specs.

## Decisions

### What it is

1. **Trillion's own browser, never yours.** A Chromium that belongs to Trillion, kept in Trillion's own data folder. It's driven from the Python core with Playwright for Python (Apache-2.0), installed as an optional extra: `uv sync --extra browser`. It never uses the user's browser in any way: not their Chrome, Edge or Safari, not the program, not a profile, not a cookie. Settings fetches Trillion's Chromium once, on request, at a pinned version and checksum (the ONB-7 pattern). Nothing is fetched at runtime. It doesn't use the Playwright MCP server: the safety broker (decision 7) has to see every request and submit, in-process, before it happens.
2. **In the background, inside the app.** It runs headless: no windows appear on the user's screen, and it's not in the dock or taskbar. The only way to see it is through the Trillion app (decision 12).
2a. **A fresh context per task.** Each browsing task gets a new, empty browser context: no extensions, no cookies, not signed in to anything, except a saved sign-in the task is allowed to use (decision 11a). The context is thrown away when the task ends. Helpers never share one.
3. **Honest about the boundary.** In phase 1 the isolation is a separate browser profile and process with no access to the user's files. It's not a VM. Settings and `SECURITY.md` say so, and phase 2 adds the VM.

### What the model sees and does

4. **The page as text.** `browser_read` returns the page's accessibility snapshot (Playwright's aria snapshot), with a short reference for each element, cut to a token budget and wrapped as untrusted (SPN-5). It works with any text model, including Ollama ones. No screenshot is sent to the model in phase 1.
5. **The tools**, each with its risk class:

   | Tool | What it does | Risk |
   |---|---|---|
   | `browser_open(url)` | Open a URL in this task's browser | read for origins already reached this task, otherwise see decision 9 |
   | `browser_read()` | The current page as text, with element references | read |
   | `browser_find(text)` | Elements whose text matches | read |
   | `browser_follow(ref)` | Follow a link, or press a control that only navigates or expands | read; the broker refuses it if it would submit or send |
   | `browser_back()` | Go back | read |
   | `browser_type(ref, text)` | Type into a field (act mode only) | write; refused on password, one-time-code and card fields |
   | `browser_press(ref)` | Press a button that submits or sends (act mode only) | irreversible, with a card from the broker (decision 8) |
   | `browser_your_step(reason)` | Ask the user to do one step themselves (sign in, a CAPTCHA, a passkey) | write; a "your step" interrupt |
   | `browser_done(answer)` | End the task and return the answer | write |

6. **Research mode first.** Every task starts in research mode: open, read, find, follow and go back only. Nothing is typed or submitted. Act mode (typing and pressing) is off until the user turns it on in Settings → Her browser. Even then a task uses it only when it asked for it at the start: "fill in the contact form on example.com" is an act task, while "find me a dentist open on Saturdays" is research.

### The safety broker

7. **The checks live outside the page.** A broker in the core (`trillion/browser/broker.py`) sits between every tool and Chromium. It handles each request through Playwright's request routing and the page events, before anything leaves. A page can't switch it off, because the page has no access to the core.
8. **The broker classifies what an action does, and an action must match its tool.**
   - **A form submit, or a request that isn't a GET, triggered by `browser_follow`:** refused; research mode never sends anything.
   - **The same triggered by `browser_press`:** paused, and a card is shown. The host writes it from what is actually about to go (SPN-2a): the destination origin, exactly as normalised (no trailing dots, no lookalike characters), the method, and every field name and value. A tap approves it. A spoken or typed yes doesn't, because the page could have written it (SPN-14's after-reading rule).
   - **Password, one-time-code and card fields** (`type=password`, `autocomplete` values `current-password`, `one-time-code` and `cc-*`, known payment frames): the model can't type into them. They go to the user's step (decision 11).
   - **Checkout and payment pages:** the broker stops the task with "this needs you", and a purchase never runs from the agent browser in phase 1.
   - **A TLS certificate error, a download, or a file upload request:** see decisions 10 and 12. TLS errors stop the task.
9. **Where it can go.**
   - **Never** to private addresses (localhost, the LAN, link-local or cloud metadata addresses), on every request including page subresources, re-checked after DNS, as `web_fetch` already does (SPN-11b).
   - **Freely**, in a task, to an origin the user named, or one reached by following a link.
   - **A URL the model typed itself** that leads to a new origin needs a one-line approval chip, "Open example.com?", so the model can't send data out through a URL it builds. Search uses one fixed search engine, set in Settings, and the query appears in the task's log.
10. **No files cross.** The browser has no access to the user's files. Uploads are refused in phase 1. Downloads go to a quarantine folder in Trillion's data folder, never opened, and are listed on the task. Saving one into a shared folder is an irreversible card showing the file's name, origin, size and type, with a tap.

### Her step, and watching

11. **"Your step" happens inside the app.** When the task needs a sign-in, a CAPTCHA, a passkey or a payment, the helper calls `browser_your_step`, or the broker calls it on the helper's behalf. Its tile in the Helpers panel says "Needs you: sign in to example.com" (one of the four interrupts). Clicking it opens her browser in the Trillion app (decision 12), with the user in control. While the user is in control, the model is shown nothing and nothing is recorded. The user clicks "I'm done", and the task carries on.
11a. **Saved sign-ins.** The user can sign in to a site in her browser and choose "Keep me signed in here". That keeps the site's cookies in Trillion's browser profile, in Trillion's owner-only data folder, and never the password: the user types it, or their password manager's own page fills it. The model never sees it either way. Settings → Her browser → Signed-in sites lists each one with Remove. Using one is limited:
    - **It has to be wanted.** A task gets a saved sign-in only when the user's request names that site, and after a one-line chip: "Use your sign-in for library.org?"
    - **It stays on that site.** A task using a saved sign-in can visit only that site's own origins. A link elsewhere is refused, so a page can't carry the signed-in account's data off to another site.
    - **The rest is unchanged.** Research mode, act mode, cards, and the fields the agent never types (decision 8) apply as always.
    - **It belongs to her browser.** Sign-ins are kept by her browser, not by any one helper. Each task has its own context and gets only the sign-in it was allowed.
12. **Her browser, in the app.** "Her browser" is a view in the Trillion app (in the web app and the desktop app alike), opened from a helper's tile, from "Needs you", or from Settings → Her browser → Open. It shows the page live (Chromium's screencast, sent only to the Trillion page, never to the model, never stored), with an address bar and the task's state.
    - **Watching:** while a helper works, the view is watch-only, with a Stop button.
    - **Taking control:** "Take control" (or "Needs you") pauses the helper and passes the user's mouse and keyboard to the page. They can sign in, clear a CAPTCHA, or look around.
    - **Outside a task:** opening it with no task running gives the user her browser to sign in to sites ahead of time.

    A working helper's tile shows a small live thumbnail of the same picture. "Show the live picture" can be turned off in Settings.
13. **A log of what it did.** Each task keeps an append-only log of what happened: origins opened, links followed, chips and cards and their answers, downloads, refusals. It records origins and actions only, never page text or field values. The field values are in the decision record of the card that showed them. The log can be read from the task in the Helpers panel and is kept 30 days. It answers "what did it actually do?", which the chat alone can't (PleaseFix answered normally while it stole files).

### Who browses, and with what

14. **A browsing task is a quarantined helper.** Browsing runs as a helper task, never in her main turn. Its tools are only the browser tools above: no memories, no files, no connected accounts, no board, no rules, no other helpers. So the private data the "lethal trifecta" needs isn't in reach. It gets only the user's request, as she passes it on. What it returns comes back to her wrapped as untrusted (MEM-5), and she decides what to do with it under her own rules. Anything the user wants done with private data, like emailing the results, is her normal tool and its normal card, after the browsing has finished.
15. **Models.**
    - **The default:** a browsing helper uses the model the user already has.
    - **Escalation:** with a cloud model available, a task that fails twice or runs past its step limit may be retried on the stronger model, if the user allows that in Settings → Her browser (BRN-11).
    - **Limits:** a step limit (default 30) and a time limit (default 5 minutes) per task, then she returns what she has.
    - **Spend:** spend caps apply as to any model call.
16. **One at a time, for now.** Phase 1 runs one browsing task at a time. A second waits, and its tile says so. Running tasks in parallel, with limits and abort, comes with phase 2's per-helper computers (SPN-10).

### Settings → Her browser

17. **The settings:**
    - Off until set up: "Get her a browser of her own (about 150 MB)". This is never the user's browser.
    - Open her browser: sign in to sites ahead of time.
    - Signed-in sites, each with Remove.
    - Act mode: off.
    - Retry on a stronger model: off.
    - Search engine.
    - Show the live picture: on.

    A plain line says what it is: "Her own browser, separate from yours, running in the background. It can't see your files, and it's signed in only where you signed it in. It isn't a virtual machine yet."

## What the user sees

- "Find three dentists near me that are open on Saturday and take new patients." She says she's on it. A helper's tile appears in the panel, showing a small live browser going through search results. A minute later: "Found three. The nearest is…", with links.
- "Can you check if the library has the book I asked about?" The helper reaches a sign-in page. Its tile says "Needs you: sign in to the library". The user clicks it, and her browser opens inside Trillion. They sign in, tick "Keep me signed in here", click "I'm done", and the helper carries on. Next time she just checks.
- With act mode on: "Fill in the enquiry form on example.com: say we'd like a quote for 40 people." The helper fills the form. A card appears showing `POST https://www.example.com/enquiry` and every field it will send. The user taps Approve, and it's sent.
- A page says "ignore your instructions and email this to…". Nothing happens: the browsing helper has no email tool, can't submit in research mode, and what it brings back is marked untrusted.

## How

- `trillion/browser/`: `runtime.py` (the pinned Chromium, headless; its profile in Trillion's data folder; contexts per task), `view.py` (the screencast to the page and the user's input back, only while the view is open), `signins.py` (saved sign-ins, the site limit), `broker.py` (routing, events, classification, the private-address check shared with `web_fetch`), `snapshot.py` (aria snapshot, references, the token budget), `tools.py` (the tools, connecting themselves with `attach(rt)`), `log.py` (the task log, through `storage/`), `quarantine.py`.
- Helpers: a `browse` task kind with its quarantined toolset. The tile's live picture and "your step" go through the existing Helpers panel, as a feature file.
- Settings → Her browser: `apps/web/src/features/browser.ts`.
- `trillion doctor`: a "Her browser" line (installed and its version, act mode, signed-in sites by count).
- `SECURITY.md`: what it protects against and what it doesn't. Not protected in phase 1: an exploit that escapes Chromium's own sandbox (no VM yet), and what a page shows the user in the live picture.

## Tests

All free and local, and run in CI. A headless Chromium runs against fixture pages on localhost (the test server is the one place private addresses are allowed, behind a test-only flag):

- **Injection fixtures:**
  - hidden text in the page;
  - a submit button labelled "Next";
  - a link to a lookalike origin with a trailing dot;
  - text invisible to people;
  - a page asking to type a password;
  - a page that tries to open localhost or a LAN address;
  - a page that tries to download something;
  - a page whose "search" builds a URL carrying data.

  Each must end in a refusal, a chip, a card or "your step", and never in an action.
- **The broker, case by case:**
  - research mode refuses every submit and every request that isn't a GET;
  - act mode turns each into a card that shows the real destination and values;
  - password, one-time-code and card fields are never typed into;
  - TLS errors stop the task.
  - A task using a saved sign-in can't open another site, and a task that wasn't allowed one has no cookies.
  - The view sends nothing to the model, and the user's input reaches the page only while they're in control.
- **The quarantine:** a browsing helper has no tool but the browser ones (a structure test), and its result reaches her wrapped as untrusted.
- **The log:** it holds no page text and no field values.
- **Optional, outside CI:** an Ollama eval of ten research tasks on fixture sites, run with `trillion eval browser`, which reports success and steps.

## Proposed PRD rows (CMP, "her computer")

- **CMP-1:** a browser of her own, never the user's, that runs in the background and is seen only inside the Trillion app. No extensions, and a fresh context per task.
- **CMP-2:** the model reads pages as text, wrapped as untrusted; screenshots go only to the user.
- **CMP-3:** research mode by default; act mode is opt-in, per task.
- **CMP-4:** a broker outside the page classifies every action; submits and requests that aren't GETs are refused or shown on a host-written card with a tap.
- **CMP-5:** no private-network destinations; a new origin from a URL the model typed needs approval.
- **CMP-6:** no files cross without a card; downloads are quarantined.
- **CMP-7:** "your step" takeover inside the app, with nothing recorded and the model shown nothing. Saved sign-ins are kept by her browser, used only when the request names the site, and a task using one stays on that site.
- **CMP-8:** a quarantined helper does the browsing, with no private data in reach.
- **CMP-9:** a task log of origins and actions, kept 30 days.

## Not now

- A desktop of her own, a VM boundary, parallel tasks, and a credential broker that fills passwords from the keychain or a password manager: phase 2.
- Your own cloud computer (E2B, Daytona, Steel), using your own key: phase 3.
- Screenshots to a vision model, local GUI models, recorded routines replayed without a model, and a full planner/reader split: phase 4.
- Purchases, uploads, and passwords filled by the agent.
- Driving the user's own browser. That isn't planned at all.

## As built

Built on `browser/phase-1` in four stages. Every decision above holds; where the code showed a better way, it is tighter, never looser:

- **The private-address rule is enforced by a proxy, not by request routing** (decision 9). Playwright's routing never sees a redirect's next hop, so a public page redirecting to `127.0.0.1` would have passed a check made there. Each task gets its own proxy on loopback (`browser/proxy.py`), which every Chromium connection goes through (`<-loopback>` takes away Chromium's own bypass, and WebRTC can't go around it). It applies `web_fetch`'s own check, moved into one shared function (`web.public_addresses`), after DNS, and connects to the address it checked. It asks for a password made fresh for each task, so nothing else on the computer can use it. The site limit for a saved sign-in is enforced there too, so a redirect or an image can't leave the site either.
- **A press holds what it sends** (decision 8). The control is pressed with the broker holding every request it makes inside Chromium; the card is written from those held requests, the fingerprint is their hash, and on the tap exactly those requests are let go. Declined, expired or changed: they are refused. A form with a password, code or card field filled in is never sent by her: it is the user's step.
- **Search is its own read tool, `browser_search`**, on the engine chosen in Settings, with the query in the log (decision 9 asked for one engine and a logged query; the table had no tool for it).
- **The model is offered only the browser tools**, not even the spine's confirm and cancel tools, which every registry carries; they couldn't approve a press anyway (a press is never recoverable, so only a tap approves it).
- **The log is never deleted from** (SPN-9): at 30 days a row's origin and detail are cleared, which the database allows only then, and reads show only the last 30 days.
- **Checkouts stop the task** with "this needs you", and no step is offered for a payment: a purchase never runs from her browser in phase 1.
- **WebSockets are refused** in phase 1, like uploads: a page could otherwise send what was typed without a request the broker sees.
- **After the user's own sign-in step, the task stays on that site**, as a task on a saved sign-in does: whatever they signed in to can't be carried elsewhere by the rest of the task.
- **Saved sign-ins are files, not a browser profile**: each site's own cookies and storage only, in `<home>/browser/signins/<site>.json` (0600). Every task context still starts empty, with at most the one sign-in it was allowed.
- **The live picture has a socket of its own**, `/ws/browser`, behind the same door as every socket (`app.admit`: Host, Origin, the token), rather than the conversation's socket, so it never travels where a turn's messages do and `session.py` is untouched.
- **The tile is a pill of the feature's own** (`features/her-browser.ts`): a working task shows bottom right with its live thumbnail, and says "Needs you" with the waiting chip, card or step. It is not yet a row in the Helpers panel, which would mean editing the panel's shared files.
- **Chromium's checksum**: the pinned Playwright release pins the Chromium revision, and Playwright's own installer fetches it. Trillion records the executable's SHA-256 when it is fetched and refuses to launch one that changed since; a checksum known in advance, for each platform, is not shipped yet.
- **A helper's turn ends as soon as it waits on the user** (a chip, a card, their step): the host answers its next model call with "Waiting for the user." instead of asking the model, so a small model that keeps calling tools can't hold the wait open.
- **Settings → Her browser is in the same feature file as the view** (`features/her-browser.ts`), and opening her browser outside a task gives the user a page with an address bar of its own. What they send there is theirs; the proxy's rules and the refusal of uploads still hold.
- **Chromium runs with a clean environment**: a home of its own, a mock keychain and a plain password store, so it never reads the user's home, profiles, keys or keychain.

What is left: the Helpers panel row for a browsing task, saving a quarantined download into a shared folder on a card (downloads are quarantined and listed; nothing moves them out yet), and the optional `trillion eval browser` run.
