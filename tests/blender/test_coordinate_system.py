"""
Blender-side coordinate-system check.

Descent 3 is right-handed **Y-up**; Blender is right-handed **Z-up**. Import
rotates 90 degrees about X, export rotates back. Both spaces being right-handed
means this is a rotation and not a mirror, so winding order is untouched and
normals transform exactly like positions.

pytest covers the maths in tests/test_mathutil.py. What it cannot reach is
whether the conversion is actually *applied* at every point where data crosses
into or out of Blender -- a missed call site is invisible to a unit test and
shows up as a model lying on its side.

The object transforms Blender keeps *outside* the mesh are checked here for the
same reason. Rotation, scale and parenting live on the object, not in
``obj.data``, and a `mathutils` matrix is not something a bpy-free unit test can
exercise -- so an exporter reading raw local coordinates loses all three
silently and only the game shows it.

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_coordinate_system.py

Exits non-zero on failure so it can be wired into CI.
"""

import math
import os
import shutil
import sys
import tempfile

import bpy
from mathutils import Vector

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from descent3_plugin import poformat  # noqa: E402
from descent3_plugin.export_pof import save_pof  # noqa: E402
from descent3_plugin.import_pof import load_pof  # noqa: E402
from descent3_plugin.mathutil import (  # noqa: E402
    Vector3,
    blender_to_descent,
    descent_to_blender,
)

FIXTURE_DIR = os.path.join(REPO, "tests", "fixtures", "textured_model")

failures = []
TOL = 1e-5


