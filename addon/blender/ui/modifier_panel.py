"""The Houdini modifier in the Properties editor, drawn as close to Houdini's parameter
pane as Blender allows.

A child panel of MOD_PT_GeometryNodes, drawn under every Geometry Nodes modifier, empty for
one that is not a Houdini modifier. Its rows, top down:
- the node row, in the panel's header (PANEL_HEADER): the HDA's icon (the shelf's) and
  label, its flags in Houdini's order (Bypass, Lock, Template, Display: NODE_FLAGS), the icon
  of its cook state, then Recook and the two Apply;
- the status and timing, the errors (red text), the version update;
- the input objects, as the inputs of a node;
- the parameters, in Houdini's order and folders (parameter_rows.draw, a row per kind), or,
  with the preference "Houdini Interface" off, only what an input cannot show (Blender
  draws the inputs: parameter_rows.draw_beside_inputs);
- Output (mode, instances in the viewport, volume, visualizer) and Cache, folded.
Blender draws the modifier's own header (name, viewport and render switches, menu, remove).
A state shows as an icon (core/icons.py) or the color of a text.
"""

import zlib

import bpy
import bpy.utils.previews

from ...shared import contract
from ..core import cook, icons, parameters, paths, streams, targets, trust
from ..features import cache, display
from ..features.shelf import catalog
from . import parameter_rows

# --- panel header ---------------------------------------------------------------------
# A child panel of MOD_PT_GeometryNodes without a header (HIDE_HEADER) draws inside the
# modifier's own frame, as Blender's own rows do. But Blender 5.2 crashes (ui::handler_panel,
# or the redraw after) when a panel type is registered or unregistered while a Properties
# editor shows Geometry Nodes modifiers and such a child exists: this extension enabled or
# disabled, Reload Scripts, any other add-on removing a panel. A child with its header
# survives them all. So it keeps its header, which holds the node row (draw_header), the
# rest being its body. The cost: Blender folds it at first on each modifier (its open state
# is a bit of the modifier's ui_expand_flag, which Python cannot set), and a Geometry Nodes
# modifier that is not Houdini's gets an empty folded row. Once Blender fixes the crash,
# False draws the node row in the body and the whole panel within the modifier's frame.
PANEL_HEADER = True

ERROR_TEXT_WIDTH = 140  # characters of an error line drawn in a panel
REGION_MARGIN = 50  # pixels at a UI scale of 1, between the Properties editor's width and the panel's

# --- the state icons ------------------------------------------------------------------

# The state dots and the looks of the Bypass, Lock, Template and Display flags
# (core/icons.py), loaded at registration; the sidebar's overview uses them too.
STATE_ICONS = ("READY", "BUSY", "ERROR", "STOPPED", "BYPASSED", *icons.SHAPES)
_previews = None


def state_icon(key):
    """The preview id of a state icon of STATE_ICONS (0 before registration)."""
    preview = _previews.get(key) if _previews is not None else None
    return preview.icon_id if preview is not None else 0


def modifier_state(tree, modifier, cooking):
    """The cook state of a Houdini modifier: a key of core/icons.py COLORS. `cooking` holds the
    keys of the targets whose cook is on its way (cook.activity()). In order: bypassed, in a
    file that waits for Allow Execution, cooking (or waiting to), in error, not cooked yet, up
    to date."""
    settings = tree.houdini_bridge
    if targets.is_bypassed(modifier):
        return "BYPASSED"
    if trust.is_blocked():
        return "STOPPED"
    if tree.session_uid in cooking or cook.cooks.is_requested(tree):
        return "BUSY"
    if settings.errors:
        return "ERROR"
    if not settings.status:
        return "STOPPED"
    return "READY"


def cooking_keys():
    return {target.key for target, _, _ in cook.activity()[0]}


def tool_icon(settings):
    """{"icon_value": id} of the icon the shelf shows for this modifier's HDA or node,
    else {"icon": "MODIFIER"} (a tool the catalog does not list, or without an icon)."""
    tool = catalog.tool_of(settings)
    icon = catalog.icon(tool) if tool is not None else 0
    return {"icon_value": icon} if icon else {"icon": "MODIFIER"}


