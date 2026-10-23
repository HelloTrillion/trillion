# Releasing

A release is one wheel, `trillion-<version>-py3-none-any.whl`, attached to a GitHub
release. It holds three things a source checkout does not:

- **the built page**, so it runs without Node;
- **our Google app** (the OAuth client and the Drive picker's key), so people can
  "Sign in with Google" without registering a Google app of their own;
- **the default soul**, as every wheel does.

[`scripts/build_release.py`](../scripts/build_release.py) puts them in, builds the
wheel, and checks it: the page and the Google app are inside, nothing private is,
and installed on its own into a fresh environment it finds both. The Release
workflow (`.github/workflows/release.yml`) runs that script.

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

## Making a release

1. Set the version in `pyproject.toml` and `src/trillion/__init__.py`, and merge.
2. Tag the merge on main and push the tag:

   ```bash
   git tag v0.0.1 && git push origin v0.0.1
   ```

3. The Release workflow builds and checks the wheel and creates the release with the
   wheel attached (a pre-release while the version is 0.x). The tag must match the
   version, or the build stops.

To try a build without releasing, run **Actions → Release → Run workflow**. The
wheel is kept as a download on that run for 30 days.

## Building one locally

```bash
pnpm install && pnpm --filter "@hellotrillion/web..." build
uv run python scripts/build_release.py
```

It uses your local `google_client.json` if you have one, and builds without Google
if you do not. `--require-google` makes a missing Google app an error, as in CI. The
wheel lands in `dist-release/`.
