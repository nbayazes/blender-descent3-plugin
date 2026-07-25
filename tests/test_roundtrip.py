"""
Full-fidelity write -> parse round-trip tests.

``test_poformat.py`` covers the reader in depth but only checks *counts* on the
writer side, so field-level corruption goes unnoticed. These tests assert that
every value a submodel carries -- positions, normals, alpha, per-face-vertex
UVs, texture indices, offsets, hierarchy -- comes back unchanged after a trip
through ``write_pof`` and ``parse_pof``.

Run with: python -m pytest tests/test_roundtrip.py -v
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "descent3_importer"))

from poformat import (  # noqa: E402
    AttachPoint,
    Color,
    FaceVertex,
    GunBank,
    Keyframe,
    ModelFace,
    POFModel,
    Submodel,
    SubmodelVertex,
    Vector3,
    parse_pof,
    write_pof,
)

# POF stores floats as 32-bit, so exact equality only holds for values that are
# representable there. Test data below sticks to binary fractions where it can;
# this tolerance covers the rest.
TOL = dict(rel=1e-6, abs=1e-6)


def approx_vec(v: Vector3):
    return pytest.approx(v.as_tuple(), **TOL)


def flat(pairs):
    """Flatten [(u, v), ...] to [u, v, ...].

    ``pytest.approx`` only applies its tolerance to a flat sequence of numbers;
    given a list of tuples it silently falls back to exact comparison, which
    would make these assertions pass or fail on float32 rounding rather than on
    the behaviour under test.
    """
    return [component for pair in pairs for component in pair]


def roundtrip(model: POFModel) -> POFModel:
    """Write a model to bytes and parse it back."""
    return parse_pof(write_pof(model))


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def make_quad(z=0.0, texnum=0, uvs=None):
    """A 4-vertex square face plus its vertices, at height ``z``."""
    corners = [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
    verts = [
        SubmodelVertex(
            position=Vector3(x, y, z),
            normal=Vector3(0.0, 0.0, 1.0),
            alpha=1.0,
        )
        for x, y in corners
    ]
    if uvs is None:
        uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    face = ModelFace(
        normal=Vector3(0.0, 0.0, 1.0),
        textured=True,
        texnum=texnum,
        vertices=[FaceVertex(index=i, u=u, v=v) for i, (u, v) in enumerate(uvs)],
    )
    return verts, face


def make_rich_model() -> POFModel:
    """A model exercising hierarchy, offsets, props, mixed face types and alpha."""
    model = POFModel(version=2300, major_version=23)
    model.textures = ["Hull", "Turret", "Glass"]
    model.radius = 2.5
    model.min_bound = Vector3(-1.0, -1.0, -0.5)
    model.max_bound = Vector3(1.0, 1.0, 1.5)

    # Root: textured quad.
    verts, face = make_quad(z=0.0, texnum=0)
    root = Submodel(
        index=0,
        parent=-1,
        name="hull",
        props="",
        offset=Vector3(0.0, 0.0, 0.0),
        radius=1.5,
        geometric_center=Vector3(0.25, -0.5, 0.75),
        normal=Vector3(0.0, 0.0, 1.0),
        point=Vector3(0.5, 0.5, 0.0),
        movement_type=-1,
        movement_axis=0,
        vertices=verts,
        faces=[face],
    )

    # Child: offset from parent, second texture, non-default movement + props.
    verts, face = make_quad(z=0.5, texnum=1)
    child = Submodel(
        index=1,
        parent=0,
        name="turret",
        props="$fov=180, 2.0, 1.0",
        offset=Vector3(0.0, 0.25, 1.0),
        radius=0.75,
        geometric_center=Vector3(0.0, 0.0, 0.5),
        movement_type=1,
        movement_axis=2,
        vertices=verts,
        faces=[face],
    )

    # Grandchild: an untextured (solid colour) face and per-vertex alpha.
    verts, _ = make_quad(z=1.0)
    for v, a in zip(verts, (1.0, 0.75, 0.5, 0.25)):
        v.alpha = a
    solid = ModelFace(
        normal=Vector3(0.0, 1.0, 0.0),
        textured=False,
        color=Color(1.0, 128 / 255.0, 64 / 255.0),
        vertices=[FaceVertex(index=i, u=0.0, v=0.0) for i in range(4)],
    )
    glass = Submodel(
        index=2,
        parent=1,
        name="glass",
        offset=Vector3(-0.5, 0.0, 0.25),
        radius=0.5,
        vertices=verts,
        faces=[solid],
    )

    model.submodels = [root, child, glass]
    return model


# ---------------------------------------------------------------------------
# Model-level fields
# ---------------------------------------------------------------------------

class TestModelFields:
    @pytest.fixture
    def result(self):
        return roundtrip(make_rich_model())

    def test_version(self, result):
        assert result.version == 2300
        assert result.major_version == 23

    def test_textures(self, result):
        assert result.textures == ["Hull", "Turret", "Glass"]

    def test_radius(self, result):
        assert result.radius == pytest.approx(2.5, **TOL)

    def test_bounds(self, result):
        assert result.min_bound.as_tuple() == approx_vec(Vector3(-1.0, -1.0, -0.5))
        assert result.max_bound.as_tuple() == approx_vec(Vector3(1.0, 1.0, 1.5))

    def test_submodel_count(self, result):
        assert len(result.submodels) == 3


# ---------------------------------------------------------------------------
# Submodel fields
# ---------------------------------------------------------------------------

class TestSubmodelFields:
    @pytest.fixture
    def pairs(self):
        original = make_rich_model()
        result = roundtrip(original)
        by_index = {sm.index: sm for sm in result.submodels}
        return [(sm, by_index[sm.index]) for sm in original.submodels]

    def test_indices_and_parents(self, pairs):
        for want, got in pairs:
            assert got.index == want.index
            assert got.parent == want.parent

    def test_names(self, pairs):
        assert [got.name for _, got in pairs] == ["hull", "turret", "glass"]

    def test_props(self, pairs):
        for want, got in pairs:
            assert got.props == want.props

    def test_offsets(self, pairs):
        """Offset-from-parent drives object placement; losing it collapses the model."""
        for want, got in pairs:
            assert got.offset.as_tuple() == approx_vec(want.offset)

    def test_radius(self, pairs):
        for want, got in pairs:
            assert got.radius == pytest.approx(want.radius, **TOL)

    def test_geometric_center(self, pairs):
        for want, got in pairs:
            assert got.geometric_center.as_tuple() == approx_vec(want.geometric_center)

    def test_separation_plane(self, pairs):
        for want, got in pairs:
            assert got.normal.as_tuple() == approx_vec(want.normal)
            assert got.point.as_tuple() == approx_vec(want.point)

    def test_movement(self, pairs):
        for want, got in pairs:
            assert got.movement_type == want.movement_type
            assert got.movement_axis == want.movement_axis

    def test_hierarchy(self, pairs):
        _, root = pairs[0]
        _, child = pairs[1]
        assert root.children == [1]
        assert child.children == [2]


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

class TestGeometry:
    @pytest.fixture
    def result(self):
        return roundtrip(make_rich_model())

    def test_vertex_positions(self, result):
        want = make_rich_model().submodels[0].vertices
        got = result.submodels[0].vertices
        assert len(got) == len(want)
        for w, g in zip(want, got):
            assert g.position.as_tuple() == approx_vec(w.position)

    def test_vertex_normals(self, result):
        for sm in result.submodels:
            for v in sm.vertices:
                assert v.normal.as_tuple() == approx_vec(Vector3(0.0, 0.0, 1.0))

    def test_vertex_alpha(self, result):
        glass = next(sm for sm in result.submodels if sm.name == "glass")
        assert [v.alpha for v in glass.vertices] == pytest.approx(
            [1.0, 0.75, 0.5, 0.25], **TOL
        )

    def test_face_normals(self, result):
        glass = next(sm for sm in result.submodels if sm.name == "glass")
        assert glass.faces[0].normal.as_tuple() == approx_vec(Vector3(0.0, 1.0, 0.0))

    def test_face_vertex_indices(self, result):
        for sm in result.submodels:
            for face in sm.faces:
                assert [fv.index for fv in face.vertices] == [0, 1, 2, 3]

    def test_texnums(self, result):
        by_name = {sm.name: sm for sm in result.submodels}
        assert by_name["hull"].faces[0].textured
        assert by_name["hull"].faces[0].texnum == 0
        assert by_name["turret"].faces[0].texnum == 1

    def test_untextured_face_keeps_colour(self, result):
        """Solid-colour faces store 8-bit RGB, so allow one quantisation step."""
        glass = next(sm for sm in result.submodels if sm.name == "glass")
        face = glass.faces[0]
        assert not face.textured
        want = (1.0, 128 / 255.0, 64 / 255.0)
        for got_c, want_c in zip((face.color.r, face.color.g, face.color.b), want):
            assert got_c == pytest.approx(want_c, abs=1.0 / 255.0)


# ---------------------------------------------------------------------------
# UVs -- the part most likely to silently corrupt
# ---------------------------------------------------------------------------

class TestFunkyUVs:
    def _model_with_uvs(self, uvs):
        model = POFModel(version=2300, major_version=23)
        model.textures = ["Hull"]
        verts, face = make_quad(uvs=uvs)
        model.submodels = [
            Submodel(index=0, parent=-1, name="hull", vertices=verts, faces=[face])
        ]
        return model

    def _uvs_after_roundtrip(self, uvs):
        result = roundtrip(self._model_with_uvs(uvs))
        return [(fv.u, fv.v) for fv in result.submodels[0].faces[0].vertices]

    def test_basic_uvs(self):
        uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        assert flat(self._uvs_after_roundtrip(uvs)) == pytest.approx(flat(uvs), **TOL)

    def test_negative_uvs(self):
        """Descent 3 tiles textures; negative UVs are legal and common."""
        uvs = [(-1.0, -0.5), (-0.25, -2.0), (0.0, -4.0), (-3.5, 0.0)]
        assert flat(self._uvs_after_roundtrip(uvs)) == pytest.approx(flat(uvs), **TOL)

    def test_uvs_beyond_one(self):
        uvs = [(2.0, 3.0), (8.5, 0.25), (16.0, 32.0), (1.125, 1.875)]
        assert flat(self._uvs_after_roundtrip(uvs)) == pytest.approx(flat(uvs), **TOL)

    def test_fractional_precision(self):
        """Values with no exact float32 form must still survive to ~7 digits."""
        uvs = [(0.1, 0.2), (0.3, 0.7), (0.123456, 0.987654), (1.0 / 3.0, 2.0 / 3.0)]
        got = flat(self._uvs_after_roundtrip(uvs))
        assert got == pytest.approx(flat(uvs), rel=1e-6, abs=1e-6)

    def test_v_axis_is_not_flipped(self):
        """A round trip must not negate V -- that mirrors every texture."""
        uvs = [(0.0, 0.25), (1.0, 0.5), (1.0, 0.75), (0.0, 1.0)]
        got = self._uvs_after_roundtrip(uvs)
        assert [v for _, v in got] == pytest.approx([0.25, 0.5, 0.75, 1.0], **TOL)
        assert [u for u, _ in got] == pytest.approx([0.0, 1.0, 1.0, 0.0], **TOL)

    def test_uv_seam_same_vertex_different_uvs(self):
        """UVs are per face-vertex, not per vertex: one vertex can carry
        different UVs in different faces. A per-vertex implementation would
        collapse these onto each other.
        """
        model = POFModel(version=2300, major_version=23)
        model.textures = ["Hull"]
        verts = [
            SubmodelVertex(position=Vector3(x, y, 0.0), normal=Vector3(0, 0, 1))
            for x, y in [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        ]
        # Two faces sharing vertices 1 and 2, with deliberately different UVs.
        face_a = ModelFace(
            textured=True,
            texnum=0,
            normal=Vector3(0, 0, 1),
            vertices=[
                FaceVertex(index=0, u=0.0, v=0.0),
                FaceVertex(index=1, u=1.0, v=0.0),
                FaceVertex(index=2, u=1.0, v=1.0),
            ],
        )
        face_b = ModelFace(
            textured=True,
            texnum=0,
            normal=Vector3(0, 0, 1),
            vertices=[
                FaceVertex(index=2, u=0.0, v=1.0),   # same vertex, other UV
                FaceVertex(index=1, u=0.0, v=0.0),   # same vertex, other UV
                FaceVertex(index=3, u=1.0, v=1.0),
            ],
        )
        model.submodels = [
            Submodel(
                index=0, parent=-1, name="hull", vertices=verts, faces=[face_a, face_b]
            )
        ]
        result = roundtrip(model)
        got_a, got_b = result.submodels[0].faces

        assert [fv.index for fv in got_a.vertices] == [0, 1, 2]
        assert flat((fv.u, fv.v) for fv in got_a.vertices) == pytest.approx(
            flat([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]), **TOL
        )

        assert [fv.index for fv in got_b.vertices] == [2, 1, 3]
        assert flat((fv.u, fv.v) for fv in got_b.vertices) == pytest.approx(
            flat([(0.0, 1.0), (0.0, 0.0), (1.0, 1.0)]), **TOL
        )


# ---------------------------------------------------------------------------
# Submodel index handling
# ---------------------------------------------------------------------------

class TestSubmodelIndices:
    """SOBJ chunks carry their own index; nothing guarantees they arrive in
    order or that the set is dense. Real models built by third-party tools hit
    both cases.
    """

    def _two_submodels(self, first_index, second_index):
        model = POFModel(version=2300, major_version=23)
        model.textures = ["Hull"]
        model.submodels = [
            Submodel(index=first_index, parent=-1, name="hull"),
            Submodel(index=second_index, parent=first_index, name="turret"),
        ]
        return model

    def test_out_of_order_chunks(self):
        """Child written before parent."""
        model = POFModel(version=2300, major_version=23)
        model.submodels = [
            Submodel(index=1, parent=0, name="turret"),
            Submodel(index=0, parent=-1, name="hull"),
        ]
        result = roundtrip(model)
        by_index = {sm.index: sm.name for sm in result.submodels}
        assert by_index == {0: "hull", 1: "turret"}

    def test_gap_in_indices(self):
        """Index 1 missing entirely -- must not crash or drop the real ones."""
        result = roundtrip(self._two_submodels(0, 2))
        by_index = {sm.index: sm.name for sm in result.submodels}
        assert by_index == {0: "hull", 2: "turret"}

    def test_children_use_submodel_indices(self):
        """``children`` must hold submodel indices, not list positions -- the
        importer looks parents up by index.
        """
        result = roundtrip(self._two_submodels(0, 2))
        root = next(sm for sm in result.submodels if sm.index == 0)
        assert root.children == [2]


# ---------------------------------------------------------------------------
# Points and attachments
# ---------------------------------------------------------------------------

class TestPointsRoundtrip:
    def test_gun_banks(self):
        model = POFModel(version=2300, major_version=23)
        model.submodels = [Submodel(index=0, parent=-1, name="hull")]
        model.gun_banks = [
            GunBank(parent=0, point=Vector3(1.0, 2.0, 3.0), normal=Vector3(0, 0, 1)),
            GunBank(parent=0, point=Vector3(-1.5, 0.5, 2.25), normal=Vector3(1, 0, 0)),
        ]
        result = roundtrip(model)
        assert len(result.gun_banks) == 2
        for want, got in zip(model.gun_banks, result.gun_banks):
            assert got.parent == want.parent
            assert got.point.as_tuple() == approx_vec(want.point)
            assert got.normal.as_tuple() == approx_vec(want.normal)

    def test_attach_points_keep_normal_and_uvec(self):
        """ATCH stores the normal; NATH stores index + normal + uvec. Reading
        them back swapped silently rotates anything attached to the model.
        """
        model = POFModel(version=2300, major_version=23)
        model.submodels = [Submodel(index=0, parent=-1, name="hull")]
        model.attach_points = [
            AttachPoint(
                parent=0,
                point=Vector3(1.0, 0.0, 0.0),
                normal=Vector3(0.0, 0.0, 1.0),
                has_uvec=True,
                uvec=Vector3(0.0, 1.0, 0.0),
            )
        ]
        result = roundtrip(model)
        ap = result.attach_points[0]
        assert ap.parent == 0
        assert ap.point.as_tuple() == approx_vec(Vector3(1.0, 0.0, 0.0))
        assert ap.normal.as_tuple() == approx_vec(Vector3(0.0, 0.0, 1.0))
        assert ap.has_uvec
        assert ap.uvec.as_tuple() == approx_vec(Vector3(0.0, 1.0, 0.0))

    def test_ground_planes(self):
        model = POFModel(version=2300, major_version=23)
        model.submodels = [Submodel(index=0, parent=-1, name="hull")]
        model.ground_planes = [
            GunBank(parent=0, point=Vector3(0.0, 0.0, -1.0), normal=Vector3(0, 0, 1))
        ]
        result = roundtrip(model)
        assert len(result.ground_planes) == 1
        assert result.ground_planes[0].point.as_tuple() == approx_vec(
            Vector3(0.0, 0.0, -1.0)
        )


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------

def _animated_model(version, major, counts):
    model = POFModel(version=version, major_version=major)
    for i, n in enumerate(counts):
        sm = Submodel(index=i, parent=-1 if i == 0 else 0, name=f"sm{i}")
        sm.num_key_angles = n
        sm.keyframes = [
            Keyframe(axis=Vector3(0.0, 0.0, 1.0), angle=1000 * i + k, rot_start_time=k)
            for k in range(n)
        ]
        model.submodels.append(sm)
    return model


class TestAnimationRoundtrip:
    def test_timed_keyframes(self):
        """v22+ stores a per-submodel key count, so uneven tracks are fine."""
        model = _animated_model(2300, 23, [3, 1])
        result = roundtrip(model)
        assert [len(sm.keyframes) for sm in result.submodels] == [3, 1]
        assert [kf.angle for kf in result.submodels[0].keyframes] == [0, 1, 2]
        assert [kf.angle for kf in result.submodels[1].keyframes] == [1000]

    def test_untimed_file_stays_readable(self):
        """Pre-v22 stores ONE global key count for every submodel (see
        polymodel.cpp ID_ANIM). Writing per-submodel counts against that layout
        produces a file the reader runs off the end of.
        """
        model = _animated_model(2100, 21, [3, 1])
        result = roundtrip(model)
        # The format cannot express uneven counts, so every submodel comes back
        # with the same number of keys -- but it must parse, and the keys that
        # were written must survive in order.
        counts = {len(sm.keyframes) for sm in result.submodels}
        assert counts == {3}
        assert [kf.angle for kf in result.submodels[0].keyframes] == [0, 1, 2]
        assert result.submodels[1].keyframes[0].angle == 1000

    def test_no_animation_writes_no_chunk(self):
        model = POFModel(version=2300, major_version=23)
        model.submodels = [Submodel(index=0, parent=-1, name="hull")]
        result = roundtrip(model)
        assert all(not sm.keyframes for sm in result.submodels)


# ---------------------------------------------------------------------------
# Text encoding
# ---------------------------------------------------------------------------

class TestNames:
    def test_ascii_name_roundtrip(self):
        model = POFModel(version=2300, major_version=23)
        model.submodels = [Submodel(index=0, parent=-1, name="hull_01-detail")]
        assert roundtrip(model).submodels[0].name == "hull_01-detail"

    def test_non_ascii_name_does_not_crash(self):
        """Blender object names are UTF-8; a user can name a submodel anything.
        The writer must degrade gracefully rather than raise.
        """
        model = POFModel(version=2300, major_version=23)
        model.submodels = [Submodel(index=0, parent=-1, name="fusée")]
        result = roundtrip(model)
        assert len(result.submodels) == 1
        assert result.submodels[0].name  # something survived; exact form is lossy

    def test_non_ascii_texture_name_does_not_crash(self):
        model = POFModel(version=2300, major_version=23)
        model.textures = ["café"]
        model.submodels = [Submodel(index=0, parent=-1, name="hull")]
        result = roundtrip(model)
        assert len(result.textures) == 1