def check(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (f"  {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def close(a, b):
    return all(abs(x - y) <= TOL for x, y in zip(a, b))


class FakeImportOp:
    import_guns = import_attach = True
    texture_dir = ""

    def report(self, level, msg):
        pass


class FakeExportOp:
    export_selected = False
    export_guns = export_attach = True
    export_version = "CONFIG"

    def report(self, level, msg):
        pass


def wipe():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials,
                 bpy.data.images):
        for item in list(coll):
            coll.remove(item)
    bpy.context.view_layer.update()


work = tempfile.mkdtemp(prefix="d3axis_")
for fn in os.listdir(FIXTURE_DIR):
    shutil.copy(os.path.join(FIXTURE_DIR, fn), os.path.join(work, fn))
MODEL = os.path.join(work, "textured_cube.oof")

source = poformat.parse_pof(open(MODEL, "rb").read())
src_sm = source.submodels[0]

# --- 1. a Descent vertex must land where the rotation says --------------
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
mesh_obj = next(o for o in bpy.data.objects if o.type == "MESH")
mesh = mesh_obj.data

check("vertex count survives import",
      len(mesh.vertices) == len(src_sm.vertices),
      f"{len(mesh.vertices)} vs {len(src_sm.vertices)}")

mismatched = []
for i, sv in enumerate(src_sm.vertices):
    expected = descent_to_blender(sv.position).as_tuple()
    actual = tuple(mesh.vertices[i].co)
    if not close(expected, actual):
        mismatched.append((i, sv.position.as_tuple(), expected, actual))

check("every vertex is rotated Y-up -> Z-up on import",
      not mismatched,
      "" if not mismatched else f"{len(mismatched)} wrong, first: {mismatched[0]}")

# Spell the first vertex out, so a failure shows the actual numbers.
v0 = src_sm.vertices[0].position
print(f"      vertex 0: descent {v0.as_tuple()} -> blender {tuple(mesh.vertices[0].co)}")

# --- 2. the conversion must not be a mirror -----------------------------
# Volume is preserved by a rotation; a mirror negates it. Compare the mesh's
# bounding-box volume against the source's.
def bbox_volume(points):
    xs = [p[0] for p in points]; ys = [p[1] for p in points]; zs = [p[2] for p in points]
    return ((max(xs) - min(xs)) * (max(ys) - min(ys)) * (max(zs) - min(zs)))

src_pts = [v.position.as_tuple() for v in src_sm.vertices]
bl_pts = [tuple(v.co) for v in mesh.vertices]
check("bounding volume is preserved (rotation, not scale)",
      abs(bbox_volume(src_pts) - bbox_volume(bl_pts)) <= 1e-4,
      f"{bbox_volume(src_pts):.6f} vs {bbox_volume(bl_pts):.6f}")

# --- 3. positions must survive the full round trip exactly --------------
out = os.path.join(work, "roundtrip.pof")
save_pof(bpy.context, out, FakeExportOp())
result = poformat.parse_pof(open(out, "rb").read())
res_sm = result.submodels[0]

check("vertex count survives export",
      len(res_sm.vertices) == len(src_sm.vertices),
      f"{len(res_sm.vertices)} vs {len(src_sm.vertices)}")

drifted = []
for i, (a, b) in enumerate(zip(src_sm.vertices, res_sm.vertices)):
    if not close(a.position.as_tuple(), b.position.as_tuple()):
        drifted.append((i, a.position.as_tuple(), b.position.as_tuple()))

check("import then export returns the original coordinates",
      not drifted,
      "" if not drifted else f"{len(drifted)} drifted, first: {drifted[0]}")

# --- 4. gun and attach points make the trip too -------------------------
# These cross the boundary through a different code path than vertices do, so
# a conversion missing here would not show up in the geometry checks above.
if source.gun_banks:
    gun_drift = [
        (i, a.point.as_tuple(), b.point.as_tuple())
        for i, (a, b) in enumerate(zip(source.gun_banks, result.gun_banks))
        if not close(a.point.as_tuple(), b.point.as_tuple())
    ]
    check("gun points round-trip", not gun_drift, str(gun_drift[:1]))
else:
    # The fixture has none, so make one in Blender and check it lands correctly.
    wipe()
    load_pof(bpy.context, MODEL, FakeImportOp())
    parent = next(o for o in bpy.data.objects if o.type == "MESH")
    marker = bpy.data.objects.new("Gun_0", None)
    marker.location = (1.0, 2.0, 3.0)
    marker.parent = parent
    bpy.context.scene.collection.objects.link(marker)

    out2 = os.path.join(work, "gun.pof")
    save_pof(bpy.context, out2, FakeExportOp())
    with_gun = poformat.parse_pof(open(out2, "rb").read())
    check("a gun point was exported", len(with_gun.gun_banks) == 1,
          f"{len(with_gun.gun_banks)}")
    if with_gun.gun_banks:
        expected = blender_to_descent(Vector3(1.0, 2.0, 3.0)).as_tuple()
        actual = with_gun.gun_banks[0].point.as_tuple()
        check("gun point is converted Z-up -> Y-up on export",
              close(expected, actual), f"expected {expected}, got {actual}")

        # And back again: re-importing must put the marker where it started.
        wipe()
        load_pof(bpy.context, out2, FakeImportOp())
        back = next((o for o in bpy.data.objects
                     if o.type == "EMPTY" and o.name.startswith("Gun_")), None)
        check("gun point returns to its Blender position",
              back is not None and close(tuple(back.location), (1.0, 2.0, 3.0)),
              str(tuple(back.location)) if back else "no marker imported")

# --- 5. a marker parented the way Blender's Ctrl+P does -----------------
# Section 4 parents with a bare ``marker.parent = parent``, which leaves
# matrix_parent_inverse at identity -- the one arrangement in which
# ``obj.location`` happens to be the world position as well, and therefore the
# one arrangement that cannot see this bug. OBJECT_OT_parent_set stores the
# parent's inverted world matrix there so the child does not jump when it is
# parented, and from then on ``obj.location`` is measured in a space that is
# neither the parent's nor the world's. Exporting it verbatim is what put a gun
# several units from where the user dropped it.
#
# The parent is rotated as well as moved, because what the file wants is a plain
# world-space delta: MinMaxSubmodel in reference/polymodel.cpp accumulates
# sm->offset down the hierarchy with vector adds and never rotates a child by
# its parent. Resolving the marker into the parent's local space would be wrong,
# even though it is the more obvious reading of "offset from the parent".
PARENT_LOCATION = (10.0, -5.0, 2.0)
PARENT_ROTATION_Z = math.radians(35.0)
MARKER_LOCATION = (1.0, 2.0, 3.0)

wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
parent = next(o for o in bpy.data.objects if o.type == "MESH")
parent.location = PARENT_LOCATION
parent.rotation_euler = (0.0, 0.0, PARENT_ROTATION_Z)
# matrix_world is stale until the depsgraph runs, and Ctrl+P reads
# matrix_parent_inverse straight off it -- so does the exporter.
bpy.context.view_layer.update()

marker = bpy.data.objects.new("Gun_0", None)
bpy.context.scene.collection.objects.link(marker)
marker.location = MARKER_LOCATION
marker.parent = parent
marker.matrix_parent_inverse = parent.matrix_world.inverted()
bpy.context.view_layer.update()

check("Ctrl+P parenting leaves the marker where the user put it",
      close(tuple(marker.matrix_world.translation), MARKER_LOCATION),
      str(tuple(marker.matrix_world.translation)))

out3 = os.path.join(work, "parented_gun.pof")
save_pof(bpy.context, out3, FakeExportOp())
parented = poformat.parse_pof(open(out3, "rb").read())

check("the parented gun point was exported", len(parented.gun_banks) == 1,
      f"{len(parented.gun_banks)}")
if parented.gun_banks:
    expected = blender_to_descent(
        Vector3(*MARKER_LOCATION) - Vector3(*PARENT_LOCATION)
    ).as_tuple()
    actual = parented.gun_banks[0].point.as_tuple()
    check("gun point exports as a world-space delta from its parent's origin",
          close(expected, actual), f"expected {expected}, got {actual}")

    # The point of the whole exercise: it comes back where the user left it.
    wipe()
    load_pof(bpy.context, out3, FakeImportOp())
    bpy.context.view_layer.update()
    back = next((o for o in bpy.data.objects
                 if o.type == "EMPTY" and o.name.startswith("Gun_")), None)
    check("a Ctrl+P parented gun point returns to the same world position",
          back is not None
          and close(tuple(back.matrix_world.translation), MARKER_LOCATION),
          str(tuple(back.matrix_world.translation)) if back
          else "no marker imported")

# --- 6. an object's own rotation and scale must reach the file ----------
# Submodel geometry used to be written straight out of obj.data, which is the
# object's *local* space: rotating or scaling a submodel in Blender changed
# nothing in the exported model, and the user found out in the game. Export
# bakes the object's world matrix in and keeps the geometry centred on the
# submodel's own origin, so the offset carries the placement and the vertices
# carry the shape.
#
# The scale is non-uniform on purpose. Positions transform by the matrix and
# normals by its inverse transpose; under a uniform scale the two agree, so a
# uniform one would let a wrong normal transform pass.
OBJECT_LOCATION = (3.0, -1.0, 0.5)
OBJECT_ROTATION = (math.radians(25.0), math.radians(-40.0), math.radians(70.0))
OBJECT_SCALE = (2.0, 0.5, 1.5)

wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
placed = next(o for o in bpy.data.objects if o.type == "MESH")
placed.location = OBJECT_LOCATION
placed.rotation_euler = OBJECT_ROTATION
placed.scale = OBJECT_SCALE
bpy.context.view_layer.update()

out4 = os.path.join(work, "transformed.pof")
save_pof(bpy.context, out4, FakeExportOp())
transformed = poformat.parse_pof(open(out4, "rb").read())
tf_sm = transformed.submodels[0]

world = placed.matrix_world.copy()
expected_positions = [
    blender_to_descent((world @ v.co) - world.translation).as_tuple()
    for v in placed.data.vertices
]

# res_sm is this same fixture exported untransformed, back in section 3. Were
# the transform above ever weakened to an identity, every check below would
# still pass against an exporter that ignores the object entirely -- so prove
# first that the expected values are not simply the untransformed ones.
check("the test transform really does move the geometry",
      any(not close(e, v.position.as_tuple())
          for e, v in zip(expected_positions, res_sm.vertices)))

moved = [
    (i, e, v.position.as_tuple())
    for i, (e, v) in enumerate(zip(expected_positions, tf_sm.vertices))
    if not close(e, v.position.as_tuple())
]
check("rotation and scale are baked into the exported vertices",
      not moved,
      "" if not moved else f"{len(moved)} wrong, first: {moved[0]}")

check("the object's world position becomes the submodel offset",
      close(blender_to_descent(world.translation).as_tuple(),
            tf_sm.offset.as_tuple()),
      f"expected {blender_to_descent(world.translation).as_tuple()}, "
      f"got {tf_sm.offset.as_tuple()}")

# Normals take the inverse transpose, or a non-uniform scale tips them off the
# surface. The expected values are derived from the untransformed export in
# section 3 rather than from the mesh, so this stays honest whichever normals
# export reads -- authored custom split normals or MeshVertex.normal. What is
# being checked is that the world transform reached them at all, not which
# layer they came out of.
normal_matrix = world.to_3x3().inverted().transposed()
expected_normals = [
    blender_to_descent(
        (normal_matrix @ Vector(descent_to_blender(v.normal).as_tuple()))
        .normalized()
    ).as_tuple()
    for v in res_sm.vertices
]
bent = [
    (i, e, v.normal.as_tuple())
    for i, (e, v) in enumerate(zip(expected_normals, tf_sm.vertices))
    if not close(e, v.normal.as_tuple())
]
check("normals are transformed by the inverse transpose of the world matrix",
      not bent,
      "" if not bent else f"{len(bent)} wrong, first: {bent[0]}")

# --- 6b. modifiers have to reach the file too ---------------------------
# The world matrix and the evaluated mesh are two separate halves of "export
# what the viewport shows", and section 6 only exercises the first: an exporter
# reading ``obj.data`` through ``matrix_world`` passes every check above and
# still writes half a ship for anything with a Mirror on it. A modifier is added
# here rather than baked into a fixture, so the fixture stays the plain model
# every other section compares against.
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
modified = next(o for o in bpy.data.objects if o.type == "MESH")
before_vertices = len(modified.data.vertices)
mirror = modified.modifiers.new(name="Mirror", type="MIRROR")
mirror.use_axis = (True, False, False)
# Offset the mesh clear of the mirror plane, or the halves weld and the vertex
# count does not move -- which would make this check pass vacuously.
for v in modified.data.vertices:
    v.co.x += 5.0
bpy.context.view_layer.update()

out8 = os.path.join(work, "modified.pof")
save_pof(bpy.context, out8, FakeExportOp())
mirrored = poformat.parse_pof(open(out8, "rb").read())
check("a modifier's geometry reaches the exported file",
      len(mirrored.submodels[0].vertices) == before_vertices * 2,
      f"{len(mirrored.submodels[0].vertices)} vertices exported, "
      f"{before_vertices} in the unevaluated mesh")
check("and the mirrored half is really on the other side",
      min(v.position.x for v in mirrored.submodels[0].vertices) < 0
      < max(v.position.x for v in mirrored.submodels[0].vertices),
      f"x range {min(v.position.x for v in mirrored.submodels[0].vertices)} .. "
      f"{max(v.position.x for v in mirrored.submodels[0].vertices)}")

# --- 7. a marker's direction must survive the round trip ----------------
# A gun bank's normal is where it fires. Import parsed it and dropped it, and
# export wrote one hardcoded placeholder for every marker, so a model that made
# the trip came back with every gun on the ship aimed the same way -- and
# nothing said so, because the positions were all still right.
#
# The directions below are deliberately all different: an exporter that writes a
# constant passes a one-marker test.
GUN_DIRECTIONS = [
    (0.0, 0.0, 1.0),
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.6, 0.8, 0.0),
    (-0.3, 0.4, -0.866025),
]
#: Attach points additionally carry an up vector, fixing their roll about the
#: direction. NATH is all-or-nothing per model -- the parser only accepts a count
#: equal to the attach-point count -- so a file either fixes every roll or none.
ATTACH_FRAMES = [
    ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
]


