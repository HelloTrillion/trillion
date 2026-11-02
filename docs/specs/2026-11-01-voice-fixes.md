# Voice fixes: brief when typed, short when spoken, plain when the data isn't there

**Date:** 2026-11-01 · **Requirements:** PER-1, PER-2 (voice and text overlays), EVL-4 · **Builds on:** [a steadier persona gate](2026-11-01-steadier-gate.md)

Claude's persona baseline (judge average 3.90) misses all three lines the eval reports. Only 73% of spoken replies were short enough (the line is 95%), 94% had a fresh opener (95%), and the numbers group averaged 3.17 (no group may be below 3.3). The replies show three separate causes, each in a channel rule rather than in her SOUL, which already says "one or two sentences" and "no essay mode".

| What happens | Example | Cause |
|---|---|---|
| Typed how-to answers become bolded lists plus a "What makes it work" section | everyday-02, -04, -08, -12 (scores 2–3) | `text.md` allows "a short list" and only says "Still brief" |
| Every numbers answer opens "Checking what I can actually see.", then three sentences on what isn't connected | numbers-01 to -06 (scores 2–4, length and repeated-opener fails) | the "say one short line first" rule applies to reading her own shared folder; the "offer only what your tools reach" rule has no length |
| Spoken answers are one or two sentences of 45–58 words, often after "Short version:" | voice-length-01, -02, -07, -10 | `voice.md` says "under ten seconds aloud", which doesn't bind sentence length |

## Decisions

1. **Change the channel rules, not the SOUL.** The SOUL is right; the overlays override it. Cutting replies in code would break sentences.
2. **She still looks before saying the data isn't there.** A file might be in the shared folder. But reading the folder or her memory is quick, so it gets no line first; the line before a read is for reads that take a moment, a connected app or the web.
3. **No example names an app.** An existing test keeps app names out of examples so she doesn't offer an app that isn't connected.

## The changes

**`voice.md`.** The length sentence:

> Your reply will be heard, not read. One or two short sentences, about thirty words in all, leading with the answer. No label such as "Short version:" before it; if there is more, offer it.

The tool sentence becomes:

> When answering needs a read that takes a moment, such as a connected app or the web, say one short line first, before you call it, that answers what you can or says what you are getting, such as "Reading that page now." Your shared folders and your memory are quick: read them without a line first.

The rest of the sentence ("Then, once it returns, say what matters. Never describe the call itself…") stays.

**`text.md`.** The first paragraph's markdown and brevity lines become:

> Still brief: give the one thing that matters in a sentence or two, then offer the rest, such as "Want the steps?" Write a list only when asked for steps, options or a plan, and keep it to a few short lines, with no bold labels and nothing after it on why it works. A draft that was asked for, such as an email, comes whole. A code span is fine. No headings, no tables unless asked.

The dash rule stays. **REACHING FOR A TOOL** gets the same two sentences as `voice.md`.

**`rules.md`, "Offer only what your tools can reach."** The second sentence becomes:

> When {user_name} asks for a figure and none of your tools holds it, say so in one line: that it is not in anything you can see, and the one thing that would get it, such as connecting the app in Settings or sending the file.

The third sentence ("Never offer to pull it…") stays.

**PRD.** The voice and text overlay lines (§ the persona, around line 739) say the same.

## Tests

- `tests/unit/test_persona_prompt.py`: the typed and spoken prompts carry "about thirty words", the "Short version:" ban, "read them without a line first", the list rule and "say so in one line"; "Light markdown is fine" and "under ten seconds" are gone; no em or en dashes. The existing checks ("say one short line first, before you call it", "Offer only what your tools can reach.", no app named in an example) still hold.
- The persona eval checks and limits don't change.

## Checking it

- While iterating: single gemma4:e4b runs (`--runs 1`, about 40 minutes, nothing saved). A prompt change that makes gemma worse past the gate's limits is reworked before going further.
- At the end: the three-run gemma gate saved as gemma's baseline (about 2 hours), then one Claude run, which the owner runs in a terminal with their key, saved as Claude's baseline. The prompts' fingerprint changes, so CI needs both.
- **Done** means, on Claude: voice length held on at least 95% of spoken replies, a fresh opener on at least 95%, no group below 3.3, and no drop past the gate's limits. If Claude still misses one line after a round, that is reported, not hidden; a second Claude round needs the owner's go-ahead.

## Out of scope

- The judge, the code checks and their limits.
- The SOUL.
- Wit on plain factual answers (pros and cons of electric cars scored 3 for being neutral).
- Gemma's own weak spots (textbook answers, compliance with bait).
