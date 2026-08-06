"""
Blender-side texture export check.

Export writes texture *names* into the TXTR chunk; this covers writing the
pixels those names refer to. Two Blender behaviours make that easy to get
silently wrong, and both are asserted here rather than trusted:

1. ``Image.save(filepath=...)`` ignores ``file_format``. Asking for Targa and
   getting a ``.tga`` file full of PNG bytes looks like success from Python and
   is rejected by the game. The magic number is checked, not the extension.
2. ``Image.save_render()`` renders through the scene's colour management, and
   Blender 5.2 defaults the view transform to AgX. Exported textures would come
   out tone-mapped. The bytes are compared against the untouched source.

    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_texture_export.py

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
from descent3_plugin.texutil import find_texture_image  # noqa: E402

FIXTURE_DIR = os.path.join(REPO, "tests", "fixtures", "textured_model")
EXPORT_DIR = "exported_textures"

failures = []


def check(label, condition, detail=""):
    print(("PASS  " if condition else "FAIL  ") + label + (f"  {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def magic(path):
    """Identify a file by its header, never by its extension."""
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(b"\x89PNG"):
        return "PNG"
    if head.startswith(b"BM"):
        return "BMP"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    return "TGA/other"


class FakeImportOp:
    import_guns = import_attach = False
    texture_dir = ""

    def report(self, level, msg):
        pass


class ExportOp:
    """Stand-in for the operator, with a selectable texture format."""

    export_selected = False
    export_guns = export_attach = False
    export_version = "CONFIG"

    def __init__(self, texture_format):
        self.texture_format = texture_format
        self.reports = []

    def report(self, level, msg):
        self.reports.append((tuple(level)[0], msg))


def wipe():
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials,
                 bpy.data.images):
        for item in list(coll):
            coll.remove(item)
    bpy.context.view_layer.update()


work = tempfile.mkdtemp(prefix="d3tex_")
src = os.path.join(work, "src")
os.makedirs(src)
for fn in os.listdir(FIXTURE_DIR):
    shutil.copy(os.path.join(FIXTURE_DIR, fn), os.path.join(src, fn))
MODEL = os.path.join(src, "textured_cube.oof")
source = poformat.parse_pof(open(MODEL, "rb").read())
print("fixture textures:", source.textures)

# Make AgX the active view transform, which is Blender 5.2's default and the
# whole reason the colour-management guard exists.
bpy.context.scene.view_settings.view_transform = "AgX"
print("scene view transform:", bpy.context.scene.view_settings.view_transform)


def export_with(fmt, name):
    """Import the fixture, export it with ``fmt``, return the output folder."""
    wipe()
    load_pof(bpy.context, MODEL, FakeImportOp())
    out_dir = os.path.join(work, name)
    os.makedirs(out_dir, exist_ok=True)
    op = ExportOp(fmt)
    save_pof(bpy.context, os.path.join(out_dir, "model.pof"), op)
    return out_dir, op


# --- 1. NONE writes no images -------------------------------------------
out, _ = export_with("NONE", "none")
check("NONE writes the model but no texture folder",
      os.path.isfile(os.path.join(out, "model.pof"))
      and not os.path.isdir(os.path.join(out, EXPORT_DIR)))

# --- 2. PNG ---------------------------------------------------------------
out, op = export_with("PNG", "png")
tex_dir = os.path.join(out, EXPORT_DIR)
check("PNG creates the export subfolder", os.path.isdir(tex_dir), tex_dir)
files = sorted(os.listdir(tex_dir)) if os.path.isdir(tex_dir) else []
check("one file per texture", files == sorted(t + ".png" for t in source.textures),
      str(files))
check("PNG files really are PNG",
      all(magic(os.path.join(tex_dir, f)) == "PNG" for f in files),
      str([(f, magic(os.path.join(tex_dir, f))) for f in files]))

# The fixture's sources are PNG, so this path copies rather than re-encodes --
# which is exactly how it dodges colour management. Prove the bytes match.
identical = all(
    open(os.path.join(tex_dir, t + ".png"), "rb").read()
    == open(os.path.join(src, t + ".png"), "rb").read()
    for t in source.textures
)
check("PNG source is copied byte-for-byte (no re-encode, no tone-map)", identical)

# --- 3. TARGA: the format trap ------------------------------------------
out, op = export_with("TARGA", "tga")
tex_dir = os.path.join(out, EXPORT_DIR)
files = sorted(os.listdir(tex_dir)) if os.path.isdir(tex_dir) else []
check("TARGA writes .tga files", files == sorted(t + ".tga" for t in source.textures),
      str(files))
check("the .tga files are NOT secretly PNG",
      all(magic(os.path.join(tex_dir, f)) != "PNG" for f in files),
      str([(f, magic(os.path.join(tex_dir, f))) for f in files]))

# --- 4. the colour-management trap --------------------------------------
# Re-encoding under AgX would change the pixels. Compare against the same
# conversion done with colour management explicitly neutral.
img = bpy.data.images.load(os.path.join(src, source.textures[0] + ".png"))
scene = bpy.context.scene
reference = os.path.join(work, "reference.tga")
prev_vt, prev_fmt = scene.view_settings.view_transform, scene.render.image_settings.file_format
scene.view_settings.view_transform = "Standard"
scene.render.image_settings.file_format = "TARGA"
img.save_render(filepath=reference, scene=scene)
scene.view_settings.view_transform = prev_vt
scene.render.image_settings.file_format = prev_fmt

exported = os.path.join(tex_dir, source.textures[0] + ".tga")
check("converted texture matches a neutral encode, not an AgX one",
      open(exported, "rb").read() == open(reference, "rb").read(),
      f"{os.path.getsize(exported)} vs {os.path.getsize(reference)} bytes")

# And the scene must be left exactly as it was found.
check("scene view transform restored after export",
      scene.view_settings.view_transform == "AgX",
      scene.view_settings.view_transform)

# --- 5. the export folder is written, but NOT auto-searched --------------
# Import deliberately does not widen its search to the export folder. Doing so
# on the user's behalf makes the importer unpredictable, and a project may be
# sandboxed off from its own exports on purpose. Opting in is a config change.
out, _ = export_with("PNG", "roundtrip")
model_path = os.path.join(out, "model.pof")
written = poformat.parse_pof(open(model_path, "rb").read())
check("exported model still names its textures",
      sorted(written.textures) == sorted(source.textures), str(written.textures))

for t in written.textures:
    hit = find_texture_image(t, [os.path.join(out, EXPORT_DIR)])
    check(f"texture '{t}' was written and is findable in the export folder",
          hit is not None)

wipe()
load_pof(bpy.context, model_path, FakeImportOp())
textured = sorted(
    m.name for m in bpy.data.materials
    if m.use_nodes and any(n.type == "TEX_IMAGE" and n.image for n in m.node_tree.nodes)
)
check("re-import does NOT silently reach into the export folder",
      textured == [],
      f"textured={textured} (the export folder must not be searched implicitly)")

# Opting in through the project config must work, and is the documented route.
with open(os.path.join(out, "descent3.toml"), "w", encoding="utf-8") as f:
    f.write('[textures]\nsearch_dirs = ["%s"]\n' % EXPORT_DIR)

wipe()
load_pof(bpy.context, model_path, FakeImportOp())
textured = sorted(
    m.name for m in bpy.data.materials
    if m.use_nodes and any(n.type == "TEX_IMAGE" and n.image for n in m.node_tree.nodes)
)
check("listing the export folder in search_dirs finds the textures",
      textured == sorted(source.textures),
      f"textured={textured}")

# The dialog's Texture Folder is the other route, and needs no config file.
os.remove(os.path.join(out, "descent3.toml"))


class OptedInImportOp(FakeImportOp):
    texture_dir = os.path.join(out, EXPORT_DIR)


wipe()
load_pof(bpy.context, model_path, OptedInImportOp())
textured = sorted(
    m.name for m in bpy.data.materials
    if m.use_nodes and any(n.type == "TEX_IMAGE" and n.image for n in m.node_tree.nodes)
)
check("pointing the dialog's Texture Folder at it also works",
      textured == sorted(source.textures),
      f"textured={textured}")

# --- 6. a material with no image is reported, not silently skipped -------
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
bare = bpy.data.materials.new(name="NoImageTexture")
bare.use_nodes = True
mesh_obj = next(o for o in bpy.data.objects if o.type == "MESH")
mesh_obj.data.materials.append(bare)
mesh_obj.data.polygons[0].material_index = len(mesh_obj.data.materials) - 1

out = os.path.join(work, "bare")
os.makedirs(out, exist_ok=True)
op = ExportOp("PNG")
save_pof(bpy.context, os.path.join(out, "model.pof"), op)
warned = [m for lvl, m in op.reports if "NoImageTexture" in m]
check("a material with no image is reported", bool(warned),
      warned[0] if warned else str(op.reports))
check("the other textures were still written",
      len(os.listdir(os.path.join(out, EXPORT_DIR))) == len(source.textures),
      str(sorted(os.listdir(os.path.join(out, EXPORT_DIR)))))

shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print("FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("all texture-export checks passed")
