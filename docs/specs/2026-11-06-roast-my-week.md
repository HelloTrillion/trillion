# Roast my week

**Status:** built, 2026-11-06. PRD: PER-14.

## Why

Her personality is dry, deadpan and honest. A roast is that personality at its most
quotable, and Trillion can do one that a generic chatbot can't: it knows the user's actual
week, from this machine, and every line can be true. The share card puts the orb on every
screenshot people post.

## What the user sees

1. They say or type "roast my week" (or "roast me").
2. A chip reads *Reading your week …*, then *Read your week*, and the orb flares.
3. She roasts the week in four to six short sentences, each built on one fact with its
   number or time, ending on one line of grudging credit.
4. Under the finished reply: **Share this roast**. It opens a card (1080 × 1350) with the
   orb, "My week, roasted", her words, "— {agent_name}", the dates and "Made with
   Trillion", with **Copy image**, **Save** and **Close**.

## How

- **`week_roast`** (`tools/roast.py`): a read, no arguments, runs at once. How to roast is
  in its description, which she follows; the facts come back as an untrusted result like
  any other, never as instructions.
- **The facts** (`storage/sqlite/week_store.py`, `roast/week.py`): the last seven days in
  the user's timezone. User messages and conversations (deleted ones left out), spoken
  and typed turns, the busiest day, the latest message after midnight, chat titles (up to
  eight, newest first), replies cut off, cards approved and turned down, reminders and
  routines set and cancelled, helpers made (and since deleted), things saved to memory,
  and what the week cost. Only facts that happened are listed; an unknown price is left
  out, never shown as free.
- **Never read:** a message's words, or anything from a connected app.
- **The page** (`apps/web/src/roast.ts`): the reply that follows a successful `week_roast`
  in the same turn is marked a roast; once her turn is over (or she is cut off) it gets
  the Share button. The card is drawn on a canvas from the dark theme's tokens, so it looks
  the same in either theme. Nothing is uploaded; the user copies or saves it.

## Not now

- A roast spoken in a special voice or with a sound. No sound effects anywhere.
- Roasting from connected apps (calendar, mail). The local facts are enough to be funny,
  and they keep it private and free.
- Sharing straight to a social network. Copy and Save leave the choice with the user.
- A roast drawn again from history after a reload: the Share button is for the live reply.
