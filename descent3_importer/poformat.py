"""
Descent 3 POF/OOF Binary Format Parser & Writer

Parses and writes Descent 3 polygon model files (POF/OOF format).
Based on the Descent 3 source code and Inferno engine reference implementation.

File format:
  Header: char[4] magic ("PSPO") + int32 version (major*100+minor)
  Chunks: char[4] id + int32 length + byte[] data
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POF_MAGIC = b"PSPO"
MIN_OBJFILE_VERSION = 1807
OBJFILE_VERSION = 2300

# Chunk IDs (four-character codes as 32-bit ints, little-endian)
def _fcc(s: str) -> int:
    """Convert a 4-char code to its little-endian 32-bit integer."""
    return struct.unpack("<I", s.encode("ascii"))[0]

CHUNK_OHDR = _fcc("OHDR")
CHUNK_TXTR = _fcc("TXTR")
CHUNK_SOBJ = _fcc("SOBJ")
CHUNK_GPNT = _fcc("GPNT")
CHUNK_WBS  = _fcc("WBAT")
CHUNK_ANIM = _fcc("ANIM")
CHUNK_RANI = _fcc("RANI")
CHUNK_PANI = _fcc("PANI")
CHUNK_GRND = _fcc("GRND")
CHUNK_ATCH = _fcc("ATCH")
CHUNK_NATH = _fcc("NATH")
CHUNK_INFO = _fcc("PINF")
CHUNK_SPCL = _fcc("SPCL")

# Subobject flags
SOF_ROTATE    = 0x01
SOF_TURRET    = 0x02
SOF_SHELL     = 0x04
SOF_FRONTFACE = 0x08
SOF_MONITOR1  = 0x010
SOF_MONITOR2  = 0x020
SOF_MONITOR3  = 0x040
SOF_MONITOR4  = 0x080
SOF_MONITOR5  = 0x0100
SOF_MONITOR6  = 0x0200
SOF_MONITOR7  = 0x0400
SOF_MONITOR8  = 0x0800
SOF_FACING    = 0x01000
SOF_VIEWER    = 0x02000
SOF_LAYER     = 0x04000
SOF_WB        = 0x08000
SOF_GLOW      = 0x0200000
SOF_CUSTOM    = 0x0400000
SOF_THRUSTER  = 0x0800000
SOF_JITTER    = 0x01000000
SOF_HEADLIGHT = 0x02000000

# Model flags
PMF_LIGHTMAP_RES  = 1
PMF_TIMED         = 2
PMF_ALPHA         = 4
PMF_FACING        = 8
PMF_NOT_RESIDENT  = 16
PMF_SIZE_COMPUTED = 32

# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class Vector3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def __add__(self, other: "Vector3") -> "Vector3":
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: "Vector3") -> "Vector3":
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, s: float) -> "Vector3":
        return Vector3(self.x * s, self.y * s, self.z * s)

    def magnitude(self) -> float:
        return (self.x**2 + self.y**2 + self.z**2) ** 0.5

    def normalized(self) -> "Vector3":
        m = self.magnitude()
        if m < 1e-10:
            return Vector3(0, 0, 0)
        return Vector3(self.x / m, self.y / m, self.z / m)


@dataclass
class Color:
    r: float = 1.0
    g: float = 1.0
    b: float = 1.0


@dataclass
class FaceVertex:
    index: int = 0
    u: float = 0.0
    v: float = 0.0


@dataclass
class ModelFace:
    normal: Vector3 = field(default_factory=Vector3)
    vertices: list[FaceVertex] = field(default_factory=list)
    textured: bool = True
    texnum: int = -1
    color: Color = field(default_factory=Color)


@dataclass
class SubmodelVertex:
    position: Vector3 = field(default_factory=Vector3)
    normal: Vector3 = field(default_factory=Vector3)
    alpha: float = 1.0


@dataclass
class Keyframe:
    axis: Vector3 = field(default_factory=Vector3)
    angle: int = 0
    position: Vector3 = field(default_factory=Vector3)
    rot_start_time: int = 0
    pos_start_time: int = 0


@dataclass
class Submodel:
    index: int = 0
    parent: int = -1
    normal: Vector3 = field(default_factory=Vector3)
    point: Vector3 = field(default_factory=Vector3)
    offset: Vector3 = field(default_factory=Vector3)
    radius: float = 0.0
    tree_offset: int = 0
    data_offset: int = 0
    geometric_center: Vector3 = field(default_factory=Vector3)
    name: str = ""
    props: str = ""
    movement_type: int = -1
    movement_axis: int = 0
    flags: int = 0

    vertices: list[SubmodelVertex] = field(default_factory=list)
    faces: list[ModelFace] = field(default_factory=list)

    num_key_angles: int = 0
    num_key_pos: int = 0
    rot_track_min: int = 0
    rot_track_max: int = 0
    pos_track_min: int = 0
    pos_track_max: int = 0
    keyframes: list[Keyframe] = field(default_factory=list)

    # Computed children (not stored in file, built after parse)
    children: list[int] = field(default_factory=list)


@dataclass
class GunBank:
    parent: int = 0
    point: Vector3 = field(default_factory=Vector3)
    normal: Vector3 = field(default_factory=Vector3)


@dataclass
class WeaponBattery:
    gunpoints: list[int] = field(default_factory=list)
    turrets: list[int] = field(default_factory=list)


@dataclass
class AttachPoint:
    parent: int = 0
    point: Vector3 = field(default_factory=Vector3)
    normal: Vector3 = field(default_factory=Vector3)
    has_uvec: bool = False
    uvec: Vector3 = field(default_factory=Vector3)


@dataclass
class POFModel:
    version: int = OBJFILE_VERSION
    major_version: int = 23
    radius: float = 0.0
    min_bound: Vector3 = field(default_factory=Vector3)
    max_bound: Vector3 = field(default_factory=Vector3)
    flags: int = 0
    frame_min: int = 0
    frame_max: int = 0

    textures: list[str] = field(default_factory=list)
    submodels: list[Submodel] = field(default_factory=list)
    gun_banks: list[GunBank] = field(default_factory=list)
    weapon_batteries: list[WeaponBattery] = field(default_factory=list)
    ground_planes: list[GunBank] = field(default_factory=list)
    attach_points: list[AttachPoint] = field(default_factory=list)

    def build_hierarchy(self):
        """Build children lists from parent indices."""
        for sm in self.submodels:
            sm.children = []
        for i, sm in enumerate(self.submodels):
            if 0 <= sm.parent < len(self.submodels):
                self.submodels[sm.parent].children.append(i)

# ---------------------------------------------------------------------------
# Binary Reader
# ---------------------------------------------------------------------------

class POFReader:
    """Reads binary POF data from a file-like object."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream

    def read_bytes(self, n: int) -> bytes:
        data = self.stream.read(n)
        if len(data) < n:
            raise EOFError(f"Expected {n} bytes, got {len(data)}")
        return data

    def read_int32(self) -> int:
        return struct.unpack("<i", self.read_bytes(4))[0]

    def read_uint32(self) -> int:
        return struct.unpack("<I", self.read_bytes(4))[0]

    def read_uint16(self) -> int:
        return struct.unpack("<H", self.read_bytes(2))[0]

    def read_uint8(self) -> int:
        return struct.unpack("<B", self.read_bytes(1))[0]

    def read_float(self) -> float:
        return struct.unpack("<f", self.read_bytes(4))[0]

    def read_vector3(self) -> Vector3:
        x, y, z = struct.unpack("<fff", self.read_bytes(12))
        return Vector3(x, y, z)

    def read_string(self) -> str:
        length = self.read_int32()
        if length < 0 or length > 10000:
            raise ValueError(f"Invalid string length: {length}")
        data = self.read_bytes(length)
        return data.decode("ascii", errors="replace")

    def read_color_rgb(self) -> Color:
        r = self.read_uint8()
        g = self.read_uint8()
        b = self.read_uint8()
        return Color(r / 255.0, g / 255.0, b / 255.0)

    def read_chunk_header(self) -> tuple[int, int]:
        """Read chunk ID and length. Returns (chunk_id, data_length)."""
        chunk_id = self.read_uint32()
        length = self.read_int32()
        return chunk_id, length

    def skip(self, n: int):
        self.stream.seek(n, 1)

    def tell(self) -> int:
        return self.stream.tell()

    def seek(self, pos: int):
        self.stream.seek(pos)

