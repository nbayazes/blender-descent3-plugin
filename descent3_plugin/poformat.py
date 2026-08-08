"""Descent 3 POF/OOF binary format parser and writer.

Parses and writes Descent 3 polygon model files. Based on the Descent 3 source
code and Inferno engine reference implementation, vendored under ``reference/``
as ``polymodel.cpp``, ``polymodel.h`` and ``polymodel_external.h``; the
file:line citations throughout this module are relative to that folder.

File format::

    Header: char[4] magic ("PSPO") + int32 version (major*100+minor)
    Chunks: char[4] id + int32 length + byte[] data

This module has no ``bpy`` dependency, so it can be exercised by the pytest
suite outside Blender. The vector type it builds on lives in :mod:`mathutil`.
"""

from __future__ import annotations

import io
import logging
import struct
from dataclasses import dataclass, field
from typing import BinaryIO

from .mathutil import Vector3

log = logging.getLogger(__package__)

POF_MAGIC = b"PSPO"

#: Oldest file version this parser accepts. Engine: ``PM_COMPATIBLE_VERSION``
#: (polymodel.h:305).
MIN_OBJFILE_VERSION = 1807

#: Version written for new files. Engine: ``PM_OBJFILE_VERSION``
#: (polymodel.h:306).
OBJFILE_VERSION = 2300

#: A POF version is ``major * 100 + minor``. Record layouts changed over the
#: format's life, so reader and writer must branch on the same thresholds or
#: they disagree about how many bytes a record occupies.
VERSION_MAJOR_SCALE = 100

#: A header value below this is a bare major version from a pre-release tool and
#: is scaled up by :data:`VERSION_MAJOR_SCALE`. Unreachable in practice: the
#: largest result, 1700, then fails the :data:`MIN_OBJFILE_VERSION` check. Kept
#: because the engine is dead here identically -- ``ReadNewModelFile``
#: (reference/polymodel.cpp:1288) rescales and then refuses anything under 1807.
#: To load pre-1807 files, lower ``MIN_OBJFILE_VERSION``, not this one: the
#: parser deliberately accepts exactly what the engine accepts.
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


@dataclass(frozen=True)
class VersionFeatures:
    """Which optional fields a given POF version's record layout carries.

    Reader and writer both derive their answer from :func:`features_for`, so a
    threshold can only be changed in one place; disagreeing about one of these
    corrupts everything after the record.

    Attributes:
        geometric_center: SOBJ carries a ``geometric_center`` vector.
        gunpoint_parent: GPNT entries are prefixed by a parent submodel index.
        lightmap_uv: Each face is followed by two lightmap UV-diff floats.
        timed_anim: ANIM/PANI store a per-submodel key count and start times
            instead of one global frame count.
        vertex_alpha: A per-vertex alpha float array follows the normals.
    """

    geometric_center: bool
    gunpoint_parent: bool
    lightmap_uv: bool
    timed_anim: bool
    vertex_alpha: bool


def features_for(version: int, major_version: int | None = None) -> VersionFeatures:
    """Return the record-layout features of a POF version.

    Args:
        version: Full ``major * 100 + minor`` version.
        major_version: Defaults to ``version // VERSION_MAJOR_SCALE``. Separate
            because :class:`POFModel` stores both and a caller can set them
            independently.

    Returns:
        The feature set for that version.

    Note:
        The comparisons are deliberately not uniform: ``geometric_center`` is
        *strictly* greater than its threshold, and it and ``gunpoint_parent``
        test the full version while the rest test the major version. This
        mirrors the engine.
    """
    if major_version is None:
        major_version = version // VERSION_MAJOR_SCALE
    return VersionFeatures(
        geometric_center=version > VERSION_GEOMETRIC_CENTER,
        gunpoint_parent=version >= VERSION_GUNPOINT_PARENT,
        lightmap_uv=major_version >= MAJOR_VERSION_LIGHTMAP,
        timed_anim=major_version >= MAJOR_VERSION_TIMED_ANIM,
        vertex_alpha=major_version >= MAJOR_VERSION_VERTEX_ALPHA,
    )

