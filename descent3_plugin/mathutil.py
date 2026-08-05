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
