"""Guards the add-on package's bpy-free import contract.

``descent3_plugin/__init__.py`` defers every ``bpy``-touching import into
``register()`` so the package can be imported in a plain interpreter. That is
what lets the rest of this suite say ``from descent3_plugin.poformat import
...`` instead of manipulating ``sys.path``. If someone adds a module-scope
``import bpy`` back to ``__init__.py``, or to any of ``config``,
``constants``, ``mathutil``, ``naming``, ``poformat`` or ``texutil``, collection of every other test file
breaks -- so these tests fail loudly and point at the cause.

Each check runs in a subprocess: by the time this module executes, the other
test files have already imported the package, so an in-process check would
prove nothing about a cold import.

Run: python -m pytest tests/test_package_import.py -v
"""

import os
import subprocess
import sys
import tomllib

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The extension manifest, which is where the add-on's version is really
#: written down. Read here rather than restating the version as a literal:
#: tests/test_manifest.py owns the manifest-versus-``bl_info`` drift check, and
#: a second copy of the number in this file would be one more thing to forget
#: on a release -- and would fail with "expected (1, 0, 0)" pointing at nothing.
MANIFEST_PATH = os.path.join(
    REPO_ROOT, "descent3_plugin", "blender_manifest.toml"
)

#: Modules that must import without Blender present.
BPY_FREE_MODULES = [
    "descent3_plugin",
    "descent3_plugin.config",
    "descent3_plugin.constants",
    "descent3_plugin.mathutil",
    "descent3_plugin.naming",
    "descent3_plugin.poformat",
    "descent3_plugin.texutil",
]


def _import_in_subprocess(module: str) -> subprocess.CompletedProcess:
    """Import ``module`` in a fresh interpreter and assert bpy stayed absent.

    Args:
        module: Dotted module name to import.

    Returns:
        The completed process. Its stdout is ``"OK"`` when the import succeeded
        without pulling in ``bpy``.
    """
    code = (
        "import sys\n"
        f"import {module}\n"
        "assert 'bpy' not in sys.modules, "
        f"'{module} imported bpy: ' + repr(sorted(sys.modules))\n"
        "print('OK')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("module", BPY_FREE_MODULES)
def test_module_imports_without_bpy(module):
    result = _import_in_subprocess(module)
    assert result.returncode == 0, (
        f"{module} failed to import without Blender:\n{result.stderr}"
    )
    assert result.stdout.strip() == "OK"


def _manifest_version() -> tuple[int, ...]:
    """Return the canonical version, from the manifest.

    Returns:
        ``blender_manifest.toml``'s ``version`` split into ints, i.e. the
        ``bl_info`` tuple every other statement of the version has to match.
    """
    with open(MANIFEST_PATH, "rb") as f:
        version = tomllib.load(f)["version"]
    return tuple(int(part) for part in version.split("."))


def test_bl_info_is_readable_without_bpy():
    """``bl_info`` must survive a cold import in a plain interpreter.

    Reading it is how tests/test_manifest.py checks the version has not drifted
    from the manifest, and that check runs without Blender -- so an ``import
    bpy`` reaching module scope in ``__init__.py`` would not just break this
    test, it would take the drift check down with it and let a release ship
    with a stale ``bl_info``.

    The expected value comes from the manifest because the manifest is
    canonical. Asserting a literal here would make this file a second place the
    version is written down, which is the exact drift the manifest exists to
    stop.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import descent3_plugin as d; print(d.bl_info['version'])",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(_manifest_version())


class TestReloadOrderIsDerivedFromThePackage:
    """The reload list used to be written out by hand, and rotted unnoticed.

    ``texexport`` was never added to it, so an edited ``texexport.py`` went on
    running its previous code after the add-on was toggled off and on -- which is
    the one workflow the reload exists to support. Nothing failed; the module was
    simply stale, and the symptom was code behaving the way it did two saves ago.

    It is read off the folder now (``_scan_package``) and topologically sorted
    (``_reload_order``). Both are ``bpy``-free by construction -- they parse the
    modules with :mod:`ast` rather than importing them -- so the invariants are
    checkable here rather than only inside Blender. What is asserted is the two
    things a hand-written list got wrong: that every module is in it, and that
    nothing is reloaded before something it imports.
    """

    def test_every_module_is_in_the_reload_order(self):
        import descent3_plugin

        imports, _ = descent3_plugin._scan_package()
        order = descent3_plugin._reload_order(imports)

        package_dir = os.path.join(REPO_ROOT, "descent3_plugin")
        expected = {
            name[:-3] for name in os.listdir(package_dir)
            if name.endswith(".py") and name != "__init__.py"
        }
        assert set(order) == expected, (
            "the reload order does not cover the package: missing "
            f"{sorted(expected - set(order))}, unexpected "
            f"{sorted(set(order) - expected)}"
        )
        assert len(order) == len(set(order)), f"duplicate entries: {order}"

    def test_a_module_is_reloaded_after_everything_it_imports(self):
        import descent3_plugin

        imports, _ = descent3_plugin._scan_package()
        order = descent3_plugin._reload_order(imports)
        position = {name: i for i, name in enumerate(order)}

        out_of_order = [
            (name, dependency)
            for name, dependencies in imports.items()
            for dependency in dependencies
            if position[dependency] > position[name]
        ]
        assert not out_of_order, (
            "these modules are reloaded before something they import, so they "
            "would keep the values from before the edit: "
            f"{out_of_order}. Order was {order}."
        )

    def test_every_module_defining_classes_is_registered(self):
        """A module with bpy types that nothing registers loses its operators.

        ``_scan_package`` reports which modules define a module-level ``classes``
        tuple. ``_SUBMODULE_NAMES`` stays an explicit list -- a module missing
        from it is silently unregistered, which is worse than a wrong reload --
        so this is what stops the two drifting.
        """
        import descent3_plugin

        _, with_classes = descent3_plugin._scan_package()
        unregistered = with_classes - set(descent3_plugin._SUBMODULE_NAMES)
        assert not unregistered, (
            f"{sorted(unregistered)} define bpy classes but are not in "
            f"_SUBMODULE_NAMES, so nothing registers them"
        )


def test_operator_modules_are_not_imported_eagerly():
    """Importing the package must not pull in the bpy-dependent submodules.

    If it did, the deferral would be pointless: the submodules import ``bpy`` at
    module scope, so a plain-interpreter import would fail again.
    """
    code = (
        "import sys, descent3_plugin\n"
        "eager = [m for m in ('descent3_plugin.import_pof',\n"
        "                     'descent3_plugin.export_pof')\n"
        "         if m in sys.modules]\n"
        "assert not eager, 'eagerly imported: ' + repr(eager)\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"
