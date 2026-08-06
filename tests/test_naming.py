"""
Unit tests for the material-name to texture-ID mapping (descent3_plugin/naming.py).

A Blender material's name is the Descent 3 texture ID. Blender uniquifies
datablock names with a ``.NNN`` suffix, so a second asset sharing a texture name
produced ``Hull.001`` -- and exporting that wrote a texture ID no bitmap
matches, which broke the round trip.

The interesting rule is not "strip the suffix" but *when* it is safe to: only
when the base name is itself a material in the scene, which proves the duplicate
is being reunited with the original it was cloned from rather than a texture
name being invented.

Run with: python -m pytest tests/test_naming.py -v
"""

import pytest

from descent3_plugin.naming import (
    TEXTURE_FORMAT_NONE,
    TEXTURE_FORMATS,
    base_material_name,
    blender_file_format,
    is_duplicate_material_name,
    resolve_texture_names,
    texture_filename,
    texture_format_extension,
)


class TestSuffixDetection:
    @pytest.mark.parametrize(
        "name", ["Hull.001", "Hull.002", "Hull.999", "Hull.1000", "Hull.1001",
                 "a.000", "some_long_name.042"],
    )
    def test_detects_blender_suffixes(self, name):
        assert is_duplicate_material_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "Hull", "Turret", "hull_01",
            "metal.plate",   # a dot, but not digits
            "panel.01",      # two digits: Blender never generates these
            "panel.1",       # one digit
            "v2.0",
            "tex.abc",
            "Hull.",
            "Hull.001.png",  # suffix not at the end
            "",
        ],
    )
    def test_leaves_authored_names_alone(self, name):
        assert is_duplicate_material_name(name) is False

    def test_suffix_is_not_limited_to_three_digits(self):
        """Measured in Blender 5.2: past .999 the counter is not zero-padded,
        so it runs .1000, .1001 and so on. A three-digit-only rule would miss
        every duplicate past the thousandth."""
        assert is_duplicate_material_name("X.1000") is True
        assert base_material_name("X.1000") == "X"

    def test_three_digits_is_the_lower_boundary(self):
        """Blender never generates a one- or two-digit suffix, so those can
        only be author intent."""
        assert base_material_name("x.01") == "x.01"
        assert base_material_name("x.001") == "x"


class TestBaseName:
    def test_strips_the_suffix(self):
        assert base_material_name("Hull.001") == "Hull"

    def test_only_the_trailing_suffix(self):
        assert base_material_name("Hull.001.002") == "Hull.001"

    def test_bare_suffix_is_not_reduced_to_empty(self):
        """An empty texture name is written to the file as a bare NUL, which the
        engine cannot resolve and which gives the user nothing to search for."""
        assert base_material_name(".001") == ".001"

    def test_is_idempotent(self):
        once = base_material_name("Hull.001")
        assert base_material_name(once) == once


class TestResolveMergesOnlyWhenTheOriginalExists:
    def test_duplicate_merges_onto_its_original(self):
        mapping, warnings = resolve_texture_names(["Hull", "Hull.001"])
        assert mapping == {"Hull": "Hull", "Hull.001": "Hull"}
        assert len(warnings) == 1
        assert "Hull.001" in warnings[0] and "Hull" in warnings[0]

    def test_several_duplicates_merge_and_warn_once(self):
        mapping, warnings = resolve_texture_names(["Hull", "Hull.001", "Hull.002"])
        assert set(mapping.values()) == {"Hull"}
        assert len(warnings) == 1

    def test_orphan_duplicate_is_kept_verbatim(self):
        """The original was deleted. Collapsing onto 'Hull' would invent a
        texture the scene never contained and merge two distinct materials."""
        mapping, warnings = resolve_texture_names(["Hull.001", "Hull.002"])
        assert mapping == {"Hull.001": "Hull.001", "Hull.002": "Hull.002"}
        assert len(warnings) == 2
        assert all("no material 'Hull' exists" in w for w in warnings)

    def test_orphan_warning_names_the_consequence(self):
        _, warnings = resolve_texture_names(["Hull.001"])
        assert "Descent 3 will not find a texture called 'Hull.001'" in warnings[0]

    def test_distinct_textures_are_untouched(self):
        mapping, warnings = resolve_texture_names(["Hull", "Turret"])
        assert mapping == {"Hull": "Hull", "Turret": "Turret"}
        assert warnings == []

    def test_authored_dot_numeric_name_is_untouched(self):
        mapping, warnings = resolve_texture_names(["panel", "panel.01"])
        assert mapping == {"panel": "panel", "panel.01": "panel.01"}
        assert warnings == []

    def test_every_input_appears_in_the_mapping(self):
        """Callers resolve through the mapping alone, so a missing key would
        silently export a face as untextured."""
        names = ["Hull", "Hull.001", "Turret.002", "panel.01", ".001", ""]
        mapping, _ = resolve_texture_names(names)
        assert set(mapping) == set(names)

    def test_mapping_is_stable_regardless_of_input_order(self):
        a, _ = resolve_texture_names(["Hull", "Hull.001"])
        b, _ = resolve_texture_names(["Hull.001", "Hull"])
        assert a == b

    def test_empty_input(self):
        assert resolve_texture_names([]) == ({}, [])

    def test_accepts_a_set(self):
        """Export passes a set of material names."""
        mapping, _ = resolve_texture_names({"Hull", "Hull.001"})
        assert mapping["Hull.001"] == "Hull"


class TestNoTextureNameIsEverEmpty:
    @pytest.mark.parametrize("names", [[".001"], [".001", ""], ["", "Hull"]])
    def test_resolved_names_are_never_shorter_than_the_input(self, names):
        mapping, _ = resolve_texture_names(names)
        for original, resolved in mapping.items():
            assert resolved or not original, (original, resolved)


class TestTextureExportFormats:
    """The Blender format identifier and the file extension are not the same
    string -- TARGA writes .tga -- so the pairing is kept in one table."""

    def test_supported_formats(self):
        assert set(TEXTURE_FORMATS) == {"PNG", "TARGA"}

    @pytest.mark.parametrize(
        "fmt,blender,ext", [("PNG", "PNG", ".png"), ("TARGA", "TARGA", ".tga")]
    )
    def test_mapping(self, fmt, blender, ext):
        assert blender_file_format(fmt) == blender
        assert texture_format_extension(fmt) == ext

    def test_targa_extension_is_not_its_identifier(self):
        """The trap this table exists to prevent."""
        assert texture_format_extension("TARGA") != ".targa"

    def test_filename_uses_the_texture_id_verbatim(self):
        """Import searches for the TXTR name, so the stem must not be altered."""
        assert texture_filename("Hull", "PNG") == "Hull.png"
        assert texture_filename("some_texture", "TARGA") == "some_texture.tga"

    def test_filename_keeps_dots_in_the_texture_name(self):
        """A Descent texture ID is not a filename; a dot in it is not an
        extension to be replaced."""
        assert texture_filename("panel.2", "PNG") == "panel.2.png"

    def test_none_is_not_a_writable_format(self):
        assert TEXTURE_FORMAT_NONE not in TEXTURE_FORMATS

    def test_ogf_is_absent_until_it_can_be_encoded(self):
        """Offering a format Blender cannot write would produce files the game
        rejects. The entry lands with an encoder, not before one."""
        assert "OGF" not in TEXTURE_FORMATS

    @pytest.mark.parametrize("fmt", ["ogf", "png", "jpeg", "", "NOPE"])
    def test_unsupported_formats_raise(self, fmt):
        with pytest.raises(KeyError):
            texture_format_extension(fmt)
