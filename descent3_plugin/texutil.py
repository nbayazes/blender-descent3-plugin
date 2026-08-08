"""Texture-resolution helpers for the Descent 3 importer.

Deliberately free of any ``bpy`` dependency so they can be unit-tested without
Blender.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

log = logging.getLogger(__package__)

#: Image formats tried, in priority order, when a texture name has no
#: extension. POF stores a bare name, so the extension has to be guessed;
#: lossless formats come first so a ``.png`` beats a ``.jpg`` of the same name.
IMAGE_EXTENSIONS = [".png", ".bmp", ".tga", ".jpg", ".jpeg", ".ogf", ".pcx"]


def clean_texture_dir(raw: str) -> str:
    """Normalize a user-entered texture directory string.

    The import dialog can only offer a plain text field, so users paste paths,
    and Windows "Copy as path" wraps them in double quotes.

    Returns:
        The path with surrounding whitespace and quotes removed, or ``""`` for
        an empty or blank value.
    """
    if not raw:
        return ""
    return raw.strip().strip('"').strip("'").strip()


def find_texture_image(
    texture_name: str,
    search_dirs: Sequence[str],
    extensions: Sequence[str] = tuple(IMAGE_EXTENSIONS),
) -> str | None:
    """Return the path of an image file matching ``texture_name``, or None.

    Each directory is checked in order, exact ``name + extension`` first and
    then case-insensitively against the directory listing, since Descent 3
    texture names do not always match the bitmap file's case. First match wins.

    Args:
        texture_name: Texture name as stored in the model's TXTR chunk,
            normally without an extension.
        search_dirs: Directories to search, in priority order. Entries that are
            empty or are not directories are skipped. Must be re-iterable: it is
            walked once to search and again to report a failure.
        extensions: Extensions to try, in priority order, lower-cased and
            dot-prefixed. ``IMAGE_EXTENSIONS`` is only the default; a project
            can override both the list and its order through
            ``config.textures.extensions``.

    Returns:
        The full path of the first matching image, or ``None`` if no directory
        holds one -- a missing texture is reported rather than raised, so one
        absent bitmap does not abort a whole import.
    """
    name_lower = texture_name.lower()
    for directory in search_dirs:
        if not directory or not os.path.isdir(directory):
            continue
        for ext in extensions:
            path = os.path.join(directory, texture_name + ext)
            if os.path.isfile(path):
                log.info("Found texture: %s", path)
                return path
        try:
            for fn in os.listdir(directory):
                stem, ext = os.path.splitext(fn)
                if ext.lower() in extensions and stem.lower() == name_lower:
                    path = os.path.join(directory, fn)
                    log.info("Found texture (case-insensitive): %s", path)
                    return path
        except OSError as e:
            log.warning("Could not list texture directory %s: %s", directory, e)
    log.warning("Texture not found: %s (searched %s)", texture_name, search_dirs)
    return None
