# Release channels and `trillion update`

**Date:** 2026-11-07 · **Requirements:** §11 Versioning (CalVer, `-beta.N`, stable, beta and dev channels), §10 Phase 2 "CalVer with stable, beta and dev channels", ONB-8 (`trillion update --check`), ONB-1 · **Builds on:** [installers](2026-11-07-installers.md), [`docs/releasing.md`](../releasing.md)

The PRD promises CalVer versions and three channels, and names `trillion update --check` as the one update check a user can ask for. Today the only releases are `v0.0.1` and `v0.0.2`, every `v0.*` tag is a pre-release, the version is set by hand in two files (and the web packages say something else), and `trillion update` does not exist.

## Decisions

1. **CalVer, written three ways.** A tag is `vYYYY.M.PATCH` or `vYYYY.M.PATCH-beta.N`, month without a leading zero. Python needs PEP 440 and Tauri needs SemVer, so each tag has both:

   | Tag | Python, PyPI, `trillion --version` | npm, the desktop app |
   |---|---|---|
   | `v2026.11.0` | `2026.11.0` | `2026.11.0` |
   | `v2026.11.0-beta.1` | `2026.11.0b1` | `2026.11.0-beta.1` |
   | dev build 57 of main at 2026.11.0 | `2026.11.1.dev57` | `2026.11.1-dev.57` |

   PEP 440 reads `2026.11.0-beta.1` as `2026.11.0b1` anyway, so a user can type either. A beta sorts before its stable; a dev build sorts after the version on main and before the next one (main at a beta, `2026.11.0b1`, gives `2026.11.0b2.devN`). Old `v0.X.Y` tags still parse, and still make a pre-release, so history stays valid.
2. **One script sets the version everywhere.** `scripts/version.py set 2026.11.0-beta.1` writes `pyproject.toml`, `src/trillion/__init__.py`, the project's entry in `uv.lock`, and the three `package.json` files (`@hellotrillion/galaxy`, web, desktop). `check` says whether they agree, and CI runs it. The desktop app's `tauri.conf.json` and `Cargo.toml` keep a placeholder: a release build stamps the version in with `tauri build --config`, now as SemVer. Standard library only, with a `--selftest`, like the other scripts.
3. **The release checks the tag first.** The Release workflow's first step after checkout runs `scripts/version.py check-tag "$TAG"`: the tag must be CalVer (or old 0.x) and name the version in `pyproject.toml` and `__init__.py`, or it stops in seconds and says the command that fixes it. `build_release.py --tag` makes the same check.
4. **stable and beta are tags; dev is main.**
   - **stable:** a tag with no suffix. A normal GitHub release, then PyPI.
   - **beta:** a `-beta.N` tag. A GitHub pre-release, then PyPI, where `2026.11.0b1` is a pre-release that `uv tool install trillion` never picks on its own.
   - **dev:** every push to main, built by a new workflow, `dev-build.yml`, into one rolling GitHub pre-release tagged `dev`. Never on PyPI.
