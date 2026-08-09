"""Writing texture images out beside an exported model.

Export writes texture *names* into the model's TXTR chunk; this writes the
pixels those names refer to.

Three silent traps in Blender's image API shape this module:

1. ``Image.save(filepath=...)`` **ignores** ``file_format`` -- a copy set to
   ``TARGA`` and saved to ``Hull.tga`` gets PNG bytes, which the game rejects.
2. ``Image.save_render()`` does convert, but through the scene's colour
   management, and Blender 5.2 defaults the view transform to **AgX**. Only
   ``Standard`` reproduces the original pixels byte-for-byte.
3. ``save_render`` obeys the scene's ``color_mode`` and ``color_depth`` too, so
   a scene left on RGB -- or BW -- writes every texture with alpha discarded.

Conversion therefore neutralises both colour management and the output settings
and restores them afterwards; a source file already in the requested format and
with no unsaved edits skips Blender and is copied byte-for-byte.

A texture ID is also a *filename*, and arrives from a TXTR chunk of unknown
provenance or a typed material name. Each goes through
:func:`~descent3_plugin.naming.rejected_texture_name` before being joined onto
the export folder, and the joined path is re-checked against that folder,
because the failure guarded against is overwriting a file elsewhere on disk.
"""

import logging
import os
import shutil

import bpy

from .naming import blender_file_format, rejected_texture_name, texture_filename

log = logging.getLogger(__package__)

#: View transform that reproduces an image's stored pixels. ``Standard`` was
#: measured to byte-match a plain ``Image.save()``; ``Raw``, ``AgX`` and
#: ``Filmic`` each produce different output.
NEUTRAL_VIEW_TRANSFORM = "Standard"

#: Look, exposure and gamma are reset alongside the view transform. A scene
#: carrying an exposure offset would otherwise bake it into every texture.
NEUTRAL_LOOK = "None"

#: Channel layout every texture is written with, whatever Output Properties
#: says. RGBA rather than RGB because a Descent 3 texture's alpha is what makes
#: a grate see-through and a canopy glass, and both formats written carry it.
NEUTRAL_COLOR_MODE = "RGBA"

#: Bits per channel. Eight is what the game reads; a scene set up for 16-bit
#: PNG renders would otherwise hand Descent 3 a file it cannot load.
NEUTRAL_COLOR_DEPTH = "8"

#: Blender's default name for the Principled node in a new material tree.
#: Deliberately duplicated in :mod:`descent3_plugin.import_pof` rather than
#: shared, so every export does not drag the importer in behind it.
PRINCIPLED_BSDF_NODE_NAME = "Principled BSDF"

#: Socket types that can carry a colour, and so can lead to a material's image.
#: The walk behind Base Color takes a first pass restricted to these and an
#: unrestricted second pass, narrowing the search order rather than the search.
#: That is what stops a Mix node handing over its mask: ``ShaderNodeMix`` keeps
#: one input set per data type -- ten sockets, not three, largely duplicate A/B
#: pairs -- and lists Factor as socket 0, ahead of either image being blended.
#: ``SHADER`` is here because a Mix Shader is an ordinary way to layer two
#: textured shaders.
COLOUR_SOCKET_TYPES = frozenset({"RGBA", "SHADER"})


