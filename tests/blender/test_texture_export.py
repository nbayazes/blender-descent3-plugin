"""
Blender-side texture export check.

Export writes texture *names* into the TXTR chunk; this covers writing the
pixels those names refer to. Several Blender behaviours make that easy to get
silently wrong, and each is asserted here rather than trusted:

1. ``Image.save(filepath=...)`` ignores ``file_format``. Asking for Targa and
   getting a ``.tga`` file full of PNG bytes looks like success from Python and
   is rejected by the game. The magic number is checked, not the extension.
2. ``Image.save_render()`` renders through the scene's colour management, and
   Blender 5.2 defaults the view transform to AgX. Exported textures would come
   out tone-mapped. The bytes are compared against the untouched source.
3. ``save_render`` also encodes through the scene's Output Properties, so a
   scene left on RGB or BW writes every texture without its alpha channel. The
   Targa pixel depth is read out of the header, because a 24-bit file with the
   right pixels in it looks correct everywhere except in the game.
4. A texture ID is a filename. Blender is perfectly happy with a material
   called ``../../../hull``, and the export used to join that straight onto the
   output folder and overwrite whatever it landed on. The file that name aims
   at is asserted *not* to appear.
5. ``Image.pixels`` edits live in the datablock, not on disk, so the fast path
   that copies the source file has to consult ``is_dirty`` or it exports the
   art as it was before the artist started painting.

The other half of picking a texture is picking the right *image* out of a PBR
material: Descent 3 gives a face one texture, and a material whose Normal Map
node was built before its Base Color used to export the normal map.

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


def tga_depth(path):
    """Return a Targa's bits per pixel, which is where a dropped alpha shows.

    Byte 16 of the header: 32 with alpha, 24 for RGB, 8 for greyscale. Measured
    against this Blender -- writing the same image with ``color_mode`` set to
    RGBA, RGB and BW gives exactly those three.
    """
    with open(path, "rb") as f:
        return f.read(18)[16]


def read(path):
    """Return a file's bytes."""
    with open(path, "rb") as f:
        return f.read()


