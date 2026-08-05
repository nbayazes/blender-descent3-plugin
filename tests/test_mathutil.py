"""
Unit tests for the bpy-free math primitives (descent3_plugin/mathutil.py).

Run with: python -m pytest tests/test_mathutil.py -v
"""

import pytest

from descent3_plugin.mathutil import NORMALIZE_EPSILON, Vector3


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
