"""
Blender-side material-ID reuse check.

A Blender material's name is the Descent 3 texture ID. ``bpy.data.materials.new``
always mints a new datablock and Blender uniquifies the name, so importing a
second asset that shares a texture used to produce ``Hull.001`` -- and export
wrote that into the TXTR chunk as a texture no bitmap matches. Re-importing then
brought half the model back untextured.

Reuse is what fixes that, and reuse has a boundary this file also pins down.
Adopting a material because it happens to share a name is not permission to
restyle it: enabling nodes on the user's own ``Hull`` and wiring an image into
it changes every object in their scene that uses it. So a material is only
finished off when it carries
:data:`~descent3_plugin.constants.PROP_KEY_ADDON_MATERIAL`, the mark this add-on
writes on datablocks it creates. Anything else is used exactly as it arrives --
and stays that way however many times the model is imported, which is its own
scenario below because the first version of this rule read authorship off the
*texture* key and so spared the user's material once and then edited it.

:data:`~descent3_plugin.constants.PROP_KEY_TEXTURE`, the separate record of
which bitmap a material stands for, is what tells ``metal`` and ``metal.001`` --
two real Descent 3 bitmaps -- apart from a material somebody duplicated in the
outliner, so the duplicate-suffix rule is exercised here on materials that carry
no record, which is now the only place it applies.

pytest cannot reach any of this: it lives entirely in ``bpy``. Run inside
Blender:

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_material_reuse.py

Exits non-zero on failure so it can be wired into CI.
"""

import os
import shutil
import sys
import tempfile

import bmesh
import bpy

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from descent3_plugin import poformat  # noqa: E402
from descent3_plugin.constants import (  # noqa: E402
    PROP_KEY_ADDON_MATERIAL,
    PROP_KEY_TEXTURE,
)
from descent3_plugin.export_pof import save_pof  # noqa: E402
from descent3_plugin.import_pof import load_pof  # noqa: E402
from descent3_plugin.naming import is_duplicate_material_name  # noqa: E402

FIXTURE_DIR = os.path.join(REPO, "tests", "fixtures", "textured_model")

failures = []


def check(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (f"  {detail}" if detail else ""))
    if not condition:
        failures.append(label)


class FakeImportOp:
    import_guns = import_attach = False
    texture_dir = ""

    def __init__(self):
        # Kept so a scenario can assert the user was actually told something,
        # rather than it having gone only to the system console -- which is
        # exactly the failure mode the adoption rules exist to end.
        self.reports = []

    def report(self, level, msg):
        self.reports.append((level, msg))
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
    """Names carrying Blender's .NNN duplicate suffix.

    Asks the production rule rather than restating it. This used to match
    exactly three digits, which is the narrowing ``\\.\\d{3,}$`` exists to avoid:
    past ``.999`` Blender stops zero-padding the counter, so a local copy of the
    rule would have called ``Hull.1000`` a clean name and the test would have
    agreed with a bug instead of catching it.
    """
    return [n for n in names if is_duplicate_material_name(n)]


def has_image(mat):
    """Whether a material actually shows an image texture."""
    return bool(
        mat.use_nodes
        and mat.node_tree
        and any(n.type == "TEX_IMAGE" and n.image for n in mat.node_tree.nodes)
    )


def slot_materials():
    """Every material assigned to a slot on an imported mesh."""
    return [m for o in bpy.data.objects if o.type == "MESH"
            for m in o.data.materials]


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
check("import records the texture ID on every material it creates",
      sorted(m.get(PROP_KEY_TEXTURE) for m in bpy.data.materials)
      == sorted(source.textures),
      str({m.name: m.get(PROP_KEY_TEXTURE) for m in bpy.data.materials}))

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
textured = [m.name for m in bpy.data.materials if has_image(m)]
check("re-imported model is fully textured",
      sorted(textured) == sorted(source.textures),
      f"textured={sorted(textured)} of {material_names()}")

