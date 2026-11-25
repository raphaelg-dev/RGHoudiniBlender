"""RG Houdini Bridge: Houdini HDAs and nodes as non-destructive Blender modifiers,
cooked by a local hython server."""

from .blender.core import cook, properties, streams
from .blender.features import cache, code_editor, display, groups, selection, shelf, viewport
from .blender.ui import modifier_panel, operators, panel, parameter_rows, preferences

# Registered in this order, unregistered in the reverse one (a feature registers its own
# modules). The preferences come first, and a PropertyGroup is registered before anything
# that refers to it.
MODULES = (
    preferences,
    properties,
    operators,
    parameter_rows,
    modifier_panel,  # unregistered after the features: it turns the Houdini interface off while the modifiers exist
    panel,
    shelf,
    groups,
    cook,  # its unregister() stops hython and the worker thread once its timers and handlers are gone
    cache,
    viewport,
    display,  # unregistered after the selection: a running one restores its cut first, then every flag is lifted
    code_editor,  # unregistered while the Houdini interface is on: what was typed reaches its parameter first
    selection,  # unregistered first of the features: a running selection restores everything
    streams,
)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
