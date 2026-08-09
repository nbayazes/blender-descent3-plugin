---
name: oof-pof-format
description: Reference for the Descent 3 POF/OOF binary model format and how to inspect it. Use when parsing, writing, or debugging .oof/.pof files, adding format fields, or diagnosing why a model's geometry/textures/UVs/hierarchy parse wrong.
---

# Descent 3 POF/OOF binary format

Descent 3 model files (`.oof`, and `.pof` here) parse/write in
`descent3_plugin/poformat.py`. The parser has **no `bpy` dependency**, so run
it standalone to inspect any file:

```python
import sys; sys.path.insert(0, ".")   # repo root
from descent3_plugin import poformat
model = poformat.parse_pof(open("tests/fixtures/textured_model/textured_cube.oof", "rb").read())
print(model.version, model.textures, len(model.submodels))   # 2300 ['Hull', 'Turret'] 1
```

`parse_pof` takes bytes; `parse_pof_stream` takes an open binary stream. Use the
committed fixture above for anything reproducible — `data/` holds real game
models but is gitignored, so it is absent on a fresh clone.

## Layout

```
Header:  char[4] magic "PSPO"  +  int32 version   (version = major*100 + minor)
Chunks:  char[4] id  +  int32 length  +  <length bytes of data>   (repeat to EOF)
```

