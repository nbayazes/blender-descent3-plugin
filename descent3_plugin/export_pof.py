"""Export side of the add-on: Blender objects to a POF/OOF file.

Holds the :class:`ExportPOF` operator and the conversion that turns Blender
meshes and empties into a :class:`~descent3_plugin.poformat.POFModel`. The
binary writing itself lives in :mod:`descent3_plugin.poformat`, which has no
``bpy`` dependency; everything in this module runs only inside Blender.
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

#: Identifier meaning "whatever the project's descent3.toml asks for",
#: mirroring the POF version control above.
TEXTURE_FORMAT_FROM_CONFIG = "CONFIG"

#: Wording for each entry of :data:`~descent3_plugin.naming.TEXTURE_FORMATS`,
#: as ``identifier -> (label, tooltip)``.
#:
#: Only the wording lives here. *Which* formats exist is that table's job -- it
#: owns the pairing of Blender encoder to file extension -- and a second list of
#: format names in this module was a second place to forget: it is how the
#: dropdown and the writer drift apart. A format with no entry below still
#: appears, labelled by its identifier, so adding an encoder stays a one-line
#: change to the table and never a format nobody can pick.
TEXTURE_FORMAT_LABELS = {
    "PNG": ("PNG", "Lossless, and what the importer looks for first"),
    "TARGA": ("Targa (.tga)", "Uncompressed, closest to Descent 3's own tooling"),
}


def _texture_format_items() -> list[tuple[str, str, str]]:
    """Build the Textures dropdown from the supported-format table.

    Returns:
        ``(identifier, label, tooltip)`` triples: the two choices that are not
        formats first, then every key of
        :data:`~descent3_plugin.naming.TEXTURE_FORMATS` in table order.

        Descent 3's native OGF is missing because that table omits it on
        purpose. Blender cannot encode OGF, so offering it would produce a file
        the game rejects; the entry appears here the moment an encoder exists,
        without anyone having to remember this list.
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


#: Texture-image formats offered in the export dialog.
#:
#: One control rather than a checkbox plus a format list: "do not write
#: textures" and "write them as PNG" are the same decision, and splitting
#: them lets a user tick the box, leave the format wrong, and not find out.
#:
#: Built once at import time and held in a module-level name, because Blender
#: keeps no reference to the strings an ``EnumProperty`` is given.
TEXTURE_FORMAT_ITEMS = _texture_format_items()

#: Seed for the bounding-box accumulator, chosen large enough that the first
#: vertex compared always replaces it on both the min and max side.
#:
#: A model with no vertices never gets that first comparison, so the seeded
#: accumulator is discarded rather than written -- see :func:`_model_bounds`.
BOUNDS_INIT = 1e30

#: Color written for a face whose material cannot be mapped to a texture.
DEFAULT_UNTEXTURED_COLOR = (0.5, 0.5, 0.5)

