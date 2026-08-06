"""The material-name to texture-ID contract.

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
material of the right name. This module is the second line of defence, for
scenes polluted before that fix and for materials a user duplicates by hand.

Kept free of ``bpy`` so the rules are unit-testable without Blender.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

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


def resolve_texture_names(
    material_names: Iterable[str],
) -> tuple[dict[str, str], list[str]]:
    """Map every material name to the texture ID it should export as.

    A duplicate-suffixed name is only collapsed onto its base when that base
    **is itself a material in the scene**. That condition is what makes the rule
    safe: it means the collapse is provably reuniting a duplicate with the
    original it was cloned from, rather than inventing a texture name.

    Without the condition, ``Hull.001`` and ``Hull.002`` in a scene whose
    ``Hull`` was deleted would both collapse onto ``Hull`` -- a texture the
    scene never contained -- merging two visually distinct materials into one
    and pointing every face at it. Exporting those two verbatim is wrong too,
    but it is *visibly* wrong and the warning says so.

    Args:
        material_names: Names of every material used by the exported objects.

    Returns:
        A ``(mapping, warnings)`` pair. ``mapping`` covers every input name, so
        callers can resolve without re-deriving the rule -- and both the texture
        list and the per-face lookup must use this same mapping or they disagree
        about which texture a face uses.
    """
    names = list(material_names)
    known = set(names)
    mapping: dict[str, str] = {}
    merged: dict[str, list[str]] = {}
    orphans: list[tuple[str, str]] = []

    for name in names:
        if not is_duplicate_material_name(name):
            mapping[name] = name
            continue
        base = base_material_name(name)
        if base != name and base in known:
            mapping[name] = base
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
            f"of material '{base}' and will export as texture '{base}'. "
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
    """
    return f"{texture_name}{texture_format_extension(fmt)}"
