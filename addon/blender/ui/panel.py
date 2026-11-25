"""The Houdini sidebar: one panel, a column of tabs on its left (Shelf, Modifiers,
Groups), the open tabs on its right.

The Modifiers tab, drawn here, lists the Houdini modifiers of the active object: one row
each, with the HDA's icon, its name, its cook state and the first line of its errors; a
click on a row shows that modifier in the Properties editor (ui/modifier_panel.py). The
Shelf and Groups tabs are drawn by their features.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass

import bpy
import bpy.utils.previews
from bpy.props import EnumProperty, StringProperty

from ..core import icons, paths, targets
from ..core.modifiers import houdini_modifiers
from ..features import groups
from ..features.shelf import catalog
from . import modifier_panel


def _active_modifiers(context):
    obj = context.active_object
    if obj is None or obj.type not in targets.INPUT_TYPES:
        return obj, []
    return obj, houdini_modifiers(obj)


def draw_modifiers(layout, context):
    """The Modifiers tab: an overview of the Houdini modifiers of the active object."""
    obj, modifiers = _active_modifiers(context)
    if not modifiers:
        row = layout.row()
        row.enabled = False
        row.label(text="Apply an HDA from the Shelf tab", icon="INFO")
        return
    layout.operator("houdini_bridge.apply_all", icon="CHECKMARK").object = obj.name
    cooking = modifier_panel.cooking_keys()
    column = layout.column(align=True)
    for modifier, tree in modifiers:
        settings = tree.houdini_bridge
        row = column.row(align=True)
        row.active = not targets.is_bypassed(modifier)  # faded, as Houdini fades a bypassed node
        row.operator("houdini_bridge.show_parameters", text=settings.label or modifier.name, emboss=False,
                     **modifier_panel.tool_icon(settings)).tree = tree.name
        state = modifier_panel.modifier_state(tree, modifier, cooking)
        row.label(text="", icon_value=modifier_panel.state_icon(state))
        if settings.errors:
            error = column.row()
            error.alert = True  # red text, no frame
            error.label(text=settings.errors.splitlines()[0][:modifier_panel.ERROR_TEXT_WIDTH], icon="ERROR")


def _modifiers_failed(context):
    return any(tree.houdini_bridge.errors for _, tree in _active_modifiers(context)[1])


@dataclass(frozen=True)
class Tab:
    """One tab of the sidebar. `available` greys its button out when it has nothing to
    show (it can still be opened); `alert` paints the button red. `houdini_icon`, when
    set, is a PNG that hython renders from the local Houdini install into the icon folder
    (never shipped); until it exists, the tab shows Blender's `icon`."""

    key: str
    label: str
    icon: str
    houdini_icon: str | None
    description: str
    draw: Callable
    available: Callable | None = None
    alert: Callable | None = None


# Top to bottom. A tab with a Houdini icon also needs its line in houdini/catalog.py
# INTERFACE_ICONS.
TABS = (
    Tab("SHELF", "Shelf", "ASSET_MANAGER", None,
        "Houdini tools: search, favorites, open an HDA file", catalog.draw_tab),
    Tab("MODIFIERS", "Modifiers", "MODIFIER", "tab_modifiers.png",
        "The Houdini modifiers of the active object", draw_modifiers, alert=_modifiers_failed),
    Tab("GROUPS", "Groups", "GROUP_VERTEX", "tab_groups.png",
        "Groups of the active mesh, sent to Houdini", groups.draw_tab, available=groups.can_group),
)
TAB_STRIP_WIDTH = 1.4  # in UI units: one icon button and its margin
TAB_BUTTON_HEIGHT = 1.4
_previews = None


def _icon(tab):
    """{"icon_value": id} once the tab's Houdini icon is on disk, else {"icon": Blender's}."""
    if _previews is not None and tab.houdini_icon:
        preview = _previews.get(tab.houdini_icon)
        if preview is None:
            path = os.path.join(paths.icons_dir(), tab.houdini_icon)
            if os.path.exists(path):
                preview = icons.load_preview(_previews, tab.houdini_icon, path)
        if preview is not None:
            return {"icon_value": preview.icon_id}
    return {"icon": tab.icon}


def _shown(window_manager):
    return set(window_manager.houdini_bridge_tabs) or {TABS[0].key}


class HOUDINI_BRIDGE_OT_sidebar_tab(bpy.types.Operator):
    """Show this tab alone; Shift+click to show it next to the others, or hide it"""

    bl_idname = "houdini_bridge.sidebar_tab"
    bl_label = "Sidebar Tab"
    bl_options = {"INTERNAL"}

    key: StringProperty()

    @classmethod
    def description(cls, context, properties):
        tab = next((tab for tab in TABS if tab.key == properties.key), None)
        if tab is None:
            return cls.__doc__
        return "%s: %s.\nShift+click to show several tabs" % (tab.label, tab.description)

    def invoke(self, context, event):
        window_manager = context.window_manager
        shown = _shown(window_manager)
        if not event.shift:
            shown = {self.key}
        elif self.key not in shown:
            shown.add(self.key)
        elif len(shown) > 1:
            shown.discard(self.key)
        window_manager.houdini_bridge_tabs = shown
        if context.area is not None:
            context.area.tag_redraw()
        return {"FINISHED"}

    def execute(self, context):
        context.window_manager.houdini_bridge_tabs = {self.key}
        return {"FINISHED"}


class HOUDINI_BRIDGE_PT_sidebar(bpy.types.Panel):
    """The Houdini sidebar: a column of tabs on the left, the open tabs on the right.
    Click a tab to show it alone, Shift+click to show several."""

    bl_label = "RG Houdini Bridge"
    bl_idname = "HOUDINI_BRIDGE_PT_sidebar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Houdini"
    # With a header: a panel without one has no background of its own over the viewport.

    def draw(self, context):
        shown = _shown(context.window_manager)

        row = self.layout.row()
        strip = row.column(align=True)
        strip.ui_units_x = TAB_STRIP_WIDTH
        strip.scale_y = TAB_BUTTON_HEIGHT
        content = row.column()

        for tab in TABS:
            icon = _icon(tab)
            button = strip.row(align=True)
            button.active = tab.available is None or tab.available(context)
            button.alert = tab.alert is not None and tab.alert(context)
            button.operator("houdini_bridge.sidebar_tab", text="", depress=tab.key in shown, **icon).key = tab.key
            if tab.key not in shown:
                continue
            box = content.box()
            box.label(text=tab.label, **icon)
            tab.draw(box.column(), context)


CLASSES = (HOUDINI_BRIDGE_OT_sidebar_tab, HOUDINI_BRIDGE_PT_sidebar)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    bpy.types.WindowManager.houdini_bridge_tabs = EnumProperty(
        name="Sidebar Tabs",
        description="The tabs shown in the Houdini sidebar",
        items=[(tab.key, tab.label, tab.description, 1 << index) for index, tab in enumerate(TABS)],
        options={"ENUM_FLAG"},
        default={TABS[0].key},
    )
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global _previews
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.houdini_bridge_tabs
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
