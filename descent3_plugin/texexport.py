"""Writing texture images out beside an exported model.

Export writes texture *names* into the model's TXTR chunk; this writes the
pixels those names refer to, so a model and the images it needs can be handed
over together.

Two things about Blender's image API make this less obvious than it looks, and
both fail silently rather than raising:

1. ``Image.save(filepath=...)`` **ignores** ``file_format``. Setting a copy's
   format to ``TARGA`` and saving to ``Hull.tga`` writes PNG bytes into a file
   named ``.tga`` -- verified by inspecting the magic number. Descent 3 would
   reject it, and nothing in Blender would have complained.
2. ``Image.save_render()`` *does* convert, but it renders through the scene's
   colour management. Blender 5.2 ships with the view transform defaulting to
   **AgX**, so textures would come out tone-mapped and washed out. Only
   ``Standard`` reproduces the original pixels byte-for-byte.

So conversion goes through ``save_render`` with colour management neutralised
and restored afterwards, and the common case -- a source file already in the
requested format -- skips Blender entirely and copies the bytes.
"""

import logging
import os
import shutil

import bpy

from .naming import blender_file_format, texture_filename

log = logging.getLogger(__package__)

#: View transform that reproduces an image's stored pixels. ``Standard`` was
#: measured to byte-match a plain ``Image.save()``; ``Raw``, ``AgX`` and
#: ``Filmic`` each produce different output.
NEUTRAL_VIEW_TRANSFORM = "Standard"

#: Look, exposure and gamma are reset alongside the view transform. A scene
#: carrying an exposure offset would otherwise bake it into every texture.
NEUTRAL_LOOK = "None"


class _NeutralColourManagement:
    """Temporarily disable colour management for the duration of a save.

    ``save_render`` has no way to bypass the scene's display transform, so the
    scene is adjusted and restored. Every field is put back in a ``finally``, so
    an error mid-export cannot leave the user's scene tone-mapped differently
    than they left it.
    """

    def __init__(self, scene: bpy.types.Scene) -> None:
        self.scene = scene
        self._saved: dict[str, object] = {}

    def __enter__(self) -> bpy.types.Scene:
        view = self.scene.view_settings
        self._saved = {
            "view_transform": view.view_transform,
            "look": view.look,
            "exposure": view.exposure,
            "gamma": view.gamma,
        }
        # Some builds ship a reduced set of transforms; fall back rather than
        # raising, and say so, because the alternative is silent tone-mapping.
        try:
            view.view_transform = NEUTRAL_VIEW_TRANSFORM
        except TypeError:
            log.warning(
                "This Blender has no '%s' view transform; exported textures may "
                "be colour-managed. Available: %s",
                NEUTRAL_VIEW_TRANSFORM,
                [i.identifier for i in
                 type(view).bl_rna.properties["view_transform"].enum_items],
            )
        try:
            view.look = NEUTRAL_LOOK
        except TypeError:
            pass
        view.exposure = 0.0
        view.gamma = 1.0
        return self.scene

    def __exit__(self, exc_type, exc, tb) -> None:
        view = self.scene.view_settings
        for key, value in self._saved.items():
            try:
                setattr(view, key, value)
            except (TypeError, AttributeError):
                pass


def _image_for_material(mat: bpy.types.Material) -> bpy.types.Image | None:
    """Return the image driving a material's colour, if it has one.

    Args:
        mat: Material to inspect.

    Returns:
        The first image-texture node's image, or ``None``. Preference goes to a
        node actually linked into the shader, so a stray unconnected texture
        node left over from experimenting does not win over the real one.
    """
    if not mat or not mat.use_nodes or mat.node_tree is None:
        return None

    unlinked = None
    for node in mat.node_tree.nodes:
        if node.type != "TEX_IMAGE" or node.image is None:
            continue
        if any(output.is_linked for output in node.outputs):
            return node.image
        if unlinked is None:
            unlinked = node.image
    return unlinked


def _source_path(image: bpy.types.Image) -> str | None:
    """Return the image's source file on disk, if it has a usable one.

    Args:
        image: Image to resolve.

    Returns:
        An absolute path, or ``None`` when the image is packed, generated, or
        its file has gone missing -- all of which have to be re-encoded instead
        of copied.
    """
    if image.packed_file is not None:
        return None
    if image.source not in {"FILE", "SEQUENCE"}:
        return None
    raw = image.filepath_from_user() if hasattr(image, "filepath_from_user") else image.filepath
    if not raw:
        return None
    path = os.path.abspath(bpy.path.abspath(raw))
    return path if os.path.isfile(path) else None


def export_textures(
    materials: dict[str, bpy.types.Material],
    directory: str,
    fmt: str,
    scene: bpy.types.Scene,
) -> tuple[list[str], list[str]]:
    """Write each texture's image into ``directory``.

    Args:
        materials: Texture ID to the material carrying its image. The key is the
            name written to the TXTR chunk, so the file is named after it and
            import finds it again.
        directory: Folder to write into. Created if missing.
        fmt: Key of :data:`~descent3_plugin.naming.TEXTURE_FORMATS`.
        scene: Scene whose colour management is borrowed for conversion.

    Returns:
        A ``(written, problems)`` pair. ``written`` holds the filenames created;
        ``problems`` holds one message per texture that could not be written, so
        the caller can report them rather than leaving the user to discover a
        half-populated folder.
    """
    written: list[str] = []
    problems: list[str] = []

    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as e:
        return [], [f"could not create {directory}: {e}"]

    for texture_name in sorted(materials):
        mat = materials[texture_name]
        image = _image_for_material(mat)
        if image is None:
            problems.append(
                f"{texture_name}: material has no image texture, nothing to write"
            )
            continue

        filename = texture_filename(texture_name, fmt)
        destination = os.path.join(directory, filename)
        try:
            if _copy_or_convert(image, destination, fmt, scene):
                written.append(filename)
        except Exception as e:
            problems.append(f"{texture_name}: {e}")

    return written, problems


def _copy_or_convert(
    image: bpy.types.Image,
    destination: str,
    fmt: str,
    scene: bpy.types.Scene,
) -> bool:
    """Write ``image`` to ``destination`` in ``fmt``.

    A source file already in the requested format is copied byte-for-byte: no
    re-encode, no colour management, no chance of the two traps this module
    exists to avoid. Anything else is converted.

    Args:
        image: Image to write.
        destination: Full path of the file to create.
        fmt: Key of :data:`~descent3_plugin.naming.TEXTURE_FORMATS`.
        scene: Scene whose colour management is borrowed.

    Returns:
        True when a file was written.

    Raises:
        RuntimeError: If the image carries no pixel data -- a source file that
            has been moved or deleted since it was loaded.
    """
    source = _source_path(image)
    if source and image.file_format == blender_file_format(fmt):
        if os.path.abspath(source) == os.path.abspath(destination):
            log.info("Texture '%s' is already at the destination", image.name)
            return False
        shutil.copyfile(source, destination)
        log.info("Copied %s -> %s", os.path.basename(source), destination)
        return True

    if not image.has_data:
        raise RuntimeError(
            "image has no pixel data (its file may have been moved or deleted)"
        )

    with _NeutralColourManagement(scene) as neutral:
        previous = neutral.render.image_settings.file_format
        neutral.render.image_settings.file_format = blender_file_format(fmt)
        try:
            image.save_render(filepath=destination, scene=neutral)
        finally:
            neutral.render.image_settings.file_format = previous
    log.info("Converted %s -> %s", image.name, destination)
    return True
