"""Parameters of an HDA, in hython: describing them for Blender, writing the values it sends.

describe_parms() walks the parameter templates (folders, multiparms and their instances);
set_parms() writes what a cook request carries. KINDS holds, for each kind of
contract.PARM_KINDS, how it is described and how its value is written. kind_of() picks the
kind a template is described as; set_parm() picks the writer from the template type alone
(written_as), so that writing never evaluates a menu. A multiparm is not a kind: its count
is described as an int, its instances as folders.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

import hou
import numpy as np

from ..shared import contract
from . import script_action
from .output import log

MULTIPARM_FOLDERS = (
    hou.folderType.MultiparmBlock,
    hou.folderType.ScrollingMultiparmBlock,
    hou.folderType.TabbedMultiparmBlock,
)
# The folders whose hidden and disabled states can be read: each has a parameter of its own.
STATE_FOLDERS = (hou.folderType.Simple, hou.folderType.Collapsible)
MULTIPARM_SLIDER_MAX = 10  # the slider of a multiparm's count; more can be typed
MULTIPARM_PASSES = 4  # set_parms: one pass per level of nested multiparms
RAMP_TOLERANCE = 1e-6  # a ramp closer than this to the one sent is not written again
# A number closer than this (relative) to the one sent is not written again: Blender holds
# floats in 32 bits, so 0.1 comes back as 0.10000000149. Near zero a 32 bit float keeps its
# relative precision: the absolute tolerance only absorbs noise, so 5e-7 can still be set.
VALUE_TOLERANCE = 1e-6
ABSOLUTE_TOLERANCE = 1e-12


def ramp_value(ramp):
    """A hou.Ramp as plain data: one basis name, position and value per key."""
    return {
        "basis": [basis.name() for basis in ramp.basis()],
        "keys": [float(key) for key in ramp.keys()],
        "values": [list(value) if isinstance(value, tuple) else float(value) for value in ramp.values()],
    }


# --- kinds ----------------------------------------------------------------------------
# describe(entry, parm_tuple, template, menu) adds the fields of its kind (PARM_FIELDS of
# shared/contract.py) to an entry that holds the common ones; `menu` is the [[token,
# label]] list kind_of() was given. write(parm_tuple, value) sets a value Blender sent,
# and leaves the parameter alone when it already has it.


def _describe_range(entry, template):
    entry["min"] = template.minValue()
    entry["max"] = template.maxValue()
    # Strict bounds (the lock in the parameter editor) cannot be crossed; a slider range can.
    entry["min_strict"] = template.minIsStrict()
    entry["max_strict"] = template.maxIsStrict()


def _describe_float(entry, parm_tuple, template, menu):
    entry["value"] = [float(v) for v in parm_tuple.eval()]
    _describe_range(entry, template)
    entry["is_color"] = template.look() == hou.parmLook.ColorSquare


def _describe_int(entry, parm_tuple, template, menu):
    entry["value"] = [int(v) for v in parm_tuple.eval()]
    _describe_range(entry, template)


def _describe_toggle(entry, parm_tuple, template, menu):
    entry["value"] = bool(parm_tuple[0].eval())


def _describe_string(entry, parm_tuple, template, menu):
    entry["value"] = _text(parm_tuple[0])
    tags = template.tags()
    if tags.get("editor") == "1":
        # A code editor in Houdini (a wrangle's VEX, a Python SOP's script), which Blender edits
        # in its Text Editor. Its menu, when it has one, holds presets of code, not groups.
        entry["editor"] = tags.get("editorlang", "").lower() or "text"
        return
    free_text = _free_text(template)
    selection = script_action.read(tags.get("script_action", "")) if free_text else None
    if free_text and (menu or selection is not None):
        # Group fields and the like: free text ("top side", "@P.y>0"), where the menu only
        # suggests values: the groups found in the input, except the empty ones, which
        # Blender does not list (the menu is read after the cook). A field Houdini selects
        # in its viewport counts as one even when its input has no group yet.
        empty = _empty_input_groups(parm_tuple.node())
        entry["suggestions"] = [token for token, _ in menu if token not in empty]
        entry["toggle"] = template.menuType() == hou.menuType.StringToggle
        _describe_group_selection(entry, parm_tuple.node(), selection)
    else:
        entry["is_file"] = template.stringType() == hou.stringParmType.FileReference


def _describe_group_selection(entry, node, selection):
    """How Houdini's arrow selects this group field in the viewport, as its script_action
    tag says (`selection`, read by houdini/script_action.py, None when it selects nothing):
    the input whose elements it numbers, the element types it takes, and the parameter of
    the node whose menu gives the type. A type given by a node inside the HDA is followed to
    the HDA parameter it references; when it references none, its current value is sent as
    the type."""
    entry["group_input"] = selection.input_index if selection is not None else None
    entry["group_types"] = list(selection.types) if selection is not None else []
    entry["group_type_parm"] = ""
    if selection is None or selection.type_parm is None:
        return
    path, name = selection.type_parm
    source = node.node(path) if path else node
    parm_tuple = source.parmTuple(name) if source is not None else None
    if parm_tuple is None:
        return
    referenced = parm_tuple[0].getReferencedParm()
    if referenced.node() == node:
        entry["group_type_parm"] = referenced.name()
        return
    group_type = contract.GROUP_TYPE_TOKENS.get(parm_tuple[0].evalAsString())
    entry["group_types"] = [group_type] if group_type else []


def _empty_input_groups(node):
    """Names of the input groups that have no element: Blender does not list them."""
    empty, filled = set(), set()
    for source in node.inputs():
        geometry = source.geometry() if source is not None else None
        if geometry is None:
            continue
        for groups, count in ((geometry.pointGroups(), "pointCount"), (geometry.primGroups(), "primCount"),
                              (geometry.edgeGroups(), "edgeCount")):
            for group in groups:
                (filled if getattr(group, count)() else empty).add(group.name())
    return empty - filled


def _describe_menu(entry, parm_tuple, template, menu):
    parm = parm_tuple[0]
    entry["menu"] = menu
    entry["value"] = parm.evalAsString()
    tokens = [token for token, _ in menu]
    if template.type() == hou.parmTemplateType.Int and entry["value"] not in tokens and 0 <= parm.eval() < len(tokens):
        # An integer menu with text tokens evaluates to the item's index.
        entry["value"] = tokens[parm.eval()]


def _describe_ramp(entry, parm_tuple, template, menu):
    entry["ramp"] = "color" if template.parmType() == hou.rampParmType.Color else "float"
    entry["value"] = ramp_value(parm_tuple[0].evalAsRamp())


def _text(parm):
    """The unexpanded text of a string parameter; its evaluated text when Houdini drives it
    (unexpandedString raises then)."""
    try:
        return parm.unexpandedString()
    except hou.OperationFailed:
        return parm.evalAsString()


def is_driven(parm_tuple):
    """Whether Houdini drives a component with keyframes or an expression (a channel
    reference is one): Blender does not send such a value until it changes there."""
    return any(parm.keyframes() for parm in parm_tuple)


def _set(parm, value):
    """Write one component as a constant. A component Houdini drives keeps its expression
    through Parm.set, which only adds a keyframe, and a channel reference writes the
    parameter it reads: both are dropped first, as typing a value in Houdini replaces them."""
    if parm.keyframes():
        parm.deleteAllKeyframes()
    parm.set(value, follow_parm_reference=False)


def _same(current, value):
    if isinstance(current, float) and isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isclose(current, value, rel_tol=VALUE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE)
    return current == value


def set_components(parm_tuple, value):
    """A list sets one value per component (cut to the parameter's size), anything else
    the first component. Also writes the templates no kind writes (a multiparm's count).
    Only the components that differ are written, so one that reads another by a channel
    reference keeps reading it."""
    values = value if isinstance(value, list) else [value]
    current = list(parm_tuple.eval())
    changed = [(parm, wanted) for parm, now, wanted in zip(parm_tuple, current, values, strict=False)
               if not _same(now, wanted)]
    for parm, wanted in changed:
        _set(parm, wanted)


def _write_int(parm_tuple, value):
    if isinstance(value, str):
        # An Int template with a menu: Blender sends the token (or an index as text).
        tokens = list(parm_tuple[0].menuItems())
        value = tokens.index(value) if value in tokens and not value.lstrip("-").isdigit() else int(value)
    set_components(parm_tuple, value)


def _write_string(parm_tuple, value):
    if isinstance(value, list):
        set_components(parm_tuple, value)
        return
    parm = parm_tuple[0]
    if _text(parm) != value:
        _set(parm, value)


def _write_menu(parm_tuple, value):
    if isinstance(value, list):
        set_components(parm_tuple, value)
        return
    parm = parm_tuple[0]
    tokens = list(parm.menuItems())
    if value in tokens and parm.evalAsString() != value:
        _set(parm, tokens.index(value))


def _write_button(parm_tuple, value):
    parm_tuple[0].pressButton()


def _write_ramp(parm_tuple, value):
    if isinstance(value, dict):
        set_ramp(parm_tuple[0], value)


@dataclass(frozen=True)
class Kind:
    describe: Callable | None  # (entry, parm_tuple, template, menu): its fields; None when it has none
    write: Callable  # (parm_tuple, value)


# In the order of contract.PARM_KINDS (tests/unit/test_parm_registry.py checks it).
KINDS = {
    contract.FLOAT: Kind(_describe_float, set_components),
    contract.INT: Kind(_describe_int, _write_int),
    contract.TOGGLE: Kind(_describe_toggle, set_components),
    contract.STRING: Kind(_describe_string, _write_string),
    contract.MENU: Kind(_describe_menu, _write_menu),
    contract.BUTTON: Kind(None, _write_button),
    contract.RAMP: Kind(_describe_ramp, _write_ramp),
}


# --- which kind a template is ---------------------------------------------------------

FREE_TEXT_MENUS = (hou.menuType.StringReplace, hou.menuType.StringToggle)
# The kind of a template without a menu, by its template type; the others are not shown.
TEMPLATE_KINDS = {
    hou.parmTemplateType.Float: contract.FLOAT,
    hou.parmTemplateType.Int: contract.INT,
    hou.parmTemplateType.Toggle: contract.TOGGLE,
    hou.parmTemplateType.String: contract.STRING,
    hou.parmTemplateType.Button: contract.BUTTON,
    hou.parmTemplateType.Ramp: contract.RAMP,
}


def _free_text(template):
    return template.type() == hou.parmTemplateType.String and template.menuType() in FREE_TEXT_MENUS


def kind_of(template, menu):
    """The kind a template is described as, or None (a parameter Blender does not show).
    `menu` is its evaluated menu, empty when it has none. A String template with a
    free-text menu is a string with suggestions; any other template with a one-component
    menu is a menu (an Int one included); the others follow their template type. The
    order of the tests matters."""
    if menu and _free_text(template):
        return contract.STRING
    if menu and template.numComponents() == 1:
        return contract.MENU
    return TEMPLATE_KINDS.get(template.type())


def written_as(template):
    """The kind whose writer sets this template, from the template type alone. A menu on an
    Int template is written as an int (its writer takes a token), on a String template as a
    string. None for the other templates (the count of a multiparm is a folder):
    set_components writes them."""
    if template.type() == hou.parmTemplateType.Menu:
        return contract.MENU
    return TEMPLATE_KINDS.get(template.type())


def _update_states(node):
    """Evaluate the hide-when and disable-when conditions of the node's parameters. In
    hython, Parm.isHidden() and isDisabled() give the state left by the last
    node.updateParmStates(), which neither a value set nor a cook refreshes (graphical
    Houdini has its Parameter Pane call it). False when the states could not be evaluated."""
    try:
        node.updateParmStates()
    except hou.Error as error:
        log.warning("parameter states of %s: %s", node.path(), error)
        return False
    return True


def _states(parm, evaluated):
    """(hidden, disabled) of a parameter; both false when the states were not evaluated."""
    if not evaluated:
        return False, False
    try:
        return bool(parm.isHidden()), bool(parm.isDisabled())
    except hou.Error:
        return False, False


def _folder_states(node, template, name, evaluated):
    """(hidden, disabled) of a Simple or Collapsible folder, read on its own folder-set
    parameter. Houdini hides or greys out everything such a folder holds, but leaves each
    parameter inside with its own state, so describe_parms passes this one down. A tab is
    never read: its folder-set parameter is shared by the tabs of the set, and holds the
    tab conditions of the first tab only, or those of the whole set."""
    if template.folderType() not in STATE_FOLDERS:
        return False, False
    parm_tuple = node.parmTuple(name)
    if parm_tuple is None or not isinstance(parm_tuple.parmTemplate(), hou.FolderSetParmTemplate):
        return False, False
    return _states(parm_tuple[0], evaluated)


def _either(states, inherited):
    return states[0] or inherited[0], states[1] or inherited[1]


def _describe_template(node, template, folder, name=None, evaluated=False):
    """The description of one parameter, or None when Blender does not show it. `name` is
    the real name of a multiparm instance (`#` replaced); `evaluated` says whether
    _update_states() could evaluate the states (if not, it is visible and active)."""
    template_type = template.type()
    name = name or template.name()
    parm_tuple = node.parmTuple(name)
    if parm_tuple is None:
        return None
    parm = parm_tuple[0]
    entry = {
        "name": name,
        "label": template.label() or name,
        "folder": folder,
        "folder_types": [],  # set by describe_parms, which knows the folders
        "help": template.help(),
        "size": template.numComponents(),
    }
    entry["hidden"], entry["disabled"] = _states(parm, evaluated)

    T = hou.parmTemplateType
    menu = []
    if template_type in (T.Menu, T.String, T.Int):
        try:
            tokens = parm.menuItems()
            labels = parm.menuLabels()
            menu = [[token, label or token] for token, label in zip(tokens, labels, strict=False)]
        except hou.Error:
            menu = []

    kind = kind_of(template, menu)
    if kind is None:
        return None
    entry["kind"] = kind
    if KINDS[kind].describe is not None:
        KINDS[kind].describe(entry, parm_tuple, template, menu)
    entry["expression"] = is_driven(parm_tuple)
    return entry


def _instance_name(name, indices):
    """The name of a multiparm instance parameter: each `#` takes an index, outer first."""
    for index in indices:
        name = name.replace("#", str(index), 1)
    return name


def _instance_title(label):
    """The title of each instance panel: "Number of Attributes" gives "Attributes"."""
    return re.sub(r"^(number of|num|#)\s+", "", label, flags=re.IGNORECASE) or label


# What each Houdini folder type is drawn as in Blender (contract.FOLDER_TYPES). A radio folder
# is drawn as a tab (one shows at a time); an import block as a group box.
FOLDER_TYPES = {
    hou.folderType.Collapsible: contract.FOLDER_COLLAPSIBLE,
    hou.folderType.Simple: contract.FOLDER_SIMPLE,
    hou.folderType.Tabs: contract.FOLDER_TAB,
    hou.folderType.RadioButtons: contract.FOLDER_TAB,
    hou.folderType.ImportBlock: contract.FOLDER_SIMPLE,
}


def describe_parms(node):
    """Flat list of the visible parameters, each with its folder path (a list of labels) and
    the type of each of those folders (folder_types: [type, tab set, name] per level).

    A multiparm gives its count (an int, `multiparm: true`) in the current
    folder, then each of its instances as a folder "<title> <n>" holding the
    instance parameters under their real names (`#` replaced); nested
    multiparms follow the same rule inside an instance. The hidden and disabled states
    are those of the values the node holds now: describe after writing the values and
    cooking (Server.cook does). A parameter is hidden (disabled) when it is, or when a
    Simple or Collapsible folder or a multiparm that holds it is, nested ones included.
    Adjacent tab folders form one set, named after its first folder (the name of the
    set's folder-set parameter), unless a folder ends its set (endsTabGroup)."""
    result = []
    evaluated = _update_states(node)
    T = hou.parmTemplateType

    def walk(templates, folder, types, indices, inherited):
        tab_set, ended = "", True  # the set of the tab folders just walked, and whether it ended
        for template in templates:
            template_type = template.type()
            folder_type = FOLDER_TYPES.get(template.folderType()) if template_type == T.Folder else None
            if folder_type != contract.FOLDER_TAB:
                tab_set, ended = "", True  # anything else between two tab folders ends their set
            if template.isHidden():
                continue
            if template_type == T.Folder and template.folderType() in MULTIPARM_FOLDERS:
                multiparm(template, folder, types, indices, inherited)
                continue
            if template_type == T.Folder:
                name = _instance_name(template.name(), indices)
                if folder_type == contract.FOLDER_TAB:
                    if ended:
                        tab_set = name
                    ended = template.endsTabGroup()
                states = _folder_states(node, template, name, evaluated)
                # The folder's name, unique in the node, tells apart two folders of one label.
                level = [folder_type or contract.FOLDER_COLLAPSIBLE, tab_set if folder_type == contract.FOLDER_TAB
                         else "", name]
                walk(template.parmTemplates(), folder + [template.label()], types + [level], indices,
                     _either(states, inherited))
                continue
            if template_type in (T.FolderSet, T.Separator, T.Label, T.Data):
                continue
            entry = _describe_template(node, template, folder, _instance_name(template.name(), indices), evaluated)
            if entry is not None:
                entry["folder_types"] = types
                entry["hidden"], entry["disabled"] = _either((entry["hidden"], entry["disabled"]), inherited)
                result.append(entry)

    def multiparm(template, folder, types, indices, inherited):
        name = _instance_name(template.name(), indices)
        count = node.parm(name)
        if count is None:
            return
        label = template.label() or name
        # The instances take the state of their count, which Houdini hides or greys out with them.
        hidden, disabled = _either(_states(count, evaluated), inherited)
        result.append({
            "name": name, "label": label, "folder": folder, "folder_types": types, "help": template.help(),
            "size": 1, "kind": contract.INT, "multiparm": True, "value": [count.evalAsInt()],
            "min": 0, "max": MULTIPARM_SLIDER_MAX, "min_strict": True, "max_strict": False,
            "hidden": hidden, "disabled": disabled, "expression": bool(count.keyframes()),
        })
        start = count.multiParmStartOffset()
        title = _instance_title(label)
        for number in range(count.evalAsInt()):
            # An instance is named after its count and its number (a parameter name has no space).
            walk(template.parmTemplates(), folder + ["%s %d" % (title, start + number)],
                 types + [[contract.FOLDER_INSTANCE, "", "%s %d" % (name, start + number)]],
                 indices + [start + number], (hidden, disabled))

    walk(node.parmTemplateGroup().entries(), [], [], [], (False, False))
    return result


def _ramp_differs(current, value):
    old = ramp_value(current)
    if list(old["basis"]) != value["basis"] or len(old["keys"]) != len(value["keys"]):
        return True
    keys = np.allclose(old["keys"], value["keys"], atol=RAMP_TOLERANCE)
    values = np.allclose(np.array(old["values"], float), np.array(value["values"], float), atol=RAMP_TOLERANCE)
    return not (keys and values)


def set_ramp(parm, value):
    if not value.get("keys") or not _ramp_differs(parm.evalAsRamp(), value):
        return
    bases = tuple(getattr(hou.rampBasis, name, hou.rampBasis.Linear) for name in value["basis"])
    values = tuple(tuple(v) if isinstance(v, list) else float(v) for v in value["values"])
    parm.set(hou.Ramp(bases, tuple(value["keys"]), values))


def set_parms(node, parms):
    """Set parameter values. A multiparm instance only exists once its count is
    set: what is missing is tried again after the others (nested multiparms
    need one more pass per level)."""
    pending = dict(parms)
    for _ in range(MULTIPARM_PASSES):
        missing = {}
        for name, value in pending.items():
            if node.parmTuple(name) is None:
                missing[name] = value
                continue
            try:
                set_parm(node, name, value)
            except (hou.Error, TypeError, ValueError) as error:
                # One value Houdini refuses must not cost the whole cook.
                log.warning("parameter %s: %s", name, error)
        if not missing or len(missing) == len(pending):
            return
        pending = missing


def set_parm(node, name, value):
    """Write one value, with the writer of the template's type (written_as)."""
    parm_tuple = node.parmTuple(name)
    if parm_tuple is None:
        return
    kind = written_as(parm_tuple.parmTemplate())
    write = KINDS[kind].write if kind is not None else set_components
    write(parm_tuple, value)
