"""
Unit tests for texture-resolution helpers (descent3_plugin/texutil.py).

These cover the pure logic that maps a Descent 3 texture name to an image file
on disk, without requiring Blender.
Run with: python -m pytest tests/test_texutil.py -v
"""

import os

from descent3_plugin.texutil import (
    IMAGE_EXTENSIONS,
    clean_texture_dir,
    find_texture_image,
)


class TestCleanTextureDir:
    def test_empty(self):
        assert clean_texture_dir("") == ""
        assert clean_texture_dir(None) == ""

    def test_plain_path(self):
        assert clean_texture_dir(r"C:\tex") == r"C:\tex"

    def test_surrounding_whitespace(self):
        assert clean_texture_dir("   C:\\tex  ") == r"C:\tex"

    def test_double_quotes(self):
        # Windows "Copy as path" wraps in double quotes
        assert clean_texture_dir('"C:\\my tex\\folder"') == r"C:\my tex\folder"

    def test_single_quotes(self):
        assert clean_texture_dir("'/home/user/tex'") == "/home/user/tex"

    def test_quotes_and_whitespace(self):
        assert clean_texture_dir('   "C:\\tex"  ') == r"C:\tex"

    def test_internal_spaces_preserved(self):
        assert clean_texture_dir('"C:\\a b\\c d"') == r"C:\a b\c d"


class TestFindTextureImage:
    def test_exact_match(self, tmp_path):
        (tmp_path / "Hull.png").write_bytes(b"x")
        result = find_texture_image("Hull", [str(tmp_path)])
        assert result == str(tmp_path / "Hull.png")

    def test_not_found(self, tmp_path):
        assert find_texture_image("Missing", [str(tmp_path)]) is None

    def test_case_insensitive(self, tmp_path):
        # On a case-insensitive FS (Windows) the exact-stem fast path resolves
        # this; on a case-sensitive one the listing fallback does.
        (tmp_path / "BlackPanel.PNG").write_bytes(b"x")
        result = find_texture_image("blackpanel", [str(tmp_path)])
        assert result is not None
        assert os.path.isfile(result)
        assert os.path.basename(result).lower() == "blackpanel.png"

    def test_extension_priority(self, tmp_path):
        # .png comes before .bmp in IMAGE_EXTENSIONS -> png wins
        (tmp_path / "Tex.bmp").write_bytes(b"x")
        (tmp_path / "Tex.png").write_bytes(b"x")
        assert find_texture_image("Tex", [str(tmp_path)]) == str(tmp_path / "Tex.png")

    def test_directory_priority(self, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        (first / "Tex.png").write_bytes(b"x")
        (second / "Tex.png").write_bytes(b"x")
        result = find_texture_image("Tex", [str(first), str(second)])
        assert result == str(first / "Tex.png")

    def test_falls_through_to_second_dir(self, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        (second / "Tex.png").write_bytes(b"x")
        result = find_texture_image("Tex", [str(first), str(second)])
        assert result == str(second / "Tex.png")

    def test_skips_nonexistent_and_empty_dirs(self, tmp_path):
        (tmp_path / "Tex.png").write_bytes(b"x")
        dirs = ["", str(tmp_path / "does_not_exist"), None, str(tmp_path)]
        assert find_texture_image("Tex", dirs) == str(tmp_path / "Tex.png")

    def test_ignores_unknown_extension(self, tmp_path):
        (tmp_path / "Tex.txt").write_bytes(b"x")
        assert find_texture_image("Tex", [str(tmp_path)]) is None

    def test_all_supported_extensions(self, tmp_path):
        for i, ext in enumerate(IMAGE_EXTENSIONS):
            name = f"tex{i}"
            (tmp_path / (name + ext)).write_bytes(b"x")
            assert find_texture_image(name, [str(tmp_path)]) == str(
                tmp_path / (name + ext)
            )
