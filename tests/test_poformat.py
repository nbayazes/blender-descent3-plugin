"""
Unit tests for the POF binary parser and writer.
Run with: python -m pytest tests/test_poformat.py -v
"""

import io
import os
import struct

import pytest

from descent3_plugin.mathutil import Vector3
from descent3_plugin.poformat import (
    AttachPoint,
    GunBank,
    MAX_SUBMODEL_INDEX,
    OBJFILE_VERSION,
    POFModel,
    POFReader,
    POFWriter,
    POF_MAGIC,
    SOF_TURRET,
    Submodel,
    SubmodelVertex,
    parse_pof,
    parse_pof_stream,
    write_pof,
)

MOCK_DIR = os.path.join(os.path.dirname(__file__), "mock_data")


class ParserSpun(RuntimeError):
    """A parse read far more than its input could possibly justify."""


class BudgetedStream(io.BytesIO):
    """A stream that ends a runaway parse instead of letting it hang the suite.

    A negative chunk length seeks back onto the header just read and
    ``parse_pof_stream`` re-reads it forever, so a plain ``pytest.raises`` would
    wedge the run at 100% CPU rather than fail. A spinning parse burns two reads
    per revolution, so any budget above a sound parse's needs trips it at once.
    """

    def __init__(self, data: bytes, budget: int = 500) -> None:
        super().__init__(data)
        self._reads_left = budget

    def read(self, *args, **kwargs):
        self._reads_left -= 1
        if self._reads_left < 0:
            raise ParserSpun("parser made no forward progress")
        return super().read(*args, **kwargs)


def _pof_header() -> bytes:
    """Return the 8-byte file header every crafted fixture below starts with."""
    return POF_MAGIC + struct.pack("<i", OBJFILE_VERSION)


def _chunk_header(tag: bytes, length: int) -> bytes:
    """Return a chunk header for ``tag`` declaring a body of ``length`` bytes.

    Chunk IDs are the little-endian int of their four ASCII bytes, so the tag
    lands in the file verbatim and can be spelled as bytes here.
    """
    return tag + struct.pack("<i", length)


class TestPOFReader:
    def test_read_int32(self):
        import io
        data = struct.pack("<i", 42)
        reader = POFReader(io.BytesIO(data))
        assert reader.read_int32() == 42

    def test_read_float(self):
        import io
        data = struct.pack("<f", 3.14)
        reader = POFReader(io.BytesIO(data))
        assert reader.read_float() == pytest.approx(3.14)

    def test_read_vector3(self):
        import io
        data = struct.pack("<fff", 1.0, 2.0, 3.0)
        reader = POFReader(io.BytesIO(data))
        v = reader.read_vector3()
        assert v.x == pytest.approx(1.0)
        assert v.y == pytest.approx(2.0)
        assert v.z == pytest.approx(3.0)

    def test_read_string(self):
        import io
        s = "hello"
        encoded = s.encode("ascii")
        data = struct.pack("<i", len(encoded)) + encoded
        reader = POFReader(io.BytesIO(data))
        assert reader.read_string() == "hello"

    def test_read_eof_error(self):
        import io
        reader = POFReader(io.BytesIO(b""))
        with pytest.raises(EOFError):
            reader.read_int32()


class TestPOFWriter:
    def test_write_int32(self):
        import io
        stream = io.BytesIO()
        writer = POFWriter(stream)
        writer.write_int32(42)
        stream.seek(0)
        assert struct.unpack("<i", stream.read(4))[0] == 42

    def test_write_float(self):
        import io
        stream = io.BytesIO()
        writer = POFWriter(stream)
        writer.write_float(3.14)
        stream.seek(0)
        assert struct.unpack("<f", stream.read(4))[0] == pytest.approx(3.14)

    def test_write_vector3(self):
        import io
        stream = io.BytesIO()
        writer = POFWriter(stream)
        writer.write_vector3(Vector3(1, 2, 3))
        stream.seek(0)
        x, y, z = struct.unpack("<fff", stream.read(12))
        assert x == pytest.approx(1.0)
        assert y == pytest.approx(2.0)
        assert z == pytest.approx(3.0)

    def test_write_string(self):
        import io
        stream = io.BytesIO()
        writer = POFWriter(stream)
        writer.write_string("hello")
        stream.seek(0)
        length = struct.unpack("<i", stream.read(4))[0]
        assert length == 6
        data = stream.read(6)
        assert data == b"hello\x00"


