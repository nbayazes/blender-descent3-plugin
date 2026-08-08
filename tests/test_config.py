"""
Unit tests for the per-project configuration layer (descent3_plugin/config.py).

The importer and the exporter both read this file, so a setting that resolves
differently on the two sides silently breaks round-tripping. A project with no
config file must get exactly the add-on's pre-configuration behaviour.

Run with: python -m pytest tests/test_config.py -v
"""

import os

import pytest

from descent3_plugin import constants
from descent3_plugin.config import (
    CONFIG_FILENAME,
    DEFAULT_CONFIG,
    Config,
    config_for_path,
    find_config_file,
    load_config,
    parse_config,
    resolved_search_dirs,
)
from descent3_plugin.poformat import MIN_OBJFILE_VERSION, OBJFILE_VERSION
from descent3_plugin.texutil import IMAGE_EXTENSIONS


def write_config(directory, text):
    path = os.path.join(str(directory), CONFIG_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestDefaults:
    """No config file must mean no behaviour change."""

    def test_naming_defaults_match_the_constants(self):
        n = DEFAULT_CONFIG.naming
        assert n.gun_prefix == constants.GUN_EMPTY_PREFIX
        assert n.attach_prefix == constants.ATTACH_EMPTY_PREFIX
        assert n.submodel_fallback_prefix == constants.SUBMODEL_FALLBACK_PREFIX
        assert n.uv_layer == constants.UV_LAYER_NAME

    def test_extension_default_matches_texutil(self):
        assert list(DEFAULT_CONFIG.textures.extensions) == IMAGE_EXTENSIONS

    def test_export_version_default(self):
        assert DEFAULT_CONFIG.export.version == OBJFILE_VERSION

    def test_no_source_path(self):
        assert DEFAULT_CONFIG.source_path is None

    def test_config_is_frozen(self):
        with pytest.raises(Exception):
            DEFAULT_CONFIG.naming.gun_prefix = "nope"


class TestDiscovery:
    def test_finds_in_same_directory(self, tmp_path):
        expected = write_config(tmp_path, "")
        assert find_config_file(str(tmp_path)) == expected

    def test_walks_up_to_a_parent(self, tmp_path):
        expected = write_config(tmp_path, "")
        deep = tmp_path / "models" / "ship"
        deep.mkdir(parents=True)
        assert find_config_file(str(deep)) == expected

    def test_nearest_wins(self, tmp_path):
        write_config(tmp_path, "")
        near_dir = tmp_path / "models"
        near_dir.mkdir()
        near = write_config(near_dir, "")
        assert find_config_file(str(near_dir)) == near

    def test_missing_returns_none(self, tmp_path):
        assert find_config_file(str(tmp_path)) is None

    def test_depth_limit_is_respected(self, tmp_path):
        write_config(tmp_path, "")
        deep = tmp_path
        for i in range(4):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True)
        assert find_config_file(str(deep), depth=1) is None
        assert find_config_file(str(deep), depth=4) is not None


