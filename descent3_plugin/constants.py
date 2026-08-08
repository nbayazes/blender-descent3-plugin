"""Values shared between the import and export halves of the add-on.

A value used by only one half belongs in that half's module; a value defined by
the POF byte stream belongs in :mod:`poformat`. The custom-property keys are the
deliberate exception -- declared together even where only one half reads a key,
because they are a contract with the user's saved ``.blend`` file and are only
safe to review as a set.

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

# Import stores what it cannot represent as Blender data as a custom property on
# the datablock it describes -- the object for submodel fields, the material for
# the texture. Renaming a key silently drops that field from every model a user
# has already imported. A key export never writes back is not dead: it records
# what the file said, and is how the exporter recognises an object as ours.

#: Submodel's own index within the file (``Submodel.index``).
#:
#: Export reassigns indices from the export order rather than writing this back
#: -- the exported set need not be the imported set, so a stored index collides
#: or leaves gaps as soon as a subset is exported. It reads the key only to
#: recognise a geometry-less submodel, which imports as an *Empty*, so that
#: Empty is exported instead of dropped.
PROP_KEY_INDEX = "pof_index"

#: Parent submodel index, or :data:`poformat.NO_PARENT` for a root submodel.
#:
#: Reference only. Export derives the hierarchy from Blender's own object
#: parenting, so reading this back would override the user's outliner edits.
PROP_KEY_PARENT = "pof_parent"

#: ``SOF_*`` flag word derived from the submodel's property string.
#:
#: Reference only, never written back: the SOBJ record has no flag word. The
#: engine re-derives every bit from the property string (reference/polymodel.cpp,
#: SetPolymodelProperties), so :data:`PROP_KEY_PROPERTIES` carries the flags.
PROP_KEY_FLAGS = "pof_flags"

#: ``bsp_info.movement_type``.
PROP_KEY_MOVEMENT_TYPE = "pof_movement_type"

#: ``bsp_info.movement_axis``.
PROP_KEY_MOVEMENT_AXIS = "pof_movement_axis"

#: Raw SOBJ property string -- the ``$rotate=`` / ``$fov=`` text the engine
#: parses. Kept verbatim so a round trip does not lose commands this add-on
#: does not itself interpret.
PROP_KEY_PROPERTIES = "pof_properties"

#: Name the submodel had in the file, recorded on the object built from it.
#:
#: A file may legally hold two submodels called ``Wing`` -- the engine treats the
#: field as a label, not an identifier -- and Blender uniquifies the second to
#: ``Wing.001``, which export then wrote into the model. A file may equally state
#: ``Wing`` and ``Wing.001`` itself, so the ``.NNN`` heuristic cannot settle it.
#: Export prefers the recorded name only when the object's current name is that
#: name plus Blender's suffix; any other rename is the user talking, and wins.
PROP_KEY_SUBMODEL_NAME = "pof_name"

#: Custom property on an *attach-point empty* recording that the file supplied
#: an explicit up vector for it, in a NATH chunk.
#:
#: The direction and roll both live in the empty's rotation, so the vectors need
#: no property -- but an attach point with no NATH entry leaves the attached
#: model's roll undefined, a legal state rather than missing data, and every
#: empty has a roll whether or not anybody chose one. Writing NATH for all of
#: them invents an up vector the model never specified; writing it for none
#: loses the one the model did state.
PROP_KEY_HAS_UVEC = "pof_has_uvec"

#: Custom property on a *Material* recording the Descent 3 texture ID it stands
#: for. Import writes it, export reads it.
#:
#: The name alone is not enough: ``metal`` and ``metal.001`` can be two distinct
#: bitmaps shipped by the game, indistinguishable by name from a material the
#: user duplicated, and export merged them and lost the second texture. A
#: material carrying this key exports as the ID it carries whatever Blender has
#: since done to its name; only materials with no provenance fall back to the
#: ``.NNN`` heuristic in :mod:`~descent3_plugin.naming`.
#:
#: Spelled ``d3_`` rather than ``pof_`` because it names a game asset rather than
#: a POF record field, and is equally meaningful set by hand to pin the export
#: name of a material the user authored.
PROP_KEY_TEXTURE = "d3_texture"

#: Marks a *Material* this add-on created from nothing. Import writes and reads
#: it; export ignores it. Distinct from :data:`PROP_KEY_TEXTURE` because that key
#: is also stamped on *adopted* materials -- and may be set by hand to pin an
#: export name -- so using it as the authorship signal let the second import
#: restyle a material the user authored.
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
#: Blender space.
#:
#: +Z is the axis Blender draws a SINGLE_ARROW empty's arrow along, so the
#: direction is visible in the viewport. Consequence: an unrotated new empty
#: means "straight up", not the fixed forward direction every marker used to be
#: written with -- a direction never read from anything, so the changed default
#: is the first time the field has meant anything.
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
