"""
Tests for the Blender extension manifest and what the installers do with it.

``blender_manifest.toml`` is the canonical source of the add-on's version: both
installers read it from there. Blender will not let it be the *only* source,
though -- ``addon_utils.modules()`` pulls ``bl_info`` out of ``__init__.py``
with ``ast.literal_eval`` and never imports the module, so a computed version
raises and the add-on disappears from Edit > Preferences > Add-ons. The literal
in ``bl_info`` therefore has to stay, and this module is what stops the two
drifting apart.

Installing is checked here too, for the same reason: both scripts used to
restate the package contents as a hardcoded list, with nothing comparing that
list to the package. They read the folder now, so the checks below install into
a throwaway directory and look at what actually arrived -- which holds however
the scripts decide what to copy, and is the only form of the question that
cannot itself go stale.

Run with: python -m pytest tests/test_manifest.py -v
"""

import functools
import os
import re
import shutil
import subprocess
import tomllib

import pytest

import descent3_plugin

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.join(REPO_ROOT, "descent3_plugin")
MANIFEST_PATH = os.path.join(PACKAGE_DIR, "blender_manifest.toml")

#: The manifest's filename on its own, for the checks that go looking for the
#: file at an install destination rather than reading the one in the repo.
MANIFEST_FILENAME = os.path.basename(MANIFEST_PATH)

#: The installer scripts, which have to stay in step with each other and with
#: the package. Parametrising on this instead of repeating the pair means a
#: third installer cannot be added with only some of these checks applying.
INSTALLERS = ("install.ps1", "install.sh")

#: The one module an installer may name outright: it is what makes the folder a
#: package, so both scripts check for it before believing they have found the
#: add-on source at all. Every other module has to be discovered from the folder
#: rather than written down a second time.
NAMEABLE_MODULE = "__init__.py"

#: Where to look for a ``bash`` that can run ``install.sh``, in order.
#:
#: PATH first, because a machine with a real bash on it has the one its owner
#: chose. The Git for Windows paths are the fallback, and they exist because
#: PATH lies on stock Windows 11: it carries a ``bash.exe`` *app-execution
#: alias* ahead of everything else, and that alias launches WSL. With no distro
#: installed it prints "Windows Subsystem for Linux has no installed
#: distributions" as UTF-16 and exits 1 for any script it is handed -- so the
#: install.sh half of these checks failed with a wall of NUL-separated text
#: about WSL, on a machine with a perfectly good bash in Git's own bin folder.
#:
#: Running pytest from Git Bash hid it, because that PATH finds Git's bash
#: first. The suite was green in one shell and red in the other.
BASH_CANDIDATES = (
    "bash",
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)

#: Seconds to let a candidate prove itself before giving up on it. Generous for
#: what it is -- ``bash -c exit 0`` -- but the WSL alias can spend a moment
#: deciding it has nothing to run.
BASH_PROBE_TIMEOUT = 20


@pytest.fixture(scope="module")
def manifest():
    with open(MANIFEST_PATH, "rb") as f:
        return tomllib.load(f)


def package_modules() -> list[str]:
    """Return every module the package ships, as bare filenames.

    Read off the directory rather than listed here, so a module added to the
    package is immediately something the installers are measured against. A
    second hand-maintained list would drift exactly the way the installers'
    own lists did.

    Returns:
        Sorted ``*.py`` filenames in ``descent3_plugin/``.
    """
    return sorted(f for f in os.listdir(PACKAGE_DIR) if f.endswith(".py"))


