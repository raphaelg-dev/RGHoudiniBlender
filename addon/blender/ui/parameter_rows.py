"""The rows of the parameters in the modifier panel. ROWS holds one Row per kind of
contract.PARM_KINDS, which draws its input, a code block, a group field's picker and arrow,
a ramp's widget, a button, or the + and - of a multiparm. Painter draws the folders, tabs and
boxes of the layout core/parameters/layout.py walks.

With the preference "Houdini Interface" off, Blender draws the inputs, in its own panels, and
draw_beside_inputs() draws below them only what an input cannot show, in Houdini's order, each
titled with its folder path: the ramps, the buttons, the code blocks, the picker and the arrow
of each group field (beside_inputs() lists them).
"""

import math
import textwrap
from collections.abc import Callable
from dataclasses import dataclass

import bpy
from bpy.props import StringProperty

from ...shared import contract
from ..core import parameters, preferences
from ..features import code_editor, selection
from . import operators


def _input(layout, modifier, parm, text):
    """Its input, as Blender draws a modifier input (the same property: keyframes, drivers,
    limits and the help as tooltip work as in Blender's own drawing)."""
    item = parameters.modifier_input(modifier, parm.socket)
    if item is not None:
        layout.prop(item, "value", text=text)


def _input_row(layout, tree, modifier, parm, text, houdini_interface):
    """A float, toggle or menu: its input, unless Blender draws it."""
    if houdini_interface:
        _input(layout, modifier, parm, text)


def _int_row(layout, tree, modifier, parm, text, houdini_interface):
    """Its input; the count of a multiparm with + and -, as Houdini draws it (the operator only
    changes the count: the inputs are rebuilt after the cook, as for a typed count). With the
    Houdini interface off, Blender draws the count."""
    if not houdini_interface:
        return
    if not parm.multiparm:
        _input(layout, modifier, parm, text)
        return
    row = layout.row(align=True)
    _input(row, modifier, parm, text)
    for icon, step in (("ADD", 1), ("REMOVE", -1)):
        button = row.operator(operators.HOUDINI_BRIDGE_OT_multiparm.bl_idname, text="", icon=icon)
        button.tree, button.parm, button.step = tree.name, parm.name, step


def _is_group_field(parm):
    return bool(parm.menu_json and parm.socket)


CODE_LINES = 6  # the lines of code a code block shows; the count of the others follows them
CODE_LINE_WIDTH = 90  # characters of a line shown, the rest cut (Blender would cut its middle)


def code_preview(code):
    """The lines a code block shows (tabs as four spaces, long lines cut), and the count of the
    lines left out."""
    lines = code.expandtabs(4).splitlines()
    shown = [line if len(line) <= CODE_LINE_WIDTH else line[:CODE_LINE_WIDTH - 1] + "…"
             for line in lines[:CODE_LINES]]
    return shown, len(lines) - len(shown)


def _code_block(layout, tree, modifier, parm, text):
    """A code parameter (a wrangle's VEX, a Python SOP's script): its title and the Edit button
    of the code editor feature, which opens it in the Text Editor, over a frame showing its
    first lines and the count of the others, then the errors of the cook, whole. Its one-line
    field is not drawn: a line cannot show code. With the Houdini interface off, Blender draws
    that field anyway, and the block stays the way to edit it."""
    item = parameters.modifier_input(modifier, parm.socket)
    lines, more = code_preview(item.value if item is not None else "")
    column = layout.column(align=True)
    header = column.row(align=True)
    header.use_property_split = False
    header.label(text=text)
    code_editor.draw_edit(header, tree, parm)
    frame = column.box().column(align=True)
    frame.scale_y = 0.75  # tight rows, as lines of code
    for line in lines:
        frame.label(text=line or " ")
    if more or not lines:
        note = frame.row()
        note.active = False
        note.label(text="… %d more line%s" % (more, "s" if more > 1 else "") if more else "Empty")
    errors = error_lines(tree.houdini_bridge.errors)
    if errors:
        # The errors of the cook (a VEX compile error names its line and column), whole, under the
        # code they are about: the status of the modifier shows only the start of each.
        alert = column.column(align=True)
        alert.alert = True
        alert.scale_y = 0.75
        for line in errors:
            alert.label(text=line)


