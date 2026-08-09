"""
Blender-side damaged-geometry check.

Two shapes of damaged face, both reproduced on 5.2.0 LTS and both handled in
``import_pof``. Neither arrives as a Python exception -- they are access
violations that kill the process -- so nothing in pytest can reach them and no
``try``/``except`` in the importer can catch them.

1. **A face naming a vertex the submodel does not have.** ``Mesh.from_pydata``
   does not check indices: on this build a face asking for vertex 40 of 4
   survives ``Mesh.update``, and ``-3`` stays ``-3`` in ``polygon.vertices``.

2. **A face using the same vertex twice.** Every index is in range, so nothing
   about it is checkable from the file at all.

Both kill ``normals_split_custom_set_from_vertices`` with an
EXCEPTION_ACCESS_VIOLATION. ``mesh.validate()`` removes both kinds of face, so
it must run before any loop data is written -- the ordering, not the filtering,
is what prevents the crash. ``_usable_faces`` still filters case 1 out first,
because ``validate`` reports it as "a face using one vertex twice, or a second
copy of another face", which is not what happened.

Both fixes drop geometry, so the surviving polygons are no longer 1:1 with the
faces read from the file and the UVs and material indices have to be re-paired
against what Blender kept; getting that wrong slides every UV onto its
neighbour rather than losing a face. The checks below therefore assert the
survivors keep *their own* UVs and materials.

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_bad_geometry.py

Exits non-zero on failure so it can be wired into CI. A regression in either
guard shows up as Blender dying part-way through the output rather than as a
FAIL line.
"""

import os
import shutil
import sys
import tempfile

import bpy

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from descent3_plugin import poformat  # noqa: E402
from descent3_plugin.import_pof import MIN_FACE_VERTICES  # noqa: E402
from descent3_plugin.import_pof import load_pof  # noqa: E402

FIXTURE_DIR = os.path.join(REPO, "tests", "fixtures", "textured_model")

#: How far past the end of the vertex list to point a corrupted corner. Any
#: overrun crashes an unguarded ``from_pydata``; this one is well clear of the
#: end so a stray reallocation cannot land somewhere harmless and hide a bug.
OVERRUN_DISTANCE = 16

#: The other half of the same bug, reaching Blender by a different route: Python
#: reads a negative index as "from the end", so a range check written as
#: ``index < len(verts)`` alone lets it through.
NEGATIVE_INDEX = -3

failures = []


def check(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (f"  {detail}" if detail else ""))
    if not condition:
        failures.append(label)


class FakeImportOp:
    import_guns = import_attach = False
    texture_dir = ""

    def __init__(self):
        # Damaged geometry has to reach the user through the operator, not only
        # the system console: a model quietly missing faces is the failure here.
        self.reports = []

    def report(self, level, msg):
        self.reports.append((level, msg))
        print("   [import]", msg)


def wipe():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials,
                 bpy.data.images):
        for item in list(coll):
            coll.remove(item)
    bpy.context.view_layer.update()


def uvs_of(mesh):
    """Every UV in polygon order, rounded so float noise cannot fail a compare."""
    layer = mesh.uv_layers.active
    if layer is None:
        return []
    return [(round(layer.data[i].uv[0], 5), round(layer.data[i].uv[1], 5))
            for poly in mesh.polygons for i in poly.loop_indices]


def expected_uvs_of(faces):
    """The same list, derived from the file. V is negated, as import negates it."""
    return [(round(fv.u, 5), round(-fv.v, 5))
            for face in faces for fv in face.vertices]


def write_damaged(damage, name):
    """Copy the fixture, damage its geometry, and return the parsed result.

    Args:
        damage: Callable given the first submodel, which corrupts it in place.
        name: Filename stem for the damaged copy.

    Returns:
        A ``(path, parsed)`` pair, re-parsed from the written bytes rather than
        reused from memory, so the checks compare against what the importer is
        handed.
    """
    model = poformat.parse_pof(open(MODEL, "rb").read())
    damage(model.submodels[0])
    path = os.path.join(work, f"{name}.pof")
    with open(path, "wb") as f:
        f.write(poformat.write_pof(model))
    return path, poformat.parse_pof(open(path, "rb").read())


work = tempfile.mkdtemp(prefix="d3bad_")
for fn in os.listdir(FIXTURE_DIR):
    shutil.copy(os.path.join(FIXTURE_DIR, fn), os.path.join(work, fn))
MODEL = os.path.join(work, "textured_cube.oof")

pristine = poformat.parse_pof(open(MODEL, "rb").read())
print(f"fixture: {len(pristine.submodels[0].vertices)} vertices, "
      f"{len(pristine.submodels[0].faces)} faces")

# --- 1. faces pointing at vertices that do not exist ---------------------
# Face 0 is corrupted first on purpose: keeping UVs by position rather than
# re-pairing survivors shifts every later face by one, which only a break at the
# *front* of the list exposes.


def overrun(sm):
    sm.faces[0].vertices[0].index = len(sm.vertices) + OVERRUN_DISTANCE
    sm.faces[2].vertices[2].index = NEGATIVE_INDEX


