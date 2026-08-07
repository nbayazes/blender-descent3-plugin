"""The name rules that survive the trip between Blender and a POF file.

Chiefly the material-name to texture-ID contract described below, but every rule
here answers the same question in a different place: a Blender datablock name is
being asked to carry something the file cares about -- a texture ID, a marker's
index, a filename -- and Blender's own naming rules get in the way. They live
together because they share one fact, :data:`DUPLICATE_SUFFIX_RE`, and a second
copy of that regex is a second thing to get wrong.

The material-name to texture-ID contract.

A Blender material's name *is* the Descent 3 texture ID: import names each
material after the texture it came from, and export maps it straight back into
the TXTR chunk. That equivalence is what makes a round trip work, and it is
fragile in one specific way.

``bpy.data.materials.new()`` always mints a new datablock, and Blender keeps
datablock names unique by appending ``.001``, ``.002`` and so on. So importing a
second asset that shares a texture name produced a material called ``Hull.001``,
and export then wrote ``Hull.001`` into the TXTR chunk as though it were a real
texture. Nothing on disk is called that, so the texture failed to resolve, and
re-importing the file brought the model back untextured.

Import now avoids creating those duplicates at all by reusing an existing
material of the right name, and records the texture ID it used on the material
itself (:data:`~descent3_plugin.constants.PROP_KEY_TEXTURE`). This module is the
second line of defence, for scenes polluted before that fix and for materials a
user duplicates by hand.

Where that recorded ID exists it *outranks* everything below, because the
suffix rule is a guess and the recording is evidence. ``metal`` and
``metal.001`` can perfectly well be two real Descent textures; only the
provenance tells them apart from a hand-cloned material, and merging them loses
a texture outright.

Kept free of ``bpy`` so the rules are unit-testable without Blender: the
provenance arrives as a plain dict the caller builds.
"""

from __future__ import annotations

import ntpath
import os
import re
from collections.abc import Iterable, Mapping

#: Blender's uniquifying suffix: a dot followed by three *or more* digits.
#:
#: Three is not the limit. Measured in Blender 5.2, creating a thousand
#: materials of the same name runs ``.997``, ``.998``, ``.999``, ``.1000``,
#: ``.1001`` -- the counter simply stops being zero-padded. Matching exactly
#: three digits would therefore miss every duplicate past 999.
#:
#: The minimum of three matters just as much: Blender never generates a one- or
#: two-digit suffix, so ``panel.01`` and ``v2.0`` can only be author intent and
#: are left alone.
DUPLICATE_SUFFIX_RE = re.compile(r"\.\d{3,}$")


def is_duplicate_material_name(material_name: str) -> bool:
    """Return whether ``material_name`` carries Blender's ``.NNN`` suffix.

    Args:
        material_name: Name of a Blender material.

    Returns:
        True if the name ends in a dot followed by three or more digits.
    """
    return bool(DUPLICATE_SUFFIX_RE.search(material_name))


def base_material_name(material_name: str) -> str:
    """Return ``material_name`` with any Blender duplicate suffix removed.

    Args:
        material_name: Name of a Blender material.

    Returns:
        The name without its ``.NNN`` suffix. A name that is *only* a suffix
        (``.001``) is returned unchanged rather than reduced to ``""``: an empty
        texture name would be written to the file as a bare NUL, which the
        engine cannot resolve and which gives the user nothing to search for.
    """
    stripped = DUPLICATE_SUFFIX_RE.sub("", material_name)
    return stripped or material_name


