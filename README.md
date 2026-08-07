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
> eleven modules have to sit together inside a `descent3_plugin/` directory,
> along with the manifest:
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
>   ├── naming.py
>   ├── poformat.py
>   ├── preferences.py
>   ├── texexport.py
>   └── texutil.py
> ```
>
> If the `.py` files are dropped directly into `scripts/addons/`, Blender cannot
> load the package — the import/export operators never register and Blender warns
> that `poformat.py` is "missing `bl_info`". Leaving *one* module behind is
> quieter and just as fatal: the add-on fails to import with a `ModuleNotFoundError`
> naming a file you may not have known existed. Copy the folder, not its contents.

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
| **Texture Folder** | Optional folder to search for texture images, ahead of everywhere else (see below). Leave it empty and the search falls back to the project's `descent3.toml`, the model's own folder, and your Texture Library preference |

Imported textures show up as materials with an image texture wired into the
Principled BSDF's Base Color. If a model looks flat/untextured in the viewport,
switch viewport shading to **Material Preview** — *Solid* mode does not display
image textures.

**A material you already had is reused, never restyled.** One material per
texture ID is what keeps a round trip honest, so if your scene already contains a
material named after one of the model's textures, the import assigns that one
rather than minting `Metal.001`. What it will *not* do is change it: a material
this add-on did not create is used exactly as it arrives — nothing added to its
node tree, nothing rewired — and the import tells you so, because the
consequence is a model that shows up untextured. Delete or rename that material
and re-import to get the model's own texture. (Materials the add-on created carry
a `d3_addon_material` property, and *those* it does finish off with the image
they were always meant to have. That is a separate mark from the `d3_texture`
property below, deliberately: adoption records `d3_texture` on your material so
its faces still export as the right texture, and if that doubled as the add-on's
signature then a second import of the same model would treat your material as its
own and restyle it after all.)

Damaged files are imported as far as they go rather than refused. A face pointing
at a vertex the submodel does not have is dropped, and so is anything Blender's
own mesh validation rejects. Both are reported with the submodel named, so
missing geometry is never silent. This is not tidiness: an out-of-range index or
a face using one vertex twice is written into the mesh without complaint, and
then kills Blender outright — an access violation, not a catchable error — the
moment the model's normals are applied to it. Every unsaved edit in the session
goes with it, not just the import.

Gun and attach markers arrive as empties **aimed the way the file aims them**.
The direction lives in the empty's rotation, which is the axis Blender draws a
single-arrow empty's arrow along — so the arrow points where the gun fires, and
rotating the empty changes it. Attach points that specify an up vector keep their
roll as well.

## Exporting

`File → Export → Descent 3 POF/OOF (.pof)`.

![The Descent 3 export dialog. POF Version and Textures are both set to "From project config"; Selected Only, Export Gun Points and Export Attach Points are ticked. The file listing shows an exported_textures folder written beside Untitled.pof](docs/images/export-dialog.png)

| Option | Description |
|--------|-------------|
| **POF Version** | Which format version to write. Leave it on *From project config* and the version in your `descent3.toml` applies; pick a specific one to override it for this export |
| **Selected Only** | Export only the selected objects rather than every mesh in the file. It bounds the gun and attach markers too, not just the meshes (see below) |
| **Export Gun Points** | Write empties named with the gun prefix as GPNT gun points |
| **Export Attach Points** | Write empties named with the attach prefix as ATCH attach points |
| **Textures** | Also write each material's image beside the model. *None* by default; *From project config* uses `texture_format` from your `descent3.toml` |

### What gets exported

Each mesh object becomes a submodel, and Blender's object parenting becomes the
model hierarchy. So does an *Empty* that came from a submodel with no geometry —
a turret's pivot, which exists to carry `$rotate=` and to be the thing the engine
turns its children about. Import records `pof_index` on it, and that property is
what tells it apart from the other empties in your file; nothing else of yours is
swept in.

**Objects are exported as you see them.** Rotation, scale and modifiers are all
applied: the exporter reads each object's evaluated mesh and world matrix, so
what lands in the file is what the viewport shows. There is no need to apply
transforms by hand first, and a mirrored or subdivided mesh exports subdivided.
Normals go through the same transform, so a scaled object's lighting stays
right.

**Authored normals are kept.** Descent 3 stores one normal per vertex, and that
is where import puts the file's normals — in Blender's custom split normals. Read
back from there rather than from Blender's computed vertex normals, so a round
trip returns the normals the model shipped with rather than the exporter's
opinion of the shading. On a mesh carrying no custom normals the computed ones
are used, which is the right answer for flat shading anyway.

A submodel's `offset` is its world position relative to its parent's. The engine
adds those offsets down the hierarchy without applying parent rotation
(`MinMaxSubmodel` in `reference/polymodel.cpp`), so world-space deltas are what
it expects — and the model's bounding box and radius are grown the same way,
from every vertex plus the offsets accumulated above it.

**Which gun and attach markers come along.** Exporting the whole file takes every
marker in it, since every submodel in the file is in the model anyway. With
**Selected Only**, a marker is included when it is *either* selected itself *or*
parented to one of the exported objects — so selecting a hull brings the guns
bolted to it without dragging in a second ship's markers from elsewhere in the
same `.blend`. Markers are written in the order their names number them, not the
order Blender stores them in, because a bank's index is what the game references.
A marker whose parent is not being exported is attached to a root submodel and
measured from *that* submodel's origin, which is where the engine measures from.
Marker positions come from where the object actually sits in the viewport, not
from its `location` field, which reads differently for anything parented with
Ctrl+P. **Marker directions come from the empty's rotation** — where its arrow
points is where the gun fires. A brand-new, unrotated empty therefore exports
pointing straight up; turn it to aim it. (Every marker used to be written facing
one fixed direction whatever you did, which is why a round trip once left every
gun on a ship aimed the same way.)

### Materials and texture names

Material names are written to the model's texture table, so name your
materials after the Descent 3 textures you want the faces to reference.

A material the add-on imported also carries the texture ID it came from in a
**`d3_texture` custom property**, and that wins over the name. It is what lets a
model referencing both `metal` and `metal.001` — two real, distinct game bitmaps
— survive a round trip: without it, export could not tell that pair apart from
one material you duplicated in the outliner, and merged them.

You can set `d3_texture` by hand (Material Properties → Custom Properties) to pin
the export name of a material you authored yourself. A material with no such
property still falls back to the old rule: a `.NNN` suffix is treated as a
Blender duplicate and collapsed onto the original, but only when that original is
actually in the scene, and you are warned when it happens.

Materials are read from each object's **material slots**, so a slot switched from
*Data* to *Object* linking exports the material the viewport shows rather than
nothing at all.

A texture ID is also the name of the file that holds it, so an ID that cannot be
a filename — one containing a path separator, `..`, a drive letter, or a reserved
Windows device name — is reported when the model is built, whether or not you
asked for texture images. The ID is still written to the model unchanged, because
rewriting it would point those faces at a different texture; what the warning
tells you is that no image can ever be written for it or found for it on import.
Rename the material, or give it a `d3_texture` property that is a plain name.

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

**Existing texture files are overwritten without asking.** Blender guards the
model file itself — the export button turns red and reads *Overwrite* when the
`.pof` already exists, as in the screenshot above — but that guard does not
extend to the images. If you hand-edit an exported texture outside Blender,
re-exporting replaces it.

PNG and Targa are supported. Descent 3's native **OGF is not** — Blender cannot
encode it, and offering the option would produce files the game rejects; it
lands when an encoder does.

The image written for a material is the one **driving its Base Color**, followed
back through whatever sits in between — a reroute, a Gamma, a Mix that fades a
detail texture over a diffuse one — so the texture nearest the shader wins rather
than whichever image node happens to come first in the tree.

Some things happen quietly in your favour here:

- An image whose source file is **already in the requested format is copied
  byte-for-byte** — unless you have unsaved edits, in which case it is
  re-encoded so Texture Paint work is not silently left behind. No re-encode
  otherwise, no quality loss, no colour management.
- Anything that *is* converted has colour management neutralised first. Blender
  5.2 defaults its view transform to **AgX**, and the only API that genuinely
  converts formats renders through it — so textures would otherwise export
  tone-mapped and washed out.
- The scene's **Output Properties** are neutralised alongside it. That panel's
  colour mode and bit depth apply to the same encoder, so a scene set to RGB
  would drop the alpha from every texture — a grate exporting solid, a canopy
  exporting opaque, and nothing wrong with the file until the game reads it.
  Textures are always written RGBA at 8 bits.

Your scene's settings are restored afterwards in every case.

A texture whose image cannot be written is **reported rather than skipped
silently**, so a half-populated folder is never a surprise. Three things get
refused, each naming the texture:

- a material with no image texture — there is nothing to write;
- a texture ID that is not a usable filename — one containing a path separator
  or `..`, an absolute path or drive letter, a Windows device name (`NUL`,
  `COM1`, …), or a trailing dot or space. These are refused *before* the path is
  built, because `os.path.join` discards the export folder entirely when handed
  an absolute second argument, and export overwrites without asking;
- an image whose source file has been moved or deleted since it was loaded.

The `exported_textures/` folder is only created once a texture is actually
written, so an export where everything was refused does not leave an empty
folder behind implying otherwise. Change the folder name with
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
texture name up in them. In priority order, most specific first:

1. **Texture Folder** — the path you entered in the import options, if you
   entered one *and it exists*.
2. **`textures.search_dirs`** — every directory the project's `descent3.toml`
   lists, in the order it lists them. Relative entries resolve against the config
   file, not your working directory.
3. **The model's own directory** — the folder holding the `.oof`/`.pof` you are
   importing (`os.path.dirname(os.path.abspath(filepath))`). Always searched.
4. **Texture Library** — the folder from your add-on preferences, if you set one
   *and it exists*. Deliberately last, so a personal library never shadows a
   texture that ships beside the model.

The first directory that yields a match wins. Only entry 3 is unconditional —
1, 2 and 4 drop out when you have not set them, and any of them that names a
folder which does not exist is skipped with a warning on the console rather than
failing the import. The list is also de-duplicated, so listing the model's own
folder in `search_dirs` does not get it searched twice.

The folder texture *export* writes into is **not** on this list. See
[Exporting textures](#exporting-textures).

### How each directory is searched

The same rules apply to every entry above, not just the model's own folder:

- Each texture **name** is treated as a **file stem** in the directory. The
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

- The search is **not recursive** — only each directory itself is looked at,
  never its subfolders or parents.

- The comparison is **exact on the stem** (apart from case). A texture named
  `Hull` matches `Hull.png` but not `Hull_01.png`, `metal_Hull.png`, or
  `Hull.001.png`.

### When nothing is configured (the default)

With the **Texture Folder** field empty, no `descent3.toml`, and no Texture
Library set, the list collapses to its one unconditional entry: **the model's
own directory**, derived from the file you picked in the import dialog and
resolved to an absolute path. Import `D:\descent3\models\pyro.oof` and the
texture directory is `D:\descent3\models\`.

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
- Searched **first**, ahead of everything else, using the same per-directory
  rules described above.

If the folder you enter does not exist, it is ignored (with a warning in the
console) and the importer carries on with the rest of the list.

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
    for each directory in [Texture Folder (if set & exists),
                           textures.search_dirs from descent3.toml,
                           model directory,
                           Texture Library from preferences (if set & exists)]:
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
[descent3_plugin] Texture not found: Hull (searched [...])
[descent3_plugin] Material 'Hull.001' looks like a Blender duplicate ...
```

