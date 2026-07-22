# Descent 3 OOF/POF Blender Import/Export Addon - Implementation Plan

## Environment
- **Blender Installation**: `C:\Program Files\Blender Foundation\Blender 5.0`
- **Addon Location**: `%APPDATA%\Blender Foundation\Blender\5.0\scripts\addons\descent3_importer\`
- **Project Root**: `D:\dev\descent\blender-pof-plugin\`

---

## File Format Summary (POF/OOF)

The Descent 3 model format is a binary chunk-based format:

```
Header:
  char[4]  magic     = "PSPO"
  int32    version   = major * 100 + minor  (e.g., 2300 = v23.00)

Chunks:
  char[4]  chunk_id
  int32    length     (does NOT include the 8-byte header)
  byte[]   data
```

### Key Chunks
| ID | Name | Contents |
|----|------|----------|
| `OHDR` | Object Header | num_submodels, radius, bounding box, detail levels |
| `TXTR` | Textures | count + length-prefixed filenames |
| `SOBJ` | Subobject | parent, offset, vertices, normals, faces, UVs, flags, properties |
| `GPNT` | Gun Points | parent, position, normal per gun |
| `WBS`/`WBAT` | Weapon Batteries | gunpoints, turrets per battery |
| `ANIM`/`RANI` | Rotation Animation | keyframe axes, angles, timing |
| `PANI` | Position Animation | keyframe positions, timing |
| `ATTACH`/`ATCH` | Attach Points | parent, position, normal |
| `GRND` | Ground Planes | parent, position, normal |

### SOBJ Subobject Structure
```
int32   index
int32   parent
vector  normal       (separation plane)
float   d
vector  point        (separation plane point)
vector  offset       (from parent)
float   radius
int32   tree_offset
int32   data_offset
vector  geometric_center  (if version > 1805)
string  name         (length-prefixed)
string  properties   (length-prefixed, e.g. "$rotate=5", "$glow=...")
int32   movement_type
int32   movement_axis
int32   num_chunks   (freespace, skip)
int32   num_vertices
vector[] vertices
vector[] normals
float[]  alpha        (if version_major >= 23)
int32   num_faces
  per face:
    vector  normal
    int32   num_verts
    int32   textured  (1=texture, 0=solid color)
    if textured: int32 texnum
    else: byte r, g, b
    per vert:
      int32  vert_index
      float  u
      float  v
    if version_major >= 21:
      float  lightmap_u_diff
      float  lightmap_v_diff