def is_uniquified_from(name: str, original: str) -> bool:
    """Return whether ``name`` is ``original`` wearing Blender's ``.NNN`` suffix.

    The suffix rule applies to every datablock, not only materials: two objects
    can no more share a name than two materials can, so a file naming two
    submodels ``Wing`` produces objects ``Wing`` and ``Wing.001``.

    Asking the question this way round -- against a name that is *known* --
    rather than stripping a suffix and hoping, is what keeps it from firing on
    author intent. ``Wing.001`` is only treated as Blender's bookkeeping when
    something already says the original is ``Wing``; when the recorded name is
    itself ``Wing.001``, the name matches outright and nothing is stripped.

    Args:
        name: Current name of the datablock.
        original: Name it is being compared against, from somewhere that knows --
            a recorded provenance property, or another datablock in the scene.

    Returns:
        True when the two are the same name, or when ``name`` is ``original``
        plus a suffix Blender could have appended and an author would not have
        typed. False for any other pair, including an unrelated rename.
    """
    if name == original:
        return True
    return (
        is_duplicate_material_name(name)
        and base_material_name(name) == original
    )


def marker_order_key(name: str, prefix: str) -> tuple[int, int, str]:
    """Return a sort key restoring the file order of gun and attach markers.

    Import names markers after their position in the file -- ``Gun_0`` up to
    ``Gun_11`` -- and export finds them again by walking ``bpy.data.objects``,
    which Blender keeps sorted *as text*. Past nine markers those two orders stop
    agreeing: ``Gun_10`` sorts between ``Gun_1`` and ``Gun_2``, and the export
    silently permutes the banks.

    That is not cosmetic. A gun bank's index is what the WBAT chunk cites when it
    says which weapon fires from where, and Descent 3 bolts specific hardware to
    specific attach-point indices, so a permuted list swaps a ship's weapons
    around. Under ten markers the text order happens to match the numeric one,
    which is why this went unnoticed.

    Args:
        name: Object name of the marker empty.
        prefix: Marker prefix from the project's naming configuration, e.g.
            ``"Gun_"``.

    Returns:
        A tuple ordering numbered markers first and numerically, then everything
        else by name. Anything the user renamed, duplicated (``Gun_3.001``) or
        added by hand carries no index to trust, so it sorts after the numbered
        ones in a stable, predictable place rather than being wedged into the
        middle of them.
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

    A material with recorded provenance -- one this add-on imported, carrying
    :data:`~descent3_plugin.constants.PROP_KEY_TEXTURE` -- exports as the ID it
    records, full stop. It is never collapsed onto anything and never warned
    about, because there is nothing left to infer: the file it came from said
    what that texture is called. This is what lets a model reference both
    ``metal`` and ``metal.001`` and get both back.

    Everything else is a material nobody recorded anything about, so the suffix
    heuristic applies. A duplicate-suffixed name is only collapsed onto its base
    when that base **is itself a material in the scene**. That condition is what
    makes the rule safe: it means the collapse is provably reuniting a duplicate
    with the original it was cloned from, rather than inventing a texture name.

    Without the condition, ``Hull.001`` and ``Hull.002`` in a scene whose
    ``Hull`` was deleted would both collapse onto ``Hull`` -- a texture the
    scene never contained -- merging two visually distinct materials into one
    and pointing every face at it. Exporting those two verbatim is wrong too,
    but it is *visibly* wrong and the warning says so.

    A collapse lands on whatever the *original* exports as, which is its
    recorded ID when it has one. A hand-duplicated clone of an imported material
    has to follow it: the two share one texture by construction, and sending the
    clone to the material's name instead would write a second TXTR entry naming
    a bitmap that does not exist.

    Args:
        material_names: Names of every material used by the exported objects.
        explicit: Material name to the texture ID recorded on that material, for
            those that carry one. A plain mapping rather than the materials
            themselves so this module stays free of ``bpy``. ``None`` -- the
            whole scene lacking provenance -- is exactly the pre-provenance
            behaviour, which is what keeps a .blend built by an older version of
            the add-on exporting as it did before.

    Returns:
        A ``(mapping, warnings)`` pair. ``mapping`` covers every input name, so
        callers can resolve without re-deriving the rule -- and both the texture
        list and the per-face lookup must use this same mapping or they disagree
        about which texture a face uses.
    """
    names = list(material_names)
    known = set(names)
    recorded = dict(explicit or {})
    mapping: dict[str, str] = {}
    merged: dict[str, list[str]] = {}
    orphans: list[tuple[str, str]] = []

    for name in names:
        if name in recorded:
            # Evidence beats inference: this material knows which texture it
            # stands for, so no amount of ``.NNN`` in its name gets to reassign
            # it to a neighbour that happens to share a stem.
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
            # Looks like a duplicate, but nothing here is the original. Export
            # it verbatim: a name the engine cannot resolve is recoverable, an
            # invented one that silently steals another texture's faces is not.
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


