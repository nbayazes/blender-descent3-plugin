"""Guards the add-on package's bpy-free import contract.

``descent3_plugin/__init__.py`` defers every ``bpy``-touching import into
``register()`` so the package can be imported in a plain interpreter. That is
what lets the rest of this suite say ``from descent3_plugin.poformat import
...`` instead of manipulating ``sys.path``. If someone adds a module-scope
``import bpy`` back to ``__init__.py``, or to any of ``constants``,
``mathutil``, ``poformat`` or ``texutil``, collection of every other test file
breaks -- so these tests fail loudly and point at the cause.

Each check runs in a subprocess: by the time this module executes, the other
test files have already imported the package, so an in-process check would
prove nothing about a cold import.

Run: python -m pytest tests/test_package_import.py -v
"""

import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Modules that must import without Blender present.
BPY_FREE_MODULES = [
    "descent3_plugin",
    "descent3_plugin.constants",
    "descent3_plugin.mathutil",
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


def test_bl_info_is_readable_without_bpy():
    """The installers regex ``bl_info["version"]`` out of ``__init__.py``."""
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
    assert result.stdout.strip() == "(1, 0, 0)"


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
