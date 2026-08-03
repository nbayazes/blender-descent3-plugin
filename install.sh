#!/usr/bin/env bash
#
# Install the Descent 3 POF/OOF add-on into your Blender user add-ons directory.
#
# Finds every Blender install -- Steam (via libraryfolders.vdf), distro packages,
# .tar.xz/portable builds, Flatpak, Snap, macOS app bundles -- works out each one's
# major.minor version, and copies the descent3_importer package folder into that
# version's user add-ons directory:
#
#   Linux    ${XDG_CONFIG_HOME:-~/.config}/blender/<ver>/scripts/addons/descent3_importer/
#   macOS    ~/Library/Application Support/Blender/<ver>/scripts/addons/descent3_importer/
#   Windows  %APPDATA%/Blender Foundation/Blender/<ver>/scripts/addons/descent3_importer/
#
# The add-on MUST be installed as a package folder (it uses `from . import poformat`),
# so this script always copies the whole folder, never loose files.
#
# Note: Steam and standalone Blender share the same user config directory, so a given
# version only needs to be installed once regardless of where the binary lives.
#
# Usage:
#   ./install.sh                       install into the newest Blender found
#   ./install.sh --list                show every install found, then exit
#   ./install.sh --all                 install into every version found
#   ./install.sh --version 4.2         install into a specific version
#   ./install.sh --target <path>       install into an explicit path
#   ./install.sh --dry-run             print what would happen, change nothing
#   ./install.sh --clean               wipe the destination folder first

set -u

ADDON_ID="descent3_importer"
REQUIRED_FILES="__init__.py constants.py export_pof.py import_pof.py poformat.py texutil.py"
TAB=$'\t'

OPT_LIST=0
OPT_ALL=0
OPT_DRYRUN=0
OPT_CLEAN=0
OPT_VERSION=""
OPT_TARGET=""

# ------------------------------------------------------------- output helpers

if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_DIM=$'\033[2m'
else
    C_RESET=""; C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""
fi

step() { printf '%s==> %s%s\n' "$C_CYAN" "$*" "$C_RESET"; }
ok()   { printf '%s    %s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
note() { printf '%s    %s%s\n' "$C_DIM" "$*" "$C_RESET"; }
warn() { printf '%s  ! %s%s\n' "$C_YELLOW" "$*" "$C_RESET"; }
fail() { printf '%s  x %s%s\n' "$C_RED" "$*" "$C_RESET"; }

usage() {
    sed -n '3,27p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

# ------------------------------------------------------------- argument parsing

while [ $# -gt 0 ]; do
    case "$1" in
        --list)     OPT_LIST=1 ;;
        --all)      OPT_ALL=1 ;;
        --dry-run)  OPT_DRYRUN=1 ;;
        --clean)    OPT_CLEAN=1 ;;
        --version)  shift; [ $# -gt 0 ] || { fail "--version needs a value, e.g. --version 4.2"; exit 2; }; OPT_VERSION="$1" ;;
        --target)   shift; [ $# -gt 0 ] || { fail "--target needs a path"; exit 2; }; OPT_TARGET="$1" ;;
        -h|--help)  usage 0 ;;
        *)          fail "unknown option: $1"; usage 2 ;;
    esac
    shift
done

# ------------------------------------------------------------------ platform

case "$(uname -s)" in
    Linux*)                 PLATFORM="linux" ;;
    Darwin*)                PLATFORM="macos" ;;
    MINGW*|MSYS*|CYGWIN*)   PLATFORM="windows" ;;
    *)                      PLATFORM="linux" ;;
esac

config_root() {
    case "$PLATFORM" in
        macos)   printf '%s\n' "$HOME/Library/Application Support/Blender" ;;
        windows) printf '%s\n' "$(winpath "${APPDATA:-$HOME/AppData/Roaming}")/Blender Foundation/Blender" ;;
        *)       printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/blender" ;;
    esac
}

# Windows-only helpers (Git Bash / MSYS). Backslash paths work unevenly with test -d,
# so normalise them; "ProgramFiles(x86)" cannot be referenced as a shell variable.
winpath() { printf '%s\n' "$1" | tr '\\' '/'; }

win_env() {
    line="$(env | grep -i "^$1=" | head -1)"
    [ -n "$line" ] || return 1
    winpath "${line#*=}"
}

# --------------------------------------------------------------- source package

script_dir() {
    cd "$(dirname "$0")" && pwd
}

