"""Reading the script_action tag of a group field: what Houdini's selection arrow would select.

In Houdini, a group field (the Base Group of a Group Create, say) carries a
`script_action` tag: a few lines of Python that the arrow next to the field runs,
for example

    import soputils
    kwargs['geometrytype'] = kwargs['node'].parmTuple('grouptype')
    kwargs['inputindex'] = 0
    soputils.selectGroupParm(kwargs)

read() reads those lines without running them (with `ast`) and says which element
types the selection takes, which parameter gives the type, and which input it
numbers the elements of. It knows only the plain statements Houdini and the SideFX
Labs write; anything else (an assignment inside an `if`, a call it does not know)
leaves that part unknown. houdini/parms.py resolves the result on the node.
"""

import ast
from typing import NamedTuple, Optional

from ..shared import contract

# hou.geometryType names -> the element types of the contract.
GEOMETRY_TYPES = {
    "Points": contract.GROUP_POINTS,
    "Primitives": contract.GROUP_PRIMITIVES,
    "Edges": contract.GROUP_EDGES,
    "Vertices": contract.GROUP_VERTICES,
    "Breakpoints": contract.GROUP_BREAKPOINTS,
}
SELECT_FUNCTION = "selectGroupParm"  # soputils.selectGroupParm(kwargs): the viewport selection of a group


class GroupSelection(NamedTuple):
    """What a script_action selects.

    types: the element types, the first one first (empty: unknown);
    type_parm: (path of a node inside the HDA, "" for the HDA itself, parameter name)
        whose menu token gives the type, or None;
    input_index: kwargs['inputindex'] as written (-n: input n when connected, else the first).
    """

    types: tuple
    type_parm: Optional[tuple]
    input_index: int


def _kwargs_key(node):
    """The key of `kwargs['key']`, or None."""
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "kwargs"
            and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
        return node.slice.value
    return None


def _geometry_type(node):
    """`hou.geometryType.Points` -> "points", or None."""
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "geometryType" and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "hou"):
        return GEOMETRY_TYPES.get(node.attr)
    return None


def _string_argument(call):
    if (isinstance(call, ast.Call) and len(call.args) == 1 and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)):
        return call.args[0].value
    return None


def _type_parm(node):
    """`kwargs['node'].parmTuple('name')` -> ("", name); `kwargs['node'].node("path").parmTuple('name')`
    -> (path, name); anything else -> None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "parmTuple"):
        return None
    name = _string_argument(node)
    owner = node.func.value
    if name is None:
        return None
    if _kwargs_key(owner) == "node":
        return "", name
    if isinstance(owner, ast.Call) and isinstance(owner.func, ast.Attribute) and owner.func.attr == "node":
        path = _string_argument(owner)
        if path is not None and _kwargs_key(owner.func.value) == "node":
            return path, name
    return None


def _types(node):
    """The element types of a literal: one hou.geometryType, or a tuple or list of them."""
    single = _geometry_type(node)
    if single is not None:
        return (single,)
    if isinstance(node, (ast.Tuple, ast.List)):
        found = tuple(_geometry_type(element) for element in node.elts)
        if found and all(found):
            return found
    return ()


def _integer(node):
    """An integer literal, negative ones included, or None."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _integer(node.operand)
        return -value if value is not None else None
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _calls_select(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == SELECT_FUNCTION):
            return True
    return False


def read(script):
    """The GroupSelection of a script_action, or None when it selects no group of an input
    of the node: no selectGroupParm call (a material picker, a visualizer), a syntax error,
    or kwargs['node'] set to another node (the selection then numbers the elements of a
    geometry inside the HDA, which Blender does not have)."""
    try:
        tree = ast.parse(script or "")
    except SyntaxError:
        return None
    if not _calls_select(tree):
        return None
    types, type_parm, input_index = (), None, 0
    assignments = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                key = _kwargs_key(target)
                if key is not None:
                    assignments.setdefault(key, []).append((node, node in tree.body))
    for node, _ in assignments.get("node", ()):
        if _kwargs_key(node.value) != "node":
            return None  # kwargs['node'] = <another node>: not an input of this one
    written = assignments.get("geometrytype", ())
    # Assigned once, at the top of the script: a type chosen in an `if` is not known here.
    if len(written) == 1 and written[0][1]:
        value = written[0][0].value
        type_parm = _type_parm(value)
        types = () if type_parm is not None else _types(value)
    written = assignments.get("inputindex", ())
    if len(written) == 1 and written[0][1]:
        index = _integer(written[0][0].value)
        if index is None or abs(index) >= contract.MAX_INPUTS:
            return None
        input_index = index
    return GroupSelection(types, type_parm, input_index)
