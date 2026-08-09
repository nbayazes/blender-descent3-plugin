"""
Generate mock POF test files for unit testing.
"""

import struct
import os

POF_MAGIC = b"PSPO"
VERSION = 2300  # v23.00


def write_int32(f, value):
    f.write(struct.pack("<i", value))


def write_uint32(f, value):
    f.write(struct.pack("<I", value))


def write_float(f, value):
    f.write(struct.pack("<f", value))


def write_vector3(f, x, y, z):
    f.write(struct.pack("<fff", x, y, z))


def write_string(f, s):
    encoded = s.encode("ascii")
    write_int32(f, len(encoded))
    f.write(encoded)


def write_chunk_header(f, chunk_id, length):
    write_uint32(f, chunk_id)
    write_int32(f, length)


def fcc(s):
    return struct.unpack("<I", s.encode("ascii"))[0]


def write_ohdr(f, n_submodels=1, radius=1.0):
    """Write OHDR chunk."""
    buf = bytearray()
    write_int32_buf(buf, n_submodels)
    write_float_buf(buf, radius)
    write_vector3_buf(buf, -1.0, -1.0, -1.0)  # min
    write_vector3_buf(buf, 1.0, 1.0, 1.0)  # max
    write_int32_buf(buf, 1)  # 1 detail level
    write_int32_buf(buf, 0)  # detail level 0 = submodel 0
    write_chunk_header(f, fcc("OHDR"), len(buf))
    f.write(buf)


def write_txtr(f, textures=None):
    """Write TXTR chunk."""
    if textures is None:
        textures = ["default"]
    buf = bytearray()
    write_int32_buf(buf, len(textures))
    for tex in textures:
        write_string_buf(buf, tex)
    write_chunk_header(f, fcc("TXTR"), len(buf))
    f.write(buf)


def write_sobj(f, index=0, parent=-1, verts=None, faces=None, name="test", props=""):
    """Write SOBJ chunk."""
    if verts is None:
        verts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    if faces is None:
        faces = [(0, 1, 2, 3)]

    buf = bytearray()
    write_int32_buf(buf, index)
    write_int32_buf(buf, parent)
    write_vector3_buf(buf, 0, 0, 1)  # normal
    write_float_buf(buf, 0.0)  # d
    write_vector3_buf(buf, 0, 0, 0)  # point
    write_vector3_buf(buf, 0, 0, 0)  # offset
    write_float_buf(buf, 1.0)  # radius
    write_int32_buf(buf, 0)  # tree_offset
    write_int32_buf(buf, 0)  # data_offset
    write_vector3_buf(buf, 0.5, 0.5, 0)  # geometric_center
    write_string_buf(buf, name)
    write_string_buf(buf, props)
    write_int32_buf(buf, -1)  # movement_type
    write_int32_buf(buf, 0)  # movement_axis
    write_int32_buf(buf, 0)  # freespace chunks

    write_int32_buf(buf, len(verts))
    for v in verts:
        write_vector3_buf(buf, *v)
    for v in verts:
        write_vector3_buf(buf, 0, 0, 1)  # normals

    # Alpha per vertex (v23+)
    for _ in verts:
        write_float_buf(buf, 1.0)

    write_int32_buf(buf, len(faces))
    for face_verts in faces:
        write_vector3_buf(buf, 0, 0, 1)  # face normal
        write_int32_buf(buf, len(face_verts))
        write_int32_buf(buf, 1)  # textured
        write_int32_buf(buf, 0)  # texnum
        for vi in face_verts:
            write_int32_buf(buf, vi)
            write_float_buf(buf, 0.0)  # u
            write_float_buf(buf, 0.0)  # v
        # Lightmap UV diffs (v21+)
        write_float_buf(buf, 0.0)
        write_float_buf(buf, 0.0)

    write_chunk_header(f, fcc("SOBJ"), len(buf))
    f.write(buf)


def write_int32_buf(buf, value):
    buf.extend(struct.pack("<i", value))


def write_float_buf(buf, value):
    buf.extend(struct.pack("<f", value))


