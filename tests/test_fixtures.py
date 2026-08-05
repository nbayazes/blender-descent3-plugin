"""
Tests that exercise the parser + texture resolution together against a committed
fixture: a real-format .oof model plus the PNG images it references, all living
in one folder (as a model + its textures would on disk).

Regenerate the fixture with: python tests/generate_fixtures.py
Run: python -m pytest tests/test_fixtures.py -v
"""

import os

from descent3_plugin.poformat import parse_pof
from descent3_plugin.texutil import find_texture_image

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "textured_model")
OOF = os.path.join(FIXTURE_DIR, "textured_cube.oof")


def _load():
    with open(OOF, "rb") as f:
        return parse_pof(f.read())


def test_fixture_files_exist():
    assert os.path.isfile(OOF)
    assert os.path.isfile(os.path.join(FIXTURE_DIR, "Hull.png"))
    assert os.path.isfile(os.path.join(FIXTURE_DIR, "Turret.png"))


def test_fixture_model_parses():
    model = _load()
    assert model.version == 2300
    assert model.textures == ["Hull", "Turret"]
    assert len(model.submodels) == 1
    assert len(model.submodels[0].vertices) == 8
    assert len(model.submodels[0].faces) == 6


def test_texture_names_have_no_trailing_null():
    # Real-format strings are NUL-terminated; parsing must strip the NUL so the
    # name matches the on-disk file stem.
    model = _load()
    for name in model.textures:
        assert "\x00" not in name
        assert name == name.strip()


def test_every_texture_resolves_to_a_png():
    model = _load()
    for name in model.textures:
        path = find_texture_image(name, [FIXTURE_DIR])
        assert path is not None, f"texture {name!r} did not resolve to a file"
        assert os.path.isfile(path)
        assert os.path.splitext(path)[1].lower() == ".png"


def test_all_faces_reference_valid_textures():
    model = _load()
    for sm in model.submodels:
        for face in sm.faces:
            assert face.textured
            assert 0 <= face.texnum < len(model.textures)


def test_faces_split_between_both_textures():
    # The fixture deliberately uses both textures so material assignment is
    # meaningfully exercised (3 faces each).
    model = _load()
    used = {f.texnum for f in model.submodels[0].faces}
    assert used == {0, 1}