Every module logs under the one `descent3_plugin` name, so that prefix is what
to grep the console for — there is no per-module tag to guess at.

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

The other four — `import_pof.py`, `export_pof.py`, `texexport.py` and
`preferences.py` — import `bpy` at module scope and are exercised through real
Blender instead; see [Tests that need Blender itself](#tests-that-need-blender-itself).
Anything that can be expressed without `bpy` belongs on the first list, because
that is the half a plain `pytest` run can cover.

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
518 passed in 1.58s
==> all tests passed
```

Arguments go straight to pytest, including bare paths:

```bash
./run_tests.sh -k naming -v
./run_tests.sh tests/test_poformat.py
```

```powershell
.\run_tests.ps1 -k naming --verbose
.\run_tests.ps1 tests\test_poformat.py
```

PowerShell binds `-v` to its own `-Verbose` before the script sees it, so pytest
never receives that one — use `--verbose` or `-vv`. Everything else passes
through.

Other options:

| Option | Effect |
|--------|--------|
| `--blender <path>` / `-Blender <path>` | use a specific Blender. Given explicitly, it is never silently substituted — a bad path is an error |
| `--system` / `-System` | use your system Python instead |
| `BLENDER=<path>` / `$env:BLENDER` | same as `--blender` |
| `--help` / `-?` | usage |

**How Blender is found.** No path is baked into the repo — the search is entirely
derived from your environment, so it works the same on a machine that installed
Blender somewhere the scripts have never heard of:

1. `--blender` / `-Blender`, or `$BLENDER` / `$env:BLENDER`;
2. `blender` on `PATH`;
3. the roots Blender's own installer writes to, newest version first —
   `$ProgramFiles` and `$LOCALAPPDATA` on Windows, `/Applications` on macOS,
   `/usr/bin` on Linux;
4. every Steam library, read from Steam's own record of them — its install path
   in the registry (Windows) or under `$HOME` (Linux/macOS), plus every extra
   library listed in `libraryfolders.vdf`;
5. failing all of that, whichever Blender the last successful run used, which is
   cached in `.pytest-blender/.interpreter`.

Step 4 covers a Steam library on any drive without any drive being named — the
scripts ask Steam rather than sweeping `C:` through `F:`, which is what they used
to do and which found nothing for anyone whose library was on `G:`. Step 5 is
then only for a portable build unpacked somewhere of your own, after you have
named it with `--blender` once. If nothing is found, the error lists every way to
point it at one; `./install.sh --list` enumerates every Blender on the machine.

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
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_bad_geometry.py
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

`test_coordinate_system.py` guards everything that crosses the Y-up/Z-up
boundary, including the parts that live on the object rather than in the mesh:
world transforms, marker positions and directions, and the offsets that
accumulate down a three-deep hierarchy.

`test_bad_geometry.py` guards the damaged-file paths, and is the one test here
whose regression is not a `FAIL` line — a face carrying an out-of-range index or
a repeated vertex kills Blender with an access violation when the model's normals
are applied, so a regression shows up as the process dying mid-output.

These deliberately do *not* run under pytest — `tests/blender/conftest.py` sets
`collect_ignore_glob`, so a bare `pytest` does not try to import them without
Blender.

There is also `tests/run_in_blender.py`, which runs the pytest suite *inside*
Blender rather than beside it. `run_tests.sh` is faster and is what you want
day to day; this one matters only if you add tests that themselves need `bpy`:

```bash
blender --background --factory-startup --python-exit-code 1 \
        --python tests/run_in_blender.py
```

The flag belongs here too. This script does exit non-zero when a test fails —
it calls `sys.exit()` with pytest's own code, and Blender honours an explicit
exit code either way — but it cannot report a failure it never reached. Without
the flag, a collection error or an import that blows up before `main()` runs
exits 0 and reads as a clean suite.

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
- **Custom properties carry what Blender cannot represent.** Movement type and
  axis, and the raw `$rotate=` / `$fov=` property string, are stored on the
  object so a round trip does not lose commands the add-on does not itself
  interpret. The `SOF_*` flag bits are stored too (`pof_flags`) but are not
  written back: the SOBJ record has no flag word, and both the engine and this
  add-on's reader derive every bit from the property string — so preserving that
  string verbatim is what carries the flags across.
  A material carries `d3_texture`, the Descent 3 texture ID it stands for. The
  material's *name* says the same thing until Blender renames the datablock or
  the user does, and a name is a guess where the property is evidence — which is
  what stops `metal` and `metal.001` being merged into one texture on export.
  Materials the add-on *created* carry a second mark, `d3_addon_material`, and
  that is the one that grants import permission to restyle them. Two properties
  rather than one because they answer different questions: import records
  `d3_texture` on a user's material too, so its faces export correctly, and if
  that also meant "mine to edit" then the next import would edit it.
  An attach-point empty carries `pof_has_uvec`, meaning the file fixed its roll
  rather than leaving it undefined; without it, export would write an up vector
  for every attach point and invent a constraint the model never had.
  An object carries `pof_name`, the name its submodel had in the file, for the
  same reason a material carries `d3_texture`: a POF may hold two submodels
  called `Wing` and Blender may not, so the second object becomes `Wing.001` and
  that suffix must not reach the model. Rename the object and the rename wins —
  the recorded name is only preferred while the object still wears it.
- **A submodel with no geometry becomes an Empty, and exports as one.** That is
  how Descent 3 spells a joint: a turret's pivot has no vertices, it carries the
  `$rotate=` the engine turns its children with. Exporting only meshes dropped it
  and re-rooted the turret onto the hull, which looks identical in Blender and
  stops the turret moving in the game. An Empty joins the model by carrying
  `pof_index`, which is also how you can promote one you made by hand; every
  other empty in your file is left alone.
- **Export reads evaluated objects, not raw mesh data.** Object transforms and
  modifiers are applied on the way out, because the alternative silently ships
  something other than what the viewport shows. Material slots are read off the
  evaluated object too, so a slot a Boolean or a Geometry Nodes tree introduced
  is a texture the model uses rather than faces exported flat grey. Submodel
  offsets are world-space deltas from the parent, which is what the engine's own
  hierarchy walk expects: it sums them as plain vector adds without applying
  parent rotation. An object whose transform mirrors — a wing duplicated and
  scaled `-1` — has each face's corners written in reverse, because a mirror
  reverses orientation and the winding would otherwise contradict the normals
  stored beside it.
- **Round-trip fidelity is the correctness bar.** Import then export should
  produce an equivalent file. This is enforced by
  `tests/test_version_features.py`, which round-trips a model at every version
  where the record layout changes.
- **Gun and attach points are empties matched by name prefix.** That makes the
  prefix a contract between import and export, which is why it is configurable
  in one place both halves read (see Settings). The number after the prefix is a
  contract too, and export sorts on it: a gun bank's index is what WBAT cites and
  what the game bolts hardware to, while `bpy.data.objects` is sorted as text and
  would put `Gun_10` between `Gun_1` and `Gun_2`.

## Repository layout

```
descent3_plugin/      the add-on (install this folder)
  __init__.py           registration and menu entries (no bpy at import time)
  import_pof.py         ImportPOF operator and POF -> Blender conversion
  export_pof.py         ExportPOF operator and Blender -> POF conversion
  texexport.py          writing texture images beside an exported model
  config.py             per-project descent3.toml settings (no bpy)
  preferences.py        per-user add-on preferences
  constants.py          values shared by the import and export halves (no bpy)
  mathutil.py           Vector3 and math helpers (no bpy)
  naming.py             material-name to texture-ID rules (no bpy)
  poformat.py           POF/OOF binary parser & writer (no bpy)
  texutil.py            texture-path resolution helpers (no bpy)
  blender_manifest.toml canonical version + extension metadata
descent3.example.toml   documented template for a project config
install.ps1 install.sh  find every Blender and deploy the package folder
run_tests.sh run_tests.ps1   run pytest under Blender's own Python
docs/images/            screenshots used by this README
LICENSE                 GPL-3.0-or-later
tests/                  pytest suite, mock data, and fixtures
reference/              vendored Descent 3 engine sources (GPL-3, not installed)
.claude/skills/         reference notes for the headless test workflow and the
                        binary format
```
