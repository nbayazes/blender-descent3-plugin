#!/usr/bin/env bash
#
# Run the test suite using Blender's own Python interpreter.
#
# The suite is bpy-free, so it does not need Blender running -- only Blender's
# interpreter. Using that instead of your system Python is what proves the code
# works on the Python version your users actually have: a stdlib module or a
# syntax feature present in one and not the other would pass locally and fail
# on their machine.
#
# This talks to Blender's python.exe directly rather than launching Blender, so
# it costs no startup time. Tests that genuinely need bpy live in
# tests/blender/ and are run through Blender itself -- see docs/development.md.
#
# Usage:
#   ./run_tests.sh                     find Blender, run the whole suite
#   ./run_tests.sh -k naming -v        extra arguments go straight to pytest
#   ./run_tests.sh --blender <path>    use a specific Blender executable
#   ./run_tests.sh --system            use the system Python instead
#   BLENDER=<path> ./run_tests.sh      same as --blender
#
# Without --blender or $BLENDER it looks on PATH, then in the locations
# Blender's own installers use, then in every Steam library Steam itself has a
# record of, then at whichever Blender the last run used. A portable build
# unpacked somewhere of your own has to be named once; `./install.sh --list`
# prints every Blender on this machine.
#
# pytest is installed on first use into .pytest-blender/ in this repo -- not
# into the Blender install, so a Blender update cannot remove it.

set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENDOR_DIR="$REPO_DIR/.pytest-blender"
# Where the interpreter lookup is remembered between runs; see blender_python().
CACHE_FILE="$VENDOR_DIR/.interpreter"
BLENDER_EXE="${BLENDER:-}"
USE_SYSTEM=0
PYTEST_ARGS=()

C_RESET=''; C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''
if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'; C_DIM=$'\033[2m'
fi
step() { printf '%s==> %s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
note() { printf '%s    %s%s\n' "$C_DIM" "$*" "$C_RESET"; }
warn() { printf '%s  ! %s%s\n' "$C_YELLOW" "$*" "$C_RESET"; }
fail() { printf '%s  x %s%s\n' "$C_RED" "$*" "$C_RESET"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --blender) shift; [ $# -gt 0 ] || { fail "--blender needs a path"; exit 2; }; BLENDER_EXE="$1" ;;
        --system)  USE_SYSTEM=1 ;;
        -h|--help) sed -n '3,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         PYTEST_ARGS+=("$1") ;;
    esac
    shift
done

# ------------------------------------------------------------- find Blender

# Windows paths arrive from the environment with backslashes, which glob
# traversal handles unevenly; MSYS accepts either separator. install.sh carries
# the same helper for the same reason.
winpath() { printf '%s\n' "$1" | tr '\\' '/'; }

# "ProgramFiles(x86)" cannot be referenced as a shell variable -- the parentheses
# are not valid in a name -- so it is read out of the environment by hand.
# install.sh carries this helper too, for the same one variable.
win_env() {
    line="$(env | grep -i "^$1=" | head -1)"
    [ -n "$line" ] || return 1
    winpath "${line#*=}"
}

# Every Steam library on this machine, one per line, read out of Steam's own
# record of where they are: its install path, and the libraryfolders.vdf that
# lists the rest. A literal /e/Steam/... used to head the candidate list below --
# it found Blender for whoever added it, found nothing for anyone else, and made
# the script look like it had searched when it had not. Asking Steam is the
# opposite: it covers every drive without naming one. install.sh reads the same
# two sources, in more detail.
steam_libraries() {
    case "$(uname -s)" in
        Darwin*) roots="$HOME/Library/Application Support/Steam" ;;
        MINGW*|MSYS*|CYGWIN*)
            # reg.exe is reachable from Git Bash and beats guessing drives.
            roots="$(reg.exe query 'HKCU\Software\Valve\Steam' //v SteamPath 2>/dev/null |
                     tr -d '\r' |
                     sed -n 's/.*SteamPath[[:space:]]*REG_SZ[[:space:]]*//p' | head -1)"
            roots="$(winpath "$roots")
