# Descent 3 POF/OOF Importer/Exporter for Blender

A Blender add-on for importing and exporting Descent 3 polygon model files
(`.pof` / `.oof`). It supports vertices, faces, UV mapping, materials with
image textures, the submodel hierarchy, gun points, attach points, and animation
keyframes.

- **Blender:** 4.2+ (developed and tested against 5.2 LTS)
- **Location in Blender:** `File → Import/Export → Descent 3 POF/OOF (.pof, .oof)`

## Installation

> **Important:** the add-on must be installed as a **package folder**, not as
> loose files. It uses relative imports (`from . import poformat`), so all
> nine modules have to sit together inside a `descent3_plugin/` directory:
>
> ```
> scripts/addons/descent3_plugin/
>   ├── __init__.py
>   ├── blender_manifest.toml
>   ├── config.py
>   ├── constants.py
>   ├── export_pof.py
>   ├── import_pof.py
>   ├── mathutil.py
>   ├── poformat.py
>   ├── preferences.py
>   └── texutil.py
> ```
>
> If the `.py` files are dropped directly into `scripts/addons/`, Blender cannot
> load the package — the import/export operators never register and Blender warns
> that `poformat.py` is "missing `bl_info`".

### Option 1 — install script (recommended)

`install.ps1` (Windows) and `install.sh` (Linux/macOS) find every Blender on the
machine — Steam, standalone installers, portable builds, Flatpak, Snap, macOS app
bundles — work out each one's version, and copy the package folder into that
version's user add-ons directory. Run from the repo root:

```powershell
.\install.ps1 -List        # show every Blender found and where it would install
.\install.ps1              # install into the newest version
.\install.ps1 -All         # install into every version found
.\install.ps1 -Version 4.2 # install into one specific version
.\install.ps1 -DryRun      # print what would happen, change nothing
.\install.ps1 -Clean       # wipe the destination folder first
```

```bash
./install.sh --list        # same flags, long-form: --all --version 4.2 --dry-run --clean
./install.sh
```

If Blender lives somewhere unusual, skip detection with
`-Target` / `--target` and pass either a version root
(`…/Blender/5.2`) or an add-ons directory directly.

Steam and standalone Blender share the same user config directory, so a given
version only needs installing once no matter where the executable lives.

The scripts always copy the whole package folder, clear stale `__pycache__`, and
warn about the loose-file mistake described above.

### Option 2 — install by hand

1. Zip the `descent3_plugin/` folder (so the zip contains the folder, not just
   its files).
2. In Blender: `Edit → Preferences → Add-ons → Install from Disk…`, choose the
   zip, then enable **"Import-Export: Descent 3 POF/OOF Importer/Exporter"**.

Alternatively, copy the `descent3_plugin/` folder straight into your Blender
`scripts/addons/` directory and enable it in Preferences.

Either way, after updating the files **restart Blender** (or toggle the add-on
off/on) so the new code loads.

## Importing

`File → Import → Descent 3 POF/OOF (.pof)`.

![The Descent 3 import dialog: a Blender file browser listing .OOF models, with Import Gun Points, Import Attach Points and Texture Folder in the right-hand sidebar](docs/images/import-dialog.png)

Options live in the file browser sidebar:

| Option | Description |
|--------|-------------|
| **Import Gun Points** | Import gun-point markers as empty objects |
| **Import Attach Points** | Import attach points as empty objects |
| **Texture Folder** | Optional folder to search for texture images (see below). Leave empty to use only the model's own folder |

Imported textures show up as materials with an image texture wired into the
Principled BSDF's Base Color. If a model looks flat/untextured in the viewport,
switch viewport shading to **Material Preview** — *Solid* mode does not display
image textures.

## Exporting

`File → Export → Descent 3 POF/OOF (.pof)`.

![The Descent 3 export dialog, with POF Version set to "From project config", plus Selected Only, Export Gun Points and Export Attach Points](docs/images/export-dialog.png)

| Option | Description |
|--------|-------------|
| **POF Version** | Which format version to write. Leave it on *From project config* and the version in your `descent3.toml` applies; pick a specific one to override it for this export |
| **Selected Only** | Export only the selected objects rather than every mesh in the file |
| **Export Gun Points** | Write empties named with the gun prefix as GPNT gun points |
| **Export Attach Points** | Write empties named with the attach prefix as ATCH attach points |
| **Textures** | Also write each material's image beside the model. *None* by default; *From project config* uses `texture_format` from your `descent3.toml` |