def marker_model():
    """Build a model carrying gun points and attach points with known frames."""
    model = poformat.POFModel(version=2300, major_version=23)
    sm = poformat.Submodel()
    sm.index = 0
    sm.name = "Root"
    sm.parent = poformat.NO_PARENT
    for corner in ((-1, -1, -1), (1, -1, -1), (1, 1, 1)):
        sv = poformat.SubmodelVertex()
        sv.position = Vector3(*corner)
        sv.normal = Vector3(0, 1, 0)
        sv.alpha = 1.0
        sm.vertices.append(sv)
    model.submodels.append(sm)
    for i, direction in enumerate(GUN_DIRECTIONS):
        model.gun_banks.append(
            poformat.GunBank(parent=0, point=Vector3(i, 0, 2),
                             normal=Vector3(*direction))
        )
    for i, (direction, up) in enumerate(ATTACH_FRAMES):
        ap = poformat.AttachPoint(parent=0, point=Vector3(0, i * 3, 0),
                                  normal=Vector3(*direction))
        ap.has_uvec = True
        ap.uvec = Vector3(*up)
        model.attach_points.append(ap)
    model.build_hierarchy()
    return model


aimed = os.path.join(work, "aimed.pof")
with open(aimed, "wb") as f:
    f.write(poformat.write_pof(marker_model()))
