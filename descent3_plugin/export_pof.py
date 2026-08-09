"""Export side of the add-on: Blender objects to a POF/OOF file.

Holds the :class:`ExportPOF` operator and the conversion into a
:class:`~descent3_plugin.poformat.POFModel`. The binary writing lives in
:mod:`descent3_plugin.poformat`, which has no ``bpy`` dependency; everything
here runs only inside Blender.
"""

import logging
import os
from dataclasses import dataclass

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Vector

from . import poformat
from .config import CONFIG_FILENAME, Config, config_for_path
from .mathutil import NORMALIZE_EPSILON, Vector3, blender_to_descent
from .naming import (
    TEXTURE_FORMAT_NONE,
    TEXTURE_FORMATS,
    is_uniquified_from,
    marker_order_key,
    rejected_texture_name,
    resolve_texture_names,
    texture_format_extension,
)
from .texexport import export_textures
from .constants import (
    EXPORT_OT_IDNAME,
    MARKER_FORWARD_AXIS,
    MARKER_UP_AXIS,
    POF_FILENAME_EXT,
    POF_FILTER_GLOB,
    PROP_KEY_HAS_UVEC,
    PROP_KEY_INDEX,
    PROP_KEY_MOVEMENT_AXIS,
    PROP_KEY_MOVEMENT_TYPE,
    PROP_KEY_PROPERTIES,
    PROP_KEY_SUBMODEL_NAME,
    PROP_KEY_TEXTURE,
)
from .poformat import (
    AttachPoint,
    Color,
    FaceVertex,
    GunBank,
    ModelFace,
    POFModel,
    Submodel,
    SubmodelVertex,
)

# Package channel, not this module's, so output keeps the single
# "[descent3_plugin]" prefix that register() configures.
log = logging.getLogger(__package__)

#: Identifier meaning "whatever the project's descent3.toml asks for". An
#: explicit dropdown entry rather than a heuristic: the config that applies is
#: the one beside the *destination*, unknown until the file browser closes, so
#: the dialog cannot pre-select the project's version.
EXPORT_VERSION_FROM_CONFIG = "CONFIG"

#: Versions offered in the export dialog, as ``(identifier, label, tooltip)``.
#: Identifiers are the ``major * 100 + minor`` version written to the file.
EXPORT_VERSION_ITEMS = [
    (EXPORT_VERSION_FROM_CONFIG, "From project config",
     "Use the version set in the project's descent3.toml, or v23.00 if there "
     "is none"),
    ("2300", "v23.00 (Latest)", "Descent 3 retail version"),
    ("2200", "v22.00", "Record layout whose ANIM/PANI chunks carry a "
     "per-submodel key count and start times -- this add-on writes no "
     "animation chunks"),
    ("2100", "v21.00", "Record layout whose faces each carry two lightmap "
     "UV-diff floats -- this add-on writes them as zeros"),
]

DEFAULT_EXPORT_VERSION = EXPORT_VERSION_FROM_CONFIG

#: Texture-format counterpart of EXPORT_VERSION_FROM_CONFIG: defer to the
#: project.
TEXTURE_FORMAT_FROM_CONFIG = "CONFIG"

#: Wording for each entry of :data:`~descent3_plugin.naming.TEXTURE_FORMATS`,
#: as ``identifier -> (label, tooltip)``. Only the wording: *which* formats
#: exist is that table's job, and a format with no entry here still appears,
#: labelled by its identifier.
TEXTURE_FORMAT_LABELS = {
    "PNG": ("PNG", "Lossless, and what the importer looks for first"),
    "TARGA": ("Targa (.tga)", "Uncompressed, closest to Descent 3's own tooling"),
}


def _texture_format_items() -> list[tuple[str, str, str]]:
    """Build the Textures dropdown from the supported-format table.

    Returns:
        ``(identifier, label, tooltip)`` triples: the two non-format choices
        first, then every key of
        :data:`~descent3_plugin.naming.TEXTURE_FORMATS` in table order. Descent
        3's native OGF is absent because Blender cannot encode it.
    """
    items = [
        (TEXTURE_FORMAT_FROM_CONFIG, "From project config",
         "Use the texture_format set in the project's descent3.toml, or write "
         "no textures if it sets none"),
        (TEXTURE_FORMAT_NONE, "None", "Write the model only, no texture images"),
    ]
    for fmt in TEXTURE_FORMATS:
        label, tooltip = TEXTURE_FORMAT_LABELS.get(
            fmt,
            (fmt, f"Write texture images as {texture_format_extension(fmt)} files"),
        )
        items.append((fmt, label, tooltip))
    return items


#: Texture-image formats offered in the export dialog. One control rather than
#: a checkbox plus a format list, since "do not write textures" and "write them
#: as PNG" are the same decision. Held in a module-level name because Blender
#: keeps no reference to the strings an ``EnumProperty`` is given.
TEXTURE_FORMAT_ITEMS = _texture_format_items()

#: Seed for the bounding-box accumulator, large enough that the first vertex
#: compared always replaces it on both the min and max side. A model with no
#: vertices never gets that comparison, so the seed is discarded rather than
#: written -- see :func:`_model_bounds`.
BOUNDS_INIT = 1e30

#: Color written for a face whose material cannot be mapped to a texture.
DEFAULT_UNTEXTURED_COLOR = (0.5, 0.5, 0.5)