wipe()
bad_path, bad_model = write_damaged(overrun, "corrupt_indices")
bad_sm = bad_model.submodels[0]
survivors = [
    f for f in bad_sm.faces
    if len(f.vertices) >= MIN_FACE_VERTICES
    and all(0 <= fv.index < len(bad_sm.vertices) for fv in f.vertices)
]
check("the damaged file really does have unusable faces",
      len(survivors) < len(bad_sm.faces),
      f"{len(survivors)} usable of {len(bad_sm.faces)}")

op = FakeImportOp()
result = load_pof(bpy.context, bad_path, op)

check("import survives a face pointing past the vertex list",
      result == {"FINISHED"}, str(result))

meshes = [o for o in bpy.data.objects if o.type == "MESH"]
check("the submodel was still imported", len(meshes) == 1,
      str([o.name for o in meshes]))

mesh = meshes[0].data
check("exactly the usable faces became polygons",
      len(mesh.polygons) == len(survivors),
      f"{len(mesh.polygons)} polygons, {len(survivors)} usable faces")
check("and they are the faces the file could support",
      [tuple(sorted(p.vertices)) for p in mesh.polygons]
      == [tuple(sorted(fv.index for fv in f.vertices)) for f in survivors])
check("the surviving faces kept their own UVs",
      uvs_of(mesh) == expected_uvs_of(survivors),
      f"{len(uvs_of(mesh))} vs {len(expected_uvs_of(survivors))} UVs")

slot_names = [m.name for m in mesh.materials]
check("the surviving faces kept their own materials",
      [slot_names[p.material_index] for p in mesh.polygons]
      == [bad_model.textures[f.texnum] for f in survivors],
      str([slot_names[p.material_index] for p in mesh.polygons]))
check("the user was told faces were dropped, through the operator",
      any("dropped" in msg for _, msg in op.reports),
      str([msg for _, msg in op.reports]))

# --- 2. a face Blender itself rejects ------------------------------------
# Every index is in range, so the check above passes it through: mesh.validate()
# is what removes it, and must run *before* the custom normals because this face
# is what kills the process inside normals_split_custom_set_from_vertices.


def repeated_vertex(sm):
    sm.faces[0].vertices[2].index = sm.faces[0].vertices[0].index


wipe()
degenerate_path, degenerate_model = write_damaged(repeated_vertex, "degenerate")
degenerate_sm = degenerate_model.submodels[0]
in_range = all(
    0 <= fv.index < len(degenerate_sm.vertices)
    for f in degenerate_sm.faces for fv in f.vertices
)
check("the degenerate face gets past the index check on its own merits",
      in_range)

op = FakeImportOp()
result = load_pof(bpy.context, degenerate_path, op)
check("import survives a face using one vertex twice",
      result == {"FINISHED"}, str(result))

mesh = next(o for o in bpy.data.objects if o.type == "MESH").data
remaining = degenerate_sm.faces[1:]
if len(mesh.polygons) == len(degenerate_sm.faces):
    # Blender's own rule about what counts as a polygon decides this and may
    # change. What must not change is that the import completes.
    print("NOTE: this Blender kept the degenerate face; validate() left it")
else:
    check("Blender removed the degenerate face and only that one",
          len(mesh.polygons) == len(remaining),
          f"{len(mesh.polygons)} polygons, expected {len(remaining)}")
    check("the faces after it kept their own UVs",
          uvs_of(mesh) == expected_uvs_of(remaining),
          f"{len(uvs_of(mesh))} vs {len(expected_uvs_of(remaining))} UVs")
    check("the user was told the geometry was invalid",
          any("invalid" in msg for _, msg in op.reports),
          str([msg for _, msg in op.reports]))

# --- 3. a submodel with nothing usable left ------------------------------
# Every face damaged at once. The submodel must come through as an object with
# no polygons rather than a failed import: it may still be somebody's parent in
# the hierarchy, and losing it would move every child.


def wreck_everything(sm):
    for face in sm.faces:
        face.vertices[0].index = len(sm.vertices) + OVERRUN_DISTANCE


wipe()
wrecked_path, _ = write_damaged(wreck_everything, "wrecked")
op = FakeImportOp()
result = load_pof(bpy.context, wrecked_path, op)
check("a submodel with no usable face at all still imports",
      result == {"FINISHED"}, str(result))
wrecked_meshes = [o for o in bpy.data.objects if o.type == "MESH"]
check("it is still an object, so the hierarchy under it survives",
      len(wrecked_meshes) == 1, str([o.name for o in wrecked_meshes]))
check("with no polygons rather than wrong ones",
      len(wrecked_meshes[0].data.polygons) == 0,
      f"{len(wrecked_meshes[0].data.polygons)} polygons")
check("and the vertices are still there to see",
      len(wrecked_meshes[0].data.vertices)
      == len(pristine.submodels[0].vertices),
      f"{len(wrecked_meshes[0].data.vertices)} vertices")

shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print("FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("all damaged-geometry checks passed")