class _NeutralColourManagement:
    """Temporarily disable colour management for the duration of a save.

    ``save_render`` has no way to bypass the scene's display transform, so the
    scene is adjusted and every field restored on exit -- an error mid-export
    must not leave the user's scene tone-mapped differently than they left it.
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


class _NeutralImageSettings:
    """Temporarily point the scene's output settings at a texture file.

    ``save_render`` encodes through ``scene.render.image_settings`` -- the block
    the user set up in Output Properties. Overriding ``file_format`` alone is
    not enough: a surviving ``color_mode`` of RGB drops alpha from every texture
    and a 16-bit ``color_depth`` writes files the game will not load, silently.

    Restoration puts ``file_format`` back *first*: the valid ``color_mode`` and
    ``color_depth`` members depend on it.
    """

    def __init__(self, scene: bpy.types.Scene, fmt: str) -> None:
        self.settings = scene.render.image_settings
        self.fmt = fmt
        self._saved: dict[str, object] = {}

    def __enter__(self) -> bpy.types.ImageFormatSettings:
        settings = self.settings
        self._saved = {
            "file_format": settings.file_format,
            "color_mode": settings.color_mode,
            "color_depth": settings.color_depth,
        }
        # Format first, for the same reason it is restored last: it decides
        # which of the enum members below exist at all.
        settings.file_format = blender_file_format(self.fmt)
        for key, value in (
            ("color_mode", NEUTRAL_COLOR_MODE),
            ("color_depth", NEUTRAL_COLOR_DEPTH),
        ):
            try:
                setattr(settings, key, value)
            except TypeError:
                # A format that cannot represent the neutral value leaves the
                # scene's own setting in force -- exactly the silent conversion
                # this class exists to prevent, so say so.
                log.warning(
                    "Format %s has no '%s' %s; that texture is written with the "
                    "scene's own setting instead.",
                    self.fmt, value, key.replace("_", " "),
                )
        return self.settings

    def __exit__(self, exc_type, exc, tb) -> None:
        settings = self.settings
        for key in ("file_format", "color_mode", "color_depth"):
            try:
                setattr(settings, key, self._saved[key])
            except (TypeError, AttributeError):
                pass


def _find_principled(mat: bpy.types.Material) -> bpy.types.Node | None:
    """Return the material's Principled BSDF node, or None.

    Looks up Blender's default node name first, then falls back to searching by
    type, because a material the user authored may well have renamed it.

    Args:
        mat: Material to search. Must use nodes; the caller has checked.

    Returns:
        The node, or ``None`` for an emission-only or hand-built shader, where
        nothing here can say which image is "the" colour one.
    """
    node = mat.node_tree.nodes.get(PRINCIPLED_BSDF_NODE_NAME)
    if node is not None and node.type == "BSDF_PRINCIPLED":
        return node
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _image_behind_base_colour(mat: bpy.types.Material) -> bpy.types.Image | None:
    """Return the image that actually drives the material's Base Color.

    Walks backwards from Base Color breadth-first, so the image *nearest* the
    shader wins, but exhausts the colour-carrying links before looking at any
    other kind. Only links and socket types are consulted, so a chain through a
    reroute, a Gamma or a node type the add-on has never heard of is followed
    without needing to know what it does.

    Args:
        mat: Material to inspect. Must use nodes; the caller has checked.

    Returns:
        The image, or ``None`` when nothing image-shaped feeds Base Color --
        including a texture inside a node group, which is deliberately not
        descended into. Every ``None`` sends the caller to
        :func:`_first_linked_image`.
    """
    bsdf = _find_principled(mat)
    if bsdf is None:
        return None
    socket = bsdf.inputs.get("Base Color")
    if socket is None or not socket.is_linked:
        return None

    start = [link.from_node for link in socket.links]
    # Two passes rather than one queue with colour merely first: a mask on a
    # Mix's Factor can sit nearer the shader than a colour-corrected diffuse, so
    # "nearest, colour preferred" would still come back with the mask.
    return (
        _nearest_image(start, colour_only=True)
        or _nearest_image(start, colour_only=False)
    )


def _nearest_image(
    start_nodes: list, colour_only: bool
) -> bpy.types.Image | None:
    """Search backwards through a node tree for the nearest image.

    Args:
        colour_only: Follow only inputs whose type is in
            :data:`COLOUR_SOCKET_TYPES`, ignoring factors, vectors and scalars.

    Returns:
        The image on the nearest reachable image-texture node, or ``None``.
        Breadth-first, so "nearest" counts links rather than tree order. A node
        group's internal tree is deliberately not descended into.
    """
    # Node names are unique within a tree, so they identify a visited node
    # without relying on bpy structs comparing the way a set needs them to.
    seen: set[str] = set()
    queue = list(start_nodes)
    while queue:
        node = queue.pop(0)
        if node is None or node.name in seen:
            continue
        seen.add(node.name)
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image
        for node_input in node.inputs:
            if colour_only and node_input.type not in COLOUR_SOCKET_TYPES:
                continue
            queue.extend(link.from_node for link in node_input.links)
    return None


def _first_linked_image(mat: bpy.types.Material) -> bpy.types.Image | None:
    """Return the first connected image in the tree, as a last resort.

    A guess, reached only when Base Color leads nowhere: no Principled BSDF, an
    unconnected socket, or a chain this module cannot follow. It beats reporting
    a material that plainly has a texture on it as having none.

    Args:
        mat: Material to inspect. Must use nodes; the caller has checked.

    Returns:
        The first image-texture node's image, preferring one with a linked
        output so a stray unconnected node left over from experimenting does not
        win over one that is wired into something.
    """
    unlinked = None
    for node in mat.node_tree.nodes:
        if node.type != "TEX_IMAGE" or node.image is None:
            continue
        if any(output.is_linked for output in node.outputs):
            return node.image
        if unlinked is None:
            unlinked = node.image
    return unlinked


def _image_for_material(mat: bpy.types.Material) -> bpy.types.Image | None:
    """Return the image driving a material's colour, if it has one.

    Descent 3 gives a face one texture, so exactly one image out of a PBR
    material has to be picked and it must be the colour one. The old rule --
    first linked image-texture node -- reads whichever node was *created* first,
    so a material whose Normal Map predated its Base Color exported the normal
    map as the game texture. It is kept only as the fallback for a material
    whose Base Color leads nowhere.

    Returns:
        The image, or ``None`` when the material has no image texture at all.
    """
    if not mat or not mat.use_nodes or mat.node_tree is None:
        return None
    return _image_behind_base_colour(mat) or _first_linked_image(mat)


def _source_path(image: bpy.types.Image) -> str | None:
    """Return the image's source file on disk, if it has a usable one.

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


