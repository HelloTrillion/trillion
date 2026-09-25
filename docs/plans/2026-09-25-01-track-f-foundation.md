# Track F Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python and JS toolchain with CI that runs on macOS and Ubuntu, enforces the structural rules, and carries the PRD fixes agreed in design review.

**Architecture:** uv manages a `src/`-layout Python package, and pnpm manages an (initially empty) workspace. Structural rules are small stdlib-only Python scripts in `scripts/`, each with a `--selftest`, in the style of the existing `containment_check.py`.

**Tech Stack:** Python 3.13, uv, hatchling, ruff, mypy, pytest, pnpm 9, TypeScript 5+, GitHub Actions.

**Spec:** `docs/specs/2026-09-25-foundation-and-spine-design.md` §2–3.

**Conventions for every commit:** `git commit -s` (DCO), and end the message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Run `python3 scripts/containment_check.py` before each commit.

---

## File map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, dependencies, entry-point groups, ruff/mypy/pytest configuration |
| `.python-version` | `3.13` for uv |
| `src/trillion/__init__.py` | `__version__` |
| `src/trillion/py.typed` | PEP 561 marker |
| `src/trillion/cli/__init__.py`, `src/trillion/cli/main.py` | The `trillion` command (prints its version for now) |
| `tests/unit/test_cli.py` | Proves the command runs |
| `package.json`, `pnpm-workspace.yaml`, `tsconfig.base.json` | JS workspace root |
| `scripts/check_agent_name.py` | PER-1a lint |
| `scripts/check_raw_hex.py` | GAL-11 lint |
| `.github/workflows/ci.yml` | Adds the python, spine, structure and js jobs |
| `docs/PRD.md` | The consistency fixes |
| `docs/architecture.md` | Module map and the life of a tool call |
| `CONTRIBUTING.md` | "Getting started" commands |

---

### Task 1: The Python package and CLI

**Files:**
- Create: `pyproject.toml`, `.python-version`, `src/trillion/__init__.py`, `src/trillion/py.typed`, `src/trillion/cli/__init__.py`, `src/trillion/cli/main.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/test_cli.py`
- Delete: `src/trillion/cli/.gitkeep`, `tests/unit/.gitkeep`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_cli.py`:
```python
import subprocess
import sys

from trillion import __version__
from trillion.cli.main import main


def test_version_flag_prints_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"trillion {__version__}"


def test_module_is_runnable():
    out = subprocess.run(
        [sys.executable, "-m", "trillion.cli.main", "--version"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert out.startswith("trillion ")
```

Create empty `tests/__init__.py` and `tests/unit/__init__.py`.

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "trillion"
version = "0.0.1"
description = "The voice-first AI agent with a face."
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.13"
dependencies = []

[project.scripts]
trillion = "trillion.cli.main:entrypoint"

# Plugin discovery (CON-7). First-party tools register here too.
[project.entry-points."trillion.tools"]
[project.entry-points."trillion.connectors"]
[project.entry-points."trillion.checkers"]
[project.entry-points."trillion.channels"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/trillion"]

[dependency-groups]
dev = []

[tool.ruff]
line-length = 100
target-version = "py313"
src = ["src", "tests", "scripts"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "S", "T20", "LOG", "G", "RUF"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S603", "S607"]
"scripts/**" = ["T20", "S603", "S607"]

[tool.mypy]
python_version = "3.13"
mypy_path = "src"
packages = ["trillion"]
warn_unused_configs = true

[[tool.mypy.overrides]]
module = ["trillion.spine.*", "trillion.storage.*", "trillion.tools.*", "trillion.voice.*", "trillion.brain.*"]
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

`.python-version`:
```
3.13
```

- [ ] **Step 3: Add the dev dependencies with uv (so the lockfile pins real versions)**

Run: `uv add --dev pytest pytest-asyncio pytest-cov hypothesis ruff mypy`
Expected: `uv.lock` is created, and `[dependency-groups] dev` lists the six packages.

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trillion'` or an ImportError for `__version__`.

- [ ] **Step 5: Implement**

`src/trillion/__init__.py`:
```python
"""Trillion: the voice-first AI agent with a face."""

__version__ = "0.0.1"
```

`src/trillion/py.typed`: empty file.

`src/trillion/cli/__init__.py`:
```python
"""The `trillion` command line."""
```

`src/trillion/cli/main.py`:
```python
"""Entry point for the `trillion` command. Subcommands arrive with their tracks."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from trillion import __version__


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trillion")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args(argv)
    if args.version:
        sys.stdout.write(f"trillion {__version__}\n")
        return 0
    parser.print_help()
    return 0


def entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
```

Delete `src/trillion/cli/.gitkeep` and `tests/unit/.gitkeep`.

- [ ] **Step 6: Run the tests and lints**

Run: `uv run pytest tests/unit -v && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: 2 passed. ruff clean. mypy `Success: no issues found`. If `ruff format --check` reports files, run `uv run ruff format .` and re-check.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version src tests
git commit -s -m "Stand up the Python package so tracks have somewhere to land

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The JS workspace root

**Files:**
- Create: `package.json`, `pnpm-workspace.yaml`, `tsconfig.base.json`

- [ ] **Step 1: Create the files**

`package.json`:
```json
{
  "name": "trillion-workspace",
  "private": true,
  "packageManager": "pnpm@9.15.0",
  "engines": { "node": ">=22" },
  "scripts": {
    "typecheck": "pnpm -r --if-present run typecheck",
    "test": "pnpm -r --if-present run test"
  }
}
```

`pnpm-workspace.yaml`:
```yaml
packages:
  - "apps/*"
  - "packages/*"
```

`tsconfig.base.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "noImplicitOverride": true,
    "isolatedModules": true,
    "verbatimModuleSyntax": true,
    "skipLibCheck": true,
    "declaration": true
  }
}
```

- [ ] **Step 2: Verify**

Run: `pnpm install && pnpm typecheck`
Expected: install completes and writes `pnpm-lock.yaml`. typecheck exits 0 (no packages yet).

- [ ] **Step 3: Commit**

```bash
git add package.json pnpm-workspace.yaml tsconfig.base.json pnpm-lock.yaml
git commit -s -m "Add the pnpm workspace root the Galaxy and web app will share

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: PER-1a agent-name check