def status_text(tree, modifier):
    """The words of the status line: "Bypassed", or the last cook's status and its total
    time, and which of the viewport and the renders the modifier is off in, when only one."""
    settings = tree.houdini_bridge
    if targets.is_bypassed(modifier):
        return "Bypassed"
    text = settings.status or "not cooked yet"
    if settings.timings and settings.status and not settings.errors:
        text += " · " + settings.timings.split(" · ")[0].replace("total ", "")
    if settings.time_dependent:
        text += " · animated"
    if display.is_cut(modifier):
        text += " · below the Display flag"
    elif not modifier.show_viewport:
        text += " · off in the viewport"
    elif not modifier.show_render:
        text += " · off in renders"
    return text[:1].upper() + text[1:]  # a sentence, as "Bypassed" is


# --- the flags of the node, in Houdini's order ----------------------------------------


def bypass_look(modifier):
    """The look of the Bypass flag (core/icons.py SHAPES): bypassed, partly (off in the
    viewport or in renders only, from Blender's own switches), or not. A modifier the Display
    flag of another one cuts from the viewport is not partly bypassed: the status line says
    why it is off."""
    if targets.is_bypassed(modifier):
        return "BYPASS_ON"
    if not modifier.show_render or (not modifier.show_viewport and not display.is_cut(modifier)):
        return "BYPASS_PARTLY"
    return "BYPASS_OFF"


def _draw_bypass(layout, tree, modifier):
    # Not drawn pressed: Blender's blue would hide the yellow, and the flag's look says the state.
    layout.operator("houdini_bridge.bypass", text="", icon_value=state_icon(bypass_look(modifier)),
                    emboss=False).tree = tree.name


def _draw_lock(layout, tree, modifier):
    """Houdini's lock flag is the setting `live`, inverted. A cached modifier is locked anyway:
    the flag shows set and cannot be changed (the Cache section says why)."""
    settings = tree.houdini_bridge
    row = layout.row(align=True)
    if settings.use_cache:
        row.enabled = False
        row.label(text="", icon_value=state_icon("LOCK_ON"))
        return
    row.prop(settings, "live", text="", emboss=False, invert_checkbox=True,
             icon_value=state_icon("LOCK_OFF" if settings.live else "LOCK_ON"))


def _draw_template(layout, tree, modifier):
    """Houdini's template flag: the result drawn as a ghost wireframe in the viewport
    (features/viewport/template.py), the stack shown as usual."""
    settings = tree.houdini_bridge
    layout.prop(settings, "template", text="", emboss=False,
                icon_value=state_icon("TEMPLATE_ON" if settings.template else "TEMPLATE_OFF"))


def _draw_display(layout, tree, modifier):
    """Houdini's display flag: the viewport shows this HDA's output, the modifiers below it cut
    (features/display.py). One per object."""
    key = "DISPLAY_ON" if display.is_flagged(modifier) else "DISPLAY_OFF"
    layout.operator("houdini_bridge.display", text="", icon_value=state_icon(key), emboss=False).tree = tree.name


# The flags of the node row, in Houdini's order: each draws its button, (layout, tree, modifier).
NODE_FLAGS = (_draw_bypass, _draw_lock, _draw_template, _draw_display)


# --- the sections ---------------------------------------------------------------------


def _section_id(tree, name):
    return "hb_%s_%08x" % (name, zlib.crc32(tree.name.encode("utf-8")))


def _draw_node_row(layout, tree, modifier, settings, bypassed):
    row = layout.row(align=False)
    row.use_property_split = False
    title = row.row(align=True)
    title.active = not bypassed  # faded as Houdini fades a bypassed node; the flags stay bright
    title.label(text=settings.label or modifier.name, **tool_icon(settings))
    flags = row.row(align=True)
    for draw_flag in NODE_FLAGS:
        draw_flag(flags, tree, modifier)
    flags.label(text="", icon_value=state_icon(modifier_state(tree, modifier, cooking_keys())))
    tools = row.row(align=True)
    tools.enabled = not bypassed  # nothing is cooked or applied for a bypassed HDA
    tools.operator("houdini_bridge.recook", text="", icon="FILE_REFRESH").tree = tree.name
    tools.operator("houdini_bridge.apply", text="", icon="CHECKMARK").tree = tree.name
    if targets.shown_above(modifier.id_data, modifier):
        block = tools.operator("houdini_bridge.apply", text="", icon="TRIA_UP_BAR")
        block.tree, block.with_above = tree.name, True


