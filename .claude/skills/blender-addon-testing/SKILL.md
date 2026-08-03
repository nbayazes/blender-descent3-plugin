---
name: blender-addon-testing
description: Install, deploy, and headless-test the Descent 3 POF/OOF Blender add-on. Use whenever changing descent3_importer/ and needing to confirm import/export works in real Blender (not just pytest) — verifying meshes, materials, textures, UVs, or the hierarchy without opening the GUI.
---

# Testing the Descent 3 add-on in Blender

pytest only covers the `bpy`-free code (`poformat.py`, `texutil.py`,
`constants.py`, and the package `__init__.py`, which defers its Blender
imports into `register()`). Anything touching `bpy` (materials, images, mesh
build, UV orientation, operator registration) must be verified by running
real Blender headless.

## Locating Blender

Do not hardcode a path or a version. Installs differ by source, and the version
directory scheme changes with every release.

**Never infer the config directory from the install directory.** A Steam install
accumulates version folders (`2.78`, `3.4`, `4.2`, `5.2`, …) next to
`blender.exe` from years of in-place updates; none of them is the user config.
Portable installs invert the rule again — Blender switches to portable mode when
a `config` folder exists under `<blender.exe dir>/<version>/`, and then ignores
`%APPDATA%` entirely.

Ask Blender instead. It is one subprocess call (~0.5 s) and is always right:

```python
bpy.app.version_string              # "5.2.0 LTS"
bpy.app.binary_path                 # which blender.exe is actually running
bpy.utils.user_resource('SCRIPTS')  # .../Blender/<ver>/scripts  -> add-ons go in ./addons
bpy.utils.user_resource('EXTENSIONS')
bpy.utils.user_resource('CONFIG')
```

### Discovery + deploy (verified on this machine)

```powershell
$candidates = @()
$onPath = (Get-Command blender -ErrorAction SilentlyContinue).Source
if ($onPath) { $candidates += $onPath }

# Steam: libraries can be on any drive; libraryfolders.vdf lists them all.
foreach ($vdf in @("${env:ProgramFiles(x86)}\Steam\steamapps\libraryfolders.vdf",
                   "$env:ProgramFiles\Steam\steamapps\libraryfolders.vdf")) {
    if (Test-Path $vdf) {
        Select-String -Path $vdf -Pattern '"path"\s+"(.+?)"' | ForEach-Object {
            $lib = $_.Matches[0].Groups[1].Value -replace '\\\\', '\'
            $candidates += "$lib\steamapps\common\Blender\blender.exe"
        }
    }
}

# Installer / winget / portable unzip. Newest version directory first.
foreach ($root in @("$env:ProgramFiles\Blender Foundation",
                    "${env:ProgramFiles(x86)}\Blender Foundation",
                    "$env:LOCALAPPDATA\Programs\Blender Foundation")) {
    if (Test-Path $root) {
        $candidates += Get-ChildItem $root -Directory | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "blender.exe" }
    }
}

$blender = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $blender) { throw "No Blender found - set `$blender manually." }

# Ask Blender where its user scripts live rather than rebuilding the path.
$probe = & $blender --background --factory-startup --python-expr `
    "import bpy; print('BL_SCRIPTS', bpy.utils.user_resource('SCRIPTS'))"
$scripts = ($probe | Select-String '^BL_SCRIPTS (.+)$' | Select-Object -First 1).Matches[0].Groups[1].Value

$repo   = git rev-parse --show-toplevel      # run from anywhere in the repo
$addons = Join-Path $scripts "addons\descent3_importer"
New-Item -ItemType Directory -Force -Path $addons | Out-Null
Copy-Item "$repo\descent3_importer\*.py" $addons -Force
if (Test-Path "$addons\__pycache__") { Remove-Item -Recurse -Force "$addons\__pycache__" }
```

Not on Windows? Same idea, different roots: `~/.config/blender/<ver>/scripts`
(Linux), `~/Library/Application Support/Blender/<ver>/scripts` (macOS), Steam
under `~/.steam/steam/steamapps/common/Blender`. Still ask Blender rather than
assuming.

> Issue #3 covers a proper install/detection script. Once it exists this section
> should shrink to a call into it — keep the logic in one place.

## CRITICAL: install as a package folder, never loose files

The add-on uses a relative import (`from . import poformat`). It **must** be
installed as a folder:

```
scripts/addons/descent3_importer/{__init__.py, constants.py, export_pof.py,
                                 import_pof.py, poformat.py, texutil.py}
```

If the `.py` files are dropped **loose** into `scripts/addons/` instead, Blender
can't load the package: the operator never registers and Blender warns
`add-on missing 'bl_info'` about `poformat.py`. This exact mistake caused the
"models import without textures" bug — the fixed code simply wasn't running.

Copy **all** the `.py` files and clear `__pycache__`; deploying only some (e.g.
forgetting `texutil.py` or `constants.py`) makes the add-on fail to import.
`install.ps1` / `install.sh` copy and verify the whole set, so prefer them. A running Blender
session loads the add-on once, so after redeploying, restart Blender or toggle
the add-on off/on in Preferences.

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

**A) Call the import/export functions directly** (fastest; no install needed,
bypasses operator registration). This is what `tests/blender/test_uv_roundtrip.py`
does — start there, it already has the fake-operator scaffolding:

```python
import bpy, sys, os
sys.path.insert(0, r"<repo root>")
from descent3_importer.import_pof import load_pof
class FakeOp:
    import_guns = import_attach = False
    texture_dir = ""
    def report(self, level, msg): print("[REPORT]", level, msg)
load_pof(bpy.context, r"...\model.oof", FakeOp())
```

**B) Exercise the real operator** without touching the user's config: point
`BLENDER_USER_SCRIPTS` at a scratch dir containing `addons/descent3_importer/`,
then:
```python
import addon_utils
addon_utils.modules_refresh(); addon_utils.enable("descent3_importer")
bpy.ops.import_scene.descent3_pof(filepath=r"...\model.oof", texture_dir=r"...\tex")
```

### Gotchas
- `bpy.ops.wm.read_factory_settings()` **disables the add-on** — re-enable after,
  or don't call it. To reset state between scenarios in one process, wipe
  `bpy.data` manually — but leave `bpy.data.collections` alone. Removing the
  scene's master collection children leaves the view layer without a valid
  object list, and iterating it then yields `None` (which blows up in
  `import_pof`'s selection loop):
  ```python
  def wipe():
      for c in (bpy.data.objects, bpy.data.meshes, bpy.data.materials):
          for d in list(c): c.remove(d)
      bpy.context.view_layer.update()
  ```
- `--factory-startup` scenes contain a default `Cube`/`Material` — wipe or filter
  them out, otherwise a full-scene export picks them up.

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

**UV orientation:** Descent 3 puts the UV origin at the top-left, Blender at the
bottom-left, so import negates V and export must negate it back. When only one
side did, every exported texture came out vertically mirrored and nothing in
pytest noticed. `tests/blender/test_uv_roundtrip.py` guards this; run it after
any change to mesh building or UV handling:

```powershell
& $blender --background --factory-startup --python tests\blender\test_uv_roundtrip.py
```
It exits non-zero on failure.

## Sample data
- `data/` — real Descent 3 `.OOF` + `.png` files (gitignored, present locally).
- `tests/fixtures/textured_model/` — committed real-format `.oof` + PNGs; small
  and safe for automated checks. Regenerate with `python tests/generate_fixtures.py`.

See also the `oof-pof-format` skill for the binary format itself.
