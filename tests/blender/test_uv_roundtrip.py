"""
Blender-side import/export round-trip check.

pytest can only reach the ``bpy``-free code, so the mesh-building half of the
add-on -- UV orientation in particular -- has to be exercised inside real
Blender. Descent 3 puts the UV origin at the top-left and Blender at the
bottom-left, so import negates V and export must negate it back; when only one
side did, every exported texture came out vertically mirrored.

Run:
    blender.exe --background --factory-startup --python tests/blender/test_uv_roundtrip.py

Exits non-zero on failure so it can be wired into CI.
"""

import os
import sys
import tempfile

import bpy

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from descent3_plugin import poformat  # noqa: E402
from descent3_plugin.export_pof import save_pof  # noqa: E402
from descent3_plugin.import_pof import load_pof  # noqa: E402

FIXTURE = os.path.join(REPO, "tests", "fixtures", "textured_model", "textured_cube.oof")

failures = []


def check(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (f"  {detail}" if detail else ""))
    if not condition:
        failures.append(label)


class FakeImportOp:
    import_guns = False
    import_attach = False
    texture_dir = ""

    def report(self, level, msg):
        print("   [import]", msg)


class FakeExportOp:
    export_version = "2300"
    export_selected = False
    export_guns = False
    export_attach = False

    def report(self, level, msg):
        print("   [export]", msg)


def wipe():
    """Clear scene data between scenarios.

    ``bpy.data.collections`` is deliberately left alone: removing the scene's
    master collection children leaves the view layer without a valid object
    list, and iterating it then yields None.
    """
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials):
        for item in list(coll):
            coll.remove(item)
    bpy.context.view_layer.update()


def model_uvs(model):
    """Every (u, v) in a parsed model, in face order."""
    return [
        (round(fv.u, 5), round(fv.v, 5))
        for sm in model.submodels
        for face in sm.faces
        for fv in face.vertices
    ]


def scene_uvs():
    """Every (u, v) on the imported meshes, in face order."""
    out = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        layer = obj.data.uv_layers.active
        if layer is None:
            continue
        for poly in obj.data.polygons:
            for loop_idx in poly.loop_indices:
                u, v = layer.data[loop_idx].uv
                out.append((round(u, 5), round(v, 5)))
    return out


wipe()

source = poformat.parse_pof(open(FIXTURE, "rb").read())
source_uvs = model_uvs(source)
check("fixture has UVs to compare", len(source_uvs) > 0, f"{len(source_uvs)} pairs")

load_pof(bpy.context, FIXTURE, FakeImportOp())

in_blender = scene_uvs()
check("import produced the same number of UVs",
      len(in_blender) == len(source_uvs), f"{len(in_blender)} vs {len(source_uvs)}")
check("import negates V (Descent top-left origin -> Blender bottom-left)",
      in_blender == [(u, -v) for u, v in source_uvs])

out_path = os.path.join(tempfile.gettempdir(), "descent3_uv_roundtrip.pof")
save_pof(bpy.context, out_path, FakeExportOp())
exported_uvs = model_uvs(poformat.parse_pof(open(out_path, "rb").read()))

check("export negates V back", exported_uvs == source_uvs,
      "" if exported_uvs == source_uvs else f"{exported_uvs[:4]} != {source_uvs[:4]}")

# Geometry and materials should survive the same trip.
reparsed = poformat.parse_pof(open(out_path, "rb").read())
check("submodel count preserved",
      len(reparsed.submodels) == len(source.submodels))
check("vertex count preserved",
      [len(sm.vertices) for sm in reparsed.submodels]
      == [len(sm.vertices) for sm in source.submodels])
check("face count preserved",
      [len(sm.faces) for sm in reparsed.submodels]
      == [len(sm.faces) for sm in source.submodels])
check("both textures still referenced",
      {f.texnum for sm in reparsed.submodels for f in sm.faces} == {0, 1})

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {', '.join(failures)}")
    sys.exit(1)
print("all checks passed")