ERROR_LINE_WIDTH = 60  # characters per line of an error under a code block
MAX_ERRORS_SHOWN = 4  # errors of a cook drawn in a panel, the first ones


def error_lines(errors):
    """The errors of a cook as the code block shows them: each one wrapped, its blanks folded."""
    return [piece for error in errors.splitlines()[:MAX_ERRORS_SHOWN]
            for piece in textwrap.wrap(" ".join(error.split()), ERROR_LINE_WIDTH)]


def _string_row(layout, tree, modifier, parm, text, houdini_interface):
    """Its text field; a code parameter as a code block; a group field on one row with a picker
    listing the groups of the input and, when Houdini can select it in its viewport, the arrow
    that selects it in Blender's (the viewport selection feature), as in Houdini. With the
    Houdini interface off, Blender draws the field: the row holds its title, the picker and
    the arrow."""
    if parm.editor:
        _code_block(layout, tree, modifier, parm, text)
        return
    if not _is_group_field(parm):
        _input_row(layout, tree, modifier, parm, text, houdini_interface)
        return
    row = layout.row(align=True)
    if houdini_interface:
        _input(row, modifier, parm, text)
    else:
        row.label(text=text)
    picker = row.operator(operators.HOUDINI_BRIDGE_OT_pick_group.bl_idname, text="", icon="GROUP_VERTEX")
    picker.tree = tree.name
    picker.parm = parm.name
    selection.draw_arrow(row, tree, parm)


def _button_row(layout, tree, modifier, parm, text, houdini_interface):
    operator = layout.operator(operators.HOUDINI_BRIDGE_OT_press_button.bl_idname, text=text)
    operator.tree = tree.name
    operator.name = parm.name


def _ramp_row(layout, tree, modifier, parm, text, houdini_interface):
    """Edited with Blender's own widget, on the node holding it (a ramp is not a modifier
    input), at its place in the panel."""
    node = parameters.ramp_node(tree, parm)
    if node is None:
        return
    column = layout.column(align=True)
    column.use_property_split = False
    column.label(text=text, icon="COLOR" if parm.ramp == "color" else "FCURVE")
    if parm.ramp == "color":
        column.template_color_ramp(node, "color_ramp", expand=True)
    else:
        column.template_curve_mapping(node, "mapping", type="NONE")


@dataclass(frozen=True)
class Row:
    """How one kind of parameter is drawn. `draw(layout, tree, modifier, parm, text,
    houdini_interface)` draws its row, titled `text`; with `houdini_interface` False, Blender
    draws the inputs, so the row draws only what an input cannot show. `beside_inputs(parm)`:
    whether it then draws something (None: never)."""

    draw: Callable
    beside_inputs: Callable | None = None


# In the order of contract.PARM_KINDS (tests/unit/test_parm_registry.py checks it).
ROWS = {
    contract.FLOAT: Row(_input_row),
    contract.INT: Row(_int_row),
    contract.TOGGLE: Row(_input_row),
    contract.STRING: Row(_string_row, beside_inputs=lambda parm: bool(parm.editor) or _is_group_field(parm)),
    contract.MENU: Row(_input_row),
    contract.BUTTON: Row(_button_row, beside_inputs=lambda parm: True),
    contract.RAMP: Row(_ramp_row, beside_inputs=lambda parm: True),
}


# --- the folders, the tabs and the boxes ----------------------------------------------

TAB_CHARACTER_WIDTH = 7  # pixels per character of a tab's label, at a UI scale of 1
TAB_MARGIN = 24