class TestParsing:
    def test_naming_overrides(self):
        config, warnings = parse_config({"naming": {"gun_prefix": "GP_"}})
        assert config.naming.gun_prefix == "GP_"
        assert config.naming.attach_prefix == DEFAULT_CONFIG.naming.attach_prefix
        assert warnings == []

    def test_export_version_override(self):
        config, warnings = parse_config({"export": {"version": 2200}})
        assert config.export.version == 2200
        assert warnings == []

    def test_extensions_are_normalized(self):
        """A hand-written list may omit the dot or use upper case."""
        config, _ = parse_config({"textures": {"extensions": ["DDS", ".Png"]}})
        assert config.textures.extensions == (".dds", ".png")

    def test_unknown_section_warns_and_is_ignored(self):
        config, warnings = parse_config({"nope": {"a": 1}})
        assert config == Config()
        assert any("unknown section" in w for w in warnings)

    def test_unknown_key_warns(self):
        _, warnings = parse_config({"naming": {"gun_prefx": "typo"}})
        assert any("naming.gun_prefx" in w for w in warnings)

    def test_wrong_type_falls_back(self):
        config, warnings = parse_config({"naming": {"gun_prefix": 42}})
        assert config.naming.gun_prefix == DEFAULT_CONFIG.naming.gun_prefix
        assert any("expected a string" in w for w in warnings)

    def test_empty_prefix_is_rejected(self):
        """An empty prefix would make export match every empty in the scene."""
        config, warnings = parse_config({"naming": {"gun_prefix": ""}})
        assert config.naming.gun_prefix == DEFAULT_CONFIG.naming.gun_prefix
        assert any("must not be empty" in w for w in warnings)

    def test_empty_extension_list_is_rejected(self):
        config, warnings = parse_config({"textures": {"extensions": []}})
        assert config.textures.extensions == DEFAULT_CONFIG.textures.extensions
        assert any("no texture would ever resolve" in w for w in warnings)

    @pytest.mark.parametrize(
        "version", [MIN_OBJFILE_VERSION - 1, OBJFILE_VERSION + 1, 0, -5]
    )
    def test_out_of_range_version_is_rejected(self, version):
        config, warnings = parse_config({"export": {"version": version}})
        assert config.export.version == DEFAULT_CONFIG.export.version
        assert any("outside the supported range" in w for w in warnings)

    def test_bool_is_not_accepted_as_a_version(self):
        """bool is an int subclass, so this would otherwise sneak through."""
        config, warnings = parse_config({"export": {"version": True}})
        assert config.export.version == DEFAULT_CONFIG.export.version
        assert any("expected an integer" in w for w in warnings)

    def test_section_that_is_not_a_table(self):
        config, warnings = parse_config({"naming": "oops"})
        assert config.naming == DEFAULT_CONFIG.naming
        assert any("expected a table" in w for w in warnings)


class TestLoading:
    def test_round_trip_through_a_real_file(self, tmp_path):
        path = write_config(tmp_path, """
[naming]
gun_prefix = "Muzzle_"

[export]
version = 2100
""")
        config, warnings = load_config(path)
        assert warnings == []
        assert config.naming.gun_prefix == "Muzzle_"
        assert config.export.version == 2100
        assert config.source_path == path

    def test_invalid_toml_degrades_to_defaults(self, tmp_path):
        path = write_config(tmp_path, "[naming\ngun_prefix = ")
        config, warnings = load_config(path)
        assert config == DEFAULT_CONFIG
        assert any("not valid TOML" in w for w in warnings)

    def test_unreadable_file_degrades_to_defaults(self, tmp_path):
        missing = os.path.join(str(tmp_path), "nope.toml")
        config, warnings = load_config(missing)
        assert config == DEFAULT_CONFIG
        assert any("could not read" in w for w in warnings)


class TestConfigForPath:
    def test_no_config_returns_defaults_silently(self, tmp_path):
        model = tmp_path / "ship.oof"
        model.write_bytes(b"")
        config, warnings = config_for_path(str(model))
        assert config == DEFAULT_CONFIG
        assert warnings == []

    def test_finds_config_above_the_model(self, tmp_path):
        write_config(tmp_path, '[naming]\ngun_prefix = "G-"\n')
        models = tmp_path / "models"
        models.mkdir()
        model = models / "ship.oof"
        model.write_bytes(b"")
        config, _ = config_for_path(str(model))
        assert config.naming.gun_prefix == "G-"


class TestSearchDirResolution:
    def test_relative_dirs_resolve_against_the_config_file(self, tmp_path):
        path = write_config(tmp_path, '[textures]\nsearch_dirs = ["shared/tex"]\n')
        config, _ = load_config(path)
        resolved = resolved_search_dirs(config)
        assert resolved == [
            os.path.normpath(os.path.join(str(tmp_path), "shared", "tex"))
        ]

    def test_absolute_dirs_are_left_alone(self, tmp_path):
        absolute = os.path.abspath(os.path.join(str(tmp_path), "abs"))
        path = write_config(
            tmp_path,
            "[textures]\nsearch_dirs = [%r]\n" % absolute.replace("\\", "/"),
        )
        config, _ = load_config(path)
        assert resolved_search_dirs(config) == [os.path.normpath(absolute)]

    def test_defaults_have_no_search_dirs(self):
        assert resolved_search_dirs(DEFAULT_CONFIG) == []


