"""
Tests for the POF version capability matrix.

The reader and the writer must agree, for every version, about which optional
fields a record carries -- disagree by one field and every byte after it is
misread. Both sides ask :func:`features_for`, so these tests pin the matrix
itself and then prove the agreement by round-tripping a model at each version
that sits on a feature boundary.

Run with: python -m pytest tests/test_version_features.py -v
"""

import pytest

from descent3_plugin.mathutil import Vector3
from descent3_plugin.poformat import (
    MAJOR_VERSION_LIGHTMAP,
    MAJOR_VERSION_TIMED_ANIM,
    MAJOR_VERSION_VERTEX_ALPHA,
    MIN_OBJFILE_VERSION,
    OBJFILE_VERSION,
    VERSION_GEOMETRIC_CENTER,
    VERSION_GUNPOINT_PARENT,
    VERSION_MAJOR_SCALE,
    FaceVertex,
    GunBank,
    ModelFace,
    POFModel,
    Submodel,
    SubmodelVertex,
    features_for,
    parse_pof,
    write_pof,
)

#: Versions worth exercising: the oldest accepted, each feature boundary, and
#: the newest written.
BOUNDARY_VERSIONS = [1807, 1908, 2100, 2200, 2300]


class TestFeatureMatrix:
    """Pin the matrix so a threshold edit cannot pass silently."""

    @pytest.mark.parametrize(
        "version,expected",
        [
            # version   geo    gun    lmap   timed  alpha
            (1807, (True, False, False, False, False)),
            (1907, (True, False, False, False, False)),
            (1908, (True, True, False, False, False)),
            (2100, (True, True, True, False, False)),
            (2200, (True, True, True, True, False)),
            (2300, (True, True, True, True, True)),
        ],
    )
    def test_known_versions(self, version, expected):
        f = features_for(version)
        actual = (
            f.geometric_center,
            f.gunpoint_parent,
            f.lightmap_uv,
            f.timed_anim,
            f.vertex_alpha,
        )
        assert actual == expected

    def test_geometric_center_is_strictly_greater(self):
        """The threshold is exclusive, unlike every other gate."""
        assert features_for(VERSION_GEOMETRIC_CENTER).geometric_center is False
        assert features_for(VERSION_GEOMETRIC_CENTER + 1).geometric_center is True

    def test_gunpoint_parent_is_inclusive(self):
        assert features_for(VERSION_GUNPOINT_PARENT - 1).gunpoint_parent is False
        assert features_for(VERSION_GUNPOINT_PARENT).gunpoint_parent is True

    @pytest.mark.parametrize(
        "major,attr",
        [
            (MAJOR_VERSION_LIGHTMAP, "lightmap_uv"),
            (MAJOR_VERSION_TIMED_ANIM, "timed_anim"),
            (MAJOR_VERSION_VERTEX_ALPHA, "vertex_alpha"),
        ],
    )
    def test_major_gates_are_inclusive(self, major, attr):
        below = (major - 1) * VERSION_MAJOR_SCALE
        at = major * VERSION_MAJOR_SCALE
        assert getattr(features_for(below), attr) is False
        assert getattr(features_for(at), attr) is True

    def test_major_version_argument_wins_over_derived(self):
        """POFModel stores both fields, so the caller's value must be honoured.

        A model whose major_version disagrees with its version keeps whatever
        layout major_version implies -- that is what the pre-matrix code did at
        each call site, and the writer must not silently switch layout.
        """
        assert features_for(2300, major_version=21).vertex_alpha is False
        assert features_for(2100, major_version=23).vertex_alpha is True

    def test_features_are_frozen(self):
        """The matrix is spec, not state: nothing should mutate it at runtime."""
        f = features_for(OBJFILE_VERSION)
        with pytest.raises(Exception):
            f.vertex_alpha = False

    def test_model_property_matches_function(self):
        model = POFModel(version=2200, major_version=22)
        assert model.features == features_for(2200, 22)

    def test_newest_version_has_every_feature(self):
        f = features_for(OBJFILE_VERSION)
        assert all(
            [f.geometric_center, f.gunpoint_parent, f.lightmap_uv,
             f.timed_anim, f.vertex_alpha]
        )

    def test_oldest_accepted_version_is_covered(self):
        """features_for must answer for the oldest file the parser accepts."""
        f = features_for(MIN_OBJFILE_VERSION)
        assert f.geometric_center is True
        assert f.vertex_alpha is False


