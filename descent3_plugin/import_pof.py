"""Import side of the add-on: POF/OOF file to Blender objects.

Holds the :class:`ImportPOF` operator and the conversion that turns a parsed
:class:`~descent3_plugin.poformat.POFModel` into meshes, materials and empties.
The binary parsing itself lives in :mod:`descent3_plugin.poformat`, which has
no ``bpy`` dependency; everything in this module runs only inside Blender.
"""

import logging
import os
from collections.abc import Sequence

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Euler, Matrix, Vector

from . import poformat
from .config import CONFIG_FILENAME, Config, config_for_path, resolved_search_dirs
from .constants import (
    ATTACH_EMPTY_DISPLAY_SIZE,
    GUN_EMPTY_DISPLAY_SIZE,
    IMPORT_OT_IDNAME,
    MARKER_FORWARD_AXIS,
    MARKER_UP_AXIS,
    POF_FILENAME_EXT,
    POF_FILTER_GLOB,
    PROP_KEY_ADDON_MATERIAL,
    PROP_KEY_FLAGS,
    PROP_KEY_HAS_UVEC,
    PROP_KEY_INDEX,
    PROP_KEY_MOVEMENT_AXIS,
    PROP_KEY_MOVEMENT_TYPE,
    PROP_KEY_PARENT,
    PROP_KEY_PROPERTIES,
    PROP_KEY_SUBMODEL_NAME,
    PROP_KEY_TEXTURE,
)
from .mathutil import NORMALIZE_EPSILON, descent_to_blender
from .poformat import AttachPoint, GunBank, ModelFace, POFModel, Submodel
from .preferences import get_preferences
from .texutil import clean_texture_dir, find_texture_image

# The log channel is the package, not this module, so console output keeps the
# single "[descent3_plugin]" prefix that register() configures.
log = logging.getLogger(__package__)

# ---------------------------------------------------------------------------
# Import-side constants
# ---------------------------------------------------------------------------

#: Blender's default name for the Principled node in a new material node tree.
PRINCIPLED_BSDF_NODE_NAME = "Principled BSDF"

#: Where the image texture node is placed, left of and above the Principled
#: BSDF so the link between them is visible without rearranging the tree.
TEX_NODE_LOCATION = (-400, 300)

#: A POF face needs at least this many corners to become a Blender polygon.
#: Degenerate one- and two-vertex faces are dropped.
MIN_FACE_VERTICES = 3

#: How many missing texture names to list before eliding the rest.
MAX_MISSING_TEXTURES_SHOWN = 8

#: Prefix on ``print()`` output, which reaches Blender's system console without
#: going through the logging handler that would otherwise label it.
CONSOLE_PREFIX = "[Descent3]"