```

---

## Implementation Steps

### Phase 1: Binary Parser & Writer (`poformat.py`)

Create a pure Python binary parser/writer that reads and writes POF/OOF files:

1. **File reading utilities** - `read_int32()`, `read_float()`, `read_vector()`, `read_string()`
2. **File writing utilities** - `write_int32()`, `write_float()`, `write_vector()`, `write_string()`
3. **Chunk iterator** - yields `(chunk_id, length, data)` tuples
4. **OHDR parser/writer** - extracts/writes submodel count, radius, bounding box
5. **TXTR parser/writer** - extracts/writes texture name list
6. **SOBJ parser/writer** - extracts/writes full submodel data (vertices, normals, faces, UVs, flags, properties)
7. **GPNT parser/writer** - gun points
8. **WBS parser/writer** - weapon batteries
9. **Animation parsers/writers** - ANIM, RANI, PANI
10. **ATTACH parser/writer** - attach points
11. **Main loader** - orchestrates chunk parsing into a `POFModel` dataclass
12. **Main writer** - serializes `POFModel` dataclass back to binary POF format

### Phase 2: Blender Addon (`__init__.py`)

Create the Blender addon structure with import AND export:

1. **bl_info / manifest.toml** - addon metadata
2. **ImportOperator** - `ImportOperator` subclass with:
   - `bl_idname = "import_scene.descent3_pof"`
   - `bl_label = "Descent 3 POF/OOF"`
   - File browser filter for `.pof`, `.oof` files
3. **ExportOperator** - `ExportOperator` subclass with:
   - `bl_idname = "export_scene.descent3_pof"`
   - `bl_label = "Descent 3 POF/OOF"`
   - File browser filter for `.pof`, `.oof` files
   - Options: version number, include animations, include gun points
4. **`load_pof(context, filepath)`** - main import function:
   - Parse file with `poformat.py`
   - Create Blender mesh objects per submodel
   - Build mesh from vertices + faces + UVs
   - Apply materials (texture names as placeholders)
   - Set up parent-child hierarchy
   - Handle submodel offsets and transforms
5. **`save_pof(context, filepath)`** - main export function:
   - Collect selected objects or all mesh objects
   - Build POFModel from Blender meshes
   - Extract UV maps, materials, vertex normals
   - Reconstruct submodel hierarchy from parent relationships
   - Write POF file with `poformat.py`
6. **Menu registration** - File > Import/Export > Descent 3 POF/OOF

### Phase 3: Mesh Construction (Import)

For each submodel in the POF:

1. **Create Blender mesh** - `bpy.data.meshes.new(name)`
2. **Set vertices** - `mesh.from_pydata(vertices, edges, faces)`
3. **Set UVs** - create UV layer, assign per-face-vertex UVs
4. **Set normals** - `mesh.normals_split_custom_set(normals)`
5. **Create object** - `bpy.data.objects.new(name, mesh)`
6. **Set material** - create material with texture name
7. **Parent** - set parent object based on SOBJ parent index
8. **Transform** - apply submodel offset

### Phase 4: Mesh Extraction (Export)

For each Blender object being exported:

1. **Read mesh data** - `obj.data.vertices`, `obj.data.polygons`
2. **Extract UVs** - from active UV layer
3. **Extract normals** - from split normals or auto-smooth
4. **Extract material** - from material slots, map to texture index
5. **Compute bounding box** - min/max across all vertices
6. **Compute radius** - max distance from origin
7. **Reconstruct hierarchy** - follow `obj.parent` chain
8. **Build submodel properties** - generate `$rotate`, `$glow` etc. from custom properties

### Phase 5: Testing

1. **Mock POF file** - create a minimal binary test file with:
   - Valid header + version
   - One OHDR chunk with 1 submodel
   - One TXTR chunk with 1 texture
   - One SOBJ chunk with a simple cube (8 verts, 6 faces)
2. **Test parser** - verify all fields are read correctly
3. **Test import** - verify Blender scene has correct mesh, materials, hierarchy
4. **Edge cases**:
   - Empty model (0 submodels)
   - Single vertex (no faces)
   - Degenerate faces (zero-area)
   - Corrupted magic bytes
   - Invalid version number
   - Truncated file

---

## File Structure

```
D:\dev\descent\blender-pof-plugin\
├── PLAN.md                      # This file
├── descent3_importer/            # Blender addon package
│   ├── __init__.py              # Addon registration, ImportOperator, ExportOperator
│   ├── poformat.py              # Binary POF/OOF parser & writer
│   └── manifest.toml            # Blender 5.0 extension manifest
├── tests/                        # Test suite
│   ├── __init__.py
│   ├── test_poformat.py         # Parser/writer unit tests
│   ├── test_import.py           # Blender import integration tests
│   ├── test_export.py           # Blender export integration tests
│   ├── test_roundtrip.py        # Import-then-export roundtrip tests
│   └── mock_data/               # Mock POF files
│       ├── minimal_cube.pof     # Simplest valid model
│       ├── empty_model.pof      # 0 submodels
│       └── single_vertex.pof    # 1 vertex, 0 faces
├── polymodel.cpp                 # Reference: Descent 3 parser (existing)
├── polymodel.h                   # Reference: Descent 3 header (existing)
└── polymodel_external.h          # Reference: Struct definitions (existing)
```

---

## Key Design Decisions

1. **Pure Python parser/writer** - no C extensions needed, Blender has struct module
2. **Dataclass-based model** - clean separation between parsing/writing and Blender creation
3. **Coordinate system conversion** - Descent uses Z-up right-handed; Blender uses Z-up too (same convention, no flip needed)
4. **Scale** - Descent units are roughly meters; import at 1:1 scale
5. **Textures** - create placeholder materials with texture names (actual .OGF textures require separate handling)
6. **Submodel hierarchy** - preserve parent-child tree as Blender object parenting
7. **Roundtrip fidelity** - import then export should produce equivalent POF files
8. **Export flexibility** - can export selected objects or entire scene
9. **Custom properties** - use Blender custom properties to store POF-specific data (flags, movement type, etc.)
10. **Version targeting** - export defaults to version 23.00 (latest compatible)

---

## Blender API Key Calls (Import)

```python
# Create mesh
mesh = bpy.data.meshes.new(name)
mesh.from_pydata(verts, [], faces)  # edges auto-computed

# Set UVs
uv_layer = mesh.uv_layers.new(name="UVMap")
for face_idx, face in enumerate(mesh.polygons):
    for loop_idx in face.loop_indices:
        vert_idx = mesh.loops[loop_idx].vertex_index
        uv_layer.data[loop_idx].uv = (u, v)

# Set normals
norms = [Vector(n) for n in vertex_normals]
mesh.normals_split_custom_set_from_vertices(norms)

# Create object
obj = bpy.data.objects.new(name, mesh)
bpy.context.collection.objects.link(obj)

# Set parent
obj.parent = parent_obj
obj.matrix_parent_inverse = parent_obj.matrix_world.inverted()

# Material
mat = bpy.data.materials.new(name=tex_name)
obj.data.materials.append(mat)
```

## Blender API Key Calls (Export)

```python
# Get mesh data
mesh = obj.data
vertices = [v.co[:] for v in mesh.vertices]
polygons = [p.vertices[:] for p in mesh.polygons]

# Get UVs
uv_layer = mesh.uv_layers.active
uvs = {}
if uv_layer:
    for face in mesh.polygons:
        for loop_idx in face.loop_indices:
            vert_idx = mesh.loops[loop_idx].vertex_index
            uvs[(face.index, vert_idx)] = uv_layer.data[loop_idx].uv[:]

# Get normals
normals = [v.normal[:] for v in mesh.vertices]

# Get material/texture
texture_name = "default"
if mesh.materials and mesh.materials[0]:
    texture_name = mesh.materials[0].name

# Check for POF-specific custom properties
props = obj.get("pof_properties", "")
flags = obj.get("pof_flags", 0)
```