5. **dev is a rolling release, not an install from git.** `uv tool install git+https://…@main` is less machinery, but it installs a checkout with no built page (it needs Node to show anything) and no Google app. A wheel built exactly as a release is built has both, is checked the same way, and installs in seconds. The cost is one small workflow; the `dev` release holds one wheel, replaced on each push, and its tag moves with main. Desktop apps are not built for dev: they are slow and need the signing secrets.
6. **The beta channel asks uv for `trillion>=0.0.0b0`.** A requirement that names a pre-release lets uv pick one for Trillion and for nothing else. `--prerelease allow` would also let every dependency be a pre-release.
7. **Leaving a channel reinstalls.** `uv tool install --upgrade trillion` keeps an installed beta that satisfies it; moving to stable means the newest stable, even if that is older than the beta. So a change of channel uses `--reinstall`.
8. **The channel is remembered in the data folder.** A one-word file, `~/.trillion/channel` (or under `TRILLION_HOME`), so the shell and PowerShell installers can read and write it as easily as Python. It is written when someone asks for a channel (`--channel`, `TRILLION_CHANNEL`, `trillion update --channel`), never otherwise; no file means stable. Inside Trillion's own folder, so no tool can change it.
9. **`trillion update --check` asks GitHub's releases API.** One request, only when typed. GitHub, not PyPI, because it is the one place that has all three channels (dev is never on PyPI), and the tag says the channel directly. The 0.x releases are marked pre-release on GitHub but are plain versions on PyPI, so the tag decides, not GitHub's mark. The catch: a release is on GitHub a little before a maintainer approves its upload to PyPI, so for those minutes `--check` can name a version `uv` can't install yet.
10. **`trillion update` runs the installer's uv command.** `uv tool install --upgrade --python 3.13 'trillion[extras]'` (with `>=0.0.0b0` for beta, or the dev wheel's URL and `--reinstall` for dev), keeping the extras `uv tool list --show-extras` shows. If it is already the newest, it does nothing. If GitHub can't be reached, stable and beta still ask uv (which asks PyPI); dev can't, so it stops. From a source checkout it says to use `git pull`. On Windows a running Trillion may hold its files; if uv fails, it says to close Trillion and run the printed line.
11. **No check otherwise (ONB-8).** Nothing at startup, nothing in the background, no "new version" badge. A test checks that only `trillion update` imports the module and that nothing else in the core names GitHub's API. The airplane test is unchanged and passes.

## What the user sees

- `curl … | sh -s -- --channel beta` installs the newest beta (or a newer stable, if there is one) and says "on the beta channel from now on". Running the one-liner again stays on beta. `--channel stable` goes back. `install.ps1` takes `-Channel beta` or `$env:TRILLION_CHANNEL = "beta"`.
- `--channel dev` installs the newest build of main. `--channel dev --version X` is refused: dev has no versions.
- `trillion update --check`:

  ```
  trillion 2026.11.0, on the stable channel.
  Newer on stable: 2026.11.1 (https://github.com/HelloTrillion/trillion/releases/tag/v2026.11.1)
  To install it: trillion update
  ```

  or "That is the newest on stable." Offline: "couldn't reach GitHub to check. Are you online? Nothing was changed."
- `trillion update` installs it and says to restart Trillion. `trillion update --channel beta` moves to beta and remembers it. `--check --channel dev` looks without moving.

## How

- `scripts/version.py`: parse, convert, `set`, `dev N`, `check`, `check-tag`, `--selftest`. CI's structure job runs the self-test and `check`.
- `.github/workflows/release.yml`: the tag check as the wheel job's first step; `*-beta.*` tags are pre-releases as `v0.*` were; the desktop jobs stamp the SemVer version. No job moved.
- `.github/workflows/dev-build.yml`: on push to main, in this repository only. `version.py dev $GITHUB_RUN_NUMBER`, `build_release.py --require-google`, then the `dev` release is made again on the commit with only the new wheel.
- `install.sh`, `install.ps1`: `--channel`/`-Channel`/`TRILLION_CHANNEL`, the channel file, the dev wheel from GitHub's API (`TRILLION_DEV_RELEASE` points it elsewhere, for tests). CI runs both against stand-in wheels: stable, beta, remembered, back to stable, and (with `install.sh`) dev.
- `src/trillion/cli/update.py`, and `update` in `cli/main.py`. Tests fake GitHub with `httpx.MockTransport` and uv with a recorder: no network, nothing installed.

## Not now

- An update check in the app or the desktop shell. ONB-8 allows it only when asked; a button that runs the same check is a small follow-up.
- Publishing `@hellotrillion/galaxy` to npm. Its version now follows the release; publishing is its own decision.
- Desktop apps for dev. A beta tag builds them like any tag; dev never does.
- A Windows `.msi`. Its version can't have a first number above 255, so CalVer needs Tauri's `bundle.windows.wix.version` set to something else; an NSIS installer has no such limit.
- A Homebrew formula per channel.
