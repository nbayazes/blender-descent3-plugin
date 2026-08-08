"""
Unit tests for the bpy-free math primitives (descent3_plugin/mathutil.py).

Run with: python -m pytest tests/test_mathutil.py -v
"""

import pytest

from descent3_plugin.mathutil import (
    NORMALIZE_EPSILON,
    Vector3,
    blender_to_descent,
    descent_to_blender,
)


class TestVector3:
    def test_creation(self):
        v = Vector3(1.0, 2.0, 3.0)
        assert v.x == 1.0
        assert v.y == 2.0
        assert v.z == 3.0

    def test_defaults_to_origin(self):
        v = Vector3()
        assert v.as_tuple() == (0.0, 0.0, 0.0)

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

    def test_add_returns_a_new_vector(self):
        v1 = Vector3(1, 2, 3)
        v2 = Vector3(4, 5, 6)
        result = v1 + v2
        assert result is not v1 and result is not v2
        assert v1.as_tuple() == (1, 2, 3)

    def test_magnitude(self):
        v = Vector3(3, 4, 0)
        assert v.magnitude() == pytest.approx(5.0)

    def test_normalized(self):
        v = Vector3(3, 0, 0)
        n = v.normalized()
        assert n.x == pytest.approx(1.0)
        assert n.y == pytest.approx(0.0)
        assert n.z == pytest.approx(0.0)

    def test_normalized_is_unit_length(self):
        n = Vector3(1, 2, 3).normalized()
        assert n.magnitude() == pytest.approx(1.0)

    def test_normalized_zero(self):
        v = Vector3(0, 0, 0)
        n = v.normalized()
        assert n.x == 0
        assert n.y == 0
        assert n.z == 0

    def test_normalized_below_epsilon_is_zero(self):
        """Degenerate normals are common in real models, so this yields zero
        rather than dividing by something near zero."""
        v = Vector3(NORMALIZE_EPSILON / 10, 0, 0)
        assert v.normalized().as_tuple() == (0, 0, 0)

    def test_as_tuple(self):
        v = Vector3(1, 2, 3)
        assert v.as_tuple() == (1.0, 2.0, 3.0)

    def test_equality_is_by_value(self):
        """``Vector3`` is a dataclass, and the round-trip tests compare by value."""
        assert Vector3(1, 2, 3) == Vector3(1, 2, 3)
        assert Vector3(1, 2, 3) != Vector3(1, 2, 4)


class TestCoordinateConversion:
    """Descent 3 is right-handed Y-up; Blender is right-handed Z-up.

    Both being right-handed makes the difference a rotation, not a mirror, so
    faces need no winding reversal and normals transform exactly like positions.
    """

    def test_up_maps_to_up(self):
        """Descent's +Y (up) must become Blender's +Z (up)."""
        assert descent_to_blender(Vector3(0, 1, 0)).as_tuple() == (0, 0, 1)

    def test_blender_up_maps_back(self):
        assert blender_to_descent(Vector3(0, 0, 1)).as_tuple() == (0, 1, 0)

    def test_right_is_unchanged(self):
        """X is the rotation axis, so it is the one component that stays put."""
        assert descent_to_blender(Vector3(1, 0, 0)).as_tuple() == (1, 0, 0)
        assert blender_to_descent(Vector3(1, 0, 0)).as_tuple() == (1, 0, 0)

    def test_descent_back_maps_to_blender_minus_y(self):
        """Blender's front view looks along +Y, so -Y is toward the viewer."""
        assert descent_to_blender(Vector3(0, 0, 1)).as_tuple() == (0, -1, 0)

    @pytest.mark.parametrize(
        "v", [(1, 2, 3), (-4.5, 0.25, 9.75), (0, 0, 0), (-1, -1, -1)]
    )
    def test_round_trip_is_exact(self, v):
        """A round trip must come back unchanged, or a Blender edit that
        touches nothing would still rewrite every vertex."""
        original = Vector3(*v)
        assert blender_to_descent(descent_to_blender(original)).as_tuple() == v
        assert descent_to_blender(blender_to_descent(original)).as_tuple() == v

    @pytest.mark.parametrize(
        "v", [(1, 2, 3), (-4.5, 0.25, 9.75), (0.001, -0.002, 0.003)]
    )
    def test_length_is_preserved(self, v):
        """A rotation cannot change magnitude. If this fails the transform is a
        scale or a shear, and radii and bounding spheres would be wrong."""
        original = Vector3(*v)
        assert descent_to_blender(original).magnitude() == pytest.approx(
            original.magnitude()
        )

    def test_handedness_is_preserved(self):
        """Cross products must survive, or winding order flips and every face
        ends up inside out. x cross y = z in both spaces, so converting the
        operands and crossing must equal converting the result.
        """
        def cross(a, b):
            return Vector3(
                a.y * b.z - a.z * b.y,
                a.z * b.x - a.x * b.z,
                a.x * b.y - a.y * b.x,
            )

        a, b = Vector3(1, 2, 3), Vector3(4, 5, 6)
        converted = cross(descent_to_blender(a), descent_to_blender(b))
        expected = descent_to_blender(cross(a, b))
        assert converted.as_tuple() == pytest.approx(expected.as_tuple())

    def test_is_a_pure_rotation_about_x(self):
        """Ninety degrees about X, applied four times, is the identity."""
        v = Vector3(1, 2, 3)
        r = v
        for _ in range(4):
            r = descent_to_blender(r)
        assert r.as_tuple() == pytest.approx(v.as_tuple())

    def test_accepts_anything_with_xyz(self):
        """Export hands it a mathutils.Vector, which is duck-typed here."""
        class FakeVector:
            x, y, z = 1.0, 2.0, 3.0

        assert blender_to_descent(FakeVector()).as_tuple() == (1.0, 3.0, -2.0)
