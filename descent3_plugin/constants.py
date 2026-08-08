"""Values shared between the import and export halves of the add-on.

A value used by only one half belongs in that half's module; a value defined by
the POF byte stream belongs in :mod:`poformat`. The custom-property keys are the
deliberate exception -- declared together even where only one half reads a key,
because they are a contract with the user's saved ``.blend`` file and are only
safe to review as a set. ``docs/custom-properties.md`` explains each one.

Must stay free of ``bpy`` and of any :mod:`poformat` import: the package
``__init__`` imports this at module scope to build the File menu entries, so
pulling Blender in here would undo the deferred-import arrangement that lets the
test suite run outside Blender.
"""

from __future__ import annotations

#: Duplicated here so ``__init__`` can build its menu entry without importing
#: the bpy-dependent operator module.
IMPORT_OT_IDNAME = "import_scene.descent3_pof"

EXPORT_OT_IDNAME = "export_scene.descent3_pof"

#: Used for both the File > Import and the File > Export entry.
MENU_LABEL = "Descent 3 POF/OOF (.pof)"

POF_FILENAME_EXT = ".pof"

#: File-browser filter. Both extensions are the same container: ``.oof`` is the
#: name Descent 3 ships models under, ``.pof`` the name its tools write.
POF_FILTER_GLOB = "*.pof;*.oof"

# These keys are a contract with the user's saved ``.blend``.
# ``docs/custom-properties.md`` explains each one in full.

#: Index of the submodel within the file (``Submodel.index``), on the *object*.
#: Export reads only its presence, never the value.
PROP_KEY_INDEX = "pof_index"

#: Parent submodel's index on the *object*, or :data:`poformat.NO_PARENT` for a
#: root submodel. Export does not read it back.
PROP_KEY_PARENT = "pof_parent"

#: ``SOF_*`` flag word derived from the submodel's property string, on the
#: *object*. Export does not read it back.
PROP_KEY_FLAGS = "pof_flags"

#: ``bsp_info.movement_type``, on the *object*. Read back on export.
PROP_KEY_MOVEMENT_TYPE = "pof_movement_type"

#: ``bsp_info.movement_axis``, on the *object*. Read back on export.
PROP_KEY_MOVEMENT_AXIS = "pof_movement_axis"

#: Raw SOBJ property string on the *object* -- the ``$rotate=`` / ``$fov=``
#: text the engine parses. Read back on export.
PROP_KEY_PROPERTIES = "pof_properties"

#: Name the submodel had in the file, on the *object* built from it. Export
#: reads it back, but only while the object still wears that name.
PROP_KEY_SUBMODEL_NAME = "pof_name"

#: Records that the file supplied an explicit up vector, in a NATH chunk, for
#: this *attach-point empty*. Export reads it, and writes NATH only where set.
PROP_KEY_HAS_UVEC = "pof_has_uvec"

#: The Descent 3 texture ID a *Material* stands for. Import writes it, export
#: reads it.
PROP_KEY_TEXTURE = "d3_texture"

#: Marks a *Material* this add-on created from nothing. Import writes and
#: reads it; export ignores it.
PROP_KEY_ADDON_MATERIAL = "d3_addon_material"

# Gun points and attach points have no Blender equivalent, so they become
# empties, which export identifies by name prefix -- import must use exactly
# these strings.

#: Prefix for empties representing GPNT gun banks (``Gun_0``, ``Gun_1``).
GUN_EMPTY_PREFIX = "Gun_"

#: Prefix for empties representing ATCH attach points.
ATTACH_EMPTY_PREFIX = "Attach_"

# A marker's direction is kept in the empty's own rotation, the only place the
# user can see and change it. The axes below are what that rotation is read
# against; import and export must agree or every gun turns ninety degrees on a
# round trip. Plain tuples, not ``mathutils.Vector``, since this module has to
# import outside Blender.

#: Local axis of a gun or attach empty that points where the marker points, in
#: Blender space. +Z is the axis Blender draws a SINGLE_ARROW empty's arrow
#: along, so the arrow in the viewport is the direction written to the file.
MARKER_FORWARD_AXIS = (0.0, 0.0, 1.0)

#: Local axis standing for an attach point's up vector -- its roll about
#: :data:`MARKER_FORWARD_AXIS`. +Y is what Blender's own ``to_track_quat("Z",
#: "Y")`` treats as up when it aims the axis above, so the two agree by
#: construction.
MARKER_UP_AXIS = (0.0, 1.0, 0.0)

#: Fallback name stem for a submodel whose SOBJ record has an empty name.
SUBMODEL_FALLBACK_PREFIX = "Submodel_"

#: "UVMap" is Blender's own default, which is what makes the layer created on
#: import the active one for new meshes.
UV_LAYER_NAME = "UVMap"

# Defaults only. The user can override both in the add-on preferences.

#: Larger than the attach-point marker so the two are distinguishable at a
#: glance.
GUN_EMPTY_DISPLAY_SIZE = 0.3

ATTACH_EMPTY_DISPLAY_SIZE = 0.2
