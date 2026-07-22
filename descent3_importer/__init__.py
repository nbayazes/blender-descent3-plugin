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

# Relative import for the poformat module (same package)
from . import poformat
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

    def execute(self, context):
        return import_pof(context, self.filepath, self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "import_guns")
        layout.prop(self, "import_attach")


def import_pof(context, filepath: str, operator=None):
    """Main import function."""
    try:
        with open(filepath, "rb") as f:
            data = f.read()
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

    # Create a collection for this model
    model_name = os.path.splitext(os.path.basename(filepath))[0]
    collection = bpy.data.collections.new(model_name)
    context.scene.collection.children.link(collection)

    # Import each submodel
    obj_list = []
    for sm in model.submodels:
        obj = _import_submodel(context, model, sm, collection)
        if obj:
            obj_list.append(obj)

    # Set up parent-child relationships
    for sm in model.submodels:
        if sm.index < len(obj_list) and obj_list[sm.index] is not None:
            if 0 <= sm.parent < len(obj_list) and obj_list[sm.parent] is not None:
                obj_list[sm.index].parent = obj_list[sm.parent]
                obj_list[sm.index].matrix_parent_inverse = (
                    obj_list[sm.parent].matrix_world.inverted()
                )

    # Import gun points
    if operator and operator.import_guns:
        for i, bank in enumerate(model.gun_banks):
            empty = bpy.data.objects.new(f"Gun_{i}", None)
            empty.empty_display_type = "SINGLE_ARROW"
            empty.empty_display_size = 0.3
            empty.location = Vector((bank.point.x, bank.point.y, bank.point.z))
            collection.objects.link(empty)
            # Parent to the gun's parent submodel
            if 0 <= bank.parent < len(obj_list) and obj_list[bank.parent] is not None:
                empty.parent = obj_list[bank.parent]

    # Import attach points
    if operator and operator.import_attach:
        for i, ap in enumerate(model.attach_points):
            empty = bpy.data.objects.new(f"Attach_{i}", None)
            empty.empty_display_type = "PLAIN_AXES"
            empty.empty_display_size = 0.2
            empty.location = Vector((ap.point.x, ap.point.y, ap.point.z))
            collection.objects.link(empty)
            if 0 <= ap.parent < len(obj_list) and obj_list[ap.parent] is not None:
                empty.parent = obj_list[ap.parent]

    # Select all imported objects
    bpy.ops.object.select_all(action="DESELECT")
    for obj in obj_list:
        if obj:
            obj.select_set(True)
    if obj_list and obj_list[0]:
        context.view_layer.objects.active = obj_list[0]

    if operator:
        operator.report({"INFO"}, f"Imported {model_name}: {len(model.submodels)} submodels")

    return {"FINISHED"}


def _import_submodel(context, model: POFModel, sm: Submodel, collection) -> bpy.types.Object | None:
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

    # Faces (list of vertex index tuples)
    face_indices = []
    for face in sm.faces:
        if len(face.vertices) >= 3:
            face_indices.append(tuple(fv.index for fv in face.vertices))

    # Build mesh
    mesh.from_pydata(verts, [], face_indices)
    mesh.update()

    # Set UVs
    if any(sm.faces):
        uv_layer = mesh.uv_layers.new(name="UVMap")
        for face_idx, pof_face in enumerate(sm.faces):
            if face_idx >= len(mesh.polygons):
                break
            blender_face = mesh.polygons[face_idx]
            for loop_idx, fv in zip(blender_face.loop_indices, pof_face.vertices):
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

    # Set materials from face texture references
    tex_set = set()
    for face in sm.faces:
        if face.textured and 0 <= face.texnum < len(model.textures):
            tex_set.add(face.texnum)

    for tex_idx in sorted(tex_set):
        tex_name = model.textures[tex_idx]
        mat = bpy.data.materials.new(name=tex_name)
        obj.data.materials.append(mat)

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