#: Direction written for a marker whose transform has no orientation to read --
#: an empty scaled flat to nothing, whose axes are degenerate. Only the floor
#: under a transform that cannot be asked: markers otherwise export the
#: direction their empty faces, see :func:`_marker_axes`.
#:
#: Already expressed in Descent 3 (Y-up) space, because it goes straight into
#: the file -- it must NOT be passed through :func:`blender_to_descent`.
DEFAULT_POINT_NORMAL = (0.0, 0.0, 1.0)


class ExportPOF(bpy.types.Operator, ExportHelper):
    """Export a Descent 3 POF/OOF model file"""

    bl_idname = EXPORT_OT_IDNAME
    bl_label = "Export Descent 3 POF"
    bl_description = (
        "Export every mesh in the file as a Descent 3 polygon model (.pof), "
        "or only the selected objects when Selected Only is on"
    )
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = POF_FILENAME_EXT
    filter_glob: StringProperty(default=POF_FILTER_GLOB, options={"HIDDEN"})

    export_version: EnumProperty(
        name="POF Version",
        items=EXPORT_VERSION_ITEMS,
        default=DEFAULT_EXPORT_VERSION,
    )
    export_selected: BoolProperty(
        name="Selected Only",
        description="Export only selected objects",
        default=False,
    )
    export_guns: BoolProperty(
        name="Export Gun Points",
        description="Export gun point empty objects",
        default=True,
    )
    export_attach: BoolProperty(
        name="Export Attach Points",
        description="Export attach point empty objects",
        default=True,
    )
    texture_format: EnumProperty(
        name="Textures",
        description=(
            "Write each material's image alongside the model, so it can be "
            "handed over complete. Files are named after the texture IDs in "
            "the model, which is what the importer searches for"
        ),
        items=TEXTURE_FORMAT_ITEMS,
        default=TEXTURE_FORMAT_FROM_CONFIG,
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Run the export and report the outcome to Blender."""
        return save_pof(context, self.filepath, self)

    def draw(self, context: bpy.types.Context) -> None:
        """Lay out the operator's options in the file browser sidebar."""
        layout = self.layout
        layout.prop(self, "export_version")
        layout.prop(self, "export_selected")
        layout.prop(self, "export_guns")
        layout.prop(self, "export_attach")
        layout.separator()
        layout.prop(self, "texture_format")


def save_pof(
    context: bpy.types.Context,
    filepath: str,
    operator: ExportPOF | None = None,
) -> set[str]:
    """Export a scene's submodel objects to a POF file.

    Args:
        operator: Carries the export options and receives user-facing reports.
            ``None`` stays silent, exports everything :func:`_is_submodel`
            accepts at :data:`DEFAULT_EXPORT_VERSION`, and writes no gun or
            attach points -- it does not fall back to the operator's defaults,
            which enable both.

    Returns:
        ``{"FINISHED"}``, or ``{"CANCELLED"}`` if there was nothing to export or
        the file could not be written.
    """
    config, config_warnings = config_for_path(filepath)
    for warning in config_warnings:
        log.warning("%s: %s", config.source_path or CONFIG_FILENAME, warning)
        if operator:
            operator.report({"WARNING"}, f"config: {warning}")

    candidates = (
        context.selected_objects
        if operator and operator.export_selected
        else bpy.data.objects
    )
    objects = [obj for obj in candidates if _is_submodel(obj)]

    if not objects:
        log.warning("Nothing to export: no mesh objects%s",
                    " selected" if operator and operator.export_selected else "")
        if operator:
            # "mesh objects", though :func:`_is_submodel` also takes empties
            # standing in for geometry-less submodels: a file of only those is a
            # hierarchy with nothing in it, so naming them here would mislead.
            operator.report({"WARNING"}, "No mesh objects to export")
        return {"CANCELLED"}

    model, material_textures, warnings = _build_pof_model(
        context, objects, operator, config
    )
    # Reported before the write, because they describe what is about to go into
    # the file -- a merged material, or an ID no image can be named after.
    for warning in warnings:
        if operator:
            operator.report({"WARNING"}, warning)

    try:
        data = poformat.write_pof(model)
        with open(filepath, "wb") as f:
            f.write(data)
    except Exception as e:
        log.error("Failed to write %s: %s", filepath, e)
        if operator:
            operator.report({"ERROR"}, f"Failed to write POF: {e}")
        return {"CANCELLED"}

    summary = f"Exported {len(model.submodels)} submodels"

    fmt = _resolve_texture_format(operator, config)
    if fmt != TEXTURE_FORMAT_NONE:
        written, problems = _write_textures(
            context, filepath, objects, material_textures, fmt, config
        )
        summary += f", {len(written)} textures"
        for problem in problems:
            log.warning("Texture export: %s", problem)
            if operator:
                operator.report({"WARNING"}, f"texture: {problem}")

    if operator:
        operator.report({"INFO"}, summary)

    return {"FINISHED"}


def _is_submodel(obj: bpy.types.Object) -> bool:
    """Return whether an object should be written to the file as a submodel.

    Returns:
        True for meshes always; for empties only when they carry
        :data:`~descent3_plugin.constants.PROP_KEY_INDEX` -- see
        ``docs/custom-properties.md``, "pof_index -- the submodel's own index".
    """
    if obj.type == "MESH":
        return True
    return obj.type == "EMPTY" and PROP_KEY_INDEX in obj


def _resolve_texture_format(operator: ExportPOF | None, config: Config) -> str:
    """Return the texture format to write.

    Args:
        config: Project configuration resolved from the destination.

    Returns:
        A key of :data:`~descent3_plugin.naming.TEXTURE_FORMATS`, or
        :data:`~descent3_plugin.naming.TEXTURE_FORMAT_NONE`. Without an operator
        the project decides, so scripted exports and the dialog default agree.
    """
    if operator is None:
        return config.export.texture_format
    # getattr, not attribute access: save_pof is also called with duck-typed
    # stand-ins from the headless scripts, which carry no such property.
    # Defaulting to the config keeps callers predating this option unchanged.
    selection = getattr(operator, "texture_format", TEXTURE_FORMAT_FROM_CONFIG)
    if selection == TEXTURE_FORMAT_FROM_CONFIG:
        return config.export.texture_format
    return selection


def _write_textures(
    context: bpy.types.Context,
    filepath: str,
    objects: list[bpy.types.Object],
    material_textures: dict[str, str],
    fmt: str,
    config: Config,
) -> tuple[list[str], list[str]]:
    """Write the images for every texture the model references.

    The model is not consulted: its texture list was built from these same
    objects' materials through this same mapping, so walking the materials
    covers exactly the names that were written to the file.

    Args:
        filepath: Path of the model just written; textures go in a subfolder
            beside it.
        material_textures: Material name to the texture it exports as.
        config: Project configuration supplying the subfolder name.

    Returns:
        A ``(written, problems)`` pair from
        :func:`~descent3_plugin.texexport.export_textures`.
    """
    # One material per texture ID: several can map onto the same texture once
    # duplicate suffixes are resolved, and they mean the same image. Evaluated
    # objects, matching _collect_textures, so a slot a modifier added gets its
    # image written rather than named in the file and then skipped.
    depsgraph = context.evaluated_depsgraph_get()
    by_texture: dict[str, bpy.types.Material] = {}
    for obj in objects:
        for mat in _slot_materials(obj.evaluated_get(depsgraph)):
            texture = material_textures.get(mat.name)
            if texture and texture not in by_texture:
                by_texture[texture] = mat

    directory = os.path.join(
        os.path.dirname(os.path.abspath(filepath)), config.textures.export_dir
    )
    written, problems = export_textures(
        by_texture, directory, fmt, context.scene
    )
    if written:
        log.info("Wrote %d texture(s) to %s", len(written), directory)
    return written, problems


def _build_pof_model(
    context: bpy.types.Context,
    objects: list[bpy.types.Object],
    operator: ExportPOF | None = None,
    config: Config | None = None,
) -> tuple[POFModel, dict[str, str], list[str]]:
    """Build a :class:`POFModel` from Blender objects.

    Args:
        context: Supplies the evaluated dependency graph every world matrix and
            modifier result is read from, and the selection that limits which
            markers belong to this export.
        objects: Objects to export, in the order they become submodels, already
            filtered by :func:`_is_submodel`.
        operator: Export options, or ``None`` for defaults.
        config: Project configuration. ``None`` resolves to the built-in
            defaults.

    Returns:
        A ``(model, material_textures, warnings)`` triple. The model is ready
        for :func:`descent3_plugin.poformat.write_pof`, hierarchy already built;
        the mapping (material name to exported texture) is handed back rather
        than recomputed so texture export cannot disagree with the file; the
        warnings are for the caller to report through the operator.
    """
    if config is None:
        config = Config()
    # Asking for the depsgraph also evaluates anything left tagged, so
    # ``matrix_world`` below is current: a script that moves an object and
    # exports in the same run would otherwise write the old position.
    depsgraph = context.evaluated_depsgraph_get()
    version = _resolve_version(operator, config)
    major = version // poformat.VERSION_MAJOR_SCALE
    features = poformat.features_for(version, major)

    model = POFModel(version=version, major_version=major)
    if features.lightmap_uv:
        model.flags |= poformat.PMF_LIGHTMAP_RES
    if features.timed_anim:
        model.flags |= poformat.PMF_TIMED

    # Submodel index is the object's position in this list, so parents can be
    # resolved by name before every submodel has been built.
    obj_to_index = {obj.name: i for i, obj in enumerate(objects)}

    model.textures, material_textures, warnings = _collect_textures(
        objects, depsgraph
    )
    tex_index = {name: i for i, name in enumerate(model.textures)}

    for obj in objects:
        sm = _build_submodel(
            obj, depsgraph, obj_to_index, tex_index, material_textures
        )
        model.submodels.append(sm)

    # Last, and from the finished submodels: where a submodel's vertices sit is
    # not known until its offset -- and every offset above it -- exists.
    model.min_bound, model.max_bound, model.radius = _model_bounds(model.submodels)

    # Markers are matched by name across the whole file, so the export set has
    # to bound them too.
    selected = None
    if operator and operator.export_selected:
        selected = {obj.name for obj in context.selected_objects}

    # Resolved once: both marker passes measure from it and must agree.
    unparented = _unparented_marker_frame(objects, obj_to_index, depsgraph)

    if operator and operator.export_guns:
        model.gun_banks = [
            GunBank(
                parent=marker.parent,
                point=marker.position,
                normal=marker.direction,
            )
            for marker in _collect_points(
                config.naming.gun_prefix, obj_to_index, depsgraph, selected,
                unparented,
            )
        ]

    if operator and operator.export_attach:
        model.attach_points = [
            AttachPoint(
                parent=marker.parent,
                point=marker.position,
                normal=marker.direction,
                # Only when the source file specified one: an empty always has a
                # roll, and inventing one for every attach point would write a
                # NATH chunk the model never had. See PROP_KEY_HAS_UVEC.
                has_uvec=marker.has_up,
                uvec=marker.up,
            )
            for marker in _collect_points(
                config.naming.attach_prefix, obj_to_index, depsgraph, selected,
                unparented,
            )
        ]

    model.build_hierarchy()

    return model, material_textures, warnings


def _resolve_version(operator: ExportPOF | None, config: Config) -> int:
    """Return the POF version to write.

    Args:
        config: Project configuration resolved from the destination path.

    Returns:
        The project's configured version unless the dialog names a specific one.
        Without an operator -- headless and scripted exports -- the project's
        version always applies, so scripts and the UI default agree.
    """
    if operator is None:
        return config.export.version
    selection = getattr(operator, "export_version", EXPORT_VERSION_FROM_CONFIG)
    if selection == EXPORT_VERSION_FROM_CONFIG:
        return config.export.version
    return int(selection)


def _collect_textures(
    objects: list[bpy.types.Object],
    depsgraph: bpy.types.Depsgraph,
) -> tuple[list[str], dict[str, str], list[str]]:
    """Collect the texture names used by ``objects``.

    A material's recorded :data:`~descent3_plugin.constants.PROP_KEY_TEXTURE`
    wins over its name; the rest fall back to the ``.NNN`` heuristic in
    :mod:`~descent3_plugin.naming` -- see ``docs/exporting.md``, "Materials and
    texture names".

    Args:
        depsgraph: Evaluated dependency graph. Slots are read off the
            *evaluated* objects because that is where the faces come from: a
            Boolean set to transfer materials, or a Geometry Nodes Set Material,
            adds slots that exist only after evaluation, and reading the
            originals left those faces exported flat grey with their texture
            missing from the model.

    Returns:
        A ``(textures, mapping, warnings)`` triple. ``textures`` is sorted so
        the same scene always produces the same texture-index assignment.
        ``mapping`` takes a material name to the texture it exports as, and the
        per-face lookup must use it too or the list and the faces disagree.
        ``warnings`` are handed back rather than only logged, because they
        change what the exported model says.
    """
    material_names = set()
    explicit: dict[str, str] = {}
    for obj in objects:
        for mat in _slot_materials(obj.evaluated_get(depsgraph)):
            material_names.add(mat.name)
            if PROP_KEY_TEXTURE in mat:
                # str(): a custom property the UI lets a user type a number
                # into, and this goes straight into the TXTR chunk as a name.
                explicit[mat.name] = str(mat[PROP_KEY_TEXTURE])

    mapping, warnings = resolve_texture_names(material_names, explicit)
    textures = sorted(set(mapping.values()))

    # A texture ID is also the filename holding it, so an ID that cannot be a
    # filename leaves the model referencing a bitmap nobody can write or find.
    # Warned here rather than in texture export, which only runs when the user
    # asked for images while the ID reaches the TXTR chunk either way.
    for texture in textures:
        problem = rejected_texture_name(texture)
        if problem:
            warnings.append(
                f"The model references texture '{texture}', which cannot be a "
                f"filename: {problem}. No image can be written for it or found "
                f"for it on import. Rename the material, or set its "
                f"'{PROP_KEY_TEXTURE}' custom property to a plain name."
            )

    for warning in warnings:
        log.warning("%s", warning)

    return textures, mapping, warnings


def _slot_materials(obj: bpy.types.Object) -> list[bpy.types.Material]:
    """Return the materials an object's slots actually resolve to.

    ``obj.material_slots``, never ``obj.data.materials``: a slot's link can be
    set to OBJECT, which moves the material onto the object and leaves the
    mesh's own list holding ``None`` for that slot, so reading the mesh sees no
    materials and writes every face flat grey for a model the viewport shows
    fully textured. The slot honours both link modes.

    Args:
        obj: Object whose slots are read. Callers pass the *evaluated* object,
            since a modifier can add slots the original does not have and the
            faces being exported are indexed against the evaluated list.

    Returns:
        One material per filled slot, in slot order. Empty slots are skipped;
        faces pointing at them export untextured.
    """
    return [slot.material for slot in obj.material_slots if slot.material]


def _build_submodel(
    obj: bpy.types.Object,
    depsgraph: bpy.types.Depsgraph,
    obj_to_index: dict[str, int],
    tex_index: dict[str, int],
    material_textures: dict[str, str],
) -> Submodel:
    """Convert one Blender object into a :class:`Submodel`.

    Written from the object as the viewport draws it: the depsgraph's evaluated
    copy, so modifiers are applied, and every vertex through ``matrix_world``,
    so rotation and scale are baked in. Reading ``obj.data`` straight drops all
    three -- a wing rotated into place exported lying flat, a mirror modifier
    exported as half a ship -- and none of it shows in Blender, only in the
    game. Positions are then re-centred on the object's own origin, because a
    submodel stores vertices relative to itself and the origin travels
    separately in ``offset``.

    An object with no geometry is converted too: a vertex-less submodel is how
    Descent 3 spells a joint, so everything but the geometry half -- offset,
    movement type, ``$rotate=`` -- is set exactly as for a mesh.

    Args:
        obj: A mesh, or an empty standing in for a submodel that never had
            geometry.
        obj_to_index: Object name to submodel index, for resolving the parent.
        tex_index: Texture name to index into the model's texture list.
        material_textures: Material name to the texture it exports as.

    Returns:
        The submodel, with its radius computed. The *model's* bounds are not
        grown here: they need each offset summed down the hierarchy, which
        nothing knows until every submodel exists -- see :func:`_model_bounds`.
    """
    sm = Submodel()
    sm.index = obj_to_index[obj.name]
    sm.name = _submodel_name(obj)

    if obj.parent and obj.parent.name in obj_to_index:
        sm.parent = obj_to_index[obj.parent.name]
    else:
        sm.parent = poformat.NO_PARENT

    sm.movement_type = int(
        obj.get(PROP_KEY_MOVEMENT_TYPE, poformat.MOVEMENT_TYPE_NONE)
    )
    sm.movement_axis = int(obj.get(PROP_KEY_MOVEMENT_AXIS, 0))
    sm.props = str(obj.get(PROP_KEY_PROPERTIES, ""))
    # No ``sm.flags``, deliberately: the SOBJ record has no flag word. The
    # engine derives every SOF_ bit from the property string
    # (reference/polymodel.cpp, SetPolymodelProperties), as does this add-on's
    # reader, so writing ``props`` verbatim above is what carries the flags
    # across; ``sm.flags`` is a field write_pof_stream never serializes.

    eval_obj = obj.evaluated_get(depsgraph)
    world = eval_obj.matrix_world
    origin = world.translation

    # A world-space delta, not ``matrix_local.translation``: the engine sums
    # offsets as plain vector adds and never rotates a child's offset by its
    # parent (reference/polymodel.cpp, MinMaxSubmodel), and matrix_local stops
    # reporting that distance the moment a parent is rotated or scaled.
    sm.offset = blender_to_descent(
        origin - _parent_origin(obj, obj_to_index, depsgraph)
    )

    # Below here is geometry, and a joint has none: offset, movement and
    # properties are already set, and zero is the right radius for no vertices.
    if obj.type != "MESH":
        return sm

    # Inverse transpose, not the matrix itself: under a non-uniform scale the
    # matrix skews a normal off the surface it is perpendicular to, and the
    # model lights wrong wherever something was stretched to fit.
    normal_matrix = world.to_3x3().inverted_safe().transposed()

    # A negative determinant means the transform mirrors -- a left wing built by
    # duplicating the right and scaling it -1 on X. The inverse transpose turns
    # the normals round but nothing turns the *winding* round, so the corners
    # are reversed below or the part renders inside out. This is the object's
    # own matrix, not the Y-up/Z-up conversion, which is a rotation and never
    # mirrors anything (see :mod:`.mathutil`).
    mirrored = world.to_3x3().determinant() < 0

    mesh = eval_obj.to_mesh()
    try:
        for v, normal in zip(mesh.vertices, _vertex_normals(mesh)):
            sv = SubmodelVertex()
            sv.position = blender_to_descent((world @ v.co) - origin)
            sv.normal = _transform_normal(normal, normal_matrix)
            sv.alpha = poformat.ALPHA_OPAQUE
            sm.vertices.append(sv)

        uv_layer = mesh.uv_layers.active
        for poly in mesh.polygons:
            sm.faces.append(
                _build_face(
                    poly, mesh, eval_obj, tex_index, material_textures, uv_layer,
                    normal_matrix, mirrored,
                )
            )
    finally:
        # The mesh to_mesh() returns is owned by the evaluated object and only
        # valid until released; in a ``finally`` so an error part-way through
        # conversion does not leak it for the rest of the session.
        eval_obj.to_mesh_clear()

    for v in sm.vertices:
        dist = v.position.magnitude()
        if dist > sm.radius:
            sm.radius = dist

    return sm


def _submodel_name(obj: bpy.types.Object) -> str:
    """Return the name to write into the submodel's SOBJ record.

    Returns:
        The name recorded in
        :data:`~descent3_plugin.constants.PROP_KEY_SUBMODEL_NAME` when the
        object's current name is that name, suffix or not; otherwise the
        object's own name. See ``docs/custom-properties.md``, "pof_name -- the
        name the submodel had in the file".
    """
    recorded = obj.get(PROP_KEY_SUBMODEL_NAME)
    if recorded is None:
        return obj.name
    # str(): a custom property the UI will let a user type anything into, and
    # whatever is here goes straight into the SOBJ record as a name.
    recorded = str(recorded)
    if is_uniquified_from(obj.name, recorded):
        return recorded
    return obj.name


def _parent_origin(
    obj: bpy.types.Object,
    obj_to_index: dict[str, int],
    depsgraph: bpy.types.Depsgraph,
) -> Vector:
    """Return the world-space point an object's offset is measured from.

    Args:
        obj_to_index: Object name to submodel index, which is exactly the set of
            objects being exported.

    Returns:
        The parent's world-space origin when the parent is itself being
        exported, else the world origin: such a submodel is written with
        :data:`~descent3_plugin.poformat.NO_PARENT`, so the engine sums its
        offsets from the model origin and the world origin is the matching
        frame. A marker cannot say NO_PARENT and so cannot use this fallback --
        see :func:`_unparented_marker_frame`.
    """
    parent = obj.parent
    if parent is not None and parent.name in obj_to_index:
        return parent.evaluated_get(depsgraph).matrix_world.translation
    return Vector((0.0, 0.0, 0.0))


def _vertex_normals(mesh: bpy.types.Mesh) -> list[Vector]:
    """Return one normal per vertex, in the mesh's own space.

    Import puts the file's normals into Blender's custom split normals (the
    ``CD_CUSTOMLOOPNORMAL`` layer), which is the only place they survive:
    ``MeshVertex.normal`` is *computed* from the surrounding faces, so reading
    it back threw the authored normals away on every round trip. Custom normals
    are per *loop*, so each vertex takes the normal of the first loop touching
    it -- exact for anything this add-on imported, which wrote one normal to
    every loop of a vertex. First rather than an average: on hand-edited custom
    normals that keeps a value the artist actually authored, where an average
    would match none of them.

    Args:
        mesh: Mesh to read, normally an evaluated copy from ``to_mesh()``.

    Returns:
        A normal per vertex, in ``mesh.vertices`` order. Falls back to the
        computed vertex normals for a mesh carrying no custom split normals,
        which is the better answer for flat shading since the engine has
        nowhere to put a per-face normal. Both attributes are looked up rather
        than assumed, so a Blender without ``corner_normals`` takes that path.
    """
    normals = [v.normal.copy() for v in mesh.vertices]
    if not getattr(mesh, "has_custom_normals", False):
        return normals
    corner_normals = getattr(mesh, "corner_normals", None)
    if not corner_normals:
        return normals

    claimed = [False] * len(normals)
    for loop, corner in zip(mesh.loops, corner_normals):
        index = loop.vertex_index
        if not claimed[index]:
            normals[index] = Vector(corner.vector)
            claimed[index] = True
    return normals


def _transform_normal(normal, normal_matrix) -> Vector3:
    """Convert a normal from Blender's space to Descent 3's.

    Args:
        normal: Direction in the mesh's own space.
        normal_matrix: 3x3 inverse transpose of the object's world matrix.

    Returns:
        The same direction in file coordinates, unit length. The inverse
        transpose does not preserve length and the engine treats these as unit
        vectors when it lights a face, so re-normalizing is not optional.
    """
    return blender_to_descent((normal_matrix @ normal).normalized())


def _grow_bounds(min_bound: Vector3, max_bound: Vector3, co) -> None:
    """Expand the running bounding box to include ``co``.

    Args:
        min_bound: Minimum corner, updated in place.
        max_bound: Maximum corner, updated in place.
        co: A point in Descent 3 coordinates -- for the model box, a vertex
            position with its submodel's accumulated offset already added, since
            that is where the vertex actually ends up.
    """
    if co.x < min_bound.x:
        min_bound.x = co.x
    if co.y < min_bound.y:
        min_bound.y = co.y
    if co.z < min_bound.z:
        min_bound.z = co.z
    if co.x > max_bound.x:
        max_bound.x = co.x
    if co.y > max_bound.y:
        max_bound.y = co.y
    if co.z > max_bound.z:
        max_bound.z = co.z


def _build_face(
    poly: bpy.types.MeshPolygon,
    mesh: bpy.types.Mesh,
    eval_obj: bpy.types.Object,
    tex_index: dict[str, int],
    material_textures: dict[str, str],
    uv_layer,
    normal_matrix,
    mirrored: bool,
) -> ModelFace:
    """Convert one Blender polygon into a :class:`ModelFace`.

    Args:
        eval_obj: *Evaluated* object the mesh belongs to, because
            ``poly.material_index`` indexes the evaluated slot list: a modifier
            can add a slot, and that index on the original finds the wrong
            material or none at all.
        tex_index: Texture name to index into the model's texture list.
        material_textures: Material name to the texture it exports as, from
            :func:`_collect_textures`. Resolving the rule here instead would let
            the faces and the texture list disagree.
        uv_layer: Active UV layer, or ``None`` if the mesh has no UVs.
        normal_matrix: 3x3 inverse transpose of the object's world matrix; the
            face normal takes the same transform as the vertex normals, or a
            rotated object exports faces pointing where they pointed before.
        mirrored: Whether the object's world matrix reverses orientation, in
            which case the corners are written in reverse order so the winding
            still agrees with the normal.

    Returns:
        The face, textured when its material maps to a known texture and flat
        :data:`DEFAULT_UNTEXTURED_COLOR` otherwise.
    """
    face = ModelFace()
    face.normal = _transform_normal(poly.normal, normal_matrix)

    mat = None
    if 0 <= poly.material_index < len(eval_obj.material_slots):
        # The slot, not ``obj.data.materials`` -- see :func:`_slot_materials`.
        mat = eval_obj.material_slots[poly.material_index].material
    texture = material_textures.get(mat.name) if mat else None
    if texture is not None and texture in tex_index:
        face.textured = True
        face.texnum = tex_index[texture]
    else:
        face.textured = False
        face.color = Color(*DEFAULT_UNTEXTURED_COLOR)

    # The loop index carries the UV as well as the vertex, so reversing the
    # sequence keeps every corner with its own texture coordinate.
    loop_indices = (
        reversed(poly.loop_indices) if mirrored else poly.loop_indices
    )
    for loop_idx in loop_indices:
        loop = mesh.loops[loop_idx]
        fv = FaceVertex()
        fv.index = loop.vertex_index
        if uv_layer:
            uv = uv_layer.data[loop_idx].uv
            fv.u = uv.x
            # Descent 3's UV origin is top-left, Blender's bottom-left; import
            # negates V, so export must negate it back.
            fv.v = -uv.y
        else:
            fv.u = 0.0
            fv.v = 0.0
        face.vertices.append(fv)

    return face


def _bounding_radius(min_bound: Vector3, max_bound: Vector3) -> float:
    """Return the radius of a sphere at the origin enclosing the bounding box.

    Returns:
        The distance from the origin to the furthest box corner. All eight are
        measured because the box is not centered on the origin, so the furthest
        is not always ``max_bound``.
    """
    corners = [
        min_bound,
        max_bound,
        Vector3(min_bound.x, max_bound.y, max_bound.z),
        Vector3(max_bound.x, min_bound.y, max_bound.z),
        Vector3(max_bound.x, max_bound.y, min_bound.z),
        Vector3(min_bound.x, min_bound.y, max_bound.z),
        Vector3(min_bound.x, max_bound.y, min_bound.z),
        Vector3(max_bound.x, min_bound.y, min_bound.z),
    ]
    return max((corner.magnitude() for corner in corners), default=0.0)


def _model_bounds(submodels: list[Submodel]) -> tuple[Vector3, Vector3, float]:
    """Return the model's bounding box and radius, in file coordinates.

    Vertices are stored relative to each submodel's own origin, so a box grown
    straight from them comes out the size of the largest single submodel. The
    engine adds each submodel's offset summed down the parent chain
    (reference/polymodel.cpp, MinMaxSubmodel, called for every root by
    FindMinMaxForModel); this does the same per vertex. An undersized box is not
    cosmetic -- the engine culls and range-checks against it, so a wing pops out
    of view and shots pass through anything sticking out.

    Args:
        submodels: Every submodel of the model, offsets already set.

    Returns:
        A ``(min_bound, max_bound, radius)`` triple in Descent 3 coordinates,
        since it goes into the file as-is. A model with no vertices gets zeros
        rather than the unreplaced :data:`BOUNDS_INIT` seeds, which would reach
        the file as a box turned inside out.
    """
    offsets = _accumulated_offsets(submodels)
    min_bound = Vector3(BOUNDS_INIT, BOUNDS_INIT, BOUNDS_INIT)
    max_bound = Vector3(-BOUNDS_INIT, -BOUNDS_INIT, -BOUNDS_INIT)
    grown = False
    for sm in submodels:
        offset = offsets[sm.index]
        grown = grown or bool(sm.vertices)
        for v in sm.vertices:
            _grow_bounds(min_bound, max_bound, v.position + offset)

    if not grown:
        return Vector3(0, 0, 0), Vector3(0, 0, 0), 0.0
    return min_bound, max_bound, _bounding_radius(min_bound, max_bound)


def _accumulated_offsets(submodels: list[Submodel]) -> dict[int, Vector3]:
    """Return each submodel's offset summed down its chain of parents.

    Every step is a plain vector add, never rotating a child's offset by its
    parent, matching the engine (reference/polymodel.cpp, MinMaxSubmodel,
    ``offset += sm->offset``) -- which is why export can write world-space
    deltas as offsets at all.

    Args:
        submodels: Every submodel of the model, keyed by ``index`` for the
            parent lookups.

    Returns:
        Submodel index to the offset of its origin from the model's own. A
        parent index naming no submodel ends the chain, which is how
        :data:`~descent3_plugin.poformat.NO_PARENT` terminates it.
    """
    by_index = {sm.index: sm for sm in submodels}
    accumulated: dict[int, Vector3] = {}
    for sm in submodels:
        total = Vector3(0.0, 0.0, 0.0)
        node = sm
        # Blender parenting cannot contain a cycle; the guard is for a model
        # hand-built through this API, where a bad parent index should cost a
        # wrong box rather than hang the export.
        visited: set[int] = set()
        while node is not None and node.index not in visited:
            visited.add(node.index)
            total = total + node.offset
            node = by_index.get(node.parent)
        accumulated[sm.index] = total
    return accumulated


def _marker_axes(obj: bpy.types.Object) -> tuple[Vector3, Vector3]:
    """Return where a marker empty points, and which way is up for it.

    :data:`~descent3_plugin.constants.MARKER_FORWARD_AXIS` is the axis Blender
    draws a SINGLE_ARROW empty's arrow along, so the arrow in the viewport is
    the firing direction in the file. Import aims it the same way round.

    Args:
        obj: The evaluated marker empty.

    Returns:
        A ``(direction, up)`` pair in Descent 3 coordinates, falling back to
        :data:`DEFAULT_POINT_NORMAL` and the world up when the matrix carries no
        usable orientation -- an empty scaled to nothing has zero-length axes,
        and the quaternion they produce is arbitrary rather than reportably
        wrong.
    """
    # to_quaternion(), not the 3x3: a marker is a direction, so a scaled empty
    # must not export a scaled normal, and orthonormalizing here is free.
    rotation = obj.matrix_world.to_quaternion()
    direction = rotation @ Vector(MARKER_FORWARD_AXIS)
    up = rotation @ Vector(MARKER_UP_AXIS)
    if direction.length < NORMALIZE_EPSILON or up.length < NORMALIZE_EPSILON:
        return Vector3(*DEFAULT_POINT_NORMAL), blender_to_descent(
            Vector(MARKER_UP_AXIS)
        )
    return (
        blender_to_descent(direction.normalized()),
        blender_to_descent(up.normalized()),
    )


@dataclass
class _Marker:
    """One gun or attach point, resolved out of the scene.

    A record rather than a tuple because the two callers take different subsets
    -- a gun bank has no up vector -- and positional unpacking whose tail some
    callers ignore is how the wrong field ends up in the wrong slot.

    Attributes:
        position: Offset from the submodel it is attached to, in file
            coordinates.
        direction: Where the marker points, in file coordinates.
        up: Up vector in file coordinates, meaningful to an attach point and
            only when ``has_up`` says the roll was chosen rather than inherited
            from however the empty happens to sit.
        has_up: Whether this marker's roll came from the file.
        parent: Submodel index the marker attaches to.
    """

    position: Vector3
    direction: Vector3
    up: Vector3
    has_up: bool
    parent: int


def _unparented_marker_frame(
    objects: list[bpy.types.Object],
    obj_to_index: dict[str, int],
    depsgraph: bpy.types.Depsgraph,
) -> tuple[int, Vector]:
    """Return the submodel an unparented marker attaches to, and its origin.

    A marker whose Blender parent is not one of the exported objects still has
    to name a submodel -- the format has no "attached to the model itself" -- so
    it is attached to a root one. Parent and measuring frame must be decided
    together: naming submodel 0 while measuring from the *world* origin made the
    engine add that submodel's offset to a position that already included it,
    moving the gun by the root offset on a plain round trip
    (reference/polymodel.cpp, GetPolyModelPointInWorld, which walks up from the
    marker's parent submodel adding each ``offset``).

    Args:
        objects: The exported objects, in submodel-index order.

    Returns:
        A ``(parent_index, origin)`` pair: the first exported object with no
        exported parent, and the world-space point its own vertices are measured
        from. A root rather than index 0, which need not be one -- objects are
        collected in ``bpy.data.objects`` order -- and a marker on a child
        submodel would follow whatever that child does when the model animates.
    """
    for obj in objects:
        if obj.parent is None or obj.parent.name not in obj_to_index:
            eval_obj = obj.evaluated_get(depsgraph)
            return obj_to_index[obj.name], eval_obj.matrix_world.translation
    # Only reachable with no objects at all: Blender parenting cannot contain a
    # cycle, so any non-empty set has something at the top of it.
    return poformat.ROOT_SUBMODEL_INDEX, Vector((0.0, 0.0, 0.0))


def _collect_points(
    name_prefix: str,
    obj_to_index: dict[str, int],
    depsgraph: bpy.types.Depsgraph,
    selected: set[str] | None,
    unparented: tuple[int, Vector],
) -> list[_Marker]:
    """Collect empties whose name marks them as gun or attach points.

    Only empties belonging to *this* export are collected -- see
    ``docs/exporting.md``, "What gets exported".

    Args:
        name_prefix: Name prefix import used, from the project's naming
            configuration.
        obj_to_index: Object name to submodel index -- which is exactly the set
            of objects being exported -- for resolving the parent.
        selected: Names of the selected objects when Selected Only is on, or
            ``None`` when the whole file is being exported.
        unparented: Submodel index and world-space origin an empty falls back to
            when its parent is not one of the exported objects, from
            :func:`_unparented_marker_frame`.

    Returns:
        One :class:`_Marker` per empty, each position relative to the submodel
        it is attached to, in the order the names number them rather than the
        order Blender stores them in.
    """
    points = []
    # bpy.data.objects is sorted as text, so ``Gun_10`` sits between ``Gun_1``
    # and ``Gun_2`` and the banks come out permuted -- see
    # :func:`~descent3_plugin.naming.marker_order_key` for why that matters.
    markers = sorted(
        (obj for obj in bpy.data.objects
         if obj.type == "EMPTY" and obj.name.startswith(name_prefix)),
        key=lambda obj: marker_order_key(obj.name, name_prefix),
    )
    for obj in markers:
        parented_in = obj.parent is not None and obj.parent.name in obj_to_index
        if selected is not None and not parented_in and obj.name not in selected:
            continue
        if parented_in:
            parent = obj_to_index[obj.parent.name]
            origin = _parent_origin(obj, obj_to_index, depsgraph)
        else:
            parent, origin = unparented
        eval_obj = obj.evaluated_get(depsgraph)
        # matrix_world, not obj.location: ``location`` is the offset from the
        # parent *before* matrix_parent_inverse, so a marker parented with
        # Ctrl+P reads as the position it had before it was parented. The
        # world-space delta is also the offset convention the engine expects.
        point = eval_obj.matrix_world.translation
        direction, up = _marker_axes(eval_obj)
        points.append(
            _Marker(
                position=blender_to_descent(point - origin),
                direction=direction,
                up=up,
                # Read off the original, not the evaluated copy: a custom
                # property is authored data on the object in the outliner.
                has_up=bool(obj.get(PROP_KEY_HAS_UVEC)),
                parent=parent,
            )
        )
    return points


classes = (ExportPOF,)
