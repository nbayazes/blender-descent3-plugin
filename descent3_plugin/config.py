"""Per-project configuration, read from a ``descent3.toml`` beside the models.

The settings here are *conventions*, not preferences: the importer and the
exporter must agree on them or a model stops surviving a round trip. If import
names a gun empty ``Gun_0`` and export looks for ``gun.0``, the point is
silently dropped. Both halves therefore read the same file, and the defaults
below are exactly the values the add-on used before the file existed -- a
project with no ``descent3.toml`` behaves as it always did.

This is deliberately not the home for *user* preferences (where my texture
library lives, how big the viewport markers are). Those belong in the add-on
preferences, which get a UI and follow the user between projects, rather than
in a file checked in next to somebody's assets.

Format constants -- version gates, chunk IDs, flag bits -- are not here and
must not be: they are facts about a binary format, not settings, and a project
that "configured" them would simply produce corrupt models.

Free of any ``bpy`` dependency so it can be unit-tested without Blender.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace

from .constants import (
    ATTACH_EMPTY_PREFIX,
    GUN_EMPTY_PREFIX,
    SUBMODEL_FALLBACK_PREFIX,
    UV_LAYER_NAME,
)
from .poformat import MIN_OBJFILE_VERSION, OBJFILE_VERSION
from .texutil import IMAGE_EXTENSIONS

log = logging.getLogger(__package__)

# A capability probe, not a swallowed error: tomllib is stdlib from Python
# 3.11, which covers every Blender this add-on supports, so this is belt and
# braces. Nothing is logged here because the package is still being imported
# and register() has not configured logging yet -- load_config() checks for the
# None and returns a warning the operator shows the user.
try:
    import tomllib
except ImportError:  # pragma: no cover - not reachable on Blender 4.2+
    tomllib = None

#: File the add-on looks for, walking up from the model being imported or
#: exported. Named after the game rather than the add-on so a project can be
#: shared between tools.
CONFIG_FILENAME = "descent3.toml"

#: How many parent directories to search before giving up. Deep enough to find
#: a config at a project root from ``<project>/models/ship/`` without walking
#: to the filesystem root and picking up an unrelated file.
CONFIG_SEARCH_DEPTH = 6


@dataclass(frozen=True)
class NamingConfig:
    """Object-naming conventions that import writes and export matches.

    Attributes:
        gun_prefix: Prefix identifying an empty as a gun point.
        attach_prefix: Prefix identifying an empty as an attach point.
        submodel_fallback_prefix: Name stem for a submodel whose SOBJ record
            carries an empty name.
        uv_layer: Name of the UV layer created on import.
    """

    gun_prefix: str = GUN_EMPTY_PREFIX
    attach_prefix: str = ATTACH_EMPTY_PREFIX
    submodel_fallback_prefix: str = SUBMODEL_FALLBACK_PREFIX
    uv_layer: str = UV_LAYER_NAME


@dataclass(frozen=True)
class TextureConfig:
    """How texture names are resolved to image files.

    Attributes:
        extensions: Extensions tried, in priority order, for a bare texture
            name. POF stores no extension, so the order decides which file wins
            when several match.
        search_dirs: Extra directories to search, ahead of the model's own
            folder. Relative entries resolve against the config file, so a
            checked-in project keeps working on someone else's machine.
    """

    extensions: tuple[str, ...] = tuple(IMAGE_EXTENSIONS)
    search_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportConfig:
    """Project-level export targets.

    Attributes:
        version: POF version new files target. A project usually pins this,
            because it is a property of the game build being modded.
    """

    version: int = OBJFILE_VERSION


@dataclass(frozen=True)
class Config:
    """A resolved configuration.

    Attributes:
        naming: Object-naming conventions.
        textures: Texture-resolution settings.
        export: Export targets.
        source_path: File this was loaded from, or ``None`` if nothing was
            found and the defaults are in use.
    """

    naming: NamingConfig = field(default_factory=NamingConfig)
    textures: TextureConfig = field(default_factory=TextureConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    source_path: str | None = None


#: The configuration used when no ``descent3.toml`` is found. Equal to the
#: add-on's behaviour before configuration existed.
DEFAULT_CONFIG = Config()

# Section -> the keys it accepts. Used to reject typos: a misspelled key in a
# hand-edited file would otherwise be silently ignored, and the user would be
# left wondering why their setting had no effect.
_SCHEMA: dict[str, set[str]] = {
    "naming": {
        "gun_prefix", "attach_prefix", "submodel_fallback_prefix", "uv_layer",
    },
    "textures": {"extensions", "search_dirs"},
    "export": {"version"},
}


def find_config_file(start_dir: str, depth: int = CONFIG_SEARCH_DEPTH) -> str | None:
    """Search ``start_dir`` and its parents for :data:`CONFIG_FILENAME`.

    Args:
        start_dir: Directory to start from, normally the model's own folder.
        depth: How many parent directories to try before giving up.

    Returns:
        Path of the nearest config file, or ``None``. Nearest wins, so a
        per-asset file overrides a project-wide one.
    """
    current = os.path.abspath(start_dir)
    for _ in range(depth + 1):
        candidate = os.path.join(current, CONFIG_FILENAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:  # reached the filesystem root
            break
        current = parent
    return None


def _coerce_str_tuple(value, key: str, warnings: list[str]) -> tuple[str, ...] | None:
    """Return ``value`` as a tuple of strings, or None if it is not a list."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        warnings.append(f"{key}: expected a list of strings, ignoring")
        return None
    return tuple(value)


