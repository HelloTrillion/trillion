## What changed and why

<!-- A few plain sentences. Say why, not just what. -->

**Requirement:** <!-- the PRD ID, for example SPN-2, or "none" --> · **Issue:** <!-- #123, if there is one -->

## How you tested it

<!-- The tests you added or changed, and anything you checked by hand (with Voice off). -->

## Checklist

- [ ] Every commit is signed off (`git commit -s`; see [DCO](https://github.com/HelloTrillion/trillion/blob/main/DCO)).
- [ ] Nothing on the [never list](https://github.com/HelloTrillion/trillion/blob/main/CONTRIBUTING.md#the-never-list) is broken.
- [ ] A spec with a Platforms section, and `docs/platforms.md` updated, if this adds a feature.
- [ ] These pass locally:
  - `uv run ruff check . && uv run ruff format --check . && uv run mypy`
  - `uv run pytest`
  - `uv run python scripts/<name>.py --selftest` and `uv run python scripts/<name>.py` for `containment_check`, `check_agent_name`, `check_raw_hex`, `check_css`, `check_plug_in` and `check_one_writer`
  - `uv run python scripts/version.py check`
  - `pnpm typecheck && pnpm test && pnpm -r --if-present --filter '!@hellotrillion/desktop' run build`
