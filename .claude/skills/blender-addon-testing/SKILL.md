---
name: blender-addon-testing
description: Install, deploy, and headless-test the Descent 3 POF/OOF Blender add-on. Use whenever changing descent3_importer/ and needing to confirm import/export works in real Blender (not just pytest) — verifying meshes, materials, textures, UVs, or the hierarchy without opening the GUI.
---

# Testing the Descent 3 add-on in Blender

pytest only covers the `bpy`-free code (`poformat.py`, `texutil.py`). Anything
touching `bpy` (materials, images, mesh build, operator registration) must be
verified by running real Blender headless.

## Blender location

On this machine: `E:\Steam\steamapps\common\Blender\blender.exe` (5.2 LTS).
Config/add-ons: `C:\Users\Frog\AppData\Roaming\Blender Foundation\Blender\5.2\`.
To find it elsewhere: search `steamapps\common\Blender`, `%ProgramFiles%\Blender Foundation`, or `%LOCALAPPDATA%\Programs\Blender Foundation`.

## CRITICAL: install as a package folder, never loose files

The add-on uses a relative import (`from . import poformat`). It **must** be
installed as a folder:

```
scripts/addons/descent3_importer/{__init__.py, poformat.py, texutil.py}
```

If the `.py` files are dropped **loose** into `scripts/addons/` instead, Blender
can't load the package: the operator never registers and Blender warns
`add-on missing 'bl_info'` about `poformat.py`. This exact mistake caused the
"models import without textures" bug — the fixed code simply wasn't running.

## Deploy the current repo code to the install

```powershell
$addons = "C:\Users\Frog\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons\descent3_importer"
$repo   = "C:\Users\frog\Desktop\blender-pof-plugin\descent3_importer"
New-Item -ItemType Directory -Force -Path $addons | Out-Null
Copy-Item "$repo\__init__.py","$repo\poformat.py","$repo\texutil.py" $addons -Force
if (Test-Path "$addons\__pycache__") { Remove-Item -Recurse -Force "$addons\__pycache__" }
```

Always copy **all three** files and clear `__pycache__` — deploying only some
(e.g. forgetting `texutil.py`) makes the add-on fail to import.
A running Blender session loads the add-on once; after redeploying, the user
must **restart Blender** or toggle the add-on off/on in Preferences.

## Enable in the real config and persist

```python
import bpy, addon_utils
addon_utils.modules_refresh()
addon_utils.enable("descent3_importer", default_set=True, persistent=True)
print(hasattr(bpy.types, "IMPORT_SCENE_OT_descent3_pof"))  # True == registered
bpy.ops.wm.save_userpref()   # so it stays enabled next launch
```
Run this with the real config (NO `--factory-startup`).

## Headless test patterns

Run: `blender.exe --background --factory-startup --python <script.py>`

**A) Call the import function directly** (fastest; bypasses operator/registration):
```python
import bpy, sys, os
sys.path.insert(0, r"C:\Users\frog\Desktop\blender-pof-plugin")
from descent3_importer import import_pof
class FakeOp:
    import_guns = import_attach = False
    texture_dir = ""
    def report(self, level, msg): print("[REPORT]", level, msg)
import_pof(bpy.context, r"...\model.oof", FakeOp())
```

**B) Exercise the real operator.** Put the package under a dir named by the
`BLENDER_USER_SCRIPTS` env var (`<dir>/addons/descent3_importer/`), then:
```python
import addon_utils
addon_utils.modules_refresh(); addon_utils.enable("descent3_importer")
bpy.ops.import_scene.descent3_pof(filepath=r"...\model.oof", texture_dir=r"...\tex")
```

### Gotchas
- `bpy.ops.wm.read_factory_settings()` **disables the add-on** — re-enable after,
  or don't call it. To reset state between scenarios in one process, wipe
  `bpy.data` manually instead:
  ```python
  def wipe():
      for c in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.collections):
          for d in list(c): c.remove(d)
      for img in list(bpy.data.images):
          if img.users == 0:
              try: bpy.data.images.remove(img)
              except Exception: pass
  ```
- `--factory-startup` scenes contain a default `Cube`/`Material` — filter it out
  when inspecting imported objects (imported meshes are named after submodels).

## Verifying an import worked

```python
for obj in bpy.data.objects:
    if obj.type != "MESH": continue
    print(obj.name, "polys", len(obj.data.polygons), "uv", [u.name for u in obj.data.uv_layers])
    from collections import Counter
    print("  mat tally:", dict(Counter(obj.data.materials[p.material_index].name for p in obj.data.polygons)))
    for m in obj.data.materials:
        img = next((n.image.name for n in m.node_tree.nodes if n.type=="TEX_IMAGE" and n.image), None)
        print("  MAT", m.name, "image=", img)
```
Cross-check the material tally against the parser's expected per-texture face
count (parse with `poformat.parse_pof`, tally `model.textures[f.texnum]` over
faces with >=3 verts). They must match — a mismatch means face/UV/material
desync.

## Sample data
- `data/` — real Descent 3 `.OOF` + `.png` files (gitignored, present locally).
- `tests/fixtures/textured_model/` — committed real-format `.oof` + PNGs; small
  and safe for automated checks. Regenerate with `python tests/generate_fixtures.py`.

See also the `oof-pof-format` skill for the binary format itself.
