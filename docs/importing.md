# Importing

[<- README](../README.md)

How imported textures become materials, what happens to a material you already
had, how damaged files are handled, and how markers arrive aimed.

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

Damaged files are imported as far as they go rather than refused. A face pointing
at a vertex the submodel does not have is dropped, as is anything Blender's own
mesh validation rejects; both are reported with the submodel named, so missing
geometry is never silent. An out-of-range index, or a face using one vertex
twice, is written into the mesh without complaint and then kills Blender
outright — an access violation, not a catchable error — the moment the model's
normals are applied, taking every unsaved edit in the session with it.

Gun and attach markers arrive as empties **aimed the way the file aims them**.
The direction lives in the empty's rotation — the axis Blender draws a
single-arrow empty's arrow along — so the arrow points where the gun fires, and
rotating the empty changes it. Attach points that specify an up vector keep their
roll too.