@functools.lru_cache(maxsize=None)
def working_bash() -> str | None:
    """Return a ``bash`` that can actually run a script, or ``None``.

    Every candidate is *run*, not merely located. Being on PATH is exactly what
    says nothing here: the WSL alias described on :data:`BASH_CANDIDATES` is
    present, resolves, and fails -- so a ``shutil.which`` that finds it produces
    an install.sh "failure" that install.sh had no part in.

    Cached because this spawns a process, and the answer cannot change during a
    run.

    Returns:
        Path of the first candidate that exits 0 for ``bash -c 'exit 0'``, or
        ``None`` when none of them does.
    """
    for candidate in BASH_CANDIDATES:
        exe = shutil.which(candidate)
        if exe is None:
            continue
        try:
            probe = subprocess.run(
                [exe, "-c", "exit 0"],
                capture_output=True,
                timeout=BASH_PROBE_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return exe
    return None


def run_installer(script: str, target) -> subprocess.CompletedProcess:
    """Run an installer against a throwaway add-ons directory.

    ``--target`` skips Blender discovery entirely, so this cannot reach a real
    install: the script creates ``<target>/descent3_plugin`` and copies into
    that and nothing else.

    Args:
        script: One of :data:`INSTALLERS`.
        target: Directory to install into, standing in for a ``scripts/addons``
            folder.

    Returns:
        The completed process. The calling test is skipped instead when the
        interpreter that script needs is absent or cannot run -- neither script
        is meant to run on the other's platform, and skipping says which half of
        the check actually ran rather than pretending both did.
    """
    script_path = os.path.join(REPO_ROOT, script)
    if script.endswith(".ps1"):
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if exe is None:
            pytest.skip("no PowerShell interpreter on this machine")
        # Bypass because the repo's scripts are unsigned, and -NoProfile so a
        # user profile cannot change what the script sees.
        command = [
            exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", script_path, "-Target", str(target),
        ]
    else:
        exe = working_bash()
        if exe is None:
            pytest.skip("no working bash on this machine")
        # Forward slashes: on Windows this is Git Bash, whose `dirname` and `cd`
        # do not agree with the backslashes os.path hands out.
        command = [
            exe, script_path.replace(os.sep, "/"),
            "--target", str(target).replace(os.sep, "/"),
        ]
    return subprocess.run(
        command, cwd=REPO_ROOT, capture_output=True, text=True
    )


@pytest.fixture(scope="module", params=INSTALLERS)
def installed_package(request, tmp_path_factory):
    """Install with one of the scripts and hand back what it produced.

    Module-scoped and parametrised rather than a plain helper, so each script
    runs once for all the checks that read the result instead of once per
    check: these are real subprocesses copying real files.

    Returns:
        A ``(script, destination)`` pair, where ``destination`` is the
        ``descent3_plugin`` folder the script created.
    """
    script = request.param
    target = tmp_path_factory.mktemp("addons")
    result = run_installer(script, target)
    assert result.returncode == 0, (
        f"{script} failed against a throwaway target:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    return script, os.path.join(str(target), os.path.basename(PACKAGE_DIR))


class TestManifestIsValid:
    def test_manifest_exists_with_the_name_blender_requires(self):
        """Blender 4.2+ only recognises this exact filename."""
        assert os.path.isfile(MANIFEST_PATH)

    def test_is_parseable_toml(self, manifest):
        assert isinstance(manifest, dict)

    @pytest.mark.parametrize(
        "key",
        ["schema_version", "id", "version", "name", "tagline", "maintainer",
         "type", "tags", "blender_version_min", "license"],
    )
    def test_required_key_present(self, manifest, key):
        assert key in manifest, f"blender_manifest.toml is missing {key!r}"

    def test_id_matches_the_package_directory(self, manifest):
        """A mismatch here makes the extension install under the wrong name."""
        assert manifest["id"] == os.path.basename(PACKAGE_DIR)

    def test_type_is_add_on(self, manifest):
        assert manifest["type"] == "add-on"

    def test_license_is_spdx(self, manifest):
        assert manifest["license"], "license must not be empty"
        for entry in manifest["license"]:
            assert entry.startswith("SPDX:"), entry

    def test_license_matches_the_vendored_sources(self, manifest):
        """reference/ redistributes GPL-3.0-or-later Descent 3 sources."""
        assert "SPDX:GPL-3.0-or-later" in manifest["license"]

    def test_license_file_exists(self):
        path = os.path.join(REPO_ROOT, "LICENSE")
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "GNU GENERAL PUBLIC LICENSE" in text
        assert "Version 3" in text


class TestVersionIsSingleSourced:
    """The manifest is canonical; everything else must agree with it."""

    def test_bl_info_matches_the_manifest(self, manifest):
        expected = tuple(int(p) for p in manifest["version"].split("."))
        assert descent3_plugin.bl_info["version"] == expected, (
            "bl_info['version'] in descent3_plugin/__init__.py has drifted from "
            "blender_manifest.toml. The manifest is canonical -- update the "
            "literal to match it."
        )

    def test_bl_info_version_is_a_literal_tuple(self):
        """It must stay literal, or the add-on vanishes from Preferences.

        addon_utils.modules() ast-parses bl_info with literal_eval without
        importing the module. A function call there raises ValueError and the
        add-on is never listed, so it cannot be enabled through the UI at all.
        """
        source = open(
            os.path.join(PACKAGE_DIR, "__init__.py"), encoding="utf-8"
        ).read()
        match = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', source)
        assert match, "bl_info['version'] must be a literal (int, int, int) tuple"

    def test_manifest_version_is_three_part(self, manifest):
        assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]), (
            manifest["version"]
        )

    def test_blender_version_min_matches_bl_info(self, manifest):
        """Two statements of the minimum Blender version; keep them equal."""
        expected = tuple(
            int(p) for p in manifest["blender_version_min"].split(".")
        )
        assert descent3_plugin.bl_info["blender"] == expected

    def test_name_matches_bl_info(self, manifest):
        assert descent3_plugin.bl_info["name"] == manifest["name"]