def _fcc(s: str) -> int:
    """Convert a four-character code to its little-endian 32-bit integer.

    Args:
        s: Exactly four ASCII characters, e.g. ``"SOBJ"``.

    Returns:
        The code as an unsigned 32-bit little-endian int, which is how chunk
        IDs appear in the byte stream.
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

#: Bytes in the smallest field a POF record is built from -- an ``int32``, a
#: float, or one vector component. Element counts are bounds-checked against
#: the remaining bytes in multiples of this, so a call site states a record's
#: minimum size in fields without repeating the layout.
SIZEOF_FIELD = 4

#: Highest index a SOBJ record may claim. ``model.submodels`` is grown with
#: ``None`` placeholders up to whatever index a record names, so an unchecked
#: index lets the file allocate ``2**31 - 1`` list slots. Deliberately looser
#: than the engine's ``MAX_SUBOBJECTS`` of 30 (polymodel_external.h:76, refused
#: at polymodel.cpp:2044) so an over-built model can still be opened and cut
#: down in Blender.
MAX_SUBMODEL_INDEX = 4095

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

#: Axis of the neutral key used to pad an empty animation track. A plain tuple
#: so each call builds its own :class:`Vector3`; a module-level ``Vector3``
#: would be shared by every model padded in the session, where one mutation
#: corrupts all of them. That only fixes sharing *across* calls: within one
#: call the padding repeats a single filler, so every padded key in a track is
#: still the same object. The value is arbitrary -- the key has a zero angle,
#: so any unit axis gives the same pose.
DEFAULT_KEYFRAME_AXIS = (0.0, 0.0, 1.0)

#: ``$rotate=`` carries a spin rate that has to pass a range check before
#: :data:`SOF_ROTATE` is set, so it is handled separately from the table below.
PROP_ROTATE = "$rotate="

#: Property command -> ``SOF_*`` flag, for commands whose flag depends only on
#: the command being present. Matched by equality against the lower-cased
#: command; payload-taking commands keep their trailing ``=`` because the string
#: is split at the first ``=``. Only ``$rotate=`` consumes its payload; nothing
#: here needs the glow/thruster colours or the turret field of view.
#:
#: The engine (``SetPolymodelProperties``, polymodel.cpp:1003-1228) uses bounded
#: ``strnicmp`` prefix matches for ``$jitter``, ``$shell``, ``$facing`` and
#: ``$frontface``, so it also accepts e.g. ``$shellfoo``. Exact matching here is
#: deliberate; loosening it is a behaviour change, not a refactor.
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


@dataclass
class Color:
    """An RGB color with components in the 0..1 range."""

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

    @property
    def features(self) -> VersionFeatures:
        """Record-layout features implied by this model's version fields."""
        return features_for(self.version, self.major_version)

    def build_hierarchy(self) -> None:
        """Rebuild every submodel's ``children`` list from its ``parent`` index.

        Safe to call repeatedly: each list is cleared before being refilled.

        Note:
            ``children`` holds submodel *indices* (``sm.index``), not positions
            in ``self.submodels``. SOBJ chunks can arrive out of order or with
            gaps, so the two are not interchangeable.
        """
        by_index = {sm.index: sm for sm in self.submodels}
        for sm in self.submodels:
            sm.children = []
        for sm in self.submodels:
            parent = by_index.get(sm.parent)  # sm.parent == -1 (root) -> None
            if parent is not None:
                parent.children.append(sm.index)


