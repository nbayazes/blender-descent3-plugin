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
# tests/blender/ and are run through Blender itself -- see the README.
#
# Usage:
#   ./run_tests.sh                     find Blender, run the whole suite
#   ./run_tests.sh -k naming -v        extra arguments go straight to pytest
#   ./run_tests.sh --blender <path>    use a specific Blender executable
#   ./run_tests.sh --system            use the system Python instead
#   BLENDER=<path> ./run_tests.sh      same as --blender
#
# pytest is installed on first use into .pytest-blender/ in this repo -- not
# into the Blender install, so a Blender update cannot remove it.

set -u

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
VENDOR_DIR="$REPO_DIR/.pytest-blender"
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
        -h|--help) sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         PYTEST_ARGS+=("$1") ;;
    esac
    shift
done

# ------------------------------------------------------------- find Blender

# Candidate Blender executables, most specific first. Deliberately a short list:
# install.sh does exhaustive discovery, and `./install.sh --list` will print
# every Blender it finds if the guesses here miss.
blender_candidates() {
    command -v blender 2>/dev/null
    for base in \
        "/c/Program Files/Blender Foundation"/Blender*/blender.exe \
        "/e/Steam/steamapps/common/Blender/blender.exe" \
        "/c/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe" \
        "$HOME/.steam/steam/steamapps/common/Blender/blender" \
        /Applications/Blender.app/Contents/MacOS/Blender \
        /usr/bin/blender /usr/local/bin/blender
    do
        [ -x "$base" ] && printf '%s\n' "$base"
    done
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
# Blender startup. So the answer is cached, keyed by the executable it came
# from, and re-derived whenever that changes or the cached path stops existing.
CACHE_FILE="$VENDOR_DIR/.interpreter"

blender_python() {
    exe="$1"
    if [ -r "$CACHE_FILE" ]; then
        cached_exe="$(sed -n '1p' "$CACHE_FILE")"
        cached_py="$(sed -n '2p' "$CACHE_FILE")"
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
        note "pass one with --blender <path>, set BLENDER=<path>,"
        note "or run './install.sh --list' to see every Blender found"
        note "or run './run_tests.sh --system' to use the system Python"
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