class TestStringNullTermination:
    """Real Descent 3 OOF strings are length-prefixed with the length
    *including* the trailing NUL. The reader must strip it; the writer adds it.
    """

    def _read(self, raw):
        import io
        return POFReader(io.BytesIO(raw)).read_string()

    def test_reads_length_including_null(self):
        # 14 == len("WarningStripe") + 1, exactly as seen in real .OOF files
        raw = struct.pack("<i", 14) + b"WarningStripe\x00"
        assert self._read(raw) == "WarningStripe"

    def test_reads_without_null(self):
        # Mock/test files omit the NUL; length equals the raw string length
        raw = struct.pack("<i", 5) + b"hello"
        assert self._read(raw) == "hello"

    def test_writer_appends_null_and_counts_it(self):
        import io
        stream = io.BytesIO()
        POFWriter(stream).write_string("hull")
        data = stream.getvalue()
        assert struct.unpack("<i", data[:4])[0] == 5  # 4 chars + NUL
        assert data[4:] == b"hull\x00"

    def test_write_read_roundtrip_has_no_trailing_null(self):
        import io
        stream = io.BytesIO()
        POFWriter(stream).write_string("ConcussionMissile")
        stream.seek(0)
        assert POFReader(stream).read_string() == "ConcussionMissile"


class TestParseMinimalCube:
    @pytest.fixture
    def model(self):
        path = os.path.join(MOCK_DIR, "minimal_cube.pof")
        with open(path, "rb") as f:
            return parse_pof(f.read())

    def test_version(self, model):
        assert model.version == OBJFILE_VERSION
        assert model.major_version == 23

    def test_submodel_count(self, model):
        assert len(model.submodels) == 1

    def test_submodel_name(self, model):
        assert model.submodels[0].name == "hull"

    def test_vertex_count(self, model):
        assert len(model.submodels[0].vertices) == 4

    def test_face_count(self, model):
        assert len(model.submodels[0].faces) == 1

    def test_face_vertex_count(self, model):
        assert len(model.submodels[0].faces[0].vertices) == 4

    def test_texture(self, model):
        assert model.textures == ["default"]

    def test_radius(self, model):
        assert model.radius == pytest.approx(1.0)

    def test_vertices_not_empty(self, model):
        verts = model.submodels[0].vertices
        assert all(isinstance(v, SubmodelVertex) for v in verts)

    def test_vertex_positions(self, model):
        verts = model.submodels[0].vertices
        assert verts[0].position.x == pytest.approx(-0.5)
        assert verts[0].position.y == pytest.approx(-0.5)
        assert verts[1].position.x == pytest.approx(0.5)


class TestParseEmptyModel:
    @pytest.fixture
    def model(self):
        path = os.path.join(MOCK_DIR, "empty_model.pof")
        with open(path, "rb") as f:
            return parse_pof(f.read())

    def test_submodel_count(self, model):
        assert len(model.submodels) == 1

    def test_vertex_count(self, model):
        assert len(model.submodels[0].vertices) == 0

    def test_face_count(self, model):
        assert len(model.submodels[0].faces) == 0


class TestParseSingleVertex:
    @pytest.fixture
    def model(self):
        path = os.path.join(MOCK_DIR, "single_vertex.pof")
        with open(path, "rb") as f:
            return parse_pof(f.read())

    def test_vertex_count(self, model):
        assert len(model.submodels[0].vertices) == 1

    def test_face_count(self, model):
        assert len(model.submodels[0].faces) == 0

    def test_texture(self, model):
        assert model.textures == ["point"]


class TestParseCorruptMagic:
    def test_raises_error(self):
        path = os.path.join(MOCK_DIR, "corrupt_magic.pof")
        with open(path, "rb") as f:
            with pytest.raises(ValueError, match="Invalid POF magic"):
                parse_pof(f.read())