- Chunk IDs are FourCCs stored in reading order; `_fcc("OHDR")` etc. Always
  `seek(chunk_start + length)` after handling a chunk, so an unknown or
  mis-parsed chunk never desyncs the file (this is why bad geometry in one
  submodel doesn't break the rest).
- Little-endian throughout.
- **Accepted versions are 1807..2300** (`MIN_OBJFILE_VERSION` ..
  `OBJFILE_VERSION`, the engine's `PM_COMPATIBLE_VERSION` / `PM_OBJFILE_VERSION`).
  Anything outside raises `ValueError`, so a "parse failure" on an old asset may
  simply be a file this parser refuses rather than one it mis-reads.
- A header value below 18 is rescaled by 100 as a bare major version — but note
  that path is unreachable in practice: the largest thing it can produce is 1700,
  which the range check above then rejects. Do not read the rescale as evidence
  that pre-1807 files load.
- Every read is bounds-checked, so truncation raises `EOFError` at the short
  read. A file that ends part-way through a *chunk header* is different: that is
  the normal end-of-file condition, so it only logs a warning about the trailing
  bytes and returns what it had. Check the log when a model is mysteriously
  short a chunk.

### What the reader refuses outright

Sizes come off disk and then decide how far to seek and how much to allocate, so
they are validated at the point they are read, and a bad one raises `ValueError`
rather than being clamped — guessing at a repair means guessing where the next
header is. Expect these when feeding it a fuzzed or third-party file:

| Refused | Why it is not merely bad data |
|---------|-------------------------------|
| chunk length < 0, or past EOF | the loop seeks to `chunk_start + length`; a negative one seeks *backwards* onto the header it just read and spins forever |
| SOBJ index outside `0..MAX_SUBMODEL_INDEX` (4095) | `submodels[-1] = sm` does not raise, it silently overwrites the last submodel parsed |
| any element count negative, or larger than the remaining bytes could hold | a huge count grinds through allocations; a negative one skips its loop and misaligns everything after |
| string length prefix < 0 or > `MAX_STRING_LENGTH` | same, for `read_string` |

`POFReader.read_count(what, item_size)` is the shared check — `item_size` is a
*floor* on one record's size, not its exact width, so the bound is "the file is
not big enough for that many" rather than a fixed cap. Read a count with it, not
with `read_int32`, when adding a chunk. The one deliberate exception is `NATH`:
its count is accepted or rejected by comparing it to the `ATCH` count, and a
mismatch falls back to skipping the up-vectors rather than failing the import.

## Strings — the subtle one

Length-prefixed, and **the int32 length INCLUDES the trailing NUL byte**.
Real file bytes: `0e 00 00 00` (=14) then `"WarningStripe\0"` (13 chars + NUL).
So the reader must `rstrip("\x00")` and the writer must append `"\x00"` and
count it. A name left with a trailing NUL won't match its image file on disk.
(The hand-written `tests/generate_mock_pof.py` omits the NUL; the reader
tolerates both. `poformat.write_pof` produces the real NUL-terminated form —
prefer it for realistic fixtures.)

## Version-gated fields (easy to get wrong)

| Condition | Field | `VersionFeatures` attribute |
|-----------|-------|-----------------------------|
| `version > 1805` | subobject `geometric_center` (vec3) present | `geometric_center` |
| `version >= 1908` | gun bank `parent` int32 before point/normal | `gunpoint_parent` |
| `major >= 21` | per-face lightmap UV diffs (2 floats); model flag `PMF_LIGHTMAP_RES` | `lightmap_uv` |
| `major >= 22` | timed animation layout (ANIM/PANI carry per-track times) | `timed_anim` |
| `major >= 23` | **per-vertex alpha float** after vertex normals | `vertex_alpha` |

**Never re-test a version number by hand.** Ask
`poformat.features_for(version, major)`, or `model.features` when you have a
model. Every gate lives in that one function and the reader and writer both go
through it, which is the only reason they cannot disagree about how many bytes a
record occupies. A hand-rolled `if version >= 2100` in new code is how that
guarantee gets lost — and the comparisons are deliberately not uniform
(`geometric_center` is *strictly* greater; it and `gunpoint_parent` test the full
version while the rest test the major), so a hand-rolled one is likely wrong
anyway.

Because the minimum accepted version is 1807, `geometric_center` is present in
every file this parser will open. The gate is kept to mirror the engine, not
because a file without it can reach the reader.

Getting the v23 alpha block or v21 lightmap diffs wrong desyncs **faces** (incl.
`texnum`) while vertices still look fine — i.e. "geometry imports but textures
are wrong." Suspect version gating first for that symptom.

## SOBJ (subobject) field order

`index, parent, normal(vec3), d(float), point(vec3), offset(vec3), radius,
tree_offset, data_offset, [geometric_center if v>1805], name(str), props(str),
movement_type, movement_axis, n_freespace_chunks(+skip), n_verts,
verts[n](vec3), normals[n](vec3), [alpha[n](float) if major>=23],
n_faces, faces[...]`.

Each face: `normal(vec3), n_fv(int32), textured(int32)` then if textured
`texnum(int32)` else `rgb(3 bytes)`, then `n_fv * (index int32, u float, v float)`,
then `[xdiff,ydiff floats if major>=21]`.

## Hierarchy — index, not list position

A submodel is stored at `model.submodels[sm.index]`, **not** appended in the
order the SOBJ chunks arrive: real files carry them out of order and with gaps.
The list is grown with `None` placeholders and any still unfilled at the end are
dropped (with a warning naming the indices), so after a parse the list is dense
again and `submodels[i].index` is *not* guaranteed to be `i`. Resolve a parent
through `sm.parent` and a lookup, never by indexing the list.

`children` is not in the file. `POFModel.build_hierarchy()` computes it from
every `sm.parent`, and `parse_pof_stream` calls it for you; a model you build by
hand needs the call before anything walks the tree.

`sm.offset` is the translation from the parent submodel, and the engine
accumulates it down the chain as a **plain vector add with no parent rotation
applied** — `MinMaxSubmodel` (polymodel.cpp:1230) does `offset += sm->offset`
before recursing into each child. So a submodel's model-space position is the
sum of the offsets from the root, and the model bounding box in OHDR must be
grown from each vertex plus that accumulated offset.

## Coordinate system

Descent 3 is right-handed **Y-up**; Blender is right-handed **Z-up**. Every
vector in the file -- vertex positions, vertex and face normals, submodel
offsets, gun/attach points, keyframe axes -- is in Descent space.

`poformat` never converts: it hands back exactly what is on disk. The rotation
happens at the Blender boundary only, in `mathutil.descent_to_blender` /
`blender_to_descent`:

```
descent (x, y, z)  ->  blender (x, -z, y)     # 90 deg about X
blender (x, y, z)  ->  descent (x,  z, -y)
```

Both spaces are right-handed, so this is a rotation and not a mirror --
winding order is preserved and faces never need reversing. If a model imports
mirrored or inside out, the cause is not this conversion.

Note that a round-trip test cannot catch a *missing* conversion: with no
conversion applied the transform is the identity in both directions, so
import-then-export still returns the original bytes. Assert the rotated
coordinate explicitly, as `tests/blender/test_coordinate_system.py` does.

## Textures

The `TXTR` chunk is a list of **names**, and faces reference them by `texnum`
(an index into that list). The names are game-texture names, NOT file paths —
the matching bitmap (`.png` here) is resolved separately by name on disk. See
`texutil.find_texture_image` and the `blender-addon-testing` skill.

A texture ID is not automatically a legal filename, and two places treat it as
one: the importer searching for `<id><ext>`, and texture export writing
`<id><ext>`. `naming.rejected_texture_name(id)` is the check — it refuses path
separators, `..`, drive letters, Windows device names (`NUL`, `COM1`, …) and
trailing dots or spaces, and returns the reason. A model of unknown provenance
can carry any of those in its TXTR chunk, and `os.path.join` silently *discards*
the destination directory when handed an absolute second argument, so the check
runs before the join, never after.

Nothing in `poformat` validates or rewrites a name: it writes the string it was
handed. Which string that is comes from `naming.resolve_texture_names`, where a
material carrying the `d3_texture` custom property exports as the ID it records
and everything else falls back to the `.NNN` duplicate-suffix heuristic. A name
`rejected_texture_name` refuses still reaches the TXTR chunk — rewriting it would
point those faces at a different bitmap — so `export_pof._collect_textures`
reports it when the model is built, which is the only place that runs whether or
not texture images were asked for.

`d3_texture` says *which texture* a material stands for. `d3_addon_material` says
the add-on *created* the datablock, and is what import checks before finishing a
material off with an image. They are separate keys on purpose: adoption writes
the first onto a user's own material, so reading authorship from it made the
second import of a model restyle a material the first import had correctly left
alone.

## Marker orientation

`GPNT` and `ATCH` each carry a `normal` — where a gun fires, where an attached
model faces — and `NATH` optionally adds a per-attach-point `uvec` fixing the
roll about it. At the Blender boundary all of that lives in the empty's
**rotation**: `constants.MARKER_FORWARD_AXIS` (local +Z, the axis Blender draws a
single-arrow empty along) is aimed at the normal, and `MARKER_UP_AXIS` (+Y) at
the uvec. Both halves read the same two constants, so changing one turns every
marker in every model by the same wrong angle.

`NATH` is all-or-nothing per model: the parser only accepts a count equal to the
`ATCH` count, so a file either fixes every roll or none. Whether it did is kept
on each empty as `pof_has_uvec`, because an empty always *has* a roll and writing
one for every attach point would invent a constraint the model never stated.

## Inspecting raw bytes

`xxd tests/fixtures/textured_model/textured_cube.oof | head -60` — the header
`50 53 50 4f`, then `TXTR` with an int32 count and length-prefixed names, makes
the string convention obvious. Map offsets by hand against the SOBJ field order
above when a parse looks off.

## Chunks handled
`OHDR` (header/bounds/detail), `TXTR` (textures), `SOBJ` (subobjects),
`GPNT` (gun points), `WBAT` (weapon batteries), `ANIM`/`RANI`/`PANI` (rotation
& position animation), `GRND` (ground planes), `ATCH`/`NATH` (attach points
& their normals/uvecs).

`PINF` and `SPCL` have named constants (`CHUNK_INFO`, `CHUNK_SPCL`) but **no
parse branch** — having an ID is not the same as reading it, and both are
skipped like any unknown chunk. Anything they carry is lost on a round trip.

The writer emits an optional chunk only when the model actually carries that
data, so a model with no guns produces no empty `GPNT`. Two fields it does not
round-trip: the separation-plane `d` is written as `0.0`, and OHDR is always
written with exactly one detail level pointing at the root submodel, whatever
the source file declared.
