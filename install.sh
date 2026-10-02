#!/bin/sh
# Installs anime-sh on macOS or Linux, from nothing.
#
# Run it and you have a working `anime` command: it installs uv if you do not
# have it, installs mpv with whatever package manager your system actually uses,
# installs anime-sh, and puts it on your PATH for this shell as well as the next
# one.
#
# uv rather than Homebrew, deliberately. A Homebrew tap has no prebuilt bottles,
# so `brew install` builds pydantic-core (Rust) from source -- 10-20 minutes for
# 0.2.89 depending on the machine, down from ~50 once Pillow stopped being
# compiled alongside it. uv ships its own Python and installs in seconds.
# Homebrew is still documented in the README for anyone who prefers it.
#
#   curl -LsSf https://raw.githubusercontent.com/Anime123450/anime-sh/master/install.sh | sh
#
# Flags:
#   --dry-run       print what would happen and change nothing
#   --with-ffmpeg   also install ffmpeg, which `anime download` needs
#
# Worth running with --dry-run first if you arrived here from a one-line command
# on the internet. Better still, read it: it is this file, and it is short.

set -eu

DRY_RUN=0
WITH_FFMPEG=0

for arg in "$@"; do
    case "$arg" in
        --dry-run)     DRY_RUN=1 ;;
        --with-ffmpeg) WITH_FFMPEG=1 ;;
        -h|--help)     sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)             printf '  !!  unknown option: %s\n' "$arg" >&2; exit 2 ;;
    esac
done

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_CYAN=$(printf '\033[36m'); C_GREEN=$(printf '\033[32m')
    C_GREY=$(printf '\033[90m'); C_RED=$(printf '\033[31m')
    C_YELLOW=$(printf '\033[33m'); C_OFF=$(printf '\033[0m')
else
    C_CYAN=''; C_GREEN=''; C_GREY=''; C_RED=''; C_YELLOW=''; C_OFF=''
fi

step() { printf '  %s->%s %s\n' "$C_CYAN" "$C_OFF" "$1"; }
ok()   { printf '  %sok%s  %s\n' "$C_GREEN" "$C_OFF" "$1"; }
note() { printf '      %s%s%s\n' "$C_GREY" "$1" "$C_OFF"; }
fail() { printf '  %s!!%s  %s\n' "$C_RED" "$C_OFF" "$1" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        note "would run: $*"
        return 0
    fi
    "$@"
}

printf '\n  anime-sh\n  %s========%s\n' "$C_GREY" "$C_OFF"
[ "$DRY_RUN" -eq 1 ] && printf '  %sDRY RUN - nothing will be changed%s\n' "$C_YELLOW" "$C_OFF"
printf '\n'

# Running this as root installs into root's home, and then it is not on the
# PATH of the account that will actually use it.
if [ "$(id -u)" = "0" ] && [ -z "${ANIME_SH_ALLOW_ROOT:-}" ]; then
    fail 'Running as root.'
    note 'uv installs per-user, so this would land in root'"'"'s home directory'
    note 'and not on your own PATH. Run it as your normal user; it will use'
    note 'sudo only for the system package manager, and ask you then.'
    exit 1
fi

# -- what package manager does this machine have? ---------------------------- #
# Only used for mpv and ffmpeg. anime-sh itself comes from uv on every system,
# so there is one install path to keep working rather than five.
SUDO=''
if [ "$(id -u)" != "0" ] && have sudo; then SUDO='sudo'; fi

detect_pm() {
    if have brew;    then echo 'brew';   return; fi
    if have apt-get; then echo 'apt';    return; fi
    if have dnf;     then echo 'dnf';    return; fi
    if have pacman;  then echo 'pacman'; return; fi
    if have zypper;  then echo 'zypper'; return; fi
    if have apk;     then echo 'apk';    return; fi
    echo ''
}

install_system_pkg() {
    pkg="$1"
    case "$(detect_pm)" in
        brew)   run brew install "$pkg" ;;
        apt)    run $SUDO apt-get update -qq && run $SUDO apt-get install -y "$pkg" ;;
        dnf)    run $SUDO dnf install -y "$pkg" ;;
        pacman) run $SUDO pacman -S --needed --noconfirm "$pkg" ;;
        zypper) run $SUDO zypper install -y "$pkg" ;;
        apk)    run $SUDO apk add "$pkg" ;;
        *)
            fail "No supported package manager found, so $pkg cannot be installed automatically."
            note "Install $pkg yourself, then run this again."
            return 1
            ;;
    esac
}

# -- 1. mpv ------------------------------------------------------------------ #
# Required, not optional: anime-sh hands the stream to mpv and plays nothing
# without it.
if have mpv; then
    ok "mpv is already installed ($(command -v mpv))"
else
    step 'Installing mpv (this is what plays the video)'
    install_system_pkg mpv || exit 1
fi

# -- 2. ffmpeg, only if asked ------------------------------------------------ #
if [ "$WITH_FFMPEG" -eq 1 ]; then
    if have ffmpeg; then
        ok 'ffmpeg is already installed'
    else
        step 'Installing ffmpeg (for anime download)'
        install_system_pkg ffmpeg || exit 1
    fi
fi

# -- 3. uv ------------------------------------------------------------------- #
if have uv; then
    ok "uv is already installed ($(uv --version 2>/dev/null || echo 'version unknown'))"
else
    step 'Installing uv (a single binary that brings its own Python)'
    if [ "$DRY_RUN" -eq 1 ]; then
        note 'would run: curl -LsSf https://astral.sh/uv/install.sh | sh'
    else
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    # uv's installer writes to ~/.local/bin and updates the shell profile, but
    # this shell has already read its profile - so put it on PATH by hand.
    [ -d "$HOME/.local/bin" ] && PATH="$HOME/.local/bin:$PATH" && export PATH
    if [ "$DRY_RUN" -eq 0 ] && ! have uv; then
        fail 'uv installed but is not on PATH in this shell.'
        note 'Open a new terminal and run this script again.'
        exit 1
    fi
fi

# -- 4. anime-sh ------------------------------------------------------------- #
# The [tui] extra is not optional in practice: without it `anime` still runs and
# `anime --version` still answers, so a missing extra is easy not to notice
# until the interface will not start.
if have anime; then
    step 'anime-sh is already installed - checking for an update'
    run uv tool upgrade anime-sh
else
    step 'Installing anime-sh'
    run uv tool install "anime-sh[tui]"
fi

# -- 5. PATH ----------------------------------------------------------------- #
# `uv tool update-shell` edits the right profile file for the shell you use, so
# `anime` is there next time without anyone editing a dotfile by hand.
step 'Making sure anime is on your PATH'
run uv tool update-shell
[ -d "$HOME/.local/bin" ] && PATH="$HOME/.local/bin:$PATH" && export PATH

if [ "$DRY_RUN" -eq 1 ]; then
    printf '\n  %sDry run finished. Nothing was changed.%s\n\n' "$C_YELLOW" "$C_OFF"
    exit 0
fi

# -- 6. prove it works ------------------------------------------------------- #
printf '\n'
if ! have anime; then
    fail 'Installed, but anime is not on PATH in this shell yet.'
    note 'Open a new terminal, then run: anime doctor'
    exit 1
fi

ok "$(anime --version 2>&1)"
printf '\n  Checking the environment:\n'
anime doctor || true
printf '\n  Start it with: %sanime%s\n\n' "$C_CYAN" "$C_OFF"