class TestParseTruncated:
    def test_raises_error(self):
        path = os.path.join(MOCK_DIR, "truncated.pof")
        with open(path, "rb") as f:
            with pytest.raises((EOFError, ValueError)):
                parse_pof(f.read())


class TestParseHierarchical:
    @pytest.fixture
    def model(self):
        path = os.path.join(MOCK_DIR, "hierarchical.pof")
        with open(path, "rb") as f:
            return parse_pof(f.read())

    def test_submodel_count(self, model):
        assert len(model.submodels) == 2

    def test_parent_child(self, model):
        assert model.submodels[0].parent == -1
        assert model.submodels[1].parent == 0

    def test_children_built(self, model):
        assert 1 in model.submodels[0].children

    def test_turret_flags(self, model):
        turret = model.submodels[1]
        assert turret.flags & SOF_TURRET != 0

    def test_multiple_textures(self, model):
        assert model.textures == ["hull", "turret"]


class TestWritePOF:
    def test_write_roundtrip(self):
        """Parse a POF, write it, parse again, verify equal."""
        path = os.path.join(MOCK_DIR, "minimal_cube.pof")
        with open(path, "rb") as f:
            original = parse_pof(f.read())

        data = write_pof(original)
        reparsed = parse_pof(data)

        assert reparsed.version == original.version
        assert len(reparsed.submodels) == len(original.submodels)
        assert len(reparsed.textures) == len(original.textures)
        assert reparsed.textures == original.textures

        assert len(reparsed.submodels[0].vertices) == len(original.submodels[0].vertices)
        assert len(reparsed.submodels[0].faces) == len(original.submodels[0].faces)

    def test_write_magic(self):
        """Written POF starts with correct magic."""
        model = POFModel()
        model.submodels.append(Submodel(index=0, name="test"))
        data = write_pof(model)
        assert data[:4] == POF_MAGIC

    def test_write_version(self):
        """Written POF has correct version."""
        model = POFModel()
        model.submodels.append(Submodel(index=0, name="test"))
        data = write_pof(model)
        version = struct.unpack("<i", data[4:8])[0]
        assert version == OBJFILE_VERSION


class TestBuildHierarchy:
    def test_build_children(self):
        model = POFModel()
        sm0 = Submodel(index=0, parent=-1)
        sm1 = Submodel(index=1, parent=0)
        sm2 = Submodel(index=2, parent=0)
        sm3 = Submodel(index=3, parent=1)
        model.submodels = [sm0, sm1, sm2, sm3]
        model.build_hierarchy()

        assert sm0.children == [1, 2]
        assert sm1.children == [3]
        assert sm2.children == []
        assert sm3.children == []


class TestEdgeCases:
    def test_empty_model_write(self):
        """Can write and re-parse an empty model."""
        model = POFModel()
        model.submodels.append(Submodel(index=0, name="empty"))
        data = write_pof(model)
        reparsed = parse_pof(data)
        assert len(reparsed.submodels) == 1
        assert reparsed.submodels[0].name == "empty"

    def test_many_vertices(self):
        """Model with many vertices parses correctly."""
        model = POFModel()
        sm = Submodel(index=0, name="many_verts")
        for i in range(100):
            sm.vertices.append(SubmodelVertex(position=Vector3(float(i), 0, 0)))
        model.submodels.append(sm)
        data = write_pof(model)
        reparsed = parse_pof(data)
        assert len(reparsed.submodels[0].vertices) == 100

    def test_special_characters_in_name(self):
        """Names with special chars are handled."""
        model = POFModel()
        sm = Submodel(index=0, name="test_model-01")
        model.submodels.append(sm)
        data = write_pof(model)
        reparsed = parse_pof(data)
        assert reparsed.submodels[0].name == "test_model-01"

    def test_properties_preserved(self):
        """Submodel properties string is preserved through roundtrip."""
        model = POFModel()
        sm = Submodel(index=0, name="rotator", props="$rotate=5.0")
        model.submodels.append(sm)
        data = write_pof(model)
        reparsed = parse_pof(data)
        assert reparsed.submodels[0].props == "$rotate=5.0"

    def test_gun_banks_roundtrip(self):
        """Gun banks survive roundtrip."""
        model = POFModel()
        model.submodels.append(Submodel(index=0, name="hull"))
        model.gun_banks.append(GunBank(parent=0, point=Vector3(1, 2, 3), normal=Vector3(0, 0, 1)))
        data = write_pof(model)
        reparsed = parse_pof(data)
        assert len(reparsed.gun_banks) == 1
        assert reparsed.gun_banks[0].point.x == pytest.approx(1.0)
        assert reparsed.gun_banks[0].point.y == pytest.approx(2.0)
        assert reparsed.gun_banks[0].point.z == pytest.approx(3.0)

    def test_attach_points_roundtrip(self):
        """Attach points survive roundtrip."""
        model = POFModel()
        model.submodels.append(Submodel(index=0, name="hull"))
        model.attach_points.append(
            AttachPoint(parent=0, point=Vector3(1, 0, 0), normal=Vector3(0, 1, 0))
        )
        data = write_pof(model)
        reparsed = parse_pof(data)
        assert len(reparsed.attach_points) == 1
        assert reparsed.attach_points[0].point.x == pytest.approx(1.0)


