# Design notes

[<- README](../README.md)

Decisions that are not obvious from the code, and the reasoning behind them.

- **Pure-Python parser/writer.** No C extension: Blender ships `struct`, and
  keeping `poformat.py` free of `bpy` is what lets the whole format layer be
  unit-tested outside Blender.
- **Coordinate conversion is a rotation, not a mirror.** Descent 3 is
  right-handed **Y-up**; Blender is right-handed **Z-up**. Both being
  right-handed makes the difference a single 90-degree rotation about X, so
  import maps `(x, y, z)` to `(x, -z, y)` and export maps it back to
  `(x, z, -y)`. Being a rotation and not a mirror, winding order is untouched and
  normals transform exactly like positions — no face reversal anywhere.
- **The conversion happens only at the Blender boundary.** Everything inside
  `poformat` stays in file coordinates, so what the parser and writer exchange is
  exactly what is on disk. `mathutil.descent_to_blender` and
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
  interpret.
  - `pof_flags` — the `SOF_*` flag bits. Stored, but not written back: the SOBJ
    record has no flag word, and both the engine and this add-on's reader derive
    every bit from the property string, so preserving that string verbatim is
    what carries the flags across.
  - `d3_texture` — on a material, the Descent 3 texture ID it stands for. The
    material's *name* says the same thing until Blender renames the datablock or
    the user does; a name is a guess where the property is evidence, which is
    what stops `metal` and `metal.001` being merged into one texture on export.
  - `d3_addon_material` — a second mark, carried only by materials the add-on
    *created*, and the one that grants import permission to restyle them. Two
    properties rather than one because they answer different questions: import
    records `d3_texture` on a user's material too, so its faces export
    correctly, and if that also meant "mine to edit" then the next import would
    edit it.
  - `pof_has_uvec` — on an attach-point empty, meaning the file fixed its roll
    rather than leaving it undefined. Without it, export would write an up vector
    for every attach point and invent a constraint the model never had.
  - `pof_name` — on an object, the name its submodel had in the file, for the
    same reason a material carries `d3_texture`: a POF may hold two submodels
    called `Wing` and Blender may not, so the second object becomes `Wing.001`
    and that suffix must not reach the model. Rename the object and the rename
    wins — the recorded name is only preferred while the object still wears it.
- **A submodel with no geometry becomes an Empty, and exports as one.** That is
  how Descent 3 spells a joint: a turret's pivot has no vertices, it carries the
  `$rotate=` the engine turns its children with. Exporting only meshes drops it
  and re-roots the turret onto the hull — identical in Blender, and the turret
  stops moving in the game. An Empty joins the model by carrying `pof_index`,
  which is also how you can promote one you made by hand; every other empty in
  your file is left alone.
- **Export reads evaluated objects, not raw mesh data.** Object transforms and
  modifiers are applied on the way out; the alternative silently ships something
  other than what the viewport shows. Material slots are read off the
  evaluated object too, so a slot a Boolean or a Geometry Nodes tree introduced
  is a texture the model uses rather than faces exported flat grey. Submodel
  offsets are world-space deltas from the parent, which is what the engine's own
  hierarchy walk expects: it sums them as plain vector adds without applying
  parent rotation. An object whose transform mirrors — a wing duplicated and
  scaled `-1` — has each face's corners written in reverse, because a mirror
  reverses orientation and the winding would otherwise contradict the normals
  stored beside it.
- **Round-trip fidelity is the correctness bar.** Import then export should
  produce an equivalent file, enforced by `tests/test_version_features.py`, which
  round-trips a model at every version where the record layout changes.
- **Gun and attach points are empties matched by name prefix.** That makes the
  prefix a contract between import and export, which is why it is configurable in
  one place both halves read (see [Configuration](configuration.md)). The number
  after the prefix is a contract too, and export sorts on it: a gun bank's index
  is what WBAT cites and what the game bolts hardware to, while
  `bpy.data.objects` is sorted as text and would put `Gun_10` between `Gun_1` and
  `Gun_2`.
