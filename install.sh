#!/bin/sh
# Install Trillion on macOS or Linux:
#
#   curl -fsSL https://raw.githubusercontent.com/HelloTrillion/trillion/main/install.sh | sh
#
# It installs uv (Astral's Python tool manager) if you don't have it, installs
# Trillion with uv, puts it on your PATH, then runs `trillion setup`, which
# prepares the data folder, checks this machine and opens the app.
# Running it again upgrades Trillion. It never uses sudo, and it won't run as
# root: Trillion lives in your own home folder.
#
# Options (after `sh -s --` when piped), or the environment variable beside each:
#   --channel C     stable (the default), beta or dev           TRILLION_CHANNEL=C
#   --version X     install Trillion X instead of the latest   TRILLION_VERSION=X
#   --wheel PATH    install this wheel file or URL instead      TRILLION_WHEEL=PATH
#   --no-setup      install only; don't run `trillion setup`    TRILLION_NO_SETUP=1
#   --help          show this help
#   -- ARGS         pass ARGS on to `trillion setup`, such as: -- --no-run
#
# The channel is remembered (in ~/.trillion/channel), so running it again, or
# `trillion update`, stays on it. stable and beta come from PyPI; dev is the newest
# build of main, from the `dev` release on GitHub.
#
# In a container where root is the only user, TRILLION_ALLOW_ROOT=1 lets it run.
#
# Windows: use install.ps1 instead (see the README).

# Everything is inside main, called on the last line, so a download cut short
# runs nothing at all.
main() {
    set -eu

    version="${TRILLION_VERSION:-}"
    wheel="${TRILLION_WHEEL:-}"
    channel="${TRILLION_CHANNEL:-}"
    no_setup="${TRILLION_NO_SETUP:-}"
    case "$no_setup" in 1 | true | yes) no_setup=1 ;; *) no_setup="" ;; esac

    while [ $# -gt 0 ]; do
        case "$1" in
            --channel) need_value "$@"; channel="$2"; shift 2 ;;
            --channel=*) channel="${1#*=}"; shift ;;
            --version) need_value "$@"; version="$2"; shift 2 ;;
            --version=*) version="${1#*=}"; shift ;;
            --wheel) need_value "$@"; wheel="$2"; shift 2 ;;
            --wheel=*) wheel="${1#*=}"; shift ;;
            --no-setup) no_setup=1; shift ;;
            -h | --help) usage; return 0 ;;
            --) shift; break ;;
            *) fail "unknown option: $1 (try --help)" ;;
        esac
    done
    # Whatever is left goes to `trillion setup`.
    if [ -n "$version" ] && [ -n "$wheel" ]; then
        fail "use --version or --wheel, not both"
    fi
    # A tag works as a version too: v2026.11.0-beta.1 is 2026.11.0-beta.1 to uv.
    version="${version#v}"

    # The channel: the one asked for, else the one remembered, else stable.
    channel_file="${TRILLION_HOME:-$HOME/.trillion}/channel"
    was="$(head -n 1 "$channel_file" 2>/dev/null | tr -d ' \r' || true)"
    case "$was" in stable | beta | dev) ;; *) was=stable ;; esac
    chosen="$channel"
    [ -n "$chosen" ] || chosen="$was"
    case "$chosen" in
        stable | beta | dev) ;;
        *) fail "unknown channel: $chosen (use stable, beta or dev)" ;;
    esac
    if [ "$chosen" = dev ] && [ -n "$version" ]; then
        fail "the dev channel has no versions: it is always the newest build of main. Use --wheel to install a particular build."
    fi

    # Trillion and its data belong to a person, in their home folder. As root they
    # would land in root's, and the app would run as root.
    if [ "$(id -u)" = 0 ]; then
        case "${TRILLION_ALLOW_ROOT:-}" in
            1 | true | yes) say "running as root, because TRILLION_ALLOW_ROOT is set" ;;
            *) fail "don't run this as root or with sudo. Run it as yourself: Trillion installs into your own home folder and never needs sudo. (In a container where root is the only user, set TRILLION_ALLOW_ROOT=1.)" ;;
        esac
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
    # First, what is installed already, and with which extras (such as Whisper):
    # a plain `uv tool install trillion` would replace them with none. An older uv
    # can't show extras; then a plain list says whether Trillion is there at all.
    detailed=1
    if ! listing="$("$uv" tool list --show-version-specifiers --show-extras 2>&1)"; then
        detailed=""
        listing="$("$uv" tool list 2>&1)" ||
            fail "uv couldn't list what it has installed: $listing