class Painter:
    """Draws the walk of the core (parameters.walk) into Blender's layouts. `width` is the width
    of the region in pixels and `scale` the UI scale, for the rows of tabs."""

    every_tab = False  # only the open tab of a set shows its content

    def __init__(self, modifier, width, scale=1.0):
        self.modifier = modifier
        self.width = width
        self.scale = scale
        self.closed = not preferences.get().folders_open

    def joined_row(self, layout):
        row = layout.row(align=True)
        row.use_property_split = False
        return row

    def parm(self, layout, tree, parm, kind):
        if parm.disabled:
            # Houdini greys it out (disable-when, as the last cook found it): faded, still editable,
            # since the state may be older than the values.
            layout = layout.column()
            layout.active = False
        ROWS[kind.name].draw(layout, tree, self.modifier, parm, parm.label, True)

    def folder(self, layout, tree, folder):
        if folder.type == contract.FOLDER_COLLAPSIBLE:
            header, body = layout.panel(parameters.panel_id(tree, folder), default_closed=self.closed)
            header.label(text=folder.label)
            return body
        box = layout.box().column()
        if folder.label:  # an unlabelled Simple folder is a frame, as Houdini draws it
            box.label(text=folder.label)
        return box

    def tabs(self, layout, tree, tabs, shown, current):
        longest = max(len(tab.label) for tab in shown)
        per_row = max(1, min(len(shown), int(self.width // ((longest * TAB_CHARACTER_WIDTH + TAB_MARGIN)
                                                            * self.scale))))
        rows = math.ceil(len(shown) / per_row)
        per_row = math.ceil(len(shown) / rows)
        column = layout.column(align=True)
        for start in range(0, len(shown), per_row):
            row = column.row(align=True)
            for tab in shown[start:start + per_row]:
                button = row.operator(HOUDINI_BRIDGE_OT_folder_tab.bl_idname, text=tab.label, depress=tab is current)
                button.tree, button.path, button.tab_set = tree.name, parameters.tab_key(tabs), tabs.name
                button.tab = tab.key[-1]
        return column.box().column()  # the open tab's content, framed under its tabs as in Houdini


def draw(layout, tree, modifier, width):
    """Draw the parameters of a Houdini modifier into the panel, in Houdini's order and folders."""
    parameters.walk(tree, Painter(modifier, width, bpy.context.preferences.system.ui_scale), layout)


def beside_inputs(tree):
    """[(parm, title)] of what draw_beside_inputs() draws for a node group, in Houdini's order:
    the parameters an input cannot show, each titled with its folder path."""
    found = []
    for kind, parm in parameters.listed(tree):
        row = ROWS[kind.name]
        if row.beside_inputs is not None and row.beside_inputs(parm):
            folder = parm.folder.replace(parameters.FOLDER_SEPARATOR, " › ")
            found.append((parm, "%s › %s" % (folder, parm.label) if folder else parm.label))
    return found


def draw_beside_inputs(layout, tree, modifier):
    """With the preference "Houdini Interface" off, Blender draws the inputs: draw only what an
    input cannot show (beside_inputs())."""
    for parm, title in beside_inputs(tree):
        ROWS[parm.kind].draw(layout, tree, modifier, parm, title, False)


class HOUDINI_BRIDGE_OT_folder_tab(bpy.types.Operator):
    """Show this tab of the Houdini folder"""

    bl_idname = "houdini_bridge.folder_tab"
    bl_label = "Folder Tab"
    bl_options = {"INTERNAL"}  # no undo step: only the panel changes, nothing in the file

    tree: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})  # the key of the folder holding the set
    tab_set: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    tab: StringProperty(options={"HIDDEN", "SKIP_SAVE"})  # its Houdini name (its label in a file saved before 1.0)

    def execute(self, context):
        path = self.path.split(parameters.FOLDER_SEPARATOR) if self.path else []
        parameters.set_open_tab(self.tree, path, self.tab_set, self.tab)
        if context.area is not None:
            context.area.tag_redraw()
        return {"FINISHED"}


CLASSES = (HOUDINI_BRIDGE_OT_folder_tab,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    parameters.clear_layouts()
