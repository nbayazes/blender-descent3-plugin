"""
Generate a self-contained texture test fixture: a small textured model plus the
PNG images it references, so importer/texture-resolution logic can be tested
against committed data.

Unlike ``generate_mock_pof.py`` (which hand-writes bytes), this uses the real
``poformat.write_pof`` writer so the .oof matches the genuine on-disk format
(length-prefixed, NUL-terminated strings, v23 layout).

One-shot generator: the output is tracked in git and the tests read those
checked-in files directly, so re-run this only to change the fixture itself.

Run:  python tests/generate_fixtures.py
Output: tests/fixtures/textured_model/{textured_cube.oof, Hull.png, Turret.png}
"""

import os
import struct
import sys
import zlib

# Run as a script, so the repo root is not on sys.path automatically.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from descent3_plugin.mathutil import Vector3  # noqa: E402
from descent3_plugin.poformat import (  # noqa: E402
    POFModel,
    Submodel,
    SubmodelVertex,
    ModelFace,
    FaceVertex,
    write_pof,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "textured_model")

# Texture name -> solid RGBA color for its generated PNG
TEXTURES = [("Hull", (200, 60, 60, 255)), ("Turret", (60, 90, 200, 255))]

# Unit cube centered on the origin
_V = [
    (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5),
    (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5),
]
# (vertex indices, face normal, texnum) — 3 faces use Hull(0), 3 use Turret(1)
_FACES = [
    ((0, 1, 2, 3), (0, 0, -1), 0),   # bottom  Hull
    ((4, 7, 6, 5), (0, 0, 1), 0),    # top     Hull
    ((0, 4, 5, 1), (0, -1, 0), 0),   # front   Hull
    ((3, 2, 6, 7), (0, 1, 0), 1),    # back    Turret
    ((0, 3, 7, 4), (-1, 0, 0), 1),   # left    Turret
    ((1, 5, 6, 2), (1, 0, 0), 1),    # right   Turret
]
_QUAD_UV = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def write_png(path, rgba, size=4):
    """Write a minimal valid solid-color RGBA PNG."""
    def chunk(typ, data):
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    pixel = bytes(rgba)
    raw = b"".join(b"\x00" + pixel * size for _ in range(size))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(raw)))
        f.write(chunk(b"IEND", b""))


def build_model():
    model = POFModel(version=2300, major_version=23)
    model.textures = [name for name, _ in TEXTURES]
    model.min_bound = Vector3(-0.5, -0.5, -0.5)
    model.max_bound = Vector3(0.5, 0.5, 0.5)
    model.radius = 0.8660254  # sqrt(0.75)

    sm = Submodel(index=0, parent=-1, name="cube")
    sm.radius = model.radius
    for x, y, z in _V:
        n = Vector3(x, y, z).normalized()
        sm.vertices.append(SubmodelVertex(position=Vector3(x, y, z), normal=n))

    for idxs, normal, texnum in _FACES:
        face = ModelFace(normal=Vector3(*normal), textured=True, texnum=texnum)
        for vi, (u, v) in zip(idxs, _QUAD_UV):
            face.vertices.append(FaceVertex(index=vi, u=u, v=v))
        sm.faces.append(face)

    model.submodels.append(sm)
    return model


def main():
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, "textured_cube.oof"), "wb") as f:
        f.write(write_pof(build_model()))
    for name, color in TEXTURES:
        write_png(os.path.join(FIXTURE_DIR, name + ".png"), color)
    print(f"Wrote fixture (model + {len(TEXTURES)} PNGs) to {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
