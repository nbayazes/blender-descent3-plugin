"""
Unit tests for the POF binary parser and writer.
Run with: python -m pytest tests/test_poformat.py -v
"""

import os
import sys
import struct
import pytest

# Add the addon directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "descent3_importer"))

from poformat import (
    POFModel,
    Submodel,
    SubmodelVertex,
    ModelFace,
    FaceVertex,
    GunBank,
    WeaponBattery,
    AttachPoint,
    Vector3,
    Color,
    POFReader,
    POFWriter,
    parse_pof,
    write_pof,
    POF_MAGIC,
    OBJFILE_VERSION,
    MIN_OBJFILE_VERSION,
    CHUNK_OHDR,
    CHUNK_TXTR,
    CHUNK_SOBJ,
    CHUNK_GPNT,
    CHUNK_WBS,
    CHUNK_ANIM,
    CHUNK_PANI,
    CHUNK_GRND,
    CHUNK_ATCH,
    SOF_ROTATE,
    SOF_TURRET,
    SOF_GLOW,
    SOF_THRUSTER,
    SOF_FACING,
    SOF_JITTER,
    PMF_ALPHA,
    PMF_TIMED,
)

MOCK_DIR = os.path.join(os.path.dirname(__file__), "mock_data")


class TestVector3:
    def test_creation(self):
        v = Vector3(1.0, 2.0, 3.0)
        assert v.x == 1.0
        assert v.y == 2.0
        assert v.z == 3.0

    def test_add(self):
        v1 = Vector3(1, 2, 3)
        v2 = Vector3(4, 5, 6)
        result = v1 + v2
        assert result.x == 5
        assert result.y == 7
        assert result.z == 9

    def test_sub(self):
        v1 = Vector3(5, 6, 7)
        v2 = Vector3(1, 2, 3)
        result = v1 - v2
        assert result.x == 4
        assert result.y == 4
        assert result.z == 4

    def test_mul(self):
        v = Vector3(1, 2, 3)
        result = v * 2
        assert result.x == 2
        assert result.y == 4
        assert result.z == 6

    def test_magnitude(self):
        v = Vector3(3, 4, 0)
        assert v.magnitude() == pytest.approx(5.0)

    def test_normalized(self):
        v = Vector3(3, 0, 0)
        n = v.normalized()
        assert n.x == pytest.approx(1.0)
        assert n.y == pytest.approx(0.0)
        assert n.z == pytest.approx(0.0)

    def test_normalized_zero(self):
        v = Vector3(0, 0, 0)
        n = v.normalized()
        assert n.x == 0
        assert n.y == 0
        assert n.z == 0

    def test_as_tuple(self):
        v = Vector3(1, 2, 3)
        assert v.as_tuple() == (1.0, 2.0, 3.0)


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
        assert length == 5
        data = stream.read(5)
        assert data == b"hello"


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
