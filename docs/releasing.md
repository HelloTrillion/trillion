# Releasing

A release is one wheel, `trillion-<version>-py3-none-any.whl`, attached to a GitHub
release and published to [PyPI](https://pypi.org/project/trillion/), so
`uv tool install trillion` installs it. It holds three things a source checkout does not:

- **the built page**, so it runs without Node;
- **our Google app** (the OAuth client and the Drive picker's key), so people can
  "Sign in with Google" without registering a Google app of their own;
- **the default soul**, as every wheel does.

[`scripts/build_release.py`](../scripts/build_release.py) puts them in, builds the
wheel, and checks it: the page and the Google app are inside, nothing private is,
and installed on its own into a fresh environment it finds both. The Release
workflow (`.github/workflows/release.yml`) runs that script.

The same workflow builds the desktop app and attaches it to the release:

- **Linux:** `Trillion_<version>_amd64.AppImage` and `Trillion_<version>_amd64.deb`,
  built on Ubuntu 22.04. Nothing to set up.
- **macOS:** `Trillion_<version>_universal.dmg` (Apple silicon and Intel), signed
  and notarized. Only once the Apple setup below is done: until then the build is
  unsigned, the log says so, and the `.dmg` is kept on the run as
  `…_unsigned.dmg`, never attached to the release.

Both are a thin shell around an installed `trillion`: they don't include Python
or the server. Someone runs the one-line installer first.

## One-time setup: the Google secret

Our Google app's settings are never in the repository. The Release workflow reads
them from a repository secret.

1. Open the repository's **Settings → Secrets and variables → Actions**.
2. **New repository secret**. Name: `GOOGLE_CLIENT_JSON`.
3. Value: the JSON file Google gave for the Desktop app client (APIs & Services →
   Credentials → the client → Download JSON), with the Drive picker's API key added
   at the top level as `"picker_key": "AIza..."`. A maintainer's local
   `src/trillion/connectors/google/google_client.json` is already in this shape, and
   the quickest way is `gh secret set GOOGLE_CLIENT_JSON < that-file`. The picker key
   is required: without it Gmail and Calendar would work but choosing Drive files
   would not, so the release build stops instead.

The workflow has no pull request trigger, so code from a fork never runs with it.
The build ships only `client_id`, `client_secret` and `picker_key`, and never prints
them. For an installed app Google does not treat the client secret as confidential:
anyone with the wheel can read it, which is expected.

## One-time setup: PyPI

The Release workflow publishes to PyPI by **trusted publishing**: PyPI trusts this
repository's Release workflow, run in an environment called `pypi`, to upload
`trillion`. No PyPI token is stored anywhere, in the repository or in GitHub.

1. **On GitHub:** **Settings → Environments → New environment**, named `pypi`.
   Under **Deployment protection rules**, tick **Required reviewers** and add the
   maintainers, so every upload waits for one of them to approve it. Under
   **Deployment branches and tags**, allow only tags matching `v*`.
2. **On PyPI** (signed in, with two-factor on): **Your account → Publishing → Add
   a new pending publisher → GitHub**, with:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `trillion` |
   | Owner | `HelloTrillion` |
   | Repository name | `trillion` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   A pending publisher becomes the project's publisher on the first upload. After
   that, the project's own **Settings → Publishing** page lists it.

A version can be uploaded to PyPI once and never replaced or reused, even after it
is deleted. So publishing is the workflow's last step: it runs only after the wheel
is built, checked and attached to the GitHub release.

## One-time setup: signing and notarizing the Mac app

Without this, releases still work: they carry the wheel and the Linux app, and the
Mac app is built unsigned and kept on the run only. With it, the Release workflow
signs the app with a Developer ID, has Apple notarize it, staples the ticket, and
attaches the `.dmg`. Tauri does the signing and notarizing itself; the workflow
only hands it these six secrets, under the names Tauri reads.

You need a paid **Apple Developer Program** membership (the individual or
organization account that will be named as the developer) and a Mac with Xcode or
the Command Line Tools.

1. **A Developer ID Application certificate.** On the Mac: Keychain Access →
   Certificate Assistant → Request a Certificate From a Certificate Authority,
   saved to disk. At [developer.apple.com](https://developer.apple.com/account/resources/certificates/list)
   → Certificates → +, choose **Developer ID Application** (not "Apple
   Development", not "Mac App Distribution"), upload the request, download the
   certificate and double-click it, so it lands in your login keychain with its
   private key.
2. **Export it as a .p12.** In Keychain Access → My Certificates, right-click
   "Developer ID Application: *Name* (*TEAMID*)" → Export, as a `.p12`, with a
   strong password of your choosing. Then base64 it, on one line:

   ```bash
   base64 -i DeveloperID.p12 | tr -d '\n' > DeveloperID.p12.base64
   ```

3. **The signing identity**, the certificate's full name:

   ```bash
   security find-identity -v -p codesigning
   ```

   Use the quoted text, such as `Developer ID Application: Example Ltd (AB12CD34EF)`.
4. **An app-specific password for notarizing.** At
   [account.apple.com](https://account.apple.com) → Sign-In and Security →
   App-Specific Passwords, make one named for this workflow. Your **Team ID** is
   the ten characters in brackets in the identity, also under Membership details
   at developer.apple.com.
5. **The GitHub secrets.** Repository **Settings → Secrets and variables →
   Actions → New repository secret**, one each. With the `gh` command, each value
   is read from your terminal or a file, so it is never in your shell history:

   | Secret | Value | |
   |---|---|---|
   | `APPLE_CERTIFICATE` | the one-line base64 of the `.p12` | `gh secret set APPLE_CERTIFICATE < DeveloperID.p12.base64` |
   | `APPLE_CERTIFICATE_PASSWORD` | the `.p12`'s export password | `gh secret set APPLE_CERTIFICATE_PASSWORD` (it asks) |
   | `APPLE_SIGNING_IDENTITY` | the identity from step 3 | `gh secret set APPLE_SIGNING_IDENTITY` |
   | `APPLE_ID` | the Apple Account email of the developer account | `gh secret set APPLE_ID` |
   | `APPLE_PASSWORD` | the app-specific password from step 4 (not the account password) | `gh secret set APPLE_PASSWORD` |
   | `APPLE_TEAM_ID` | the Team ID | `gh secret set APPLE_TEAM_ID` |

   The first three sign; the last three notarize. With only the first three, the
   app is signed but not notarized, and the log warns; the `.dmg` is not attached.
6. **Delete the local copies** of the `.p12` and its base64 once the secrets are
   set. The certificate stays in your keychain; export it again if it is ever
   needed.

The workflow never prints these values: it names a missing secret, never a set
one's value, and GitHub masks every secret in the log. It has no pull request
trigger, so code from a fork never runs with them. A Developer ID certificate is
valid for five years; when it is renewed, repeat steps 1 to 3 and update the first
three secrets. To revoke access, delete the secrets, and revoke the app-specific
password at account.apple.com.

Apple's notary service usually answers in a few minutes, sometimes much longer;
the job allows an hour. If notarizing fails, the job fails and the log has
Apple's reason, and `xcrun notarytool log <id>` on a Mac with the same Apple
Account shows the details.

## Versions and channels

Versions are CalVer: `YYYY.M.PATCH`, the month without a leading zero, the patch
counting from 0 within the month. There are three channels
([the spec](specs/2026-11-07-release-channels.md)):

| Channel | Tag | GitHub | PyPI | Installed with |
|---|---|---|---|---|
| stable | `v2026.11.0` | release | `2026.11.0` | the one-liner, `uv tool install trillion` |
| beta | `v2026.11.0-beta.1` | pre-release | `2026.11.0b1` (a pre-release) | `--channel beta` |
| dev | none: every push to main | the rolling `dev` pre-release | never | `--channel dev` |

Python writes a beta as `2026.11.0b1`, and npm and the desktop app as
`2026.11.0-beta.1`. [`scripts/version.py`](../scripts/version.py) knows both and
sets the version in every place at once: `pyproject.toml`, `src/trillion/__init__.py`,
the project's entry in `uv.lock`, and the `package.json` of `@hellotrillion/galaxy`,
the web app and the desktop app. CI fails if they disagree. The desktop app's
`tauri.conf.json` and `Cargo.toml` keep a placeholder: the release build stamps the
version in.

```bash
python3 scripts/version.py                    # 2026.11.0b1
python3 scripts/version.py --semver           # 2026.11.0-beta.1
python3 scripts/version.py set 2026.11.0      # a tag works too: v2026.11.0
python3 scripts/version.py check              # every place agrees
```

The old `v0.X.Y` tags still work, and are still pre-releases on GitHub.

## Making a stable release

1. Set the version and merge it:

   ```bash
   python3 scripts/version.py set 2026.11.0
   ```

2. Tag the merge on main and push the tag:

   ```bash
   git tag v2026.11.0 && git push origin v2026.11.0
   ```

3. The Release workflow first checks the tag names the version in `pyproject.toml`
   and `src/trillion/__init__.py`; if not, it stops at once and says how to fix it.
   Then it builds and checks the wheel and creates the release with the wheel
   attached. It waits for a maintainer to approve the `pypi` environment, and
   publishes the same wheel to PyPI.
4. Meanwhile it builds the desktop app, with the version from `pyproject.toml` (as
   SemVer), and attaches the Linux AppImage and `.deb`, and the Mac `.dmg` if it was
   signed and notarized. A desktop job that fails can be re-run on its own; it
   replaces what it attached before.

## Making a beta

The same, with a `-beta.N` version, counting from 1:

```bash
python3 scripts/version.py set 2026.12.0-beta.1   # pyproject says 2026.12.0b1
git tag v2026.12.0-beta.1 && git push origin v2026.12.0-beta.1
```

The GitHub release is marked pre-release, and PyPI treats `2026.12.0b1` as one, so
`uv tool install trillion` and the stable channel never pick it. The beta channel
does, and also gets any stable release newer than the newest beta. When the beta is
good, set `2026.12.0`, merge, and tag `v2026.12.0`.

## The dev channel

Every push to main runs [`dev-build.yml`](../.github/workflows/dev-build.yml): it
builds the wheel as a release does (page and Google app inside, checked), gives it
the version of dev build N of main (`2026.11.0` on main makes `2026.11.1.devN`,
which sorts after it and before the next release), and puts it on one GitHub
pre-release tagged `dev`, replacing the wheel before. The `dev` tag moves with main.
Nothing goes to PyPI and no desktop app is built. It runs only in this repository.

Nothing to do by hand. To rebuild it, run **Actions → Dev build → Run workflow**.

## `trillion update`

`trillion update --check` asks GitHub's releases API for the newest version on the
user's channel and says whether they have it. `trillion update` installs it with
the installer's own `uv tool install`, keeping their extras. They run only when
typed: nothing checks for updates on its own (ONB-8). The channel is the one the
user installed from (`~/.trillion/channel`; none means stable), or
`--channel stable|beta|dev`, which `trillion update` then remembers.

A release is on GitHub a little before its PyPI upload is approved. In that time
`--check` names the new version but uv can't install it yet: approve the upload.

To try a build without releasing, run **Actions → Release → Run workflow**. The
wheel and the desktop builds are kept as downloads on that run for 30 days.

## Building one locally

```bash
pnpm install && pnpm --filter "@hellotrillion/web..." build
uv run python scripts/build_release.py
```

It uses your local `google_client.json` if you have one, and builds without Google
if you do not. `--require-google` makes a missing Google app an error, as in CI. The
wheel lands in `dist-release/`.

## The one-line installer

[`install.sh`](../install.sh) (macOS and Linux) and [`install.ps1`](../install.ps1)
(Windows) are served straight from this repository's main branch:

```bash
curl -fsSL https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.sh | sh
```

```powershell
irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1 | iex
```

hellotrillion.ai can redirect to these later. A change to either script reaches
everyone as soon as it is merged, without a release.

Each installs uv if it is missing (with Astral's own installer), then installs
the latest version on PyPI, or upgrades to it. `--channel beta` (`-Channel beta`,
`TRILLION_CHANNEL=beta`) installs the newest beta or stable, whichever is newer, by
asking uv for `trillion>=0.0.0b0`, plus `--prerelease-package trillion=allow` when
`uv tool install --help` lists it. Older uv picks a pre-release from the requirement
alone and lacks the option; newer uv (0.13) needs it. Either way only Trillion may
be a pre-release, never what it depends on. `--channel dev` asks GitHub's
API for the wheel on the `dev` release and installs it with `--reinstall`. The
channel asked for is remembered in `~/.trillion/channel` (or under `TRILLION_HOME`),
so a re-run and `trillion update` stay on it; moving to another channel reinstalls,
so going back to stable gets the newest stable even from a newer beta.
`--channel dev` with `--version` is refused: dev has no versions. They work from release 0.0.2, the
first on PyPI. If Trillion is already installed from PyPI, they run
`uv tool upgrade trillion`, which keeps the extras someone added (such as
`[whisper]`) and the tool's Python; if that fails, they stop and say how to
reinstall, rather than install again without the extras. Otherwise, including an
install pinned to a version or made from a wheel file, they run
`uv tool install --upgrade --python 3.13 trillion`, with the extras it had. `--wheel`
keeps the extras too (`<wheel>[whisper]`, or `trillion[whisper] @ <URL>`). A uv too
old to list extras still installs and upgrades: the installer falls back to a plain
`uv tool list`, and upgrades with `uv tool upgrade trillion` if Trillion is there. `--version X` (`TRILLION_VERSION`) installs a given version and `--wheel`
(`TRILLION_WHEEL`) a wheel file or URL instead, which is how to try a build before
it is released. Then they add uv's tool folder to the PATH (`uv tool update-shell`)
and run `trillion setup`, unless `--no-setup` (`TRILLION_NO_SETUP=1`). With
`install.sh`, anything after `--` goes to `trillion setup`: `sh install.sh -- --no-run`
prepares and checks without starting the app.

`install.sh` won't run as root or under `sudo`: Trillion and its data belong in a
person's home folder. In a container where root is the only user,
`TRILLION_ALLOW_ROOT=1` lets it run.

They do not install the desktop app (it is on the release page, and needs the
installed `trillion`), a wake-word model (deferred), or a Homebrew formula (none yet).

CI tests both (the `installer` job in `.github/workflows/ci.yml`): on Ubuntu and
Windows for every pull request, and on macOS too for every push to main, nightly
and on demand (Mac runners are too scarce to block a pull request). It builds the wheel as
above but without Google, installs it with the installer and `--wheel`, runs the
installer a second time (piped, with the environment variables) to prove a re-run
is fine, and checks `trillion --version` from uv's tool folder. It checks that a
failure exits nonzero, that the installer installs uv when there is none, that
a re-run and `--wheel` keep `[whisper]`, and that the channels work (stable, beta,
remembered, back to stable, and with `install.sh` dev, with `TRILLION_DEV_RELEASE`
pointing at a stand-in for GitHub's answer), with stand-in wheels in a local folder in
place of PyPI ([`scripts/fake_trillion_wheels.py`](../scripts/fake_trillion_wheels.py)).
On Ubuntu it also
runs `shellcheck install.sh`. On Windows it runs `install.ps1` in Windows
PowerShell 5.1, as a file and through `Invoke-Expression`, and checks that through
`Invoke-Expression` it returns to its caller and leaves nothing behind. It also runs
it as a script block (`& ([scriptblock]::Create(...)) -Wheel … -NoSetup`), which
takes parameters, and through `Invoke-Expression` inside a function called with
arguments of its own, which must not be taken as the installer's. Installing from PyPI
itself is not tested there, so after a release, run the one-liner once on a clean
machine.

## The clean-machine check

Phase 0 is done only if, on a clean Mac and a clean Ubuntu 22.04,
`uv tool install trillion && trillion setup` succeeds and `trillion doctor` is green.
The `clean-machine` job in `.github/workflows/ci.yml` checks that: on Ubuntu 22.04
for every pull request, and on macOS for every push to main, nightly and on demand.
With an empty home and only `/usr/bin:/bin` on the PATH (no uv, no Python of the
runner's), it runs `install.sh --wheel <the wheel built here> -- --no-run --no-open`,
which has to install uv with Astral's installer, install Trillion and run
`trillion setup`. Then `trillion doctor` must exit 0 with no `fail` line, and
`trillion run` must serve the page on port 7777. No keys, no model and no paid
service: the first rung of the ladder. Run it on your own machine the same way,
with a throwaway home:

```bash
home="$(mktemp -d)"
env -i PATH=/usr/bin:/bin HOME="$home" sh install.sh --wheel "$PWD"/dist-release/trillion-*.whl -- --no-run --no-open
env -i PATH=/usr/bin:/bin HOME="$home" "$home/.local/bin/trillion" doctor
```
