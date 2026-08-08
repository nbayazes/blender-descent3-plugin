"""Run the pytest suite inside Blender's own Python.

    blender --background --factory-startup --python-exit-code 1 \\
        --python tests/run_in_blender.py

``--python-exit-code 1`` is not optional. Blender exits 0 when a ``--python``
script raises, so without it a run that never reached :func:`main` -- a syntax
error, an import that dies at module scope, a collection error -- reports
success. The ``sys.exit`` at the bottom covers every failure the suite is
*able* to report, which is what makes the omission invisible.

Command-line ``pytest`` runs the same tests against *your system* Python;
Blender ships its own interpreter and the add-on only ever executes there, so
only this run proves the code works on the Python version users have.

Blender does not bundle pytest. This script finds it, or prints the install
command -- built from the running Blender's own interpreter path, so it is
correct for whichever build was launched.

Exits non-zero if any test fails -- and, given the flag above, if the suite
could not be run at all.
"""

import os
import sys

#: Directory added to ``sys.path`` if pytest is not already importable.
#: Git-ignored, and inside the repo rather than the Blender install, so a
#: Blender update or Steam verify cannot wipe it.
VENDOR_DIRNAME = ".pytest-blender"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(REPO_ROOT, VENDOR_DIRNAME)


def _blender_python() -> str:
    """Return the path of the interpreter Blender is running.

    Returns:
        ``sys.executable``, which inside Blender is the bundled
        ``python/bin/python.exe`` rather than the Blender binary -- so it is
        usable as a pip entry point.
    """
    return sys.executable


def _install_hint() -> str:
    """Return the exact command that makes pytest available to this Blender."""
    return (
        f'"{_blender_python()}" -m pip install --target "{VENDOR_DIR}" pytest'
    )


def _load_pytest():
    """Import pytest, falling back to the vendored directory.

    Returns:
        The ``pytest`` module, or ``None`` if it is not available.
    """
    try:
        import pytest  # noqa: F401
        return pytest
    except ImportError:
        pass

    if os.path.isdir(VENDOR_DIR):
        sys.path.insert(0, VENDOR_DIR)
        try:
            import pytest  # noqa: F401
            return pytest
        except ImportError:
            pass
    return None


def main() -> int:
    """Run the suite and return the exit code."""
    pytest = _load_pytest()
    if pytest is None:
        print()
        print("pytest is not available to this Blender.")
        print()
        print("Install it once with:")
        print(f"    {_install_hint()}")
        print()
        print(f"That writes into {VENDOR_DIRNAME}/ in the repo, not into the")
        print("Blender install, so a Blender update cannot remove it.")
        print()
        return 2

    # Import the add-on from the repo, not from wherever it may be installed,
    # so the tests exercise the working tree.
    sys.path.insert(0, REPO_ROOT)
    os.chdir(REPO_ROOT)

    print(f"pytest {pytest.__version__} on Python "
          f"{'.'.join(str(v) for v in sys.version_info[:3])} (Blender's)")
    print(f"repo: {REPO_ROOT}")
    print()

    # -p no:cacheprovider keeps Blender from writing a .pytest_cache owned by a
    # different interpreter than the one the command-line runs use.
    args = ["-q", "--no-header", "-p", "no:cacheprovider"]

    # Anything after a bare "--" is passed straight to pytest, e.g.
    #     ... --python tests/run_in_blender.py -- -k naming -v
    extra = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args.extend(extra or ["tests"])

    return int(pytest.main(args))


if __name__ == "__main__":
    code = main()
    print(f"\nexit code {code}")
    sys.exit(code)
