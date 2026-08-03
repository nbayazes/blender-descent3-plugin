"""Descent 3 POF/OOF Blender Importer/Exporter.

Import and export Descent 3 3D model files (POF/OOF format). Supports vertices,
faces, UV mapping, materials, submodel hierarchy, gun points, weapon batteries,
attach points, and animation keyframes.

This module deliberately has no module-scope ``bpy`` import. The operators and
the conversion code live in :mod:`descent3_importer.import_pof` and
:mod:`descent3_importer.export_pof`, which are pulled in only from
:func:`register`. That keeps ``import descent3_importer`` -- and therefore
``from descent3_importer.poformat import ...`` -- working in a plain Python
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

import importlib
import logging
import sys

# constants is bpy-free by contract, so importing it here does not drag Blender
# in. Everything bpy-touching stays behind _submodules().
from . import constants

#: Submodules that import ``bpy``, in registration order. Each exposes a
#: ``classes`` tuple of the bpy types it wants registered.
_SUBMODULE_NAMES = ("import_pof", "export_pof")

#: Modules reloaded when the add-on is re-enabled, in dependency order.
#: ``constants`` comes first because the submodules copy its values into their
#: own globals with ``from .constants import ...`` at import time -- reloading
#: it afterwards would leave them holding the previous values. The menu funcs
#: below are the only call-time readers of ``constants``.
_RELOAD_NAMES = ("constants",) + _SUBMODULE_NAMES

#: Exactly the classes :func:`register` installed, so :func:`unregister` removes
#: those same objects. Re-deriving them from ``sys.modules`` risks unregistering
#: a fresh class object, which Blender rejects with "not the registered object"
#: after the menu entries have already been stripped.
_registered = []


def _submodules(reload_first: bool = False) -> list:
    """Import the bpy-touching submodules on demand.

    ``addon_utils.enable()`` reloads only this package's ``__init__``, and only
    when its own mtime changed, so an edited ``import_pof.py`` would keep running
    stale code after toggling the add-on off and on in Preferences. Reloading
    explicitly at register time preserves that workflow.

    Args:
        reload_first: Reload already-imported modules rather than reusing them.

    Returns:
        The submodule objects named by :data:`_SUBMODULE_NAMES`, in order.
    """
    if reload_first:
        for name in _RELOAD_NAMES:
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
            pass


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
        except RuntimeError:
            # Already unregistered -- Blender drops the previous class when a
            # reloaded one claims the same bl_idname. Nothing left to undo.
            pass