class TestTruncatedTail:
    """A file cut short mid-chunk-header must say so.

    Chunk reading ends on EOFError, which is also how a well-formed file
    finishes, so a truncated tail used to be indistinguishable from a clean end
    and the model silently lost whatever the cut-off chunks held.
    """

    @staticmethod
    def _valid_bytes():
        model = POFModel(version=OBJFILE_VERSION, major_version=23)
        model.textures = ["Hull"]
        sm = Submodel(index=0, parent=-1, name="Root")
        sm.vertices.append(SubmodelVertex(position=Vector3(1.0, 2.0, 3.0)))
        model.submodels.append(sm)
        return write_pof(model)

    def test_clean_file_warns_nothing(self, caplog):
        data = self._valid_bytes()
        with caplog.at_level("WARNING"):
            parse_pof(data)
        assert "trailing" not in caplog.text

    @pytest.mark.parametrize("extra", [1, 3, 7])
    def test_truncated_header_warns(self, caplog, extra):
        """1-7 leftover bytes cannot be a chunk header (which needs 8)."""
        data = self._valid_bytes() + b"\x00" * extra
        with caplog.at_level("WARNING"):
            model = parse_pof(data)
        assert "truncated" in caplog.text.lower()
        assert f"{extra} trailing byte" in caplog.text
        # Still returns what it managed to read, rather than raising.
        assert len(model.submodels) == 1

    def test_truncation_does_not_lose_earlier_chunks(self, caplog):
        data = self._valid_bytes() + b"\x01\x02\x03"
        with caplog.at_level("WARNING"):
            model = parse_pof(data)
        assert model.textures == ["Hull"]
        assert model.submodels[0].name == "Root"


class TestChunkLengthGuard:
    """A chunk length is signed and comes straight off disk.

    At -8 ``chunk_end`` equals the offset of the header being read, so the parse
    loop re-reads that header forever on Blender's main thread with no cancel.
    Every case here runs on a :class:`BudgetedStream` so a regression fails
    rather than hangs. A length running past the end of the file gets the
    opposite treatment -- see :class:`TestOverrunningChunkLength`.
    """

    @pytest.mark.parametrize("length", [-8, -1, -12, -(1 << 30)])
    def test_negative_length_is_rejected_and_cannot_spin(self, length):
        data = _pof_header() + _chunk_header(b"JUNK", length)
        with pytest.raises(ValueError):
            parse_pof_stream(BudgetedStream(data))

    def test_empty_unknown_chunk_still_parses(self):
        """Zero is a legal length: the header alone is forward progress."""
        data = _pof_header() + _chunk_header(b"JUNK", 0)
        model = parse_pof_stream(BudgetedStream(data))
        assert model.submodels == []

    def test_a_well_formed_file_is_unaffected(self):
        """The guard must not cost a legitimate file its chunks."""
        path = os.path.join(MOCK_DIR, "hierarchical.pof")
        with open(path, "rb") as f:
            model = parse_pof_stream(BudgetedStream(f.read(), budget=10000))
        assert len(model.submodels) == 2
        assert model.textures == ["hull", "turret"]


