"""Values shared between the import and export halves of the add-on.

Everything here is part of the contract *between* ``import_pof`` and
``export_pof``: a name one side writes and the other side reads back. A value
used by only one half belongs in that half's module instead, and a value
defined by the POF byte stream belongs in :mod:`poformat` next to the other
wire-format constants.

The custom-property keys below are the one deliberate exception, and they are
declared here together even where only one half ever reads a given key. They are
not really a contract between the two modules at all: they are a contract with
the user's saved ``.blend`` file, so renaming one costs data in files that
already exist, and -- as :data:`PROP_KEY_ADDON_MATERIAL` records the hard way --
two of them meaning subtly different things is a bug that only shows up on the
*second* import. Both hazards are about the set of names as a whole, and a set
split across two modules is one nobody reads as a set.

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
# Import stores what it cannot represent as Blender data as a custom property on
# the datablock it describes -- the object for submodel fields, the material for
# the texture it stands for. The names are part of the saved .blend file, so
# renaming one silently drops that field from every model a user has already
# imported.
#
# Most of them are read back on export, but not all, and the ones that are not
# say so individually below rather than being quietly assumed to be. A key that
# export ignores is not dead: it records what the file said, which is what lets
# somebody see why an object is shaped the way it is, and it is how the exporter
# recognises an object as one of ours in the first place.

#: Submodel's own index within the file (``Submodel.index``).
#:
#: Export does not write this back -- indices are reassigned from the export
#: order, since the set of objects being exported need not be the set that was
#: imported. It reads it for a different reason: a submodel with no geometry
#: comes in as an *Empty*, and this key is what tells that Empty apart from every
#: other empty in the user's scene, so it can be exported as the submodel it is
#: instead of being dropped along with the rotating joint it stands for.
PROP_KEY_INDEX = "pof_index"

#: Parent submodel index, or :data:`poformat.NO_PARENT` for a root submodel.
#:
#: Recorded for reference only. Export derives the hierarchy from Blender's own
#: object parenting, which is the copy the user can see and edit; reading this
#: one back would override what they did in the outliner with what the file said
#: before they touched it.
PROP_KEY_PARENT = "pof_parent"

#: ``SOF_*`` flag word derived from the submodel's property string.
#:
#: Recorded for reference only, and deliberately never written back: the SOBJ
#: record has no flag word. The engine re-derives every bit from the property
#: string as it parses it (reference/polymodel.cpp, SetPolymodelProperties), so
#: :data:`PROP_KEY_PROPERTIES` is what actually carries the flags across.
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
#: The object's name is normally the submodel's name, and export writes it
#: straight back. It stops being the submodel's name when a file holds two
#: submodels called ``Wing`` -- which is legal, and which real models do, since
#: the engine treats the field as a label rather than an identifier. Blender
#: cannot: two objects cannot share a name, so the second becomes ``Wing.001``
#: and the export wrote Blender's bookkeeping into the model.
#:
#: This is :data:`PROP_KEY_TEXTURE`'s story with the material replaced by the
#: object, and the ``.NNN`` heuristic is no more able to settle it here than it
#: was there: a file may perfectly well name two submodels ``Wing`` and
#: ``Wing.001``, and collapsing the second onto the first would lose a name the
#: file actually stated. So export prefers what was recorded, and only when the
#: object's current name is that same name wearing Blender's uniquifying suffix.
#: Rename the object to anything else and the rename wins, because at that point
#: it is the user talking rather than Blender.
PROP_KEY_SUBMODEL_NAME = "pof_name"

#: Custom property on an *attach-point empty* recording that the file supplied
#: an explicit up vector for it, in a NATH chunk.
#:
#: The direction and the roll both live in the empty's rotation, so the vectors
#: themselves need no custom property. Whether the file *had* a NATH entry does:
#: an attach point without one leaves the attached model's roll undefined, and
#: every empty has a roll whether or not anybody chose it. Writing NATH for all
#: of them would invent an up vector the model never specified; writing it for
#: none loses the one the model did. This flag is which of the two an attach
#: point is.
PROP_KEY_HAS_UVEC = "pof_has_uvec"

#: Custom property on a *Material* recording the Descent 3 texture ID it stands
#: for. Import writes it, export reads it.
#:
#: The material's name is normally the texture ID, and for one model in an empty
#: scene that is enough. It stops being enough the moment two texture IDs differ
#: only by something Blender treats as its own bookkeeping: a model that
#: references both ``metal`` and ``metal.001`` -- two real, distinct bitmaps
#: shipped by the game -- is indistinguishable, by name alone, from one material
#: the user duplicated in the outliner. Export guessed "duplicate", merged them,
#: pointed every face at ``metal``, and the second texture was gone.
#:
#: Recording the ID the material was built from turns that guess into a fact: a
#: material carrying this key exports as the ID it carries, whatever Blender has
#: since done to its name, and only materials with no provenance fall back to the
#: ``.NNN`` heuristic in :mod:`~descent3_plugin.naming`.
#:
#: Spelled ``d3_`` rather than ``pof_`` like the submodel fields above because it
#: is not a field of a POF record: it names a game asset, and it is just as
#: meaningful set by hand on a material that never came out of a file, which is
#: how a user pins the export name of a material they authored themselves.
PROP_KEY_TEXTURE = "d3_texture"

#: Custom property on a *Material* marking the datablock as one this add-on
#: created. Import writes it; only import reads it.
#:
#: This exists because :data:`PROP_KEY_TEXTURE` answers a different question and
#: was briefly made to answer both. Import reuses a material whose name matches a
#: texture ID, and it may finish off a material it built itself -- attaching the
#: image a first import could not find -- but it must never restyle one the user
#: authored. "Did this add-on build it?" was read off the texture key, and the
#: texture key is also written onto an *adopted* material so export knows which
#: bitmap those faces mean. So the user's own ``Metal`` survived the first import
#: untouched, came out of it carrying provenance, and the second import read that
#: provenance as its own signature and wired an image into their material after
#: all. The bug simply arrived one import later.
#:
#: The same collision hits a material nobody imported: the docstring above
#: invites a user to set ``d3_texture`` by hand to pin an export name, and doing
#: so must not hand the importer permission to edit it.
#:
#: So authorship gets its own mark, written *only* where a material is created
#: from nothing. A material carrying the texture key and not this one is
#: somebody's own work that this add-on has merely recognised.
PROP_KEY_ADDON_MATERIAL = "d3_addon_material"

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

# A marker carries a direction as well as a position -- where a gun fires, where
# an attached model faces -- and the only place to keep a direction that the user
# can see and change is the empty's own rotation. The two axes below are what
# that rotation is read against, and import and export must name the same ones or
# every gun in the model turns by ninety degrees on the round trip.
#
# Plain tuples, not ``mathutils.Vector``: this module has to import outside
# Blender.

#: Local axis of a gun or attach empty that points where the marker points, in
#: Blender space.
#:
#: +Z because that is the axis Blender draws a SINGLE_ARROW empty's arrow along.
#: The direction a marker fires in is then something the user can see in the
#: viewport and correct by rotating the empty, rather than a number hidden in a
#: custom property. It costs one thing: a brand-new empty, unrotated, means
#: "straight up" rather than the fixed forward direction every marker used to be
#: written with. That direction was never read from anything, so what looks like
#: a changed default is the first time the field has meant anything at all.
MARKER_FORWARD_AXIS = (0.0, 0.0, 1.0)

#: Local axis standing for an attach point's up vector -- its roll about
#: :data:`MARKER_FORWARD_AXIS`. +Y is what Blender's own ``to_track_quat("Z",
#: "Y")`` treats as up when it aims the axis above, so the two agree by
#: construction.
MARKER_UP_AXIS = (0.0, 1.0, 0.0)

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