SOURCE_DIR="$(script_dir)/$ADDON_ID"

check_source() {
    if [ ! -d "$SOURCE_DIR" ]; then
        fail "add-on source folder not found: $SOURCE_DIR"
        fail "run this script from the repository root"
        exit 1
    fi
    for f in $REQUIRED_FILES; do
        if [ ! -f "$SOURCE_DIR/$f" ]; then
            fail "add-on source is incomplete -- missing $f in $SOURCE_DIR"
            exit 1
        fi
    done
}

addon_version() {
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*(\([0-9]*\)[[:space:]]*,[[:space:]]*\([0-9]*\)[[:space:]]*,[[:space:]]*\([0-9]*\)).*/\1.\2.\3/p' \
        "$SOURCE_DIR/__init__.py" | head -1
}

# -------------------------------------------------------------- steam discovery

# Prints one Steam library root per line.
steam_libraries() {
    roots=""
    case "$PLATFORM" in
        macos)
            roots="$HOME/Library/Application Support/Steam"
            ;;
        windows)
            # Steam records its own location in the registry; reg.exe is reachable
            # from Git Bash/MSYS and is far more reliable than guessing drives.
            for key in 'HKCU\Software\Valve\Steam//v//SteamPath' \
                       'HKLM\SOFTWARE\WOW6432Node\Valve\Steam//v//InstallPath'; do
                path_key="${key%%//v//*}"; value_name="${key##*//v//}"
                found="$(reg.exe query "$path_key" //v "$value_name" 2>/dev/null |
                         tr -d '\r' |
                         sed -n "s/.*$value_name[[:space:]]*REG_SZ[[:space:]]*//p" | head -1)"
                [ -n "$found" ] && roots="$roots
$(winpath "$found")"
            done
            for guess in "$(win_env 'ProgramFiles(x86)' || echo 'C:/Program Files (x86)')/Steam" \
                         "$(win_env 'ProgramFiles' || echo 'C:/Program Files')/Steam" \
                         "C:/Steam"; do
                roots="$roots
$guess"
            done
            ;;
        *)
            roots="$HOME/.steam/steam
$HOME/.steam/root
$HOME/.local/share/Steam
$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam"
            ;;
    esac

    printf '%s\n' "$roots" | while IFS= read -r root; do
        [ -n "$root" ] || continue
        [ -d "$root" ] || continue
        printf '%s\n' "$root"

        # Extra library folders; the vdf moved between Steam client versions.
        for vdf in "$root/steamapps/libraryfolders.vdf" "$root/config/libraryfolders.vdf"; do
            [ -f "$vdf" ] || continue
            sed -n 's/^[[:space:]]*"path"[[:space:]]*"\(.*\)"[[:space:]]*$/\1/p' "$vdf" |
                sed 's|\\\\|/|g' |
                while IFS= read -r lib; do
                    [ -n "$lib" ] && [ -d "$lib" ] && printf '%s\n' "$lib"
                done
        done
    done
}

# ------------------------------------------------------------ blender discovery

# version_from_resources <dir> -- Blender keeps its runtime data in a <major>.<minor>
# folder; reading that is instant and works for Steam builds, which carry no version
# anywhere in their path.
version_from_resources() {
    dir="$1"
    [ -d "$dir" ] || return 1
    found=""
    for entry in "$dir"/*; do
        [ -d "$entry" ] || continue
        base="$(basename "$entry")"
        case "$base" in
            [0-9]*.[0-9]*)
                if printf '%s' "$base" | grep -Eq '^[0-9]+\.[0-9]+$'; then
                    if [ -z "$found" ] || version_gt "$base" "$found"; then found="$base"; fi
                fi
                ;;
        esac
    done
    [ -n "$found" ] || return 1
    printf '%s\n' "$found"
}

version_gt() {
    a_major="${1%%.*}"; a_minor="${1#*.}"
    b_major="${2%%.*}"; b_minor="${2#*.}"
    [ "$a_major" -gt "$b_major" ] && return 0
    [ "$a_major" -lt "$b_major" ] && return 1
    [ "$a_minor" -gt "$b_minor" ]
}

# version_from_exe <exe> -- fall back to asking Blender itself (slow, ~1s).
version_from_exe() {
    exe="$1"
    [ -x "$exe" ] || return 1
    "$exe" --version 2>/dev/null | head -1 |
        sed -n 's/^Blender[[:space:]]\{1,\}\([0-9]\{1,\}\)\.\([0-9]\{1,\}\).*/\1.\2/p' |
        grep . || return 1
}