#: Direction written for a marker whose transform has no orientation to read --
#: an empty scaled flat to nothing, where the axes are degenerate and the
#: quaternion they produce means nothing.
#:
#: This used to be written for *every* gun and attach point, because the empty's
#: rotation was never read: the file's own firing directions were parsed on
#: import, dropped, and replaced here with this one constant, so a round trip
#: pointed every gun on the ship the same way. Markers now export the direction
#: their empty actually faces (see :func:`_marker_axes`) and this is only the
#: floor under a transform that cannot be asked.
#:
#: Already expressed in Descent 3 (Y-up) space, because it goes straight into
#: the file -- it must NOT be passed through :func:`blender_to_descent`.
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
        context: Blender context supplying the selection to export.
        filepath: Path of the ``.pof`` file to write.
        operator: Operator whose options steer the export and which receives
            user-facing reports. Passing ``None`` stays silent, exports
            everything :func:`_is_submodel` accepts at
            :data:`DEFAULT_EXPORT_VERSION`, and writes no gun or attach points --
            it does not fall back to the operator's defaults, which enable both.

    Returns:
        ``{"FINISHED"}`` on success, or ``{"CANCELLED"}`` if there was nothing
        to export or the file could not be written.
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
            # "mesh objects", though :func:`_is_submodel` also takes the empties
            # standing in for geometry-less submodels: a file holding one of
            # those and no meshes is a hierarchy with nothing in it, and naming
            # the empties here would only invite the user to go looking for one.
            operator.report({"WARNING"}, "No mesh objects to export")
        return {"CANCELLED"}

    model, material_textures, warnings = _build_pof_model(
        context, objects, operator, config
    )
    # Before the file is written, because they describe what is about to go into
    # it -- a material merged onto another's texture, or an ID no image can be
    # named after. The console has always had these; the user has not.
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

    Every mesh is one. So is an *Empty* that came from a submodel with no
    geometry, and it has to be: that is how Descent 3 spells a rotating joint --
    a turret's pivot exists to carry ``$rotate=`` and to be the thing the engine
    turns its children about, and it has no vertices of its own. Exporting only
    meshes dropped it, re-rooted its children onto the hull, and left the model
    looking correct in Blender while the turret no longer moved.

    Args:
        obj: Any object in the scene or the selection.

    Returns:
        True for meshes, and for empties carrying
        :data:`~descent3_plugin.constants.PROP_KEY_INDEX`. That property is the
        whole test for an empty, because the alternative -- "an empty that is not
        a marker" -- would sweep up every light rig, pivot and organisational
        empty in the user's file and write each one into the model as a submodel
        nobody asked for. An empty a user builds by hand joins the model by being
        given that property, which is also how they would give it a ``$rotate=``.
    """
    if obj.type == "MESH":
        return True
    return obj.type == "EMPTY" and PROP_KEY_INDEX in obj


def _resolve_texture_format(operator: ExportPOF | None, config: Config) -> str:
    """Return the texture format to write.

    Args:
        operator: Operator carrying the dialog selection, or ``None``.
        config: Project configuration resolved from the destination.

    Returns:
        A key of :data:`~descent3_plugin.naming.TEXTURE_FORMATS`, or
        :data:`~descent3_plugin.naming.TEXTURE_FORMAT_NONE`. Without an
        operator the project decides, so a scripted export and the dialog's
        default agree instead of quietly differing.
    """
    if operator is None:
        return config.export.texture_format
    # getattr, not attribute access: save_pof is called with duck-typed
    # stand-ins from the headless scripts, and adding a property to the operator
    # would otherwise break every one of them. Defaulting to the config keeps a
    # caller that predates this option behaving as it always did.
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

    The model itself is not consulted, and does not need to be: its texture list
    was built from these same objects' materials through this same mapping, so
    walking the materials covers exactly the names that were written to the file.
    A check for a texture no material carries used to live here and could not
    fire -- both sides came from one source -- so it is gone rather than kept as
    reassurance it never provided.

    Args:
        context: Blender context, for the scene used during conversion.
        filepath: Path of the model just written; textures go in a subfolder
            beside it.
        objects: Objects that were exported.
        material_textures: Material name to the texture it exports as.
        fmt: Format to write.
        config: Project configuration supplying the subfolder name.

    Returns:
        A ``(written, problems)`` pair from
        :func:`~descent3_plugin.texexport.export_textures`.
    """
    # Pick one material per texture ID. Several materials can map onto the
    # same texture once duplicate suffixes are resolved, and they are meant
    # to be the same image, so the first is representative.
    #
    # Evaluated objects, matching _collect_textures: a slot a modifier added is a
    # texture the model references, and it needs its image written like any
    # other. Reading the originals here would name it in the file and then not
    # write it.
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
        context: Blender context, supplying the evaluated dependency graph that
            every world matrix and every modifier result is read from, and the
            selection that limits which markers belong to this export.
        objects: Objects to export, in the order they become submodels. Already
            filtered by :func:`_is_submodel`, so each is a mesh or an empty
            standing in for a submodel that never had geometry.
        operator: Operator carrying the export options, or ``None`` for
            defaults.
        config: Project configuration. ``None`` resolves to the built-in
            defaults, which match the add-on's behaviour before configuration
            existed.

    Returns:
        A ``(model, material_textures, warnings)`` triple. The model is ready for
        :func:`descent3_plugin.poformat.write_pof` with its hierarchy already
        built; the mapping takes a material name to the texture it exported as,
        and is handed back rather than recomputed so texture export cannot
        disagree with what was written into the file; the warnings are things the
        user has to be told about the model that was just built, for the caller
        to report through the operator.
    """
    if config is None:
        config = Config()
    # Asking for the depsgraph also evaluates anything left tagged, which is
    # what makes ``matrix_world`` below the object's current transform rather
    # than whatever it was at the last redraw. A script that moves an object and
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

    # Bounds come last and from the finished submodels: the box spans the whole
    # model, and where a submodel's vertices actually sit is not known until its
    # offset -- and every offset above it -- exists.
    model.min_bound, model.max_bound, model.radius = _model_bounds(model.submodels)

    # Markers are matched by name across the whole file, so the export set has
    # to bound them too, exactly as it bounds the meshes.
    selected = None
    if operator and operator.export_selected:
        selected = {obj.name for obj in context.selected_objects}

    # Resolved once, because both marker passes measure from it and the two must
    # not be allowed to answer differently.
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
                # Only when the file this came from specified one. An empty
                # always has a roll; whether anybody chose it is the question,
                # and inventing an up vector for every attach point would write
                # a NATH chunk the model never had. See PROP_KEY_HAS_UVEC.
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
    selection = getattr(operator, "export_version", EXPORT_VERSION_FROM_CONFIG)
    if selection == EXPORT_VERSION_FROM_CONFIG:
        return config.export.version
    return int(selection)


def _collect_textures(
    objects: list[bpy.types.Object],
    depsgraph: bpy.types.Depsgraph,
) -> tuple[list[str], dict[str, str], list[str]]:
    """Collect the texture names used by ``objects``.

    A material's name is normally the texture name -- import names each material
    after the texture it came from -- but a name is not evidence. A material this
    add-on created also records the texture ID it stands for in
    :data:`~descent3_plugin.constants.PROP_KEY_TEXTURE`, and that recording wins
    outright: ``metal`` and ``metal.001`` are two real Descent 3 bitmaps, and
    reading the second as "a Blender duplicate of the first" merged them,
    repointed its faces and lost a texture from the model.

    Materials carrying no such record -- authored by hand, or imported before
    the add-on recorded anything -- still go through the ``.NNN`` heuristic in
    :mod:`~descent3_plugin.naming`, which only collapses a suffixed name onto an
    original that is actually in the scene, and says so when it does. Those are
    now the only materials that get merged.

    Args:
        objects: Objects being exported.
        depsgraph: Evaluated dependency graph. The slots are read off the
            *evaluated* objects, because that is where the faces come from: a
            Boolean set to transfer materials, or a Geometry Nodes Set Material,
            adds slots that exist only after evaluation, and reading the
            originals left those faces pointing at a slot the original does not
            have -- exported flat grey, with their texture missing from the model
            entirely and nothing said about either.

    Returns:
        A ``(textures, mapping, warnings)`` triple. ``textures`` is sorted so the
        same scene always produces the same texture-index assignment. ``mapping``
        takes a material name to the texture it exports as, and the per-face
        lookup must use it too -- resolving the rule twice is how the texture
        list and the faces end up disagreeing. ``warnings`` are for the user, and
        are handed back rather than only logged: a merged material or an
        unusable texture ID changes what the exported model says, and the system
        console is not where somebody finds that out.
    """
    material_names = set()
    explicit: dict[str, str] = {}
    for obj in objects:
        for mat in _slot_materials(obj.evaluated_get(depsgraph)):
            material_names.add(mat.name)
            if PROP_KEY_TEXTURE in mat:
                # str(): this is a custom property, so the UI will happily let a
                # user type a number into it, and whatever is here goes straight
                # into the TXTR chunk as a texture name.
                explicit[mat.name] = str(mat[PROP_KEY_TEXTURE])

    mapping, warnings = resolve_texture_names(material_names, explicit)
    textures = sorted(set(mapping.values()))

    # A texture ID is also the name of the file that holds it, so an ID that
    # cannot be a filename leaves the model referencing a bitmap nobody can ever
    # write or find. Texture export already refuses those names, but it only runs
    # when the user asked for images -- the ID goes into the TXTR chunk either
    # way, and with Textures set to None nothing said a word about it. Said here,
    # where the name is chosen, rather than where one consequence of it shows up.
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

    ``obj.material_slots``, never ``obj.data.materials``. A slot's link can be
    set to OBJECT, which moves the material onto the object and leaves the mesh's
    own list holding ``None`` for that slot -- so reading the mesh sees no
    materials at all, exports an empty TXTR chunk and writes every face flat
    grey, for a model the viewport shows fully textured. The slot is the accessor
    that honours both link modes, which is the entire reason it exists.

    Args:
        obj: Object whose slots are read. Callers pass the *evaluated* object,
            since a modifier can add slots the original does not have and the
            faces being exported are indexed against the evaluated list.

    Returns:
        One material per filled slot, in slot order. Empty slots are skipped:
        they name no texture, and the faces pointing at them export untextured.
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

    What gets written is the object as the viewport draws it: the depsgraph's
    evaluated copy, so modifiers are applied, and every vertex through
    ``matrix_world``, so rotation and scale are baked in. Reading ``obj.data``
    straight -- which is what this did -- writes the mesh exactly as it was
    authored and silently drops all three: a wing rotated into place exported
    lying flat, a model scaled to fit exported at its modelling size, and a
    mirror modifier exported as half a ship. None of that is visible in Blender,
    only in the game.

    Positions are re-centred on the object's own origin afterwards, because a
    submodel stores its vertices relative to itself; the origin's own position
    travels separately in ``offset``.

    An object with no geometry is converted too, and is not a degenerate case: a
    submodel with no vertices is how Descent 3 spells a joint, and this add-on
    imports one as an Empty. Everything that makes it a joint -- its offset, its
    movement type, its ``$rotate=`` -- is exactly the half of this function that
    is not about geometry, so it is set the same way a mesh's is and the rest is
    skipped.

    Args:
        obj: Object to convert: a mesh, or an empty standing in for a submodel
            that never had geometry.
        depsgraph: Evaluated dependency graph, supplying this object's modifier
            result and the world matrices of it and its parent.
        obj_to_index: Object name to submodel index, for resolving the parent.
        tex_index: Texture name to index into the model's texture list.
        material_textures: Material name to the texture it exports as.

    Returns:
        The submodel, with its radius computed. The *model's* bounds are not
        grown here: they need each submodel's offset summed down the hierarchy,
        which nothing knows until every submodel exists -- see
        :func:`_model_bounds`.
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
    # No ``sm.flags`` is set, deliberately. The SOBJ record has no flag word:
    # the engine derives every SOF_ bit from the property string while parsing
    # it (reference/polymodel.cpp, SetPolymodelProperties) and so does this
    # add-on's reader, so writing ``props`` verbatim above is what carries the
    # flags across. Assigning ``sm.flags`` from the object's ``pof_flags``
    # property only filled in a field write_pof_stream never serializes, which
    # read as though the flags were being exported when they were not.

    eval_obj = obj.evaluated_get(depsgraph)
    world = eval_obj.matrix_world
    origin = world.translation

    # A world-space delta, not ``matrix_local.translation``. The engine sums
    # offsets down the hierarchy as plain vector adds and never rotates a
    # child's offset by its parent (reference/polymodel.cpp, MinMaxSubmodel),
    # so the number it wants is the distance between the two origins in world
    # space -- which is also the one thing matrix_local stops reporting the
    # moment a parent is rotated or scaled.
    sm.offset = blender_to_descent(
        origin - _parent_origin(obj, obj_to_index, depsgraph)
    )

    # Everything above is what a submodel is; everything below is geometry, and
    # a joint has none. Nothing here is skipped for it -- offset, movement and
    # properties are already set, and a radius of zero is the right radius for
    # something with no vertices.
    if obj.type != "MESH":
        return sm

    # Directions take the inverse transpose rather than the matrix itself:
    # under a non-uniform scale the matrix skews a normal off the surface it is
    # supposed to be perpendicular to, and the model lights subtly wrong in
    # exactly the cases somebody stretched something to fit.
    normal_matrix = world.to_3x3().inverted_safe().transposed()

    # A negative determinant means the transform mirrors -- the standard way to
    # build a left wing is to duplicate the right one and scale it -1 on X. The
    # inverse transpose turns the normals round with the surface, but nothing
    # turns the *winding* round, so a mirrored part would be written with every
    # face's corners in the order they had before the mirror and a normal
    # pointing the other way: the two disagree, and the part renders inside out.
    # Reversing the corners here is what puts them back in agreement. Note this
    # is about the object's own matrix and not the Y-up/Z-up conversion, which
    # is a rotation and never mirrors anything (see :mod:`.mathutil`).
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
        # The mesh to_mesh() returns is owned by the evaluated object and is
        # only valid until it is released. In a ``finally`` because an error
        # part-way through conversion would otherwise leak it for the rest of
        # the session.
        eval_obj.to_mesh_clear()

    for v in sm.vertices:
        dist = v.position.magnitude()
        if dist > sm.radius:
            sm.radius = dist

    return sm


def _submodel_name(obj: bpy.types.Object) -> str:
    """Return the name to write into the submodel's SOBJ record.

    Normally the object's name, which is what the user sees and what they expect
    to be able to change. The exception is Blender's uniquifying suffix: a file
    holding two submodels called ``Wing`` -- legal, and something real models do,
    since the engine uses the field as a label and never looks a submodel up by
    it -- imports as ``Wing`` and ``Wing.001``, and writing that back put
    Blender's bookkeeping into the model.

    Args:
        obj: Object being exported as a submodel.

    Returns:
        The name recorded from the file when the object still carries it and its
        current name is that name, suffix or not; otherwise the object's name,
        which covers both a rename (the user's word beats the file's) and an
        object nobody imported (there is no other word to use). See
        :data:`~descent3_plugin.constants.PROP_KEY_SUBMODEL_NAME`.
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
        obj: Object whose parent is wanted.
        obj_to_index: Object name to submodel index, which is exactly the set of
            objects being exported.
        depsgraph: Evaluated dependency graph, for the parent's world matrix.

    Returns:
        The parent's world-space origin when the parent is itself being
        exported, else the world origin. A *submodel* whose parent is not in the
        file is written with :data:`~descent3_plugin.poformat.NO_PARENT`, so the
        engine sums offsets to it from the model origin and the world origin is
        the matching frame to measure from. A marker cannot say NO_PARENT and so
        cannot use this fallback -- see :func:`_unparented_marker_frame`.
    """
    parent = obj.parent
    if parent is not None and parent.name in obj_to_index:
        return parent.evaluated_get(depsgraph).matrix_world.translation
    return Vector((0.0, 0.0, 0.0))


def _vertex_normals(mesh: bpy.types.Mesh) -> list[Vector]:
    """Return one normal per vertex, in the mesh's own space.

    Descent 3 stores a single normal per vertex, and import puts the file's
    normals into Blender's custom split normals -- the ``CD_CUSTOMLOOPNORMAL``
    layer written by ``normals_split_custom_set_from_vertices``. That is the only
    place they survive: ``MeshVertex.normal`` is *computed* from the surrounding
    faces, so reading it back exported Blender's idea of the shading and threw
    the authored normals away on every single round trip.

    Custom normals are per *loop*, so each vertex takes the normal of the first
    loop that touches it. For anything this add-on imported that is exact -- the
    same normal was written to every loop of a vertex -- and for hand-edited
    normals it is at least one of the values the artist actually authored rather
    than an average of several that matches none of them.

    Args:
        mesh: Mesh to read, normally an evaluated copy from ``to_mesh()``.

    Returns:
        A normal per vertex, in ``mesh.vertices`` order. Falls back to the
        computed vertex normals for a mesh carrying no custom split normals --
        where the two agree anyway for smooth shading, and where the computed
        one is the better answer for flat shading, since the engine has nowhere
        to put a per-face normal. Both attributes are looked up rather than
        assumed, so a Blender without ``corner_normals`` takes the same path.
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
        The same direction in file coordinates, unit length. Re-normalizing is
        not optional: the inverse transpose preserves a normal's direction but
        not its length, and the engine treats these as unit vectors when it
        lights a face.
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
        poly: Polygon to convert.
        mesh: Mesh the polygon belongs to, for its loop list.
        eval_obj: *Evaluated* object the mesh belongs to, for its material slots.
            It has to be the evaluated one, because ``poly.material_index``
            indexes the evaluated slot list: a modifier can add a slot, and
            looking that index up on the original object finds either the wrong
            material or none at all.
        tex_index: Texture name to index into the model's texture list.
        material_textures: Material name to the texture it exports as, from
            :func:`_collect_textures`. Resolving the rule here instead would let
            the faces and the texture list disagree.
        uv_layer: Active UV layer, or ``None`` if the mesh has no UVs.
        normal_matrix: 3x3 inverse transpose of the object's world matrix. The
            face normal goes through the same transform as the vertex normals,
            or a rotated object exports faces pointing where they pointed before
            it was turned.
        mirrored: Whether the object's world matrix reverses orientation, in
            which case the corners are written in reverse order so the winding
            still agrees with the normal.

    Returns:
        The face, textured when its material maps to a known texture and flat
        :data:`DEFAULT_UNTEXTURED_COLOR` otherwise. The material name is
        resolved through ``material_textures``, so a material carrying recorded
        provenance lands on the texture it records and a duplicate-suffixed one
        still finds the texture it was cloned from.
    """
    face = ModelFace()
    face.normal = _transform_normal(poly.normal, normal_matrix)

    mat = None
    if 0 <= poly.material_index < len(eval_obj.material_slots):
        # The slot, not ``obj.data.materials``: see :func:`_slot_materials` for
        # what reading the mesh's own list loses.
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


def _model_bounds(submodels: list[Submodel]) -> tuple[Vector3, Vector3, float]:
    """Return the model's bounding box and radius, in file coordinates.

    Every submodel stores its vertices relative to its own origin, so growing a
    box straight from them describes each submodel where it is *not*: a wing two
    metres out contributes vertices that all sit within a metre of the model
    centre, and the box comes out the size of the largest single submodel. The
    engine grows it from each submodel's own extents plus its offset summed down
    the parent chain (reference/polymodel.cpp, MinMaxSubmodel, called for every
    root by FindMinMaxForModel), and this does the same per vertex, which is the
    same box.

    An undersized box is not cosmetic. The engine culls and range-checks against
    it, so the wing pops out of view while the hull is still on screen and shots
    pass through anything sticking out of it.

    Args:
        submodels: Every submodel of the model, offsets already set.

    Returns:
        A ``(min_bound, max_bound, radius)`` triple, in Descent 3 coordinates
        because it goes into the file as-is. A model with no vertices at all
        gets zeros rather than the :data:`BOUNDS_INIT` seeds, which nothing
        would have replaced: those would reach the file as a box turned inside
        out, its minimum corner 1e30 above its maximum.
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

    Every step is a plain vector add, with no rotation of a child's offset by
    its parent: that is exactly what the engine does as it walks into the
    children (reference/polymodel.cpp, MinMaxSubmodel, ``offset += sm->offset``),
    and it is the reason export can write world-space deltas as offsets in the
    first place.

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
        # The chain mirrors Blender's object parenting, which cannot contain a
        # cycle. The guard is for a model hand-built through this API, where a
        # bad parent index should cost a wrong box rather than hang the export.
        visited: set[int] = set()
        while node is not None and node.index not in visited:
            visited.add(node.index)
            total = total + node.offset
            node = by_index.get(node.parent)
        accumulated[sm.index] = total
    return accumulated