def textured_material(name, base_image, normal_image=None):
    """Build a material, creating its Normal Map branch *before* Base Color.

    Node order in ``node_tree.nodes`` is creation order, so this is the exact
    shape that used to export the normal map as the game texture: any earlier
    node with a linked output won under the old rule.

    Args:
        name: Material name, which is also the texture ID it exports as.
        base_image: Image wired into the Principled BSDF's Base Color.
        normal_image: Image wired into a Normal Map node first, or ``None``.

    Returns:
        The material.
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    if normal_image is not None:
        tex_normal = nodes.new("ShaderNodeTexImage")
        tex_normal.image = normal_image
        normal_map = nodes.new("ShaderNodeNormalMap")
        links.new(tex_normal.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], bsdf.inputs["Normal"])
    tex_base = nodes.new("ShaderNodeTexImage")
    tex_base.image = base_image
    links.new(tex_base.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def add_material(mat):
    """Give the first mesh in the file another material slot, used by a face."""
    obj = next(o for o in bpy.data.objects if o.type == "MESH")
    obj.data.materials.append(mat)
    obj.data.polygons[0].material_index = len(obj.data.materials) - 1
    return obj


def export_here(name, fmt="PNG"):
    """Export whatever is currently in the file, without reloading it.

    ``export_with`` wipes and re-imports first, which is wrong for the cases
    that are *about* the state of the scene -- an unsaved paint edit does not
    survive a wipe.

    Args:
        name: Subfolder of the work directory to export into.
        fmt: Texture format for the export dialog.

    Returns:
        An ``(out_dir, operator)`` pair.
    """
    out_dir = os.path.join(work, name)
    os.makedirs(out_dir, exist_ok=True)
    op = ExportOp(fmt)
    save_pof(bpy.context, os.path.join(out_dir, "model.pof"), op)
    return out_dir, op


def neutral_encode(image, destination, fmt):
    """Encode ``image`` the way an untone-mapped, alpha-preserving save would.

    The reference every conversion is compared against: the scene's own
    settings are what the export has to override, so the comparison has to
    spell out the values it is meant to end up at rather than trusting them.

    Args:
        image: Image to encode.
        destination: File to write.
        fmt: Blender ``file_format`` identifier.
    """
    scene = bpy.context.scene
    settings = scene.render.image_settings
    saved = (
        scene.view_settings.view_transform,
        settings.file_format,
        settings.color_mode,
        settings.color_depth,
    )
    scene.view_settings.view_transform = "Standard"
    settings.file_format = fmt
    settings.color_mode = "RGBA"
    settings.color_depth = "8"
    image.save_render(filepath=destination, scene=scene)
    (
        scene.view_settings.view_transform,
        settings.file_format,
        settings.color_mode,
        settings.color_depth,
    ) = saved


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
neutral_encode(img, reference, "TARGA")

exported = os.path.join(tex_dir, source.textures[0] + ".tga")
check("converted texture matches a neutral encode, not an AgX one",
      read(exported) == read(reference),
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

# --- 7. the image exported is the one driving Base Color -----------------
# A PBR material has several images and Descent 3 takes one. Picking the first
# linked image-texture node picks whichever was *created* first, so this
# material -- normal map built first, base colour second -- used to export its
# normal map as the game texture and render lilac in the game.
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
base_img = bpy.data.images.load(os.path.join(src, "Hull.png"), check_existing=True)
normal_img = bpy.data.images.load(os.path.join(src, "Turret.png"), check_existing=True)
add_material(textured_material("nodeorder", base_img, normal_image=normal_img))
out, op = export_here("nodeorder")

written_tex = os.path.join(out, EXPORT_DIR, "nodeorder.png")
check("a Base Color texture is written even when a Normal Map node came first",
      os.path.isfile(written_tex), str(sorted(os.listdir(os.path.join(out, EXPORT_DIR)))))
if os.path.isfile(written_tex):
    base_bytes = read(os.path.join(src, "Hull.png"))
    normal_bytes = read(os.path.join(src, "Turret.png"))
    exported_bytes = read(written_tex)
    check("the exported image is the Base Color one, not the normal map",
          exported_bytes == base_bytes,
          "base colour" if exported_bytes == base_bytes else
          "normal map" if exported_bytes == normal_bytes else "neither source")

# --- 7b. ... even when it arrives through a Mix node ---------------------
# The standard way to layer a detail texture over a diffuse one is a Mix node
# with a mask driving its Factor. Following the Mix's inputs in the order
# Blender stores them reaches Factor first -- socket 0 of ten, because the node
# keeps one input set per data type -- so the *mask* was exported as the game
# texture: a greyscale image where the diffuse should be, and a worse answer
# than the naive rule this search replaced.
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
diffuse_img = bpy.data.images.load(os.path.join(src, "Hull.png"), check_existing=True)
mask_img = bpy.data.images.load(os.path.join(src, "Turret.png"), check_existing=True)

mixed = bpy.data.materials.new(name="masked")
mixed.use_nodes = True
nodes, links = mixed.node_tree.nodes, mixed.node_tree.links
bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
# Created first, so "whichever node came first" cannot be what makes this pass.
tex_mask = nodes.new("ShaderNodeTexImage")
tex_mask.image = mask_img
tex_diffuse = nodes.new("ShaderNodeTexImage")
tex_diffuse.image = diffuse_img
mix = nodes.new("ShaderNodeMix")
mix.data_type = "RGBA"
# By name *and* type: the node carries a Factor and an A for every data type it
# supports, and only the RGBA pair is the one this Mix is actually blending.
links.new(tex_mask.outputs["Color"],
          next(s for s in mix.inputs if s.name == "Factor" and s.type == "VALUE"))
links.new(tex_diffuse.outputs["Color"],
          next(s for s in mix.inputs if s.name == "A" and s.type == "RGBA"))
links.new(next(s for s in mix.outputs if s.type == "RGBA"),
          bsdf.inputs["Base Color"])
add_material(mixed)
out, op = export_here("masked")

written_tex = os.path.join(out, EXPORT_DIR, "masked.png")
check("a Mix-driven material exports an image at all",
      os.path.isfile(written_tex),
      str(sorted(os.listdir(os.path.join(out, EXPORT_DIR)))))
if os.path.isfile(written_tex):
    exported_bytes = read(written_tex)
    diffuse_bytes = read(os.path.join(src, "Hull.png"))
    mask_bytes = read(os.path.join(src, "Turret.png"))
    check("the exported image is the blended colour, not the mask",
          exported_bytes == diffuse_bytes,
          "diffuse" if exported_bytes == diffuse_bytes else
          "mask" if exported_bytes == mask_bytes else "neither source")

# --- 8. a texture name shaped like a path escape is refused --------------
# Blender allows separators in a datablock name, so this is a material a user
# can type and a TXTR chunk can carry. Joined onto the export folder it climbs
# out of it and overwrites whatever it lands on, without a prompt.
escapes = ["../../../d3_escape_probe", "..\\..\\..\\d3_escape_win"]
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
# Reloaded rather than carried over: wipe() removed the datablock the previous
# section was holding, and a stale reference to one is a crash, not an error.
base_img = bpy.data.images.load(os.path.join(src, "Hull.png"), check_existing=True)
named = []
for escape in escapes:
    mat = textured_material(escape, base_img)
    named.append(mat.name)
    add_material(mat)
check("Blender kept the traversal-shaped material names verbatim",
      named == escapes, str(named))

targets = [
    os.path.normpath(os.path.join(work, "escape", EXPORT_DIR, name + ".png"))
    for name in named
]
for target in targets:
    if os.path.exists(target):  # left by an earlier, broken run
        os.remove(target)

out, op = export_here("escape")
for name, target in zip(named, targets):
    check(f"'{name}' was not written outside the export folder",
          not os.path.exists(target), target)
    check(f"'{name}' was reported as a problem",
          any(name in msg for _, msg in op.reports),
          str([m for _, m in op.reports]))
check("refusing a name does not stop the rest of the textures",
      sorted(os.listdir(os.path.join(out, EXPORT_DIR)))
      == sorted(t + ".png" for t in source.textures),
      str(sorted(os.listdir(os.path.join(out, EXPORT_DIR)))))

# The model still references the refused ID -- it has to, the ID is the round
# trip's identity and rewriting it would point the faces at a different texture.
# So the user has to be told about the *model*, not only about the file that was
# not written, and told even when they asked for no texture images at all: the
# ID goes into the TXTR chunk either way, and that path used to say nothing.
model_path = os.path.join(out, "model.pof")
exported_model = poformat.parse_pof(open(model_path, "rb").read())
check("the unusable ID really is in the model's texture list",
      all(name in exported_model.textures for name in named),
      str(exported_model.textures))
check("the user is told the model references a name no image can be found for",
      all(any(name in msg and "cannot be a filename" in msg
              for _, msg in op.reports)
          for name in named),
      str([m for _, m in op.reports]))

_, none_op = export_here("escape_no_textures", fmt="NONE")
check("and is told it with texture images switched off too",
      all(any(name in msg and "cannot be a filename" in msg
              for _, msg in none_op.reports)
          for name in named),
      str([m for _, m in none_op.reports]))

# --- 9. the scene's Output Properties must not eat the alpha channel -----
# save_render encodes through scene.render.image_settings, which is the panel
# the user set up for rendering. A scene on BW writes 8-bit greyscale Targas:
# every glass and grate texture exports opaque, and the file looks fine in a
# viewer.
scene.render.image_settings.color_mode = "BW"
out, op = export_with("TARGA", "alpha")
tex_dir = os.path.join(out, EXPORT_DIR)
files = sorted(os.listdir(tex_dir)) if os.path.isdir(tex_dir) else []
check("textures keep their alpha channel whatever the scene renders as",
      bool(files) and all(tga_depth(os.path.join(tex_dir, f)) == 32 for f in files),
      str([(f, tga_depth(os.path.join(tex_dir, f))) for f in files]))
check("the scene's own colour mode is restored after export",
      scene.render.image_settings.color_mode == "BW",
      scene.render.image_settings.color_mode)
scene.render.image_settings.color_mode = "RGBA"

# --- 10. unsaved edits are exported, not the stale file on disk ----------
# Texture Paint keeps its strokes in the datablock until somebody saves. The
# copy fast path would hand over the file as it was before the artist started.
wipe()
load_pof(bpy.context, MODEL, FakeImportOp())
edited_name = source.textures[0]
edited = next(
    n.image for n in bpy.data.materials[edited_name].node_tree.nodes
    if n.type == "TEX_IMAGE" and n.image
)
pixels = list(edited.pixels)
pixels[0:4] = [0.0, 1.0, 0.0, 1.0]
edited.pixels[:] = pixels
check("editing pixels marks the image dirty", edited.is_dirty)

out, op = export_here("dirty")
exported = os.path.join(out, EXPORT_DIR, edited_name + ".png")
stale = read(os.path.join(src, edited_name + ".png"))
check("an image with unsaved edits is not copied from disk",
      os.path.isfile(exported) and read(exported) != stale,
      "the stale file on disk was copied")

reference = os.path.join(work, "edited.png")
neutral_encode(edited, reference, "PNG")
check("the exported bytes are the edited pixels, neutrally encoded",
      os.path.isfile(exported) and read(exported) == read(reference),
      f"{os.path.getsize(exported)} vs {os.path.getsize(reference)} bytes")

untouched = source.textures[1]
check("an unedited image still takes the byte-for-byte copy path",
      read(os.path.join(out, EXPORT_DIR, untouched + ".png"))
      == read(os.path.join(src, untouched + ".png")))

# --- 11. an export that writes nothing leaves no folder behind -----------
wipe()
bpy.ops.mesh.primitive_cube_add()
bare = bpy.data.materials.new(name="NoTextureAnywhere")
bare.use_nodes = True
next(o for o in bpy.data.objects if o.type == "MESH").data.materials.append(bare)

out, op = export_here("nothing")
check("the model is still written when no texture can be",
      os.path.isfile(os.path.join(out, "model.pof")))
check("nothing writable leaves no empty texture folder beside the model",
      not os.path.isdir(os.path.join(out, EXPORT_DIR)),
      str(sorted(os.listdir(out))))
check("and the reason is reported",
      any("NoTextureAnywhere" in msg for _, msg in op.reports),
      str([m for _, m in op.reports]))

wipe()
shutil.rmtree(work, ignore_errors=True)

print()
if failures:
    print("FAILURES: " + ", ".join(failures))
    sys.exit(1)
print("all texture-export checks passed")
