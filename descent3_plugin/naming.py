"""The name rules that survive the trip between Blender and a POF file.

Each rule here is a Blender datablock name being asked to carry something the
file cares about -- a texture ID, a marker's index, a filename -- against
Blender's own naming rules. They share one fact, :data:`DUPLICATE_SUFFIX_RE`.

A Blender material's name *is* the Descent 3 texture ID: import names each
material after the texture it came from, export maps it back into the TXTR
chunk. ``bpy.data.materials.new()`` always mints a new datablock and Blender
uniquifies names with ``.001``, so a second asset sharing a texture name
produced ``Hull.001``, which export wrote into TXTR as a texture nothing on disk
is called. Import now reuses an existing material of the right name and records
the ID it used (:data:`~descent3_plugin.constants.PROP_KEY_TEXTURE`); this
module is the second line of defence, for scenes polluted before that fix and
for materials a user duplicates by hand.

A recorded ID *outranks* the suffix rule, because the suffix is a guess and the
recording is evidence: ``metal`` and ``metal.001`` can be two real Descent
textures, and merging them loses one outright.

Kept free of ``bpy`` so the rules are unit-testable without Blender: the
provenance arrives as a plain dict the caller builds.
"""

from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Iterable, Mapping

#: Blender's uniquifying suffix: a dot followed by three *or more* digits.
#: Measured in Blender 5.2, the counter stops being zero-padded past 999
#: (``.999``, ``.1000``), so matching exactly three would miss those. Three is
#: the minimum because Blender never generates a shorter suffix, so ``panel.01``
#: and ``v2.0`` can only be author intent and are left alone.
DUPLICATE_SUFFIX_RE = re.compile(r"\.\d{3,}$")


def is_duplicate_material_name(material_name: str) -> bool:
    """Return whether ``material_name`` carries Blender's ``.NNN`` suffix.

    Returns:
        True if the name ends in a dot followed by three or more digits.
    """
    return bool(DUPLICATE_SUFFIX_RE.search(material_name))


def base_material_name(material_name: str) -> str:
    """Return ``material_name`` with any Blender duplicate suffix removed.

    Returns:
        The name without its ``.NNN`` suffix. A name that is *only* a suffix
        (``.001``) is returned unchanged: an empty texture name would be written
        to the file as a bare NUL, which the engine cannot resolve.
    """
    stripped = DUPLICATE_SUFFIX_RE.sub("", material_name)
    return stripped or material_name


def is_uniquified_from(name: str, original: str) -> bool:
    """Return whether ``name`` is ``original`` wearing Blender's ``.NNN`` suffix.

    The suffix rule applies to every datablock, not only materials: a file naming
    two submodels ``Wing`` produces objects ``Wing`` and ``Wing.001``. Asking
    against a name that is *known*, rather than stripping a suffix and hoping, is
    what keeps this from firing on author intent.

    Args:
        original: Name it is being compared against, from somewhere that knows --
            a recorded provenance property, or another datablock in the scene.

    Returns:
        True when the two are the same name, or when ``name`` is ``original``
        plus a suffix Blender could have appended. False for any other pair,
        including an unrelated rename.
    """
    if name == original:
        return True
    return (
        is_duplicate_material_name(name)
        and base_material_name(name) == original
    )


def marker_order_key(name: str, prefix: str) -> tuple[int, int, str]:
    """Return a sort key restoring the file order of gun and attach markers.

    Import names markers after their position in the file (``Gun_0``..``Gun_11``)
    and export finds them by walking ``bpy.data.objects``, which Blender keeps
    sorted *as text*: past nine markers ``Gun_10`` sorts between ``Gun_1`` and
    ``Gun_2``. Not cosmetic -- the WBAT chunk cites a gun bank by index and
    Descent 3 bolts specific hardware to specific attach-point indices, so a
    permuted list swaps a ship's weapons around.

    Args:
        prefix: Marker prefix from the project's naming configuration, e.g.
            ``"Gun_"``.

    Returns:
        A tuple ordering numbered markers first and numerically, then everything
        else by name. Anything renamed, duplicated (``Gun_3.001``) or added by
        hand carries no index to trust, so it sorts after the numbered ones.
    """
    suffix = name[len(prefix):] if name.startswith(prefix) else name
    # isdecimal, not isdigit: both accept non-ASCII digits, but only the decimal
    # ones convert -- ``"²".isdigit()`` is True and ``int("²")`` raises.
    if suffix.isdecimal():
        return (0, int(suffix), name)
    return (1, 0, name)