# --- 5. a material the USER made is reused, and left exactly as it is ------
# Sharing a name with a texture is a coincidence, not consent. The user's own
# Hull is used by their own objects too, so wiring an image into it changes
# every one of them because of an import that has nothing to do with them.
#
# The shading is authored here rather than left at the default: ``use_nodes``
# cannot carry the assertion, because from Blender 5.x a material always uses
# nodes and setting the property False is ignored. What the user actually loses
# in that bug is their node tree, so that is what is checked.
wipe()
shaded = bpy.data.materials.new(name=source.textures[0])
shaded.use_nodes = True
rgb = shaded.node_tree.nodes.new("ShaderNodeRGB")
principled = shaded.node_tree.nodes["Principled BSDF"]
shaded.node_tree.links.new(rgb.outputs["Color"], principled.inputs["Base Color"])
nodes_before = len(shaded.node_tree.nodes)
plain = bpy.data.materials.new(name=source.textures[1])
check("user materials created under the texture names",
      [shaded.name, plain.name] == list(source.textures[:2]),
      f"{shaded.name}, {plain.name}")
check("user materials carry no provenance",
      PROP_KEY_TEXTURE not in shaded and PROP_KEY_TEXTURE not in plain)

op = FakeImportOp()
load_pof(bpy.context, MODEL, op)
names = material_names()
check("user's materials were adopted, not duplicated",
      suffixed(names) == [], str(names))
check("adopted material is the same datablock",
      bpy.data.materials.get(source.textures[0]) is shaded)
check("the imported mesh uses the user's materials for those textures",
      shaded in slot_materials() and plain in slot_materials(),
      str([m.name for m in slot_materials()]))
check("no image was wired into the user's material",
      not has_image(shaded), f"nodes now: {[n.type for n in shaded.node_tree.nodes]}")
check("the user's node tree was not added to",
      len(shaded.node_tree.nodes) == nodes_before,
      f"{len(shaded.node_tree.nodes)} nodes, was {nodes_before}")
base_color = shaded.node_tree.nodes["Principled BSDF"].inputs["Base Color"]
check("the user's own shading still drives Base Color",
      base_color.is_linked and base_color.links[0].from_node.type == "RGB",
      str([lnk.from_node.type for lnk in base_color.links]))
check("the untouched material was left with no image either",
      not has_image(plain), f"nodes: {[n.type for n in plain.node_tree.nodes]}")
check("adoption records which texture the material stands for",
      shaded.get(PROP_KEY_TEXTURE) == source.textures[0]
      and plain.get(PROP_KEY_TEXTURE) == source.textures[1],
      f"{shaded.get(PROP_KEY_TEXTURE)}, {plain.get(PROP_KEY_TEXTURE)}")
check("adoption does NOT claim authorship of the user's material",
      PROP_KEY_ADDON_MATERIAL not in shaded
      and PROP_KEY_ADDON_MATERIAL not in plain,
      f"{shaded.get(PROP_KEY_ADDON_MATERIAL)}, "
      f"{plain.get(PROP_KEY_ADDON_MATERIAL)}")
check("the user was told what happened, through the operator and not only the "
      "console",
      any("WARNING" in level and source.textures[0] in msg and "reused" in msg
          for level, msg in op.reports),
      str([msg for _, msg in op.reports]))

# --- 5b. and it is still theirs on the SECOND import ----------------------
# The bug this half exists for did not show up on the import that spared the
# material -- it showed up on the next one. Authorship was read off the texture
# key, and adoption writes the texture key, so the user's material came back
# from import 1 wearing what import 2 took for the add-on's own signature and
# wired an image into it after all. Nothing about the scene changed in between,
# so importing twice is the whole reproduction.
op2 = FakeImportOp()
load_pof(bpy.context, MODEL, op2)
check("a second import still creates no duplicate materials",
      suffixed(material_names()) == [], str(material_names()))
check("the second import did not wire an image into the user's material",
      not has_image(shaded),
      f"nodes now: {[n.type for n in shaded.node_tree.nodes]}")
check("the second import did not add to the user's node tree",
      len(shaded.node_tree.nodes) == nodes_before,
      f"{len(shaded.node_tree.nodes)} nodes, was {nodes_before}")