def _model_at(version):
    """Build a small but complete model targeting ``version``."""
    model = POFModel(
        version=version, major_version=version // VERSION_MAJOR_SCALE
    )
    model.textures = ["Hull"]

    sm = Submodel(index=0, parent=-1, name="Root")
    sm.geometric_center = Vector3(0.5, 0.5, 0.5)
    for pos in [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]:
        v = SubmodelVertex()
        v.position = Vector3(*pos)
        v.normal = Vector3(0.0, 0.0, 1.0)
        v.alpha = 1.0
        sm.vertices.append(v)
    face = ModelFace()
    face.normal = Vector3(0.0, 0.0, 1.0)
    face.textured = True
    face.texnum = 0
    for i, (u, v_) in enumerate([(0, 0), (1, 0), (1, 1), (0, 1)]):
        face.vertices.append(FaceVertex(index=i, u=float(u), v=float(v_)))
    sm.faces.append(face)
    model.submodels.append(sm)

    bank = GunBank()
    bank.parent = 0
    bank.point = Vector3(1.0, 2.0, 3.0)
    bank.normal = Vector3(0.0, 0.0, 1.0)
    model.gun_banks.append(bank)

    return model


class TestPerVersionRoundtrip:
    """Prove reader/writer agreement at every feature boundary.

    A layout disagreement does not fail politely: the reader walks off into the
    next field and the damage shows up as wrong geometry, so these assert the
    payload, not just that parsing returned.
    """

    @pytest.mark.parametrize("version", BOUNDARY_VERSIONS)
    def test_geometry_survives(self, version):
        result = parse_pof(write_pof(_model_at(version)))
        assert result.version == version
        assert len(result.submodels) == 1
        sm = result.submodels[0]
        assert sm.name == "Root"
        assert len(sm.vertices) == 4
        assert len(sm.faces) == 1
        assert [v.position.as_tuple() for v in sm.vertices] == [
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)
        ]

    @pytest.mark.parametrize("version", BOUNDARY_VERSIONS)
    def test_uvs_survive(self, version):
        """UVs sit immediately before the version-gated lightmap floats, so a
        lightmap_uv disagreement corrupts the *next* face's UVs."""
        result = parse_pof(write_pof(_model_at(version)))
        uvs = [(fv.u, fv.v) for fv in result.submodels[0].faces[0].vertices]
        assert uvs == [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

    @pytest.mark.parametrize("version", BOUNDARY_VERSIONS)
    def test_textures_survive(self, version):
        result = parse_pof(write_pof(_model_at(version)))
        assert result.textures == ["Hull"]
        assert result.submodels[0].faces[0].texnum == 0

    @pytest.mark.parametrize("version", BOUNDARY_VERSIONS)
    def test_gun_point_survives(self, version):
        """Below VERSION_GUNPOINT_PARENT the parent index is not stored, so it
        comes back as 0 rather than being read out of the following vector."""
        result = parse_pof(write_pof(_model_at(version)))
        assert len(result.gun_banks) == 1
        assert result.gun_banks[0].point.as_tuple() == (1.0, 2.0, 3.0)

    @pytest.mark.parametrize("version", BOUNDARY_VERSIONS)
    def test_alpha_matches_the_matrix(self, version):
        """Alpha is only stored from MAJOR_VERSION_VERTEX_ALPHA; older files
        load fully opaque rather than carrying a garbage float."""
        result = parse_pof(write_pof(_model_at(version)))
        assert all(v.alpha == 1.0 for v in result.submodels[0].vertices)

    @pytest.mark.parametrize("version", BOUNDARY_VERSIONS)
    def test_whole_stream_is_consumed(self, version):
        """Every byte written must be accounted for by the reader.

        Chunk headers carry their own length and the parser seeks to each chunk
        end, so a size disagreement inside a chunk would otherwise be silently
        skipped over instead of surfacing.
        """
        data = write_pof(_model_at(version))
        result = parse_pof(data)
        assert write_pof(result) == data