class ImportPOF(bpy.types.Operator, ImportHelper):
    """Import a Descent 3 POF/OOF model file"""

    bl_idname = IMPORT_OT_IDNAME
    bl_label = "Import Descent 3 POF"
    bl_description = "Import a Descent 3 polygon model (.pof / .oof)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = POF_FILENAME_EXT
    filter_glob: StringProperty(default=POF_FILTER_GLOB, options={"HIDDEN"})

    import_guns: BoolProperty(
        name="Import Gun Points",
        description="Import gun point markers as empty objects",
        default=True,
    )
    import_attach: BoolProperty(
        name="Import Attach Points",
        description="Import attach points as empty objects",
        default=True,
    )
    texture_dir: StringProperty(
        name="Texture Folder",
        description=(
            "Optional folder to search for texture images. Paste a path here "
            "(a browse button cannot be shown while the import dialog is open). "
            "If set, it is searched before the model's own folder. Leave empty "
            "to use only the folder the model is in"
        ),
        default="",
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Run the import and report the outcome to Blender."""
        return load_pof(context, self.filepath, self)

    def draw(self, context: bpy.types.Context) -> None:
        """Lay out the operator's options in the file browser sidebar."""
        layout = self.layout
        layout.prop(self, "import_guns")
        layout.prop(self, "import_attach")
        layout.prop(self, "texture_dir")


def load_pof(
    context: bpy.types.Context,
    filepath: str,
    operator: ImportPOF | None = None,
) -> set[str]:
    """Import a POF/OOF file into the current scene.

    Every submodel becomes an object inside a new collection named after the
    file, parented to mirror the model's own hierarchy.

    Args:
        context: Blender context supplying the scene to import into.
        filepath: Path of the ``.pof`` or ``.oof`` file to read.
        operator: Operator whose options steer the import and which receives
            user-facing reports. Passing ``None`` stays silent and skips gun
            and attach points entirely -- it does not fall back to the
            operator's defaults, which enable both.

    Returns:
        ``{"FINISHED"}`` on success, or ``{"CANCELLED"}`` if the file could not
        be read or parsed.
    """
    log.info("=== Importing POF: %s ===", filepath)
    print(f"{CONSOLE_PREFIX} Importing: {filepath}")

    try:
        with open(filepath, "rb") as f:
            data = f.read()
        log.info("Read %d bytes", len(data))
    except Exception as e:
        log.error("Failed to read %s: %s", filepath, e)
        if operator:
            operator.report({"ERROR"}, f"Failed to read file: {e}")
        return {"CANCELLED"}

    try:
        model = poformat.parse_pof(data)
    except Exception as e:
        log.error("Failed to parse %s: %s", filepath, e)
        if operator:
            operator.report({"ERROR"}, f"Failed to parse POF: {e}")
        return {"CANCELLED"}

    config, config_warnings = config_for_path(filepath)
    for warning in config_warnings:
        log.warning("%s: %s", config.source_path or CONFIG_FILENAME, warning)
        if operator:
            operator.report({"WARNING"}, f"config: {warning}")
    if config.source_path:
        print(f"{CONSOLE_PREFIX} settings from {config.source_path}")

    prefs = get_preferences(context)
    model_dir = os.path.dirname(os.path.abspath(filepath))
    search_dirs = _texture_search_dirs(model_dir, operator, config, prefs)

    # name -> True if a matching image file was found on disk
    tex_report: dict[str, bool] = {}

    # Messages the user has to see, collected as the import runs and reported in
    # one place at the end. Gathering them rather than reporting from the depths
    # is what keeps the mesh and material helpers free of the operator: they
    # describe what happened, ``load_pof`` decides how to say it -- the same
    # arrangement export uses for its texture ``problems``.
    notices: list[str] = []

    log.info("Texture search directories: %s", search_dirs)
    log.info("Model v%d: %d textures %s, %d submodels",
             model.version, len(model.textures), model.textures, len(model.submodels))
    print(f"{CONSOLE_PREFIX} v{model.version}: {len(model.textures)} textures, "
          f"{len(model.submodels)} submodels; texture search: {search_dirs}")

    # Create a collection for this model
    model_name = os.path.splitext(os.path.basename(filepath))[0]
    collection = bpy.data.collections.new(model_name)
    context.scene.collection.children.link(collection)

    # Import each submodel, keyed by its own index so hierarchy and parenting
    # lookups stay correct even if submodel indices are sparse or out of order.
    # material_cache lets submodels that share a texture reuse one material.
    material_cache: dict[str, bpy.types.Material] = {}
    obj_by_index: dict[int, bpy.types.Object] = {}
    for sm in model.submodels:
        log.info("Importing submodel %d: '%s' (verts=%d, faces=%d, parent=%d)",
                 sm.index, sm.name, len(sm.vertices), len(sm.faces), sm.parent)
        obj = _import_submodel(
            context, model, sm, collection, search_dirs, tex_report,
            material_cache, config, notices,
        )
        if obj:
            obj_by_index[sm.index] = obj

    # Set up parent-child relationships.
    #
    # ``matrix_parent_inverse`` is deliberately left at identity, which is what a
    # new object already has, so a child's world position is its parent's plus
    # its own ``location`` -- offsets add down the chain. That is the engine's
    # own rule (reference/polymodel.cpp, MinMaxSubmodel: ``offset += sm->offset``
    # with no parent rotation applied), it is the placement export reads back as
    # world-space deltas, and it is what makes a round trip return the file's
    # offsets unchanged.
    #
    # This used to assign ``parent.matrix_world.inverted()`` here, the way
    # Ctrl+P does. It was harmless only by accident: nothing has evaluated the
    # depsgraph at this point, so every ``matrix_world`` is still identity and
    # the assignment stored identity. Had it ever seen a real parent matrix it
    # would have cancelled the parent out, leaving each submodel at its *raw*
    # offset from the model origin -- correct for one level, and wrong for
    # everything below it, with export then writing deltas that no longer match
    # the file.
    log.info("Setting up parent-child hierarchy")
    for sm in model.submodels:
        child = obj_by_index.get(sm.index)
        parent = obj_by_index.get(sm.parent)  # sm.parent == NO_PARENT -> None
        if child is not None and parent is not None:
            child.parent = parent
            log.info("  '%s' -> parent index %d", sm.name, sm.parent)

    if operator and operator.import_guns:
        log.info("Importing %d gun points", len(model.gun_banks))
        _import_points(
            model.gun_banks,
            config.naming.gun_prefix,
            "SINGLE_ARROW",
            _marker_size(prefs, "gun_marker_size", GUN_EMPTY_DISPLAY_SIZE),
            collection,
            obj_by_index,
        )

    if operator and operator.import_attach:
        log.info("Importing %d attach points", len(model.attach_points))
        _import_points(
            model.attach_points,
            config.naming.attach_prefix,
            "PLAIN_AXES",
            _marker_size(prefs, "attach_marker_size", ATTACH_EMPTY_DISPLAY_SIZE),
            collection,
            obj_by_index,
        )

    _select_imported(context, obj_by_index)
    # Before the summary, so the summary is the last thing the status bar shows
    # while the detail stays above it in the Info editor.
    for notice in notices:
        print(f"{CONSOLE_PREFIX} {notice}")
        log.warning("%s", notice)
        if operator:
            operator.report({"WARNING"}, notice)
    _report_textures(tex_report, model, model_name, operator)

    return {"FINISHED"}


def _marker_size(prefs, attribute: str, default: float) -> float:
    """Return a marker size from preferences, or ``default`` without them.

    Args:
        prefs: Add-on preferences, or ``None`` when the add-on is not
            registered (the headless scripts call the importer directly).
        attribute: Preference property to read.
        default: Value to use when preferences are unavailable.

    Returns:
        The configured size, or ``default``.
    """
    if prefs is None:
        return default
    return float(getattr(prefs, attribute, default))


def _texture_search_dirs(
    model_dir: str,
    operator: ImportPOF | None,
    config: Config,
    prefs=None,
) -> list[str]:
    """Build the ordered list of directories to search for texture images.

    Args:
        model_dir: Folder the model itself lives in.
        operator: Operator carrying the optional ``texture_dir`` override.
        config: Project configuration supplying extra search directories.
        prefs: Add-on preferences supplying the user's personal texture
            library, or ``None`` when the add-on is not registered.

    Returns:
        Directories in search order, most specific first: the dialog's Texture
        Folder, the project's configured directories, the model's own folder,
        and finally the user's texture library as a catch-all.

        The folder export writes textures into is deliberately *not* added.
        Widening the search on the user's behalf makes the importer harder to
        predict, and a project may be sandboxed off from its own exports on
        purpose; anyone who wants those textures found can list the folder in
        ``textures.search_dirs``.
    """
    search_dirs: list[str] = []
    if operator is not None:
        # Tolerate a pasted path with surrounding whitespace or quotes
        # (Windows "Copy as path" wraps the path in double quotes).
        tex_dir = clean_texture_dir(getattr(operator, "texture_dir", "") or "")
        if tex_dir:
            tex_dir = os.path.abspath(bpy.path.abspath(tex_dir))
            if os.path.isdir(tex_dir):
                search_dirs.append(tex_dir)
            else:
                log.warning("Texture Folder does not exist: %s", tex_dir)
    for configured in resolved_search_dirs(config):
        if configured in search_dirs:
            continue
        if os.path.isdir(configured):
            search_dirs.append(configured)
        else:
            log.warning("Configured texture directory does not exist: %s",
                        configured)
    if model_dir not in search_dirs:
        search_dirs.append(model_dir)


    library = clean_texture_dir(getattr(prefs, "texture_library", "") or "")
    if library:
        library = os.path.normpath(os.path.abspath(bpy.path.abspath(library)))
        if not os.path.isdir(library):
            log.warning("Texture Library does not exist: %s", library)
        elif library not in search_dirs:
            search_dirs.append(library)
    return search_dirs


def _basis(forward: Vector, up: Vector) -> Matrix:
    """Return the rotation whose axes are ``forward``, ``up`` and their cross.

    Args:
        forward: Unit direction to become the matrix's third column.
        up: Unit direction perpendicular to ``forward``, the second column.

    Returns:
        A 3x3 rotation mapping the standard basis onto ``(up x forward, up,
        forward)``. ``Matrix`` builds from *rows*, so it is transposed: the
        columns are what say where each axis is sent.
    """
    return Matrix((up.cross(forward), up, forward)).transposed()


#: The marker's own axes as a basis, so the file's basis can be expressed
#: relative to it rather than the two agreeing by coincidence. Derived from the
#: shared constants, which is what keeps this and export reading the same
#: rotation.
_MARKER_BASIS = _basis(Vector(MARKER_FORWARD_AXIS), Vector(MARKER_UP_AXIS))


def _marker_rotation(point: GunBank | AttachPoint) -> Euler | None:
    """Return the rotation that aims a marker empty the way the file aims it.

    A gun bank's ``normal`` is where it fires and an attach point's is where the
    thing bolted to it faces. Both were read out of the file and then dropped on
    the floor: the empty was created unrotated, and export wrote a fixed
    placeholder direction for every marker, so one round trip pointed every gun
    on the ship the same way. Carrying it in the empty's rotation is what ends
    that, and it puts the direction somewhere the user can see and change --
    :data:`~descent3_plugin.constants.MARKER_FORWARD_AXIS` is the axis Blender
    draws the arrow along.

    An attach point may also carry an up vector, which fixes its roll about that
    direction. When the file supplies one the full orientation is rebuilt from
    the pair, so the roll survives too. Otherwise the shortest rotation from the
    marker's own axis is used, which picks *a* roll without pretending it means
    anything -- the file did not say, and neither does the engine.

    Args:
        point: Gun bank or attach point, in file coordinates.

    Returns:
        The rotation to give the empty, or ``None`` when the file's direction is
        degenerate -- a zero-length normal aims at nothing, and an arbitrary
        rotation would look like a decision somebody made.
    """
    forward = Vector(descent_to_blender(point.normal).as_tuple())
    if forward.length < NORMALIZE_EPSILON:
        return None
    forward.normalize()

    if getattr(point, "has_uvec", False):
        up = Vector(descent_to_blender(point.uvec).as_tuple())
        # Gram-Schmidt. The file's two vectors are meant to be perpendicular but
        # are stored as floats and need not still be, and a basis that is not
        # quite orthogonal is not a rotation -- ``to_euler`` would read a shear
        # as a turn.
        up -= forward * up.dot(forward)
        if up.length >= NORMALIZE_EPSILON:
            up.normalize()
            return (_basis(forward, up) @ _MARKER_BASIS.inverted()).to_euler()

    # No up vector, so any roll about the direction is as good as any other, and
    # the shortest arc is the one that leaves the empty looking least twisted.
    return Vector(MARKER_FORWARD_AXIS).rotation_difference(forward).to_euler()


def _import_points(
    points: Sequence[GunBank | AttachPoint],
    name_prefix: str,
    display_type: str,
    display_size: float,
    collection: bpy.types.Collection,
    obj_by_index: dict[int, bpy.types.Object],
) -> None:
    """Create an empty for each gun bank or attach point.

    Gun points and attach points have no Blender equivalent, so they become
    empties named with a prefix that export matches on. Renaming one in the
    outliner therefore drops it from the next export.

    Args:
        points: Objects exposing ``point``, ``normal`` and ``parent``, i.e. gun
            banks or attach points.
        name_prefix: Name prefix export recognises, from
            :mod:`descent3_plugin.constants`.
        display_type: Blender ``empty_display_type`` for the marker.
        display_size: Viewport size of the marker.
        collection: Collection the empties are linked into.
        obj_by_index: Submodel index to imported object, used for parenting.
    """
    for i, point in enumerate(points):
        empty = bpy.data.objects.new(f"{name_prefix}{i}", None)
        empty.empty_display_type = display_type
        empty.empty_display_size = display_size
        empty.location = Vector(descent_to_blender(point.point).as_tuple())
        rotation = _marker_rotation(point)
        if rotation is not None:
            empty.rotation_euler = rotation
        if getattr(point, "has_uvec", False):
            # Whether the file *chose* this roll, which is what decides if it is
            # written back out. See PROP_KEY_HAS_UVEC in constants.
            empty[PROP_KEY_HAS_UVEC] = True
        collection.objects.link(empty)
        parent = obj_by_index.get(point.parent)
        if parent is not None:
            empty.parent = parent


def _select_imported(
    context: bpy.types.Context, obj_by_index: dict[int, bpy.types.Object]
) -> None:
    """Select the imported objects and make the root submodel active.

    Args:
        context: Blender context whose view layer selection is replaced.
        obj_by_index: Submodel index to imported object.
    """
    for obj in context.view_layer.objects:
        # A view layer that has not been re-synced since objects were removed
        # from ``bpy.data`` yields None for the entries that are gone. Nothing in
        # the UI gets there -- Blender flushes between operators -- but a script
        # that clears the scene and imports in the same run does, and losing the
        # whole import to an AttributeError while *deselecting* would be a
        # cosmetic step taking the useful work down with it.
        if obj is None:
            continue
        obj.select_set(False)
    imported_objs = list(obj_by_index.values())
    for obj in imported_objs:
        obj.select_set(True)
    root = obj_by_index.get(poformat.ROOT_SUBMODEL_INDEX)
    if root is None and imported_objs:
        root = imported_objs[0]
    if root is not None:
        context.view_layer.objects.active = root


def _elided(names: Sequence[str], limit: int = MAX_MISSING_TEXTURES_SHOWN) -> str:
    """Join names for display, cutting the list off after ``limit``.

    Args:
        names: Names to list, already in the order they should be shown.
        limit: How many to show before eliding.

    Returns:
        The names comma-separated, with a trailing ``...`` when some were left
        out. The console line and the operator report have to agree word for
        word -- two spellings of the same list read as two different lists.
    """
    shown = ", ".join(names[:limit])
    return f"{shown} ..." if len(names) > limit else shown


def _report_textures(
    tex_report: dict[str, bool],
    model: POFModel,
    model_name: str,
    operator: ImportPOF | None,
) -> None:
    """Summarize texture resolution for the user.

    Missing images are surfaced explicitly, because silently producing blank
    materials looks like a broken model rather than an absent texture folder.

    Args:
        tex_report: Texture name to whether an image file was found.
        model: The imported model, for the submodel count in the summary.
        model_name: Name of the collection the model was imported into.
        operator: Operator to report through, or ``None`` to only log.
    """
    missing = sorted(name for name, found in tex_report.items() if not found)
    found_count = sum(1 for found in tex_report.values() if found)
    total_count = len(tex_report)
    if total_count:
        summary = f"textures {found_count}/{total_count} found"
        if missing:
            summary += f" (missing: {_elided(missing)})"
        log.info(summary)
        print(f"{CONSOLE_PREFIX} {summary}")

    if not operator:
        return

    msg = f"Imported {model_name}: {len(model.submodels)} submodels"
    if total_count:
        msg += f", {found_count}/{total_count} textures"
    if missing:
        operator.report({"WARNING"}, f"{msg} — missing textures: {_elided(missing)}")
    else:
        operator.report({"INFO"}, msg)


def _get_or_create_material(
    name: str,
    search_dirs: list[str],
    tex_report: dict[str, bool],
    material_cache: dict[str, bpy.types.Material],
    config: Config,
    notices: list[str],
) -> bpy.types.Material:
    """Return a material for a texture name, creating it at most once.

    Args:
        name: Texture name from the model's TXTR chunk. It becomes the
            material's name and is also recorded on the material as
            :data:`~descent3_plugin.constants.PROP_KEY_TEXTURE`, which is what
            export maps back to a texture index.
        search_dirs: Directories to search for the image, in priority order.
        tex_report: Texture name to whether an image was found. Updated here;
            the first success sticks, so a texture used by several submodels is
            not downgraded to "missing" by a later lookup.
        material_cache: Texture name to material, so submodels sharing a texture
            reuse one material rather than creating a duplicate each.
        config: Project configuration supplying the image extension order.
        notices: User-facing messages, appended to when an existing material is
            reused as-is.

    Returns:
        The material for ``name``. A material is still returned when no image is
        found, so the mesh keeps its slot assignment and the missing texture is
        reported rather than silently changing the face's material index.
    """
    cached = material_cache.get(name)
    if cached is not None:
        return cached

    existing = _find_material(name)
    if existing is not None:
        _adopt_material(existing, name, search_dirs, tex_report, config, notices)
        material_cache[name] = existing
        return existing

    mat = bpy.data.materials.new(name=name)
    # Which texture this material stands for, recorded on the material itself.
    # The name says the same thing today, but a name is not evidence: Blender
    # will rename this datablock behind our back the moment it collides, and the
    # user may rename it on purpose. Export reads this key first, so neither
    # costs the model its texture. See PROP_KEY_TEXTURE in constants.
    mat[PROP_KEY_TEXTURE] = name
    # And that this datablock is the add-on's own, which is a separate fact and
    # is only ever recorded here, where a material is made from nothing. It is
    # what lets a later import finish this material off without that permission
    # also extending to every material the line above touches -- see
    # PROP_KEY_ADDON_MATERIAL in constants for the import-after-next bug that
    # conflating the two produced.
    mat[PROP_KEY_ADDON_MATERIAL] = True
    # Blender uniquifies datablock names, so a clash here produces "<name>.001".
    # The recorded ID above means export still writes the right texture, but the
    # scene now holds two materials for one texture and the lookup that was
    # supposed to prevent that has failed -- which is a bug here, not user error.
    if mat.name != name:
        log.error(
            "Material '%s' was created as '%s'; it still exports as '%s', but "
            "the scene now has two materials for one texture. An existing "
            "material of that name should have been reused instead.",
            name, mat.name, name,
        )
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get(PRINCIPLED_BSDF_NODE_NAME)
    if bsdf is None:
        bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")

    image_path = find_texture_image(
        name, search_dirs, config.textures.extensions
    )
    tex_report[name] = tex_report.get(name, False) or bool(image_path)
    if image_path:
        _attach_image(mat, bsdf, image_path, name)
    else:
        log.info("No image found for texture '%s'; using blank material", name)

    material_cache[name] = mat
    return mat


def _find_material(name: str) -> bpy.types.Material | None:
    """Return the scene's material for a texture name, preferring a local one.

    Material names are namespaced per library, so a local ``Hull`` and a linked
    ``Hull`` can coexist. A plain ``bpy.data.materials.get(name)`` returns
    whichever comes first in the list, which is an ordering accident rather than
    a rule; asking for the local one explicitly makes the choice deliberate.

    A linked material is still reused when it is the only candidate: someone
    linking a shared material library is exactly the "the right one already
    exists" case this reuse is for, and a linked material assigns to a local
    mesh perfectly well even though it is not editable.

    Args:
        name: Texture name, which is also the material name.

    Returns:
        The local material of that name, else a linked one, else ``None``.
    """
    local = bpy.data.materials.get((name, None))
    if local is not None:
        return local
    return bpy.data.materials.get(name)


def _find_principled(mat: bpy.types.Material) -> bpy.types.Node | None:
    """Return the material's Principled BSDF node, or None.

    Looks up Blender's default node name first, then falls back to searching by
    type, because a material the user authored may well have renamed it.

    Args:
        mat: Material to search.

    Returns:
        The node, or ``None`` if the material has no Principled BSDF.
    """
    if not mat.use_nodes or mat.node_tree is None:
        return None
    node = mat.node_tree.nodes.get(PRINCIPLED_BSDF_NODE_NAME)
    if node is not None and node.type == "BSDF_PRINCIPLED":
        return node
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _has_image(mat: bpy.types.Material) -> bool:
    """Return whether the material already has an image texture node with an image.

    Args:
        mat: Material to inspect.

    Returns:
        True if any image texture node has an image assigned.
    """
    if not mat.use_nodes or mat.node_tree is None:
        return False
    return any(
        node.type == "TEX_IMAGE" and node.image is not None
        for node in mat.node_tree.nodes
    )


def _attach_image(
    mat: bpy.types.Material,
    bsdf: bpy.types.Node,
    image_path: str,
    name: str,
) -> None:
    """Load ``image_path`` and wire it into the material's base colour.

    Base Color takes a single link, so connecting to a socket that already has
    one silently replaces it. The image node is still added when that happens,
    leaving the texture available in the tree, but the user's existing
    connection is left driving the surface.

    Args:
        mat: Material to add the texture node to.
        bsdf: Principled BSDF whose Base Color input receives the texture.
        image_path: Path of the image file to load.
        name: Texture name, for logging.
    """
    try:
        img = bpy.data.images.load(image_path, check_existing=True)
        log.info("Loaded image '%s' (%dx%d) for material '%s'",
                 img.name, img.size[0], img.size[1], name)
        tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = img
        tex_node.location = TEX_NODE_LOCATION
        base_color = bsdf.inputs["Base Color"]
        if base_color.is_linked:
            log.warning(
                "Material '%s' already drives Base Color from another node, so "
                "'%s' was added to the node tree but left unconnected rather "
                "than replacing that link.", name, os.path.basename(image_path),
            )
            return
        mat.node_tree.links.new(tex_node.outputs["Color"], base_color)
    except Exception as e:
        log.error("Failed to load image '%s': %s", image_path, e)


def _adopt_material(
    mat: bpy.types.Material,
    name: str,
    search_dirs: list[str],
    tex_report: dict[str, bool],
    config: Config,
    notices: list[str],
) -> None:
    """Reuse a material that already exists in the scene under this texture name.

    Reusing rather than creating is what keeps exactly one material per texture
    ID. The material may come from an earlier import or from the user; either
    way its name *is* the texture ID, so it is the right material for these
    faces. Creating a second one would make Blender name it ``<name>.001``, so
    the scene would end up with two materials for one texture.

    Reusing it is not permission to *restyle* it, though, and the two were
    conflated until an import of a model with a ``Metal`` texture enabled nodes
    on the user's own ``Metal`` material and wired an image into its Base Colour
    -- changing every object in their scene that used it, with the only notice
    on the system console. A shared name is a coincidence, not consent.

    So the two cases are separated by evidence rather than by name: a material
    carrying :data:`~descent3_plugin.constants.PROP_KEY_ADDON_MATERIAL` is a
    datablock this add-on created, and may be finished off with the image it was
    always meant to have. Anything else is somebody's work and is used exactly as
    it arrives, with the user told that is what happened -- an untextured model
    with an explanation beats a scene quietly edited underneath them, and
    deleting the material and re-importing is a one-step fix once they know.

    Adoption does record the *texture ID* on the material when it carries none,
    so export writes the right ID even if the material is later renamed. That is
    a custom property, invisible in the viewport and changing nothing about how
    the material looks -- and, importantly, it is not the mark that grants
    permission above. It used to be: authorship was read off the texture key,
    which adoption writes, so the user's material came out of its first import
    wearing what the second import read as this add-on's own signature, and their
    material was restyled on the import after the one that spared it.

    Args:
        mat: The existing material.
        name: Texture name it stands for.
        search_dirs: Directories to search for the image.
        tex_report: Texture name to whether the texture resolved; updated here.
        config: Project configuration supplying the image extension order.
        notices: User-facing messages; appended to when the material is reused
            without being touched, because that is the case where the scene
            does not end up looking like the model.
    """
    # A linked material lives in another .blend and Blender refuses to write to
    # it at all, so it counts as somebody else's however it was made.
    editable = mat.library is None
    made_here = editable and bool(mat.get(PROP_KEY_ADDON_MATERIAL))
    if editable and PROP_KEY_TEXTURE not in mat:
        mat[PROP_KEY_TEXTURE] = name
    elif PROP_KEY_TEXTURE in mat and str(mat[PROP_KEY_TEXTURE]) != name:
        # Pinned to a different texture, by hand or by an earlier import of
        # another model. Never clobbered -- the pin is the user's decision --
        # but it silently redirects these faces at export, so say so now rather
        # than let them find out from the exported file.
        #
        # Asked of the pin itself and not of ``made_here``: a material somebody
        # pinned by hand is the case this warning is most for, and it is exactly
        # the case ``made_here`` is False for.
        notices.append(
            f"The model's '{name}' texture was given to the existing material "
            f"of that name, but that material records "
            f"'{mat[PROP_KEY_TEXTURE]}' as its Descent 3 texture, so those "
            f"faces will export as '{mat[PROP_KEY_TEXTURE]}'. Clear its "
            f"'{PROP_KEY_TEXTURE}' custom property to export them as '{name}'."
        )

    if _has_image(mat):
        log.info("Reusing existing material '%s' (already textured)", name)
        tex_report[name] = True
        return

    if not made_here:
        # Deliberately no image search: nothing is going to be attached, and
        # reporting the texture as found would claim a textured model that the
        # user is not looking at.
        log.info(
            "Reusing existing material '%s' unmodified; this add-on did not "
            "create it, so no texture was attached to it", name,
        )
        tex_report.setdefault(name, False)
        notices.append(
            f"Material '{name}' already existed in this scene and was reused "
            f"exactly as it is -- this add-on did not create it, so nothing was "
            f"attached to it and the model shows it untextured. Delete or "
            f"rename that material and re-import to get the model's own "
            f"'{name}' texture."
        )
        return

    image_path = find_texture_image(
        name, search_dirs, config.textures.extensions
    )
    tex_report[name] = tex_report.get(name, False) or bool(image_path)
    if not image_path:
        log.info(
            "Reusing existing material '%s'; no image found for it either", name
        )
        return

    if not mat.use_nodes:
        # The image has to hang off a node tree. Only reachable on Blender 4.2,
        # the oldest version supported: from 5.x a material always uses nodes
        # and the property cannot be switched off at all.
        mat.use_nodes = True

    bsdf = _find_principled(mat)
    if bsdf is None:
        log.warning(
            "Material '%s' has no Principled BSDF, so '%s' was not attached. "
            "The material is reused unmodified.",
            name, os.path.basename(image_path),
        )
        return

    log.info(
        "Material '%s' came from this add-on and had no image texture; "
        "attaching '%s'", name, os.path.basename(image_path),
    )
    _attach_image(mat, bsdf, image_path, name)


def _usable_faces(
    faces: Sequence[ModelFace], vertex_count: int, label: str
) -> tuple[list[ModelFace], str | None]:
    """Select the faces that can safely be handed to ``Mesh.from_pydata``.

    Blender does not check the indices it is given, and its own docstring says
    so: ``from_pydata`` writes them straight into the loop array. Measured on
    Blender 5.2.0 LTS, a face naming vertex 40 of 4 is built without complaint
    and survives ``Mesh.update``, and a face naming vertex ``-3`` keeps the
    ``-3`` -- ``polygon.vertices`` reads back ``(0, 1, -3)``. Nothing has
    rejected anything at that point; the mesh simply holds an index into memory
    that is not a vertex.

    What then reads it decides how bad that is, and on the same build
    ``normals_split_custom_set_from_vertices`` reads it and dies with an
    EXCEPTION_ACCESS_VIOLATION, taking every unsaved edit in the session rather
    than just this import. :func:`_validate_mesh` is what stands between the two,
    and it is genuinely what prevents the crash: ``validate`` removes such faces.

    So this is not the only defence, and the honest reason it exists is not
    "Blender will crash on the index" -- it is that letting ``validate`` be the
    one to reject the face costs the user the explanation. Blender knows only
    that a polygon was unusable, so the notice says "a face using one vertex
    twice, or a second copy of another face", which is not what happened and
    sends whoever reads it looking for the wrong thing. Filtering here names the
    submodel, the vertex count and the offending index. That an unchecked index
    also never reaches the C that reads it is defence in depth on top, and worth
    keeping: which call happens to survive one is a property of the build, not a
    promise.

    Truncated files and files from third-party tools are where these come from,
    and one damaged face is no reason to lose the other nine hundred, so the
    offenders are dropped and the rest of the submodel is imported.

    Args:
        faces: The submodel's faces, in file order.
        vertex_count: Number of vertices the submodel actually has.
        label: Submodel name, so a user with forty submodels is told which one
            to go and look at.

    Returns:
        A ``(kept, problem)`` pair. ``kept`` holds the faces to build, in file
        order, which is the order ``from_pydata`` creates polygons in and
        therefore the pairing :func:`_validate_mesh` starts from. ``problem``
        describes the dropped faces, or is ``None`` when nothing was dropped.

        Faces with fewer than :data:`MIN_FACE_VERTICES` corners are dropped
        without comment: a one- or two-corner face carries no surface, cannot
        become a polygon, and is a normal feature of the format rather than
        damage worth interrupting the user over.
    """
    kept: list[ModelFace] = []
    dropped = 0
    first_bad: int | None = None
    for face in faces:
        if len(face.vertices) < MIN_FACE_VERTICES:
            continue
        out_of_range = [
            fv.index for fv in face.vertices if not 0 <= fv.index < vertex_count
        ]
        if out_of_range:
            dropped += 1
            if first_bad is None:
                first_bad = out_of_range[0]
            continue
        kept.append(face)

    if not dropped:
        return kept, None
    return kept, (
        f"Submodel '{label}': dropped {dropped} face(s) that point at vertices "
        f"it does not have (the first asks for vertex {first_bad} of "
        f"{vertex_count}). The file is damaged, or was written by a tool that "
        f"numbers vertices differently; the rest of the submodel was imported."
    )


def _realign_faces(
    mesh: bpy.types.Mesh, built_faces: Sequence[ModelFace]
) -> list[ModelFace] | None:
    """Pair each surviving polygon with the source face it was built from.

    ``Mesh.validate`` compacts the polygon array in place, so the survivors stay
    in file order and one forward walk pairs them up. The match is on the
    polygon's vertex set rather than on a list of the reasons Blender removes a
    face: reading back what it actually did keeps this correct when a future
    version rejects something this one accepts.

    Args:
        mesh: The validated mesh.
        built_faces: Source faces in the order they were handed to
            ``from_pydata``; a superset of what survived.

    Returns:
        One source face per polygon, in polygon order, or ``None`` when a polygon
        could not be matched -- which means the walk's assumption no longer holds
        and nothing may be trusted to line up.
    """
    aligned: list[ModelFace] = []
    next_face = 0
    for poly in mesh.polygons:
        corners = sorted(poly.vertices)
        while next_face < len(built_faces):
            face = built_faces[next_face]
            next_face += 1
            if sorted(fv.index for fv in face.vertices) == corners:
                aligned.append(face)
                break
        else:
            return None
    return aligned


def _validate_mesh(
    mesh: bpy.types.Mesh,
    built_faces: list[ModelFace],
    label: str,
    notices: list[str],
) -> list[ModelFace]:
    """Let Blender vet the raw mesh, and re-pair the source faces with what survives.

    Running this before any loop data is written is not a tidiness preference.
    Measured on Blender 5.2.0 LTS, ``from_pydata`` builds a face naming the same
    vertex twice without complaint, and so does ``Mesh.update``; it is
    ``normals_split_custom_set_from_vertices`` that then takes the process down
    with an EXCEPTION_ACCESS_VIOLATION. Being an access violation and not an
    exception, the ``try``/``except`` around that call cannot catch it -- the
    user loses the session, not the import. ``validate`` removes precisely those
    faces, and the same call is safe once it has, which makes the ordering here
    the thing that prevents the crash rather than merely tidying up before it.

    :func:`_usable_faces` cannot cover this one. Every index in such a face is in
    range, so nothing about it is checkable from the file alone; it is Blender's
    rule about what counts as a polygon, not ours, that decides. The reverse is
    not true -- an out-of-range index is caught by both -- and that overlap is
    deliberate, because only the earlier check can say *which* index was wrong.

    Measured on the same build, ``validate`` drops a face with a repeated vertex,
    a face with fewer than three corners, and a face duplicating another in
    either winding; it never removes a vertex. So the polygons are no longer 1:1
    with the faces read from the file, and the callers that write UVs and
    material indices match the two up by position. Getting that wrong would not
    lose a face, it would slide every UV in the submodel onto its neighbour --
    which is why the survivors are re-paired here rather than assumed.

    Args:
        mesh: The freshly built mesh, before any loop data is written to it.
        built_faces: Source faces in the order they were handed to
            ``from_pydata``.
        label: Submodel name, for the message.
        notices: User-facing messages; appended to when faces were removed,
            because geometry disappearing from an import is not something to
            leave the user to notice.

    Returns:
        The source faces that still have a polygon, in polygon order. Empty when
        they could not be matched up: the geometry is still imported, but nothing
        that depends on the pairing is written, because a submodel with no UVs is
        a visible problem and one with shuffled UVs is a silent one.
    """
    before = len(mesh.polygons)
    repaired = mesh.validate(verbose=False)
    mesh.update()
    if not repaired:
        return built_faces

    removed = before - len(mesh.polygons)
    if not removed:
        notices.append(
            f"Submodel '{label}': Blender found the geometry invalid and "
            f"corrected it in place; no faces were lost."
        )
        return built_faces

    aligned = _realign_faces(mesh, built_faces)
    if aligned is None:
        notices.append(
            f"Submodel '{label}': Blender removed {removed} face(s) as invalid "
            f"geometry and the rest could not be matched back to the file, so "
            f"this submodel was imported without UVs or materials rather than "
            f"with the wrong ones."
        )
        return []

    notices.append(
        f"Submodel '{label}': Blender removed {removed} face(s) as invalid "
        f"geometry -- a face using one vertex twice, or a second copy of "
        f"another face. The rest of the submodel imported normally."
    )
    return aligned


def _import_submodel(
    context: bpy.types.Context,
    model: POFModel,
    sm: Submodel,
    collection: bpy.types.Collection,
    search_dirs: list[str],
    tex_report: dict[str, bool],
    material_cache: dict[str, bpy.types.Material],
    config: Config,
    notices: list[str],
) -> bpy.types.Object | None:
    """Import a single submodel as a Blender mesh object.

    Args:
        context: Blender context. Unused directly, but kept so the signature
            matches the other import helpers.
        model: The model being imported, for its texture name list.
        sm: Submodel to convert.
        collection: Collection the new object is linked into.
        search_dirs: Directories to search for texture images.
        tex_report: Texture name to whether an image was found; updated here.
        material_cache: Shared texture-name to material cache.
        config: Project configuration supplying naming conventions.
        notices: User-facing messages; appended to when geometry or a material
            did not come through as the file describes it.

    Returns:
        The new object. A submodel with no geometry becomes an empty rather than
        an empty mesh, so it can still act as a parent in the hierarchy.
    """
    fallback_name = f"{config.naming.submodel_fallback_prefix}{sm.index}"
    label = sm.name or fallback_name

    if not sm.vertices:
        obj = bpy.data.objects.new(label, None)
        obj.location = Vector(descent_to_blender(sm.offset).as_tuple())
        collection.objects.link(obj)
        # The same fields as the mesh path below, and for a stronger reason:
        # everything that makes a geometry-less submodel worth having is in
        # them. It is a joint -- a turret's pivot, carrying the ``$rotate=`` the
        # engine turns the turret with -- and with the fields dropped it is
        # indistinguishable from any other empty in the user's scene, so export
        # could only leave it out and re-root its children onto the hull.
        _record_submodel_fields(obj, sm)
        return obj

    mesh = bpy.data.meshes.new(label)

    verts = [
        Vector(descent_to_blender(v.position).as_tuple()) for v in sm.vertices
    ]

    # Every index is checked against the vertex list before any of them reach
    # Blender -- see _usable_faces, where the crash this prevents is spelled out.
    built_faces, dropped_faces = _usable_faces(sm.faces, len(verts), label)
    if dropped_faces:
        notices.append(dropped_faces)
    face_indices = [tuple(fv.index for fv in f.vertices) for f in built_faces]

    mesh.from_pydata(verts, [], face_indices)
    mesh.update()

    # Before the UVs, normals and material indices, all of which would either
    # crash on or be shuffled by geometry Blender is about to reject. What comes
    # back is the source face for each surviving polygon, in polygon order,
    # which is the pairing everything below relies on.
    kept_faces = _validate_mesh(mesh, built_faces, label, notices)

    # Set UVs (mesh.polygons is 1:1 with kept_faces, in order)
    if kept_faces:
        uv_layer = mesh.uv_layers.new(name=config.naming.uv_layer)
        for poly, pof_face in zip(mesh.polygons, kept_faces):
            for loop_idx, fv in zip(poly.loop_indices, pof_face.vertices):
                if loop_idx < len(uv_layer.data):
                    # Descent 3 puts the UV origin at the top-left, Blender at
                    # the bottom-left, so V is negated here and negated back on
                    # export. See the matching comment in export_pof.
                    uv_layer.data[loop_idx].uv = (fv.u, -fv.v)

    # Set vertex normals
    if len(sm.vertices) == len(mesh.vertices):
        custom_normals = [
            descent_to_blender(v.normal).as_tuple() for v in sm.vertices
        ]
        try:
            mesh.normals_split_custom_set_from_vertices(custom_normals)
        except Exception as e:
            # Fallback: let Blender compute normals. Worth saying out loud —
            # silently recomputed normals look like a shading bug later.
            log.warning(
                "Submodel '%s': could not apply custom normals (%s); "
                "using Blender's computed normals", label, e
            )

    obj = bpy.data.objects.new(label, mesh)
    obj.location = Vector(descent_to_blender(sm.offset).as_tuple())
    collection.objects.link(obj)

    _assign_materials(
        obj, mesh, model, kept_faces, search_dirs, tex_report, material_cache,
        config, notices,
    )

    _record_submodel_fields(obj, sm)

    return obj


def _record_submodel_fields(obj: bpy.types.Object, sm: Submodel) -> None:
    """Store the submodel fields Blender has nowhere else to keep.

    A movement axis, a ``$rotate=`` string or the name a file gave a submodel are
    not things a Blender object *has*, so they ride along as custom properties
    and export reads back the ones it writes. See the custom-property section of
    :mod:`~descent3_plugin.constants` for which those are and why the rest are
    still worth recording.

    Args:
        obj: Object built from ``sm`` -- a mesh, or the Empty standing in for a
            submodel with no geometry.
        sm: Submodel the object came from.
    """
    obj[PROP_KEY_INDEX] = sm.index
    obj[PROP_KEY_PARENT] = sm.parent
    obj[PROP_KEY_FLAGS] = sm.flags
    obj[PROP_KEY_MOVEMENT_TYPE] = sm.movement_type
    obj[PROP_KEY_MOVEMENT_AXIS] = sm.movement_axis
    if sm.props:
        obj[PROP_KEY_PROPERTIES] = sm.props
    if sm.name:
        # Only when the file named it. A submodel with no name gets a generated
        # object name, and recording *that* would turn a placeholder the add-on
        # invented into something the exporter treats as the file's own word.
        obj[PROP_KEY_SUBMODEL_NAME] = sm.name


def _assign_materials(
    obj: bpy.types.Object,
    mesh: bpy.types.Mesh,
    model: POFModel,
    kept_faces: list[ModelFace],
    search_dirs: list[str],
    tex_report: dict[str, bool],
    material_cache: dict[str, bpy.types.Material],
    config: Config,
    notices: list[str],
) -> None:
    """Build one material slot per texture used by this submodel's faces.

    Args:
        obj: Object receiving the material slots.
        mesh: The object's mesh, whose polygons are 1:1 with ``kept_faces``.
        model: The model being imported, for its texture name list.
        kept_faces: Faces that became polygons, in polygon order.
        search_dirs: Directories to search for texture images.
        tex_report: Texture name to whether an image was found; updated here.
        material_cache: Shared texture-name to material cache.
        config: Project configuration supplying the image extension order.
        notices: User-facing messages, passed on to material adoption.
    """
    tex_set = {
        f.texnum for f in kept_faces
        if f.textured and 0 <= f.texnum < len(model.textures)
    }
    mat_slots: dict[int, int] = {}     # texnum -> material slot index
    name_to_slot: dict[str, int] = {}  # texture name -> slot (dedupe per object)
    for tex_idx in sorted(tex_set):
        tex_name = model.textures[tex_idx]
        if tex_name not in name_to_slot:
            mat = _get_or_create_material(
                tex_name, search_dirs, tex_report, material_cache, config,
                notices,
            )
            obj.data.materials.append(mat)
            name_to_slot[tex_name] = len(obj.data.materials) - 1
        mat_slots[tex_idx] = name_to_slot[tex_name]

    # Assign material indices to faces (aligned with kept_faces / mesh.polygons).
    assigned = 0
    for poly, pof_face in zip(mesh.polygons, kept_faces):
        if pof_face.textured and pof_face.texnum in mat_slots:
            poly.material_index = mat_slots[pof_face.texnum]
            assigned += 1
    if kept_faces:
        log.info("  Assigned material to %d/%d faces", assigned, len(kept_faces))


#: bpy types this module contributes to add-on registration.
classes = (ImportPOF,)
