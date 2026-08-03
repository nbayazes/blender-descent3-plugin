"""Descent 3 POF/OOF binary format parser and writer.

Parses and writes Descent 3 polygon model files. Based on the Descent 3 source
code and Inferno engine reference implementation, which are vendored at the
repository root as ``polymodel.cpp``, ``polymodel.h`` and
``polymodel_external.h``; comments here cite them by symbol name.

File format::

    Header: char[4] magic ("PSPO") + int32 version (major*100+minor)
    Chunks: char[4] id + int32 length + byte[] data

This module has no ``bpy`` dependency, so it can be exercised by the pytest
suite outside Blender.
"""

from __future__ import annotations

import io
import logging
import struct
from dataclasses import dataclass, field
from typing import BinaryIO

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POF_MAGIC = b"PSPO"

#: Oldest file version this parser accepts. Engine: ``PM_COMPATIBLE_VERSION``
#: (polymodel.h:305).
MIN_OBJFILE_VERSION = 1807

#: Version written for new files. Engine: ``PM_OBJFILE_VERSION``
#: (polymodel.h:306).
OBJFILE_VERSION = 2300

# ---------------------------------------------------------------------------
# Version gates
# ---------------------------------------------------------------------------
# A POF version is ``major * 100 + minor``. Several record layouts changed over
# the format's life, and both the reader and the writer have to branch on the
# same thresholds or the two disagree about how many bytes a record occupies.

#: Divisor of the ``major * 100 + minor`` encoding.
VERSION_MAJOR_SCALE = 100

#: A header value below this is a bare major version from a pre-release tool
#: and is scaled up by :data:`VERSION_MAJOR_SCALE` before use.
MAX_UNSCALED_VERSION = 18

#: Above this version each SOBJ record carries a ``geometric_center`` vector.
VERSION_GEOMETRIC_CENTER = 1805

#: At or above this version each GPNT gun bank is prefixed by its parent
#: submodel index.
VERSION_GUNPOINT_PARENT = 1908

#: Major version at which every face is followed by two lightmap UV-diff
#: floats.
MAJOR_VERSION_LIGHTMAP = 21

#: Major version at which animation becomes *timed*: ANIM/PANI store a
#: per-submodel key count and start times instead of one global frame count.
MAJOR_VERSION_TIMED_ANIM = 22

#: Major version at which a per-vertex alpha float array follows the vertex
#: normals.
MAJOR_VERSION_VERTEX_ALPHA = 23

# Chunk IDs (four-character codes as 32-bit ints, little-endian)
def _fcc(s: str) -> int:
    """Convert a four-character code to its little-endian 32-bit integer.

    Args:
        s: Exactly four ASCII characters, e.g. ``"SOBJ"``.

    Returns:
        The code reinterpreted as an unsigned 32-bit little-endian integer,
        which is how chunk IDs appear in the byte stream.
    """
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
# Field sentinels and limits
# ---------------------------------------------------------------------------

#: ``Submodel.parent`` value meaning "this is a root submodel".
NO_PARENT = -1

#: ``Submodel.movement_type`` value meaning the subobject does not move
#: (polymodel_external.h:157).
MOVEMENT_TYPE_NONE = -1

#: ``ModelFace.texnum`` value for an untextured face, which carries an RGB
#: triple instead of a texture index.
TEXNUM_NONE = -1

#: Index of the root submodel, which is also detail level 0.
ROOT_SUBMODEL_INDEX = 0

#: Fully opaque vertex alpha.
ALPHA_OPAQUE = 1.0

#: A vertex alpha below this marks the whole model :data:`PMF_ALPHA`. Exact
#: equality against 1.0 would flag models whose alpha survived a float round
#: trip a hair under opaque.
ALPHA_OPAQUE_THRESHOLD = 0.99

#: Untextured face colors are stored as three unsigned bytes and exposed as
#: 0..1 floats.
COLOR_CHANNEL_MAX = 255

#: Upper bound on a length-prefixed string before it is read. A corrupt or
#: misaligned length would otherwise drive an enormous allocation.
MAX_STRING_LENGTH = 10000

#: Largest ``$rotate=`` spin rate accepted. Values outside ``0 < rate <= 20``
#: leave :data:`SOF_ROTATE` unset, matching the engine's own range check.
MAX_SPIN_RATE = 20

#: A property string shorter than this cannot hold a command (``$`` plus at
#: least two letters).
MIN_PROPS_LENGTH = 3

