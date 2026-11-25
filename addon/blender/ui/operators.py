"""The operators of the Houdini modifiers: open an HDA file, recook, bypass, press a button, apply,
pick a group, add or remove a multiparm instance, update the HDA version, show a
modifier in the Properties editor, run Houdini. The cache has its own operator, in
features/cache.py.
"""

from dataclasses import dataclass, field

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..core import cook, hython, link, modifiers, parameters, streams, targets, trust
from ..features import display
from ..features.shelf import catalog


@dataclass
class OpenedFile:
    """The last HDA file opened that holds several definitions, for picking one."""

    path: str = ""
    definitions: list = field(default_factory=list)


_opened = OpenedFile()


class HOUDINI_BRIDGE_OT_add_hda(bpy.types.Operator, ImportHelper):
    """Apply an HDA file to the active object (or as a new object, as set in Apply to)"""

    bl_idname = "houdini_bridge.add_hda"
    bl_label = "Open HDA File"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*.hda;*.otl;*.hdalc;*.hdanc", options={"HIDDEN"})

    def execute(self, context):
        try:
            supported = modifiers.supported_definitions(self.filepath)
        except hython.ServerError as error:
            self.report({"ERROR"}, str(error).splitlines()[-1])
            return {"CANCELLED"}
        if not supported:
            self.report({"ERROR"}, "No SOP HDA in this file (only SOP HDAs are supported)")
            return {"CANCELLED"}
        if len(supported) == 1:
            modifiers.place_hda(context, self.filepath, supported[0])
            return {"FINISHED"}
        _opened.path, _opened.definitions = self.filepath, supported
        bpy.ops.houdini_bridge.choose_definition("INVOKE_DEFAULT")
        return {"FINISHED"}


def _definition_items(self, context):
    return [(d["type"], d["label"] or d["type"], d["type"]) for d in _opened.definitions] or [("", "—", "")]


