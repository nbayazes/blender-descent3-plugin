# Custom properties

[<- README](../README.md)

Every custom property the add-on writes: which datablock carries it, what it
means, and whether export reads it back.

A POF file states things Blender has no native place for — a submodel's index in
the file, the raw `$rotate=` string the engine parses, the texture ID a material
stands for. Import stores each of them as a custom property on the datablock it
describes: the object for submodel and marker fields, the material for texture
fields. You see them in the Properties editor, under **Object Properties →
Custom Properties** and **Material Properties → Custom Properties**.

The key names are a contract with your saved `.blend`. Renaming one silently
drops that field from every model already imported, and which key means what
decides whether a round trip survives.

## The keys at a glance

| Key | Datablock | On export | Meaning |
| --- | --- | --- | --- |
| `pof_index` | Object | Presence only | The submodel's own index within the file |
| `pof_parent` | Object | Not read | Parent submodel's index, or `-1` for a root submodel |
| `pof_flags` | Object | Not read | The `SOF_*` flag word derived from the property string |
| `pof_movement_type` | Object | Read | How the submodel moves (`bsp_info.movement_type`) |
| `pof_movement_axis` | Object | Read | Which axis it moves or rotates about (`bsp_info.movement_axis`) |
| `pof_properties` | Object | Read | The raw SOBJ property string — the `$rotate=` / `$fov=` text |
| `pof_name` | Object | Read, conditionally | The name the submodel had in the file |
| `pof_has_uvec` | Object (attach empty) | Read | The file supplied an explicit up vector for this attach point |
| `d3_texture` | Material | Read | The Descent 3 texture ID the material stands for |
| `d3_addon_material` | Material | Not read | Marks a material this add-on created from nothing |

A key export never writes back is not dead: it records what the file said, and
is how the exporter recognises an object as ours.

## `pof_index` — the submodel's own index

`Submodel.index`, written on every object import builds from a submodel.

**Export reassigns indices from the export order rather than writing this back.**
The exported set need not be the imported set, so a stored index collides or
leaves gaps as soon as a subset is exported.

Export reads the key in one place only, and only its *presence*, never its
value: a submodel with no geometry imports as an **Empty**, and this property is
what keeps that Empty in the export instead of dropping it. That matters because
a vertex-less submodel is how Descent 3 spells a joint — a turret's pivot has no
vertices, it carries the `$rotate=` the engine turns its children with. Export
only the meshes and the turret re-roots onto the hull: identical in Blender, and
the turret stops moving in the game.

Presence being the whole test is also how you **promote an Empty you built by
hand** into a submodel — give it this property and it joins the model. Nothing
looser would do: "an empty that is not a marker" would sweep up every light rig
and organisational empty in your file. Meshes need no property; every mesh is a
submodel. See [Exporting](exporting.md#what-gets-exported).

## `pof_parent` — the parent submodel's index

The parent submodel's index, or `-1` (`NO_PARENT`) for a root submodel.

**Reference only.** Export derives the hierarchy from Blender's own object
parenting, so reading this back would override your outliner edits. Reparent an
object in the outliner and the exported model follows; the recorded value goes
stale and is ignored.

## `pof_flags` — the `SOF_*` bits

The `SOF_*` flag word (`SOF_ROTATE`, `SOF_TURRET`, `SOF_GLOW`, and the rest) the
add-on's reader derived for the submodel.

**Reference only, never written back:** the SOBJ record has no flag word to
write it to, and the writer never serialises the field. The engine re-derives
every bit from the submodel's property string (`reference/polymodel.cpp`,
`SetPolymodelProperties`), as does this add-on's reader, so preserving that
string verbatim is what carries the flags across a round trip.

Editing this property therefore changes nothing in the exported file. The flag
lives in `pof_properties`, so that is the one to edit.

## `pof_movement_type` and `pof_movement_axis` — how the submodel moves

`bsp_info.movement_type` and `bsp_info.movement_axis`, straight from the SOBJ
record. Both are read back on export and written out as they stand.

An object carrying neither — one you modelled rather than imported — exports as
movement type `-1` (`MOVEMENT_TYPE_NONE`, "does not move") on axis `0`.