#: Gunpoints kept per weapon battery; the engine's array is this wide, so extra
#: indices are read to stay aligned with the stream and then discarded.
MAX_WB_GUNPOINTS = 8

#: Turrets kept per weapon battery -- a separate engine array of the same size.
MAX_WB_TURRETS = 8

#: Magnitude below which a vector is treated as degenerate and normalizes to
#: zero rather than dividing by something near zero.
NORMALIZE_EPSILON = 1e-10

#: Axis of the neutral key used to pad an empty animation track. Kept as a
#: plain tuple so each *call* builds its own :class:`Vector3`; a module-level
#: ``Vector3`` would be shared by every model padded in the session, where a
#: single mutation would corrupt all of them. Within one call the padding keys
#: still alias one another, exactly as before this was named. The value is
#: arbitrary -- the key it belongs to has a zero angle, so any unit axis
#: produces the same pose.
DEFAULT_KEYFRAME_AXIS = (0.0, 0.0, 1.0)

# ---------------------------------------------------------------------------
# Submodel property commands
# ---------------------------------------------------------------------------

#: ``$rotate=`` carries a spin rate that has to pass a range check before
#: :data:`SOF_ROTATE` is set, so it is handled separately from the table below.
PROP_ROTATE = "$rotate="

#: Property command -> ``SOF_*`` flag it sets, for every command whose flag
#: depends only on the command being present. Keys are matched against the
#: lower-cased command by equality. Commands that take a payload keep their
#: trailing ``=`` because the command is split at the first ``=``; the payload
#: itself is only consumed for ``$rotate=``, since nothing here needs the
#: glow/thruster colors or the turret field of view.
#:
#: The engine's own chain (``SetPolymodelProperties``, polymodel.cpp:1003-1228)
#: is mostly ``stricmp``, but uses bounded ``strnicmp`` prefix matches for
#: ``$jitter`` (:1048), ``$shell`` (:1055), ``$facing`` (:1062) and
#: ``$frontface`` (:1068), so it would also accept e.g. ``$shellfoo``. This
#: table deliberately preserves the add-on's existing exact-match behaviour
#: rather than adopting the engine's; changing it is a behaviour change, not a
#: refactor.
PROPERTY_COMMAND_FLAGS = {
    "$jitter": SOF_JITTER,
    "$shell": SOF_SHELL,
    "$facing": SOF_FACING,
    "$frontface": SOF_FRONTFACE,
    "$glow=": SOF_GLOW,
    "$thruster=": SOF_THRUSTER,
    "$fov=": SOF_TURRET,
    "$monitor01": SOF_MONITOR1,
    "$monitor02": SOF_MONITOR2,
    "$monitor03": SOF_MONITOR3,
    "$monitor04": SOF_MONITOR4,
    "$monitor05": SOF_MONITOR5,
    "$monitor06": SOF_MONITOR6,
    "$monitor07": SOF_MONITOR7,
    "$monitor08": SOF_MONITOR8,
    "$viewer": SOF_VIEWER,
    "$layer": SOF_LAYER,
    "$custom": SOF_CUSTOM,
}

# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class Vector3:
    """A three-component vector, matching the engine's ``vector`` type.

    Deliberately not ``mathutils.Vector``: this module has to stay importable
    outside Blender so the parser can be unit-tested.

    Attributes:
        x: First component.
        y: Second component.
        z: Third component.
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def as_tuple(self) -> tuple[float, float, float]:
        """Return the components as a plain ``(x, y, z)`` tuple."""
        return (self.x, self.y, self.z)

    def __add__(self, other: Vector3) -> Vector3:
        """Return the component-wise sum of this vector and ``other``."""
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vector3) -> Vector3:
        """Return the component-wise difference of this vector and ``other``."""
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, s: float) -> Vector3:
        """Return this vector scaled by ``s``."""
        return Vector3(self.x * s, self.y * s, self.z * s)

    def magnitude(self) -> float:
        """Return the Euclidean length of this vector."""
        return (self.x**2 + self.y**2 + self.z**2) ** 0.5

    def normalized(self) -> Vector3:
        """Return a unit-length copy of this vector.

        Returns:
            A vector of length 1 in the same direction, or a zero vector if this
            one is shorter than :data:`NORMALIZE_EPSILON`. Degenerate input is
            common in real models, so it yields zero rather than raising.
        """
        m = self.magnitude()
        if m < NORMALIZE_EPSILON:
            return Vector3(0, 0, 0)
        return Vector3(self.x / m, self.y / m, self.z / m)


@dataclass
class Color:
    """An RGB color with components in the 0..1 range.

    Attributes:
        r: Red component.
        g: Green component.
        b: Blue component.
    """

    r: float = 1.0
    g: float = 1.0
    b: float = 1.0


@dataclass
class FaceVertex:
    """One corner of a face: a vertex reference plus that corner's UV.

    UVs live per face-corner rather than per vertex, so a vertex shared by
    several faces can carry a different UV in each.

    Attributes:
        index: Index into the owning submodel's vertex list.
        u: Horizontal texture coordinate.
        v: Vertical texture coordinate, with the origin at the top-left.
    """

    index: int = 0
    u: float = 0.0
    v: float = 0.0


@dataclass
class ModelFace:
    """A single polygon.

    Attributes:
        normal: Face normal as stored in the file.
        vertices: Corners in winding order.
        textured: Whether the face uses ``texnum``; if false it uses ``color``.
        texnum: Index into :attr:`POFModel.textures`, or :data:`TEXNUM_NONE`.
        color: Flat color used when ``textured`` is false.
    """

    normal: Vector3 = field(default_factory=Vector3)
    vertices: list[FaceVertex] = field(default_factory=list)
    textured: bool = True
    texnum: int = TEXNUM_NONE
    color: Color = field(default_factory=Color)


@dataclass
class SubmodelVertex:
    """A vertex with its normal and opacity.

    Attributes:
        position: Position in the submodel's local space.
        normal: Vertex normal.
        alpha: Opacity. Only present in the file from
            :data:`MAJOR_VERSION_VERTEX_ALPHA`; older files load fully opaque.
    """

    position: Vector3 = field(default_factory=Vector3)
    normal: Vector3 = field(default_factory=Vector3)
    alpha: float = ALPHA_OPAQUE


@dataclass
class Keyframe:
    """One animation key, holding both the rotation and position tracks.

    The file stores rotation (ANIM/RANI) and position (PANI) in separate chunks
    that may differ in length, but both are merged into this one list; see
    :func:`_padded_keyframes` for how a short track is reconciled.

    Attributes:
        axis: Rotation axis, normalized on load.
        angle: Rotation angle in the engine's fixed-point angle units.
        position: Translation for this key.
        rot_start_time: Start time of the rotation key, timed formats only.
        pos_start_time: Start time of the position key, timed formats only.
    """

    axis: Vector3 = field(default_factory=Vector3)
    angle: int = 0
    position: Vector3 = field(default_factory=Vector3)
    rot_start_time: int = 0
    pos_start_time: int = 0


@dataclass
class Submodel:
    """One SOBJ subobject: a named mesh plus its place in the hierarchy.

    Mirrors the engine's ``bsp_info`` (polymodel_external.h:155-219). The
    animation fields are populated from the separate ANIM/RANI and PANI chunks
    rather than from the SOBJ record itself.

    Attributes:
        index: This submodel's own index, as written in the file.
        parent: Parent's index, or :data:`NO_PARENT` for a root submodel.
        normal: Separation-plane normal. Read but unused; kept for round trips.
        point: Separation-plane point. Read but unused; kept for round trips.
        offset: Translation from the parent submodel.
        radius: Bounding radius about the submodel's own origin.
        tree_offset: Offset of the BSP tree data. Not interpreted here.
        data_offset: Offset of the render data. Not interpreted here.
        geometric_center: Center of the geometry, present above
            :data:`VERSION_GEOMETRIC_CENTER`.
        name: Submodel name, which Blender uses as the object name.
        props: Raw ``$``-command property string, preserved verbatim.
        movement_type: How the subobject moves, or :data:`MOVEMENT_TYPE_NONE`.
        movement_axis: Which axis it moves or rotates about.
        flags: ``SOF_*`` bits derived from ``props`` by
            :func:`_parse_submodel_flags`.
        vertices: Vertex list in local space.
        faces: Polygon list referencing ``vertices`` by index.
        num_key_angles: Rotation key count.
        num_key_pos: Position key count.
        rot_track_min: First frame of the rotation track, timed formats only.
        rot_track_max: Last frame of the rotation track, timed formats only.
        pos_track_min: First frame of the position track, timed formats only.
        pos_track_max: Last frame of the position track, timed formats only.
        keyframes: Merged rotation and position keys.
        children: Indices of child submodels. Computed by
            :meth:`POFModel.build_hierarchy`, not stored in the file.
    """

    index: int = ROOT_SUBMODEL_INDEX
    parent: int = NO_PARENT
    normal: Vector3 = field(default_factory=Vector3)
    point: Vector3 = field(default_factory=Vector3)
    offset: Vector3 = field(default_factory=Vector3)
    radius: float = 0.0
    tree_offset: int = 0
    data_offset: int = 0
    geometric_center: Vector3 = field(default_factory=Vector3)
    name: str = ""
    props: str = ""
    movement_type: int = MOVEMENT_TYPE_NONE
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
    """A firing position, from GPNT. Ground planes (GRND) reuse this shape.

    Attributes:
        parent: Submodel the point is attached to. Only stored in the file from
            :data:`VERSION_GUNPOINT_PARENT`; older files load it as 0.
        point: Position in the parent submodel's space.
        normal: Firing direction.
    """

    parent: int = 0
    point: Vector3 = field(default_factory=Vector3)
    normal: Vector3 = field(default_factory=Vector3)


@dataclass
class WeaponBattery:
    """A WBAT battery grouping gunpoints with the turrets that aim them.

    Attributes:
        gunpoints: Gun bank indices, capped at :data:`MAX_WB_GUNPOINTS`.
        turrets: Turret submodel indices, capped at :data:`MAX_WB_TURRETS`.
    """

    gunpoints: list[int] = field(default_factory=list)
    turrets: list[int] = field(default_factory=list)


@dataclass
class AttachPoint:
    """A mounting point where another model can be joined to this one.

    Attributes:
        parent: Submodel the point is attached to.
        point: Position in the parent submodel's space.
        normal: Facing direction, from the ATCH chunk.
        has_uvec: Whether an up vector was supplied by a NATH chunk. Without
            one the attached model's roll about ``normal`` is undefined.
        uvec: Up vector, valid only when ``has_uvec`` is true.
    """

    parent: int = 0
    point: Vector3 = field(default_factory=Vector3)
    normal: Vector3 = field(default_factory=Vector3)
    has_uvec: bool = False
    uvec: Vector3 = field(default_factory=Vector3)


@dataclass
class POFModel:
    """A whole parsed model: header, textures, submodels and attachment data.

    Attributes:
        version: File version as ``major * 100 + minor``.
        major_version: ``version // VERSION_MAJOR_SCALE``, cached because every
            record-layout gate is expressed against the major version.
        radius: Bounding radius of the whole model.
        min_bound: Minimum corner of the bounding box.
        max_bound: Maximum corner of the bounding box.
        flags: ``PMF_*`` bits describing which optional data the model carries.
        frame_min: Earliest animation frame across all submodels.
        frame_max: Latest animation frame across all submodels.
        textures: Texture names, indexed by :attr:`ModelFace.texnum`.
        submodels: Submodels in file order.
        gun_banks: GPNT firing positions.
        weapon_batteries: WBAT batteries.
        ground_planes: GRND planes, which reuse the :class:`GunBank` shape.
        attach_points: ATCH mounting points.
    """

    version: int = OBJFILE_VERSION
    major_version: int = OBJFILE_VERSION // VERSION_MAJOR_SCALE
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

    def build_hierarchy(self) -> None:
        """Rebuild every submodel's ``children`` list from its ``parent`` index.

        Safe to call repeatedly: each list is cleared before being refilled.

        Note:
            ``children`` holds submodel *indices* (``sm.index``), not positions
            in ``self.submodels``. SOBJ chunks can arrive out of order or with
            gaps, so the two are not interchangeable, and callers look parents
            up by index.
        """
        by_index = {sm.index: sm for sm in self.submodels}
        for sm in self.submodels:
            sm.children = []
        for sm in self.submodels:
            parent = by_index.get(sm.parent)  # sm.parent == -1 (root) -> None
            if parent is not None:
                parent.children.append(sm.index)

# ---------------------------------------------------------------------------
# Binary Reader
# ---------------------------------------------------------------------------

class POFReader:
    """Reads little-endian POF primitives from a binary stream.

    Every read is bounds-checked through :meth:`read_bytes`, so a truncated or
    misaligned file raises :class:`EOFError` at the point of the short read
    rather than silently returning garbage.

    Attributes:
        stream: The underlying binary stream, positioned at the next read.
    """

    def __init__(self, stream: BinaryIO) -> None:
        """Wrap ``stream`` for reading.

        Args:
            stream: A readable, seekable binary stream.
        """
        self.stream = stream

    def read_bytes(self, n: int) -> bytes:
        """Read exactly ``n`` bytes.

        Args:
            n: Number of bytes to read.

        Returns:
            Exactly ``n`` bytes.

        Raises:
            EOFError: If fewer than ``n`` bytes remain.
        """
        data = self.stream.read(n)
        if len(data) < n:
            raise EOFError(f"Expected {n} bytes, got {len(data)}")
        return data

    def read_int32(self) -> int:
        """Read a signed 32-bit integer."""
        return struct.unpack("<i", self.read_bytes(4))[0]

    def read_uint32(self) -> int:
        """Read an unsigned 32-bit integer."""
        return struct.unpack("<I", self.read_bytes(4))[0]

    def read_uint16(self) -> int:
        """Read an unsigned 16-bit integer."""
        return struct.unpack("<H", self.read_bytes(2))[0]

    def read_uint8(self) -> int:
        """Read a single unsigned byte."""
        return struct.unpack("<B", self.read_bytes(1))[0]

    def read_float(self) -> float:
        """Read a 32-bit float."""
        return struct.unpack("<f", self.read_bytes(4))[0]

    def read_vector3(self) -> Vector3:
        """Read three consecutive floats as a :class:`Vector3`."""
        x, y, z = struct.unpack("<fff", self.read_bytes(12))
        return Vector3(x, y, z)

    def read_string(self) -> str:
        """Read a length-prefixed string.

        Writers disagree about whether the stored length counts a trailing NUL,
        so the terminator is stripped after decoding rather than assumed. Bytes
        that are not valid ASCII are substituted, matching the tolerance in
        :meth:`POFWriter.write_string`.

        Returns:
            The decoded string, without any trailing NUL.

        Raises:
            ValueError: If the length prefix is negative or exceeds
                :data:`MAX_STRING_LENGTH`, meaning the stream is corrupt or
                misaligned.
        """
        length = self.read_int32()
        if length < 0 or length > MAX_STRING_LENGTH:
            raise ValueError(f"Invalid string length: {length}")
        data = self.read_bytes(length)
        s = data.decode("ascii", errors="replace")
        return s.rstrip("\x00")

    def read_color_rgb(self) -> Color:
        """Read three bytes as a :class:`Color` with 0..1 components."""
        r = self.read_uint8()
        g = self.read_uint8()
        b = self.read_uint8()
        return Color(
            r / COLOR_CHANNEL_MAX, g / COLOR_CHANNEL_MAX, b / COLOR_CHANNEL_MAX
        )

    def read_chunk_header(self) -> tuple[int, int]:
        """Read a chunk header.

        Returns:
            A ``(chunk_id, data_length)`` pair. The length excludes the eight
            header bytes just consumed.
        """
        chunk_id = self.read_uint32()
        length = self.read_int32()
        return chunk_id, length

    def skip(self, n: int) -> None:
        """Advance the stream by ``n`` bytes, relative to the current position."""
        self.stream.seek(n, 1)

    def tell(self) -> int:
        """Return the current absolute stream position."""
        return self.stream.tell()

    def seek(self, pos: int) -> None:
        """Move to absolute stream position ``pos``."""
        self.stream.seek(pos)

# ---------------------------------------------------------------------------
# Binary Writer
# ---------------------------------------------------------------------------

class POFWriter:
    """Writes little-endian POF primitives to a binary stream.

    Chunk bodies are built in a scratch :class:`io.BytesIO` and only then
    written with their length prefix, because a chunk header has to state the
    body size before the body exists.

    Attributes:
        stream: The underlying binary stream, positioned at the next write.
    """

    def __init__(self, stream: BinaryIO) -> None:
        """Wrap ``stream`` for writing.

        Args:
            stream: A writable, seekable binary stream.
        """
        self.stream = stream

    def write_bytes(self, data: bytes) -> None:
        """Write ``data`` verbatim."""
        self.stream.write(data)

    def write_int32(self, value: int) -> None:
        """Write ``value`` as a signed 32-bit integer."""
        self.stream.write(struct.pack("<i", value))

    def write_uint32(self, value: int) -> None:
        """Write ``value`` as an unsigned 32-bit integer."""
        self.stream.write(struct.pack("<I", value))

    def write_uint16(self, value: int) -> None:
        """Write ``value`` as an unsigned 16-bit integer."""
        self.stream.write(struct.pack("<H", value))

    def write_uint8(self, value: int) -> None:
        """Write ``value`` as a single unsigned byte."""
        self.stream.write(struct.pack("<B", value))

    def write_float(self, value: float) -> None:
        """Write ``value`` as a 32-bit float."""
        self.stream.write(struct.pack("<f", value))

    def write_vector3(self, v: Vector3) -> None:
        """Write ``v`` as three consecutive floats."""
        self.stream.write(struct.pack("<fff", v.x, v.y, v.z))

    def write_string(self, s: str) -> None:
        """Write a length-prefixed, NUL-terminated string.

        The format is 8-bit ASCII but Blender object and material names are
        UTF-8, so unrepresentable characters are substituted rather than
        raising: losing an accent beats failing the whole export. The reader
        decodes with the same tolerance.
        """
        try:
            raw = s.encode("ascii")
        except UnicodeEncodeError:
            raw = s.encode("ascii", errors="replace")
            log.warning(
                "Non-ASCII characters in %r are not representable in POF; "
                "wrote %r instead", s, raw.decode("ascii")
            )
        encoded = raw + b"\x00"
        self.write_int32(len(encoded))
        self.write_bytes(encoded)

    def write_color_rgb(self, c: Color) -> None:
        """Write ``c`` as three bytes, scaled from 0..1 and clamped to a byte."""
        self.write_uint8(int(c.r * COLOR_CHANNEL_MAX) & 0xFF)
        self.write_uint8(int(c.g * COLOR_CHANNEL_MAX) & 0xFF)
        self.write_uint8(int(c.b * COLOR_CHANNEL_MAX) & 0xFF)

    def write_chunk_header(self, chunk_id: int, length: int) -> None:
        """Write a chunk header.

        Args:
            chunk_id: Four-character code as produced by :func:`_fcc`.
            length: Size of the chunk body, excluding these eight bytes.
        """
        self.write_uint32(chunk_id)
        self.write_int32(length)

    def tell(self) -> int:
        """Return the current absolute stream position."""
        return self.stream.tell()

    def seek(self, pos: int) -> None:
        """Move to absolute stream position ``pos``."""
        self.stream.seek(pos)

# ---------------------------------------------------------------------------
# Parsing Functions
# ---------------------------------------------------------------------------

def _parse_subobj(reader: POFReader, model: POFModel, chunk_end: int) -> None:
    """Parse one SOBJ chunk and store the submodel on ``model``.

    Args:
        reader: Reader positioned at the start of the chunk body.
        model: Model to append to. Its ``version`` and ``major_version`` must
            already be set, because they select the record layout.
        chunk_end: Absolute offset of the end of this chunk. The caller seeks
            here afterwards, so trailing fields this parser ignores are skipped
            rather than mistaken for the next chunk.

    Note:
        The submodel is stored at ``model.submodels[sm.index]``, growing the
        list with ``None`` placeholders as needed, because SOBJ chunks can
        arrive out of order. :func:`parse_pof_stream` drops any placeholder that
        is still unfilled once every chunk has been read.
    """
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

    if model.version > VERSION_GEOMETRIC_CENTER:
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
    if model.major_version >= MAJOR_VERSION_VERTEX_ALPHA:
        for i in range(n_verts):
            sm.vertices[i].alpha = reader.read_float()
            if sm.vertices[i].alpha < ALPHA_OPAQUE_THRESHOLD:
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
        if model.major_version >= MAJOR_VERSION_LIGHTMAP:
            reader.read_float()  # xdiff
            reader.read_float()  # ydiff

        sm.faces.append(face)

    # Parse properties for flags
    _parse_submodel_flags(sm)

    # Ensure index is within bounds
    while len(model.submodels) <= sm.index:
        model.submodels.append(None)
    model.submodels[sm.index] = sm


def _parse_submodel_flags(sm: Submodel) -> None:
    """Set ``sm.flags`` from the ``$``-command in the submodel's property string.

    Only the first command is honoured, matching the engine: the string is split
    at the first ``=`` into a command (including that ``=``) and its payload, and
    the command is looked up case-insensitively. Everything except ``$rotate=``
    is a straight command-to-flag mapping held in :data:`PROPERTY_COMMAND_FLAGS`.

    Args:
        sm: Submodel whose ``props`` is read and whose ``flags`` is updated
            in place. Unrecognised commands leave ``flags`` untouched -- the raw
            string is still preserved, so a command this add-on does not model
            survives a round trip.
    """
    props = sm.props.strip()
    if len(props) < MIN_PROPS_LENGTH:
        return

    eq_pos = props.find("=")
    if eq_pos >= 0:
        command = props[:eq_pos + 1].lower().strip()
        data = props[eq_pos + 1:].strip()
    else:
        command = props.lower().strip()
        data = ""

    if command == PROP_ROTATE:
        try:
            spin_rate = float(data)
        except ValueError:
            log.warning(
                "Submodel '%s': %s expects a number, got %r",
                sm.name, PROP_ROTATE, data,
            )
            return
        if 0 < spin_rate <= MAX_SPIN_RATE:
            sm.flags |= SOF_ROTATE
        return

    flag = PROPERTY_COMMAND_FLAGS.get(command)
    if flag is not None:
        sm.flags |= flag


# ---------------------------------------------------------------------------
# Main Parse / Write Functions
# ---------------------------------------------------------------------------

def parse_pof(data: bytes) -> POFModel:
    """Parse a POF file held in memory.

    Args:
        data: Complete contents of a ``.pof`` or ``.oof`` file.

    Returns:
        The parsed model, with its hierarchy already built.

    Raises:
        ValueError: If the magic bytes or version are not acceptable.
        EOFError: If the data is truncated.
    """
    stream = io.BytesIO(data)
    return parse_pof_stream(stream)


def parse_pof_stream(stream: BinaryIO) -> POFModel:
    """Parse a POF file from a binary stream.

    Chunks are dispatched by ID; an unrecognised chunk is skipped by seeking to
    the end offset its own header declares, so an unfamiliar chunk costs data
    but does not desynchronise the stream.

    Args:
        stream: Readable, seekable stream positioned at the file header.

    Returns:
        The parsed model, with its hierarchy already built.

    Raises:
        ValueError: If the magic bytes do not match :data:`POF_MAGIC`, or the
            version falls outside :data:`MIN_OBJFILE_VERSION` ..
            :data:`OBJFILE_VERSION`.
        EOFError: If a chunk runs past the end of the stream.
    """
    reader = POFReader(stream)
    model = POFModel()

    # Read header
    magic = reader.read_bytes(4)
    if magic != POF_MAGIC:
        raise ValueError(f"Invalid POF magic: expected {POF_MAGIC!r}, got {magic!r}")

    version = reader.read_int32()
    if version < MAX_UNSCALED_VERSION:
        version *= VERSION_MAJOR_SCALE  # a bare major version from an old tool

    if version < MIN_OBJFILE_VERSION or version > OBJFILE_VERSION:
        raise ValueError(f"Unsupported POF version: {version}")

    model.version = version
    model.major_version = version // VERSION_MAJOR_SCALE

    if model.major_version >= MAJOR_VERSION_LIGHTMAP:
        model.flags |= PMF_LIGHTMAP_RES
    if model.major_version >= MAJOR_VERSION_TIMED_ANIM:
        model.flags |= PMF_TIMED

    # Read chunks
    timed = model.major_version >= MAJOR_VERSION_TIMED_ANIM

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
                if model.version >= VERSION_GUNPOINT_PARENT:
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
                    if j < MAX_WB_GUNPOINTS:
                        wb.gunpoints.append(gp)
                n_turrets = reader.read_int32()
                for j in range(n_turrets):
                    t = reader.read_int32()
                    if j < MAX_WB_TURRETS:
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

                # Read exactly num_key_pos entries. A submodel can carry more
                # position keys than rotation keys, so the existing keyframe
                # list (built from ANIM) is extended rather than iterated.
                for k in range(sm.num_key_pos):
                    while len(sm.keyframes) <= k:
                        sm.keyframes.append(Keyframe())
                    kf = sm.keyframes[k]
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
                    # Layout is index, normal, uvec -- the normal repeats what
                    # ATCH already gave us and is discarded (polymodel.cpp,
                    # ID_ATTACH_NORMALS). Reading these two the other way round
                    # silently rotates anything attached to the model.
                    reader.read_vector3()  # normal (already read from ATCH)
                    model.attach_points[i].uvec = reader.read_vector3()
            else:
                log.warning(
                    "Ignoring ATTACH normals: %d attach points but %d normals",
                    len(model.attach_points), n_normals,
                )

        else:
            # Unknown chunk, skip to end
            pass

        # Ensure we're at the end of this chunk
        reader.seek(chunk_end)

    # Drop the placeholders left where a SOBJ index was never filled in. This
    # has to happen before build_hierarchy, which cannot walk a list of Nones.
    missing = [i for i, sm in enumerate(model.submodels) if sm is None]
    if missing:
        log.warning(
            "Model declares submodel indices with no SOBJ chunk: %s", missing
        )
        model.submodels = [sm for sm in model.submodels if sm is not None]

    model.build_hierarchy()

    return model


def _padded_keyframes(keyframes: list[Keyframe], count: int) -> list[Keyframe]:
    """Return exactly ``count`` keyframes, padding a short track if needed.

    Pre-v22 files store one global key count that applies to every submodel
    (``ID_ANIM`` in polymodel.cpp), so a submodel with a shorter track has to be
    padded out or the reader runs off the end of the chunk. Repeating the last
    key holds the final pose; an empty track pads with a neutral key.

    Args:
        keyframes: Track to pad. A longer track is truncated to ``count``.
        count: Exact number of keys the caller must write.

    Returns:
        A list of exactly ``count`` keyframes.
    """
    keys = list(keyframes[:count])
    if len(keys) < count:
        filler = keys[-1] if keys else Keyframe(axis=Vector3(*DEFAULT_KEYFRAME_AXIS))
        keys.extend([filler] * (count - len(keys)))
    return keys


def write_pof(model: POFModel) -> bytes:
    """Serialize a model to POF bytes.

    Args:
        model: Model to serialize. Its ``version`` selects the record layout.

    Returns:
        The complete file contents.
    """
    stream = io.BytesIO()
    write_pof_stream(model, stream)
    return stream.getvalue()


def write_pof_stream(model: POFModel, stream: BinaryIO) -> None:
    """Write a model to a binary stream.

    Optional chunks are emitted only when the model carries the corresponding
    data, so a model with no guns or attach points produces no GPNT or ATCH
    chunk rather than an empty one.

    Args:
        model: Model to serialize. Its ``version`` and ``major_version`` select
            which version-gated fields are written; they must agree with each
            other or the result will not parse back.
        stream: Writable binary stream.
    """
    writer = POFWriter(stream)

    # Header
    writer.write_bytes(POF_MAGIC)
    writer.write_int32(model.version)

    timed = model.major_version >= MAJOR_VERSION_TIMED_ANIM

    # OHDR chunk
    ohdr_buf = io.BytesIO()
    ohdr_w = POFWriter(ohdr_buf)
    ohdr_w.write_int32(len(model.submodels))
    ohdr_w.write_float(model.radius)
    ohdr_w.write_vector3(model.min_bound)
    ohdr_w.write_vector3(model.max_bound)
    ohdr_w.write_int32(1)  # 1 detail level
    ohdr_w.write_int32(ROOT_SUBMODEL_INDEX)  # detail level 0 = root submodel
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
        if model.version > VERSION_GEOMETRIC_CENTER:
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
        if model.major_version >= MAJOR_VERSION_VERTEX_ALPHA:
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
            if model.major_version >= MAJOR_VERSION_LIGHTMAP:
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
            if model.version >= VERSION_GUNPOINT_PARENT:
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
        max_keys = max((sm.num_key_angles for sm in model.submodels), default=0)
        if not timed:
            # One count for the whole model: every submodel must emit max_keys.
            anim_w.write_int32(max_keys)
        for sm in model.submodels:
            if timed:
                anim_w.write_int32(sm.num_key_angles)
                anim_w.write_int32(sm.rot_track_min)
                anim_w.write_int32(sm.rot_track_max)
                keys = _padded_keyframes(sm.keyframes, sm.num_key_angles)
            else:
                keys = _padded_keyframes(sm.keyframes, max_keys)
            for kf in keys:
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
        max_keys = max((sm.num_key_pos for sm in model.submodels), default=0)
        if not timed:
            pani_w.write_int32(max_keys)
        for sm in model.submodels:
            if timed:
                pani_w.write_int32(sm.num_key_pos)
                pani_w.write_int32(sm.pos_track_min)
                pani_w.write_int32(sm.pos_track_max)
                keys = _padded_keyframes(sm.keyframes, sm.num_key_pos)
            else:
                keys = _padded_keyframes(sm.keyframes, max_keys)
            for kf in keys:
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
