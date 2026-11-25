"""Add Houdini Node: a button under Blender's Add Modifier, with the Houdini logo, that
opens a search of every tool of the shelf, like Houdini's Tab menu: the picked tool becomes
a Houdini modifier of the object. The favorites come first.

The button is appended to Blender's modifier panel (DATA_PT_modifiers), between Add
Modifier and the stack, for objects that can take a Houdini modifier.
"""

import os

import bpy
import bpy.utils.previews
import numpy
from bpy.props import EnumProperty

from ...core import feedback, icons, paths
from . import catalog

ICON_SIZE = 64  # pixels of the button's icon: the logo, its see-through spiral filled with black
ICON_FILE = "logo_on_black_v2.png"  # raise the version when the drawing changes
_previews = None
_logo_icon = 0  # the icon_id of the button's icon, 0 until it is made
# The items of the search, kept here: Blender reads the strings of a dynamic enum after
# the callback returns, so they must stay alive as long as the search is open.
_items = []
_keys = []


def _on_black(logo):
    """RGBA bytes of the button's icon: `logo` (an ICON_SIZE square float array, top row
    first, whose spiral is transparent) over black, so that the spiral is black."""
    icon = numpy.ones((ICON_SIZE, ICON_SIZE, 4), dtype=numpy.float32)
    icon[..., :3] = logo[..., :3] * logo[..., 3:]
    return bytearray((numpy.clip(icon, 0.0, 1.0) * 255 + 0.5).astype(numpy.uint8).tobytes())


def _load_logo():
    """Timer: once hython has rendered the Houdini logo (never shipped), make the button's
    icon. Not in register() or draw(), where Blender data cannot be loaded."""
    global _logo_icon
    if _previews is None:
        return None
    source = paths.houdini_logo()
    if not os.path.exists(source):
        return 2.0
    path = os.path.join(paths.status_icons_dir(), ICON_FILE)
    try:
        if not os.path.exists(path):
            image = bpy.data.images.load(source, check_existing=False)
            try:
                image.scale(ICON_SIZE, ICON_SIZE)
                pixels = numpy.empty(ICON_SIZE * ICON_SIZE * 4, dtype=numpy.float32)
                image.pixels.foreach_get(pixels)
            finally:
                bpy.data.images.remove(image)
            icons.write_png(path, ICON_SIZE, _on_black(pixels.reshape(ICON_SIZE, ICON_SIZE, 4)[::-1]))
        _logo_icon = icons.load_preview(_previews, "logo_on_black", path).icon_id
    except (RuntimeError, OSError, ValueError) as error:
        feedback.log.warning("Add Houdini Node icon not built (%s)", error)
        return None
    feedback.redraw()
    return None


def _tool_items(self, context):
    favorites = catalog.favorite_tools()
    favorite_keys = {catalog.tool_key(tool) for tool in favorites}
    ordered = favorites + [tool for tool in catalog.listed_tools() if catalog.tool_key(tool) not in favorite_keys]
    _keys[:] = [catalog.tool_key(tool) for tool in ordered]
    _items[:] = [(str(index), catalog.display_name(tool), tool.get("summary") or tool["type"],
                  catalog.icon(tool), index) for index, tool in enumerate(ordered)]
    return _items


class HOUDINI_BRIDGE_OT_add_node(bpy.types.Operator):
    """Add a Houdini node or HDA as a modifier of this object: type to search every tool of the shelf"""

    bl_idname = "houdini_bridge.add_node"
    bl_label = "Add Houdini Node"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "tool"

    tool: EnumProperty(name="Tool", items=_tool_items, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return catalog.takes_modifier(context.object)

    def invoke(self, context, event):
        if not catalog.state.tools:
            self.report({"WARNING"}, "The shelf is empty: read the Houdini tools first (Shelf tab, Refresh)")
            return {"CANCELLED"}
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        index = int(self.tool) if self.tool.isdigit() else -1
        if not 0 <= index < len(_keys):
            return {"CANCELLED"}
        error = catalog.use_tool(context, _keys[index], "MODIFIER")
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        return {"FINISHED"}


def _draw_button(self, context):
    """The Add Houdini Node button, under Blender's Add Modifier."""
    if not catalog.takes_modifier(context.object):
        return
    icon = {"icon_value": _logo_icon} if _logo_icon else {"icon": "ADD"}
    self.layout.operator("houdini_bridge.add_node", text="Add Houdini Node", **icon)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    bpy.utils.register_class(HOUDINI_BRIDGE_OT_add_node)
    bpy.types.DATA_PT_modifiers.append(_draw_button)
    bpy.app.timers.register(_load_logo, first_interval=0.5, persistent=True)


def unregister():
    global _previews, _logo_icon
    if bpy.app.timers.is_registered(_load_logo):
        bpy.app.timers.unregister(_load_logo)
    bpy.types.DATA_PT_modifiers.remove(_draw_button)
    bpy.utils.unregister_class(HOUDINI_BRIDGE_OT_add_node)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
    _logo_icon = 0
    _items.clear()
    _keys.clear()
