"""Values shared between the import and export halves of the add-on.

Everything here is part of the contract *between* ``import_pof`` and
``export_pof``: a name one side writes and the other side reads back. A value
used by only one half belongs in that half's module instead, and a value
defined by the POF byte stream belongs in :mod:`poformat` next to the other
wire-format constants.

This module must stay free of ``bpy`` and of any import from :mod:`poformat`.
The package ``__init__`` imports it at module scope to build the File menu
entries, so pulling Blender in here would undo the deferred-import arrangement
that lets the test suite run outside Blender.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Operator identity
# ---------------------------------------------------------------------------

#: ``bl_idname`` of the import operator. Duplicated here so ``__init__`` can
#: build its menu entry without importing the bpy-dependent operator module.
IMPORT_OT_IDNAME = "import_scene.descent3_pof"

#: ``bl_idname`` of the export operator.
EXPORT_OT_IDNAME = "export_scene.descent3_pof"

#: Text shown for both entries under File > Import and File > Export.
MENU_LABEL = "Descent 3 POF/OOF (.pof)"

# ---------------------------------------------------------------------------
# File dialog
# ---------------------------------------------------------------------------

#: Default extension offered by the import and export file browsers.
POF_FILENAME_EXT = ".pof"

#: File-browser filter. Both extensions are the same container: ``.oof`` is the
#: name Descent 3 ships models under, ``.pof`` the name its tools write.
POF_FILTER_GLOB = "*.pof;*.oof"

# ---------------------------------------------------------------------------
# Custom property keys
# ---------------------------------------------------------------------------
# Import stores the POF fields it cannot represent as Blender data on the
# object itself; export reads them back. The names are part of the saved .blend
# file, so renaming one silently drops that field from every model a user has
# already imported.

#: Submodel's own index within the file (``Submodel.index``).
PROP_KEY_INDEX = "pof_index"

#: Parent submodel index, or :data:`poformat.NO_PARENT` for a root submodel.
PROP_KEY_PARENT = "pof_parent"

#: ``SOF_*`` flag word derived from the submodel's property string.
PROP_KEY_FLAGS = "pof_flags"

#: ``bsp_info.movement_type``.
PROP_KEY_MOVEMENT_TYPE = "pof_movement_type"

#: ``bsp_info.movement_axis``.
PROP_KEY_MOVEMENT_AXIS = "pof_movement_axis"

#: Raw SOBJ property string -- the ``$rotate=`` / ``$fov=`` text the engine
#: parses. Kept verbatim so a round trip does not lose commands this add-on
#: does not itself interpret.
PROP_KEY_PROPERTIES = "pof_properties"

# ---------------------------------------------------------------------------
# Empty-object naming
# ---------------------------------------------------------------------------
# Gun points and attach points have no Blender equivalent, so they become
# empties. Export identifies them by name prefix, which is why import must use
# exactly these strings.

#: Name prefix for empties representing GPNT gun banks (``Gun_0``, ``Gun_1``).
GUN_EMPTY_PREFIX = "Gun_"

#: Name prefix for empties representing ATCH attach points.
ATTACH_EMPTY_PREFIX = "Attach_"

# ---------------------------------------------------------------------------
# Mesh naming
# ---------------------------------------------------------------------------

#: Fallback name stem for a submodel whose SOBJ record has an empty name.
SUBMODEL_FALLBACK_PREFIX = "Submodel_"

#: Name of the UV layer created on import. "UVMap" is Blender's own default,
#: which is what makes the layer the active one for new meshes.
UV_LAYER_NAME = "UVMap"

# ---------------------------------------------------------------------------
# Viewport markers
# ---------------------------------------------------------------------------
# Defaults only. The user can override both in the add-on preferences.

#: Viewport size of a gun-point empty. Larger than the attach-point marker so
#: the two are distinguishable at a glance.
GUN_EMPTY_DISPLAY_SIZE = 0.3

#: Viewport size of an attach-point empty.
ATTACH_EMPTY_DISPLAY_SIZE = 0.2
