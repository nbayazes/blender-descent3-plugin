"""Run the pytest suite inside Blender's own Python.

    blender --background --factory-startup --python tests/run_in_blender.py

Why bother, when ``pytest`` on the command line already runs the same tests?
Because it runs them against *your system* Python. Blender ships its own
interpreter, and the add-on only ever executes there. Running the suite inside
Blender is what proves the code works on the Python version your users actually
have -- a syntax feature or a stdlib module that exists in your 3.13 and not in
Blender's would otherwise sail through CI and fail on their machine.

Blender does not bundle pytest. This script finds it, and if it cannot, prints
the exact command to install it -- built from the running Blender's own
interpreter path, so it is correct for whichever build you launched.

Exits non-zero if any test fails, so it can be wired into CI.
"""

import os
import sys

#: Directory this script will add to ``sys.path`` if pytest is not already
#: importable. Keeping pytest here rather than inside Blender means a Blender
#: update (or a Steam verify) cannot wipe it, and nothing under the Blender
#: install is modified. It is git-ignored.
VENDOR_DIRNAME = ".pytest-blender"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(REPO_ROOT, VENDOR_DIRNAME)


def _blender_python() -> str:
    """Return the path of the interpreter Blender is running.

    Returns:
        ``sys.executable``, which inside Blender is the bundled
        ``python/bin/python.exe`` rather than the Blender binary -- so it is
        directly usable as a pip entry point.
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
        The imported ``pytest`` module, or ``None`` if it is not available.
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

    # Anything after a bare "--" is passed straight to pytest, so the usual
    #     ... --python tests/run_in_blender.py -- -k naming -v
    # works. Without it, default to the whole suite.
    extra = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args.extend(extra or ["tests"])

    return int(pytest.main(args))


if __name__ == "__main__":
    code = main()
    print(f"\nexit code {code}")
    sys.exit(code)
