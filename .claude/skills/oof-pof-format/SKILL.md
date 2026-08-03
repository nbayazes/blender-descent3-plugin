---
name: oof-pof-format
description: Reference for the Descent 3 POF/OOF binary model format and how to inspect it. Use when parsing, writing, or debugging .oof/.pof files, adding format fields, or diagnosing why a model's geometry/textures/UVs/hierarchy parse wrong.
---

# Descent 3 POF/OOF binary format

Descent 3 model files (`.oof`, and `.pof` here) parse/write in
`descent3_importer/poformat.py`. The parser has **no `bpy` dependency**, so run
it standalone to inspect any file:

```python
import sys; sys.path.insert(0, ".")   # repo root
from descent3_importer import poformat
model = poformat.parse_pof(open("data/some.OOF", "rb").read())
print(model.version, model.textures, len(model.submodels))
```

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

## Strings — the subtle one

Length-prefixed, and **the int32 length INCLUDES the trailing NUL byte**.
Real file bytes: `0e 00 00 00` (=14) then `"WarningStripe\0"` (13 chars + NUL).
So the reader must `rstrip("\x00")` and the writer must append `"\x00"` and
count it. A name left with a trailing NUL won't match its image file on disk.
(The hand-written `tests/generate_mock_pof.py` omits the NUL; the reader
tolerates both. `poformat.write_pof` produces the real NUL-terminated form —
prefer it for realistic fixtures.)

## Version-gated fields (easy to get wrong)

| Condition | Field |
|-----------|-------|
| `version < 18` | multiply version by 100 (old files) |
| `version > 1805` | subobject `geometric_center` (vec3) present |
| `major >= 21` | per-face lightmap UV diffs (2 floats); model flag `PMF_LIGHTMAP_RES` |
| `major >= 22` | timed animation layout (ANIM/PANI carry per-track times) |
| `major >= 23` | **per-vertex alpha float** after vertex normals |
| `version >= 1908` | gun bank `parent` int32 before point/normal |

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

## Textures

The `TXTR` chunk is a list of **names**, and faces reference them by `texnum`
(an index into that list). The names are game-texture names, NOT file paths —
the matching bitmap (`.png` here) is resolved separately by name on disk. See
`texutil.find_texture_image` and the `blender-addon-testing` skill.

## Inspecting raw bytes

`xxd data/some.OOF | head -60` — the header `50 53 50 4f`, then `TXTR` with an
int32 count and length-prefixed names, makes the string convention obvious. Map
offsets by hand against the SOBJ field order above when a parse looks off.

## Chunks handled
`OHDR` (header/bounds/detail), `TXTR` (textures), `SOBJ` (subobjects),
`GPNT` (gun points), `WBAT` (weapon batteries), `ANIM`/`RANI`/`PANI` (rotation
& position animation), `GRND` (ground planes), `ATCH`/`NATH` (attach points
& their normals/uvecs).