$(win_env 'ProgramFiles(x86)' || echo 'C:/Program Files (x86)')/Steam
$(win_env 'ProgramFiles' || echo 'C:/Program Files')/Steam
C:/Steam"
            ;;
        *)  roots="$HOME/.steam/steam
$HOME/.local/share/Steam
$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam" ;;
    esac

    printf '%s\n' "$roots" | while IFS= read -r root; do
        [ -n "$root" ] && [ -d "$root" ] || continue
        printf '%s\n' "$root"
        # The vdf moved between Steam client versions, so both places are read.
        for vdf in "$root/steamapps/libraryfolders.vdf" "$root/config/libraryfolders.vdf"; do
            [ -f "$vdf" ] || continue
            sed -n 's/^[[:space:]]*"path"[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$vdf" |
                sed 's|\\\\|/|g'
        done
    done
}

# Candidate Blender executables, most specific first. Every one is PATH, a
# location an official Blender installer uses by default, or a Steam library
# Steam itself named -- spelled out of $PROGRAMFILES/$LOCALAPPDATA/$HOME and the
# registry, never a drive letter, never a path off one developer's machine.
#
# Deliberately still shorter than install.sh, which does the exhaustive
# discovery (Flatpak, Snap, distro packages, portable builds) and prints what it
# finds with `./install.sh --list` -- which is what the failure message points at.
blender_candidates() {
    command -v blender 2>/dev/null
    for base in \
        "$(winpath "${PROGRAMFILES:-}")/Blender Foundation"/Blender*/blender.exe \
        "$(winpath "${LOCALAPPDATA:-}")/Programs/Blender Foundation"/Blender*/blender.exe \
        /Applications/Blender.app/Contents/MacOS/Blender \
        "$HOME/Applications/Blender.app/Contents/MacOS/Blender" \
        /usr/bin/blender /usr/local/bin/blender
    do
        [ -x "$base" ] && printf '%s\n' "$base"
    done
    # .exe before the extensionless name, which is not the platform showing
    # through: MSYS makes `test -x blender` true for a file called blender.exe,
    # so the bare name would be reported and cached as a path that does not
    # exist as spelled -- and run_tests.ps1 reads that same cache file.
    steam_libraries | sort -u | while IFS= read -r library; do
        for exe in "$library/steamapps/common/Blender/blender.exe" \
                   "$library/steamapps/common/Blender/blender" \
                   "$library/steamapps/common/Blender/Blender.app/Contents/MacOS/Blender"
        do
            [ -x "$exe" ] && printf '%s\n' "$exe"
        done
    done
    cached_blender
}

# The Blender a previous run resolved an interpreter for. Tried last, so it only
# ever rescues the case the searches above cannot reach -- a portable build
# unpacked somewhere of the user's own -- and then only after --blender named it
# once. Remembering the machine you are on is the honest version of the
# hardcoded Steam path this list used to carry; assuming everyone shares it was
# not, and neither was sweeping drive letters and calling that a search.
cached_blender() {
    exe="$(cache_line 1)" || return 0
    [ -n "$exe" ] && [ -x "$exe" ] && printf '%s\n' "$exe"
    return 0
}

find_blender() {
    blender_candidates | while read -r candidate; do
        [ -n "$candidate" ] && [ -x "$candidate" ] && { printf '%s\n' "$candidate"; break; }
    done
}

# Ask Blender where its interpreter is rather than rebuilding the path: the
# version directory between the executable and python/bin changes with every
# release, and portable and Steam builds lay out differently.
#
# That costs a Blender launch, which is most of this script's runtime -- and
# the whole point of talking to the interpreter directly is to avoid paying for
# Blender startup. So the answer is cached in $CACHE_FILE, keyed by the
# executable it came from, and re-derived whenever that changes or the cached
# path stops existing.

# Line <n> of the cache, or failure if there is no cache to read. run_tests.ps1
# writes this same file, and Windows PowerShell's `Set-Content -Encoding utf8`
# puts a BOM at the front of it, so line 1 read back raw is three invisible
# bytes longer than the path it names -- the comparison below then never
# matches and every run pays for a Blender launch it did not need.
cache_line() {
    [ -r "$CACHE_FILE" ] || return 1
    line="$(sed -n "${1}p" "$CACHE_FILE" | tr -d '\r')"
    bom=$'\xef\xbb\xbf'
    printf '%s\n' "${line#"$bom"}"
}