# emit_record <version> <source> <install_dir> [addons_dir] [priority]
# Records are sortable: <sortkey>\t<version>\t<source>\t<install_dir>\t<addons_dir>
# The sort key is <major><minor><priority>, zero-padded so a reverse sort yields
# newest-first, and so a real install outranks a bare leftover config dir for the
# same version (both map to the same add-ons directory, but only one is worth showing).
emit_record() {
    ver="$1"; src="$2"; install="$3"
    addons="${4:-$(config_root)/$ver/scripts/addons}"
    prio="${5:-1}"
    major="${ver%%.*}"; minor="${ver#*.}"
    printf '%09d%09d%d%s%s%s%s%s%s%s%s\n' \
        "$major" "$minor" "$prio" "$TAB" "$ver" "$TAB" "$src" "$TAB" "$install" "$TAB" "$addons"
}

# consider <source> <install_dir> <resources_dir> <exe>
consider() {
    src="$1"; install="$2"; resources="$3"; exe="$4"
    [ -d "$install" ] || return 0
    ver="$(version_from_resources "$resources" 2>/dev/null)" || ver=""
    if [ -z "$ver" ] && [ -n "$exe" ]; then
        ver="$(version_from_exe "$exe" 2>/dev/null)" || ver=""
    fi
    [ -n "$ver" ] || return 0
    emit_record "$ver" "$src" "$install"
}

