"""The kinds of parameters on the Blender side, and their registry KINDS.

A kind (contract.PARM_KINDS) is a Kind: a record of functions with no state (what a
parameter keeps is stored in its HoudiniBridgeParm). It says of which socket type the
parameter's input is, how it enters the usage chain, how its value is read and written in
the server's format, what it copies from a description, and what in a description
rebuilds the inputs. Its row in the modifier panel is drawn by ui/parameter_rows.py.

What one kind alone does is a plain function, called where it is needed: the node holding
a ramp (ramps.py), and the code editor and group field of a string (below).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass

from ....shared import contract
from .. import stored


@dataclass(frozen=True)
class Kind:
    """One kind of parameter on the Blender side. `parm` is a HoudiniBridgeParm, `description`
    one entry of the "parms" of a cook answer."""

    name: str  # its name in contract.PARM_KINDS
    socket: Callable | None = None  # (parm) -> the socket type of its input; None: no input
    use: Callable | None = None  # (chain, parm, source): how it enters the usage chain; None: stored as it is
    read: Callable | None = None  # (parm, raw) -> the input's value in the server's format
    convert: Callable | None = None  # (parm, value) -> a server value as the input takes it, or None
    ranged: bool = False  # it has a range: its values are held within the limits the preference chooses
    copy: Callable | None = None  # (parm, description): the fields of its kind, at a rebuild
    signature: tuple = ()  # the fields of its description whose change rebuilds the inputs


RANGE = ("min", "max", "min_strict", "max_strict")


# --- float: a Float, Vector or Color input --------------------------------------------


def _float_socket(parm):
    if parm.is_color:
        return "NodeSocketColor"
    return "NodeSocketFloat" if parm.size == 1 else "NodeSocketVector"


def _float_read(parm, raw):
    return [float(raw)] if parm.size == 1 else [float(v) for v in list(raw)[: parm.size]]


def _float_convert(parm, value):
    values = list(value) if isinstance(value, (list, tuple)) else [value]
    if parm.is_color:
        return (values + [0.0, 0.0, 0.0])[:3] + [1.0]
    if parm.size == 1:
        return float(values[0])
    return values[: parm.size]


def _float_copy(parm, description):
    parm.is_color = bool(description.get("is_color")) and parm.size == 3


FLOAT = Kind(contract.FLOAT, socket=_float_socket, read=_float_read,
             convert=_float_convert, ranged=True, copy=_float_copy, signature=RANGE)


# --- int and toggle -------------------------------------------------------------------


def _int_convert(parm, value):
    return int(value[0] if isinstance(value, (list, tuple)) else value)


INT = Kind(contract.INT, socket=lambda parm: "NodeSocketInt", read=lambda parm, raw: [int(raw)],
           convert=_int_convert, ranged=True, signature=RANGE)
TOGGLE = Kind(contract.TOGGLE, socket=lambda parm: "NodeSocketBool", read=lambda parm, raw: bool(raw),
              convert=lambda parm, value: bool(value))


# --- string: text, or a group field (free text with suggestions) ----------------------


def _string_use(chain, parm, source):
    length = chain.node("FunctionNodeStringLength")
    chain.link(source, length.inputs[0])
    chain.store(length.outputs[0], "INT", parm.label)


def _string_copy(parm, description):
    parm.is_file = bool(description.get("is_file"))


STRING = Kind(contract.STRING, socket=lambda parm: "NodeSocketString", use=_string_use,
              read=lambda parm, raw: str(raw), convert=lambda parm, value: str(value), copy=_string_copy)


def refresh_from_answer(parm, description):
    """What a parameter takes from every cook answer: only a string has something, the
    language of its code editor (contract.PARM_FIELDS: editor) and the suggestions of a group
    field. A code parameter offers no suggestions: the code presets of its menu, which a file
    saved before 1.0 may keep, are cleared."""
    if parm.kind != contract.STRING:
        return
    editor = description.get("editor", "")
    if parm.editor != editor:
        parm.editor = editor
    if editor:
        if parm.menu_json:
            parm.menu_json = ""
        if parm.group_selection:
            parm.group_selection = ""
        return
    if "suggestions" in description:
        # Group fields: the groups found in the input, refreshed at every cook.
        parm.menu_json = json.dumps(description["suggestions"])
        parm.toggle = bool(description.get("toggle"))
        selection = ""
        if description.get("group_input") is not None:
            selection = json.dumps({"input": int(description["group_input"]),
                                    "types": list(description.get("group_types") or []),
                                    "type_parm": description.get("group_type_parm") or ""})
        if parm.group_selection != selection:
            parm.group_selection = selection


def _menu_pairs(parm):
    """[[token, label]] of a menu, or the [[value, label]] suggestions of a string field."""
    return stored.parse(parm.menu_json, [])


def group_suggestions(parm):
    """The values a group field offers (the groups of its input); none for another kind."""
    return _menu_pairs(parm) if parm.kind == contract.STRING else []


def pick_group(parm, current, group):
    """The value of a group field once `group` is picked: that group, or, for a toggle field,
    the groups already there (separated by spaces) and that one."""
    if parm.kind != contract.STRING or not parm.toggle:
        return group
    current = (current or "").split()
    return " ".join(current + [group]) if group not in current else " ".join(current)


def group_selection(parm):
    """How the viewport selects a group field: {"input", "types", "type_parm"} (from
    contract.PARM_FIELDS: group_input, group_types, group_type_parm), or None when the
    parameter cannot be selected there."""
    return stored.parse(parm.group_selection, None) if parm.kind == contract.STRING else None


# --- menu: a Menu input, whose items a Menu Switch defines ----------------------------


def menu_tokens(parm):
    """The Houdini tokens of a menu parameter, in the order of its items."""
    return [token for token, _ in _menu_pairs(parm)]


def menu_names(parm):
    """The item names of a menu socket: the Houdini labels, made unique."""
    names = []
    for _, label in _menu_pairs(parm):
        name = label or "Item"
        while name in names:
            name += " "
        names.append(name)
    return names


def _menu_use(chain, parm, source):
    # The Menu Switch also defines the items of the menu input.
    switch = chain.node("GeometryNodeMenuSwitch")
    switch.label = parm.label
    switch.data_type = "INT"
    switch.enum_items.clear()
    for name in menu_names(parm):
        switch.enum_items.new(name)
    chain.link(source, switch.inputs[0])
    chain.store(switch.outputs[0], "INT", parm.label)


def _menu_read(parm, raw):
    tokens = menu_tokens(parm)
    names = menu_names(parm)
    index = names.index(raw) if raw in names else 0
    return tokens[index] if tokens else ""


def _menu_convert(parm, value):
    tokens = menu_tokens(parm)
    index = tokens.index(value) if value in tokens else 0
    return menu_names(parm)[index] if tokens else None


def _menu_copy(parm, description):
    parm.menu_json = json.dumps(description["menu"])


MENU = Kind(contract.MENU, socket=lambda parm: "NodeSocketMenu", use=_menu_use, read=_menu_read,
            convert=_menu_convert, copy=_menu_copy, signature=("menu",))


# --- button: no input, a button of the panel ------------------------------------------

BUTTON = Kind(contract.BUTTON)


# --- ramp: held by a Float Curve or Color Ramp node of the node group (ramps.py) -------


def _ramp_copy(parm, description):
    parm.ramp = description.get("ramp", "")


RAMP = Kind(contract.RAMP, copy=_ramp_copy, signature=("ramp",))


# --- the registry ---------------------------------------------------------------------

# In the order of contract.PARM_KINDS (tests/unit/test_parm_registry.py checks it).
KINDS = {kind.name: kind for kind in (FLOAT, INT, TOGGLE, STRING, MENU, BUTTON, RAMP)}

# A kind this version does not know (a file saved by a later one): no input, no value, nothing drawn.
UNKNOWN = Kind("")


def kind_of(name):
    """The Kind named `name` (HoudiniBridgeParm.kind, or a description's "kind")."""
    return KINDS.get(name, UNKNOWN)