# ---------------------------------------------------------------------------
# Binary Writer
# ---------------------------------------------------------------------------

class POFWriter:
    """Writes binary POF data to a file-like object."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream

    def write_bytes(self, data: bytes):
        self.stream.write(data)

    def write_int32(self, value: int):
        self.stream.write(struct.pack("<i", value))

    def write_uint32(self, value: int):
        self.stream.write(struct.pack("<I", value))

    def write_uint16(self, value: int):
        self.stream.write(struct.pack("<H", value))

    def write_uint8(self, value: int):
        self.stream.write(struct.pack("<B", value))

    def write_float(self, value: float):
        self.stream.write(struct.pack("<f", value))

    def write_vector3(self, v: Vector3):
        self.stream.write(struct.pack("<fff", v.x, v.y, v.z))

    def write_string(self, s: str):
        encoded = s.encode("ascii")
        self.write_int32(len(encoded))
        self.write_bytes(encoded)

    def write_color_rgb(self, c: Color):
        self.write_uint8(int(c.r * 255) & 0xFF)
        self.write_uint8(int(c.g * 255) & 0xFF)
        self.write_uint8(int(c.b * 255) & 0xFF)

    def write_chunk_header(self, chunk_id: int, length: int):
        self.write_uint32(chunk_id)
        self.write_int32(length)

    def tell(self) -> int:
        return self.stream.tell()

    def seek(self, pos: int):
        self.stream.seek(pos)

# ---------------------------------------------------------------------------
# Parsing Functions
# ---------------------------------------------------------------------------

def _parse_subobj(reader: POFReader, model: POFModel, chunk_end: int):
    """Parse a SOBJ (subobject) chunk."""
    sm = Submodel()
    sm.index = reader.read_int32()
    sm.parent = reader.read_int32()
    sm.normal = reader.read_vector3()
    _d = reader.read_float()  # separation plane d, not used
    sm.point = reader.read_vector3()
    sm.offset = reader.read_vector3()
    sm.radius = reader.read_float()
    sm.tree_offset = reader.read_int32()
    sm.data_offset = reader.read_int32()

    if model.version > 1805:
        sm.geometric_center = reader.read_vector3()

    sm.name = reader.read_string()
    sm.props = reader.read_string()
    sm.movement_type = reader.read_int32()
    sm.movement_axis = reader.read_int32()

    # Skip freespace chunks
    n_chunks = reader.read_int32()
    for _ in range(n_chunks):
        reader.read_int32()

    # Vertices
    n_verts = reader.read_int32()
    for _ in range(n_verts):
        pos = reader.read_vector3()
        sm.vertices.append(SubmodelVertex(position=pos))

    for i in range(n_verts):
        sm.vertices[i].normal = reader.read_vector3()

    # Alpha per vertex (version >= 23)
    if model.major_version >= 23:
        for i in range(n_verts):
            sm.vertices[i].alpha = reader.read_float()
            if sm.vertices[i].alpha < 0.99:
                model.flags |= PMF_ALPHA

    # Faces
    n_faces = reader.read_int32()
    for _ in range(n_faces):
        face = ModelFace()
        face.normal = reader.read_vector3()
        n_fv = reader.read_int32()
        textured = reader.read_int32()
        if textured:
            face.textured = True
            face.texnum = reader.read_int32()
        else:
            face.textured = False
            face.color = reader.read_color_rgb()

        for _ in range(n_fv):
            idx = reader.read_int32()
            u = reader.read_float()
            v = reader.read_float()
            face.vertices.append(FaceVertex(index=idx, u=u, v=v))

        # Lightmap UV diffs (version >= 21)
        if model.major_version >= 21:
            reader.read_float()  # xdiff
            reader.read_float()  # ydiff

        sm.faces.append(face)

    # Parse properties for flags
    _parse_submodel_flags(sm)

    # Ensure index is within bounds
    while len(model.submodels) <= sm.index:
        model.submodels.append(None)
    model.submodels[sm.index] = sm


def _parse_submodel_flags(sm: Submodel):
    """Parse property string to set submodel flags."""
    props = sm.props.strip()
    if len(props) < 3:
        return

    eq_pos = props.find("=")
    if eq_pos >= 0:
        command = props[:eq_pos + 1].lower().strip()
        data = props[eq_pos + 1:].strip()
    else:
        command = props.lower().strip()
        data = ""

    if command == "$rotate=":
        try:
            spin_rate = float(data)
            if 0 < spin_rate <= 20:
                sm.flags |= SOF_ROTATE
        except ValueError:
            pass
    elif command == "$jitter":
        sm.flags |= SOF_JITTER
    elif command == "$shell":
        sm.flags |= SOF_SHELL
    elif command == "$facing":
        sm.flags |= SOF_FACING
    elif command == "$frontface":
        sm.flags |= SOF_FRONTFACE
    elif command == "$glow=":
        sm.flags |= SOF_GLOW
    elif command == "$thruster=":
        sm.flags |= SOF_THRUSTER
    elif command.startswith("$fov="):
        sm.flags |= SOF_TURRET
    elif command == "$monitor01":
        sm.flags |= SOF_MONITOR1
    elif command == "$monitor02":
        sm.flags |= SOF_MONITOR2
    elif command == "$monitor03":
        sm.flags |= SOF_MONITOR3
    elif command == "$monitor04":
        sm.flags |= SOF_MONITOR4
    elif command == "$monitor05":
        sm.flags |= SOF_MONITOR5
    elif command == "$monitor06":
        sm.flags |= SOF_MONITOR6
    elif command == "$monitor07":
        sm.flags |= SOF_MONITOR7
    elif command == "$monitor08":
        sm.flags |= SOF_MONITOR8
    elif command == "$viewer":
        sm.flags |= SOF_VIEWER
    elif command == "$layer":
        sm.flags |= SOF_LAYER
    elif command == "$custom":
        sm.flags |= SOF_CUSTOM


# ---------------------------------------------------------------------------
# Main Parse / Write Functions
# ---------------------------------------------------------------------------

def parse_pof(data: bytes) -> POFModel:
    """Parse a POF file from raw bytes and return a POFModel."""
    stream = io.BytesIO(data)
    return parse_pof_stream(stream)


def parse_pof_stream(stream: BinaryIO) -> POFModel:
    """Parse a POF file from a binary stream and return a POFModel."""
    reader = POFReader(stream)
    model = POFModel()

    # Read header
    magic = reader.read_bytes(4)
    if magic != POF_MAGIC:
        raise ValueError(f"Invalid POF magic: expected {POF_MAGIC!r}, got {magic!r}")

    version = reader.read_int32()
    if version < 18:
        version *= 100  # fix old version

    if version < MIN_OBJFILE_VERSION or version > OBJFILE_VERSION:
        raise ValueError(f"Unsupported POF version: {version}")

    model.version = version
    model.major_version = version // 100

    if model.major_version >= 21:
        model.flags |= PMF_LIGHTMAP_RES
    if model.major_version >= 22:
        model.flags |= PMF_TIMED

    # Read chunks
    timed = model.major_version >= 22

    while True:
        pos = reader.tell()
        try:
            chunk_id, chunk_len = reader.read_chunk_header()
        except EOFError:
            break

        chunk_start = reader.tell()
        chunk_end = chunk_start + chunk_len

        if chunk_id == CHUNK_OHDR:
            n_submodels = reader.read_int32()
            model.radius = reader.read_float()
            model.min_bound = reader.read_vector3()
            model.max_bound = reader.read_vector3()
            n_detail = reader.read_int32()
            for _ in range(n_detail):
                reader.read_int32()  # skip detail indices

        elif chunk_id == CHUNK_TXTR:
            n_textures = reader.read_int32()
            model.textures = []
            for _ in range(n_textures):
                name = reader.read_string()
                model.textures.append(name)

        elif chunk_id == CHUNK_SOBJ:
            _parse_subobj(reader, model, chunk_end)

        elif chunk_id == CHUNK_GPNT:
            n_guns = reader.read_int32()
            model.gun_banks = []
            for _ in range(n_guns):
                bank = GunBank()
                if model.version >= 1908:
                    bank.parent = reader.read_int32()
                bank.point = reader.read_vector3()
                bank.normal = reader.read_vector3()
                model.gun_banks.append(bank)

        elif chunk_id == CHUNK_WBS:
            n_wb = reader.read_int32()
            model.weapon_batteries = []
            for _ in range(n_wb):
                wb = WeaponBattery()
                n_gps = reader.read_int32()
                for j in range(n_gps):
                    gp = reader.read_int32()
                    if j < 8:
                        wb.gunpoints.append(gp)
                n_turrets = reader.read_int32()
                for j in range(n_turrets):
                    t = reader.read_int32()
                    if j < 8:
                        wb.turrets.append(t)
                model.weapon_batteries.append(wb)

        elif chunk_id in (CHUNK_ANIM, CHUNK_RANI):
            nframes = 0
            if not timed:
                nframes = reader.read_int32()

            for i, sm in enumerate(model.submodels):
                if sm is None:
                    continue
                if timed:
                    sm.num_key_angles = reader.read_int32()
                    sm.rot_track_min = reader.read_int32()
                    sm.rot_track_max = reader.read_int32()
                    if sm.rot_track_min < model.frame_min:
                        model.frame_min = sm.rot_track_min
                    if sm.rot_track_max > model.frame_max:
                        model.frame_max = sm.rot_track_max
                else:
                    sm.num_key_angles = nframes

                sm.keyframes = []
                for _ in range(sm.num_key_angles):
                    kf = Keyframe()
                    if timed:
                        kf.rot_start_time = reader.read_int32()
                    kf.axis = reader.read_vector3()
                    m = kf.axis.magnitude()
                    if m > 0:
                        kf.axis = kf.axis * (1.0 / m)
                    kf.angle = reader.read_int32()
                    sm.keyframes.append(kf)

        elif chunk_id == CHUNK_PANI:
            nframes = 0
            if not timed:
                nframes = reader.read_int32()

            for i, sm in enumerate(model.submodels):
                if sm is None:
                    continue
                if timed:
                    sm.num_key_pos = reader.read_int32()
                    sm.pos_track_min = reader.read_int32()
                    sm.pos_track_max = reader.read_int32()
                    if sm.pos_track_min < model.frame_min:
                        model.frame_min = sm.pos_track_min
                    if sm.pos_track_max > model.frame_max:
                        model.frame_max = sm.pos_track_max
                else:
                    sm.num_key_pos = nframes

                for kf in sm.keyframes:
                    if timed:
                        kf.pos_start_time = reader.read_int32()
                    kf.position = reader.read_vector3()

        elif chunk_id == CHUNK_GRND:
            n_ground = reader.read_int32()
            model.ground_planes = []
            for _ in range(n_ground):
                bank = GunBank()
                bank.parent = reader.read_int32()
                bank.point = reader.read_vector3()
                bank.normal = reader.read_vector3()
                model.ground_planes.append(bank)

        elif chunk_id == CHUNK_ATCH:
            n_attach = reader.read_int32()
            model.attach_points = []
            for _ in range(n_attach):
                ap = AttachPoint()
                ap.parent = reader.read_int32()
                ap.point = reader.read_vector3()
                ap.normal = reader.read_vector3()
                model.attach_points.append(ap)

        elif chunk_id == CHUNK_NATH:
            n_normals = reader.read_int32()
            if len(model.attach_points) == n_normals:
                for i in range(n_normals):
                    model.attach_points[i].has_uvec = True
                    reader.read_int32()  # index
                    model.attach_points[i].uvec = reader.read_vector3()
                    # Also read the normal again (overwrite or skip)
                    _ = reader.read_vector3()

        else:
            # Unknown chunk, skip to end
            pass

        # Ensure we're at the end of this chunk
        reader.seek(chunk_end)

    # Build hierarchy
    model.build_hierarchy()

    # Remove None entries from submodels
    model.submodels = [sm for sm in model.submodels if sm is not None]

    return model


def write_pof(model: POFModel) -> bytes:
    """Serialize a POFModel to POF binary bytes."""
    stream = io.BytesIO()
    write_pof_stream(model, stream)
    return stream.getvalue()


def write_pof_stream(model: POFModel, stream: BinaryIO):
    """Write a POFModel to a binary stream."""
    writer = POFWriter(stream)

    # Header
    writer.write_bytes(POF_MAGIC)
    writer.write_int32(model.version)

    timed = model.major_version >= 22

    # OHDR chunk
    ohdr_buf = io.BytesIO()
    ohdr_w = POFWriter(ohdr_buf)
    ohdr_w.write_int32(len(model.submodels))
    ohdr_w.write_float(model.radius)
    ohdr_w.write_vector3(model.min_bound)
    ohdr_w.write_vector3(model.max_bound)
    ohdr_w.write_int32(1)  # 1 detail level
    ohdr_w.write_int32(0)  # detail level 0 = first submodel
    writer.write_chunk_header(CHUNK_OHDR, ohdr_buf.tell())
    writer.write_bytes(ohdr_buf.getvalue())

    # TXTR chunk
    txtr_buf = io.BytesIO()
    txtr_w = POFWriter(txtr_buf)
    txtr_w.write_int32(len(model.textures))
    for tex in model.textures:
        txtr_w.write_string(tex)
    writer.write_chunk_header(CHUNK_TXTR, txtr_buf.tell())
    writer.write_bytes(txtr_buf.getvalue())

    # SOBJ chunks
    for sm in model.submodels:
        sobj_buf = io.BytesIO()
        sobj_w = POFWriter(sobj_buf)
        sobj_w.write_int32(sm.index)
        sobj_w.write_int32(sm.parent)
        sobj_w.write_vector3(sm.normal)
        sobj_w.write_float(0.0)  # d
        sobj_w.write_vector3(sm.point)
        sobj_w.write_vector3(sm.offset)
        sobj_w.write_float(sm.radius)
        sobj_w.write_int32(sm.tree_offset)
        sobj_w.write_int32(sm.data_offset)
        if model.version > 1805:
            sobj_w.write_vector3(sm.geometric_center)
        sobj_w.write_string(sm.name)
        sobj_w.write_string(sm.props)
        sobj_w.write_int32(sm.movement_type)
        sobj_w.write_int32(sm.movement_axis)
        sobj_w.write_int32(0)  # no freespace chunks

        # Vertices
        sobj_w.write_int32(len(sm.vertices))
        for v in sm.vertices:
            sobj_w.write_vector3(v.position)
        for v in sm.vertices:
            sobj_w.write_vector3(v.normal)

        # Alpha per vertex (version >= 23)
        if model.major_version >= 23:
            for v in sm.vertices:
                sobj_w.write_float(v.alpha)

        # Faces
        sobj_w.write_int32(len(sm.faces))
        for face in sm.faces:
            sobj_w.write_vector3(face.normal)
            sobj_w.write_int32(len(face.vertices))
            if face.textured:
                sobj_w.write_int32(1)
                sobj_w.write_int32(face.texnum)
            else:
                sobj_w.write_int32(0)
                sobj_w.write_color_rgb(face.color)
            for fv in face.vertices:
                sobj_w.write_int32(fv.index)
                sobj_w.write_float(fv.u)
                sobj_w.write_float(fv.v)
            # Lightmap UV diffs (version >= 21)
            if model.major_version >= 21:
                sobj_w.write_float(0.0)
                sobj_w.write_float(0.0)

        writer.write_chunk_header(CHUNK_SOBJ, sobj_buf.tell())
        writer.write_bytes(sobj_buf.getvalue())

    # GPNT chunk
    if model.gun_banks:
        gpnt_buf = io.BytesIO()
        gpnt_w = POFWriter(gpnt_buf)
        gpnt_w.write_int32(len(model.gun_banks))
        for bank in model.gun_banks:
            if model.version >= 1908:
                gpnt_w.write_int32(bank.parent)
            gpnt_w.write_vector3(bank.point)
            gpnt_w.write_vector3(bank.normal)
        writer.write_chunk_header(CHUNK_GPNT, gpnt_buf.tell())
        writer.write_bytes(gpnt_buf.getvalue())

    # WBAT chunk
    if model.weapon_batteries:
        wbat_buf = io.BytesIO()
        wbat_w = POFWriter(wbat_buf)
        wbat_w.write_int32(len(model.weapon_batteries))
        for wb in model.weapon_batteries:
            wbat_w.write_int32(len(wb.gunpoints))
            for gp in wb.gunpoints:
                wbat_w.write_int32(gp)
            wbat_w.write_int32(len(wb.turrets))
            for t in wb.turrets:
                wbat_w.write_int32(t)
        writer.write_chunk_header(CHUNK_WBS, wbat_buf.tell())
        writer.write_bytes(wbat_buf.getvalue())

    # ANIM chunk (rotation animation)
    has_anim = any(sm.num_key_angles > 0 for sm in model.submodels)
    if has_anim:
        anim_buf = io.BytesIO()
        anim_w = POFWriter(anim_buf)
        if not timed:
            # Use max key angles across all submodels
            max_keys = max((sm.num_key_angles for sm in model.submodels), default=0)
            anim_w.write_int32(max_keys)
        for sm in model.submodels:
            if timed:
                anim_w.write_int32(sm.num_key_angles)
                anim_w.write_int32(sm.rot_track_min)
                anim_w.write_int32(sm.rot_track_max)
            for kf in sm.keyframes[:sm.num_key_angles]:
                if timed:
                    anim_w.write_int32(kf.rot_start_time)
                anim_w.write_vector3(kf.axis)
                anim_w.write_int32(kf.angle)
        writer.write_chunk_header(CHUNK_ANIM, anim_buf.tell())
        writer.write_bytes(anim_buf.getvalue())

    # PANI chunk (positional animation)
    has_pos_anim = any(sm.num_key_pos > 0 for sm in model.submodels)
    if has_pos_anim:
        pani_buf = io.BytesIO()
        pani_w = POFWriter(pani_buf)
        if not timed:
            max_keys = max((sm.num_key_pos for sm in model.submodels), default=0)
            pani_w.write_int32(max_keys)
        for sm in model.submodels:
            if timed:
                pani_w.write_int32(sm.num_key_pos)
                pani_w.write_int32(sm.pos_track_min)
                pani_w.write_int32(sm.pos_track_max)
            for kf in sm.keyframes[:sm.num_key_pos]:
                if timed:
                    pani_w.write_int32(kf.pos_start_time)
                pani_w.write_vector3(kf.position)
        writer.write_chunk_header(CHUNK_PANI, pani_buf.tell())
        writer.write_bytes(pani_buf.getvalue())

    # GRND chunk
    if model.ground_planes:
        grnd_buf = io.BytesIO()
        grnd_w = POFWriter(grnd_buf)
        grnd_w.write_int32(len(model.ground_planes))
        for gp in model.ground_planes:
            grnd_w.write_int32(gp.parent)
            grnd_w.write_vector3(gp.point)
            grnd_w.write_vector3(gp.normal)
        writer.write_chunk_header(CHUNK_GRND, grnd_buf.tell())
        writer.write_bytes(grnd_buf.getvalue())

    # ATCH chunk
    if model.attach_points:
        atch_buf = io.BytesIO()
        atch_w = POFWriter(atch_buf)
        atch_w.write_int32(len(model.attach_points))
        for ap in model.attach_points:
            atch_w.write_int32(ap.parent)
            atch_w.write_vector3(ap.point)
            atch_w.write_vector3(ap.normal)
        writer.write_chunk_header(CHUNK_ATCH, atch_buf.tell())
        writer.write_bytes(atch_buf.getvalue())

        # NATH chunk (attach normals)
        if any(ap.has_uvec for ap in model.attach_points):
            nath_buf = io.BytesIO()
            nath_w = POFWriter(nath_buf)
            nath_w.write_int32(len(model.attach_points))
            for i, ap in enumerate(model.attach_points):
                nath_w.write_int32(i)
                nath_w.write_vector3(ap.normal)
                nath_w.write_vector3(ap.uvec)
            writer.write_chunk_header(CHUNK_NATH, nath_buf.tell())
            writer.write_bytes(nath_buf.getvalue())