def _marker_axes(obj: bpy.types.Object) -> tuple[Vector3, Vector3]:
    """Return where a marker empty points, and which way is up for it.

    The empty's own rotation is the only place a direction can live that the
    user can see and change, and
    :data:`~descent3_plugin.constants.MARKER_FORWARD_AXIS` is the axis Blender
    draws a SINGLE_ARROW empty's arrow along -- so the arrow in the viewport is
    the firing direction in the file. Import aims it the same way round.

    Args:
        obj: The evaluated marker empty.

    Returns:
        A ``(direction, up)`` pair in Descent 3 coordinates. Both fall back to
        :data:`DEFAULT_POINT_NORMAL` and the world up when the object's matrix
        carries no usable orientation -- an empty scaled to nothing has axes of
        length zero, and the quaternion they produce is arbitrary rather than
        wrong in a way that could be reported.
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

    A plain record rather than a tuple because the two callers take different
    subsets of it -- a gun bank has no up vector -- and positional unpacking
    that some callers ignore the tail of is how the wrong field ends up in the
    wrong slot.

    Attributes:
        position: Offset from the submodel it is attached to, in file
            coordinates.
        direction: Where the marker points, in file coordinates.
        up: The marker's up vector, in file coordinates. Meaningful to an attach
            point, and only when ``has_up`` says the roll was chosen rather than
            inherited from however the empty happens to sit.
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

    A marker whose Blender parent is not one of the exported objects still has to
    name a submodel -- the format has no "attached to the model itself" -- so it
    is attached to a root one. The two halves of that decision have to be made
    together, and were not: the parent was set to submodel 0 while the position
    was measured from the *world* origin, which is only the same point when
    submodel 0 sits exactly there. Everywhere else the engine added that
    submodel's offset to a position that already included it, and the gun moved
    by exactly the root offset on a plain import-and-export
    (reference/polymodel.cpp, GetPolyModelPointInWorld, which walks from the
    marker's parent submodel up the chain adding each ``offset``).

    Args:
        objects: The exported objects, in submodel-index order.
        obj_to_index: Object name to submodel index.
        depsgraph: Evaluated dependency graph, for the world matrix.

    Returns:
        A ``(parent_index, origin)`` pair: the first exported object that has no
        exported parent, and the world-space point its own vertices are measured
        from. A root rather than index 0, because index 0 need not be one --
        objects are collected in ``bpy.data.objects`` order, so a file whose root
        sorts last exports it last -- and attaching a marker to a child submodel
        would hang it off whatever that child does when the model animates.
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

    Only empties belonging to *this* export are collected. Scanning the whole
    file for a name prefix meant a second ship in the same .blend donated its
    ``Gun_*`` markers to whichever model was exported first, glued to submodel 0,
    and nothing said so. The rule is the one the README states, which is Selected
    Only's promise -- the selected objects rather than every mesh in the file --
    applied to markers as well as meshes:

    * exporting everything: every marker in the file, since every submodel in
      the file is in the model too;
    * Selected Only: a marker that is itself selected, or whose parent is one of
      the exported objects. The second half is what lets a user select a hull and
      get the guns bolted to it, without dragging in the markers of a model they
      did not select.

    Args:
        name_prefix: Name prefix import used, from the project's naming
            configuration.
        obj_to_index: Object name to submodel index -- which is exactly the set
            of objects being exported -- for resolving the parent.
        depsgraph: Evaluated dependency graph, for the world matrices.
        selected: Names of the selected objects when Selected Only is on, or
            ``None`` when the whole file is being exported.
        unparented: Submodel index and world-space origin an empty falls back to
            when its parent is not one of the exported objects, from
            :func:`_unparented_marker_frame`.

    Returns:
        One :class:`_Marker` per empty, each position relative to the submodel it
        is attached to, and in the order the names number them rather than the
        order Blender happens to store them in.
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
        # Ctrl+P -- which stuffs the parent's inverse in there -- reads as the
        # position it had before it was parented, several units from where it
        # visibly sits. The world-space delta is also the offset convention the
        # engine expects, the same one submodels use.
        point = eval_obj.matrix_world.translation
        direction, up = _marker_axes(eval_obj)
        points.append(
            _Marker(
                position=blender_to_descent(point - origin),
                direction=direction,
                up=up,
                # Read off the original, not the evaluated copy: a custom
                # property is authored data and belongs to the object the user
                # can see in the outliner.
                has_up=bool(obj.get(PROP_KEY_HAS_UVEC)),
                parent=parent,
            )
        )
    return points


#: bpy types this module contributes to add-on registration.
classes = (ExportPOF,)
