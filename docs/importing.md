# Importing

[<- README](../README.md)

How imported textures become materials, what happens to a material you already
had, how damaged files are handled, how markers arrive aimed, and what the file
carries that does not reach the scene.

Imported textures show up as materials with an image texture wired into the
Principled BSDF's Base Color. If a model looks flat/untextured in the viewport,
switch viewport shading to **Material Preview** — *Solid* mode does not display
image textures.

**A material you already had is reused, never restyled.** One material per
texture ID keeps a round trip honest: if your scene already contains a material
named after one of the model's textures, the import assigns that one rather than
minting `Metal.001`. A material this add-on did not create is used exactly as it
arrives — nothing added to its node tree, nothing rewired — and the import
reports it, because the result is a model that shows up untextured. Delete or
rename that material and re-import to get the model's own texture.

Materials the add-on created carry a `d3_addon_material` property, and *those* it
does finish off with their image. That is deliberately a separate mark from
`d3_texture` (see [Custom properties](custom-properties.md)): adoption records
`d3_texture` on your material so its faces still export as the right texture,
and if that doubled as the add-on's signature a second import of the same model
would treat your material as its own and restyle it.

**Damage inside a chunk the parser reads costs the whole file, not just the
chunk.** A file cut mid-record — how a failed download or a half-extracted hog
usually breaks — stops the parser at the short read: the import reports
`Failed to parse POF`, cancels, and nothing is created at all, not even the
submodels that had already parsed. The same goes for a bad magic, a version
outside the supported range, a submodel index out of range, and any element
count claiming more data than the file holds. Truncation is survived where it
lands on a chunk boundary, inside a chunk this add-on does not read, or past the
last field a chunk's own parser needs: reading stops there, a console warning
says so, and everything before it is imported.

**Damage inside a face costs only that face.** One pointing at a vertex the
submodel does not have is dropped, as is anything Blender's own mesh validation
rejects — a face naming the same vertex twice, or a second copy of another face.
The rest of the submodel is imported either way, and both are reported with the
submodel named, so missing geometry is never silent. A face with fewer than
three corners cannot become a polygon and is dropped without a word.

**Some of what the file carries is parsed but never reaches the scene.**
Animation keyframes, weapon batteries, ground planes, per-vertex alpha and the
flat colour of an untextured face are read out of the file and then dropped: no
object, material or custom property holds them, nothing in the viewport shows
them, and nothing is reported. Less visibly, so are each submodel's geometric
centre and separation plane, and the file's own per-face normals. Since none of
it is in the scene, export cannot put it back — the chunk is left out, or the
default is written: zero vectors, fully opaque vertices, mid-grey for a face
with no texture, a face normal recomputed from the winding. A round trip through
Blender therefore loses all of it and the file you imported stays the only copy.
[Design notes](design-notes.md) says why the parser reads it anyway.

Gun and attach markers arrive as empties **aimed the way the file aims them**.
The direction lives in the empty's rotation — the axis Blender draws a
single-arrow empty's arrow along — so the arrow points where the gun fires, and
rotating the empty changes it. Attach points that specify an up vector keep their
roll too.