# Read back through the parser, not from the model above: these are the values
# the importer is actually handed.
aimed_src = poformat.parse_pof(open(aimed, "rb").read())

wipe()
load_pof(bpy.context, aimed, FakeImportOp())
bpy.context.view_layer.update()

turned = [o for o in bpy.data.objects
          if o.type == "EMPTY" and o.name.startswith("Gun_")
          and tuple(o.rotation_euler) != (0.0, 0.0, 0.0)]
check("import puts the direction somewhere the user can see it",
      len(turned) >= len(GUN_DIRECTIONS) - 1,
      f"{len(turned)} of {len(GUN_DIRECTIONS)} markers are rotated")

out5 = os.path.join(work, "aimed_back.pof")
save_pof(bpy.context, out5, FakeExportOp())
aimed_back = poformat.parse_pof(open(out5, "rb").read())

check("every gun point survives", len(aimed_back.gun_banks) == len(GUN_DIRECTIONS),
      f"{len(aimed_back.gun_banks)}")
misaimed = [
    (i, a.normal.as_tuple(), b.normal.as_tuple())
    for i, (a, b) in enumerate(zip(aimed_src.gun_banks, aimed_back.gun_banks))
    if not close(a.normal.as_tuple(), b.normal.as_tuple())
]
check("a gun point is exported facing where the file aimed it",
      not misaimed,
      "" if not misaimed else f"{len(misaimed)} wrong, first: {misaimed[0]}")

