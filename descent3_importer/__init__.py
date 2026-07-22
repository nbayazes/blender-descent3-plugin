"""
Descent 3 POF/OOF Blender Importer/Exporter

Import and export Descent 3 3D model files (POF/OOF format).
Supports vertices, faces, UV mapping, materials, submodel hierarchy,
gun points, weapon batteries, attach points, and animation keyframes.
"""

bl_info = {
    "name": "Descent 3 POF/OOF Importer/Exporter",
    "author": "Descent Community",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "File > Import/Export > Descent 3 POF/OOF (.pof, .oof)",
    "description": "Import and export Descent 3 polygon model files",
    "category": "Import-Export",
}

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Vector, Matrix
import os
import logging

log = logging.getLogger(__name__)

# Relative imports for sibling modules (same package)
from . import poformat
from .texutil import clean_texture_dir, find_texture_image
from .poformat import (
    POFModel,
    Submodel,
    SubmodelVertex,
    ModelFace,
    FaceVertex,
    GunBank,
    WeaponBattery,
    AttachPoint,
    Vector3,
    Color,
    SOF_ROTATE,
    SOF_TURRET,
    SOF_SHELL,
    SOF_FRONTFACE,
    SOF_FACING,
    SOF_VIEWER,
    SOF_LAYER,
    SOF_GLOW,
    SOF_THRUSTER,
    SOF_JITTER,
    SOF_CUSTOM,
    SOF_HEADLIGHT,
    PMF_TIMED,
    PMF_ALPHA,
    MIN_OBJFILE_VERSION,
    OBJFILE_VERSION,
)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

class ImportPOF(bpy.types.Operator, ImportHelper):
    """Import a Descent 3 POF/OOF model file"""
    bl_idname = "import_scene.descent3_pof"
    bl_label = "Import Descent 3 POF"
    bl_description = "Import a Descent 3 polygon model (.pof / .oof)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".pof"
    filter_glob: StringProperty(default="*.pof;*.oof", options={"HIDDEN"})

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

    def execute(self, context):
        return import_pof(context, self.filepath, self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "import_guns")
        layout.prop(self, "import_attach")
        layout.prop(self, "texture_dir")