def resolve_texture_names(
    material_names: Iterable[str],
    explicit: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Map every material name to the texture ID it should export as.

    A material carrying :data:`~descent3_plugin.constants.PROP_KEY_TEXTURE`
    exports as the ID it records, never collapsed and never warned about; that
    is what lets a model reference both ``metal`` and ``metal.001``.

    Everything else falls to the suffix heuristic, and a duplicate-suffixed name
    is only collapsed onto its base when that base **is itself a material in the
    scene**. Without that condition, ``Hull.001`` and ``Hull.002`` in a scene
    whose ``Hull`` was deleted would both collapse onto a texture the scene never
    contained, merging two distinct materials; exporting them verbatim is also
    wrong but *visibly* so, and the warning says as much.

    A collapse lands on whatever the *original* exports as, its recorded ID
    included: a hand-duplicated clone shares one texture with its source by
    construction, so sending it to the material's name would write a second TXTR
    entry naming a bitmap that does not exist.

    Args:
        material_names: Names of every material used by the exported objects.
        explicit: Material name to the texture ID recorded on that material, for
            those that carry one. A plain mapping rather than the materials
            themselves so this module stays free of ``bpy``. ``None`` gives the
            pre-provenance behaviour, keeping a .blend built by an older version
            of the add-on exporting as it did before.

    Returns:
        A ``(mapping, warnings)`` pair. ``mapping`` covers every input name; both
        the texture list and the per-face lookup must use this same mapping or
        they disagree about which texture a face uses.
    """
    names = list(material_names)
    known = set(names)
    recorded = dict(explicit or {})
    mapping: dict[str, str] = {}
    merged: dict[str, list[str]] = {}
    orphans: list[tuple[str, str]] = []

    for name in names:
        if name in recorded:
            # Evidence beats inference: ``.NNN`` in the name never gets to
            # reassign a recorded material to a neighbour sharing its stem.
            mapping[name] = recorded[name]
            continue
        if not is_duplicate_material_name(name):
            mapping[name] = name
            continue
        base = base_material_name(name)
        if base != name and base in known:
            mapping[name] = recorded.get(base, base)
            merged.setdefault(base, []).append(name)
        else:
            # Looks like a duplicate but no original is present. Verbatim: a
            # name the engine cannot resolve is recoverable, an invented one
            # that silently steals another texture's faces is not.
            mapping[name] = name
            orphans.append((name, base))

    warnings = []
    for base, dupes in sorted(merged.items()):
        warnings.append(
            f"{', '.join(sorted(dupes))} "
            f"{'look' if len(dupes) > 1 else 'looks'} like a Blender duplicate "
            f"of material '{base}' and will export as texture "
            f"'{recorded.get(base, base)}'. "
            f"Rename or remove the duplicate to silence this."
        )
    for name, base in sorted(orphans):
        warnings.append(
            f"Material '{name}' looks like a Blender duplicate, but no material "
            f"'{base}' exists to merge it with, so it is exported verbatim. "
            f"Descent 3 will not find a texture called '{name}'."
        )
    return mapping, warnings


TEXTURE_FORMAT_NONE = "NONE"

#: Supported export formats: identifier -> (Blender ``file_format``, extension).
#: The Blender identifier is what ``scene.render.image_settings.file_format``
#: takes, and it is *not* interchangeable with the extension -- ``TARGA`` writes
#: ``.tga`` -- which is why the pairing lives in one table. Descent 3's native
#: OGF is absent because Blender cannot write it; it needs its own encoder first.
TEXTURE_FORMATS = {
    "PNG": ("PNG", ".png"),
    "TARGA": ("TARGA", ".tga"),
}


def texture_format_extension(fmt: str) -> str:
    """Return the file extension for an export format identifier.

    Args:
        fmt: Key of :data:`TEXTURE_FORMATS`.

    Returns:
        The extension, including the leading dot.

    Raises:
        KeyError: If the format is not supported. Callers validate before
            reaching here; this is a programming error, not user input.
    """
    return TEXTURE_FORMATS[fmt][1]


def blender_file_format(fmt: str) -> str:
    """Return the Blender ``file_format`` identifier for an export format.

    Args:
        fmt: Key of :data:`TEXTURE_FORMATS`.

    Returns:
        The value to assign to ``scene.render.image_settings.file_format``.

    Raises:
        KeyError: If the format is not supported.
    """
    return TEXTURE_FORMATS[fmt][0]


def texture_filename(texture_name: str, fmt: str) -> str:
    """Return the filename a texture should be written as.

    The stem is the texture ID exactly as it appears in the model's TXTR chunk,
    because that is what import searches for; renaming here breaks the round
    trip just as a ``.001`` suffix does.

    Args:
        texture_name: Texture ID from the model.
        fmt: Key of :data:`TEXTURE_FORMATS`.

    Returns:
        ``<texture name><extension>``. Any extension already on the texture name
        is left alone -- a name like ``panel.2`` is not an extension. Does not
        validate; the caller runs :func:`rejected_texture_name` first.
    """
    return f"{texture_name}{texture_format_extension(fmt)}"


#: Names Windows refuses to give a file, whatever extension follows them.
#: ``open("NUL.png", "wb")`` succeeds, swallows every byte and leaves nothing on
#: disk, so an export naming a texture ``NUL`` reports success and produces no
#: image. Matched on the part before the first dot, as Windows matches it:
#: ``AUX.tga`` is as reserved as ``AUX``. ``COM10`` and ``CONSOLE`` are *not*
#: reserved and must not be refused -- only the exact set below is.
RESERVED_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def rejected_texture_name(texture_name: str) -> str | None:
    """Return why ``texture_name`` is unusable as a filename, or None.

    The texture ID becomes the stem of a file this add-on creates, and it comes
    from unchecked places: a TXTR chunk of unknown provenance, or a material name
    a user typed. Handed an absolute second argument ``os.path.join``
    *discards* the directory, so ``C:\\Windows\\Hull`` is written there rather
    than beside the model, and ``../../Hull`` climbs out just as effectively;
    neither raises, and export overwrites without asking. Deliberately as strict
    as :func:`~descent3_plugin.config._rejected_export_dir`, which guards the
    directory half of the same join.

    Args:
        texture_name: Texture ID as it will be written into the TXTR chunk,
            exactly as it will be used -- not stripped, since stripping would
            change which file import later looks for.

    Returns:
        A message describing the problem, or ``None`` when the name is a plain
        filename stem.
    """
    if not texture_name.strip():
        return "the texture name is empty, so there is no filename to write it as"
    # ``ntpath`` rather than ``os.path``: a drive letter is not a filename on any
    # platform, and the same .blend gets exported from Linux and from Windows,
    # so letting ``C:tex`` pass on one of them lands the file on another drive.
    if os.path.isabs(texture_name) or ntpath.splitdrive(texture_name)[0]:
        return f"{texture_name!r} is an absolute path, but a texture name must be a plain filename"
    if texture_name[0] in "/\\":
        return f"{texture_name!r} starts with a path separator, which resolves to the drive root"
    parts = texture_name.replace("\\", "/").split("/")
    if ".." in parts:
        return f"{texture_name!r} escapes the export folder with '..'"
    if len(parts) > 1:
        return f"{texture_name!r} contains a path separator, but it names one file, not a path"
    if texture_name.split(".", 1)[0].upper() in RESERVED_DEVICE_NAMES:
        return f"{texture_name!r} is a reserved Windows device name and cannot be a file"
    if texture_name[-1] in ". ":
        # Windows strips these when creating the file, so the bitmap would land
        # under a name the TXTR chunk does not spell and import would not find.
        return f"{texture_name!r} ends in a dot or a space, which Windows strips from filenames"
    return None