def _draw_blocked(layout):
    """A file Blender would not let run its scripts: nothing cooks until it is allowed."""
    column = layout.column(align=True)
    column.label(text="Auto Run Python Scripts is off: this file does not cook", icon="ERROR")
    column.label(text="Cooking starts Houdini and loads the HDA files the file names", icon="BLANK1")
    column.operator("houdini_bridge.allow_file", icon="CHECKMARK")


def _draw_messages(layout, tree, modifier, settings):
    if trust.is_blocked():
        _draw_blocked(layout)
    row = layout.row()
    row.use_property_split = False
    row.label(text=status_text(tree, modifier), icon="TIME" if settings.time_dependent else "INFO")
    if settings.errors:
        column = layout.column(align=True)
        column.alert = True  # red text, no frame
        for line in settings.errors.splitlines()[:parameter_rows.MAX_ERRORS_SHOWN]:
            column.label(text=line[:ERROR_TEXT_WIDTH], icon="ERROR")
    newer = catalog.newer_version(settings)
    if newer is not None:
        layout.operator("houdini_bridge.upgrade", icon="SORT_DESC",
                        text="Update to %s" % newer["type"].split("::")[-1]).tree = tree.name


def _draw_inputs(layout, modifier, settings):
    """The inputs of the node, above its parameters: input 1 is this object with the modifiers
    above it, the others are objects."""
    for item in settings.inputs:
        if item.is_self:
            above = [other.name for other in targets.shown_above(modifier.id_data, modifier)]
            text = "this object + %s" % ", ".join(above) if above else "this object"
            row = layout.row()
            row.label(text=item.label)
            row.label(text=text, icon="MESH_DATA")
        else:
            layout.prop(item, "object", text=item.label)


def _draw_volume(layout, settings):
    """The volume lives on a hidden object: its display and render settings are reachable here
    only (Apply copies them to the Volume object it makes)."""
    holder = settings.volume_holder
    if holder is None or not streams.summary(settings).get(contract.VOLUME):
        return
    volume = holder.data
    layout.label(text="Volume", icon="VOLUME_DATA")
    column = layout.column(align=True)
    column.prop(volume.display, "density", text="Viewport Density")
    column.prop(volume.display, "wireframe_type", text="Wireframe")
    column.prop(volume.display, "interpolation_method", text="Interpolation")
    column = layout.column(align=True)
    column.prop(volume.render, "space", text="Render Space")
    column.prop(volume.render, "step_size")
    column.prop(volume.render, "precision")
    column.prop(volume.render, "clipping")
    if volume.materials and volume.materials[0] is not None:
        layout.prop(volume.materials[0], "name", text="Material", icon="MATERIAL")


def _draw_output(layout, tree, settings):
    header, body = layout.panel(_section_id(tree, "output"), default_closed=True)
    header.label(text="Output", icon="OUTPUT")
    if body is None:
        return
    body.row().prop(settings, "output_mode", expand=True)
    if streams.summary(settings).get(contract.INSTANCES):
        body.prop(settings, "instance_display", text="Instances in Viewport")
    if settings.outputs_json and settings.outputs_json != "[]":
        body.prop(settings, "visualize", icon="HIDE_OFF")
    _draw_volume(body, settings)


def _draw_cache(layout, tree, settings):
    header, body = layout.panel(_section_id(tree, "cache"), default_closed=not settings.use_cache)
    header.label(text="Cache", icon="FILE_CACHE")
    if body is not None:
        body.use_property_split = False
        cache.draw_cache(body, targets.Target(tree), tree)


def _region_width(context):
    region = context.region
    scale = context.preferences.system.ui_scale
    return (region.width if region is not None else 400) - REGION_MARGIN * scale


def _houdini(panel):
    """(modifier, node group, settings) when the panel's modifier is a Houdini modifier, else None."""
    modifier = panel.custom_data
    tree = targets.houdini_tree(modifier)
    return (modifier, tree, tree.houdini_bridge) if tree is not None else None


