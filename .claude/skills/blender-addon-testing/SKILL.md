---
name: blender-addon-testing
description: Install, deploy, and headless-test the Descent 3 POF/OOF Blender add-on. Use whenever changing descent3_plugin/ and needing to confirm import/export works in real Blender (not just pytest) — verifying meshes, materials, textures, UVs, or the hierarchy without opening the GUI.
---

# Testing the Descent 3 add-on in Blender

pytest only covers the `bpy`-free code: `poformat.py`, `mathutil.py`,
`texutil.py`, `naming.py`, `config.py`, `constants.py`, and the package
`__init__.py`, which defers its Blender imports into `register()`. That list is
not a description, it is a contract — `tests/test_package_import.py` imports
each of those modules in a subprocess and fails if `bpy` turns up in
`sys.modules`, so a module-scope `import bpy` cannot quietly shrink the pytest
surface.

The rest (`import_pof.py`, `export_pof.py`, `texexport.py`, `preferences.py`)
imports `bpy` at module scope. Everything they own — materials, images, mesh
build, object transforms, UV orientation, texture image writing, operator
registration — must be verified by running real Blender headless.

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

### Deploying: use the install scripts

`install.ps1` (Windows) and `install.sh` (Linux/macOS) at the repo root already
do all of this. They find every Blender on the machine, work out each one's
version, and copy the **whole package folder** — every `.py` *and*
`blender_manifest.toml` — into that version's user add-ons directory, clearing
stale `__pycache__` on the way. Prefer them to a hand-rolled copy: they are the
one place the "complete set" rule below is enforced.

```powershell
.\install.ps1 -List      # every Blender found + where it would install; changes nothing
.\install.ps1            # install into the newest version
.\install.ps1 -All       # install into every version found
.\install.ps1 -DryRun    # print what would happen
.\install.ps1 -Target "D:\BlenderPortable\5.2"   # skip detection entirely
```

```bash
./install.sh --list      # same flags long-form: --all --version 4.2 --dry-run --clean --target
```

### Discovery by hand (verified on this machine)

Still worth having, because the headless test patterns below need the *path* to
`blender.exe`, which installing does not give you. Deploying by hand is the
fallback for a Blender the scripts cannot find; if you do it, copy the manifest
as well — the step below does, and the reason is the same bug that made this
section's warning necessary.

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
$probe = & $blender --background --factory-startup --python-exit-code 1 --python-expr `
    "import bpy; print('BL_SCRIPTS', bpy.utils.user_resource('SCRIPTS'))"
$scripts = ($probe | Select-String '^BL_SCRIPTS (.+)$' | Select-Object -First 1).Matches[0].Groups[1].Value

$repo   = git rev-parse --show-toplevel      # run from anywhere in the repo
$addons = Join-Path $scripts "addons\descent3_plugin"
New-Item -ItemType Directory -Force -Path $addons | Out-Null
# Both lines, not just the first: '*.py' alone leaves blender_manifest.toml
# behind, which is what the extension loader reads. install.ps1 carries the
# same pair for the same reason.
Copy-Item "$repo\descent3_plugin\*.py" $addons -Force
Copy-Item "$repo\descent3_plugin\blender_manifest.toml" $addons -Force
if (Test-Path "$addons\__pycache__") { Remove-Item -Recurse -Force "$addons\__pycache__" }
```

Not on Windows? Same idea, different roots: `~/.config/blender/<ver>/scripts`
(Linux), `~/Library/Application Support/Blender/<ver>/scripts` (macOS), Steam
under `~/.steam/steam/steamapps/common/Blender`. Still ask Blender rather than
assuming.

## CRITICAL: install as a package folder, never loose files

The add-on uses a relative import (`from . import poformat`). It **must** be
installed as a folder:

```
scripts/addons/descent3_plugin/{__init__.py, config.py, constants.py,
                               export_pof.py, import_pof.py, mathutil.py,
                               naming.py, poformat.py, preferences.py,
                               texexport.py, texutil.py,
                               blender_manifest.toml}
```

Eleven `.py` modules and the manifest — twelve files. Do not check a deployment
against the list above from memory; the package grows, and a list that has gone
stale is worse than no list, because it reads as authoritative while quietly
omitting the module you forgot. Ask git:

```powershell
git ls-tree HEAD --name-only descent3_plugin/
```

If the `.py` files are dropped **loose** into `scripts/addons/` instead, Blender
can't load the package: the operator never registers and Blender warns
`add-on missing 'bl_info'` about `poformat.py`. This exact mistake caused the
"models import without textures" bug — the fixed code simply wasn't running.