def import_pof(context, filepath: str, operator=None):
    """Main import function."""
    log.info("=== Importing POF: %s ===", filepath)
    print(f"[Descent3] Importing: {filepath}")

    try:
        with open(filepath, "rb") as f:
            data = f.read()
        log.info("Read %d bytes", len(data))
    except Exception as e:
        if operator:
            operator.report({"ERROR"}, f"Failed to read file: {e}")
        return {"CANCELLED"}

    try:
        model = poformat.parse_pof(data)
    except Exception as e:
        if operator:
            operator.report({"ERROR"}, f"Failed to parse POF: {e}")
        return {"CANCELLED"}

    model_dir = os.path.dirname(os.path.abspath(filepath))

    # Build the ordered list of directories to search for texture images.
    # An explicit texture directory (if provided) takes priority, then the
    # folder the model itself lives in.
    search_dirs = []
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
    if model_dir not in search_dirs:
        search_dirs.append(model_dir)

    # name -> True if a matching image file was found on disk
    tex_report = {}

    log.info("Texture search directories: %s", search_dirs)
    log.info("Model v%d: %d textures %s, %d submodels",
             model.version, len(model.textures), model.textures, len(model.submodels))
    print(f"[Descent3] v{model.version}: {len(model.textures)} textures, "
          f"{len(model.submodels)} submodels; texture search: {search_dirs}")

    # Create a collection for this model
    model_name = os.path.splitext(os.path.basename(filepath))[0]
    collection = bpy.data.collections.new(model_name)
    context.scene.collection.children.link(collection)

    # Import each submodel, keyed by its own index so hierarchy and parenting
    # lookups stay correct even if submodel indices are sparse or out of order.
    # material_cache lets submodels that share a texture reuse one material.
    material_cache = {}  # texture name -> bpy.types.Material
    obj_by_index = {}
    for sm in model.submodels:
        log.info("Importing submodel %d: '%s' (verts=%d, faces=%d, parent=%d)",
                 sm.index, sm.name, len(sm.vertices), len(sm.faces), sm.parent)
        obj = _import_submodel(
            context, model, sm, collection, search_dirs, tex_report, material_cache
        )
        if obj:
            obj_by_index[sm.index] = obj

    # Set up parent-child relationships
    log.info("Setting up parent-child hierarchy")
    for sm in model.submodels:
        child = obj_by_index.get(sm.index)
        parent = obj_by_index.get(sm.parent)  # sm.parent == -1 (root) -> None
        if child is not None and parent is not None:
            child.parent = parent
            child.matrix_parent_inverse = parent.matrix_world.inverted()
            log.info("  '%s' -> parent index %d", sm.name, sm.parent)

    # Import gun points
    if operator and operator.import_guns:
        log.info("Importing %d gun points", len(model.gun_banks))
        for i, bank in enumerate(model.gun_banks):
            empty = bpy.data.objects.new(f"Gun_{i}", None)
            empty.empty_display_type = "SINGLE_ARROW"
            empty.empty_display_size = 0.3
            empty.location = Vector((bank.point.x, bank.point.y, bank.point.z))
            collection.objects.link(empty)
            # Parent to the gun's parent submodel
            gun_parent = obj_by_index.get(bank.parent)
            if gun_parent is not None:
                empty.parent = gun_parent

    # Import attach points
    if operator and operator.import_attach:
        log.info("Importing %d attach points", len(model.attach_points))
        for i, ap in enumerate(model.attach_points):
            empty = bpy.data.objects.new(f"Attach_{i}", None)
            empty.empty_display_type = "PLAIN_AXES"
            empty.empty_display_size = 0.2
            empty.location = Vector((ap.point.x, ap.point.y, ap.point.z))
            collection.objects.link(empty)
            ap_parent = obj_by_index.get(ap.parent)
            if ap_parent is not None:
                empty.parent = ap_parent

    # Select all imported objects, making the root submodel active.
    for obj in context.view_layer.objects:
        obj.select_set(False)
    imported_objs = list(obj_by_index.values())
    for obj in imported_objs:
        obj.select_set(True)
    root = obj_by_index.get(0)
    if root is None and imported_objs:
        root = imported_objs[0]
    if root is not None:
        context.view_layer.objects.active = root

    # Summarize texture resolution so missing images are visible to the user
    # instead of silently producing blank materials.
    missing = sorted(name for name, found in tex_report.items() if not found)
    found_count = sum(1 for found in tex_report.values() if found)
    total_count = len(tex_report)
    if total_count:
        summary = f"textures {found_count}/{total_count} found"
        if missing:
            shown = ", ".join(missing[:8]) + (" ..." if len(missing) > 8 else "")
            summary += f" (missing: {shown})"
        log.info(summary)
        print(f"[Descent3] {summary}")

    if operator:
        msg = f"Imported {model_name}: {len(model.submodels)} submodels"
        if total_count:
            msg += f", {found_count}/{total_count} textures"
        if missing:
            operator.report(
                {"WARNING"},
                msg + f" — missing textures: {', '.join(missing[:8])}"
                + (" ..." if len(missing) > 8 else ""),
            )
        else:
            operator.report({"INFO"}, msg)

    return {"FINISHED"}