check("every attach point survives",
      len(aimed_back.attach_points) == len(ATTACH_FRAMES),
      f"{len(aimed_back.attach_points)}")
unrolled = [
    (i, a.normal.as_tuple(), b.normal.as_tuple(), a.uvec.as_tuple(),
     b.uvec.as_tuple(), a.has_uvec, b.has_uvec)
    for i, (a, b) in enumerate(zip(aimed_src.attach_points,
                                   aimed_back.attach_points))
    if not (close(a.normal.as_tuple(), b.normal.as_tuple())
            and a.has_uvec == b.has_uvec
            and close(a.uvec.as_tuple(), b.uvec.as_tuple()))
]
check("an attach point keeps its facing and its roll",
      not unrolled,
      "" if not unrolled else f"{len(unrolled)} wrong, first: {unrolled[0]}")

# And the direction really is read off the empty, rather than stashed on import
# and copied back: turning the marker in Blender must turn the gun in the file.
gun0 = next(o for o in bpy.data.objects
            if o.type == "EMPTY" and o.name == "Gun_0")
gun0.rotation_euler = (math.radians(-90.0), 0.0, 0.0)
bpy.context.view_layer.update()
out6 = os.path.join(work, "turned.pof")
save_pof(bpy.context, out6, FakeExportOp())
turned_back = poformat.parse_pof(open(out6, "rb").read())
# The marker's forward axis is local +Z. Turned -90 degrees about X it points
# along Blender +Y, which is Descent -Z -- the exact opposite of the (0, 0, 1)
# this marker came out of the file with, so a stale value cannot pass.
TURNED_DIRECTION = (0.0, 0.0, -1.0)
check("the marker's own direction is the one it started with",
      close(aimed_src.gun_banks[0].normal.as_tuple(),
            tuple(-c for c in TURNED_DIRECTION)),
      str(aimed_src.gun_banks[0].normal.as_tuple()))
check("rotating the empty in Blender turns the gun in the file",
      close(turned_back.gun_banks[0].normal.as_tuple(), TURNED_DIRECTION),
      str(turned_back.gun_banks[0].normal.as_tuple()))

# --- 8. offsets accumulate down the hierarchy ---------------------------
# The engine places a submodel by summing sm->offset from the root down
# (reference/polymodel.cpp, MinMaxSubmodel: ``offset += sm->offset``, a plain
# vector add). Import mirrors that by leaving matrix_parent_inverse at identity,
# so a child's world position is its parent's plus its own location, and export
# writes world-space deltas back. Get either half wrong and one level still
# looks right -- it takes three to show it, which is why this is not folded into
# the two-deep checks above.
CHILD_OFFSET = (10.0, 0.0, 0.0)
GRAND_OFFSET = (0.0, 5.0, 0.0)