class POFReader:
    """Reads little-endian POF primitives from a binary stream.

    Every read is bounds-checked through :meth:`read_bytes`, so a truncated or
    misaligned file raises :class:`EOFError` at the point of the short read
    rather than silently returning garbage.
    """

    def __init__(self, stream: BinaryIO) -> None:
        """Wrap ``stream`` for reading.

        Args:
            stream: A readable, seekable binary stream.
        """
        self.stream = stream

    def read_bytes(self, n: int) -> bytes:
        """Read exactly ``n`` bytes.

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
        so the terminator is stripped after decoding rather than assumed.
        Non-ASCII bytes are substituted, as in :meth:`POFWriter.write_string`.

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

    def read_count(self, what: str, item_size: int = SIZEOF_FIELD) -> int:
        """Read an element count, refusing one the stream could not supply.

        A corrupt count is a liveness problem, not just a data one: a negative
        one skips its loop and misaligns every later read, an enormous one
        grinds through allocations until the short read stops it. The ceiling is
        the remaining file size rather than a fixed limit, so no legitimate
        model is refused for being large.

        Args:
            item_size: Smallest number of bytes one counted record can occupy, a
                floor rather than the exact size -- it only has to refuse
                impossible counts; a short read catches whatever slips past.

        Returns:
            The count, non-negative and small enough that the remaining bytes
            could hold that many records.

        Raises:
            ValueError: If the count is negative, or larger than the bytes left
                in the stream could supply.
        """
        count = self.read_int32()
        if count < 0:
            raise ValueError(f"Invalid {what} count: {count}")
        remaining = self.bytes_remaining()
        if count > remaining // item_size:
            raise ValueError(
                f"Invalid {what} count: {count} needs at least "
                f"{count * item_size} bytes, but {remaining} remain"
            )
        return count

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

        A negative length is refused here because :func:`parse_pof_stream`
        reaches the next header by seeking to ``chunk_start + length``: a
        negative one seeks backwards onto the header just read and the loop
        spins forever on Blender's main thread. A length running merely *past
        the end* is deliberately allowed through -- whether that is fatal
        depends on the chunk ID, which only the caller has; refusing it here
        made a good model with junk bytes on the end unimportable.

        Returns:
            A ``(chunk_id, data_length)`` pair. The length excludes the eight
            header bytes just consumed and is non-negative. It is *not* promised
            to fit in the stream.

        Raises:
            ValueError: If the length is negative.
            EOFError: If fewer than eight bytes remain. Callers use this to
                detect the end of the chunk list.
        """
        chunk_id = self.read_uint32()
        length = self.read_int32()
        if length < 0:
            raise ValueError(f"Invalid chunk length: {length}")
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

    def bytes_remaining(self) -> int:
        """Return how many bytes follow the current position.

        Returns:
            The byte count between here and the end of the stream. The position
            is restored, so this is safe to call mid-parse.
        """
        here = self.tell()
        end = self.stream.seek(0, io.SEEK_END)
        self.seek(here)
        return end - here


class POFWriter:
    """Writes little-endian POF primitives to a binary stream.

    Chunk bodies are built in a scratch :class:`io.BytesIO` and only then
    written with their length prefix, because a chunk header has to state the
    body size before the body exists.
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

        The format is 8-bit ASCII but Blender names are UTF-8, so
        unrepresentable characters are substituted rather than raising: losing
        an accent beats failing the whole export.
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


def _parse_subobj(reader: POFReader, model: POFModel, chunk_end: int) -> None:
    """Parse one SOBJ chunk and store the submodel on ``model``.

    Args:
        reader: Reader positioned at the start of the chunk body.
        model: Model to append to. Its ``version`` and ``major_version`` must
            already be set, because they select the record layout.
        chunk_end: Absolute offset of the end of this chunk. The caller seeks
            here afterwards, so trailing fields this parser ignores are skipped
            rather than mistaken for the next chunk.

    Raises:
        ValueError: If the record's own index is outside ``0`` ..
            :data:`MAX_SUBMODEL_INDEX`, or if one of its element counts claims
            more data than the file holds.
        EOFError: If the record is truncated.

    Note:
        The submodel is stored at ``model.submodels[sm.index]``, growing the
        list with ``None`` placeholders as needed, because SOBJ chunks can
        arrive out of order. :func:`parse_pof_stream` drops any placeholder
        still unfilled once every chunk has been read.

        That storage is why the index is validated first: a negative index does
        not raise but quietly overwrites, since ``model.submodels[-1] = sm``
        replaces the last submodel parsed and leaves no placeholder for the
        missing-submodel warning to catch.
    """
    sm = Submodel()
    sm.index = reader.read_int32()
    if not 0 <= sm.index <= MAX_SUBMODEL_INDEX:
        raise ValueError(
            f"Invalid submodel index: {sm.index} "
            f"(expected 0..{MAX_SUBMODEL_INDEX})"
        )
    sm.parent = reader.read_int32()
    sm.normal = reader.read_vector3()
    _d = reader.read_float()  # separation plane d, not used
    sm.point = reader.read_vector3()
    sm.offset = reader.read_vector3()
    sm.radius = reader.read_float()
    sm.tree_offset = reader.read_int32()
    sm.data_offset = reader.read_int32()

    features = model.features

    if features.geometric_center:
        sm.geometric_center = reader.read_vector3()

    sm.name = reader.read_string()
    sm.props = reader.read_string()
    sm.movement_type = reader.read_int32()
    sm.movement_axis = reader.read_int32()

    n_chunks = reader.read_count("freespace chunk", SIZEOF_FIELD)
    for _ in range(n_chunks):
        reader.read_int32()

    n_verts = reader.read_count("vertex", SIZEOF_FIELD * 3)
    for _ in range(n_verts):
        pos = reader.read_vector3()
        sm.vertices.append(SubmodelVertex(position=pos))

    for i in range(n_verts):
        sm.vertices[i].normal = reader.read_vector3()

    if features.vertex_alpha:
        for i in range(n_verts):
            sm.vertices[i].alpha = reader.read_float()
            if sm.vertices[i].alpha < ALPHA_OPAQUE_THRESHOLD:
                model.flags |= PMF_ALPHA

    n_faces = reader.read_count("face", SIZEOF_FIELD * 3)
    for _ in range(n_faces):
        face = ModelFace()
        face.normal = reader.read_vector3()
        n_fv = reader.read_count("face vertex", SIZEOF_FIELD * 3)
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

        if features.lightmap_uv:
            reader.read_float()  # xdiff
            reader.read_float()  # ydiff

        sm.faces.append(face)

    _parse_submodel_flags(sm)

    while len(model.submodels) <= sm.index:
        model.submodels.append(None)
    model.submodels[sm.index] = sm


def _parse_submodel_flags(sm: Submodel) -> None:
    """Set ``sm.flags`` from the ``$``-command in the submodel's property string.

    Only the first command is honoured, matching the engine: the string is split
    at the first ``=`` into command (including that ``=``) and payload, and the
    command is looked up case-insensitively in :data:`PROPERTY_COMMAND_FLAGS`.

    Args:
        sm: Updated in place. An unrecognised command leaves ``flags``
            untouched; ``props`` is preserved either way, so a command this
            add-on does not model still survives a round trip.
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


