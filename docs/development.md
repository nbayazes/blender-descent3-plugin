# Development

[<- README](../README.md)

Which modules may import `bpy` and which may not, how to run the test suite,
and how the repo's version number stays in one place.

## The `bpy`-free contract

The parsing/writing (`poformat.py`), math primitives (`mathutil.py`),
texture-path logic (`texutil.py`), naming rules (`naming.py`), project
`config.py` and shared `constants.py` have **no `bpy` dependency**. Neither
does the package `__init__.py`, which defers its Blender imports into
`register()` — so the whole package imports under plain Python.

`tests/test_package_import.py` guards that contract: it fails if a
module-scope `import bpy` reappears in any of those modules.

The other four — `import_pof.py`, `export_pof.py`, `texexport.py` and
`preferences.py` — import `bpy` at module scope and are exercised through real
Blender instead; see [Tests that need Blender itself](#tests-that-need-blender-itself).
Anything expressible without `bpy` belongs on the first list — that is the half
a plain `pytest` run can cover.

## Running the tests

```bash
./run_tests.sh          # macOS / Linux / Git Bash
.\run_tests.ps1         # Windows PowerShell
```

The script finds Blender, uses **Blender's own Python** to run the suite, and
installs pytest the first time if it is missing.

```
==> Blender: E:\Steam\steamapps\common\Blender\blender.exe
    Python 3.13.13  (E:\Steam\...\Blender\5.2\python\bin\python.exe)
==> Running tests
518 passed in 1.58s
==> all tests passed
```

Arguments go straight to pytest, including bare paths:

```bash
./run_tests.sh -k naming -v
./run_tests.sh tests/test_poformat.py
```

```powershell
.\run_tests.ps1 -k naming --verbose
.\run_tests.ps1 tests\test_poformat.py
```

PowerShell binds `-v` to its own `-Verbose` before the script sees it, so pytest
never receives that one — use `--verbose` or `-vv`. Everything else passes
through.

Other options:

| Option | Effect |
|--------|--------|
| `--blender <path>` / `-Blender <path>` | use a specific Blender. Given explicitly, it is never silently substituted — a bad path is an error |
| `--system` / `-System` | use your system Python instead |
| `BLENDER=<path>` / `$env:BLENDER` | same as `--blender` |
| `--help` / `-?` | usage |

**How Blender is found.** No path is baked into the repo — the search is derived
entirely from your environment:

1. `--blender` / `-Blender`, or `$BLENDER` / `$env:BLENDER`;
2. `blender` on `PATH`;
3. the roots Blender's own installer writes to, newest version first —
   `$ProgramFiles` and `$LOCALAPPDATA` on Windows, `/Applications` on macOS,
   `/usr/bin` on Linux;
4. every Steam library, read from Steam's own record of them — its install path
   in the registry (Windows) or under `$HOME` (Linux/macOS), plus every extra
   library listed in `libraryfolders.vdf`;
5. failing all of that, whichever Blender the last successful run used, which is
   cached in `.pytest-blender/.interpreter`.

Step 4 covers a Steam library on any drive without any drive being named — the
scripts ask Steam rather than sweeping drive letters. Step 5 is then only for a
portable build unpacked somewhere of your own, after you have named it with
`--blender` once. If nothing is found, the error lists every way to point it at
one; `./install.sh --list` enumerates every Blender on the machine.

## Why Blender's Python and not yours

The add-on only ever executes inside Blender, which ships its own interpreter.
On this machine the system Python is 3.13.3 and Blender's is 3.13.13 — close,
but not the same. A stdlib module or syntax feature present in one and not the
other passes locally and fails on a user's machine. Running against Blender's
interpreter closes that gap for free: the suite is `bpy`-free, so it needs
Blender's *Python*, not Blender.

pytest is installed into a git-ignored `.pytest-blender/` **in the repo, not
inside the Blender install** — a Blender update, or a Steam "verify files",
cannot remove it, and nothing under the Blender folder is modified. The path to
Blender's interpreter is cached there too, so only the first run pays the
Blender launch needed to ask where it lives.

You can still run pytest directly; it exercises the same tests against whatever
Python is on your PATH:

```bash
python -m pytest -q
```

## Tests that need Blender itself

Mesh building, materials, UV orientation and operator registration only exist
inside `bpy`, so those are driven through real Blender. Each is a standalone
script that exits non-zero on failure:

```bash
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_uv_roundtrip.py
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_material_reuse.py
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_coordinate_system.py
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_texture_export.py
blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender/test_bad_geometry.py
```

> **`--python-exit-code 1` is not optional.** Blender exits **0** when a script
> raises or fails to parse, so without it a test that crashes before reaching
> its own `sys.exit(1)` reads as a pass. That flag makes a Python failure a
> non-zero exit.

`test_uv_roundtrip.py` guards UV orientation: Descent 3 puts the UV origin at
the top-left and Blender at the bottom-left, so import negates V and export must
negate it back — when only one side did, every exported texture came out
mirrored.

`test_material_reuse.py` guards the material-name-is-the-texture-ID contract: a
second import must reuse an existing material rather than let Blender mint
`Hull.001`, which export would write into the file as a texture no bitmap
matches.

`test_coordinate_system.py` guards everything that crosses the Y-up/Z-up
boundary, including the parts that live on the object rather than in the mesh:
world transforms, marker positions and directions, and the offsets that
accumulate down a three-deep hierarchy.

`test_bad_geometry.py` guards the damaged-file paths, and is the one test here
whose regression is not a `FAIL` line — a face carrying an out-of-range index or
a repeated vertex kills Blender with an access violation when the model's
normals are applied, so a regression shows up as the process dying mid-output.

These deliberately do *not* run under pytest — `tests/blender/conftest.py` sets
`collect_ignore_glob`, so a bare `pytest` does not try to import them without
Blender.

`tests/run_in_blender.py` runs the pytest suite *inside* Blender rather than
beside it. `run_tests.sh` is faster and is what you want day to day; this one
matters only if you add tests that themselves need `bpy`:

```bash
blender --background --factory-startup --python-exit-code 1 \
        --python tests/run_in_blender.py
```

The flag belongs here too. This script does exit non-zero when a test fails — it
calls `sys.exit()` with pytest's own code, and Blender honours an explicit exit
code either way — but without the flag, a collection error or an import that
blows up before `main()` runs exits 0 and reads as a clean suite.

## Validating the extension manifest

```bash
blender --command extension validate descent3_plugin
```

## Versioning

`descent3_plugin/blender_manifest.toml` is the canonical version. Both
installers read it from there, and `blender --command extension validate
descent3_plugin` checks the manifest itself.

`bl_info` in `__init__.py` still carries a literal copy, and has to:
`addon_utils.modules()` pulls `bl_info` out of the file text with
`ast.literal_eval` *without importing the module*, so computing the version at
import time makes the add-on disappear from Edit → Preferences → Add-ons
entirely. `tests/test_manifest.py` fails if the two ever disagree, so the
duplication cannot silently drift.