def draw_header(panel, context):
    """The node row in the panel's header, which says the essential while the panel is folded.
    Nothing for a Geometry Nodes modifier that is not Houdini's (its header stays empty)."""
    found = _houdini(panel)
    if found is not None:
        modifier, tree, settings = found
        _draw_node_row(panel.layout, tree, modifier, settings, targets.is_bypassed(modifier))


def draw(panel, context):
    """The body of the panel: the whole Houdini modifier, except its node row when the panel
    has a header (PANEL_HEADER); nothing for another modifier."""
    found = _houdini(panel)
    if found is None:
        return
    modifier, tree, settings = found
    layout = panel.layout
    layout.use_property_split = True
    layout.use_property_decorate = True
    bypassed = targets.is_bypassed(modifier)
    if not PANEL_HEADER:
        _draw_node_row(layout, tree, modifier, settings, bypassed)
    body = layout.column()
    body.active = not bypassed
    _draw_messages(body, tree, modifier, settings)
    _draw_inputs(body, modifier, settings)
    if parameters.houdini_interface_on():
        parameter_rows.draw(body, tree, modifier, _region_width(context))
    else:
        parameter_rows.draw_beside_inputs(body, tree, modifier)
    _draw_output(body, tree, settings)
    _draw_cache(body, tree, settings)


class HOUDINI_BRIDGE_PT_modifier(bpy.types.Panel):
    """The Houdini modifier, inside the panel of every Geometry Nodes modifier (nothing for one
    that is not Houdini's). With its header or without: PANEL_HEADER."""

    bl_label = ""  # no label: the header of another Geometry Nodes modifier stays empty
    bl_idname = "HOUDINI_BRIDGE_PT_modifier"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "modifier"
    bl_parent_id = "MOD_PT_GeometryNodes"
    bl_options = set() if PANEL_HEADER else {"HIDE_HEADER"}

    def draw_header(self, context):
        draw_header(self, context)

    def draw(self, context):
        draw(self, context)


class HOUDINI_BRIDGE_PT_rebuild(bpy.types.Panel):
    """Registered and unregistered at once, right after the modifier panel: Blender frees every
    instanced panel when a panel type goes, and the next redraw makes them again, with the new
    child. Without it, the modifier panels already drawn lack the child, and Blender 5.2 crashes
    drawing them (an extension enabled while a Geometry Nodes modifier shows)."""

    bl_label = "Houdini Modifier Panels"
    bl_idname = "HOUDINI_BRIDGE_PT_rebuild"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Houdini"

    @classmethod
    def poll(cls, context):
        return False

    def draw(self, context):
        pass


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    folder = paths.status_icons_dir()
    for key in STATE_ICONS:
        icons.load_preview(_previews, key, icons.write_state_icon(folder, key))
    bpy.utils.register_class(HOUDINI_BRIDGE_PT_modifier)
    bpy.utils.register_class(HOUDINI_BRIDGE_PT_rebuild)
    bpy.utils.unregister_class(HOUDINI_BRIDGE_PT_rebuild)
    # Blender registers an extension with its data out of reach (RestrictBlend): the modifiers
    # of the open file get the Houdini interface from a timer, at once after; a file loaded
    # later, from load_post.
    bpy.app.timers.register(apply_houdini_interface_after_register, first_interval=0.0)


def apply_houdini_interface_after_register():
    """Apply the Houdini interface to every Houdini modifier of the open file, once the extension
    is registered (a timer, which background mode does not run: there, a file loaded or a cook
    applies it)."""
    parameters.apply_houdini_interface_to_all()
    return None


def unregister():
    """Turn the Houdini interface off on every Houdini modifier, so that Blender draws its
    inputs again and no modifier is left empty, then remove the panel."""
    global _previews
    if bpy.app.timers.is_registered(apply_houdini_interface_after_register):
        bpy.app.timers.unregister(apply_houdini_interface_after_register)
    if isinstance(bpy.data, bpy.types.BlendData):  # not restricted when an extension is disabled
        parameters.apply_houdini_interface_to_all(False)
    bpy.utils.unregister_class(HOUDINI_BRIDGE_PT_modifier)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
