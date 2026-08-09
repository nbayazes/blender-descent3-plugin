# How textures are located

[<- README](../README.md)

How the importer turns a texture *name* stored in a model into an image file on
disk: which directories it searches, in what order, and what happens when a
texture is not found.

Descent 3 models do **not** store image files. The model's `TXTR` chunk holds a
list of **texture names** (e.g. `WarningStripe`, `ConcussionMissile`), and each
face references one by index. The importer has to resolve each name to an actual
image file on disk.

## Search directories and their priority

The importer builds an ordered list of directories, most specific first, and
looks each texture name up in them:

1. **Texture Folder** — the path from the import options, if set *and it exists*.
2. **`textures.search_dirs`** — every directory the project's `descent3.toml`
   lists, in the order it lists them. Relative entries resolve against the config
   file, not your working directory.
3. **The model's own directory** — the folder holding the `.oof`/`.pof` being
   imported (`os.path.dirname(os.path.abspath(filepath))`). Always searched.
4. **Texture Library** — the folder from add-on preferences, if set *and it
   exists*. Deliberately last, so a personal library never shadows a texture that
   ships beside the model.

The first directory that yields a match wins. Only entry 3 is unconditional —
1, 2 and 4 drop out when unset, and any naming a folder that does not exist is
skipped with a console warning rather than failing the import. The list is
de-duplicated, so listing the model's own folder in `search_dirs` does not
search it twice.

The folder texture *export* writes into is **not** on this list. See
[Exporting textures](exporting.md#exporting-textures).

## How each directory is searched

The same rules apply to every entry above, not just the model's own folder:

- Each texture **name** is treated as a **file stem**. The importer appends each
  supported extension, in this order, and uses the first file that exists:

  ```
  .png  .bmp  .tga  .jpg  .jpeg  .ogf  .pcx
  ```

  So `ConcussionMissile` tries `ConcussionMissile.png`, then
  `ConcussionMissile.bmp`, and so on.

- **`.ogf` and `.pcx` are matched but cannot be opened.** Blender decodes
  neither, and `.ogf` is exactly the extension Descent 3 itself appends to a
  `TXTR` name, so it is the one a folder of untouched game assets will be full
  of. The match still counts: the importer accepts the file, builds the material
  with an image node whose image is 0×0 and holds no pixel data, logs Blender's
  `unknown file-format` error to the console, and counts the texture **found**.
  A folder of `.ogf` textures therefore imports as `textures N/N found`, with
  nothing reported missing and no texture on the model. Convert them to `.png`
  before importing, or drop the two extensions from `textures.extensions` in
  `descent3.toml` to have them reported missing instead.

- Failing an exact filename match, a **case-insensitive** scan of the directory
  listing accepts any file named `<texturename>.<ext>` (ignoring case) for a
  supported `<ext>` — the model stores `blackpanel`, the file is
  `BlackPanel.png`. On Windows the filesystem is already case-insensitive, so the
  exact-name step usually resolves it; the fallback matters on case-sensitive
  filesystems.

- The search is **not recursive** — only each directory itself, never its
  subfolders or parents.

- The comparison is **exact on the stem** (apart from case). `Hull` matches
  `Hull.png` but not `Hull_01.png`, `metal_Hull.png`, or `Hull.001.png`.

## When nothing is configured (the default)

With the **Texture Folder** field empty, no `descent3.toml`, and no Texture
Library set, the list collapses to its one unconditional entry: **the model's
own directory**, taken from the file picked in the import dialog and resolved to
an absolute path. Import `D:\descent3\models\pyro.oof` and the texture directory
is `D:\descent3\models\`.

**Practical consequence:** keep a model's `.oof` and all of its `.png` (or other
supported) textures in one folder, import the `.oof` from there with the Texture
Folder left blank, and every texture resolves automatically.

## When a Texture Folder *is* provided

Use this when models and textures live in different folders — common with
Descent 3 assets extracted from `.hog` archives. The value you enter is:

- Cleaned of surrounding whitespace and quotes (so a Windows
  *"Copy as path"* value like `"D:\tex\folder"` works when pasted).
- Resolved with Blender's path rules (`//`-relative paths are made absolute).
- Searched **first**, ahead of everything else, using the same per-directory
  rules.

A folder that does not exist is ignored (with a console warning) and the importer
carries on with the rest of the list.

> The **Texture Folder** field is a plain text box you paste a path into — it has
> no "browse" button. Blender only allows one file-selector dialog at a time, and
> the import file browser already occupies it, so a folder-picker cannot be
> opened from inside the import dialog.

## When a texture cannot be found

If no search directory contains a matching image, the importer still creates a
material named after the texture, with no image attached (a plain Principled
BSDF). Missing textures are reported:

- The import finishes with a status message such as
  `Imported pyro: 3 submodels, 5/7 textures — missing textures: X, Y`.
- The system console (`Window → Toggle System Console`) shows a
  `[Descent3] … textures N/M found (missing: …)` summary and the exact
  directories searched.

## Resolution at a glance

```
for each texture name referenced by the model's faces:
    for each directory in [Texture Folder (if set & exists),
                           textures.search_dirs from descent3.toml,
                           model directory,
                           Texture Library from preferences (if set & exists)]:
        try name + ".png", ".bmp", ".tga", ".jpg", ".jpeg", ".ogf", ".pcx"   → first hit wins
                                          (.ogf and .pcx match, but Blender
                                           cannot decode either — see below)
        else scan directory for a case-insensitive  name.<supported-ext>       → first hit wins
    if nothing matched anywhere:
        create a blank material named after the texture and report it missing
```

A `.ogf` or `.pcx` hit never reaches that last branch. It matched, so it is
counted as found and reported as such; the material it produces just carries an
empty image.

Supported extensions and their priority order default to `IMAGE_EXTENSIONS`
in `descent3_plugin/texutil.py`, and a project can override them (see
[Configuration](configuration.md)).