class TestExampleFile:
    """The shipped example must stay in step with the real schema.

    An example documenting a key the parser rejects, or omitting one it accepts,
    is worse than none: it is the first thing a user copies.
    """

    @staticmethod
    def _example_text():
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "descent3.example.toml",
        )
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_example_exists_and_is_valid_toml(self):
        import tomllib

        data = tomllib.loads(self._example_text())
        assert set(data) <= {"naming", "textures", "export"}

    def test_example_parses_without_warnings(self):
        import tomllib

        config, warnings = parse_config(tomllib.loads(self._example_text()))
        assert warnings == []

    def test_example_is_fully_commented_out(self):
        """Copying it verbatim must change nothing."""
        import tomllib

        config, _ = parse_config(tomllib.loads(self._example_text()))
        assert config.naming == DEFAULT_CONFIG.naming
        assert config.textures == DEFAULT_CONFIG.textures
        assert config.export == DEFAULT_CONFIG.export

    def test_documents_every_supported_key(self):
        from descent3_plugin.config import _SCHEMA

        text = self._example_text()
        undocumented = [
            f"{section}.{key}"
            for section, keys in _SCHEMA.items()
            for key in keys
            if f"# {key} =" not in text
        ]
        assert undocumented == []

    def test_documents_no_unsupported_key(self):
        import re

        from descent3_plugin.config import _SCHEMA

        section = None
        unknown = []
        for line in self._example_text().splitlines():
            stripped = line.strip()
            header = re.match(r"^\[(\w+)\]$", stripped)
            if header:
                section = header.group(1)
                continue
            commented = re.match(r"^#\s*(\w+)\s*=", stripped)
            if commented and section:
                key = commented.group(1)
                if key not in _SCHEMA.get(section, ()):
                    unknown.append(f"{section}.{key}")
        assert unknown == []


class TestTextureExportSettings:
    def test_export_dir_default(self):
        assert DEFAULT_CONFIG.textures.export_dir == "exported_textures"

    def test_texture_format_defaults_to_none(self):
        """Exporting a model must not drop image files beside it unasked."""
        assert DEFAULT_CONFIG.export.texture_format == "NONE"

    @pytest.mark.parametrize("value", ["PNG", "TARGA", "NONE"])
    def test_valid_formats_accepted(self, value):
        config, warnings = parse_config({"export": {"texture_format": value}})
        assert config.export.texture_format == value
        assert warnings == []

    def test_format_is_case_insensitive(self):
        config, _ = parse_config({"export": {"texture_format": "targa"}})
        assert config.export.texture_format == "TARGA"

    @pytest.mark.parametrize("value", ["ogf", "jpeg", "tga", ""])
    def test_unsupported_format_rejected(self, value):
        config, warnings = parse_config({"export": {"texture_format": value}})
        assert config.export.texture_format == "NONE"
        assert any("not supported" in w for w in warnings)

    def test_export_dir_accepts_a_relative_subfolder(self):
        config, warnings = parse_config({"textures": {"export_dir": "tex/out"}})
        assert config.textures.export_dir == "tex/out"
        assert warnings == []

    @pytest.mark.parametrize(
        "value,reason",
        [
            ("C:/somewhere", "absolute"),
            ("/textures", "separator"),
            (r"\textures", "separator"),
            ("../escape", "escapes"),
            ("tex/../..", "escapes"),
        ],
    )
    def test_export_dir_must_stay_beside_the_model(self, value, reason):
        """Python 3.13 stopped treating a single leading slash as absolute on
        Windows, so os.path.isabs alone would let '/textures' through to the
        drive root."""
        config, warnings = parse_config({"textures": {"export_dir": value}})
        assert config.textures.export_dir == DEFAULT_CONFIG.textures.export_dir
        assert any(reason in w for w in warnings), warnings

    def test_empty_export_dir_rejected(self):
        config, warnings = parse_config({"textures": {"export_dir": "   "}})
        assert config.textures.export_dir == DEFAULT_CONFIG.textures.export_dir
        assert any("non-empty" in w for w in warnings)
