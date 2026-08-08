"""Per-project configuration, read from a ``descent3.toml`` beside the models.

The settings here are *conventions*, not preferences: importer and exporter must
agree on them or a model stops surviving a round trip -- import naming a gun
empty ``Gun_0`` while export looks for ``gun.0`` drops the point silently. Both
halves read the same file, and the defaults below are the values the add-on used
before the file existed, so a project with no ``descent3.toml`` behaves as
always.

Not the home for *user* preferences (texture library location, viewport marker
size); those belong in the add-on preferences. Format constants -- version
gates, chunk IDs, flag bits -- must not be here either: they are facts about a
binary format, and a project that "configured" them would produce corrupt
models.

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
from .naming import TEXTURE_FORMAT_NONE, TEXTURE_FORMATS
from .poformat import MIN_OBJFILE_VERSION, OBJFILE_VERSION
from .texutil import IMAGE_EXTENSIONS

log = logging.getLogger(__package__)

# Belt and braces: tomllib is stdlib from Python 3.11, which covers every
# Blender this add-on supports. Nothing is logged here because register() has
# not configured logging yet -- load_config() turns the None into a warning.
try:
    import tomllib
except ImportError:  # pragma: no cover - not reachable on Blender 4.2+
    tomllib = None

#: Searched for by walking up from the model being imported or exported. Named
#: after the game rather than the add-on so a project can be shared between
#: tools.
CONFIG_FILENAME = "descent3.toml"

#: How many parent directories to search above the starting one. Deep enough to
#: find a config at a project root from ``<project>/models/ship/`` without
#: walking to the filesystem root and picking up an unrelated file.
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
        export_dir: Subfolder beside the exported model that written texture
            images go into, relative to the ``.pof``. The default says
            "exported" so generated images are not mistaken, by user or tool,
            for hand-authored source art. Export-only: import does
            not search it, so exports stay sandboxed from the models they came
            from unless the folder is deliberately listed in ``search_dirs``.
    """

    extensions: tuple[str, ...] = tuple(IMAGE_EXTENSIONS)
    search_dirs: tuple[str, ...] = ()
    export_dir: str = "exported_textures"


@dataclass(frozen=True)
class ExportConfig:
    """Project-level export targets.

    Attributes:
        version: POF version new files target. Usually pinned per project, being
            a property of the game build being modded.
        texture_format: Format written for texture images, or
            :data:`~descent3_plugin.naming.TEXTURE_FORMAT_NONE` to write none.
            Defaults to none: exporting should not drop image files beside a
            model unless that was asked for.
    """

    version: int = OBJFILE_VERSION
    texture_format: str = TEXTURE_FORMAT_NONE


@dataclass(frozen=True)
class Config:
    """A resolved configuration.

    Attributes:
        source_path: File this was loaded from, or ``None`` if nothing was
            found and the defaults are in use.
    """

    naming: NamingConfig = field(default_factory=NamingConfig)
    textures: TextureConfig = field(default_factory=TextureConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    source_path: str | None = None


#: Used when no ``descent3.toml`` is found. Equal to the add-on's behaviour
#: before configuration existed.
DEFAULT_CONFIG = Config()

# Section -> the keys it accepts. Used to reject typos, which would otherwise be
# silently ignored and leave the user wondering why a setting had no effect.
_SCHEMA: dict[str, set[str]] = {
    "naming": {
        "gun_prefix", "attach_prefix", "submodel_fallback_prefix", "uv_layer",
    },
    "textures": {"extensions", "search_dirs", "export_dir"},
    "export": {"version", "texture_format"},
}


def find_config_file(start_dir: str, depth: int = CONFIG_SEARCH_DEPTH) -> str | None:
    """Search ``start_dir`` and its parents for :data:`CONFIG_FILENAME`.

    Args:
        start_dir: Directory to start from, normally the model's own folder.
        depth: How many *parent* directories to search above ``start_dir``,
            which is itself always searched.

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


def _rejected_export_dir(value: str) -> str | None:
    """Return why ``value`` is unusable as an export subfolder, or None.

    Stricter than :func:`os.path.isabs` on purpose: Python 3.13 changed
    ``ntpath.isabs`` so a single leading slash is no longer absolute on Windows,
    letting ``"/textures"`` pass and then resolve to the current drive's root.
    The setting names a folder *beside the exported model*, so anything that
    could land elsewhere is refused.

    Args:
        value: The configured directory, already stripped.

    Returns:
        A message describing the problem, or ``None`` when the value is a plain
        relative subfolder.
    """
    if os.path.isabs(value) or os.path.splitdrive(value)[0]:
        return f"{value!r} is an absolute path, but it must be relative to the model"
    if value[0] in "/\\":
        return f"{value!r} starts with a path separator, which resolves to the drive root"
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        return f"{value!r} escapes the model's folder with '..'"
    return None


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
    if "export_dir" in table:
        export_dir = table["export_dir"]
        if not isinstance(export_dir, str) or not export_dir.strip():
            warnings.append(
                "textures.export_dir: expected a non-empty string, ignoring"
            )
        else:
            problem = _rejected_export_dir(export_dir.strip())
            if problem:
                warnings.append(f"textures.export_dir: {problem}; ignoring")
            else:
                values["export_dir"] = export_dir.strip()
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
    if "texture_format" in table:
        fmt = table["texture_format"]
        if not isinstance(fmt, str):
            warnings.append("export.texture_format: expected a string, ignoring")
        else:
            upper = fmt.strip().upper()
            if upper in TEXTURE_FORMATS or upper == TEXTURE_FORMAT_NONE:
                values["texture_format"] = upper
            else:
                allowed = ", ".join(sorted(TEXTURE_FORMATS) + [TEXTURE_FORMAT_NONE])
                warnings.append(
                    f"export.texture_format: {fmt!r} is not supported "
                    f"(expected one of {allowed}); ignoring"
                )
    return replace(ExportConfig(), **values)


def parse_config(data: dict, source_path: str | None = None) -> tuple[Config, list[str]]:
    """Build a :class:`Config` from an already-decoded TOML mapping.

    Unknown sections and keys warn rather than raise, and every invalid value
    falls back to its default: a config file is hand-edited, so one bad line
    should cost that line, not the whole import.

    Args:
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
    current working directory, so a project in version control keeps working
    wherever it is cloned. Results are normalized because a TOML file uses
    forward slashes even on Windows while the caller dedupes by string equality
    against :func:`os.path.abspath` output, so ``C:/tex`` and ``C:\\tex`` would
    otherwise be searched twice.

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
