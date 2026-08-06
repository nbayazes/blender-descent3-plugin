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
from mathutils import Vector

from . import poformat
from .config import CONFIG_FILENAME, Config, config_for_path, resolved_search_dirs
from .constants import (
    ATTACH_EMPTY_DISPLAY_SIZE,
    GUN_EMPTY_DISPLAY_SIZE,
    IMPORT_OT_IDNAME,
    POF_FILENAME_EXT,
    POF_FILTER_GLOB,
    PROP_KEY_FLAGS,
    PROP_KEY_INDEX,
    PROP_KEY_MOVEMENT_AXIS,
    PROP_KEY_MOVEMENT_TYPE,
    PROP_KEY_PARENT,
    PROP_KEY_PROPERTIES,
)
from .mathutil import descent_to_blender
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
            material_cache, config,
        )
        if obj:
            obj_by_index[sm.index] = obj

    # Set up parent-child relationships
    log.info("Setting up parent-child hierarchy")
    for sm in model.submodels:
        child = obj_by_index.get(sm.index)
        parent = obj_by_index.get(sm.parent)  # sm.parent == NO_PARENT -> None
        if child is not None and parent is not None:
            child.parent = parent
            child.matrix_parent_inverse = parent.matrix_world.inverted()
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
        points: Objects exposing ``point`` and ``parent``, i.e. gun banks or
            attach points.
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
        obj.select_set(False)
    imported_objs = list(obj_by_index.values())
    for obj in imported_objs:
        obj.select_set(True)
    root = obj_by_index.get(poformat.ROOT_SUBMODEL_INDEX)
    if root is None and imported_objs:
        root = imported_objs[0]
    if root is not None:
        context.view_layer.objects.active = root


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
            shown = ", ".join(missing[:MAX_MISSING_TEXTURES_SHOWN]) + (
                " ..." if len(missing) > MAX_MISSING_TEXTURES_SHOWN else ""
            )
            summary += f" (missing: {shown})"
        log.info(summary)
        print(f"{CONSOLE_PREFIX} {summary}")

    if not operator:
        return

    msg = f"Imported {model_name}: {len(model.submodels)} submodels"
    if total_count:
        msg += f", {found_count}/{total_count} textures"
    if missing:
        shown = ", ".join(missing[:MAX_MISSING_TEXTURES_SHOWN])
        if len(missing) > MAX_MISSING_TEXTURES_SHOWN:
            shown += " ..."
        operator.report({"WARNING"}, f"{msg} — missing textures: {shown}")
    else:
        operator.report({"INFO"}, msg)