blender_python() {
    exe="$1"
    if [ -r "$CACHE_FILE" ]; then
        cached_exe="$(cache_line 1)"
        cached_py="$(cache_line 2)"
        if [ "$cached_exe" = "$exe" ] && [ -x "$cached_py" ]; then
            printf '%s\n' "$cached_py"
            return 0
        fi
    fi

    resolved="$("$exe" --background --factory-startup --python-expr \
        "import sys; print('PYEXE:' + sys.executable)" 2>/dev/null |
        sed -n 's/^PYEXE://p' | head -1 | tr -d '\r')"
    if [ -n "$resolved" ] && [ -x "$resolved" ]; then
        mkdir -p "$VENDOR_DIR" 2>/dev/null
        printf '%s\n%s\n' "$exe" "$resolved" > "$CACHE_FILE" 2>/dev/null
    fi
    printf '%s\n' "$resolved"
}

if [ "$USE_SYSTEM" -eq 1 ]; then
    PYTHON="$(command -v python3 || command -v python)"
    [ -n "$PYTHON" ] || { fail "no system python found"; exit 1; }
    step "Using system Python"
else
    # An explicitly named Blender is a requirement, not a hint. Falling back to
    # a different one would run the tests against an interpreter the caller did
    # not ask for, and say nothing about having done so.
    if [ -n "$BLENDER_EXE" ]; then
        if [ ! -x "$BLENDER_EXE" ]; then
            fail "not an executable Blender: $BLENDER_EXE"
            note "it was given explicitly, so no fallback is applied"
            exit 1
        fi
        exe="$BLENDER_EXE"
    else
        exe="$(find_blender)"
    fi
    if [ -z "$exe" ]; then
        fail "could not find a Blender executable"
        note "not on PATH, not in a standard install location, not in any Steam"
        note "library, and no previous run to remember. Point this script at"
        note "yours -- any one of:"
        note "  ./run_tests.sh --blender /path/to/blender"
        note "  BLENDER=/path/to/blender ./run_tests.sh"
        note "  ./install.sh --list      # prints every Blender on this machine"
        note "  ./run_tests.sh --system  # use the system Python instead"
        exit 1
    fi
    step "Blender: $exe"
    PYTHON="$(blender_python "$exe")"
    if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
        fail "Blender did not report a usable interpreter"
        note "falling back is not automatic -- rerun with --system if you want that"
        exit 1
    fi
fi

version="$("$PYTHON" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null)"
note "Python $version  ($PYTHON)"

# ------------------------------------------------------------------- pytest

if ! "$PYTHON" -c 'import pytest' >/dev/null 2>&1; then
    if [ ! -d "$VENDOR_DIR" ] || ! PYTHONPATH="$VENDOR_DIR" "$PYTHON" -c 'import pytest' >/dev/null 2>&1; then
        step "Installing pytest into .pytest-blender/ (first run only)"
        note "goes in the repo, not the Blender install, so a Blender update cannot remove it"
        if ! "$PYTHON" -m pip install --quiet --disable-pip-version-check --target "$VENDOR_DIR" pytest; then
            fail "could not install pytest"
            note "install it yourself with:"
            note "  \"$PYTHON\" -m pip install --target \"$VENDOR_DIR\" pytest"
            exit 1
        fi
    fi
    export PYTHONPATH="$VENDOR_DIR${PYTHONPATH:+:$PYTHONPATH}"
fi

# ---------------------------------------------------------------------- run

set -- "${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}"
[ $# -gt 0 ] || set -- -q

step "Running tests"
cd "$REPO_DIR" || exit 1
"$PYTHON" -m pytest "$@"
status=$?

if [ $status -eq 0 ]; then
    printf '%s==> all tests passed%s\n' "$C_GREEN" "$C_RESET"
else
    warn "pytest exited with $status"
fi
exit $status