Copy **all** of them — every `.py` *and* `blender_manifest.toml` — and clear
`__pycache__`. Deploying only some fails in two different ways, and only one is
loud: a missing module the package imports (`texutil.py`, `constants.py`,
`naming.py`) raises at registration, but a missing `blender_manifest.toml` just
makes the extension loader ignore the folder. `install.ps1` / `install.sh` copy
and verify the whole set, so prefer them. A running Blender session loads the
add-on once, so after redeploying, restart Blender or toggle the add-on off/on
in Preferences.

## Enable in the real config and persist

```python
import bpy, addon_utils
addon_utils.modules_refresh()
addon_utils.enable("descent3_plugin", default_set=True, persistent=True)
print(hasattr(bpy.types, "IMPORT_SCENE_OT_descent3_pof"))  # True == registered
bpy.ops.wm.save_userpref()   # so it stays enabled next launch
```
Run this with the real config (NO `--factory-startup`), and still with the exit
flag, because a registration failure has to be an error and not a log line:

```powershell
& $blender --background --python-exit-code 1 --python enable_addon.py
```

## Headless test patterns

Run: `blender.exe --background --factory-startup --python-exit-code 1 --python <script.py>`

**`--python-exit-code 1` is not optional, on any invocation in this file.**
Blender exits 0 when a script raises or fails to parse, so a test that dies
before reaching its own `sys.exit(1)` reads as a pass.

Measured on this machine, and the asymmetry is the whole trap:

| script does | without the flag | with it |
|-------------|------------------|---------|
| `sys.exit(1)` | **1** | 1 |
| raises `RuntimeError` | **0** | 1 |
| syntax error | **0** | 1 |

An explicit `sys.exit(1)` propagates either way. So a test that fails its own
assertion reports correctly with the flag missing, and only a test that *crashes
on the way there* — the case you most need to hear about, and the one a
half-finished refactor produces — silently passes. That is why the flag went
unnoticed as missing in this file for so long, and why a green run proves
nothing about a command that omits it.

**A) Call the import/export functions directly** (fastest; no install needed,
bypasses operator registration). This is what `tests/blender/test_uv_roundtrip.py`
does — start there, it already has the fake-operator scaffolding:

```python
import bpy, sys, os
sys.path.insert(0, r"<repo root>")
from descent3_plugin.import_pof import load_pof
from descent3_plugin.export_pof import save_pof
class FakeImportOp:
    import_guns = import_attach = False
    texture_dir = ""
    def report(self, level, msg): print("[REPORT]", level, msg)
class FakeExportOp:
    export_selected = False
    export_guns = export_attach = True
    export_version = "CONFIG"       # or "2300"; see EXPORT_VERSION_ITEMS
    texture_format = "NONE"         # or "PNG" / "TARGA" / "CONFIG"
    def report(self, level, msg): print("[REPORT]", level, msg)
load_pof(bpy.context, r"...\model.oof", FakeImportOp())
save_pof(bpy.context, r"...\out.pof", FakeExportOp())
```

Give the export stand-in **every** attribute the operator declares, not just the
ones you care about. `save_pof` reads `export_selected` directly, so a class
missing it raises; and the options are not independent — `export_selected`
bounds which gun and attach empties are collected, not only which meshes (a
marker survives it by being selected *or* by being parented to an exported
mesh), so a stand-in that omits it tests a path no user is on. Passing `None`
instead of a stand-in is a third behaviour again: it exports every mesh in the
file and *no* gun or attach points, rather than falling back to the operator's
defaults.

Two more things about export worth knowing before writing a test that asserts
coordinates: geometry goes through the **evaluated** depsgraph and each object's
`matrix_world`, so modifiers, rotation and scale are all baked into what is
written; and a scene built with `bpy.data.objects.new` needs
`context.view_layer.update()` (or simply asking for the depsgraph, which export
does) before those matrices are current.