## `pof_properties` — the raw property string

The SOBJ property string: the `$rotate=` / `$fov=` text the engine parses. Kept
verbatim so a round trip does not lose commands this add-on does not itself
interpret, and read back on export unchanged. Import writes it only when the
file's string was non-empty; an object without it exports an empty string.

This string is also what carries the `SOF_*` flags — see
[`pof_flags`](#pof_flags--the-sof_-bits) above.

## `pof_name` — the name the submodel had in the file

Recorded on the object built from the submodel, and only when the file actually
named it. A submodel with an empty name gets a generated fallback name
(`Submodel_<index>` unless the project configures otherwise, see
[Configuration](configuration.md)) and no property, since recording the fallback
would hand the exporter a placeholder as if it were the file's own.

The problem it solves is Blender's uniquifying suffix. A file may legally hold
two submodels called `Wing` — the engine treats the field as a label, never
looks a submodel up by it — and Blender may not, so the second object becomes
`Wing.001`, and export wrote Blender's bookkeeping into the model. The `.NNN`
heuristic alone cannot settle it either, because a file may equally state `Wing`
and `Wing.001` itself.

**The recorded name is preferred only while the object still wears it.** Export
uses it when the object's current name is that name, with or without Blender's
suffix. Any other rename is you talking, and wins. An object nobody imported
carries no record and exports under its own name.

## `pof_has_uvec` — whether the file fixed an attach point's roll

Written on an **attach-point empty** to record that the file supplied an
explicit up vector for it, in a NATH chunk. Gun banks have no up vector, so they
never carry it.

The direction and the roll both live in the empty's own rotation, so the vectors
themselves need no property. What needs recording is whether anybody *chose* the
roll: an attach point with no NATH entry leaves the attached model's roll
undefined — a legal state rather than missing data — while every Blender empty
has a roll whether or not anybody chose one.

**Export writes a NATH entry only for attach points carrying this key.** Writing
one for all of them invents an up vector the model never specified; writing one
for none loses the one the model did state. Add the property by hand to an
attach empty you want the roll written for; the vector itself comes from the
empty's rotation, so there is nothing else to fill in.

## `d3_texture` — the texture a material stands for

On a **Material**, the Descent 3 texture ID it represents. Import writes it,
export reads it.

The material's *name* says the same thing right up until Blender renames the
datablock or you do — a name is a guess where the property is evidence. The
guess is not good enough: `metal` and `metal.001` can be two distinct bitmaps
shipped by the game, indistinguishable by name from a material you duplicated by
hand, and export merged them and lost the second texture.

**A material carrying this key exports as the ID it carries**, whatever Blender
has since done to its name. Only materials with no such record fall back to the
`.NNN` heuristic in `naming.py` — see
[Exporting](exporting.md#materials-and-texture-names). Import records the key on
*adopted* materials too, meaning one of yours that an import reused rather than
created, so those faces still export as the right texture. A library-linked
material is the exception: Blender refuses to write to it, so it never gains the
key however it was matched.

**You may set it by hand** (Material Properties → Custom Properties) to pin the
export name of a material you authored; the property is as meaningful set that
way as recorded by import. It is spelled `d3_` rather than `pof_` because it
names a game asset rather than a POF record field.

A pin is never clobbered. If a later import adopts a material whose recorded ID
differs from the texture the model asked for, the recorded ID still wins and
those faces export as it — the importer reports this, and clearing the property
is what sends them back to the model's own name.

## `d3_addon_material` — the add-on's own mark

Marks a **Material** this add-on created from nothing. Import writes it and
reads it; export ignores it entirely.

It is what grants a later import permission to *restyle* that material — enable
nodes and wire an image into its Base Colour. Reuse is not permission to
restyle: with the two conflated, importing a model with a `Metal` texture edited
the user's own `Metal` material.

Two keys rather than one because they answer different questions. `d3_texture`
is stamped on adopted materials as well, and may be set by hand to pin an export
name, so using it as the authorship signal let the second import restyle a
material the user authored. See [Importing](importing.md) for what a restyle
does.

**Do not set this one by hand.** It is the add-on's own mark, and putting it on
a material you authored tells the next import that material is the add-on's to
edit.