Material names are written to the model's texture table, so name your
materials after the Descent 3 textures you want the faces to reference.

### Exporting textures

The **Textures** control writes each material's image alongside the model, so a
model and the images it needs can be handed over together. Files land in an
`exported_textures/` subfolder next to the `.pof` and are named after the
texture IDs in the model, which is what the importer searches for.

```
export/
  model.pof
  exported_textures/
    Hull.png
    Turret.png
```

PNG and Targa are supported. Descent 3's native **OGF is not** — Blender cannot
encode it, and offering the option would produce files the game rejects; it
lands when an encoder does.

Two things happen quietly in your favour here:

- An image whose source file is **already in the requested format is copied
  byte-for-byte**. No re-encode, no quality loss, no colour management.
- Anything that *is* converted has colour management neutralised first. Blender
  5.2 defaults its view transform to **AgX**, and the only API that genuinely
  converts formats renders through it — so textures would otherwise export
  tone-mapped and washed out. Your scene's settings are restored afterwards.

A material with no image texture is reported rather than skipped silently, so a
half-populated folder is never a surprise. Change the folder name with
`textures.export_dir`, and the default format with `export.texture_format`.

**Import does not search this folder.** Exports stay sandboxed from the models
they came from: widening the search automatically would make the importer
harder to predict, and keeping generated output out of the source pipeline is
often deliberate. If you do want those images picked up, opt in explicitly —
either point the import dialog's **Texture Folder** at it, or add it to your
project config:

```toml
[textures]
search_dirs = ["exported_textures"]
```

## How textures are located

Descent 3 models do **not** store image files. The model's `TXTR` chunk holds a
list of **texture names** (e.g. `WarningStripe`, `ConcussionMissile`), and each
face references one of those names by index. On import, the add-on has to turn
each name into an actual image file on disk.

### Search directories and their priority

The importer builds an ordered list of directories to search, then looks each
texture name up in them:

1. **Texture Folder** — if you entered one in the import options *and it exists*,
   it is searched **first**.
2. **The model's own directory** — the folder that contains the `.oof`/`.pof`
   file you are importing (`os.path.dirname(os.path.abspath(filepath))`), always
   searched, and always **last**.

The first directory that yields a match wins.

### When no Texture Folder is provided (the default)

With the **Texture Folder** field left empty, the search list contains exactly
one entry: **the model's own directory**. In other words, the importer looks for
each texture's image file *right next to the model file* and nowhere else.

- The lookup directory is derived from the file you picked in the import dialog,
  resolved to an absolute path. If you import
  `D:\descent3\models\pyro.oof`, the texture directory is `D:\descent3\models\`.
- Each texture **name** is treated as a **file stem** in that directory. The
  importer appends each supported image extension, in this order, and uses the
  first file that exists:

  ```
  .png  .bmp  .tga  .jpg  .jpeg  .ogf  .pcx
  ```

  So for a texture named `ConcussionMissile` it tries
  `ConcussionMissile.png`, then `ConcussionMissile.bmp`, and so on.

- If no exact filename matches, it falls back to a **case-insensitive** scan of
  the directory listing: any file whose name (ignoring case) is
  `<texturename>.<ext>` for a supported `<ext>` is accepted. This covers the
  common case where the model stores `blackpanel` but the file on disk is
  `BlackPanel.png`. (On Windows the filesystem is already case-insensitive, so
  the exact-name step usually resolves it; the fallback matters on
  case-sensitive filesystems.)

- The search is **not recursive** — only the model's immediate directory is
  looked at, never its subfolders or parents.

- The comparison is **exact on the stem** (apart from case). A texture named
  `Hull` matches `Hull.png` but not `Hull_01.png`, `metal_Hull.png`, or
  `Hull.001.png`.

**Practical consequence:** the simplest setup is to keep a model's `.oof` and all
of its `.png` (or other supported) textures in one folder. Import the `.oof` from
there with the Texture Folder left blank, and every texture resolves
automatically.

### When a Texture Folder *is* provided

Use this when your models and textures live in different folders — common with
Descent 3 assets extracted from `.hog` archives. The value you enter is:

- Cleaned of surrounding whitespace and quotes (so a Windows
  *"Copy as path"* value like `"D:\tex\folder"` works when pasted).
- Resolved with Blender's path rules (`//`-relative paths are made absolute).
- Searched **before** the model's own directory, using the same per-directory
  rules (exact extension order, then the case-insensitive fallback).

