"""Descent 3 POF/OOF Blender Importer/Exporter.

Import and export Descent 3 3D model files (POF/OOF format). Supports vertices,
faces, UV mapping, materials, submodel hierarchy, gun points, weapon batteries,
attach points, and animation keyframes.

This module deliberately has no module-scope ``bpy`` import. The operators and
the conversion code live in :mod:`descent3_plugin.import_pof` and
:mod:`descent3_plugin.export_pof`, which are pulled in only from
:func:`register`. That keeps ``import descent3_plugin`` -- and therefore
``from descent3_plugin.poformat import ...`` -- working in a plain Python
interpreter, which is what the pytest suite relies on.
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

#: Submodules that import ``bpy``, in registration order. Each exposes a
#: ``classes`` tuple of the bpy types it wants registered. Deliberately written
#: out rather than derived: a module missing from here loses its operators
#: outright, so which modules register -- and in what order -- is a decision
#: worth stating. :func:`_submodules` says so out loud if a module defines
#: ``classes`` without being named here. The reload order below is derived
#: instead, because a module missing from *that* changes nothing visible, which
#: is precisely how ``texexport`` stayed off it for so long.
_SUBMODULE_NAMES = ("preferences", "import_pof", "export_pof")

#: Exactly the classes :func:`register` installed, so :func:`unregister` removes
#: those same objects. Re-deriving them from ``sys.modules`` risks unregistering
#: a fresh class object, which Blender rejects with "not the registered object"
#: after the menu entries have already been stripped.
_registered = []


def _scan_package() -> tuple[dict[str, set[str]], set[str]]:
    """Read the package folder to find out what it contains.

    The reload list this feeds used to be written out by hand, and it quietly
    rotted: ``texexport`` was never added to it, so an edited ``texexport.py``
    kept running its previous code after toggling the add-on off and on -- the
    exact workflow :func:`_submodules` exists to preserve. Nothing fails when
    that list is wrong, which is why nobody noticed for so long. Reading the
    folder instead means it cannot go wrong again: a new module is picked up the
    moment it lands next to this file.

    The modules are parsed with :mod:`ast`, never imported. Importing them here
    would defeat the deferral that keeps ``import descent3_plugin`` working
    without Blender, and would drag ``bpy`` into a package that promises not to
    touch it outside :func:`register`.

    Returns:
        An ``(imports, with_classes)`` pair. ``imports`` maps every sibling
        module's name to the set of sibling modules it imports; ``with_classes``
        holds the names of the modules that define a module-level ``classes``
        tuple, i.e. the ones that have bpy types to register.
    """
    directory = os.path.dirname(os.path.abspath(__file__))
    try:
        entries = sorted(os.listdir(directory))
    except OSError as e:
        # Survivable: a package folder that cannot be listed is one that cannot
        # be reloaded, and running the already-imported code is a far better
        # outcome than refusing to register at all.
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
            # Keep the module in the reload set with no recorded dependencies.
            # Reloading it in a suboptimal position is recoverable; leaving it
            # out is the failure this whole mechanism exists to prevent, and a
            # module that really is broken raises on reload, which is loud.
            log.warning("Could not scan %s.py, assuming no dependencies: %s", name, e)
            continue

        # Every relative import in the file, not just the module-scope ones: a
        # dependency imported inside a function is still a dependency, and a
        # missed edge is a wrong reload order.
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

    ``from .x import NAME`` binds the value, not the module, so every module
    holds copies of whatever its dependencies exported at import time. Reloading
    a dependency *after* its dependents therefore leaves them holding the values
    from before the edit -- a failure that shows up as code behaving the way it
    did two saves ago, with nothing to point at.

    Args:
        imports: Module name -> the sibling modules it imports, as returned by
            :func:`_scan_package`.

    Returns:
        Every name in ``imports``, each preceded by everything it imports.
        Modules that do not depend on each other come out alphabetically, so the
        order is stable between runs.
    """
    pending = {name: set(deps) for name, deps in imports.items()}
    ordered = []
    while pending:
        ready = sorted(name for name, deps in pending.items() if not deps)
        if not ready:
            # An import cycle has no correct reload order at all, and Python
            # tolerates some of them at run time, so break it deterministically
            # rather than dropping the modules involved on the floor.
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
    when its own mtime changed, so an edited ``import_pof.py`` would keep running
    stale code after toggling the add-on off and on in Preferences. Reloading
    explicitly at register time preserves that workflow.

    What gets reloaded is read off the package folder by :func:`_scan_package`
    rather than listed by hand. Only modules that are already in
    ``sys.modules`` are reloaded, so this never imports anything -- and never
    pulls ``bpy`` in -- that was not loaded already.

    Args:
        reload_first: Reload already-imported modules rather than reusing them.

    Returns:
        The submodule objects named by :data:`_SUBMODULE_NAMES`, in order.
    """
    if reload_first:
        imports, with_classes = _scan_package()

        # A module that declares ``classes`` but is missing from
        # _SUBMODULE_NAMES is never registered: its operators simply do not
        # exist, and Blender reports nothing at all. The fix is one word in the
        # tuple above, so name the module rather than let it fail in silence.
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
            # Not fatal -- the entry is gone either way, which is the goal. It
            # does mean something else removed it, so say so rather than let a
            # disappearing menu entry go unexplained.
            log.warning(
                "%s was not in %s; it had already been removed",
                func.__name__, menu.__name__,
            )


def register():
    """Register the operators and add both File menu entries.

    Re-entrant: registering while already registered unregisters first. Without
    that guard the reload in :func:`_submodules` would hand Blender fresh class
    objects, which it accepts for an already-taken ``bl_idname``, leaving a
    duplicate row in each File menu and a doubled :data:`_registered`.
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
            # same bl_idname, so this is expected after an add-on reload -- but
            # it also fires if registration half-failed, so it is worth saying.
            log.warning("Could not unregister %s: %s", cls.__name__, e)
