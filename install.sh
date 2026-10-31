#!/bin/sh
# Install Trillion on macOS or Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.sh | sh
#
# It installs uv (Astral's Python tool manager) if you don't have it, installs
# Trillion with uv, puts it on your PATH, then runs `trillion setup`, which
# prepares the data folder, checks this machine and opens the app.
# Running it again upgrades Trillion. It never uses sudo.
#
# Options (after `sh -s --` when piped), or the environment variable beside each:
#   --version X     install Trillion X instead of the latest   TRILLION_VERSION=X
#   --wheel PATH    install this wheel file or URL instead      TRILLION_WHEEL=PATH
#   --no-setup      install only; don't run `trillion setup`    TRILLION_NO_SETUP=1
#   --help          show this help
#
# Windows: use install.ps1 instead (see the README).

# Everything is inside main, called on the last line, so a download cut short
# runs nothing at all.
main() {
    set -eu

    version="${TRILLION_VERSION:-}"
    wheel="${TRILLION_WHEEL:-}"
    no_setup="${TRILLION_NO_SETUP:-}"
    case "$no_setup" in 1 | true | yes) no_setup=1 ;; *) no_setup="" ;; esac

    while [ $# -gt 0 ]; do
        case "$1" in
            --version) need_value "$@"; version="$2"; shift 2 ;;
            --version=*) version="${1#*=}"; shift ;;
            --wheel) need_value "$@"; wheel="$2"; shift 2 ;;
            --wheel=*) wheel="${1#*=}"; shift ;;
            --no-setup) no_setup=1; shift ;;
            -h | --help) usage; return 0 ;;
            *) fail "unknown option: $1 (try --help)" ;;
        esac
    done
    if [ -n "$version" ] && [ -n "$wheel" ]; then
        fail "use --version or --wheel, not both"
    fi

    case "$(uname -s)" in
        Darwin | Linux) ;;
        *) fail "this installer is for macOS and Linux. On Windows, use install.ps1:
  irm https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.ps1 | iex" ;;
    esac

    # 1. uv
    uv="$(find_uv)"
    if [ -z "$uv" ]; then
        say "installing uv, the tool that installs and runs Trillion"
        install_uv
        uv="$(find_uv)"
        [ -n "$uv" ] || fail "uv was installed but can't be found. Open a new terminal and run this again."
    fi
    say "using uv at $uv"
    old_uv="If your uv is old, run 'uv self update' and try again."

    # 2. Trillion. uv downloads Python 3.13 itself if this machine doesn't have it.
    if [ -n "$wheel" ]; then
        # --reinstall, so a rebuilt wheel with the same version still replaces the old one.
        say "installing Trillion from $wheel"
        "$uv" tool install --reinstall --python 3.13 "$wheel" ||
            fail "uv could not install $wheel (see above). $old_uv"
    else
        # Already installed? Keep its extras (such as Whisper): a plain
        # `uv tool install trillion` would replace them with none.
        listing="$("$uv" tool list --show-version-specifiers --show-extras 2>/dev/null)" ||
            fail "this uv is too old for this installer. Update it ('uv self update', or the way you installed it) and try again."
        installed="$(printf '%s\n' "$listing" | grep '^trillion v' || true)"
        extras="$(printf '%s\n' "$installed" | sed -n 's/.*\[extras: \([^]]*\)\].*/\1/p' | tr -d ' ')"
        package="trillion"
        [ -n "$extras" ] && package="trillion[$extras]"
        # `uv tool upgrade` keeps the extras and the Python, but also keeps a pinned
        # version or a wheel file, which this installer exists to move past.
        pinned=""
        case "$installed" in *"[required:"*) pinned=1 ;; esac
        if [ -n "$installed" ] && [ -z "$version" ] && [ -z "$pinned" ]; then
            say "upgrading Trillion${extras:+ (keeping $extras)}"
            "$uv" tool upgrade trillion ||
                fail "uv could not upgrade Trillion (see above). To reinstall it: uv tool install --reinstall --python 3.13 '$package'. $old_uv"
        else
            [ -n "$version" ] && package="$package==$version"
            say "installing $package (this upgrades it if it is already installed)"
            "$uv" tool install --upgrade --python 3.13 "$package" ||
                fail "uv could not install $package (see above). $old_uv"
        fi
    fi

    # 3. PATH, for new terminals. uv fails when the shell files already have it
    # but this terminal doesn't yet (a second run before opening a new one): fine.
    bindir="$("$uv" tool dir --bin)" ||
        fail "uv didn't say where it put Trillion; your uv is probably too old. Run 'uv self update' and try again."
    if shell_said="$("$uv" tool update-shell 2>&1)"; then
        say "$bindir is on your PATH in new terminals"
    else
        case "$shell_said" in
            *already*) say "$bindir is on your PATH in new terminals" ;;
            *) say "couldn't add $bindir to your PATH; add it yourself to run trillion from any terminal" ;;
        esac
    fi

    trillion="$bindir/trillion"
    [ -x "$trillion" ] || fail "Trillion was installed but $trillion is missing"
    say "installed $("$trillion" --version) in $bindir"

    if [ -n "$no_setup" ]; then
        say "done. Open a new terminal and run 'trillion setup' to start."
        return 0
    fi

    # 4. Setup: prepares, checks, starts the app and opens the browser. Under
    # `curl | sh` our input is the script itself, so give setup the terminal.
    say "running trillion setup (Ctrl+C stops Trillion; to start it again later, open a new terminal and run 'trillion run')"
    if (exec </dev/tty) 2>/dev/null; then
        exec "$trillion" setup </dev/tty
    else
        exec "$trillion" setup </dev/null
    fi
}

say() {
    printf 'trillion-install: %s\n' "$1"
}

fail() {
    printf 'trillion-install: error: %s\n' "$1" >&2
    exit 1
}

need_value() {
    [ $# -ge 2 ] || fail "$1 needs a value"
}

usage() {
    cat <<'EOF'
Install Trillion on macOS or Linux, or upgrade it, then run `trillion setup`.

  curl -fsSL https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.sh | sh
  curl -fsSL https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.sh | sh -s -- --no-setup

Options, or the environment variable beside each:
  --version X     install Trillion X instead of the latest   TRILLION_VERSION=X
  --wheel PATH    install this wheel file or URL instead      TRILLION_WHEEL=PATH
  --no-setup      install only; don't run `trillion setup`    TRILLION_NO_SETUP=1
  --help          show this help
EOF
}

# uv on PATH, else where Astral's installer puts it. Prints nothing if not found.
find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
    for candidate in "${UV_INSTALL_DIR:-}/uv" "${UV_INSTALL_DIR:-}/bin/uv" "${XDG_BIN_HOME:-}/uv" \
        "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        case "$candidate" in /uv | /bin/uv) continue ;; esac
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
}

# Astral's official installer: https://docs.astral.sh/uv/getting-started/installation/
install_uv() {
    # Download first, then run: a failed download must not look like a finished install.
    if command -v curl >/dev/null 2>&1; then
        uv_installer="$(curl -LsSf https://astral.sh/uv/install.sh)" ||
            fail "couldn't download the uv installer from astral.sh"
    elif command -v wget >/dev/null 2>&1; then
        uv_installer="$(wget -qO- https://astral.sh/uv/install.sh)" ||
            fail "couldn't download the uv installer from astral.sh"
    else
        fail "neither curl nor wget is installed; install one of them, or install uv yourself: https://docs.astral.sh/uv/"
    fi
    printf '%s\n' "$uv_installer" | sh || fail "the uv installer failed (see above)"
}

main "$@"
