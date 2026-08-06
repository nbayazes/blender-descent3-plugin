"""
Blender-side material-ID reuse check.

A Blender material's name is the Descent 3 texture ID. ``bpy.data.materials.new``
always mints a new datablock and Blender uniquifies the name, so importing a
second asset that shares a texture used to produce ``Hull.001`` -- and export
wrote that into the TXTR chunk as a texture no bitmap matches. Re-importing then
brought half the model back untextured.

pytest cannot reach this: it lives entirely in ``bpy``. Run inside Blender:

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_material_reuse.py

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

FIXTURE_DIR = os.path.join(REPO, "tests", "fixtures", "textured_model")

failures = []


def check(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (f"  {detail}" if detail else ""))
    if not condition:
        failures.append(label)


class FakeImportOp:
    import_guns = import_attach = False
    texture_dir = ""

    def report(self, level, msg):
        print("   [import]", msg)


class FakeExportOp:
    export_selected = False
    export_guns = export_attach = False
    export_version = "CONFIG"

    def report(self, level, msg):
        print("   [export]", msg)


def wipe():
    """Clear scene data between scenarios.

    ``bpy.data.collections`` is deliberately left alone, and the view layer is
    updated afterwards: removing objects without doing so leaves the view layer
    holding stale entries, and iterating it then yields None. Images are dropped
    too, so a reused ``bpy.data.images`` entry cannot make an untextured
    material look textured.
    """
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials,
                 bpy.data.images):
        for item in list(coll):
            coll.remove(item)
    bpy.context.view_layer.update()


def material_names():
    return sorted(m.name for m in bpy.data.materials)


def suffixed(names):
    """Names carrying Blender's .NNN duplicate suffix."""
    return [n for n in names
            if "." in n and n.rsplit(".", 1)[-1].isdigit()
            and len(n.rsplit(".", 1)[-1]) == 3]


work = tempfile.mkdtemp(prefix="d3mat_")
for fn in os.listdir(FIXTURE_DIR):
    shutil.copy(os.path.join(FIXTURE_DIR, fn), os.path.join(work, fn))
MODEL = os.path.join(work, "textured_cube.oof")

source = poformat.parse_pof(open(MODEL, "rb").read())
print(f"fixture textures: {source.textures}")

# --- 1. importing the same model twice must not duplicate materials --------
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
after_first = material_names()
load_pof(bpy.context, MODEL, FakeImportOp())
after_second = material_names()

check("first import creates one material per texture",
      after_first == sorted(source.textures), str(after_first))
check("second import creates no new materials",
      after_second == after_first, str(after_second))
check("no .NNN duplicate materials exist",
      suffixed(after_second) == [], str(suffixed(after_second)))

# --- 2. the exported TXTR chunk must stay clean ---------------------------
out = os.path.join(work, "twice.pof")
save_pof(bpy.context, out, FakeExportOp())
written = poformat.parse_pof(open(out, "rb").read())
check("export writes the original texture names",
      sorted(written.textures) == sorted(source.textures), str(written.textures))
check("export writes no .NNN texture names",
      suffixed(written.textures) == [], str(suffixed(written.textures)))

# --- 3. every exported texture must resolve back to a bitmap --------------
from descent3_plugin.texutil import find_texture_image  # noqa: E402

unresolved = [t for t in written.textures if not find_texture_image(t, [work])]
check("every exported texture resolves to a file on disk",
      unresolved == [], str(unresolved))

# --- 4. the full round trip: re-import must be fully textured -------------
wipe()
load_pof(bpy.context, out, FakeImportOp())
textured = [
    m.name for m in bpy.data.materials
    if m.use_nodes and any(n.type == "TEX_IMAGE" and n.image for n in m.node_tree.nodes)
]
check("re-imported model is fully textured",
      sorted(textured) == sorted(source.textures),
      f"textured={sorted(textured)} of {material_names()}")

# --- 5. a material the USER already made is adopted, not duplicated -------
wipe()
user_mat = bpy.data.materials.new(name=source.textures[0])
user_mat.use_nodes = True
check("user material created under the texture name",
      user_mat.name == source.textures[0], user_mat.name)

load_pof(bpy.context, MODEL, FakeImportOp())
names = material_names()
check("user's material was adopted, not duplicated",
      suffixed(names) == [], str(names))
check("adopted material is the same datablock",
      bpy.data.materials.get(source.textures[0]) is user_mat)
check("adopted material had the texture attached",
      user_mat.use_nodes
      and any(n.type == "TEX_IMAGE" and n.image for n in user_mat.node_tree.nodes),
      "an image node was added to the user's material")

# --- 6. export defends against a scene polluted before the fix ------------
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
# Hand-duplicate a material the way a user might, and point a face at it.
original = bpy.data.materials[source.textures[0]]
dupe = original.copy()          # Blender names this "<texture>.001"
check("hand-duplicated material is .NNN suffixed",
      suffixed([dupe.name]) != [], dupe.name)

mesh_obj = next(o for o in bpy.data.objects if o.type == "MESH")
mesh_obj.data.materials.append(dupe)
dupe_slot = len(mesh_obj.data.materials) - 1
mesh_obj.data.polygons[0].material_index = dupe_slot

out2 = os.path.join(work, "polluted.pof")
save_pof(bpy.context, out2, FakeExportOp())
polluted = poformat.parse_pof(open(out2, "rb").read())
check("export strips the duplicate suffix",
      suffixed(polluted.textures) == [], str(polluted.textures))
check("duplicate collapses onto the real texture",
      sorted(polluted.textures) == sorted(source.textures), str(polluted.textures))

face0 = polluted.submodels[0].faces[0]
check("the face using the duplicate still references a valid texture",
      face0.textured and 0 <= face0.texnum < len(polluted.textures),
      f"textured={face0.textured} texnum={face0.texnum}")
check("and it resolves to the original texture",
      polluted.textures[face0.texnum] == source.textures[0],
      polluted.textures[face0.texnum] if face0.textured else "untextured")

# --- 7. an ORPHAN duplicate must NOT be collapsed onto a missing original --
# Hull.001 with no Hull: merging would invent a texture the scene never had and
# fuse two distinct materials. Export verbatim and warn instead.
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
original = bpy.data.materials[source.textures[0]]
orphan = original.copy()                 # "<texture>.001"
orphan_name = orphan.name
mesh_obj = next(o for o in bpy.data.objects if o.type == "MESH")
mesh_obj.data.materials.append(orphan)
mesh_obj.data.polygons[0].material_index = len(mesh_obj.data.materials) - 1
bpy.data.materials.remove(original)      # delete the original it was cloned from
check("orphan duplicate exists with no original",
      bpy.data.materials.get(source.textures[0]) is None
      and bpy.data.materials.get(orphan_name) is not None, orphan_name)

out3 = os.path.join(work, "orphan.pof")
save_pof(bpy.context, out3, FakeExportOp())
orphaned = poformat.parse_pof(open(out3, "rb").read())
check("orphan is exported verbatim, not merged onto a missing name",
      orphan_name in orphaned.textures, str(orphaned.textures))
check("no texture was invented for the deleted original",
      source.textures[0] not in orphaned.textures, str(orphaned.textures))

shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print("FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("all material-reuse checks passed")