def _rejected_destination(directory: str, destination: str) -> str | None:
    """Return why ``destination`` is not safely inside ``directory``, or None.

    :func:`~descent3_plugin.naming.rejected_texture_name` has already refused
    every texture ID that could climb out of the export folder, so a problem
    here means something got past it: a symlinked folder, a path form some
    future Windows learns to interpret, a caller that forgot the check. The
    join is therefore re-examined against what the filesystem resolves the two
    paths to rather than how they were spelled, because the failure being
    guarded is a file overwritten where the user is not looking.

    Args:
        directory: Export folder the file is meant to land in. It need not
            exist yet; both sides resolve the same way either way, since the
            folder is only created once a write is about to happen.

    Returns:
        A message describing the problem, or ``None`` when the file lands
        inside the folder.
    """
    root = os.path.normcase(os.path.realpath(directory))
    resolved = os.path.normcase(os.path.realpath(destination))
    try:
        inside = os.path.commonpath([root, resolved]) == root
    except ValueError:
        # Two different Windows drives have no common path at all, so asking
        # for one raises rather than returning something falsy.
        inside = False
    if not inside or resolved == root:
        return (
            f"writing it would land on {destination!r}, which is not inside "
            f"the export folder {directory!r}"
        )
    return None


def export_textures(
    materials: dict[str, bpy.types.Material],
    directory: str,
    fmt: str,
    scene: bpy.types.Scene,
) -> tuple[list[str], list[str]]:
    """Write each texture's image into ``directory``.

    Args:
        materials: Texture ID to the material carrying its image. The key is the
            name written to the TXTR chunk and becomes the filename, which is
            why it is validated as one here rather than sanitised into one.
        directory: Folder to write into. Created only when the first texture is
            actually written, so a wholly failed export leaves no empty folder
            beside the model implying otherwise.
        fmt: Key of :data:`~descent3_plugin.naming.TEXTURE_FORMATS`.
        scene: Scene whose colour management is borrowed for conversion.

    Returns:
        A ``(written, problems)`` pair: the filenames created, and one message
        per texture that could not be written, for the caller to report.
    """
    written: list[str] = []
    problems: list[str] = []
    directory_ready = False

    for texture_name in sorted(materials):
        problem = rejected_texture_name(texture_name)
        if problem:
            # Judged on the name, not only on the path it produces: a rejected
            # path can only say the file would land somewhere else, while the
            # name can say which texture the user has to go and rename.
            problems.append(f"{texture_name}: {problem}")
            continue

        mat = materials[texture_name]
        image = _image_for_material(mat)
        if image is None:
            problems.append(
                f"{texture_name}: material has no image texture, nothing to write"
            )
            continue

        filename = texture_filename(texture_name, fmt)
        destination = os.path.join(directory, filename)
        problem = _rejected_destination(directory, destination)
        if problem:
            problems.append(f"{texture_name}: {problem}")
            continue

        if not directory_ready:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as e:
                # Nothing after this one can be written either, and repeating
                # the same message per texture would bury the reason.
                problems.append(
                    f"could not create {directory}: {e}; no textures were written"
                )
                break
            directory_ready = True

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

    A source file already in the requested format is copied byte-for-byte,
    sidestepping the conversion traps this module exists to prevent. An image
    with unsaved edits is converted instead: Texture Paint keeps them in the
    datablock, so the file on disk predates whatever the artist painted.

    Args:
        destination: Full path of the file to create. The caller has already
            established that it is inside the export folder.
        fmt: Key of :data:`~descent3_plugin.naming.TEXTURE_FORMATS`.
        scene: Scene whose colour management is borrowed.

    Returns:
        True when a file was written.

    Raises:
        RuntimeError: If the image carries no pixel data -- a source file that
            has been moved or deleted since it was loaded.
    """
    source = _source_path(image)
    if source and not image.is_dirty and image.file_format == blender_file_format(fmt):
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
        with _NeutralImageSettings(neutral, fmt):
            image.save_render(filepath=destination, scene=neutral)
    log.info("Converted %s -> %s", image.name, destination)
    return True