class TestOverrunningChunkLength:
    """A chunk body that is not in the file ends the chunk list.

    Eight or more junk bytes on the end of a good model read as a chunk header
    whose body is not there, and so does a file cut short by a failed copy.
    Every chunk before that point parsed, so validating this length the way the
    negative one is validated made imports that used to work fail completely.
    """

    @staticmethod
    def _valid_bytes():
        model = POFModel(version=OBJFILE_VERSION, major_version=23)
        model.textures = ["Hull"]
        sm = Submodel(index=0, parent=-1, name="Root")
        sm.vertices.append(SubmodelVertex(position=Vector3(1.0, 2.0, 3.0)))
        model.submodels.append(sm)
        return write_pof(model)

    @pytest.mark.parametrize(
        "tail",
        [
            b"JUNKJUNK",                            # reads as a huge length
            struct.pack("<Ii", 0x4B4E554A, 4096),   # a plausible-looking header
        ],
    )
    def test_trailing_junk_keeps_the_chunks_that_parsed(self, caplog, tail):
        with caplog.at_level("WARNING"):
            model = parse_pof(self._valid_bytes() + tail)
        assert model.textures == ["Hull"]
        assert len(model.submodels) == 1
        assert model.submodels[0].name == "Root"
        assert "truncated" in caplog.text.lower()

    def test_truncated_body_keeps_the_chunks_that_parsed(self, caplog):
        """A file cut short mid-body, not mid-header."""
        data = _pof_header() + _chunk_header(b"JUNK", 64) + b"\x00" * 16
        with caplog.at_level("WARNING"):
            model = parse_pof_stream(BudgetedStream(data))
        assert model.submodels == []
        assert "truncated" in caplog.text.lower()

    def test_a_body_that_exactly_fits_is_not_truncated(self, caplog):
        """The boundary: length == bytes remaining is a complete chunk."""
        data = _pof_header() + _chunk_header(b"JUNK", 4) + b"\x00" * 4
        with caplog.at_level("WARNING"):
            parse_pof_stream(BudgetedStream(data))
        assert "truncated" not in caplog.text.lower()


class TestSubmodelIndexGuard:
    """A SOBJ index is used to place the submodel in a list.

    A negative one does not raise, it overwrites: the growth loop never runs and
    ``submodels[-1] = sm`` replaces the submodel parsed before it. No ``None``
    is left behind, so the missing-submodel warning never fires and the children
    of the overwritten submodel lose their parent and land at the model origin.
    """

    @staticmethod
    def _two_submodels() -> bytes:
        model = POFModel(version=OBJFILE_VERSION, major_version=23)
        model.textures = ["Hull"]
        model.submodels.append(Submodel(index=0, parent=-1, name="Root"))
        model.submodels.append(Submodel(index=1, parent=0, name="Turret"))
        return write_pof(model)

    @staticmethod
    def _patch_last_index(data: bytes, index: int) -> bytes:
        """Rewrite the index field of the last SOBJ record in ``data``.

        Chunk tags survive into the file as their four ASCII bytes, so the
        record is found by searching for the tag rather than by re-implementing
        the chunk walk. The index is the first field of the body, i.e. eight
        bytes past the tag -- a 4-byte tag and a 4-byte length.
        """
        field = data.rindex(b"SOBJ") + 8
        return data[:field] + struct.pack("<i", index) + data[field + 4:]

    def test_fixture_is_what_the_patch_assumes(self):
        model = parse_pof(self._two_submodels())
        assert [sm.name for sm in model.submodels] == ["Root", "Turret"]
        assert model.submodels[1].index == 1

    def test_patch_helper_rewrites_the_index(self):
        model = parse_pof(self._patch_last_index(self._two_submodels(), 3))
        assert model.submodels[-1].index == 3
        assert model.submodels[-1].name == "Turret"

    @pytest.mark.parametrize("index", [-1, -2, -(1 << 30)])
    def test_negative_index_is_rejected(self, index):
        data = self._patch_last_index(self._two_submodels(), index)
        with pytest.raises(ValueError, match="submodel index"):
            parse_pof(data)

    def test_negative_index_no_longer_overwrites_a_parsed_submodel(self):
        """The old failure mode: two SOBJ records, one submodel, no warning."""
        data = self._patch_last_index(self._two_submodels(), -1)
        try:
            model = parse_pof(data)
        except ValueError:
            return
        assert [sm.name for sm in model.submodels] == ["Root", "Turret"], (
            "a SOBJ record silently replaced an already-parsed submodel"
        )

    def test_index_past_the_ceiling_is_rejected(self):
        """An index is a list length, so it cannot be taken on trust."""
        data = self._patch_last_index(self._two_submodels(), MAX_SUBMODEL_INDEX + 1)
        with pytest.raises(ValueError, match="submodel index"):
            parse_pof(data)

    def test_ceiling_itself_is_accepted(self):
        """The bound is deliberately far looser than the engine's own limit."""
        data = self._patch_last_index(self._two_submodels(), MAX_SUBMODEL_INDEX)
        model = parse_pof(data)
        assert model.submodels[-1].index == MAX_SUBMODEL_INDEX