def _parse_naming(table: dict, warnings: list[str]) -> NamingConfig:
    """Build a :class:`NamingConfig` from the ``[naming]`` table."""
    values = {}
    for key in _SCHEMA["naming"]:
        if key not in table:
            continue
        value = table[key]
        if not isinstance(value, str):
            warnings.append(f"naming.{key}: expected a string, ignoring")
            continue
        if not value:
            warnings.append(f"naming.{key}: must not be empty, ignoring")
            continue
        values[key] = value
    return replace(NamingConfig(), **values)


def _parse_textures(table: dict, warnings: list[str]) -> TextureConfig:
    """Build a :class:`TextureConfig` from the ``[textures]`` table."""
    values = {}
    if "extensions" in table:
        exts = _coerce_str_tuple(table["extensions"], "textures.extensions", warnings)
        if exts is not None:
            if not exts:
                warnings.append("textures.extensions: empty, no texture would ever resolve; ignoring")
            else:
                normalized = tuple(
                    e if e.startswith(".") else "." + e for e in (x.lower() for x in exts)
                )
                values["extensions"] = normalized
    if "search_dirs" in table:
        dirs = _coerce_str_tuple(table["search_dirs"], "textures.search_dirs", warnings)
        if dirs is not None:
            values["search_dirs"] = dirs
    return replace(TextureConfig(), **values)


def _parse_export(table: dict, warnings: list[str]) -> ExportConfig:
    """Build an :class:`ExportConfig` from the ``[export]`` table."""
    values = {}
    if "version" in table:
        version = table["version"]
        if not isinstance(version, int) or isinstance(version, bool):
            warnings.append("export.version: expected an integer, ignoring")
        elif not MIN_OBJFILE_VERSION <= version <= OBJFILE_VERSION:
            warnings.append(
                f"export.version: {version} is outside the supported range "
                f"{MIN_OBJFILE_VERSION}..{OBJFILE_VERSION}, ignoring"
            )
        else:
            values["version"] = version
    return replace(ExportConfig(), **values)


def parse_config(data: dict, source_path: str | None = None) -> tuple[Config, list[str]]:
    """Build a :class:`Config` from an already-decoded TOML mapping.

    Unknown sections and keys produce warnings rather than errors, and every
    invalid value falls back to its default. A config file is something a user
    hand-edits, so one bad line should cost that line, not the whole import.

    Args:
        data: Decoded TOML document.
        source_path: Path the document came from, recorded on the result.

    Returns:
        A ``(config, warnings)`` pair.
    """
    warnings: list[str] = []

    for section in data:
        if section not in _SCHEMA:
            warnings.append(f"unknown section [{section}], ignoring")
            continue
        table = data[section]
        if not isinstance(table, dict):
            warnings.append(f"[{section}]: expected a table, ignoring")
            continue
        for key in table:
            if key not in _SCHEMA[section]:
                warnings.append(f"unknown key {section}.{key}, ignoring")

    def table_of(name: str) -> dict:
        value = data.get(name, {})
        return value if isinstance(value, dict) else {}

    config = Config(
        naming=_parse_naming(table_of("naming"), warnings),
        textures=_parse_textures(table_of("textures"), warnings),
        export=_parse_export(table_of("export"), warnings),
        source_path=source_path,
    )
    return config, warnings


def load_config(path: str) -> tuple[Config, list[str]]:
    """Load and validate a config file.

    Args:
        path: Path of the TOML file.

    Returns:
        A ``(config, warnings)`` pair. A file that cannot be read or decoded
        yields :data:`DEFAULT_CONFIG` and a warning naming the problem, so a
        broken config degrades to stock behaviour instead of blocking the user.
    """
    if tomllib is None:  # pragma: no cover - defensive
        return DEFAULT_CONFIG, [
            "tomllib is unavailable in this Python; using default settings"
        ]
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        return DEFAULT_CONFIG, [f"could not read {path}: {e}"]
    except tomllib.TOMLDecodeError as e:
        return DEFAULT_CONFIG, [f"{path} is not valid TOML: {e}"]
    return parse_config(data, source_path=path)


def config_for_path(model_path: str) -> tuple[Config, list[str]]:
    """Resolve the configuration governing a model file.

    Args:
        model_path: Path of the model about to be imported or exported. Its
            directory is where the search for :data:`CONFIG_FILENAME` starts.

    Returns:
        A ``(config, warnings)`` pair. With no config file anywhere above the
        model, this is :data:`DEFAULT_CONFIG` and no warnings.
    """
    start = os.path.dirname(os.path.abspath(model_path))
    found = find_config_file(start)
    if found is None:
        log.info("No %s found above %s; using default settings",
                 CONFIG_FILENAME, start)
        return DEFAULT_CONFIG, []
    config, warnings = load_config(found)
    log.info("Loaded settings from %s", found)
    return config, warnings


def resolved_search_dirs(config: Config) -> list[str]:
    """Return ``config.textures.search_dirs`` as absolute paths.

    Relative entries resolve against the config file's own directory, not the
    current working directory, so a project checked into version control keeps
    working wherever it is cloned.

    Every result is normalized. A TOML file naturally uses forward slashes even
    on Windows, and the caller dedupes search directories by string equality
    against paths from :func:`os.path.abspath`; without this, ``C:/tex`` and
    ``C:\\tex`` would read as two different directories and get searched twice.

    Args:
        config: Configuration to resolve.

    Returns:
        Absolute, normalized directory paths, in the order given. Relative
        entries are dropped when the config did not come from a file, since
        there is nothing to resolve them against.
    """
    base = (
        os.path.dirname(os.path.abspath(config.source_path))
        if config.source_path
        else None
    )
    resolved = []
    for entry in config.textures.search_dirs:
        if os.path.isabs(entry):
            resolved.append(os.path.normpath(entry))
        elif base is not None:
            resolved.append(os.path.normpath(os.path.join(base, entry)))
    return resolved
