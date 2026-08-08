"""Math primitives shared across the add-on.

Deliberately free of any ``bpy`` dependency, like :mod:`texutil`, so the format
layer and its tests run without Blender. Inside Blender the conversion modules
translate between these types and ``mathutils.Vector`` at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Magnitude below which a vector is treated as degenerate and normalizes to
#: zero rather than dividing by something near zero.
NORMALIZE_EPSILON = 1e-10


@dataclass
class Vector3:
    """A three-component vector, matching the engine's ``vector`` type.

    Deliberately not ``mathutils.Vector``: this type has to stay constructible
    outside Blender so the parser can be unit-tested.
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
            A vector of length 1 in the same direction, or zero if this one is
            shorter than :data:`NORMALIZE_EPSILON`. Degenerate input is common
            in real models, so it yields zero rather than raising.
        """
        m = self.magnitude()
        if m < NORMALIZE_EPSILON:
            return Vector3(0, 0, 0)
        return Vector3(self.x / m, self.y / m, self.z / m)


# Y-up -> Z-up is a single rotation about X: (x, y, z) -> (x, -z, y).
# In Descent's frame +X is right, +Y up and +Z back.
# See docs/design-notes.md.


def descent_to_blender(v) -> Vector3:
    """Convert a Descent 3 (Y-up) vector to Blender (Z-up) space.

    Args:
        v: Anything exposing ``x``, ``y`` and ``z`` -- a :class:`Vector3` from
            the parser, typically.

    Returns:
        The same direction expressed in Blender's axes.
    """
    return Vector3(v.x, -v.z, v.y)


def blender_to_descent(v) -> Vector3:
    """Convert a Blender (Z-up) vector to Descent 3 (Y-up) space.

    The exact inverse of :func:`descent_to_blender`, so a model making the trip
    in both directions comes back bit-for-bit unchanged.

    Args:
        v: Anything exposing ``x``, ``y`` and ``z`` -- a ``mathutils.Vector``
            straight from a mesh, typically.

    Returns:
        The same direction expressed in Descent 3's axes.
    """
    return Vector3(v.x, v.z, -v.y)