def hierarchy_model():
    """Build a root -> child -> grandchild chain with known offsets."""
    model = poformat.POFModel(version=2300, major_version=23)
    for index, (name, parent, offset) in enumerate([
        ("Root", poformat.NO_PARENT, (0.0, 0.0, 0.0)),
        ("Child", 0, CHILD_OFFSET),
        ("Grand", 1, GRAND_OFFSET),
    ]):
        sm = poformat.Submodel()
        sm.index = index
        sm.name = name
        sm.parent = parent
        sm.offset = Vector3(*offset)
        for corner in ((-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, 0.5)):
            sv = poformat.SubmodelVertex()
            sv.position = Vector3(*corner)
            sv.normal = Vector3(0, 1, 0)
            sv.alpha = 1.0
            sm.vertices.append(sv)
        model.submodels.append(sm)
    model.build_hierarchy()
    return model


deep = os.path.join(work, "deep.pof")
with open(deep, "wb") as f:
    f.write(poformat.write_pof(hierarchy_model()))

wipe()
load_pof(bpy.context, deep, FakeImportOp())
bpy.context.view_layer.update()

by_name = {o.name: o for o in bpy.data.objects}
expected_world = descent_to_blender(
    Vector3(*CHILD_OFFSET) + Vector3(*GRAND_OFFSET)
).as_tuple()
check("a grandchild is imported at its accumulated world position",
      close(tuple(by_name["Grand"].matrix_world.translation), expected_world),
      f"expected {expected_world}, "
      f"got {tuple(by_name['Grand'].matrix_world.translation)}")
check("and its own location is still the offset the file stated",
      close(tuple(by_name["Grand"].location),
            descent_to_blender(Vector3(*GRAND_OFFSET)).as_tuple()),
      str(tuple(by_name["Grand"].location)))

out7 = os.path.join(work, "deep_back.pof")
save_pof(bpy.context, out7, FakeExportOp())
deep_back = poformat.parse_pof(open(out7, "rb").read())
back_by_name = {sm.name: sm for sm in deep_back.submodels}
check("a three-deep hierarchy exports the offsets it was imported with",
      close(back_by_name["Child"].offset.as_tuple(), CHILD_OFFSET)
      and close(back_by_name["Grand"].offset.as_tuple(), GRAND_OFFSET),
      f"Child {back_by_name['Child'].offset.as_tuple()}, "
      f"Grand {back_by_name['Grand'].offset.as_tuple()}")

# The model box has to contain the grandchild where it actually ends up, not
# where its own vertices sit relative to itself.
far_corner = (
    CHILD_OFFSET[0] + GRAND_OFFSET[0] + 0.5,
    CHILD_OFFSET[1] + GRAND_OFFSET[1] + 0.5,
    CHILD_OFFSET[2] + GRAND_OFFSET[2] + 0.5,
)
check("the model bounding box reaches the furthest submodel",
      close(deep_back.max_bound.as_tuple(), far_corner),
      f"expected {far_corner}, got {deep_back.max_bound.as_tuple()}")

# --- 9. a mirrored object must not export inside out --------------------
# Duplicating a wing and scaling it -1 on X is how a left wing gets built, and
# it is the one transform that reverses orientation. Baking matrix_world into
# the vertices moves the surface but leaves each face's corners in the order
# they had before the mirror, while the normal -- through the inverse transpose
# -- turns round with the surface. The two then disagree, and the part renders
# inside out in the game while looking perfect in Blender.
#
# Checked by recomputing each exported face's normal from its own stored
# vertices and comparing it against the normal stored beside them: they have to
# agree, on the mirrored object exactly as on its un-mirrored twin.
def winding_disagreements(sm):
    """Return how many of a submodel's faces are wound against their normal."""
    wrong = 0
    for face in sm.faces:
        if len(face.vertices) < 3:
            continue
        p = [sm.vertices[fv.index].position for fv in face.vertices[:3]]
        a = Vector((p[1].x - p[0].x, p[1].y - p[0].y, p[1].z - p[0].z))
        b = Vector((p[2].x - p[0].x, p[2].y - p[0].y, p[2].z - p[0].z))
        wound = a.cross(b)
        if wound.length <= TOL:
            continue
        n = Vector((face.normal.x, face.normal.y, face.normal.z))
        if wound.normalized().dot(n) < 0:
            wrong += 1
    return wrong


wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
original = next(o for o in bpy.data.objects if o.type == "MESH")
original.name = "WingR"
mirror = bpy.data.objects.new("WingL", original.data)
mirror.location = (-2.0, 0.0, 0.0)
mirror.scale = (-1.0, 1.0, 1.0)
bpy.context.scene.collection.objects.link(mirror)
bpy.context.view_layer.update()