If the folder you enter does not exist, it is ignored (with a warning in the
console) and the importer falls back to the model's own directory.

> The **Texture Folder** field is a plain text box you paste a path into — it has
> no "browse" button. Blender only allows one file-selector dialog at a time, and
> the import file browser already occupies it, so a folder-picker cannot be
> opened from inside the import dialog.

### When a texture cannot be found

If none of the search directories contain a matching image, the importer still
creates a material named after the texture — it just has no image attached
(a plain Principled BSDF). Missing textures are reported so they are not silent:

- The import finishes with a status message such as
  `Imported pyro: 3 submodels, 5/7 textures — missing textures: X, Y`.
- The system console (`Window → Toggle System Console`) shows a
  `[Descent3] … textures N/M found (missing: …)` summary and the exact
  directories that were searched.

### Resolution at a glance

```
for each texture name referenced by the model's faces:
    for each directory in [Texture Folder (if set & exists), model directory]:
        try name + ".png", ".bmp", ".tga", ".jpg", ".jpeg", ".ogf", ".pcx"   → first hit wins
        else scan directory for a case-insensitive  name.<supported-ext>       → first hit wins
    if nothing matched anywhere:
        create a blank material named after the texture and report it missing
```

Supported extensions and their priority order default to `IMAGE_EXTENSIONS`
in `descent3_plugin/texutil.py`, and a project can override them (see
Settings below).

## Seeing what the add-on is doing

Most of what the add-on has to tell you — a texture it could not find, a
material that looks like a Blender duplicate, a file that ends mid-chunk — is
written to Blender's system console. The status bar only ever shows the last
line, so open the console before investigating anything.

`Window → Toggle System Console`