**Files:**
- Create: `scripts/check_agent_name.py`

**Rule:** the literal word `Trillion` must not appear in agent-facing text. Agent-facing text means:
- Markdown and text files under `persona/` and under any `src/trillion/**/prompts/` directory, **with HTML comments removed** (SOUL.md's header comment legitimately explains the default name)
- every string literal in `.py` files under `src/trillion/**/prompts/`, excluding module, class and function docstrings

- [ ] **Step 1: Write the script with its self-test**

`scripts/check_agent_name.py`:
```python
#!/usr/bin/env python3
"""Fail the build if an agent-facing string hardcodes the brand name (PER-1a).

The brand is Trillion; the agent's name belongs to the user and reaches every
prompt as {agent_name}. A prompt that says "Trillion" makes a user's "Nova"
introduce herself by the wrong name.

    python3 scripts/check_agent_name.py            # scan the repository
    python3 scripts/check_agent_name.py --selftest # prove the rule works
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND = re.compile(r"\bTrillion\b")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
TEXT_SUFFIXES = {".md", ".txt"}


def agent_facing_files(root: Path) -> list[Path]:
    files = [p for p in (root / "persona").rglob("*") if p.is_file()]
    for prompts in (root / "src" / "trillion").rglob("prompts"):
        if prompts.is_dir():
            files.extend(p for p in prompts.rglob("*") if p.is_file())
    return sorted(files)


def text_hits(text: str) -> list[int]:
    stripped = HTML_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return [stripped.count("\n", 0, m.start()) + 1 for m in BRAND.finditer(stripped)]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def python_hits(source: str) -> list[int]:
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and BRAND.search(node.value)
    )


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in agent_facing_files(root):
        rel = path.relative_to(root)
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            lines = python_hits(text)
        elif path.suffix in TEXT_SUFFIXES:
            lines = text_hits(text)
        else:
            continue
        findings.extend(f"  {rel}:{n}  hardcodes the brand; use {{agent_name}}" for n in lines)
    return findings


def selftest() -> int:
    failures: list[str] = []
    if text_hits("You are Trillion.") != [1]:
        failures.append("  MISSED: plain markdown mention")
    if text_hits("<!-- default is Trillion -->\nYou are {agent_name}.") != []:
        failures.append("  FALSE POSITIVE: mention inside an HTML comment")
    if text_hits("A trillion stars.") != []:
        failures.append("  FALSE POSITIVE: lowercase common noun")
    if python_hits('"""Prompts for Trillion."""\nX = "hi"\n') != []:
        failures.append("  FALSE POSITIVE: module docstring")
    if python_hits('X = f"I am Trillion, {y}"\n') != [1]:
        failures.append("  MISSED: f-string literal part")
    if python_hits('X = "I am {agent_name}"\n') != []:
        failures.append("  FALSE POSITIVE: placeholder string")
    if failures:
        print("Agent-name self-test FAILED:\n" + "\n".join(failures))
        return 1
    print("Agent-name self-test passed.")
    return 0


def main() -> int:
    findings = scan(ROOT)
    if findings:
        print("Agent-name check FAILED (PER-1a):\n" + "\n".join(findings))
        return 1
    print("Agent-name check passed: no agent-facing string hardcodes the brand.")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
```

- [ ] **Step 2: Run the self-test and the check**

Run: `python3 scripts/check_agent_name.py --selftest && python3 scripts/check_agent_name.py`
Expected: `Agent-name self-test passed.` then `Agent-name check passed: …` (SOUL.md only mentions the brand inside its header comment).

- [ ] **Step 3: Prove the check fails on a real violation**

Run: `printf 'Hi, I am Trillion.\n' > persona/_probe.md && python3 scripts/check_agent_name.py; echo "exit=$?"; rm persona/_probe.md`
Expected: `persona/_probe.md:1  hardcodes the brand` and `exit=1`.

- [ ] **Step 4: Lint and commit**

Run: `uv run ruff check scripts && uv run ruff format --check scripts`

```bash
git add scripts/check_agent_name.py
git commit -s -m "Fail the build when a prompt hardcodes the brand name (PER-1a)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: GAL-11 raw-hex check

**Files:**
- Create: `scripts/check_raw_hex.py`

**Rule:** hex colour literals (`#abc`, `#aabbcc`, `#aabbccdd`) are forbidden in `.ts`, `.tsx`, `.css`, `.html` and `.glsl` files under `apps/` and `packages/`, except in any file named `tokens.css`. `node_modules` and `dist` are skipped. In HTML, `&#123;` entities are not colours.

- [ ] **Step 1: Write the script**

`scripts/check_raw_hex.py`:
```python
#!/usr/bin/env python3
"""Fail the build if a colour is hardcoded outside tokens.css (GAL-11).

One file holds every UI colour so the light theme is real and a rebrand is
one diff. Shader colours are linear-RGB floats in states.ts, not hex.

    python3 scripts/check_raw_hex.py [--selftest]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEX = re.compile(r"(?<![&\w])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
SUFFIXES = {".ts", ".tsx", ".css", ".html", ".glsl"}
SKIP = {"node_modules", "dist", ".vite"}
ALLOWED_NAME = "tokens.css"


def hits(text: str) -> list[int]:
    return [text.count("\n", 0, m.start()) + 1 for m in HEX.finditer(text)]


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for top in ("apps", "packages"):
        base = root / top
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if (
                not path.is_file()
                or path.suffix not in SUFFIXES
                or path.name == ALLOWED_NAME
                or SKIP & set(path.parts)
            ):
                continue
            rel = path.relative_to(root)
            text = path.read_text(encoding="utf-8")
            findings.extend(f"  {rel}:{n}  raw hex colour; use a token" for n in hits(text))
    return findings


def selftest() -> int:
    cases = {
        "color: #2DD4A8;": 1,
        "color: #fff;": 1,
        "background: #0E0F13cc;": 1,
        "&#123; entity": 0,
        "color: var(--accent);": 0,
        "#main-heading { }": 0,
    }
    failures = [f"  {src!r}: expected {n}" for src, n in cases.items() if len(hits(src)) != n]
    if failures:
        print("Raw-hex self-test FAILED:\n" + "\n".join(failures))
        return 1
    print("Raw-hex self-test passed.")
    return 0


def main() -> int:
    findings = scan(ROOT)
    if findings:
        print("Raw-hex check FAILED (GAL-11):\n" + "\n".join(findings))
        return 1
    print("Raw-hex check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
```

Note: `#main-heading` is a CSS id selector. `main` is not hex, so it doesn't match. A selector like `#add` or `#bad` **would** match; the Galaxy must not use such ids.

- [ ] **Step 2: Run**

Run: `python3 scripts/check_raw_hex.py --selftest && python3 scripts/check_raw_hex.py`
Expected: both pass.

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff check scripts && uv run ruff format --check scripts
git add scripts/check_raw_hex.py
git commit -s -m "Keep every UI colour in tokens.css (GAL-11)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: CI jobs

**Files:**
- Modify: `.github/workflows/ci.yml` (append jobs; keep `containment` and `dco` unchanged)

- [ ] **Step 1: Append these jobs under `jobs:`**

```yaml
  python:
    name: Python (${{ matrix.os }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - run: uv sync --locked
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run mypy
      - name: Unit and integration tests
        run: uv run pytest tests/unit tests/integration

  spine:
    name: Spine safety suite (${{ matrix.os }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
      - run: uv sync --locked
      - name: tests/spine (protected; 95% branch coverage on trillion.spine)
        run: |
          if find tests/spine -name 'test_*.py' | grep -q .; then
            uv run pytest tests/spine --cov=trillion.spine --cov-branch --cov-fail-under=95
          else
            echo "No spine tests yet."
          fi

  structure:
    name: Structural rules
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: python3 scripts/check_agent_name.py --selftest
      - run: python3 scripts/check_agent_name.py
      - run: python3 scripts/check_raw_hex.py --selftest
      - run: python3 scripts/check_raw_hex.py

  js:
    name: TypeScript
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - run: pnpm typecheck
      - run: pnpm test
```

`tests/integration` must exist for the python job. It does (`tests/integration/.gitkeep`). pytest exits with code 5 when it collects no tests, so add `tests/integration/test_placeholder_import.py`:
```python
def test_package_imports():
    import trillion

    assert trillion.__version__
```

- [ ] **Step 2: Validate the YAML locally**

Run: `uv run python -c "import yaml" 2>/dev/null || uv run --with pyyaml python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml tests/integration/test_placeholder_import.py
git commit -s -m "Run lint, types, tests and structural rules on macOS and Ubuntu

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: PRD consistency fixes

**Files:**
- Modify: `docs/PRD.md`

Apply each edit exactly. Line numbers are as of commit `0991beb`; search for the quoted text rather than trusting the numbers.

- [ ] **Step 1: §9 item 3.** Replace `From then on it calls itself Nova, and saying "Hello Trillion" still wakes it.` with `From then on it calls itself Nova. (The "Hello Trillion" wake word is Phase 1, VOI-10a; the separate wake.phrase / wake.model settings already exist.)`
- [ ] **Step 2: §9 "Out of scope".** Replace `channels, phone, Windows and Linux, signed builds.` with `channels, the wake word (VOI-10a), phone, Windows, signed builds. Linux is in scope (SUR-3a).`
- [ ] **Step 3: §7 Portability.** Replace `macOS 13+ (P0). Windows 11 and Ubuntu 22.04+ (P2).` with `macOS 13+ and Ubuntu 22.04+ (P0, SUR-3a). Windows 11 (P1, SUR-3c).`
- [ ] **Step 4: ONB-3.** Replace `and then asks for the ElevenLabs, Deepgram and Anthropic keys (VOI-11a; pasted into a secure field, never spoken).` with `and then walks the capability ladder (§4): a model key first (Anthropic, or any OpenAI-compatible endpoint including local Ollama), then ElevenLabs (VOI-11a), then Deepgram. Keys are pasted into a secure field, never spoken, and each one can be skipped (ONB-9).` Also replace `explains that the wake phrase stays "Hello Trillion", and` with `explains that the wake phrase (when it arrives in Phase 1) stays "Hello Trillion", and`.
- [ ] **Step 5: ONB-4.** Replace `Works with no keys at all once a local tier exists (beta). | P0 / P2 |` with `With no keys at all the app runs at rung 0 (§4). The free local voice path is VOI-11b. | P0 |`
- [ ] **Step 6: §8.4.** Replace `- \`connector_credentials\`` with `` - `connector_credentials` (metadata only: name, store kind, added_at, last_used — never a value; SEC-4) ``. Replace `- Migrations are numbered files (\`001_core.sql\`, …) with a version table.` with ``- Migrations are numbered files with a version table and a checksum. Each track adds its own migration when it lands (`001_spine.sql` first); the table list below is the Phase 0 target, not the contents of 001.``
- [ ] **Step 7: §9 tracks.** Track B contents: `VOI-1…8, 11, 13…18` → `VOI-1…11, 13…18 (VOI-9 tap barge-in, VOI-10 push-to-talk; VOI-10a wake word is P1)`. Track D contents: `PER-1…11` → `PER-1…6, 8…11`.
- [ ] **Step 8: §11.** Replace `**License:** Apache-2.0 (pending §13).` with `**License:** Apache-2.0 (settled; §13).`
- [ ] **Step 9: §10 Phase 1.** Replace `  - voice barge-in, wake word` with `  - voice barge-in (VOI-9 P1 half)`.
- [ ] **Step 10: §13 settled wake word.** Replace `(VOI-10).` at the end of the wake-word bullet with `(VOI-10a, Phase 1).`
- [ ] **Step 11: CON-3.** Replace `file tools (list, read, write, search in user-granted directories)` with `file tools in user-granted directories: \`file_list\`, \`file_read\` and \`file_search\` (read), and \`file_create\`, \`file_overwrite\` and \`file_append\` — **every write is \`irreversible\` and asks first**, and overwrite is unrecoverable, so it needs a tap (SPN-14)`.
- [ ] **Step 12: Update the header.** Bump `**Version:**` to the next minor version and add `2026-09-25: consistency pass after peer review (wake word P1, Linux P0, ladder-ordered onboarding, write tools).` to the status line.
- [ ] **Step 13: Verify and commit**

Run: `grep -n 'still wakes it\|Windows and Linux, signed\|(P2)\. Python\|pending §13' docs/PRD.md; python3 scripts/containment_check.py`
Expected: no grep output, and the containment check passes.

```bash
git add docs/PRD.md
git commit -s -m "Make the PRD agree with itself after two review rounds

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Architecture doc and getting started

**Files:**
- Create: `docs/architecture.md`
- Modify: `CONTRIBUTING.md` (the "Getting started" section)

- [ ] **Step 1: Write `docs/architecture.md`** with these sections, prose only, under 150 lines:
  1. **One process.** `trillion run` serves the UI and API and runs the workers, against SQLite at `~/.trillion/trillion.db` (`TRILLION_HOME` overrides it).
  2. **Module map.** The §8.1 tree from the PRD, one line per package, marking which exist today (`cli`, `spine`, `storage`, `tools`) and which are placeholders.
  3. **The life of a tool call.** Lookup → schema validation → deny floor → loop guard → risk branch (`read`/`write` execute and wrap; `irreversible`/`spend` freeze into a `PendingAction`) → audit. Then the confirm path: state machine → deny floor again → precheck again → atomic claim → execute → recorded outcome.
  4. **Where each guarantee lives.** A table: guarantee → module → test file (for example "arguments cannot change on the yes turn → `spine/contract.py` confirm tool schema → `tests/spine/test_confirmation.py`").
  5. **Frontend.** `packages/galaxy` is standalone. `apps/web` consumes it (later).

- [ ] **Step 2: Replace CONTRIBUTING's "Getting started" code block and the paragraph after it** with:

````markdown
```bash
git clone https://github.com/HelloTrillion/trillion.git
cd trillion
uv sync                 # Python 3.13 toolchain and dependencies
uv run pytest           # every Python test
pnpm install            # JS workspace (Node 22+)
pnpm --filter @hellotrillion/galaxy dev   # the orb, in your browser
```

Before you push, the same checks CI runs:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
python3 scripts/containment_check.py
python3 scripts/check_agent_name.py
python3 scripts/check_raw_hex.py
```

[`docs/PRD.md`](docs/PRD.md) is the specification: every requirement carries an ID (`GAL-3`, `VOI-2`, `SPN-5`, …), and issues reference those IDs. [`docs/TESTING.md`](docs/TESTING.md) says how each one is proven. [`docs/architecture.md`](docs/architecture.md) is the map.
````

- [ ] **Step 3: Verify and commit**

Run: `python3 scripts/containment_check.py`

```bash
git add docs/architecture.md CONTRIBUTING.md
git commit -s -m "Document the architecture and the real build commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Final verification

- [ ] Run: `uv sync --locked && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest && python3 scripts/containment_check.py --selftest && python3 scripts/containment_check.py && python3 scripts/check_agent_name.py --selftest && python3 scripts/check_agent_name.py && python3 scripts/check_raw_hex.py --selftest && python3 scripts/check_raw_hex.py && pnpm install --frozen-lockfile && pnpm typecheck`
- [ ] Expected: every command succeeds. Record the output in the hand-off.
