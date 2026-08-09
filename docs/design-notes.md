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
  axis, the `$rotate=` string, the texture a material stands for. Authorship
  and texture identity are deliberately two keys rather than one, because they
  answer different questions — see [Custom properties](custom-properties.md).
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
- **Round-trip fidelity is the correctness bar, and today it is met at the
  binary layer only.** Write then read back produces an equivalent file, enforced
  by `tests/test_version_features.py`, which round-trips a model at every version
  where the record layout changes and asserts the re-emitted bytes are identical,
  and by `tests/test_roundtrip.py`, which checks field by field that nothing is
  corrupted on the way. Both drive `write_pof` → `parse_pof` with no Blender in
  the loop. The same bar through the scene — import then export — is not met:
  the Blender layer never carries a good deal of what the parser reads, and the
  next note sets out what.
- **The format layer parses more than the Blender layer surfaces, and the
  parsing is kept on purpose.** `poformat` reads — and writes back — a good deal
  that `import_pof` never puts in the scene: animation keyframes (`ANIM`/`RANI`
  and `PANI`, with the per-submodel track ranges), per-vertex alpha, weapon
  batteries (`WBAT`, which says which gun points fire as one bank and which
  turret aims them), ground planes (`GRND`), an untextured face's flat RGB and
  the `textured` flag that selects it, the file's authored
  per-face normal, and the SOBJ auxiliary fields — separation-plane normal and
  point, geometric centre, tree and data offsets. The model's own frame range is
  not in that list: `POFModel.frame_min`/`frame_max` are derived at parse from the
  track bounds and no chunk carries them, so `write_pof` has nothing to write.
  The per-submodel track ranges are the part that is genuinely written back, and
  only in the timed formats. Because none of the rest lands on a Blender
  datablock, `export_pof` has nothing to read back, and it fails to write it out
  in two distinct ways. Where the field is simply never set, the `Submodel`
  dataclass default reaches the file — a zero separation-plane normal and point,
  a zero geometric centre, zero tree and data offsets — and where a list stays
  empty the chunk is omitted outright, which is what happens to `WBAT` and
  `GRND`; `ANIM`/`PANI` go the same way because export leaves the per-submodel
  key counts the writer gates on at zero. Where export builds the record itself
  it writes a
  placeholder over the top: every vertex is assigned `ALPHA_OPAQUE`, every face
  that matches no texture is assigned `DEFAULT_UNTEXTURED_COLOR` grey, and every
  face normal is taken from Blender's polygon normal rather than the authored one.
  A round trip through Blender therefore drops all of it, silently.
  The parsing stays because it is the hard half: the record layouts are
  version-gated and covered by the binary-layer tests, and they are what the
  scene-building side will be built on when each of these grows a Blender
  representation. Two consequences worth stating plainly. The first is the gap
  named above: `write_pof` → `parse_pof` preserves all of this, `import_pof` →
  `export_pof` does not, and no test crosses that boundary *for the fields
  listed here*. Tests do cross it: the four Blender round-trip scripts —
  `test_uv_roundtrip.py`, `test_coordinate_system.py`,
  `test_material_reuse.py` and `test_texture_export.py` — all run `load_pof` →
  `save_pof` → `parse_pof`. What they assert is geometry, UVs, materials and the
  Y-up/Z-up conversion, none of which is dropped. The tests that do assert the
  dropped fields, `tests/test_roundtrip.py`, drive `write_pof` → `parse_pof` with
  no Blender in the loop. Nothing checks both at once, so a green suite is not
  evidence of a faithful round trip through the scene.
  And a few things are lost a layer lower still, before Blender is involved at
  all: the OHDR detail-level table, the separation plane's `d`, and the per-face
  lightmap UV offsets are read only to stay aligned with the stream and never
  stored, so the writer re-emits fixed values for them. [Importing](importing.md)
  states the user-facing half of this.
- **Gun and attach points are empties matched by name prefix.** That makes the
  prefix a contract between import and export, which is why it is configurable in
  one place both halves read (see [Configuration](configuration.md)). The number
  after the prefix is a contract too, and export sorts on it: a gun bank's index
  is what WBAT cites and what the game bolts hardware to, while
  `bpy.data.objects` is sorted as text and would put `Gun_10` between `Gun_1` and
  `Gun_2`.