# ---------------------------------------------------------------------------
# Texture export formats
# ---------------------------------------------------------------------------

#: Value meaning "do not write any texture images".
TEXTURE_FORMAT_NONE = "NONE"

#: Supported export formats: identifier -> (Blender ``file_format``, extension).
#:
#: The Blender identifier is what ``scene.render.image_settings.file_format``
#: takes. It is *not* interchangeable with the extension -- ``TARGA`` writes
#: ``.tga`` -- which is exactly why the pairing lives in one table rather than
#: being derived at each call site.
#:
#: Descent 3's native OGF belongs here eventually. It is deliberately absent
#: rather than present-and-broken: Blender cannot write OGF, so it needs its own
#: encoder, and adding the entry once that exists is the only change required.
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
    because that is what import searches for. Renaming here would break the
    round trip just as surely as a ``.001`` suffix does.

    Args:
        texture_name: Texture ID from the model.
        fmt: Key of :data:`TEXTURE_FORMATS`.

    Returns:
        ``<texture name><extension>``. Any extension already on the texture
        name is left alone rather than stripped -- Descent texture IDs do not
        normally carry one, and a name like ``panel.2`` is not an extension.
        This does not validate: a texture ID is only a filename if
        :func:`rejected_texture_name` says so, and the caller runs that first.
    """
    return f"{texture_name}{texture_format_extension(fmt)}"


# ---------------------------------------------------------------------------
# Texture IDs used as filenames
# ---------------------------------------------------------------------------

#: Names Windows refuses to give a file, whatever extension follows them.
#:
#: These are devices, not files. ``open("NUL.png", "wb")`` succeeds, swallows
#: every byte written to it and leaves nothing on disk, so an export naming a
#: texture ``NUL`` would report success and produce no image -- the one failure
#: mode worth more effort than the rest of this table put together, because
#: nothing else about it looks wrong.
#:
#: The device is matched on the part before the first dot, which is how Windows
#: matches it: ``AUX.tga`` is as reserved as ``AUX``. ``COM10`` and ``CONSOLE``
#: are not reserved and must not be refused -- only the exact set below is.
RESERVED_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def rejected_texture_name(texture_name: str) -> str | None:
    """Return why ``texture_name`` is unusable as a filename, or None.

    The texture ID becomes the stem of a file this add-on creates, and it comes
    from places nobody checked: a TXTR chunk in a model of unknown provenance,
    or a material name a user typed. ``os.path.join`` is unforgiving about that
    -- handed an absolute second argument it *discards* the directory entirely,
    so a texture called ``C:\\Windows\\Hull`` is written there instead of beside
    the model, and ``../../Hull`` climbs out of the export folder just as
    effectively. Neither raises; both silently write somewhere the user is not
    looking, and export overwrites without asking.

    Refusing the name costs one texture and a message saying which. Writing it
    costs a file somewhere else on the disk, so the balance is not close, and
    this is deliberately as strict as :func:`~descent3_plugin.config._rejected_export_dir`,
    which guards the directory half of the very same join.

    Args:
        texture_name: Texture ID as it will be written into the TXTR chunk,
            exactly as it will be used -- not stripped, since the stripping
            would itself change which file import later looks for.

    Returns:
        A message describing the problem, or ``None`` when the name is a plain
        filename stem.
    """
    if not texture_name.strip():
        return "the texture name is empty, so there is no filename to write it as"
    # ``ntpath`` rather than ``os.path``: a drive letter is not a filename on any
    # platform, and the same .blend gets exported from Linux and from Windows.
    # Leaving ``C:tex`` to pass on one of them means the model exports fine for
    # one artist on a team and lands on another drive for the next.
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
