"""Add-on preferences: the settings that belong to the *user*, not the project.

The split from :mod:`config` is deliberate: a checked-in ``descent3.toml``
describes a project's conventions, while these settings are personal and follow
the user between projects. Mixing the two commits one person's absolute texture
path to somebody else's repository.
"""

import logging

import bpy
from bpy.props import FloatProperty, StringProperty

from .constants import ATTACH_EMPTY_DISPLAY_SIZE, GUN_EMPTY_DISPLAY_SIZE

log = logging.getLogger(__package__)


class Descent3Preferences(bpy.types.AddonPreferences):
    """User-level settings for the Descent 3 add-on."""

    # Must match the add-on's module name; __package__ stays correct under both
    # the legacy add-on path and the extension loader, where the module is
    # nested under bl_ext.
    bl_idname = __package__

    texture_library: StringProperty(
        name="Texture Library",
        description=(
            "Optional folder holding your own copy of the game's textures. It "
            "is searched last, after the import dialog's Texture Folder, any "
            "directories the project's descent3.toml lists, and the model's "
            "own folder"
        ),
        subtype="DIR_PATH",
        default="",
    )
    gun_marker_size: FloatProperty(
        name="Gun Marker Size",
        description="Viewport size of the empty created for each gun point",
        default=GUN_EMPTY_DISPLAY_SIZE,
        min=0.01,
        max=10.0,
    )
    attach_marker_size: FloatProperty(
        name="Attach Marker Size",
        description="Viewport size of the empty created for each attach point",
        default=ATTACH_EMPTY_DISPLAY_SIZE,
        min=0.01,
        max=10.0,
    )

    def draw(self, context: bpy.types.Context) -> None:
        """Lay out the preferences panel."""
        layout = self.layout

        box = layout.box()
        box.label(text="Textures", icon="TEXTURE")
        box.prop(self, "texture_library")
        box.label(
            text="Project-specific settings belong in a descent3.toml "
                 "next to your models.",
            icon="INFO",
        )

        box = layout.box()
        box.label(text="Viewport Markers", icon="EMPTY_ARROWS")
        row = box.row()
        row.prop(self, "gun_marker_size")
        row.prop(self, "attach_marker_size")


def get_preferences(context: bpy.types.Context) -> Descent3Preferences | None:
    """Return the add-on's preferences, or ``None`` if they are unavailable.

    Returns:
        The preferences, or ``None`` when the add-on is not registered -- the
        normal case for headless scripts calling the conversion functions
        directly. Callers fall back to the built-in defaults.
    """
    try:
        addon = context.preferences.addons[__package__]
    except (AttributeError, KeyError) as e:
        # Expected when the conversion functions are driven directly rather than
        # through the operators (the headless scripts), so debug, not a warning.
        log.debug("Add-on preferences unavailable (%s); using defaults", e)
        return None
    return getattr(addon, "preferences", None)


classes = (Descent3Preferences,)