class TestReadCount:
    """Counts decide how long a loop runs and how big a list gets.

    A negative one silently skips its loop and leaves every later read
    misaligned; an enormous one allocates until a short read stops it. The
    ceiling is the file's own size, so a large but honest model is never
    refused.
    """

    @staticmethod
    def _reader(count: int, payload: bytes = b"") -> POFReader:
        return POFReader(io.BytesIO(struct.pack("<i", count) + payload))

    def test_rejects_a_negative_count(self):
        with pytest.raises(ValueError, match="vertex count"):
            self._reader(-1).read_count("vertex")

    def test_rejects_more_records_than_the_stream_can_hold(self):
        # Three 12-byte records need 36 bytes; only 24 follow the count.
        with pytest.raises(ValueError, match="vertex count"):
            self._reader(3, b"\x00" * 24).read_count("vertex", 12)

    def test_accepts_an_exact_fit(self):
        assert self._reader(2, b"\x00" * 24).read_count("vertex", 12) == 2

    def test_zero_needs_no_bytes_at_all(self):
        assert self._reader(0).read_count("vertex", 12) == 0

    def test_leaves_the_stream_positioned_after_the_count(self):
        reader = self._reader(1, b"abcd")
        assert reader.read_count("thing", 4) == 1
        assert reader.read_bytes(4) == b"abcd"


class TestCorruptCountsInChunks:
    """The count guards, reached the way a real file reaches them."""

    def test_impossible_texture_count(self):
        body = struct.pack("<i", 2 ** 31 - 1)
        data = _pof_header() + _chunk_header(b"TXTR", len(body)) + body
        with pytest.raises(ValueError, match="texture count"):
            parse_pof_stream(BudgetedStream(data))

    def test_negative_texture_count(self):
        body = struct.pack("<i", -3)
        data = _pof_header() + _chunk_header(b"TXTR", len(body)) + body
        with pytest.raises(ValueError, match="texture count"):
            parse_pof_stream(BudgetedStream(data))

    def test_negative_detail_count(self):
        body = struct.pack("<i", 1) + struct.pack("<f", 1.0)
        body += struct.pack("<fff", 0, 0, 0) + struct.pack("<fff", 0, 0, 0)
        body += struct.pack("<i", -1)
        data = _pof_header() + _chunk_header(b"OHDR", len(body)) + body
        with pytest.raises(ValueError, match="detail level count"):
            parse_pof_stream(BudgetedStream(data))


class TestBytesRemaining:
    def test_counts_from_the_current_position(self):
        reader = POFReader(io.BytesIO(b"0123456789"))
        assert reader.bytes_remaining() == 10
        reader.read_bytes(4)
        assert reader.bytes_remaining() == 6

    def test_restores_the_position(self):
        reader = POFReader(io.BytesIO(b"0123456789"))
        reader.read_bytes(4)
        before = reader.tell()
        reader.bytes_remaining()
        assert reader.tell() == before
        assert reader.read_bytes(2) == b"45"

    def test_zero_at_end(self):
        reader = POFReader(io.BytesIO(b"abc"))
        reader.read_bytes(3)
        assert reader.bytes_remaining() == 0
