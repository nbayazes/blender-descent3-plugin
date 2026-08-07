"""Writing texture images out beside an exported model.

Export writes texture *names* into the model's TXTR chunk; this writes the
pixels those names refer to, so a model and the images it needs can be handed
over together.

Three things about Blender's image API make this less obvious than it looks,
and every one of them fails silently rather than raising:

1. ``Image.save(filepath=...)`` **ignores** ``file_format``. Setting a copy's
   format to ``TARGA`` and saving to ``Hull.tga`` writes PNG bytes into a file
   named ``.tga`` -- verified by inspecting the magic number. Descent 3 would
   reject it, and nothing in Blender would have complained.
2. ``Image.save_render()`` *does* convert, but it renders through the scene's
   colour management. Blender 5.2 ships with the view transform defaulting to
   **AgX**, so textures would come out tone-mapped and washed out. Only
   ``Standard`` reproduces the original pixels byte-for-byte.
3. ``save_render`` obeys the *rest* of the scene's output settings too, not
   just its file format. ``color_mode`` and ``color_depth`` come from the same
   Output Properties panel the user configured for rendering, so a scene left
   on RGB -- or on BW, after somebody rendered a matte -- writes every texture
   with its alpha channel discarded. A grate exports solid, a canopy exports
   opaque, and the file opens perfectly well in an image viewer: it is only
   wrong once the game reads it.

So conversion goes through ``save_render`` with both colour management and the
output settings neutralised and restored afterwards, and the common case -- a
source file already in the requested format and with no unsaved edits -- skips
Blender entirely and copies the bytes.

A texture ID is also a *filename* here, and it arrives from a TXTR chunk in a
model of unknown provenance or from a material name somebody typed. Every one
is put through :func:`~descent3_plugin.naming.rejected_texture_name` before it
is joined onto the export folder, and the path that join produces is checked
against the folder again afterwards, because the failure being guarded is
overwriting a file elsewhere on the disk without asking.
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
#: says. RGBA rather than RGB because a Descent 3 texture's alpha is not
#: decoration -- it is what makes a grate see-through and a canopy glass -- and
#: both formats this add-on writes carry it.
NEUTRAL_COLOR_MODE = "RGBA"

#: Bits per channel. Eight is what the game reads, and it is what the source
#: art almost always came in at; a scene set up for 16-bit PNG renders would
#: otherwise spend twice the disk space on precision that was never there and
#: hand Descent 3 a file it cannot load.
NEUTRAL_COLOR_DEPTH = "8"

#: Blender's default name for the Principled node in a new material tree.
#:
#: Import has its own copy of this. Duplicating one string is the cheaper half
#: of the trade: importing it from :mod:`descent3_plugin.import_pof` would make
#: every export drag the importer in behind it, for a constant that is fixed by
#: Blender rather than by either module.
PRINCIPLED_BSDF_NODE_NAME = "Principled BSDF"

#: Socket types that can carry a colour, and so can lead to the image a material
#: is textured with.
#:
#: What the first pass of the walk behind Base Color is restricted to; a second
#: pass without the restriction follows if that finds nothing, so this narrows
#: the search order rather than the search. It is what stops a Mix node handing
#: over its mask: ``ShaderNodeMix`` lists Factor first -- socket 0 of ten, since
#: the node keeps one input set per data type -- so a walk taking its inputs in
#: the order Blender stores them reaches whatever drives the blend before either
#: of the images being blended.
#:
#: ``SHADER`` is here because a Mix Shader is a perfectly ordinary way to layer
#: two textured shaders, and the image is then two links further back on the
#: shader side rather than the Fac side.
COLOUR_SOCKET_TYPES = frozenset({"RGBA", "SHADER"})


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


class _NeutralImageSettings:
    """Temporarily point the scene's output settings at a texture file.

    ``save_render`` encodes through ``scene.render.image_settings``, which is
    the very block the user set up in Output Properties for rendering their
    animation. Overriding ``file_format`` alone is not enough: ``color_mode``
    and ``color_depth`` survive, so a scene on RGB drops the alpha channel from
    every texture written and a scene on 16-bit writes files the game will not
    load. Neither raises and neither is visible in the result until something
    that should be transparent is not.

    Restoration puts ``file_format`` back *first*: the valid ``color_mode`` and
    ``color_depth`` members depend on it, so the saved pair is only assignable
    again once the format it was read under is back in place.
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
                # scene's own setting in force. Say so: the texture is about to
                # be written with whatever the user had configured for
                # rendering, which is exactly the silent conversion this class
                # exists to prevent.
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
        The node, or ``None`` if the material has no Principled BSDF -- an
        emission-only or hand-built shader, where nothing here can say which
        image is "the" colour and the caller falls back to guessing.
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

    The search walks backwards from the Base Color socket, breadth-first, so the
    image *nearest* the shader wins -- but it follows the colour-carrying links
    to exhaustion before it will look at any other kind. That is what makes a Mix
    node give up its base layer rather than its mask: a Mix's sockets start with
    Factor, so a search that took its inputs in the order Blender stores them
    picked whatever drives the *blend* -- a greyscale mask, in the standard
    layering setup -- and exported that as the model's texture, which is a worse
    answer than the naive first-image rule it replaced. A chain through a
    reroute, a Gamma or a Hue/Saturation is followed without needing to know what
    any of those nodes do: only the links and the socket types are consulted,
    which is what keeps this right for node types the add-on has never heard of.

    Args:
        mat: Material to inspect. Must use nodes; the caller has checked.

    Returns:
        The image, or ``None`` when nothing image-shaped feeds Base Color --
        including the case of a texture hidden inside a node group, whose
        internal tree is deliberately not descended into. Every ``None`` sends
        the caller to :func:`_first_linked_image`, so a material this cannot
        read is no worse off than it was before the search existed.
    """
    bsdf = _find_principled(mat)
    if bsdf is None:
        return None
    socket = bsdf.inputs.get("Base Color")
    if socket is None or not socket.is_linked:
        return None

    start = [link.from_node for link in socket.links]
    # Two passes rather than one with the colour links merely queued first: a
    # colour-corrected diffuse is one node further from the shader than the mask
    # on the Mix's Factor beside it, so "nearest, colour preferred" would still
    # come back with the mask. Exhausting the colour side first is what makes the
    # preference mean something. The second pass then still finds an image that
    # reaches Base Color through something this cannot recognise as colour, which
    # is better than sending an ordinary material to the guess in
    # :func:`_first_linked_image`.
    return (
        _nearest_image(start, colour_only=True)
        or _nearest_image(start, colour_only=False)
    )


def _nearest_image(
    start_nodes: list, colour_only: bool
) -> bpy.types.Image | None:
    """Search backwards through a node tree for the nearest image.

    Args:
        start_nodes: Nodes to start from -- whatever feeds the socket being
            traced.
        colour_only: Follow only inputs whose type is in
            :data:`COLOUR_SOCKET_TYPES`, ignoring factors, vectors and scalars.

    Returns:
        The image on the nearest reachable image-texture node, or ``None``.
        Breadth-first, so "nearest" is by number of links rather than by
        whatever order the nodes happen to sit in the tree. A node group's
        internal tree is deliberately not descended into.
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

    This is a guess, and it is only reached for a material whose Base Color
    leads nowhere: no Principled BSDF, an unconnected socket, or a chain that
    ends somewhere this module cannot follow. Guessing beats returning nothing,
    because the alternative for a material that plainly has a texture on it is
    to report it as having none and export the model without its image.

    Args:
        mat: Material to inspect. Must use nodes; the caller has checked.

    Returns:
        The first image-texture node's image, preferring one with a linked
        output so a stray unconnected texture node left over from experimenting
        does not win over a node that is wired into something.
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
    material has to be picked, and it must be the colour one. Taking the first
    linked image-texture node instead -- which is all this used to do -- reads
    whichever node was *created* first, so a material whose Normal Map was set
    up before its Base Color exported the normal map as the game texture: a
    model that renders lilac and wrong, from a .blend that looks perfect in the
    viewport. Base Color is therefore followed properly, and the old rule is
    kept only for materials that leave it unconnected.

    Args:
        mat: Material to inspect.

    Returns:
        The image, or ``None`` when the material has no image texture at all.
    """
    if not mat or not mat.use_nodes or mat.node_tree is None:
        return None
    return _image_behind_base_colour(mat) or _first_linked_image(mat)


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


def _rejected_destination(directory: str, destination: str) -> str | None:
    """Return why ``destination`` is not safely inside ``directory``, or None.

    :func:`~descent3_plugin.naming.rejected_texture_name` has already refused
    every texture ID that could climb out of the export folder, so reaching
    here with a problem means something got past it: a symlinked folder, a path
    form some future Windows learns to interpret, a caller that forgot the
    check. It is worth checking twice because of what the first check is
    preventing -- a file overwritten somewhere the user is not looking, without
    a prompt and without a trace of what used to be there.

    So the join is re-examined against what the filesystem says the two paths
    actually resolve to, rather than against what they were spelled as.

    Args:
        directory: Export folder the file is meant to land in. It need not
            exist yet; both sides resolve the same way either way, since the
            folder is only created once a write is about to happen.
        destination: Path the write would use.

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
            name written to the TXTR chunk, so the file is named after it and
            import finds it again -- which is also why it is validated as a
            filename here rather than sanitised into one.
        directory: Folder to write into. Created when the first texture is
            actually written, so an export where every texture fails leaves no
            empty folder sitting beside the model implying otherwise.
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
    directory_ready = False

    for texture_name in sorted(materials):
        problem = rejected_texture_name(texture_name)
        if problem:
            # Judged on the name, not only on the path it produces. A rejected
            # path can only report that something would land somewhere else;
            # the name can say which texture is at fault and what about it is
            # wrong, which is what the user has to go and rename.
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

    A source file already in the requested format is copied byte-for-byte: no
    re-encode, no colour management, no chance of the conversion traps this
    module exists to avoid. Anything else is converted, and so is an image with
    unsaved edits -- Texture Paint keeps them in the datablock until somebody
    saves, so the file on disk is still the version from before the artist
    picked up a brush, and copying it exports art that has not existed since
    the session started.

    Args:
        image: Image to write.
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
