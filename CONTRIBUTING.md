# Contributing to Trillion

Thanks for being here. This project is early, which means your work will shape it more than it would in a mature repository.

Read this page before your first pull request. It is short, and two sections of it are not negotiable: **the never list** and **sign-off**.

---

## The three-pillar test

Trillion is a voice-first agent with a face and a safety spine. Every feature has to strengthen at least one pillar:

1. **The Galaxy** — the visual system: orb, landscape, constellation, arcs, celebrations.
2. **The Voice** — real-time conversation and the personality that carries it.
3. **The Spine** — safety enforced in code.

A good idea that strengthens none of them is still a good idea, just not for this repository yet. Open a discussion before you build it, and we'll tell you honestly.

We are deliberately **not** competing on the number of chat integrations, on agents that write their own skills, or on a public skill marketplace.

## The never list

A pull request will be rejected, however good the rest of it is, if it:

1. **Adds a tool without a `risk` level.** There is no default. State it.
2. **Marks third-party output as trusted.** Anything from the web, email, a file, a channel, a skill or an MCP server is untrusted data, not instructions.
3. **Lets a confirmation be satisfied with arguments other than the frozen ones**, or lets a confirmation be created and satisfied in the same turn.
4. **Adds a hard-delete path.** Deletion is soft: set `deleted_at`.
5. **Makes the identity files writable by the agent.** The agent may never edit its own soul.
6. **Binds a listener to anything but `127.0.0.1` by default,** or accepts a request without a session token.
7. **Logs conversation text, prompts, or any secret value.** Key names and lengths only.
8. **Adds a runtime dependency on a service the user did not connect.** No CDN, no secrets manager, no hosted database, no licence check, no update ping, no analytics. Vendor it, bundle it, or make it a provider the user chooses. Trillion has to start and run with the network off ("the airplane test").
9. **Hardcodes the agent's name.** Use `{agent_name}`; the brand is Trillion, but the user's agent may be called anything.
10. **Ships personal data** — a real person's name, path, email, key or account — in code, prompts or fixtures. CI checks this, and so do we.

If you think one of these rules is wrong, open an issue and argue the case. Don't route around it in a pull request.

## Getting started

```bash
git clone https://github.com/HelloTrillion/trillion.git
cd trillion
```

Build instructions land with the first code in each area. Until then, [`docs/PRD.md`](docs/PRD.md) is the specification: every requirement carries an ID (`GAL-3`, `VOI-2`, `SPN-5`, …), and issues reference those IDs.

## Working agreement

- **Claim an issue before starting** anything larger than a typo, so two people don't build the same thing.
- **One concern per pull request.** A refactor plus a feature is two pull requests.
- **Tests are required** for anything marked P0 in the PRD, and for every bug fix. A bug fix without a test that fails before it is not finished.
- **Match the surrounding code.** No reformatting of files you didn't otherwise change.
- **Write the commit message for the person reading it in a year.** Say why, not just what.

### Areas that need a maintainer review

`CODEOWNERS` requires a maintainer on:

- `src/trillion/spine/**` — the safety guarantees
- `src/trillion/persona/**` and `persona/**` — the identity
- `src/trillion/voice/**` capture — hard-won microphone behaviour
- `tests/spine/**` — the tests that prove the guarantees
- `.github/**` — CI, including the checks above

This is not distrust. These are the parts where a subtle change quietly removes a promise we make to users.

## Sign-off (DCO)

Every commit must carry a `Signed-off-by` line certifying that you wrote the contribution or have the right to submit it. The full text is in [`DCO`](DCO).

Git adds the line for you:

```bash
git commit -s -m "Your message"
```

Forgot on the last commit?

```bash
git commit --amend -s --no-edit
```

We use a DCO rather than a contributor license agreement, so there is nothing to sign and no paperwork.

## Reporting bugs

Open an issue with what you expected, what happened, and how to reproduce it. For anything voice-related, include the output of `trillion doctor` and your operating system and microphone or audio interface. Microphone bugs are almost always device-specific.

**Never paste an API key, a token or a log containing one.** Redact before you post.

For security vulnerabilities, follow [`SECURITY.md`](SECURITY.md) instead. Do not open a public issue.

## Code of conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Reports go to me@hellotrillion.ai.