Update uv ('uv self update', or the way you installed it) and try again."
    fi
    installed="$(printf '%s\n' "$listing" | grep '^trillion v' || true)"
    extras="$(printf '%s\n' "$installed" | sed -n 's/.*\[extras: \([^]]*\)\].*/\1/p' | tr -d ' ')"
    package="trillion"
    [ -n "$extras" ] && package="trillion[$extras]"
    # `uv tool upgrade` keeps the extras and the Python, but also keeps a pinned
    # version or a wheel file, which this installer exists to move past.
    pinned=""
    case "$installed" in *"[required:"*) pinned=1 ;; esac

    if [ -n "$wheel" ]; then
        # The extras go on the wheel: path[extras], or name[extras] @ URL.
        target="$wheel"
        if [ -n "$extras" ]; then
            case "$wheel" in
                *://*) target="trillion[$extras] @ $wheel" ;;
                *) target="${wheel}[$extras]" ;;
            esac
        fi
        # --reinstall, so a rebuilt wheel with the same version still replaces the old one.
        say "installing Trillion from $wheel${extras:+ (keeping $extras)}"
        "$uv" tool install --reinstall --python 3.13 "$target" ||
            fail "uv could not install $target (see above). $old_uv"
    elif [ "$chosen" = dev ]; then
        url="$(dev_wheel)"
        [ -n "$url" ] || fail "there is no dev build to install yet (no wheel on https://github.com/HelloTrillion/trillion/releases/tag/dev)"
        target="trillion @ $url"
        [ -n "$extras" ] && target="trillion[$extras] @ $url"
        # --reinstall: each dev build replaces the one before.
        say "installing the newest dev build of Trillion${extras:+ (keeping $extras)}"
        "$uv" tool install --reinstall --python 3.13 "$target" ||
            fail "uv could not install $url (see above). $old_uv"
    elif [ -n "$installed" ] && [ -z "$version" ] && [ -z "$pinned" ] &&
        [ "$chosen" = stable ] && [ "$was" = stable ]; then
        say "upgrading Trillion${extras:+ (keeping $extras)}"
        if [ -z "$detailed" ]; then
            reinstall="uv tool install --reinstall --python 3.13 trillion (as 'trillion[whisper]' if you use Whisper)"
        else
            reinstall="uv tool install --reinstall --python 3.13 '$package'"
        fi
        "$uv" tool upgrade trillion ||
            fail "uv could not upgrade Trillion (see above). To reinstall it: $reinstall. $old_uv"
    else
        if [ -n "$version" ]; then
            package="$package==$version"
        elif [ "$chosen" = beta ]; then
            # A requirement that names a pre-release lets uv pick one for Trillion,
            # and for nothing else it depends on.
            package="$package>=0.0.0b0"
        fi
        # Leaving a channel needs a fresh start: --upgrade alone would keep a beta
        # that is installed, and moving to stable means the newest stable.
        fresh="--upgrade"
        [ "$chosen" = "$was" ] || fresh="--reinstall"
        say "installing $package from the $chosen channel (this upgrades it if it is already installed)"
        "$uv" tool install "$fresh" --python 3.13 "$package" ||
            fail "uv could not install $package (see above). $old_uv"
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

    # Remember a channel that was asked for, so a re-run and `trillion update` stay on it.
    if [ -n "$channel" ]; then
        if (umask 077 && mkdir -p "$(dirname "$channel_file")" && printf '%s\n' "$chosen" >"$channel_file"); then
            say "on the $chosen channel from now on"
        else
            say "couldn't note the $chosen channel in $channel_file; a re-run will need --channel $chosen again"
        fi
    fi

    if [ -n "$no_setup" ]; then
        [ $# -eq 0 ] || say "not running setup (--no-setup), so '$*' is not used"
        say "done. Open a new terminal and run 'trillion setup' to start."
        return 0
    fi

    # 4. Setup: prepares, checks, starts the app and opens the browser. Under
    # `curl | sh` our input is the script itself, so give setup the terminal.
    say "running trillion setup${1:+ $*} (Ctrl+C stops Trillion; to start it again later, open a new terminal and run 'trillion run')"
    if (exec </dev/tty) 2>/dev/null; then
        exec "$trillion" setup "$@" </dev/tty
    else
        exec "$trillion" setup "$@" </dev/null
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
  --channel C     stable (the default), beta or dev           TRILLION_CHANNEL=C
  --version X     install Trillion X instead of the latest   TRILLION_VERSION=X
  --wheel PATH    install this wheel file or URL instead      TRILLION_WHEEL=PATH
  --no-setup      install only; don't run `trillion setup`    TRILLION_NO_SETUP=1
  --help          show this help
  -- ARGS         pass ARGS on to `trillion setup`, such as: -- --no-run

The channel is remembered, so running this again stays on it. beta has the
pre-releases as well as the releases; dev is the newest build of main.

It never uses sudo, and won't run as root. In a container where root is the
only user, set TRILLION_ALLOW_ROOT=1.
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

# The wheel on the rolling `dev` release: the newest build of main. Prints its URL,
# or nothing if there is none.
dev_wheel() {
    api="${TRILLION_DEV_RELEASE:-https://api.github.com/repos/HelloTrillion/trillion/releases/tags/dev}"
    if command -v curl >/dev/null 2>&1; then
        answer="$(curl -fsSL -H 'Accept: application/vnd.github+json' "$api")" ||
            fail "couldn't ask GitHub for the dev build ($api)"
    elif command -v wget >/dev/null 2>&1; then
        answer="$(wget -qO- "$api")" || fail "couldn't ask GitHub for the dev build ($api)"
    else
        fail "neither curl nor wget is installed; install one of them to use the dev channel"
    fi
    printf '%s\n' "$answer" |
        grep -o '"browser_download_url": *"[^"]*/trillion-[^"/]*\.whl"' |
        head -n 1 | sed 's/.*"\([^"]*\)"$/\1/'
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
