"""HDA parameters as inputs of the modifier's node group.

Each Houdini modifier is a Geometry Nodes modifier whose group shows the
cooked mesh (Object Info → Group Output). The HDA parameters become inputs of
that group: animatable, drivable, stored with the modifier. The extension's
panel (ui/modifier_panel.py) draws them inside the modifier, in Houdini's order
and folders (layout.py lays them out, ui/parameter_rows.py draws them), and hides
them from Blender's own drawing (apply_houdini_interface). With the preference "Houdini
Interface" off, Blender draws them as Geometry Nodes inputs, folders as panels.

Blender 5.2 exposes modifier inputs as `modifier.properties.inputs.<id>.value`
(menus are enums of item names); a Geometry Nodes modifier has no ID properties.

kinds.py is the registry of the kinds of parameters (KINDS). interface.py builds the
node group and decides who draws its inputs, layout.py walks the parameters as the panel lays them out,
values.py reads and writes the values and holds them within their limits, ramps.py
handles the nodes holding ramps, inputs.py the items of the modifier inputs.
"""

from .inputs import copy_input, modifier_input
from .interface import (
    FOLDER_SEPARATOR,
    STREAMS_NODE,
    apply_houdini_interface,
    apply_houdini_interface_to_all,
    apply_houdini_interface_to_modifier,
    build_node_group,
    houdini_interface_on,
    is_hidden,
    sync_interface,
)
from .kinds import KINDS, group_selection, group_suggestions, kind_of, menu_tokens, pick_group, refresh_from_answer
from .layout import clear as clear_layouts
from .layout import drawn, listed, panel_id, set_open_tab, tab_key, walk
from .layout import forget as forget_layout
from .ramps import ramp_node
from .values import (
    apply_values,
    hold_within_limits,
    limits,
    read_value,
    read_values,
    refresh_limits,
    set_socket_limits,
    store_range,
    write_value,
)

__all__ = [
    # the registry of the kinds
    "KINDS", "kind_of",
    # the node group
    "build_node_group", "sync_interface", "is_hidden", "FOLDER_SEPARATOR", "STREAMS_NODE",
    # who draws the inputs
    "apply_houdini_interface", "apply_houdini_interface_to_modifier", "apply_houdini_interface_to_all",
    "houdini_interface_on",
    # how the panel lays them out (ui/parameter_rows.py draws them)
    "walk", "listed", "drawn", "panel_id", "tab_key", "set_open_tab", "forget_layout", "clear_layouts",
    # the values
    "read_value", "write_value", "read_values", "apply_values", "store_range", "menu_tokens", "refresh_from_answer",
    # the group fields
    "group_suggestions", "pick_group", "group_selection",
    # the limits
    "limits", "set_socket_limits", "refresh_limits", "hold_within_limits",
    # the modifier inputs and the ramp nodes
    "modifier_input", "copy_input", "ramp_node",
]
