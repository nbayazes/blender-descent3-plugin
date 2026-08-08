"""Descent 3 POF/OOF Blender Importer/Exporter.

Import and export Descent 3 3D model files (POF/OOF format). Supports vertices,
faces, UV mapping, materials, submodel hierarchy, gun points, weapon batteries,
attach points, and animation keyframes.

No module-scope ``bpy`` import: the bpy-touching submodules are pulled in only
from :func:`register`, which keeps ``import descent3_plugin`` working in a plain
Python interpreter, as the pytest suite relies on.
"""

bl_info = {
    "name": "Descent 3 POF/OOF Importer/Exporter",
    "author": "Descent Community",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "File > Import/Export > Descent 3 POF/OOF (.pof, .oof)",
    "description": "Import and export Descent 3 polygon model files",
    "category": "Import-Export",
}

import ast
import importlib
import logging
import os
import sys

log = logging.getLogger(__name__)

# constants is bpy-free by contract, so importing it here does not drag Blender
# in. Everything bpy-touching stays behind _submodules().
from . import constants

#: Submodules that import ``bpy``, in registration order; each exposes a
#: ``classes`` tuple of the bpy types it wants registered. Listed by hand rather
#: than derived: a module missing from here loses its operators outright, so
#: :func:`_submodules` warns when a module defines ``classes`` but is not named.
_SUBMODULE_NAMES = ("preferences", "import_pof", "export_pof")

#: Exactly the classes :func:`register` installed, so :func:`unregister` removes
#: those same objects. Re-deriving them from ``sys.modules`` risks unregistering
#: a fresh class object, which Blender rejects with "not the registered object".
_registered = []


def _scan_package() -> tuple[dict[str, set[str]], set[str]]:
    """Read the package folder to find out what it contains.

    The reload list this feeds used to be hand-written and rotted silently --
    ``texexport`` was never on it, so edits to it kept running stale code after
    an add-on toggle. Reading the folder cannot go out of date.

    Modules are parsed with :mod:`ast`, never imported: importing them here would
    drag ``bpy`` in and defeat the deferral that keeps ``import descent3_plugin``
    working without Blender.

    Returns:
        An ``(imports, with_classes)`` pair. ``imports`` maps every sibling
        module's name to the set of sibling modules it imports; ``with_classes``
        holds the modules defining a module-level ``classes`` tuple.
    """
    directory = os.path.dirname(os.path.abspath(__file__))
    try:
        entries = sorted(os.listdir(directory))
    except OSError as e:
        # Survivable: a folder that cannot be listed cannot be reloaded, and
        # running the already-imported code beats refusing to register at all.
        log.warning("Could not read %s to plan the reload: %s", directory, e)
        return {}, set()

    names = [e[:-3] for e in entries if e.endswith(".py") and e != "__init__.py"]
    imports = {name: set() for name in names}
    with_classes = set()

    for name in names:
        path = os.path.join(directory, f"{name}.py")
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
        except (OSError, SyntaxError, ValueError) as e:
            # ``continue`` leaves the module in the reload set with no recorded
            # dependencies: a suboptimal reload position is recoverable, leaving
            # it out is the failure this mechanism exists to prevent. A module
            # that really is broken raises from importlib.reload() at register
            # time, so this warning hides nothing.
            log.warning("Could not scan %s.py, assuming no dependencies: %s", name, e)
            continue

        # Walked, not just module scope: a dependency imported inside a function
        # is still a dependency, and a missed edge is a wrong reload order.
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 1:
                continue
            if node.module is None:
                # ``from . import poformat`` -- the siblings are the names.
                imports[name].update(a.name for a in node.names if a.name in imports)
            elif node.module in imports:
                # ``from .poformat import POFModel``
                imports[name].add(node.module)

        # Module scope only, because that is the only place register() looks.
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "classes"
                for target in node.targets
            ):
                with_classes.add(name)

    return imports, with_classes


