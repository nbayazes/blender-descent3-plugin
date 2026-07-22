# Descent 3 POF/OOF Importer/Exporter for Blender

A Blender add-on for importing and exporting Descent 3 polygon model files
(`.pof` / `.oof`). It supports vertices, faces, UV mapping, materials with
image textures, the submodel hierarchy, gun points, attach points, and animation
keyframes.

- **Blender:** 4.2+ (developed and tested against 5.2 LTS)
- **Location in Blender:** `File → Import/Export → Descent 3 POF/OOF (.pof, .oof)`

## Installation

> **Important:** the add-on must be installed as a **package folder**, not as
> loose files. It uses a relative import (`from . import poformat`), so the
> three modules have to sit together inside a `descent3_importer/` directory:
>
> ```
> scripts/addons/descent3_importer/
>   ├── __init__.py
>   ├── poformat.py
>   └── texutil.py
> ```
>
> If the `.py` files are dropped directly into `scripts/addons/`, Blender cannot
> load the package — the import/export operators never register and Blender warns
> that `poformat.py` is "missing `bl_info`".

**To install:**

1. Zip the `descent3_importer/` folder (so the zip contains the folder, not just
   its files).
2. In Blender: `Edit → Preferences → Add-ons → Install from Disk…`, choose the
   zip, then enable **"Import-Export: Descent 3 POF/OOF Importer/Exporter"**.

Alternatively, copy the `descent3_importer/` folder straight into your Blender
`scripts/addons/` directory and enable it in Preferences. After updating the
files, **restart Blender** (or toggle the add-on off/on) so the new code loads.

## Importing

`File → Import → Descent 3 POF/OOF (.pof)`. Options in the file browser sidebar:

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

`File → Export → Descent 3 POF/OOF (.pof)`. Choose the POF version (v23.00 is
Descent 3 retail), and whether to export only selected objects, gun points, and
attach points. Material names are written to the model's texture table, so name
your materials after the Descent 3 textures you want the faces to reference.

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

Supported extensions and their priority order are defined by
`IMAGE_EXTENSIONS` in `descent3_importer/texutil.py`.

## Development

The parsing/writing (`poformat.py`) and texture-path logic (`texutil.py`) have
**no `bpy` dependency**, so they can be unit-tested with plain Python:

```bash
python -m pytest tests/ -q
```

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

## Repository layout

```
descent3_importer/      the add-on (install this folder)
  __init__.py           operators, import/export, Blender integration
  poformat.py           POF/OOF binary parser & writer (no bpy)
  texutil.py            texture-path resolution helpers (no bpy)
tests/                  pytest suite, mock data, and fixtures
polymodel.{cpp,h}       Descent 3 source reference for the format
```
