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

That rule is still a guess, and ``metal`` / ``metal.001`` is the case where it
guesses wrong -- both are real Descent textures. So a material imported by this
add-on carries the texture ID it was built from, and the tests below hold the
line that recorded provenance is never overruled by the suffix, while a material
with no provenance behaves exactly as it did before the key existed.

Run with: python -m pytest tests/test_naming.py -v
"""

import pytest

from descent3_plugin.naming import (
    RESERVED_DEVICE_NAMES,
    TEXTURE_FORMAT_NONE,
    TEXTURE_FORMATS,
    base_material_name,
    blender_file_format,
    is_duplicate_material_name,
    is_uniquified_from,
    marker_order_key,
    rejected_texture_name,
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


class TestUniquifiedFrom:
    """Export asks this before writing an object's name into a submodel.

    Two submodels called ``Wing`` are legal in a POF file and impossible in
    Blender, so the second object becomes ``Wing.001`` and that suffix must not
    reach the model. The question is asked against the name the *file* gave the
    submodel, which is what keeps it from firing on a rename.
    """

    def test_identical_names_match(self):
        assert is_uniquified_from("Wing", "Wing") is True

    def test_blender_suffix_matches_its_original(self):
        assert is_uniquified_from("Wing.001", "Wing") is True
        assert is_uniquified_from("Wing.1000", "Wing") is True

    def test_a_rename_does_not_match(self):
        """The user's word beats the file's, so this has to be False."""
        assert is_uniquified_from("LeftWing", "Wing") is False

    def test_a_recorded_name_that_looks_like_a_suffix_matches_itself(self):
        """A file may name a submodel ``Wing.001``; nothing is stripped then."""
        assert is_uniquified_from("Wing.001", "Wing.001") is True

    def test_a_suffix_of_a_different_name_does_not_match(self):
        assert is_uniquified_from("Hull.001", "Wing") is False

    @pytest.mark.parametrize("name", ["Wing.01", "Wing.1", "Wing.x"])
    def test_suffixes_blender_never_generates_do_not_match(self, name):
        assert is_uniquified_from(name, "Wing") is False


class TestMarkerOrderKey:
    """Gun-bank and attach-point indices are semantic, so order is data.

    ``bpy.data.objects`` is sorted as text, which puts ``Gun_10`` between
    ``Gun_1`` and ``Gun_2``. WBAT cites banks by index, so a permuted export
    swaps a ship's weapons around; under ten markers the two orders agree, which
    is why it went unnoticed.
    """

    def test_numeric_order_beats_text_order(self):
        names = [f"Gun_{i}" for i in range(12)]
        assert sorted(names, key=lambda n: marker_order_key(n, "Gun_")) == names

    def test_text_sorting_really_does_permute(self):
        """Guard against a future 'sorted() already does this' simplification."""
        names = [f"Gun_{i}" for i in range(12)]
        assert sorted(names) != names

    def test_unnumbered_markers_sort_after_numbered_ones(self):
        names = ["Gun_2", "Gun_left", "Gun_0", "Gun_10.001", "Gun_10"]
        assert sorted(names, key=lambda n: marker_order_key(n, "Gun_")) == [
            "Gun_0", "Gun_2", "Gun_10", "Gun_10.001", "Gun_left",
        ]

    def test_a_non_decimal_digit_does_not_crash(self):
        """``"²".isdigit()`` is True and ``int("²")`` raises, so the
        rule tests isdecimal instead."""
        assert marker_order_key("Gun_²", "Gun_") == (1, 0, "Gun_²")

    def test_the_prefix_is_the_configured_one(self):
        assert marker_order_key("Attach_7", "Attach_") == (0, 7, "Attach_7")

    def test_a_name_without_the_prefix_is_ordered_by_name(self):
        assert marker_order_key("Gun_0", "Attach_")[0] == 1


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


#: Scenes with no provenance anywhere -- every .blend built before the material
#: key existed. Their behaviour is frozen: whatever the suffix rule did for them
#: yesterday it must still do today, which is what makes upgrading the add-on
#: safe for a project mid-flight.
PRE_PROVENANCE_CASES = [
    ["Hull", "Hull.001"],
    ["Hull", "Hull.001", "Hull.002"],
    ["Hull.001", "Hull.002"],
    ["Hull", "Turret"],
    ["panel", "panel.01"],
    ["Hull.001.002", "Hull.001"],
    [".001", ""],
    [],
]


class TestExplicitNoneReproducesThePreProvenanceBehaviour:
    @pytest.mark.parametrize("names", PRE_PROVENANCE_CASES)
    def test_passing_none_is_the_same_as_passing_nothing(self, names):
        assert resolve_texture_names(names, None) == resolve_texture_names(names)

    @pytest.mark.parametrize("names", PRE_PROVENANCE_CASES)
    def test_an_empty_mapping_is_the_same_as_passing_nothing(self, names):
        """A scene where no material carries the key -- which is every scene
        saved before it existed -- must export exactly as it used to."""
        assert resolve_texture_names(names, {}) == resolve_texture_names(names)

    def test_the_merge_warning_is_word_for_word_unchanged(self):
        _, warnings = resolve_texture_names(["Hull", "Hull.001"], {})
        assert warnings == [
            "Hull.001 looks like a Blender duplicate of material 'Hull' and "
            "will export as texture 'Hull'. Rename or remove the duplicate to "
            "silence this."
        ]

    def test_the_orphan_warning_is_word_for_word_unchanged(self):
        _, warnings = resolve_texture_names(["Hull.001"], {})
        assert warnings == [
            "Material 'Hull.001' looks like a Blender duplicate, but no "
            "material 'Hull' exists to merge it with, so it is exported "
            "verbatim. Descent 3 will not find a texture called 'Hull.001'."
        ]


class TestRecordedProvenanceOutranksTheSuffixHeuristic:
    def test_two_recorded_ids_that_look_like_duplicates_do_not_merge(self):
        """The bug this whole mechanism exists for. 'metal' and 'metal.001' are
        two real textures the game ships; import recorded both, so there is
        nothing left to guess and the suffix rule gets no vote."""
        explicit = {"metal": "metal", "metal.001": "metal.001"}
        mapping, warnings = resolve_texture_names(["metal", "metal.001"], explicit)
        assert mapping == {"metal": "metal", "metal.001": "metal.001"}
        assert warnings == []

    def test_both_textures_reach_the_texture_list(self):
        """Export builds the TXTR chunk from ``set(mapping.values())``, so a
        merge did not merely mislabel the second texture -- it deleted it."""
        explicit = {"metal": "metal", "metal.001": "metal.001"}
        mapping, _ = resolve_texture_names(["metal", "metal.001"], explicit)
        assert sorted(set(mapping.values())) == ["metal", "metal.001"]

    def test_the_recorded_id_need_not_match_the_material_name(self):
        """Blender renamed the material because the scene already held a 'Hull';
        the texture it stands for did not change when it did."""
        mapping, warnings = resolve_texture_names(["Hull.001"], {"Hull.001": "Hull"})
        assert mapping == {"Hull.001": "Hull"}
        assert warnings == []

    def test_a_recorded_orphan_is_never_warned_about(self):
        """It only looks orphaned. The file it came from says what it is."""
        mapping, warnings = resolve_texture_names(
            ["Hull.001", "Hull.002"],
            {"Hull.001": "Hull.001", "Hull.002": "Hull.002"},
        )
        assert mapping == {"Hull.001": "Hull.001", "Hull.002": "Hull.002"}
        assert warnings == []

    def test_a_hand_duplicated_material_still_collapses(self):
        """The user cloned an imported material in the outliner. The clone
        carries no provenance of its own, so the heuristic still owns it."""
        mapping, warnings = resolve_texture_names(
            ["metal", "metal.001"], {"metal": "metal"}
        )
        assert mapping == {"metal": "metal", "metal.001": "metal"}
        assert len(warnings) == 1
        assert "metal.001" in warnings[0]

    def test_a_clone_follows_its_original_to_the_recorded_id(self):
        """Collapsing onto the material *name* would write a TXTR entry naming
        a bitmap that does not exist; the clone shares the original's texture by
        construction, so it has to land where the original lands."""
        mapping, warnings = resolve_texture_names(
            ["Hull", "Hull.001"], {"Hull": "hull_side"}
        )
        assert mapping == {"Hull": "hull_side", "Hull.001": "hull_side"}
        assert "will export as texture 'hull_side'" in warnings[0]

    def test_provenance_elsewhere_does_not_disturb_an_unrecorded_pair(self):
        mapping, warnings = resolve_texture_names(
            ["Hull", "Hull.001", "Turret"], {"Turret": "turret_tex"}
        )
        assert mapping == {
            "Hull": "Hull", "Hull.001": "Hull", "Turret": "turret_tex",
        }
        assert len(warnings) == 1

    def test_an_unrecorded_orphan_still_exports_verbatim_and_warns(self):
        mapping, warnings = resolve_texture_names(["Hull.001"], {"Other": "other"})
        assert mapping == {"Hull.001": "Hull.001"}
        assert len(warnings) == 1
        assert "no material 'Hull' exists" in warnings[0]

    def test_two_materials_recording_one_texture_merge_without_a_warning(self):
        """Legitimate and intended: two Blender materials wrapping one game
        bitmap is how a model gives the same texture two shader setups."""
        mapping, warnings = resolve_texture_names(
            ["metal", "metal_shiny"], {"metal": "metal", "metal_shiny": "metal"}
        )
        assert set(mapping.values()) == {"metal"}
        assert warnings == []

    def test_provenance_for_a_material_that_is_not_exported_is_ignored(self):
        """``explicit`` is built from the scene, not from the export selection,
        so it routinely names materials no exported object uses."""
        mapping, warnings = resolve_texture_names(["Hull"], {"Unused": "whatever"})
        assert mapping == {"Hull": "Hull"}
        assert warnings == []

    def test_every_input_still_appears_in_the_mapping(self):
        names = ["metal", "metal.001", "Hull", "Hull.002", "panel.01"]
        mapping, _ = resolve_texture_names(names, {"metal.001": "metal.001"})
        assert set(mapping) == set(names)

    def test_the_result_is_stable_regardless_of_input_order(self):
        explicit = {"Hull": "hull_side"}
        a, _ = resolve_texture_names(["Hull", "Hull.001"], explicit)
        b, _ = resolve_texture_names(["Hull.001", "Hull"], explicit)
        assert a == b

    def test_accepts_a_set_of_names(self):
        """Export passes a set of material names alongside the dict."""
        mapping, _ = resolve_texture_names(
            {"metal", "metal.001"}, {"metal.001": "metal.001"}
        )
        assert mapping["metal.001"] == "metal.001"

    def test_the_caller_s_dict_is_not_mutated(self):
        explicit = {"Hull": "hull_side"}
        mapping, _ = resolve_texture_names(["Hull", "Hull.001"], explicit)
        mapping["Hull"] = "something else"
        assert explicit == {"Hull": "hull_side"}


class TestRejectedTextureName:
    """The texture ID becomes the stem of a file the exporter creates, and
    ``os.path.join`` discards its directory argument when the second one is
    absolute. Every case below writes somewhere other than beside the model, or
    writes nothing at all, and none of them raise."""

    @pytest.mark.parametrize(
        "name",
        [
            "Hull", "metal", "metal.001", "some_texture", "hull-01", "a",
            "panel.2",        # a dot is not an extension in a Descent texture ID
            "tex v2",         # an interior space is fine; only a trailing one is not
            "CONSOLE", "COM10", "LPT0", "NULLIFY", "AUXILIARY",
            "über",
        ],
    )
    def test_plain_filename_stems_are_accepted(self, name):
        assert rejected_texture_name(name) is None

    @pytest.mark.parametrize("name", ["", " ", "   ", "\t", "\n"])
    def test_empty_or_whitespace_only_is_rejected(self, name):
        assert rejected_texture_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "C:\\textures\\Hull", "C:/textures/Hull", "c:Hull",
            "\\\\server\\share\\Hull",
        ],
    )
    def test_drive_letters_and_unc_paths_are_rejected(self, name):
        """``os.path.join(directory, 'C:\\\\x')`` throws the directory away."""
        assert rejected_texture_name(name)

    def test_the_drive_rule_does_not_depend_on_the_exporting_platform(self):
        """A drive-relative name is unusable whichever OS Blender runs on, and
        the same .blend gets exported from both by different people."""
        assert rejected_texture_name("C:Hull") is not None

    @pytest.mark.parametrize("name", ["/Hull", "\\Hull", "/", "\\"])
    def test_a_leading_separator_is_rejected(self, name):
        """Python 3.13 made ``ntpath.isabs('/x')`` False, so this needs its own
        check: it still resolves to the root of the current drive."""
        assert rejected_texture_name(name)

    @pytest.mark.parametrize(
        "name",
        ["..", "../Hull", "..\\Hull", "../../Hull", "sub/../../Hull",
         "textures/../../../Hull", "Hull/.."],
    )
    def test_traversal_is_rejected(self, name):
        problem = rejected_texture_name(name)
        assert problem and ".." in problem

    @pytest.mark.parametrize(
        "name", ["sub/Hull", "sub\\Hull", "a/b/Hull", "Hull/", "Hull\\"]
    )
    def test_any_path_separator_is_rejected(self, name):
        assert rejected_texture_name(name)

    def test_a_dot_dot_inside_a_name_is_not_traversal(self):
        """Only a whole path *component* of '..' escapes anywhere."""
        assert rejected_texture_name("Hull..plate") is None

    @pytest.mark.parametrize("device", sorted(RESERVED_DEVICE_NAMES))
    def test_every_reserved_device_name_is_rejected(self, device):
        assert rejected_texture_name(device)

    @pytest.mark.parametrize("device", sorted(RESERVED_DEVICE_NAMES))
    def test_reserved_names_are_rejected_case_insensitively(self, device):
        assert rejected_texture_name(device.lower())
        assert rejected_texture_name(device.capitalize())

    @pytest.mark.parametrize(
        "name", ["NUL.png", "nul.tga", "CON.png", "aux.tga", "com1.png",
                 "LPT9.tga", "prn.png", "CON.foo.bar"],
    )
    def test_reserved_names_are_rejected_with_an_extension_too(self, name):
        """Windows matches the device on the part before the first dot, and
        ``open('NUL.png','wb')`` swallows every byte and leaves no file: the
        export would report success and produce nothing."""
        assert rejected_texture_name(name)

    def test_the_reserved_table_is_exactly_the_documented_set(self):
        assert RESERVED_DEVICE_NAMES == frozenset(
            {"CON", "PRN", "AUX", "NUL"}
            | {f"COM{i}" for i in range(1, 10)}
            | {f"LPT{i}" for i in range(1, 10)}
        )

    @pytest.mark.parametrize("name", ["Hull.", "Hull ", "Hull...", "Hull  ", "."])
    def test_trailing_dots_and_spaces_are_rejected(self, name):
        """Windows strips them when creating the file, so the bitmap lands under
        a name the TXTR chunk does not spell and import never finds it."""
        assert rejected_texture_name(name)

    def test_a_leading_space_is_left_alone(self):
        """Only trailing dots and spaces are stripped by the filesystem, and
        rejecting more than is unusable costs the user a texture for nothing."""
        assert rejected_texture_name(" Hull") is None

    @pytest.mark.parametrize(
        "name", ["", "C:\\Hull", "/Hull", "../Hull", "sub/Hull", "NUL", "Hull."]
    )
    def test_a_rejection_always_explains_itself(self, name):
        """The message is shown to the user in place of the written file, so an
        empty or bare-boolean answer would leave them with a missing texture and
        no idea which name caused it."""
        problem = rejected_texture_name(name)
        assert isinstance(problem, str) and problem.strip()

    def test_the_message_names_the_offending_texture(self):
        assert "Hull" in rejected_texture_name("../Hull")

    def test_accepted_names_survive_texture_filename(self):
        """The two halves have to agree: anything this accepts must still be a
        plain filename once the extension is on it."""
        for name in ["Hull", "metal.001", "panel.2"]:
            assert "/" not in texture_filename(name, "PNG")
            assert "\\" not in texture_filename(name, "PNG")


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