def _reload_order(imports: dict[str, set[str]]) -> tuple[str, ...]:
    """Order the package's modules so a dependency is always reloaded first.

    ``from .x import NAME`` binds the value, not the module, so reloading a
    dependency *after* its dependents leaves them holding pre-edit values --
    which surfaces as code behaving the way it did two saves ago.

    Args:
        imports: Module name -> the sibling modules it imports, as returned by
            :func:`_scan_package`.

    Returns:
        Every name in ``imports``, each preceded by everything it imports.
        Independent modules come out alphabetically, so the order is stable.
    """
    pending = {name: set(deps) for name, deps in imports.items()}
    ordered = []
    while pending:
        ready = sorted(name for name, deps in pending.items() if not deps)
        if not ready:
            # An import cycle has no correct reload order, and Python tolerates
            # some at run time, so break it deterministically rather than
            # dropping the modules involved.
            ready = [min(pending)]
            log.warning(
                "Import cycle among %s; reloading %s first, which may leave "
                "stale values behind",
                ", ".join(sorted(pending)), ready[0],
            )
        ordered.extend(ready)
        for deps in pending.values():
            deps.difference_update(ready)
        for name in ready:
            del pending[name]
    return tuple(ordered)


def _submodules(reload_first: bool = False) -> list:
    """Import the bpy-touching submodules on demand.

    ``addon_utils.enable()`` reloads only this package's ``__init__``, and only
    when its mtime changed, so an edited ``import_pof.py`` would keep running
    stale code after toggling the add-on off and on. Reloading explicitly at
    register time preserves that workflow.

    Only modules already in ``sys.modules`` are reloaded, so this never imports
    -- and never pulls ``bpy`` in -- anything that was not loaded already.

    Returns:
        The submodule objects named by :data:`_SUBMODULE_NAMES`, in order.
    """
    if reload_first:
        imports, with_classes = _scan_package()

        # A module declaring ``classes`` but missing from _SUBMODULE_NAMES is
        # never registered and Blender reports nothing at all, so name it here
        # rather than let it fail in silence.
        unregistered = sorted(with_classes.difference(_SUBMODULE_NAMES))
        if unregistered:
            log.warning(
                "%s define bpy classes but are missing from _SUBMODULE_NAMES, "
                "so nothing they declare is registered",
                ", ".join(unregistered),
            )

        for name in _reload_order(imports):
            module = sys.modules.get(f"{__name__}.{name}")
            if module is not None:
                importlib.reload(module)

    modules = []
    for name in _SUBMODULE_NAMES:
        full_name = f"{__name__}.{name}"
        module = sys.modules.get(full_name)
        if module is None:
            module = importlib.import_module(full_name)
        modules.append(module)
    return modules


def menu_func_import(self, context):
    """Add the importer to the File > Import menu."""
    self.layout.operator(constants.IMPORT_OT_IDNAME, text=constants.MENU_LABEL)


def menu_func_export(self, context):
    """Add the exporter to the File > Export menu."""
    self.layout.operator(constants.EXPORT_OT_IDNAME, text=constants.MENU_LABEL)


def _remove_menu_entries() -> None:
    """Remove both File menu entries, tolerating an already-removed one."""
    import bpy

    for menu, func in (
        (bpy.types.TOPBAR_MT_file_import, menu_func_import),
        (bpy.types.TOPBAR_MT_file_export, menu_func_export),
    ):
        try:
            menu.remove(func)
        except ValueError:
            # Not fatal -- the entry is gone either way. It does mean something
            # else removed it, so say so rather than leave that unexplained.
            log.warning(
                "%s was not in %s; it had already been removed",
                func.__name__, menu.__name__,
            )


def register():
    """Register the operators and add both File menu entries.

    Re-entrant: registering while already registered unregisters first. Blender
    accepts fresh class objects for an already-taken ``bl_idname``, so without
    the guard the reload leaves a duplicate row in each File menu.
    """
    import bpy

    if _registered:
        unregister()

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    for module in _submodules(reload_first=True):
        for cls in module.classes:
            bpy.utils.register_class(cls)
            _registered.append(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    """Remove the File menu entries and unregister the operators.

    Classes are popped as they are unregistered, so a failure part-way through
    cannot leave :data:`_registered` naming types that are already gone.
    """
    import bpy

    _remove_menu_entries()
    while _registered:
        cls = _registered.pop()
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError as e:
            # Not fatal: the class is gone, which is what unregistering wanted.
            # Blender drops the previous class when a reloaded one claims the
            # same bl_idname, so this is expected after a reload -- but it also
            # fires if registration half-failed, so it is worth logging.
            log.warning("Could not unregister %s: %s", cls.__name__, e)