def write_vector3_buf(buf, x, y, z):
    buf.extend(struct.pack("<fff", x, y, z))


def write_string_buf(buf, s):
    encoded = s.encode("ascii")
    write_int32_buf(buf, len(encoded))
    buf.extend(encoded)


def generate_minimal_cube(output_path):
    """Generate a minimal valid POF with a simple quad."""
    with open(output_path, "wb") as f:
        f.write(POF_MAGIC)
        write_int32(f, VERSION)
        write_ohdr(f, n_submodels=1, radius=1.0)
        write_txtr(f, ["default"])
        write_sobj(
            f,
            index=0,
            parent=-1,
            verts=[
                (-0.5, -0.5, 0.0),
                (0.5, -0.5, 0.0),
                (0.5, 0.5, 0.0),
                (-0.5, 0.5, 0.0),
            ],
            faces=[(0, 1, 2, 3)],
            name="hull",
        )


def generate_empty_model(output_path):
    """Generate a POF with 0 vertices and 0 faces."""
    with open(output_path, "wb") as f:
        f.write(POF_MAGIC)
        write_int32(f, VERSION)
        write_ohdr(f, n_submodels=1, radius=0.0)
        write_txtr(f, [])
        write_sobj(
            f,
            index=0,
            parent=-1,
            verts=[],
            faces=[],
            name="empty",
        )


def generate_single_vertex(output_path):
    """Generate a POF with 1 vertex and 0 faces."""
    with open(output_path, "wb") as f:
        f.write(POF_MAGIC)
        write_int32(f, VERSION)
        write_ohdr(f, n_submodels=1, radius=0.0)
        write_txtr(f, ["point"])
        write_sobj(
            f,
            index=0,
            parent=-1,
            verts=[(0.0, 0.0, 0.0)],
            faces=[],
            name="point",
        )


def generate_corrupt_magic(output_path):
    """Generate a file with invalid magic bytes."""
    with open(output_path, "wb") as f:
        f.write(b"NOPE")
        write_int32(f, VERSION)


def generate_truncated(output_path):
    """Generate a truncated POF file."""
    with open(output_path, "wb") as f:
        f.write(POF_MAGIC)
        write_int32(f, VERSION)
        write_uint32(f, fcc("OHDR"))
        write_int32(f, 100)  # claim 100 bytes but don't write them


def generate_hierarchical(output_path):
    """Generate a POF with parent-child hierarchy (2 submodels)."""
    with open(output_path, "wb") as f:
        f.write(POF_MAGIC)
        write_int32(f, VERSION)
        write_ohdr(f, n_submodels=2, radius=2.0)
        write_txtr(f, ["hull", "turret"])
        write_sobj(
            f,
            index=0,
            parent=-1,
            verts=[
                (-1.0, -1.0, 0.0),
                (1.0, -1.0, 0.0),
                (1.0, 1.0, 0.0),
                (-1.0, 1.0, 0.0),
            ],
            faces=[(0, 1, 2, 3)],
            name="hull",
        )
        write_sobj(
            f,
            index=1,
            parent=0,
            verts=[
                (-0.2, -0.2, 0.5),
                (0.2, -0.2, 0.5),
                (0.2, 0.2, 0.5),
                (-0.2, 0.2, 0.5),
            ],
            faces=[(0, 1, 2, 3)],
            name="turret",
            props="$fov=180, 2.0, 1.0",
        )


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "mock_data")
    os.makedirs(out_dir, exist_ok=True)

    generate_minimal_cube(os.path.join(out_dir, "minimal_cube.pof"))
    generate_empty_model(os.path.join(out_dir, "empty_model.pof"))
    generate_single_vertex(os.path.join(out_dir, "single_vertex.pof"))
    generate_corrupt_magic(os.path.join(out_dir, "corrupt_magic.pof"))
    generate_truncated(os.path.join(out_dir, "truncated.pof"))
    generate_hierarchical(os.path.join(out_dir, "hierarchical.pof"))

    print(f"Generated mock POF files in {out_dir}")
