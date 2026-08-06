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

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_coordinate_system.py

Exits non-zero on failure so it can be wired into CI.
"""

import os
import shutil
import sys
import tempfile

import bpy

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

# --- 5. "up" really is up ------------------------------------------------
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