def _get_or_create_material(name, search_dirs, tex_report, material_cache):
    """Return a material for a texture name, creating it (and loading its image)
    once and caching it so submodels sharing a texture reuse the same material.
    """
    cached = material_cache.get(name)
    if cached is not None:
        return cached

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")

    image_path = find_texture_image(name, search_dirs)
    # Record resolution status (first success sticks) for the import summary.
    tex_report[name] = tex_report.get(name, False) or bool(image_path)
    if image_path:
        try:
            img = bpy.data.images.load(image_path, check_existing=True)
            log.info("Loaded image '%s' (%dx%d) for material '%s'",
                     img.name, img.size[0], img.size[1], name)
            tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex_node.image = img
            tex_node.location = (-400, 300)
            mat.node_tree.links.new(
                tex_node.outputs["Color"], bsdf.inputs["Base Color"]
            )
        except Exception as e:
            log.error("Failed to load image '%s': %s", image_path, e)
    else:
        log.info("No image found for texture '%s'; using blank material", name)

    material_cache[name] = mat
    return mat


def _import_submodel(context, model: POFModel, sm: Submodel, collection, search_dirs, tex_report, material_cache) -> bpy.types.Object | None:
    """Import a single submodel as a Blender mesh object."""
    if not sm.vertices:
        # Create an empty for submodels with no geometry
        obj = bpy.data.objects.new(sm.name or f"Submodel_{sm.index}", None)
        obj.location = Vector((sm.offset.x, sm.offset.y, sm.offset.z))
        collection.objects.link(obj)
        return obj

    # Create mesh
    mesh = bpy.data.meshes.new(sm.name or f"Submodel_{sm.index}")

    # Vertices
    verts = [Vector((v.position.x, v.position.y, v.position.z)) for v in sm.vertices]

    # Only faces with 3+ vertices become Blender polygons. Keep this filtered
    # list so UVs and material indices stay aligned with mesh.polygons even when
    # some source faces are dropped.
    kept_faces = [f for f in sm.faces if len(f.vertices) >= 3]
    face_indices = [tuple(fv.index for fv in f.vertices) for f in kept_faces]

    # Build mesh
    mesh.from_pydata(verts, [], face_indices)
    mesh.update()

    # Set UVs (mesh.polygons is 1:1 with kept_faces, in order)
    if kept_faces:
        uv_layer = mesh.uv_layers.new(name="UVMap")
        for poly, pof_face in zip(mesh.polygons, kept_faces):
            for loop_idx, fv in zip(poly.loop_indices, pof_face.vertices):
                if loop_idx < len(uv_layer.data):
                    uv_layer.data[loop_idx].uv = (fv.u, fv.v)

    # Set vertex normals
    if len(sm.vertices) == len(mesh.vertices):
        custom_normals = [
            (v.normal.x, v.normal.y, v.normal.z) for v in sm.vertices
        ]
        try:
            mesh.normals_split_custom_set_from_vertices(custom_normals)
        except Exception:
            pass  # Fallback: let Blender compute normals

    # Create object
    obj = bpy.data.objects.new(sm.name or f"Submodel_{sm.index}", mesh)
    obj.location = Vector((sm.offset.x, sm.offset.y, sm.offset.z))
    collection.objects.link(obj)

    # Build/reuse one material per texture used by this submodel's faces.
    tex_set = {
        f.texnum for f in kept_faces
        if f.textured and 0 <= f.texnum < len(model.textures)
    }
    mat_slots = {}      # texnum -> material slot index on this object
    name_to_slot = {}   # texture name -> slot index (dedupe within this object)
    for tex_idx in sorted(tex_set):
        tex_name = model.textures[tex_idx]
        if tex_name not in name_to_slot:
            mat = _get_or_create_material(
                tex_name, search_dirs, tex_report, material_cache
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

    # Store POF-specific properties as custom properties
    obj["pof_index"] = sm.index
    obj["pof_parent"] = sm.parent
    obj["pof_flags"] = sm.flags
    obj["pof_movement_type"] = sm.movement_type
    obj["pof_movement_axis"] = sm.movement_axis
    if sm.props:
        obj["pof_properties"] = sm.props

    return obj


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ExportPOF(bpy.types.Operator, ExportHelper):
    """Export a Descent 3 POF/OOF model file"""
    bl_idname = "export_scene.descent3_pof"
    bl_label = "Export Descent 3 POF"
    bl_description = "Export the selected objects as a Descent 3 polygon model (.pof)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".pof"
    filter_glob: StringProperty(default="*.pof;*.oof", options={"HIDDEN"})

    export_version: EnumProperty(
        name="POF Version",
        items=[
            ("2300", "v23.00 (Latest)", "Descent 3 retail version"),
            ("2200", "v22.00", "Timed animation support"),
            ("2100", "v21.00", "Lightmap UV support"),
        ],
        default="2300",
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

    def execute(self, context):
        return export_pof(context, self.filepath, self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "export_version")
        layout.prop(self, "export_selected")
        layout.prop(self, "export_guns")
        layout.prop(self, "export_attach")


def export_pof(context, filepath: str, operator=None):
    """Main export function."""
    # Collect objects to export
    if operator and operator.export_selected:
        objects = [obj for obj in context.selected_objects if obj.type == "MESH"]
    else:
        objects = [obj for obj in bpy.data.objects if obj.type == "MESH"]

    if not objects:
        if operator:
            operator.report({"WARNING"}, "No mesh objects to export")
        return {"CANCELLED"}

    # Build POFModel from Blender objects
    model = _build_pof_model(context, objects, operator)

    # Write file
    try:
        data = poformat.write_pof(model)
        with open(filepath, "wb") as f:
            f.write(data)
    except Exception as e:
        if operator:
            operator.report({"ERROR"}, f"Failed to write POF: {e}")
        return {"CANCELLED"}

    if operator:
        operator.report({"INFO"}, f"Exported {len(model.submodels)} submodels")

    return {"FINISHED"}


def _build_pof_model(context, objects: list, operator=None) -> POFModel:
    """Build a POFModel from Blender objects."""
    version = int(operator.export_version) if operator else 2300
    major = version // 100
    timed = major >= 22

    model = POFModel(version=version, major_version=major)
    if major >= 21:
        model.flags |= poformat.PMF_LIGHTMAP_RES
    if timed:
        model.flags |= PMF_TIMED

    # Map object names to indices
    obj_to_index = {}
    for i, obj in enumerate(objects):
        obj_to_index[obj.name] = i

    # Collect textures
    tex_set = set()
    for obj in objects:
        if obj.type != "MESH":
            continue
        for mat in obj.data.materials:
            if mat:
                tex_set.add(mat.name)
    model.textures = sorted(tex_set)
    tex_index = {name: i for i, name in enumerate(model.textures)}

    # Build submodels
    all_verts = []
    all_faces = []
    min_bound = Vector3(1e30, 1e30, 1e30)
    max_bound = Vector3(-1e30, -1e30, -1e30)

    for obj in objects:
        if obj.type != "MESH":
            continue

        sm = Submodel()
        sm.index = obj_to_index[obj.name]
        sm.name = obj.name

        # Get parent index
        if obj.parent and obj.parent.name in obj_to_index:
            sm.parent = obj_to_index[obj.parent.name]
        else:
            sm.parent = -1

        # Get POF-specific properties
        sm.flags = int(obj.get("pof_flags", 0))
        sm.movement_type = int(obj.get("pof_movement_type", -1))
        sm.movement_axis = int(obj.get("pof_movement_axis", 0))
        sm.props = str(obj.get("pof_properties", ""))

        # Get offset from parent (location in local space)
        if obj.parent and obj.parent.type == "MESH":
            local_loc = obj.matrix_local.translation
            sm.offset = Vector3(local_loc.x, local_loc.y, local_loc.z)
        else:
            sm.offset = Vector3(0, 0, 0)

        mesh = obj.data

        # Vertices
        vert_offset = len(sm.vertices)
        for v in mesh.vertices:
            sv = SubmodelVertex()
            sv.position = Vector3(v.co.x, v.co.y, v.co.z)
            sv.normal = Vector3(v.normal.x, v.normal.y, v.normal.z)
            sv.alpha = 1.0
            sm.vertices.append(sv)

            # Update bounds
            if v.co.x < min_bound.x:
                min_bound.x = v.co.x
            if v.co.y < min_bound.y:
                min_bound.y = v.co.y
            if v.co.z < min_bound.z:
                min_bound.z = v.co.z
            if v.co.x > max_bound.x:
                max_bound.x = v.co.x
            if v.co.y > max_bound.y:
                max_bound.y = v.co.y
            if v.co.z > max_bound.z:
                max_bound.z = v.co.z

        # Faces
        uv_layer = mesh.uv_layers.active
        for poly in mesh.polygons:
            face = ModelFace()
            face.normal = Vector3(
                poly.normal.x, poly.normal.y, poly.normal.z
            )

            # Determine texture
            if poly.material_index < len(obj.data.materials):
                mat = obj.data.materials[poly.material_index]
                if mat and mat.name in tex_index:
                    face.textured = True
                    face.texnum = tex_index[mat.name]
                else:
                    face.textured = False
                    face.color = Color(0.5, 0.5, 0.5)
            else:
                face.textured = False
                face.color = Color(0.5, 0.5, 0.5)

            # Face vertices with UVs
            for loop_idx in poly.loop_indices:
                loop = mesh.loops[loop_idx]
                fv = FaceVertex()
                fv.index = vert_offset + loop.vertex_index
                if uv_layer:
                    uv = uv_layer.data[loop_idx].uv
                    fv.u = uv.x
                    fv.v = uv.y
                else:
                    fv.u = 0.0
                    fv.v = 0.0
                face.vertices.append(fv)

            sm.faces.append(face)

        # Compute radius
        for v in sm.vertices:
            dist = v.position.magnitude()
            if dist > sm.radius:
                sm.radius = dist

        model.submodels.append(sm)

    model.min_bound = min_bound
    model.max_bound = max_bound

    # Compute model radius
    for v_pos in [
        min_bound,
        max_bound,
        Vector3(min_bound.x, max_bound.y, max_bound.z),
        Vector3(max_bound.x, min_bound.y, max_bound.z),
        Vector3(max_bound.x, max_bound.y, min_bound.z),
        Vector3(min_bound.x, min_bound.y, max_bound.z),
        Vector3(min_bound.x, max_bound.y, min_bound.z),
        Vector3(max_bound.x, min_bound.y, min_bound.z),
    ]:
        dist = v_pos.magnitude()
        if dist > model.radius:
            model.radius = dist

    # Collect gun points from empties
    if operator and operator.export_guns:
        for obj in bpy.data.objects:
            if obj.type == "EMPTY" and obj.name.startswith("Gun_"):
                bank = GunBank()
                bank.point = Vector3(obj.location.x, obj.location.y, obj.location.z)
                bank.normal = Vector3(0, 0, 1)  # default
                if obj.parent and obj.parent.name in obj_to_index:
                    bank.parent = obj_to_index[obj.parent.name]
                else:
                    bank.parent = 0
                model.gun_banks.append(bank)

    # Collect attach points from empties
    if operator and operator.export_attach:
        for obj in bpy.data.objects:
            if obj.type == "EMPTY" and obj.name.startswith("Attach_"):
                ap = AttachPoint()
                ap.point = Vector3(obj.location.x, obj.location.y, obj.location.z)
                ap.normal = Vector3(0, 0, 1)
                if obj.parent and obj.parent.name in obj_to_index:
                    ap.parent = obj_to_index[obj.parent.name]
                else:
                    ap.parent = 0
                model.attach_points.append(ap)

    # Build hierarchy
    model.build_hierarchy()

    return model


# ---------------------------------------------------------------------------
# Menu Integration
# ---------------------------------------------------------------------------

def menu_func_import(self, context):
    self.layout.operator(ImportPOF.bl_idname, text="Descent 3 POF/OOF (.pof)")


def menu_func_export(self, context):
    self.layout.operator(ExportPOF.bl_idname, text="Descent 3 POF/OOF (.pof)")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    ImportPOF,
    ExportPOF,
)


def register():
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
