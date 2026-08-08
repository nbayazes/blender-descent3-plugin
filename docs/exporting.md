# Exporting

[<- README](../README.md)

How Blender objects become a POF model, how material names become texture IDs,
and what the Textures option writes.

## What gets exported

Each mesh object becomes a submodel, and Blender's object parenting becomes the
model hierarchy. So does an *Empty* imported from a submodel with no geometry — a
turret's pivot, which carries `$rotate=` and is what the engine turns its children
about. Import records `pof_index` on those; empties without it are not swept in.

**Objects are exported as you see them.** The exporter reads each object's
evaluated mesh and world matrix, so rotation, scale and modifiers are applied — no
need to apply transforms by hand, and a mirrored or subdivided mesh exports
subdivided. Normals go through the same transform, so a scaled object's lighting
stays right.

**Authored normals are kept.** Descent 3 stores one normal per vertex; import puts
the file's normals in Blender's custom split normals, and export reads them back
from there rather than from Blender's computed vertex normals — so a round trip
returns the normals the model shipped with. A mesh with no custom normals falls
back to the computed ones, which is right for flat shading anyway.

A submodel's `offset` is its world position relative to its parent's: the engine
adds offsets down the hierarchy without applying parent rotation (`MinMaxSubmodel`
in `reference/polymodel.cpp`), so it expects world-space deltas. The model's
bounding box and radius are grown the same way, from every vertex plus the offsets
accumulated above it.

**Which gun and attach markers come along.** Exporting the whole file takes every
marker in it — every submodel is in the model anyway. With **Selected Only**, a
marker is included when it is *either* selected itself *or* parented to an exported
object, so selecting a hull brings the guns bolted to it without dragging in a
second ship's markers from the same `.blend`. Markers are written in the order
their names number them, not the order Blender stores them in, because a bank's
index is what the game references. A marker whose parent is not exported is
attached to a root submodel and measured from *that* submodel's origin, which is
where the engine measures from. Marker positions come from where the object
actually sits in the viewport, not from its `location` field, which reads
differently for anything parented with Ctrl+P. **Marker directions come from the
empty's rotation** — the arrow points where the gun fires. A new, unrotated empty
exports pointing straight up; turn it to aim it.

## Materials and texture names

Material names are written to the model's texture table, so name your materials
after the Descent 3 textures you want the faces to reference.

A material the add-on imported also carries its source texture ID in a
**`d3_texture` custom property**, which wins over the name. That is what lets a
model referencing both `metal` and `metal.001` — two real, distinct game bitmaps —
survive a round trip; without it, export cannot tell that pair from one material
you duplicated in the outliner, and merges them.

Set `d3_texture` by hand (Material Properties → Custom Properties) to pin the
export name of a material you authored yourself — see
[Custom properties](custom-properties.md#d3_texture--the-texture-a-material-stands-for).
Without it, the old rule applies: a `.NNN` suffix is treated as a Blender
duplicate and collapsed onto the original, but only when that original is
actually in the scene, and you are warned when it happens.

Materials are read from each object's **material slots**, so a slot switched from
*Data* to *Object* linking exports the material the viewport shows rather than
nothing at all.

A texture ID is also the name of the file that holds it, so an ID that cannot be a
filename — one containing a path separator, `..`, a drive letter, or a reserved
Windows device name — is reported when the model is built, whether or not you asked
for texture images. The ID is still written to the model unchanged, since rewriting
it would point those faces at a different texture; the warning means no image can
ever be written for it or found for it on import. Rename the material, or give it a
`d3_texture` property that is a plain name.

## Exporting textures

The **Textures** control writes each material's image alongside the model, so a
model and the images it needs travel together. Files land in an
`exported_textures/` subfolder next to the `.pof`, named after the texture IDs in
the model — which is what the importer searches for.

```
export/
  model.pof
  exported_textures/
    Hull.png
    Turret.png
```

**Existing texture files are overwritten without asking.** Blender guards the model
file itself — the export button turns red and reads *Overwrite* when the `.pof`
already exists — but not the images. Hand-edit an exported texture outside Blender
and re-exporting replaces it.

PNG and Targa are supported. Descent 3's native **OGF is not** — Blender cannot
encode it, and the option would produce files the game rejects; it lands when an
encoder does.

The image written for a material is the one **driving its Base Color**, followed
back through whatever sits in between — a reroute, a Gamma, a Mix fading a detail
texture over a diffuse one — so the texture nearest the shader wins rather than
whichever image node comes first in the tree.

Some things happen quietly in your favour:

- An image whose source file is **already in the requested format is copied
  byte-for-byte** — unless you have unsaved edits, which force a re-encode so
  Texture Paint work is not silently left behind. Otherwise no re-encode, no
  quality loss, no colour management.
- Anything that *is* converted has colour management neutralised first. Blender 5.2
  defaults its view transform to **AgX**, and the only API that genuinely converts
  formats renders through it — textures would otherwise export tone-mapped and
  washed out.
- The scene's **Output Properties** are neutralised alongside it. That panel's
  colour mode and bit depth apply to the same encoder, so a scene set to RGB would
  drop the alpha from every texture — a grate exporting solid, a canopy exporting
  opaque, and nothing wrong with the file until the game reads it. Textures are
  always written RGBA at 8 bits.

Your scene's settings are restored afterwards in every case.

A texture whose image cannot be written is **reported rather than skipped
silently**. Three things get refused, each naming the texture:

- a material with no image texture — nothing to write;
- a texture ID that is not a usable filename — one containing a path separator or
  `..`, an absolute path or drive letter, a Windows device name (`NUL`, `COM1`, …),
  or a trailing dot or space. These are refused *before* the path is built, because
  `os.path.join` discards the export folder entirely when handed an absolute second
  argument, and export overwrites without asking;
- an image whose source file has been moved or deleted since it was loaded.

The `exported_textures/` folder is created only once a texture is actually written,
so an export where everything was refused leaves no empty folder behind. Change the
folder name with `textures.export_dir`, and the default format with
`export.texture_format`.

**Import does not search this folder.** Exports stay sandboxed from the models they
came from — widening the search automatically would make the importer harder to
predict, and generated output is often deliberately kept out of the source
pipeline. To have those images picked up, opt in explicitly: point the import
dialog's **Texture Folder** at it, or add it to your project config:

```toml
[textures]
search_dirs = ["exported_textures"]
```
