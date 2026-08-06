"""Export side of the add-on: Blender objects to a POF/OOF file.

Holds the :class:`ExportPOF` operator and the conversion that turns Blender
meshes and empties into a :class:`~descent3_plugin.poformat.POFModel`. The
binary writing itself lives in :mod:`descent3_plugin.poformat`, which has no
``bpy`` dependency; everything in this module runs only inside Blender.
"""

import logging

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import poformat
from .config import CONFIG_FILENAME, Config, config_for_path
from .mathutil import Vector3
from .naming import resolve_texture_names
from .constants import (
    EXPORT_OT_IDNAME,
    POF_FILENAME_EXT,
    POF_FILTER_GLOB,
    PROP_KEY_FLAGS,
    PROP_KEY_MOVEMENT_AXIS,
    PROP_KEY_MOVEMENT_TYPE,
    PROP_KEY_PROPERTIES,
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

# The log channel is the package, not this module, so console output keeps the
# single "[descent3_plugin]" prefix that register() configures.
log = logging.getLogger(__package__)

# ---------------------------------------------------------------------------
# Export-side constants
# ---------------------------------------------------------------------------

#: Identifier meaning "whatever the project's descent3.toml asks for".
#:
#: This is an explicit dropdown entry rather than a heuristic. The config that
#: applies is the one beside the *destination*, which is not known until the
#: file browser closes, so the dialog cannot honestly pre-select the project's
#: version. Making "use the project's setting" a visible choice means the user
#: can see which one they are getting instead of the add-on guessing whether
#: they meant the default they never touched.
EXPORT_VERSION_FROM_CONFIG = "CONFIG"

#: Versions offered in the export dialog, as ``(identifier, label, tooltip)``.
#: Identifiers are the ``major * 100 + minor`` version written to the file.
EXPORT_VERSION_ITEMS = [
    (EXPORT_VERSION_FROM_CONFIG, "From project config",
     "Use the version set in the project's descent3.toml, or v23.00 if there "
     "is none"),
    ("2300", "v23.00 (Latest)", "Descent 3 retail version"),
    ("2200", "v22.00", "Timed animation support"),
    ("2100", "v21.00", "Lightmap UV support"),
]

#: Default selection: defer to the project.
DEFAULT_EXPORT_VERSION = EXPORT_VERSION_FROM_CONFIG

#: Seed for the bounding-box accumulator, chosen large enough that the first
#: vertex compared always replaces it on both the min and max side.
BOUNDS_INIT = 1e30

#: Color written for a face whose material cannot be mapped to a texture.
DEFAULT_UNTEXTURED_COLOR = (0.5, 0.5, 0.5)

#: Placeholder direction written for gun and attach points. Blender empties
#: carry a rotation this exporter does not yet read, so every point is written
#: facing +Z rather than in the direction the empty is actually pointing.
DEFAULT_POINT_NORMAL = (0.0, 0.0, 1.0)


class ExportPOF(bpy.types.Operator, ExportHelper):
    """Export a Descent 3 POF/OOF model file"""

    bl_idname = EXPORT_OT_IDNAME
    bl_label = "Export Descent 3 POF"
    bl_description = "Export the selected objects as a Descent 3 polygon model (.pof)"
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


def save_pof(
    context: bpy.types.Context,
    filepath: str,
    operator: ExportPOF | None = None,
) -> set[str]:
    """Export mesh objects to a POF file.

    Args:
        context: Blender context supplying the selection to export.
        filepath: Path of the ``.pof`` file to write.
        operator: Operator whose options steer the export and which receives
            user-facing reports. Passing ``None`` stays silent, exports every
            mesh in the file at :data:`DEFAULT_EXPORT_VERSION`, and writes no
            gun or attach points -- it does not fall back to the operator's
            defaults, which enable both.

    Returns:
        ``{"FINISHED"}`` on success, or ``{"CANCELLED"}`` if there was nothing
        to export or the file could not be written.
    """
    config, config_warnings = config_for_path(filepath)
    for warning in config_warnings:
        log.warning("%s: %s", config.source_path or CONFIG_FILENAME, warning)
        if operator:
            operator.report({"WARNING"}, f"config: {warning}")

    if operator and operator.export_selected:
        objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
    else:
        objects = [obj for obj in bpy.data.objects if obj.type == "MESH"]

    if not objects:
        log.warning("Nothing to export: no mesh objects%s",
                    " selected" if operator and operator.export_selected else "")
        if operator:
            operator.report({"WARNING"}, "No mesh objects to export")
        return {"CANCELLED"}

    model = _build_pof_model(context, objects, operator, config)

    try:
        data = poformat.write_pof(model)
        with open(filepath, "wb") as f:
            f.write(data)
    except Exception as e:
        log.error("Failed to write %s: %s", filepath, e)
        if operator:
            operator.report({"ERROR"}, f"Failed to write POF: {e}")
        return {"CANCELLED"}

    if operator:
        operator.report({"INFO"}, f"Exported {len(model.submodels)} submodels")

    return {"FINISHED"}


def _build_pof_model(
    context: bpy.types.Context,
    objects: list[bpy.types.Object],
    operator: ExportPOF | None = None,
    config: Config | None = None,
) -> POFModel:
    """Build a :class:`POFModel` from Blender objects.

    Args:
        context: Blender context. Unused directly, but kept so the signature
            matches the other export helpers.
        objects: Mesh objects to export, in the order they become submodels.
        operator: Operator carrying the export options, or ``None`` for
            defaults.
        config: Project configuration. ``None`` resolves to the built-in
            defaults, which match the add-on's behaviour before configuration
            existed.

    Returns:
        A model ready for :func:`descent3_plugin.poformat.write_pof`, with its
        hierarchy already built.
    """
    if config is None:
        config = Config()
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

    model.textures, material_textures = _collect_textures(objects)
    tex_index = {name: i for i, name in enumerate(model.textures)}

    min_bound = Vector3(BOUNDS_INIT, BOUNDS_INIT, BOUNDS_INIT)
    max_bound = Vector3(-BOUNDS_INIT, -BOUNDS_INIT, -BOUNDS_INIT)

    for obj in objects:
        if obj.type != "MESH":
            continue
        sm = _build_submodel(
            obj, obj_to_index, tex_index, material_textures, min_bound, max_bound
        )
        model.submodels.append(sm)

    model.min_bound = min_bound
    model.max_bound = max_bound
    model.radius = _bounding_radius(min_bound, max_bound)

    if operator and operator.export_guns:
        model.gun_banks = [
            GunBank(
                parent=parent,
                point=point,
                normal=Vector3(*DEFAULT_POINT_NORMAL),
            )
            for point, parent in _collect_points(
                config.naming.gun_prefix, obj_to_index
            )
        ]

    if operator and operator.export_attach:
        model.attach_points = [
            AttachPoint(
                parent=parent,
                point=point,
                normal=Vector3(*DEFAULT_POINT_NORMAL),
            )
            for point, parent in _collect_points(
                config.naming.attach_prefix, obj_to_index
            )
        ]

    model.build_hierarchy()

    return model


def _resolve_version(operator: ExportPOF | None, config: Config) -> int:
    """Return the POF version to write.

    Args:
        operator: Operator carrying the dialog selection, or ``None``.
        config: Project configuration resolved from the destination path.

    Returns:
        The project's configured version unless the dialog names a specific
        one. Without an operator -- headless and scripted exports -- the
        project's version always applies, so a script and the UI's default
        agree instead of quietly targeting different builds.
    """
    if operator is None:
        return config.export.version
    selection = operator.export_version
    if selection == EXPORT_VERSION_FROM_CONFIG:
        return config.export.version
    return int(selection)


def _collect_textures(objects: list[bpy.types.Object]) -> list[str]:
    """Collect the texture names used by ``objects``.

    Material names are the texture names: import names each material after the
    texture it came from, so export can map back without extra bookkeeping.

    Blender's ``.NNN`` duplicate suffix is stripped first. Import reuses an
    existing material rather than letting Blender mint ``Hull.001``, but a scene
    built before that fix -- or a material the user duplicated by hand -- can
    still carry one, and writing ``Hull.001`` into the TXTR chunk produces a
    model referencing a bitmap that does not exist. Materials that collapse onto
    the same texture ID are merged, which is the intent: they stand for one
    texture.

    Args:
        objects: Mesh objects being exported.

    Returns:
        A ``(textures, mapping)`` pair. ``textures`` is sorted so the same scene
        always produces the same texture-index assignment. ``mapping`` takes a
        material name to the texture it exports as, and the per-face lookup must
        use it too -- resolving the rule twice is how the texture list and the
        faces end up disagreeing.
    """
    material_names = set()
    for obj in objects:
        if obj.type != "MESH":
            continue
        for mat in obj.data.materials:
            if mat:
                material_names.add(mat.name)

    mapping, warnings = resolve_texture_names(material_names)
    for warning in warnings:
        log.warning("%s", warning)

    return sorted(set(mapping.values())), mapping


def _build_submodel(
    obj: bpy.types.Object,
    obj_to_index: dict[str, int],
    tex_index: dict[str, int],
    material_textures: dict[str, str],
    min_bound: Vector3,
    max_bound: Vector3,
) -> Submodel:
    """Convert one Blender mesh object into a :class:`Submodel`.

    Args:
        obj: Mesh object to convert.
        obj_to_index: Object name to submodel index, for resolving the parent.
        tex_index: Texture name to index into the model's texture list.
        material_textures: Material name to the texture it exports as.
        min_bound: Running minimum corner, updated in place.
        max_bound: Running maximum corner, updated in place.

    Returns:
        The submodel, with its radius computed.
    """
    sm = Submodel()
    sm.index = obj_to_index[obj.name]
    sm.name = obj.name

    if obj.parent and obj.parent.name in obj_to_index:
        sm.parent = obj_to_index[obj.parent.name]
    else:
        sm.parent = poformat.NO_PARENT

    sm.flags = int(obj.get(PROP_KEY_FLAGS, 0))
    sm.movement_type = int(
        obj.get(PROP_KEY_MOVEMENT_TYPE, poformat.MOVEMENT_TYPE_NONE)
    )
    sm.movement_axis = int(obj.get(PROP_KEY_MOVEMENT_AXIS, 0))
    sm.props = str(obj.get(PROP_KEY_PROPERTIES, ""))

    # Offset from the parent, in local space. Only meaningful when the parent is
    # itself an exported mesh; a root submodel sits at the model origin.
    if obj.parent and obj.parent.type == "MESH":
        local_loc = obj.matrix_local.translation
        sm.offset = Vector3(local_loc.x, local_loc.y, local_loc.z)
    else:
        sm.offset = Vector3(0, 0, 0)

    mesh = obj.data

    for v in mesh.vertices:
        sv = SubmodelVertex()
        sv.position = Vector3(v.co.x, v.co.y, v.co.z)
        sv.normal = Vector3(v.normal.x, v.normal.y, v.normal.z)
        sv.alpha = poformat.ALPHA_OPAQUE
        sm.vertices.append(sv)
        _grow_bounds(min_bound, max_bound, v.co)

    uv_layer = mesh.uv_layers.active
    for poly in mesh.polygons:
        sm.faces.append(
            _build_face(poly, mesh, obj, tex_index, material_textures, uv_layer)
        )

    for v in sm.vertices:
        dist = v.position.magnitude()
        if dist > sm.radius:
            sm.radius = dist

    return sm


def _grow_bounds(min_bound: Vector3, max_bound: Vector3, co) -> None:
    """Expand the running bounding box to include ``co``.

    Args:
        min_bound: Minimum corner, updated in place.
        max_bound: Maximum corner, updated in place.
        co: A Blender vertex coordinate.
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
    obj: bpy.types.Object,
    tex_index: dict[str, int],
    material_textures: dict[str, str],
    uv_layer,
) -> ModelFace:
    """Convert one Blender polygon into a :class:`ModelFace`.

    Args:
        poly: Polygon to convert.
        mesh: Mesh the polygon belongs to, for its loop list.
        obj: Object the mesh belongs to, for its material slots.
        tex_index: Texture name to index into the model's texture list.
        material_textures: Material name to the texture it exports as, from
            :func:`_collect_textures`. Resolving the suffix rule here instead
            would let the faces and the texture list disagree.
        uv_layer: Active UV layer, or ``None`` if the mesh has no UVs.

    Returns:
        The face, textured when its material maps to a known texture and flat
        :data:`DEFAULT_UNTEXTURED_COLOR` otherwise. The material name is
        resolved through ``material_textures`` so a duplicate-suffixed
        material still finds the texture it was cloned from.
    """
    face = ModelFace()
    face.normal = Vector3(poly.normal.x, poly.normal.y, poly.normal.z)

    mat = None
    if poly.material_index < len(obj.data.materials):
        mat = obj.data.materials[poly.material_index]
    texture = material_textures.get(mat.name) if mat else None
    if texture is not None and texture in tex_index:
        face.textured = True
        face.texnum = tex_index[texture]
    else:
        face.textured = False
        face.color = Color(*DEFAULT_UNTEXTURED_COLOR)

    for loop_idx in poly.loop_indices:
        loop = mesh.loops[loop_idx]
        fv = FaceVertex()
        fv.index = loop.vertex_index
        if uv_layer:
            uv = uv_layer.data[loop_idx].uv
            fv.u = uv.x
            # Descent 3 puts the UV origin at the top-left, Blender at the
            # bottom-left. Import negates V, so export must negate it back or
            # every exported texture comes out mirrored.
            fv.v = -uv.y
        else:
            fv.u = 0.0
            fv.v = 0.0
        face.vertices.append(fv)

    return face


def _bounding_radius(min_bound: Vector3, max_bound: Vector3) -> float:
    """Return the radius of a sphere at the origin enclosing the bounding box.

    Args:
        min_bound: Minimum corner of the box.
        max_bound: Maximum corner of the box.

    Returns:
        The distance from the origin to the furthest box corner. All eight
        corners are measured because the box is not centered on the origin, so
        the furthest one is not always ``max_bound``.
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


def _collect_points(
    name_prefix: str, obj_to_index: dict[str, int]
) -> list[tuple[Vector3, int]]:
    """Collect empties whose name marks them as gun or attach points.

    Args:
        name_prefix: Name prefix import used, from the project's naming
            configuration.
        obj_to_index: Object name to submodel index, for resolving the parent.

    Returns:
        ``(position, parent_index)`` pairs. An empty whose parent is not an
        exported mesh is attached to the root submodel rather than dropped.
    """
    points = []
    for obj in bpy.data.objects:
        if obj.type != "EMPTY" or not obj.name.startswith(name_prefix):
            continue
        parent = obj_to_index.get(
            obj.parent.name if obj.parent else "", poformat.ROOT_SUBMODEL_INDEX
        )
        point = Vector3(obj.location.x, obj.location.y, obj.location.z)
        points.append((point, parent))
    return points


#: bpy types this module contributes to add-on registration.
classes = (ExportPOF,)