def parse_pof(data: bytes) -> POFModel:
    """Parse a POF file held in memory.

    Args:
        data: Complete contents of a ``.pof`` or ``.oof`` file.

    Returns:
        The parsed model, with its hierarchy already built.

    Raises:
        ValueError: If the magic bytes or version are not acceptable, or if the
            data declares a size it does not hold; see
            :func:`parse_pof_stream`.
        EOFError: If the data is truncated.
    """
    stream = io.BytesIO(data)
    return parse_pof_stream(stream)


def parse_pof_stream(stream: BinaryIO) -> POFModel:
    """Parse a POF file from a binary stream.

    Chunks are dispatched by ID; an unrecognised chunk is skipped by seeking to
    the end offset its own header declares, so an unfamiliar chunk costs data
    but does not desynchronise the stream. Every size that offset depends on is
    bounds-checked first -- see :meth:`POFReader.read_chunk_header` and
    :meth:`POFReader.read_count`.

    A size that would send the loop *backwards* is refused outright, since the
    loop is driven by those offsets and a bad one spins forever. A size running
    off the end of the file is tolerated only for an unrecognised chunk, which
    is skipped anyway; a chunk this parser reads raises instead, because half a
    submodel record is not a submodel.

    Args:
        stream: Readable, seekable stream positioned at the file header.

    Returns:
        The parsed model, with its hierarchy already built. A truncated file
        yields everything up to that point rather than nothing at all, with a
        warning saying where reading stopped.

    Raises:
        ValueError: If the magic bytes do not match :data:`POF_MAGIC`, if the
            version falls outside :data:`MIN_OBJFILE_VERSION` ..
            :data:`OBJFILE_VERSION`, if a chunk length is negative, or if a
            submodel index or an element count describes more data than the file
            holds.
        EOFError: If the file ends in the middle of a record.
    """
    reader = POFReader(stream)
    model = POFModel()

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

    features = model.features
    if features.lightmap_uv:
        model.flags |= PMF_LIGHTMAP_RES
    if features.timed_anim:
        model.flags |= PMF_TIMED

    timed = features.timed_anim

    while True:
        pos = reader.tell()
        try:
            chunk_id, chunk_len = reader.read_chunk_header()
        except EOFError:
            # Normal exit: running out of chunks is how a well-formed file ends.
            # A clean end lands exactly on a chunk boundary, so leftover bytes
            # mean a header was cut in half and chunks are missing.
            reader.seek(pos)
            trailing = reader.bytes_remaining()
            if trailing > 0:
                log.warning(
                    "File ends mid-chunk-header: %d trailing byte(s) after the "
                    "last complete chunk. The file is truncated and anything "
                    "past this point was not read.", trailing,
                )
            elif trailing < 0:
                # The previous chunk declared a body past EOF but needed less of
                # it than claimed, so the seek to its end landed beyond EOF.
                # Report that as truncation, not as "-40 trailing bytes".
                log.warning(
                    "The last chunk's declared body runs %d byte(s) past the "
                    "end of the file. It is truncated and anything past this "
                    "point was not read.", -trailing,
                )
            break

        chunk_start = reader.tell()
        chunk_end = chunk_start + chunk_len

        if chunk_id == CHUNK_OHDR:
            n_submodels = reader.read_int32()
            model.radius = reader.read_float()
            model.min_bound = reader.read_vector3()
            model.max_bound = reader.read_vector3()
            n_detail = reader.read_count("detail level", SIZEOF_FIELD)
            for _ in range(n_detail):
                reader.read_int32()  # skip detail indices

        elif chunk_id == CHUNK_TXTR:
            n_textures = reader.read_count("texture", SIZEOF_FIELD)
            model.textures = []
            for _ in range(n_textures):
                name = reader.read_string()
                model.textures.append(name)

        elif chunk_id == CHUNK_SOBJ:
            _parse_subobj(reader, model, chunk_end)

        elif chunk_id == CHUNK_GPNT:
            n_guns = reader.read_count("gun bank", SIZEOF_FIELD * 6)
            model.gun_banks = []
            for _ in range(n_guns):
                bank = GunBank()
                if features.gunpoint_parent:
                    bank.parent = reader.read_int32()
                bank.point = reader.read_vector3()
                bank.normal = reader.read_vector3()
                model.gun_banks.append(bank)

        elif chunk_id == CHUNK_WBS:
            n_wb = reader.read_count("weapon battery", SIZEOF_FIELD * 2)
            model.weapon_batteries = []
            for _ in range(n_wb):
                wb = WeaponBattery()
                n_gps = reader.read_count("battery gunpoint", SIZEOF_FIELD)
                for j in range(n_gps):
                    gp = reader.read_int32()
                    if j < MAX_WB_GUNPOINTS:
                        wb.gunpoints.append(gp)
                n_turrets = reader.read_count("battery turret", SIZEOF_FIELD)
                for j in range(n_turrets):
                    t = reader.read_int32()
                    if j < MAX_WB_TURRETS:
                        wb.turrets.append(t)
                model.weapon_batteries.append(wb)

        elif chunk_id in (CHUNK_ANIM, CHUNK_RANI):
            nframes = 0
            if not timed:
                nframes = reader.read_count("animation frame", SIZEOF_FIELD * 4)

            for i, sm in enumerate(model.submodels):
                if sm is None:
                    continue
                if timed:
                    sm.num_key_angles = reader.read_count(
                        "rotation key", SIZEOF_FIELD * 4
                    )
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
                nframes = reader.read_count("animation frame", SIZEOF_FIELD * 3)

            for i, sm in enumerate(model.submodels):
                if sm is None:
                    continue
                if timed:
                    sm.num_key_pos = reader.read_count(
                        "position key", SIZEOF_FIELD * 3
                    )
                    sm.pos_track_min = reader.read_int32()
                    sm.pos_track_max = reader.read_int32()
                    if sm.pos_track_min < model.frame_min:
                        model.frame_min = sm.pos_track_min
                    if sm.pos_track_max > model.frame_max:
                        model.frame_max = sm.pos_track_max
                else:
                    sm.num_key_pos = nframes

                # A submodel can carry more position keys than rotation keys, so
                # the list built from ANIM is extended rather than iterated.
                for k in range(sm.num_key_pos):
                    while len(sm.keyframes) <= k:
                        sm.keyframes.append(Keyframe())
                    kf = sm.keyframes[k]
                    if timed:
                        kf.pos_start_time = reader.read_int32()
                    kf.position = reader.read_vector3()

        elif chunk_id == CHUNK_GRND:
            n_ground = reader.read_count("ground plane", SIZEOF_FIELD * 7)
            model.ground_planes = []
            for _ in range(n_ground):
                bank = GunBank()
                bank.parent = reader.read_int32()
                bank.point = reader.read_vector3()
                bank.normal = reader.read_vector3()
                model.ground_planes.append(bank)

        elif chunk_id == CHUNK_ATCH:
            n_attach = reader.read_count("attach point", SIZEOF_FIELD * 7)
            model.attach_points = []
            for _ in range(n_attach):
                ap = AttachPoint()
                ap.parent = reader.read_int32()
                ap.point = reader.read_vector3()
                ap.normal = reader.read_vector3()
                model.attach_points.append(ap)

        elif chunk_id == CHUNK_NATH:
            # Read plainly rather than through read_count: the equality test
            # below is already the bound, accepting only the ATCH point count.
            # Anything else takes the tolerant path instead of failing an import
            # over optional up-vectors.
            n_normals = reader.read_int32()
            if len(model.attach_points) == n_normals:
                for i in range(n_normals):
                    model.attach_points[i].has_uvec = True
                    reader.read_int32()  # index
                    # Layout is index, normal, uvec (polymodel.cpp,
                    # ID_ATTACH_NORMALS); the normal repeats ATCH's and is
                    # discarded. Swapping the two silently rotates anything
                    # attached to the model.
                    reader.read_vector3()  # normal (already read from ATCH)
                    model.attach_points[i].uvec = reader.read_vector3()
            else:
                log.warning(
                    "Ignoring ATTACH normals: %d attach points but %d normals",
                    len(model.attach_points), n_normals,
                )

        else:
            # Unknown chunk: nothing to read, only somewhere to skip to. If that
            # is past EOF there is no next chunk, so the chunk list stops here
            # rather than refusing a model whose every other chunk parsed --
            # padding or junk bytes reading as a header is a real case. Only
            # tolerable here: a chunk the parser understands needs its body, and
            # half of one raises EOFError above instead.
            if chunk_len > reader.bytes_remaining():
                log.warning(
                    "Unreadable chunk at offset %d declares %d byte(s) of body "
                    "with only %d left in the file. It is truncated, and "
                    "anything past this point was not read.",
                    pos, chunk_len, reader.bytes_remaining(),
                )
                break

        # Forward progress is the only thing that ends this loop: a chunk_end at
        # or before this iteration's header re-reads that header forever on
        # Blender's main thread. read_chunk_header already refuses every length
        # that could do that, so this is a backstop against a future edit to it.
        if chunk_end <= pos:
            raise ValueError(
                f"Chunk at offset {pos} ends at {chunk_end}, which is not "
                f"forward progress"
            )
        reader.seek(chunk_end)

    # Drop placeholders left where a SOBJ index was never filled in; must
    # happen before build_hierarchy, which cannot walk a list of Nones.
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

    Pre-v22 files store one global key count for every submodel (``ID_ANIM`` in
    polymodel.cpp), so a shorter track must be padded or the reader runs off the
    end of the chunk. Repeating the last key holds the final pose.

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
    """
    writer = POFWriter(stream)

    writer.write_bytes(POF_MAGIC)
    writer.write_int32(model.version)

    features = model.features
    timed = features.timed_anim

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

    txtr_buf = io.BytesIO()
    txtr_w = POFWriter(txtr_buf)
    txtr_w.write_int32(len(model.textures))
    for tex in model.textures:
        txtr_w.write_string(tex)
    writer.write_chunk_header(CHUNK_TXTR, txtr_buf.tell())
    writer.write_bytes(txtr_buf.getvalue())

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
        if features.geometric_center:
            sobj_w.write_vector3(sm.geometric_center)
        sobj_w.write_string(sm.name)
        sobj_w.write_string(sm.props)
        sobj_w.write_int32(sm.movement_type)
        sobj_w.write_int32(sm.movement_axis)
        sobj_w.write_int32(0)  # no freespace chunks

        sobj_w.write_int32(len(sm.vertices))
        for v in sm.vertices:
            sobj_w.write_vector3(v.position)
        for v in sm.vertices:
            sobj_w.write_vector3(v.normal)

        if features.vertex_alpha:
            for v in sm.vertices:
                sobj_w.write_float(v.alpha)

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
            # Lightmap UV diffs are never authored here and the reader
            # discards them, so there is nothing to round-trip -- emit zeros.
            if features.lightmap_uv:
                sobj_w.write_float(0.0)
                sobj_w.write_float(0.0)

        writer.write_chunk_header(CHUNK_SOBJ, sobj_buf.tell())
        writer.write_bytes(sobj_buf.getvalue())

    if model.gun_banks:
        gpnt_buf = io.BytesIO()
        gpnt_w = POFWriter(gpnt_buf)
        gpnt_w.write_int32(len(model.gun_banks))
        for bank in model.gun_banks:
            if features.gunpoint_parent:
                gpnt_w.write_int32(bank.parent)
            gpnt_w.write_vector3(bank.point)
            gpnt_w.write_vector3(bank.normal)
        writer.write_chunk_header(CHUNK_GPNT, gpnt_buf.tell())
        writer.write_bytes(gpnt_buf.getvalue())

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
