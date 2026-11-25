"""Reading the add-on preferences: get() and DEFAULTS.

The AddonPreferences class itself, with the callbacks that recook or rebuild when a
preference changes, is in ui/preferences.py.
"""

import types

import bpy

from ...shared import contract
from . import hython, paths

# The default of every preference, by tab: the properties of ui/preferences.py take
# theirs from here, and get() gives them while the add-on is not registered.
DEFAULTS = {
    # Modifiers
    "default_target": "MODIFIER",
    "generator_at_cursor": True,
    "live_default": True,
    "default_output": "NATIVE",
    "edit_mode_cook": "LIVE",
    "parameter_limits": "MINIMUM",
    "houdini_interface": True,
    "follow_hide_when": True,
    "folders_open": False,
    "bring_back_extras": True,
    # Output
    "points_above": 200_000,
    "sphere_frequency": contract.SPHERE_FREQUENCY,
    "curve_radius": 0.005,
    "point_radius": 0.02,
    "live_volume_folder": "",
    "applied_volumes": "BESIDE",
    "applied_folder_name": "houdini_volumes",
    "applied_folder": "",
    "relative_volume_paths": True,
    # Shelf
    "include_packages": True,
    "include_native": True,
    "all_versions": False,
    "hidden_tools": "",
    "strip_labs_prefix": True,
    "group_by_category": True,
    "search_help": True,
    "refresh_on_start": True,
    "catalog_depth": 2,
    # Houdini
    "hython_path": "",
    "skip_packages": False,
    "start_with_blender": False,
    "idle_minutes": 0,
    "startup_timeout": hython.STARTUP_TIMEOUT,
    # Viewport
    "show_feedback": True,
    "show_indicator": True,
    "show_legend": True,
}
_UNREGISTERED = types.SimpleNamespace(folders=(), **DEFAULTS)


def get(missing=_UNREGISTERED):
    """The add-on preferences. While the add-on is not registered, `missing`: by default
    DEFAULTS as attributes and no shelf folder (pass None to tell that case apart)."""
    addon = bpy.context.preferences.addons.get(paths.PACKAGE)
    found = addon.preferences if addon else None
    # While the extension registers again, its entry is there but its preferences are not yet.
    return found if found is not None else missing