discover_installs() {
    # --- Steam ---------------------------------------------------------------
    steam_libraries | sort -u | while IFS= read -r lib; do
        case "$PLATFORM" in
            macos)
                app="$lib/steamapps/common/Blender/Blender.app"
                consider "Steam" "$app" "$app/Contents/Resources" "$app/Contents/MacOS/Blender"
                ;;
            windows)
                dir="$lib/steamapps/common/Blender"
                consider "Steam" "$dir" "$dir" "$dir/blender.exe"
                ;;
            *)
                dir="$lib/steamapps/common/Blender"
                consider "Steam" "$dir" "$dir" "$dir/blender"
                ;;
        esac
    done

    # --- Standalone / packaged ----------------------------------------------
    case "$PLATFORM" in
        macos)
            for app in "/Applications/Blender.app" "$HOME/Applications/Blender.app" \
                       /Applications/Blender*.app "$HOME"/Applications/Blender*.app; do
                [ -d "$app" ] || continue
                consider "Standalone" "$app" "$app/Contents/Resources" "$app/Contents/MacOS/Blender"
            done
            ;;
        windows)
            for root in "$(win_env 'ProgramFiles' || echo 'C:/Program Files')/Blender Foundation" \
                        "$(win_env 'ProgramFiles(x86)' || echo 'C:/Program Files (x86)')/Blender Foundation" \
                        "$(win_env 'LOCALAPPDATA' || echo "$HOME/AppData/Local")/Programs/Blender Foundation"; do
                [ -d "$root" ] || continue
                for dir in "$root"/Blender*; do
                    [ -d "$dir" ] || continue
                    consider "Standalone" "$dir" "$dir" "$dir/blender.exe"
                done
            done
            ;;
        *)
            # Distro packages keep the runtime data away from the binary.
            for share in /usr/share/blender /usr/local/share/blender; do
                exe="$(dirname "$(dirname "$share")")/bin/blender"
                consider "Package" "$share" "$share" "$exe"
            done
            # Portable .tar.xz extractions. Deliberately not testing -x on the binary:
            # an archive extracted without the exec bit is still a valid install, and
            # consider() already requires a real <major>.<minor> runtime folder.
            for dir in /opt/blender* /usr/local/blender* "$HOME"/blender* \
                       "$HOME"/.local/share/blender* "$HOME"/Downloads/blender*; do
                [ -d "$dir" ] || continue
                [ -e "$dir/blender" ] || continue
                consider "Portable" "$dir" "$dir" "$dir/blender"
            done
            # Snap
            for dir in /snap/blender/current /var/lib/snapd/snap/blender/current; do
                [ -d "$dir" ] || continue
                ver="$(version_from_resources "$dir" 2>/dev/null)" || ver=""
                [ -n "$ver" ] || continue
                emit_record "$ver" "Snap" "$dir" "$HOME/snap/blender/current/.config/blender/$ver/scripts/addons"
            done
            ;;
    esac

    # --- Flatpak (config lives in its own sandbox) ---------------------------
    flatpak_config="$HOME/.var/app/org.blender.Blender/config/blender"
    if [ -d "$flatpak_config" ]; then
        for dir in "$flatpak_config"/*; do
            [ -d "$dir" ] || continue
            ver="$(basename "$dir")"
            printf '%s' "$ver" | grep -Eq '^[0-9]+\.[0-9]+$' || continue
            emit_record "$ver" "Flatpak" "org.blender.Blender" "$dir/scripts/addons"
        done
    fi

    # --- Anything on PATH, plus an explicit override -------------------------
    for exe in "${BLENDER_EXE:-}" "$(command -v blender 2>/dev/null || true)"; do
        [ -n "$exe" ] || continue
        [ -x "$exe" ] || continue
        real="$exe"
        # Resolve one level of symlink (e.g. /usr/local/bin/blender -> /opt/blender/blender)
        if [ -L "$exe" ]; then
            link="$(readlink "$exe")"
            case "$link" in
                /*) real="$link" ;;
                *)  real="$(dirname "$exe")/$link" ;;
            esac
        fi
        dir="$(dirname "$real")"
        consider "PATH" "$dir" "$dir" "$real"
    done

    # --- Versions with an existing config but no binary we could find --------
    root="$(config_root)"
    if [ -d "$root" ]; then
        for dir in "$root"/*; do
            [ -d "$dir" ] || continue
            ver="$(basename "$dir")"
            printf '%s' "$ver" | grep -Eq '^[0-9]+\.[0-9]+$' || continue
            emit_record "$ver" "existing config" "-" "" 0
        done
    fi
}

# Dedupe by version, keeping the first (highest-priority) record, newest first.
collect_targets() {
    discover_installs | sort -r | awk -F"$TAB" '!seen[$2]++'
}

# ------------------------------------------------------------------- the install

resolve_target_path() {
    path="$1"
    leaf="$(basename "$path")"
    if [ "$leaf" = "$ADDON_ID" ]; then
        dirname "$path"
    elif printf '%s' "$leaf" | grep -Eq '^[0-9]+\.[0-9]+$'; then
        printf '%s\n' "$path/scripts/addons"
    else
        printf '%s\n' "$path"
    fi
}

install_addon() {
    addons_dir="$1"
    dest="$addons_dir/$ADDON_ID"
    step "Installing to $dest"

    # Loose .py files directly in scripts/addons break the package import and make the
    # operators silently fail to register. Flag them; --clean removes them.
    for f in $REQUIRED_FILES; do
        [ -f "$addons_dir/$f" ] || continue
        if [ "$OPT_CLEAN" -eq 1 ] && [ "$OPT_DRYRUN" -eq 0 ]; then
            rm -f "$addons_dir/$f"
            warn "removed stray loose file $f from scripts/addons"
        elif [ "$OPT_CLEAN" -eq 1 ]; then
            warn "would remove stray loose file $f from scripts/addons"
        else
            warn "stray loose file in scripts/addons: $f -- re-run with --clean to remove it (it can stop the add-on loading)"
        fi
    done

    if [ "$OPT_CLEAN" -eq 1 ] && [ -d "$dest" ]; then
        if [ "$OPT_DRYRUN" -eq 1 ]; then
            note "would delete existing $dest"
        else
            rm -rf "$dest"
            note "deleted existing $dest"
        fi
    fi

    if [ "$OPT_DRYRUN" -eq 1 ]; then
        note "would create $dest"
        for f in "$SOURCE_DIR"/*.py; do
            [ -f "$f" ] && note "would copy $(basename "$f")"
        done
        return 0
    fi

    if ! mkdir -p "$dest"; then
        fail "could not create $dest"
        return 1
    fi

    copied=""
    for f in "$SOURCE_DIR"/*.py; do
        [ -f "$f" ] || continue
        if ! cp -f "$f" "$dest/"; then
            fail "could not copy $(basename "$f") to $dest"
            return 1
        fi
        copied="$copied $(basename "$f")"
    done
    ok "copied:$copied"

    # Stale bytecode from a previous version shadows the new code.
    if [ -d "$dest/__pycache__" ]; then
        rm -rf "$dest/__pycache__"
        note "cleared __pycache__"
    fi

    # Modules removed from the repo but still sitting in the destination
    for f in "$dest"/*.py; do
        [ -f "$f" ] || continue
        base="$(basename "$f")"
        if [ ! -f "$SOURCE_DIR/$base" ]; then
            warn "stale file left behind: $base (use --clean to remove)"
        fi
    done

    for f in $REQUIRED_FILES; do
        if [ ! -f "$dest/$f" ]; then
            fail "verification failed -- $f is missing from $dest"
            return 1
        fi
    done
    return 0
}

# ------------------------------------------------------------------------- main

check_source
step "Add-on source: $SOURCE_DIR (v$(addon_version))"

if [ -n "$OPT_TARGET" ]; then
    addons_dir="$(resolve_target_path "$OPT_TARGET")"
    if install_addon "$addons_dir"; then
        echo
        printf '%sDone. Restart Blender (or toggle the add-on off/on in Preferences) so the new code loads.%s\n' "$C_GREEN" "$C_RESET"
        exit 0
    fi
    exit 1
fi

TARGETS="$(collect_targets)"

if [ -z "$TARGETS" ]; then
    fail "No Blender installation found."
    echo
    echo "Searched:"
    echo "  - Steam libraries (libraryfolders.vdf)"
    case "$PLATFORM" in
        macos)   echo "  - /Applications/Blender.app, ~/Applications/Blender.app" ;;
        windows) echo "  - %ProgramFiles%/Blender Foundation, %LOCALAPPDATA%/Programs/Blender Foundation" ;;
        *)       echo "  - /usr/share/blender, /opt/blender*, ~/blender*, Snap, Flatpak" ;;
    esac
    echo "  - \$BLENDER_EXE, PATH"
    echo "  - $(config_root)"
    echo
    echo "If Blender lives somewhere else, point at it directly:"
    echo "  ./install.sh --target /path/to/blender/config/4.2"
    exit 1
fi

if [ "$OPT_LIST" -eq 1 ]; then
    echo
    printf '%s\n' "$TARGETS" | while IFS="$TAB" read -r _key ver src install addons; do
        printf '  Blender %-6s [%s]\n' "$ver" "$src"
        [ "$install" = "-" ] || note "path:   $install"
        if [ -d "$addons/$ADDON_ID" ]; then
            ok   "addons: $addons  [add-on installed]"
        else
            note "addons: $addons"
        fi
    done
    echo
    note "Steam and standalone Blender share one user config dir per version, so each"
    note "version above only needs installing once."
    exit 0
fi

SELECTED="$TARGETS"
if [ -n "$OPT_VERSION" ]; then
    SELECTED="$(printf '%s\n' "$TARGETS" | awk -F"$TAB" -v v="$OPT_VERSION" '$2 == v')"
    if [ -z "$SELECTED" ]; then
        available="$(printf '%s\n' "$TARGETS" | cut -d"$TAB" -f2 | tr '\n' ' ')"
        fail "No Blender $OPT_VERSION found. Available: $available"
        exit 1
    fi
elif [ "$OPT_ALL" -eq 0 ]; then
    SELECTED="$(printf '%s\n' "$TARGETS" | head -1)"
    count="$(printf '%s\n' "$TARGETS" | wc -l | tr -d ' ')"
    if [ "$count" -gt 1 ]; then
        newest="$(printf '%s\n' "$SELECTED" | cut -d"$TAB" -f2)"
        note "$count versions found; installing to newest ($newest). Use --all or --version to change that."
    fi
fi

[ "$OPT_DRYRUN" -eq 1 ] && note "DRY RUN -- nothing will be written"

FAILURES=0
while IFS="$TAB" read -r _key ver src _install addons; do
    [ -n "${ver:-}" ] || continue
    echo
    printf 'Blender %s  [%s]\n' "$ver" "$src"
    install_addon "$addons" || FAILURES=$((FAILURES + 1))
done <<EOF
$SELECTED
EOF

echo
if [ "$FAILURES" -gt 0 ]; then
    fail "$FAILURES install(s) failed."
    exit 1
fi
if [ "$OPT_DRYRUN" -eq 0 ]; then
    printf '%sDone. In Blender: Edit > Preferences > Add-ons, enable "Descent 3 POF/OOF Importer/Exporter".%s\n' "$C_GREEN" "$C_RESET"
    printf '%sIf it was already enabled, restart Blender (or toggle it off/on) so the new code loads.%s\n' "$C_GREEN" "$C_RESET"
fi
exit 0