class HOUDINI_BRIDGE_OT_choose_definition(bpy.types.Operator):
    """Pick the HDA to use when the file holds several"""

    bl_idname = "houdini_bridge.choose_definition"
    bl_label = "Choose HDA"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    node_type: EnumProperty(name="HDA", items=_definition_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        definition = next((d for d in _opened.definitions if d["type"] == self.node_type), None)
        if definition is None:
            return {"CANCELLED"}
        modifiers.place_hda(context, _opened.path, definition)
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_recook(modifiers.ModifierOperator, bpy.types.Operator):
    """Send all inputs again and recook the HDA"""

    bl_idname = "houdini_bridge.recook"
    bl_label = "Recook"

    def execute(self, context):
        tree = self.node_group()
        if tree is None:
            return {"CANCELLED"}
        cook.allow_file()  # the user's own action: the open file may cook (core/trust.py)
        cook.cooks.request(tree, force=True)
        return {"FINISHED"}


def _bypass_description(modifier):
    if targets.is_bypassed(modifier):
        return "Bypassed: off in the viewport and in renders, and not cooked. Click to turn it back on"
    if display.is_cut(modifier) and modifier.show_render:
        return ("Cut from the viewport by the Display flag of a modifier above, still cooked for renders. "
                "Click to bypass it")
    if not modifier.show_viewport:
        return "Off in the viewport only (Properties editor), still cooked for renders. Click to bypass it"
    if not modifier.show_render:
        return "Off in renders only (Properties editor), still cooked for the viewport. Click to bypass it"
    return ("Bypass this HDA, as Houdini's bypass flag: off in the viewport and in renders, the stack "
            "goes on without it, and Houdini does not cook it")


class HOUDINI_BRIDGE_OT_bypass(modifiers.ModifierOperator, bpy.types.Operator):
    """Bypass this HDA, as Houdini's bypass flag, or turn it back on"""

    bl_idname = "houdini_bridge.bypass"
    bl_label = "Bypass"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def description(cls, context, properties):
        tree = bpy.data.node_groups.get(properties.tree)
        users = targets.modifier_users(tree) if tree is not None else []
        return _bypass_description(users[0][1]) if users else None

    def execute(self, context):
        tree = self.node_group()
        users = targets.modifier_users(tree) if tree else []
        if not users:
            return {"CANCELLED"}
        modifier = users[0][1]
        # One rule for every state: bypassed (both off) turns both on, anything else turns both off.
        targets.set_bypassed(modifier, not targets.is_bypassed(modifier))
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_press_button(modifiers.ModifierOperator, bpy.types.Operator):
    """Press this HDA button"""

    bl_idname = "houdini_bridge.press_button"
    bl_label = "Button"

    name: StringProperty()

    def execute(self, context):
        tree = self.node_group()
        if tree is None:
            return {"CANCELLED"}
        cook.allow_file()
        cook.cooks.request(tree, buttons=[self.name])
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_apply(modifiers.ModifierOperator, bpy.types.Operator):
    """Apply the modifier: the mesh becomes the object's mesh, the instances, curves, points
    and volume become objects of their own, and the link to Houdini is dropped"""

    bl_idname = "houdini_bridge.apply"
    bl_label = "Apply"
    bl_options = {"REGISTER", "UNDO"}

    with_above: BoolProperty(
        name="With the Modifiers Above",
        description="Apply the modifiers above along with this one, as one block: its result already holds theirs",
        options={"SKIP_SAVE"},
    )

    @classmethod
    def description(cls, context, properties):
        if properties.with_above:
            return ("Apply this modifier and every modifier above it, as one block (a combo: VDB from "
                    "Polygons then Convert VDB…): its result already holds theirs")
        return None

    def execute(self, context):
        tree = self.node_group()
        users = targets.modifier_users(tree) if tree else []
        if not users:
            return {"CANCELLED"}
        if users[0][0].mode != "OBJECT":
            self.report({"ERROR"}, "Leave Edit Mode to apply the modifier")
            return {"CANCELLED"}
        try:
            obj, made, adopted = modifiers.apply_modifier(context, tree, self.with_above)
        except ValueError as error:  # bypassed, cut by a Display flag above, a volume gone, or refused by Blender
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        _report_applied(self, made, adopted)
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_apply_all(bpy.types.Operator):
    """Apply the whole modifier stack of the object at once: the last Houdini modifier with
    everything above it, then the Blender modifiers below it"""

    bl_idname = "houdini_bridge.apply_all"
    bl_label = "Apply All"
    bl_options = {"REGISTER", "UNDO"}

    object: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        obj = bpy.data.objects.get(self.object)
        if obj is None or obj.mode != "OBJECT":
            self.report({"ERROR"}, "Leave Edit Mode to apply the modifiers")
            return {"CANCELLED"}
        try:
            applied = modifiers.apply_all(context, obj)
        except ValueError as error:  # the Display flag cuts the stack, or Blender refuses
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        if applied is None:
            self.report({"ERROR"}, "No Houdini modifier to apply: every one is bypassed")
            return {"CANCELLED"}
        obj, made, adopted = applied
        _report_applied(self, made, adopted)
        return {"FINISHED"}


def _report_applied(operator, made, adopted):
    """Say what Apply made, and where a volume's file went (Output preferences)."""
    if made:
        parts = []
        for new in made:
            if new.type == "VOLUME" and streams.is_waiting(new.data.filepath):
                parts.append("%s (its .vdb moves next to the .blend when you save it)" % new.name)
            elif new.type == "VOLUME":
                parts.append("%s (%s)" % (new.name, bpy.path.abspath(new.data.filepath)))
            else:
                parts.append(new.name)
        operator.report({"INFO"}, "Applied: %s" % ", ".join(parts))
    elif adopted:
        operator.report({"INFO"}, "%d Houdini group(s) added to the Groups panel" % adopted)


# Blender requires the strings of a dynamic enum to stay alive: the items of the last
# lists asked for are kept, the oldest dropped beyond SUGGESTIONS_KEPT.
_suggestion_items = {}
SUGGESTIONS_KEPT = 16


def _suggestions(self, context):
    tree = bpy.data.node_groups.get(self.tree)
    parm = tree.houdini_bridge.parms.get(self.parm) if tree else None
    key = tuple(parameters.group_suggestions(parm)) if parm else ()
    items = _suggestion_items.pop(key, None)
    if items is None:
        items = [(name, name, "") for name in key] or [("", "No group in the input", "")]
    _suggestion_items[key] = items  # the most recent last
    while len(_suggestion_items) > SUGGESTIONS_KEPT:
        del _suggestion_items[next(iter(_suggestion_items))]
    return items


class HOUDINI_BRIDGE_OT_pick_group(modifiers.ModifierOperator, bpy.types.Operator):
    """Pick a group of the input for this parameter (Groups panel, boolean attributes)"""

    bl_idname = "houdini_bridge.pick_group"
    bl_label = "Pick Group"
    bl_property = "choice"
    bl_options = {"REGISTER", "UNDO"}

    parm: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    choice: EnumProperty(items=_suggestions)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        tree = self.node_group()
        if tree is None or not self.choice:
            return {"CANCELLED"}
        if not modifiers.pick_group(tree, self.parm, self.choice):
            return {"CANCELLED"}
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_upgrade(modifiers.ModifierOperator, bpy.types.Operator):
    """Switch to the version of this HDA listed in the shelf (parameters with the same name keep their value)"""

    bl_idname = "houdini_bridge.upgrade"
    bl_label = "Update HDA Version"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = self.node_group()
        tool = catalog.newer_version(tree.houdini_bridge) if tree else None
        if tool is None:
            return {"CANCELLED"}
        old = modifiers.upgrade(tree, tool)
        self.report({"INFO"}, "%s → %s" % (old, tool["type"]))
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_multiparm(modifiers.ModifierOperator, bpy.types.Operator):
    """Add an instance to this multiparm, or remove the last one, as Houdini's + and - do"""

    bl_idname = "houdini_bridge.multiparm"
    bl_label = "Multiparm Instances"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    parm: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    step: IntProperty(default=1, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        return "Add an instance" if properties.step > 0 else "Remove the last instance"

    def execute(self, context):
        tree = self.node_group()
        users = targets.modifier_users(tree) if tree else []
        parm = tree.houdini_bridge.parms.get(self.parm) if users else None
        if parm is None or not parm.multiparm:
            return {"CANCELLED"}
        count = (parameters.read_value(users[0][1], parm) or [0])[0]
        # Only the count changes, as when it is typed: the next cook rebuilds the inputs.
        parameters.write_value(users[0][1], parm, max(0, count + self.step))
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_show_parameters(modifiers.ModifierOperator, bpy.types.Operator):
    """Show this modifier in the Properties editor, where everything that sets it is"""

    bl_idname = "houdini_bridge.show_parameters"
    bl_label = "Parameters"

    def execute(self, context):
        tree = self.node_group()
        users = targets.modifier_users(tree) if tree else []
        if not users:
            return {"CANCELLED"}
        obj, modifier = users[0]
        obj.modifiers.active = modifier
        modifier.show_expanded = True
        for area in context.screen.areas if context.screen is not None else ():
            if area.type != "PROPERTIES":
                continue
            try:
                area.spaces.active.context = "MODIFIER"
            except TypeError:  # pinned on an object without a Modifiers tab (a camera, a light): the next one
                continue
            area.tag_redraw()
            return {"FINISHED"}
        self.report({"INFO"}, "Open a Properties editor: the parameters are in its Modifiers tab")
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_server(bpy.types.Operator):
    """Start, stop or restart Houdini (the hython server)"""

    bl_idname = "houdini_bridge.server"
    bl_label = "Houdini Server"

    action: EnumProperty(items=[
        ("START", "Start", ""),
        ("STOP", "Stop", ""),
        ("RESTART", "Restart", ""),
    ])

    def execute(self, context):
        if self.action in ("STOP", "RESTART"):
            cook.stop_server()
        if self.action in ("START", "RESTART"):
            cook.start_server()
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_allow_file(bpy.types.Operator):
    """Let the Houdini modifiers of this file cook until another file is opened. Cooking starts
    Houdini and loads the HDA files the file names, which can run Python: allow only a file
    you trust"""

    bl_idname = "houdini_bridge.allow_file"
    bl_label = "Allow Execution"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return trust.is_blocked()

    def execute(self, context):
        cook.allow_file()
        return {"FINISHED"}


class HOUDINI_BRIDGE_OT_server_log(bpy.types.Operator):
    """Copy the hython output into the "Houdini Bridge Log" text"""

    bl_idname = "houdini_bridge.server_log"
    bl_label = "Show Log"

    def execute(self, context):
        text = bpy.data.texts.get("Houdini Bridge Log") or bpy.data.texts.new("Houdini Bridge Log")
        text.from_string("\n".join(link.server.log()) or "(empty)")
        self.report({"INFO"}, 'Log copied into the "Houdini Bridge Log" text')
        return {"FINISHED"}


CLASSES = (
    HOUDINI_BRIDGE_OT_add_hda,
    HOUDINI_BRIDGE_OT_choose_definition,
    HOUDINI_BRIDGE_OT_recook,
    HOUDINI_BRIDGE_OT_bypass,
    HOUDINI_BRIDGE_OT_press_button,
    HOUDINI_BRIDGE_OT_apply,
    HOUDINI_BRIDGE_OT_apply_all,
    HOUDINI_BRIDGE_OT_pick_group,
    HOUDINI_BRIDGE_OT_upgrade,
    HOUDINI_BRIDGE_OT_multiparm,
    HOUDINI_BRIDGE_OT_show_parameters,
    HOUDINI_BRIDGE_OT_server,
    HOUDINI_BRIDGE_OT_allow_file,
    HOUDINI_BRIDGE_OT_server_log,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