base_color = shaded.node_tree.nodes["Principled BSDF"].inputs["Base Color"]
check("the user's own shading still drives Base Color after two imports",
      base_color.is_linked and base_color.links[0].from_node.type == "RGB",
      str([lnk.from_node.type for lnk in base_color.links]))
check("the second import told the user too, rather than going quiet",
      any("WARNING" in level and source.textures[0] in msg and "reused" in msg
          for level, msg in op2.reports),
      str([msg for _, msg in op2.reports]))

# --- 5c. a hand-pinned texture ID is honoured, and its owner warned --------
# The docs invite a user to set d3_texture by hand to pin an export name. That
# pin must not read as permission to edit the material, and when it disagrees
# with the texture being imported the user has to hear about it -- their faces
# are about to export as something other than what the model called them.
wipe()
pinned = bpy.data.materials.new(name=source.textures[0])
pinned[PROP_KEY_TEXTURE] = "some_other_bitmap"
op3 = FakeImportOp()
load_pof(bpy.context, MODEL, op3)
check("a hand-pinned material is not restyled either",
      not has_image(pinned),
      f"nodes: {[n.type for n in pinned.node_tree.nodes]}")
check("a hand-pinned texture ID is never clobbered",
      pinned.get(PROP_KEY_TEXTURE) == "some_other_bitmap",
      str(pinned.get(PROP_KEY_TEXTURE)))
check("the user is warned that the pin redirects those faces",
      any("some_other_bitmap" in msg for _, msg in op3.reports),
      str([msg for _, msg in op3.reports]))

# --- 6. a material THIS ADD-ON made is finished off, not left blank --------
# The other half of the same rule: the authorship mark says the add-on built
# this datablock, so attaching the image it was always meant to have changes
# nothing the user decided.
wipe()
ours = bpy.data.materials.new(name=source.textures[0])
ours[PROP_KEY_TEXTURE] = source.textures[0]
ours[PROP_KEY_ADDON_MATERIAL] = True
# Ignored from Blender 5.x, where a material always uses nodes; on 4.2, the
# oldest version the add-on supports, it is the state import has to recover from.
ours.use_nodes = False
load_pof(bpy.context, MODEL, FakeImportOp())
check("the add-on's own material is reused, not duplicated",
      suffixed(material_names()) == [], str(material_names()))
check("the add-on's own material is the same datablock",
      bpy.data.materials.get(source.textures[0]) is ours)
check("the add-on's own material had the texture attached",
      has_image(ours), f"use_nodes={ours.use_nodes}")
check("import marks every material it creates as its own",
      all(m.get(PROP_KEY_ADDON_MATERIAL)
          for m in bpy.data.materials if m is not ours),
      str({m.name: m.get(PROP_KEY_ADDON_MATERIAL) for m in bpy.data.materials}))

# --- 7. export defends against a scene polluted before provenance existed --
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
# Hand-duplicate a material the way a user might, and point a face at it. The
# recorded texture ID is stripped: a copy made today inherits it and exports
# straight back to the original, so dropping it is what leaves the ``.NNN``
# heuristic -- the rule this scenario is here to exercise -- in charge.
original = bpy.data.materials[source.textures[0]]
dupe = original.copy()          # Blender names this "<texture>.001"
del dupe[PROP_KEY_TEXTURE]
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

# --- 8. two REAL textures that merely look like duplicates both survive ----
# Descent 3 ships bitmaps called both "metal" and "metal.001". Read by name
# alone the second is a Blender duplicate of the first, and export merged them,
# repointed its faces and lost a texture from the model. The recorded ID is
# what tells the two cases apart, so here the clone stands for its own bitmap.
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
original = bpy.data.materials[source.textures[0]]
sibling = original.copy()                     # named "<texture>.001" ...
sibling[PROP_KEY_TEXTURE] = sibling.name      # ... and that is a real texture
mesh_obj = next(o for o in bpy.data.objects if o.type == "MESH")
mesh_obj.data.materials.append(sibling)
mesh_obj.data.polygons[0].material_index = len(mesh_obj.data.materials) - 1

out3 = os.path.join(work, "two_textures.pof")
save_pof(bpy.context, out3, FakeExportOp())
both = poformat.parse_pof(open(out3, "rb").read())
check("a recorded texture ID is never collapsed as a duplicate",
      sorted(both.textures) == sorted(list(source.textures) + [sibling.name]),
      str(both.textures))