![Blender's Window menu open, with the Toggle System Console item highlighted](docs/images/system-console.png)

On Linux and macOS there is no menu item, because Blender is already attached
to the terminal that launched it — start Blender from a terminal and the output
appears there.

Typical output for a healthy import:

```
[Descent3] Importing: C:\models\Hellion.oof
[Descent3] v2300: 2 textures, 1 submodels; texture search: [...]
[Descent3] textures 2/2 found
```

and for one that needs attention:

```
[descent3_plugin] WARNING Texture not found: Hull (searched [...])
[descent3_plugin] WARNING Material 'Hull.001' looks like a Blender duplicate ...
```

## Settings

Settings live in one of three places, chosen by who owns the value.

### Project settings — `descent3.toml`

Conventions the importer and exporter must agree on. Export identifies gun
and attach points purely by name prefix, so if import writes `Gun_0` and
export looks for `gun.0`, the point is silently dropped — both halves read
this one file.

Put a `descent3.toml` at your project root; the add-on searches from the
model's folder upwards, so one file covers every model beneath it, and a
file closer to a model wins. Copy `descent3.example.toml` to start — it
documents every key and is fully commented out, so copying it verbatim
changes nothing.

```toml
[naming]
gun_prefix = "Muzzle_"

[textures]
# relative to this file, not your working directory
search_dirs = ["../shared/textures"]

[export]
version = 2200
```

A missing file means defaults, which are exactly the add-on's behaviour
before configuration existed. A malformed file degrades to those defaults
and reports the problem rather than blocking the import, and unknown keys
are reported so a typo does not fail silently.

### User settings — add-on preferences

`Edit → Preferences → Add-ons → Descent 3 POF/OOF`. These follow you between
projects and stay out of anyone else's repository: your own texture library
folder, and the viewport size of the gun and attach markers.

![The add-on's preferences in Blender: a Textures panel with a Texture Library path, and a Viewport Markers panel with Gun Marker Size and Attach Marker Size](docs/images/addon-preferences.png)

**Texture Library** is a fallback for textures you keep outside any one
project — an extracted copy of the game's bitmaps, say. It is searched *last*,
so it never overrides a texture that ships beside the model.

Texture search order, most specific first:

1. the import dialog's **Texture Folder**
2. `search_dirs` from the project's `descent3.toml`
3. the model's own folder
4. the **Texture Library** from preferences

The folder that texture *export* writes into is **not** on that list. See
[Exporting textures](#exporting-textures).

### Format constants — not configurable

Version gates, chunk IDs and flag bits are facts about the POF binary
format, not settings. They live in `poformat.py` and there is deliberately
no way to override them: a project that changed one would write models the
game cannot load. The supported version matrix is declared once as
`VersionFeatures`, which the reader and writer both consult, so the two
cannot disagree about a record's layout.

## Development

The parsing/writing (`poformat.py`), math primitives (`mathutil.py`),
texture-path logic (`texutil.py`), naming rules (`naming.py`), project
`config.py` and shared `constants.py` have **no `bpy` dependency**. Neither
does the package `__init__.py`, which defers its Blender imports into
`register()` — so the whole package imports under plain Python.

`tests/test_package_import.py` guards that contract: it fails if a
module-scope `import bpy` reappears in any of those modules.

## Running the tests

```bash
./run_tests.sh          # macOS / Linux / Git Bash
.\run_tests.ps1         # Windows PowerShell
```

That is the whole thing. The script finds Blender, uses **Blender's own Python**
to run the suite, and installs pytest the first time if it is missing.

```
==> Blender: E:\Steam\steamapps\common\Blender\blender.exe
    Python 3.13.13  (E:\Steam\...\Blender\5.2\python\bin\python.exe)
==> Running tests
286 passed in 0.53s
==> all tests passed
```

Arguments go straight to pytest:

```bash
./run_tests.sh -k naming -v
./run_tests.sh tests/test_poformat.py
```

Other options:

| Option | Effect |
|--------|--------|
| `--blender <path>` / `-Blender <path>` | use a specific Blender. Given explicitly, it is never silently substituted — a bad path is an error |
| `--system` / `-System` | use your system Python instead |
| `BLENDER=<path>` / `$env:BLENDER` | same as `--blender` |
| `--help` / `-?` | usage |

### Why Blender's Python and not yours

The add-on only ever executes inside Blender, which ships its own interpreter.
On this machine the system Python is 3.13.3 and Blender's is 3.13.13 — close
enough to lull you, far enough apart to matter. A stdlib module or syntax
feature present in one and not the other passes locally and fails on a user's
machine. Running against Blender's interpreter closes that gap, and costs
nothing: the suite is `bpy`-free, so it needs Blender's *Python*, not Blender.

pytest is installed into a git-ignored `.pytest-blender/` **in the repo, not
inside the Blender install** — a Blender update, or a Steam "verify files",
cannot remove it, and nothing under the Blender folder is modified. The path to
Blender's interpreter is cached there too, so only the first run pays the
Blender launch needed to ask where it lives.

You can of course still just run pytest directly; it exercises the same tests
against whatever Python is on your PATH:

```bash
python -m pytest -q
```

### Tests that need Blender itself

Mesh building, materials, UV orientation and operator registration only exist
inside `bpy`, so those are driven through real Blender. Each is a standalone
script that exits non-zero on failure:

```bash
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_uv_roundtrip.py
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_material_reuse.py
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_coordinate_system.py
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_texture_export.py
```

> **`--python-exit-code 1` is not optional.** Blender exits **0** when a script
> raises or fails to parse, so without it a test that crashes before reaching
> its own `sys.exit(1)` reads as a pass. That flag makes a Python failure a
> non-zero exit.

`test_uv_roundtrip.py` guards UV orientation: Descent 3 puts the UV origin at
the top-left and Blender at the bottom-left, so import negates V and export
must negate it back — when only one side did, every exported texture came out
mirrored.

`test_material_reuse.py` guards the material-name-is-the-texture-ID contract: a
second import must reuse an existing material rather than let Blender mint
`Hull.001`, which export would otherwise write into the file as a texture no
bitmap matches.

These deliberately do *not* run under pytest — `tests/blender/conftest.py` sets
`collect_ignore_glob`, so a bare `pytest` does not try to import them without
Blender.

There is also `tests/run_in_blender.py`, which runs the pytest suite *inside*
Blender rather than beside it. `run_tests.sh` is faster and is what you want
day to day; this one matters only if you add tests that themselves need `bpy`:

```bash
blender --background --factory-startup --python tests/run_in_blender.py
```

### Validating the extension manifest

```bash
blender --command extension validate descent3_plugin
```

### Versioning

`descent3_plugin/blender_manifest.toml` is the canonical version. Both
installers read it from there, and `blender --command extension validate
descent3_plugin` checks the manifest itself.

`bl_info` in `__init__.py` still carries a literal copy, and has to:
`addon_utils.modules()` pulls `bl_info` out of the file text with
`ast.literal_eval` *without importing the module*, so computing the version
at import time makes the add-on disappear from Edit → Preferences → Add-ons
entirely. `tests/test_manifest.py` fails if the two ever disagree, so the
duplication cannot silently drift.

## Licence

GPL-3.0-or-later — see `LICENSE`. The add-on redistributes the Descent 3
engine sources under `reference/`, which are GPL-3.0-or-later, © 2024
Parallax Software.

Test assets:

- `tests/mock_data/` — hand-written `.pof` files for parser tests
  (regenerate with `python tests/generate_mock_pof.py`).
- `tests/fixtures/textured_model/` — a committed real-format `.oof` plus the
  `.png` textures it references, used to test parsing + texture resolution
  end-to-end (regenerate with `python tests/generate_fixtures.py`).

Anything that touches Blender (materials, images, mesh building, operator
registration) is verified by running Blender headless against a real model; see
the `.claude/skills/blender-addon-testing` skill for the workflow, and
`.claude/skills/oof-pof-format` for the binary format reference.

## Design notes

Decisions that are not obvious from the code, and the reasoning behind them.

- **Pure-Python parser/writer.** No C extension: Blender ships `struct`, and
  keeping `poformat.py` free of `bpy` is what lets the whole format layer be
  unit-tested outside Blender.
- **Coordinate conversion is a rotation, not a mirror.** Descent 3 is
  right-handed **Y-up**; Blender is right-handed **Z-up**. Both being
  right-handed makes the difference a single 90-degree rotation about X, so
  import maps `(x, y, z)` to `(x, -z, y)` and export maps it back to
  `(x, z, -y)`. Because it is a rotation rather than a mirror, winding order is
  untouched and normals transform exactly like positions — no face reversal is
  needed anywhere.
- **The conversion happens only at the Blender boundary.** Everything inside
  `poformat` stays in file coordinates, so what the parser and writer exchange
  is always exactly what is on disk. `mathutil.descent_to_blender` and
  `blender_to_descent` are the only two places the axes change.
- **UVs need their own flip.** Descent puts the UV origin at the top-left and
  Blender at the bottom-left, so import negates V and export negates it back.
  This is separate from the axis conversion above.
- **1:1 scale.** Descent units are roughly metres and are imported unscaled.
- **Dataclass model.** Parsing produces a plain `POFModel` tree with no Blender
  types in it, so the binary layer and the scene-building layer can be changed
  and tested independently.
- **Hierarchy becomes parenting.** The submodel tree maps onto Blender object
  parenting; submodels are keyed by their own index rather than list position,
  because SOBJ chunks can arrive out of order or with gaps.
- **Custom properties carry what Blender cannot represent.** Submodel flags,
  movement type and axis, and the raw property string are stored on the object
  so a round trip does not lose commands the add-on does not itself interpret.
- **Round-trip fidelity is the correctness bar.** Import then export should
  produce an equivalent file. This is enforced by
  `tests/test_version_features.py`, which round-trips a model at every version
  where the record layout changes.
- **Gun and attach points are empties matched by name prefix.** That makes the
  prefix a contract between import and export, which is why it is configurable
  in one place both halves read (see Settings).

## Repository layout

```
descent3_plugin/      the add-on (install this folder)
  __init__.py           registration and menu entries (no bpy at import time)
  import_pof.py         ImportPOF operator and POF -> Blender conversion
  export_pof.py         ExportPOF operator and Blender -> POF conversion
  config.py             per-project descent3.toml settings (no bpy)
  preferences.py        per-user add-on preferences
  constants.py          values shared by the import and export halves (no bpy)
  mathutil.py           Vector3 and math helpers (no bpy)
  poformat.py           POF/OOF binary parser & writer (no bpy)
  texutil.py            texture-path resolution helpers (no bpy)
descent3_plugin/blender_manifest.toml   canonical version + extension metadata
descent3.example.toml   documented template for a project config
docs/images/            screenshots used by this README
LICENSE                 GPL-3.0-or-later
tests/                  pytest suite, mock data, and fixtures
reference/              vendored Descent 3 engine sources (GPL-3, not installed)
```