**B) Exercise the real operator** without touching the user's config: point
`BLENDER_USER_SCRIPTS` at a scratch dir containing `addons/descent3_plugin/`,
then:
```python
import addon_utils
addon_utils.modules_refresh(); addon_utils.enable("descent3_plugin")
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
- **`Material.use_nodes` cannot be turned off.** Measured on Blender 5.2 LTS: the
  property is not read-only, but the assignment does not stick —
  `m = bpy.data.materials.new("X"); m.use_nodes = False; m.use_nodes` is `True`.
  Materials there are always node-based. So a test that sets up "a plain
  non-node material the user authored" does not get one, and an assertion that
  the add-on left `use_nodes` alone can never pass whatever the add-on does.
  Assert on the node *tree* instead — node count, or that no `TEX_IMAGE` node
  was added — which is the thing the user would actually notice changing. By the
  same token, add-on code guarding with `if not mat.use_nodes:` is dead on this
  Blender; keep it for older builds, do not rely on it firing.

## Verifying an import worked

```python
for obj in bpy.data.objects:
    if obj.type != "MESH": continue
    print(obj.name, "polys", len(obj.data.polygons), "uv", [u.name for u in obj.data.uv_layers])
    from collections import Counter
    print("  mat tally:", dict(Counter(obj.data.materials[p.material_index].name for p in obj.data.polygons)))
    for m in obj.data.materials:
        # Guard the node tree rather than reaching straight for `.nodes`. On 5.2
        # every material has one (see the use_nodes gotcha), but a .blend from an
        # older Blender can still carry a non-node material, and import now
        # reuses a user's material *unmodified* — so the case where node_tree is
        # None is exactly the case where the add-on behaved correctly, and it
        # should not be an AttributeError in your test.
        nodes = m.node_tree.nodes if (m.use_nodes and m.node_tree) else []
        img = next((n.image.name for n in nodes if n.type=="TEX_IMAGE" and n.image), None)
        print("  MAT", m.name, "image=", img, "d3_texture=", m.get("d3_texture"))
```
Cross-check the material tally against the parser's expected per-texture face
count (parse with `poformat.parse_pof`, tally `model.textures[f.texnum]` over
faces with >=3 verts). They must match — a mismatch means face/UV/material
desync.

The `d3_texture` custom property printed above
(`constants.PROP_KEY_TEXTURE`) is the texture ID the material stands for, which
import records and export reads back. Check it rather than the material's name:
the name is what Blender may have appended `.001` to, the property is what the
file actually said.

It is **not** the test for "the add-on created this material" — that is
`d3_addon_material` (`constants.PROP_KEY_ADDON_MATERIAL`), and the distinction is
load-bearing. Import records `d3_texture` on a user's material as well, so its
faces still export as the right texture; when authorship was read off that same
key, the user's material was spared on the first import and restyled on the
second, because it came back from import 1 wearing what import 2 took for the
add-on's signature. If you are asserting "import left this alone", assert on the
node tree and on `d3_addon_material` being absent, and **import twice** — once is
not the reproduction.

**UV orientation:** Descent 3 puts the UV origin at the top-left, Blender at the
bottom-left, so import negates V and export must negate it back. When only one
side did, every exported texture came out vertically mirrored and nothing in
pytest noticed. `tests/blender/test_uv_roundtrip.py` guards this; run it after
any change to mesh building or UV handling:

```powershell
& $blender --background --factory-startup --python-exit-code 1 --python tests\blender\test_uv_roundtrip.py
```

Exit 0 is a pass **only because of that flag** — see the table above. It is the
same for its neighbours, which are worth running as a set after anything that
touches conversion:

```powershell
foreach ($t in "test_uv_roundtrip", "test_material_reuse",
                "test_coordinate_system", "test_texture_export",
                "test_bad_geometry") {
    & $blender --background --factory-startup --python-exit-code 1 `
        --python "tests\blender\$t.py"
    if ($LASTEXITCODE -ne 0) { throw "$t failed" }
}
```

`test_bad_geometry.py` is the odd one out: it feeds Blender faces with
out-of-range and repeated vertex indices, and a regression there does not print
`FAIL` — the process dies with an EXCEPTION_ACCESS_VIOLATION and `$LASTEXITCODE`
is 11. Measured on 5.2.0 LTS, `from_pydata` accepts both kinds happily (a
polygon really does read back as `(0, 1, -3)`); what dies is
`normals_split_custom_set_from_vertices`, and `mesh.validate()` before it is what
prevents that. If you reorder anything in `_import_submodel`, run this one.

`$LASTEXITCODE` is the only thing to trust here: these scripts print their own
progress, and a run that ends in a stack trace still prints most of it.

## Sample data
- `tests/fixtures/textured_model/` — committed real-format `.oof` + PNGs; small
  and safe for automated checks, and the only sample guaranteed to be there.
  Regenerate with `python tests/generate_fixtures.py`.
- `tests/mock_data/` — hand-written `.pof` files for parser tests. Note these
  omit the trailing NUL on strings (the reader tolerates both); prefer
  `poformat.write_pof` output for a realistic fixture.
- `data/` — real Descent 3 `.OOF` + `.png` files, if someone dropped them there.
  It is gitignored and **absent on a fresh clone**, so check before writing a
  command around it rather than assuming, and never make a test depend on it.

See also the `oof-pof-format` skill for the binary format itself.