sibling_face = both.submodels[0].faces[0]
check("the face keeps the texture its material records",
      sibling_face.textured
      and both.textures[sibling_face.texnum] == sibling.name,
      both.textures[sibling_face.texnum] if sibling_face.textured else "untextured")

# --- 9. an ORPHAN duplicate must NOT be collapsed onto a missing original --
# Hull.001 with no Hull: merging would invent a texture the scene never had and
# fuse two distinct materials. Export verbatim and warn instead. As in 7, the
# recorded ID is stripped so this is the heuristic being tested and not
# provenance quietly answering for it.
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
original = bpy.data.materials[source.textures[0]]
orphan = original.copy()                 # "<texture>.001"
del orphan[PROP_KEY_TEXTURE]
orphan_name = orphan.name
mesh_obj = next(o for o in bpy.data.objects if o.type == "MESH")
mesh_obj.data.materials.append(orphan)
mesh_obj.data.polygons[0].material_index = len(mesh_obj.data.materials) - 1
bpy.data.materials.remove(original)      # delete the original it was cloned from
check("orphan duplicate exists with no original",
      bpy.data.materials.get(source.textures[0]) is None
      and bpy.data.materials.get(orphan_name) is not None, orphan_name)

out4 = os.path.join(work, "orphan.pof")
save_pof(bpy.context, out4, FakeExportOp())
orphaned = poformat.parse_pof(open(out4, "rb").read())
check("orphan is exported verbatim, not merged onto a missing name",
      orphan_name in orphaned.textures, str(orphaned.textures))
check("no texture was invented for the deleted original",
      source.textures[0] not in orphaned.textures, str(orphaned.textures))

# --- 10. a slot a MODIFIER added is still a texture the model uses ---------
# Geometry comes from the evaluated object, so a Boolean set to transfer
# materials -- or a Geometry Nodes Set Material -- produces faces whose
# ``material_index`` points into the *evaluated* slot list. Reading the slots off
# the original object then finds a shorter list: those faces exported flat grey
# and their texture never reached the TXTR chunk, with nothing said about either,
# for a model the viewport showed fully textured.
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
host = next(o for o in bpy.data.objects if o.type == "MESH")
transferred = bpy.data.materials.new(name="Transferred")
transferred[PROP_KEY_TEXTURE] = "Transferred"

cutter_mesh = bpy.data.meshes.new("CutterMesh")
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=0.6)
bm.to_mesh(cutter_mesh)
bm.free()
cutter_mesh.materials.append(transferred)
cutter = bpy.data.objects.new("Cutter", cutter_mesh)
cutter.location = (0.4, 0.0, 0.0)
bpy.context.scene.collection.objects.link(cutter)

boolean = host.modifiers.new("Bool", "BOOLEAN")
boolean.object = cutter
boolean.operation = "UNION"
transfers = hasattr(boolean, "material_mode")
if transfers:
    boolean.material_mode = "TRANSFER"
cutter.hide_viewport = True          # the cutter itself is not part of the ship
bpy.context.view_layer.update()

evaluated = host.evaluated_get(bpy.context.evaluated_depsgraph_get())
check("the modifier really does add a slot the original lacks",
      not transfers or len(evaluated.material_slots) > len(host.material_slots),
      f"{len(host.material_slots)} original vs {len(evaluated.material_slots)} evaluated")

out5 = os.path.join(work, "modifier_slot.pof")
save_pof(bpy.context, out5, FakeExportOp())
with_modifier = poformat.parse_pof(open(out5, "rb").read())
host_sm = next(sm for sm in with_modifier.submodels if sm.name == host.name)
untextured = [f for f in host_sm.faces if not f.textured]
check("a material a modifier introduced reaches the TXTR chunk",
      not transfers or "Transferred" in with_modifier.textures,
      str(with_modifier.textures))
check("and no face was left flat grey for want of a slot",
      not untextured, f"{len(untextured)} of {len(host_sm.faces)} untextured")

shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print("FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("all material-reuse checks passed")