class TestInstallersReadTheManifest:
    """Both installers must source the version from the manifest, not bl_info."""

    @pytest.mark.parametrize("script", INSTALLERS)
    def test_installer_references_the_manifest(self, script):
        with open(os.path.join(REPO_ROOT, script), encoding="utf-8") as f:
            text = f.read()
        assert MANIFEST_FILENAME in text, (
            f"{script} should read the version from {MANIFEST_FILENAME}"
        )

    def test_installer_ships_the_manifest(self, installed_package):
        """The package is useless as an extension if the manifest is not copied.

        Both installers used to glob '*.py' only, which is why the previous
        manifest sat in the repo for its whole life without ever reaching a
        Blender install. Naming the file somewhere in the script is no evidence
        that it is copied -- that is all the check above can say -- so this one
        installs into a throwaway directory and looks for the file afterwards.
        """
        script, destination = installed_package
        installed = os.path.join(destination, MANIFEST_FILENAME)
        assert os.path.isfile(installed), (
            f"{script} installed the package without {MANIFEST_FILENAME}. "
            f"Blender 4.2+ needs it to treat the folder as an extension, and "
            f"a copy step that only takes '*.py' will never pick it up."
        )

        with open(installed, "rb") as f:
            copied = f.read()
        with open(MANIFEST_PATH, "rb") as f:
            source = f.read()
        assert copied == source, (
            f"{script} installed a {MANIFEST_FILENAME} that is not the one in "
            f"the repo"
        )


class TestInstallersShipTheWholePackage:
    """Nothing used to check that an install contains the whole add-on.

    Both scripts once restated the package contents as a hardcoded list, which
    is how the README, the skill docs and both installers came to disagree with
    the package all at once. They read the folder now, but the question worth
    asking does not depend on that, and it is asked of the result rather than
    the source: after running the script, is every module really there? A
    missing one is not a missing feature -- ``from . import poformat`` raises
    and the add-on does not register at all.
    """

    def test_installer_installs_every_module(self, installed_package):
        """Every module in the package has to survive the trip."""
        script, destination = installed_package
        installed = os.listdir(destination)
        missing = [name for name in package_modules() if name not in installed]
        assert not missing, (
            f"{script} installed the add-on without {', '.join(missing)}. "
            f"Whatever decides which files to copy has fallen behind the "
            f"package."
        )

    @pytest.mark.parametrize("script", INSTALLERS)
    def test_installer_does_not_restate_the_package_contents(self, script):
        """A module list written into a script is a copy that goes stale.

        This is the check the hardcoded lists never had. It runs everywhere,
        including where the sibling script's interpreter is missing and the
        install itself has to be skipped.
        """
        with open(os.path.join(REPO_ROOT, script), encoding="utf-8") as f:
            text = f.read()
        named = [
            name for name in package_modules()
            if name != NAMEABLE_MODULE and name in text
        ]
        assert not named, (
            f"{script} names {', '.join(named)}. The files to install are read "
            f"off {os.path.basename(PACKAGE_DIR)}/ so that adding a module "
            f"cannot silently fail to install it -- name modules in prose "
            f"without the .py suffix rather than reintroducing a list."
        )
