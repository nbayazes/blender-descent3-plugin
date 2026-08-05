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
    OBJFILE_VERSION,
    POFModel,
    POFReader,
    POFWriter,
    POF_MAGIC,
    SOF_TURRET,
    Submodel,
    SubmodelVertex,
    parse_pof,
    write_pof,
)

MOCK_DIR = os.path.join(os.path.dirname(__file__), "mock_data")


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
    finishes, so a truncated tail used to be indistinguishable from a clean
    end: the model silently lost whatever the cut-off chunks held.
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