out8 = os.path.join(work, "mirrored.pof")
save_pof(bpy.context, out8, FakeExportOp())
mirrored_model = poformat.parse_pof(open(out8, "rb").read())
by_sm = {sm.name: sm for sm in mirrored_model.submodels}
check("an un-mirrored object's winding agrees with its normals",
      winding_disagreements(by_sm["WingR"]) == 0,
      f"{winding_disagreements(by_sm['WingR'])} of {len(by_sm['WingR'].faces)}")
check("a negatively scaled object's winding agrees with its normals",
      winding_disagreements(by_sm["WingL"]) == 0,
      f"{winding_disagreements(by_sm['WingL'])} of {len(by_sm['WingL'].faces)}")

# --- 10. a submodel with no geometry is a joint, not a mistake -----------
# A turret's pivot has no vertices: it exists to carry ``$rotate=`` and to be
# the thing the engine turns the turret about. Import makes it an Empty, and
# exporting only meshes dropped it and re-rooted its children onto the hull --
# the geometry still landed in the right place, so nothing looked wrong in
# Blender and the turret simply stopped moving in the game.
PIVOT_OFFSET = (0.0, 0.0, 4.0)
TURRET_OFFSET = (0.0, 2.0, 0.0)
PIVOT_PROPS = "$rotate=2.5"


def jointed_model():
    """Build hull -> pivot (no vertices) -> turret."""
    model = poformat.POFModel(version=2300, major_version=23)
    for index, (name, parent, offset, verts) in enumerate([
        ("Hull", poformat.NO_PARENT, (0.0, 6.0, 0.0), True),
        ("Pivot", 0, PIVOT_OFFSET, False),
        ("Turret", 1, TURRET_OFFSET, True),
    ]):
        sm = poformat.Submodel()
        sm.index = index
        sm.name = name
        sm.parent = parent
        sm.offset = Vector3(*offset)
        if not verts:
            sm.props = PIVOT_PROPS
            sm.movement_type = 1
            sm.movement_axis = 2
        for corner in ((-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, 0.5)):
            if not verts:
                break
            sv = poformat.SubmodelVertex()
            sv.position = Vector3(*corner)
            sv.normal = Vector3(0, 1, 0)
            sv.alpha = 1.0
            sm.vertices.append(sv)
        model.submodels.append(sm)
    model.gun_banks.append(
        poformat.GunBank(parent=1, point=Vector3(0.0, 1.0, 0.0),
                         normal=Vector3(0.0, 0.0, 1.0))
    )
    model.build_hierarchy()
    return model


jointed = os.path.join(work, "jointed.pof")
with open(jointed, "wb") as f:
    f.write(poformat.write_pof(jointed_model()))

wipe()
load_pof(bpy.context, jointed, FakeImportOp())
bpy.context.view_layer.update()
pivot_obj = bpy.data.objects.get("Pivot")
check("a geometry-less submodel imports as an Empty",
      pivot_obj is not None and pivot_obj.type == "EMPTY",
      pivot_obj.type if pivot_obj else "missing")
check("and it keeps the fields that make it a joint",
      pivot_obj is not None and pivot_obj.get("pof_properties") == PIVOT_PROPS
      and pivot_obj.get("pof_movement_type") == 1
      and pivot_obj.get("pof_movement_axis") == 2,
      str(sorted(pivot_obj.keys())) if pivot_obj else "")

out9 = os.path.join(work, "jointed_back.pof")
save_pof(bpy.context, out9, FakeExportOp())
jointed_back = poformat.parse_pof(open(out9, "rb").read())
joint_by_name = {sm.name: sm for sm in jointed_back.submodels}
check("the joint survives the round trip",
      "Pivot" in joint_by_name, str(sorted(joint_by_name)))
if "Pivot" in joint_by_name:
    check("with its offset, movement and properties intact",
          close(joint_by_name["Pivot"].offset.as_tuple(), PIVOT_OFFSET)
          and joint_by_name["Pivot"].props == PIVOT_PROPS
          and joint_by_name["Pivot"].movement_type == 1
          and joint_by_name["Pivot"].movement_axis == 2,
          f"offset {joint_by_name['Pivot'].offset.as_tuple()}, "
          f"props {joint_by_name['Pivot'].props!r}")
    check("and the turret is still parented to it, not to the hull",
          joint_by_name["Turret"].parent == joint_by_name["Pivot"].index,
          f"parent {joint_by_name['Turret'].parent}, "
          f"pivot is {joint_by_name['Pivot'].index}")
    check("a marker on the joint stays on the joint",
          jointed_back.gun_banks
          and jointed_back.gun_banks[0].parent == joint_by_name["Pivot"].index,
          str([(g.parent, g.point.as_tuple()) for g in jointed_back.gun_banks]))