def _get_or_create_material(
    name: str,
    search_dirs: list[str],
    tex_report: dict[str, bool],
    material_cache: dict[str, bpy.types.Material],
    config: Config,
) -> bpy.types.Material:
    """Return a material for a texture name, creating it at most once.

    Args:
        name: Texture name from the model's TXTR chunk, used as the material
            name so export can map it back to a texture index.
        search_dirs: Directories to search for the image, in priority order.
        tex_report: Texture name to whether an image was found. Updated here;
            the first success sticks, so a texture used by several submodels is
            not downgraded to "missing" by a later lookup.
        material_cache: Texture name to material, so submodels sharing a texture
            reuse one material rather than creating a duplicate each.
        config: Project configuration supplying the image extension order.

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
        _adopt_material(existing, name, search_dirs, tex_report, config)
        material_cache[name] = existing
        return existing

    mat = bpy.data.materials.new(name=name)
    # Blender uniquifies datablock names, so a clash here would have produced
    # "<name>.001" -- which export would then write to the file as a texture ID
    # matching nothing on disk. The lookup above is what prevents that, so if it
    # still happens something is wrong and the round trip is already broken.
    if mat.name != name:
        log.error(
            "Material '%s' was created as '%s'; export would write the wrong "
            "texture ID. An existing material of that name should have been "
            "reused instead.", name, mat.name,
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
) -> None:
    """Reuse a material that already exists in the scene under this texture name.

    Reusing rather than creating is what keeps exactly one material per texture
    ID. The material may come from an earlier import or from the user; either
    way its name *is* the texture ID, so it is the right material for these
    faces. Creating a second one would make Blender name it ``<name>.001``, and
    export would write that to the file as a texture that does not exist.

    An adopted material carrying no image gets one attached, which modifies a
    datablock this import did not create -- so that is warned about rather than
    done quietly.

    Args:
        mat: The existing material.
        name: Texture name it stands for.
        search_dirs: Directories to search for the image.
        tex_report: Texture name to whether the texture resolved; updated here.
        config: Project configuration supplying the image extension order.
    """
    if _has_image(mat):
        log.info("Reusing existing material '%s' (already textured)", name)
        tex_report[name] = True
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
        log.warning(
            "Existing material '%s' did not use nodes; enabling them so the "
            "texture can be attached. This modifies a material that this "
            "import did not create.", name,
        )
        mat.use_nodes = True

    bsdf = _find_principled(mat)
    if bsdf is None:
        log.warning(
            "Existing material '%s' has no Principled BSDF, so '%s' was not "
            "attached. The material is reused unmodified.",
            name, os.path.basename(image_path),
        )
        return

    log.warning(
        "Existing material '%s' had no image texture; attaching '%s'. This "
        "modifies a material that this import did not create.",
        name, os.path.basename(image_path),
    )
    _attach_image(mat, bsdf, image_path, name)


def _import_submodel(
    context: bpy.types.Context,
    model: POFModel,
    sm: Submodel,
    collection: bpy.types.Collection,
    search_dirs: list[str],
    tex_report: dict[str, bool],
    material_cache: dict[str, bpy.types.Material],
    config: Config,
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

    Returns:
        The new object. A submodel with no geometry becomes an empty rather than
        an empty mesh, so it can still act as a parent in the hierarchy.
    """
    fallback_name = f"{config.naming.submodel_fallback_prefix}{sm.index}"

    if not sm.vertices:
        obj = bpy.data.objects.new(sm.name or fallback_name, None)
        obj.location = Vector(descent_to_blender(sm.offset).as_tuple())
        collection.objects.link(obj)
        return obj

    mesh = bpy.data.meshes.new(sm.name or fallback_name)

    verts = [
        Vector(descent_to_blender(v.position).as_tuple()) for v in sm.vertices
    ]

    # Only faces with 3+ vertices become Blender polygons. Keep this filtered
    # list so UVs and material indices stay aligned with mesh.polygons even when
    # some source faces are dropped.
    kept_faces = [f for f in sm.faces if len(f.vertices) >= MIN_FACE_VERTICES]
    face_indices = [tuple(fv.index for fv in f.vertices) for f in kept_faces]

    mesh.from_pydata(verts, [], face_indices)
    mesh.update()

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
                "using Blender's computed normals", sm.name, e
            )

    obj = bpy.data.objects.new(sm.name or fallback_name, mesh)
    obj.location = Vector(descent_to_blender(sm.offset).as_tuple())
    collection.objects.link(obj)

    _assign_materials(
        obj, mesh, model, kept_faces, search_dirs, tex_report, material_cache,
        config,
    )

    # Store POF-specific properties as custom properties
    obj[PROP_KEY_INDEX] = sm.index
    obj[PROP_KEY_PARENT] = sm.parent
    obj[PROP_KEY_FLAGS] = sm.flags
    obj[PROP_KEY_MOVEMENT_TYPE] = sm.movement_type
    obj[PROP_KEY_MOVEMENT_AXIS] = sm.movement_axis
    if sm.props:
        obj[PROP_KEY_PROPERTIES] = sm.props

    return obj


def _assign_materials(
    obj: bpy.types.Object,
    mesh: bpy.types.Mesh,
    model: POFModel,
    kept_faces: list[ModelFace],
    search_dirs: list[str],
    tex_report: dict[str, bool],
    material_cache: dict[str, bpy.types.Material],
    config: Config,
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
                tex_name, search_dirs, tex_report, material_cache, config
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