# --- 11. a marker with no exported parent ------------------------------
# It cannot say "attached to the model itself" -- the format has no such value
# -- so it is attached to a root submodel. That makes the frame it is measured
# from the root submodel's origin, not the world origin: the engine adds the
# root's offset back on (GetPolyModelPointInWorld walks from the marker's parent
# submodel up the chain), so measuring from the world moved the gun by exactly
# that offset. Reachable without hand-authoring: Selected Only, with the marker
# selected and its parent mesh not.
def engine_position(model, parent, point):
    """Where the engine puts a marker: its point plus every offset above it."""
    x, y, z = point.as_tuple()
    node, seen = parent, set()
    while node != poformat.NO_PARENT and node not in seen \
            and 0 <= node < len(model.submodels):
        seen.add(node)
        offset = model.submodels[node].offset
        x, y, z = x + offset.x, y + offset.y, z + offset.z
        node = model.submodels[node].parent
    return (x, y, z)


ROOT_LOCATION = (2.0, 3.0, 4.0)
LOOSE_MARKER_LOCATION = (1.0, 0.0, 5.0)

wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
root_obj = next(o for o in bpy.data.objects if o.type == "MESH")
root_obj.location = ROOT_LOCATION
loose = bpy.data.objects.new("Gun_0", None)
loose.location = LOOSE_MARKER_LOCATION
bpy.context.scene.collection.objects.link(loose)
bpy.context.view_layer.update()

out10 = os.path.join(work, "loose_marker.pof")
save_pof(bpy.context, out10, FakeExportOp())
loose_model = poformat.parse_pof(open(out10, "rb").read())
check("an unparented marker is still exported", len(loose_model.gun_banks) == 1,
      str(len(loose_model.gun_banks)))
if loose_model.gun_banks:
    landed = engine_position(loose_model, loose_model.gun_banks[0].parent,
                             loose_model.gun_banks[0].point)
    wanted = blender_to_descent(Vector3(*LOOSE_MARKER_LOCATION)).as_tuple()
    check("and the engine puts it where the empty actually is",
          close(landed, wanted),
          f"expected {wanted}, got {landed} "
          f"(root offset {loose_model.submodels[0].offset.as_tuple()})")

# --- 12. marker order is data, not decoration ---------------------------
# WBAT cites gun banks by index, and Descent 3 bolts hardware to specific attach
# point indices. Export finds markers by walking bpy.data.objects, which Blender
# sorts as text, so from ten markers up ``Gun_10`` falls between ``Gun_1`` and
# ``Gun_2`` and the banks come out permuted. Under ten the two orders agree,
# which is why this needs twelve.
MANY = 12

wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
host = next(o for o in bpy.data.objects if o.type == "MESH")
for i in range(MANY):
    marker = bpy.data.objects.new(f"Gun_{i}", None)
    marker.location = (float(i), 0.0, 0.0)
    marker.parent = host
    bpy.context.scene.collection.objects.link(marker)
bpy.context.view_layer.update()

out11 = os.path.join(work, "many_markers.pof")
save_pof(bpy.context, out11, FakeExportOp())
many = poformat.parse_pof(open(out11, "rb").read())
exported_order = [round(g.point.x, 3) for g in many.gun_banks]
check("gun banks are exported in name order, not text order",
      exported_order == [float(i) for i in range(MANY)],
      str(exported_order))

# --- 13. "up" really is up ------------------------------------------------
# The one assertion a human can sanity-check by eye: a model's up axis in
# Descent must point up in Blender.
check("Descent +Y (up) imports as Blender +Z (up)",
      descent_to_blender(Vector3(0, 1, 0)).as_tuple() == (0, 0, 1))
check("Blender +Z (up) exports as Descent +Y (up)",
      blender_to_descent(Vector3(0, 0, 1)).as_tuple() == (0, 1, 0))

shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print("FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("all coordinate-system checks passed")
