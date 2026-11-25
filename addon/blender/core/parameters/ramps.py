"""Ramps: the Float Curve or Color Ramp node holding one, read and written in the
server's format (keys, values and one Houdini interpolation per key)."""

import json

from .. import stored

# A Houdini ramp is held by a node of the modifier's node group, edited with
# Blender's own widgets (in the modifier panel, or on the node): a Float Curve for
# a float ramp, a Color Ramp for a color ramp. Houdini sets an interpolation per
# key; a Color Ramp has one for all its keys, a curve point a handle type. The
# Houdini interpolation of each key is kept (ramp_basis) and sent back as long
# as Blender's own still matches it.


COLOR_INTERPOLATION = {"Constant": "CONSTANT", "Linear": "LINEAR", "CatmullRom": "CARDINAL", "BSpline": "B_SPLINE",
                       "MonotoneCubic": "EASE", "Bezier": "CARDINAL", "Hermite": "CARDINAL"}
COLOR_BASIS = {"CONSTANT": "Constant", "LINEAR": "Linear", "CARDINAL": "CatmullRom", "B_SPLINE": "BSpline",
               "EASE": "MonotoneCubic"}
HANDLE_TYPES = {"Constant": "VECTOR", "Linear": "VECTOR", "MonotoneCubic": "AUTO_CLAMPED"}  # the others: AUTO
HANDLE_BASIS = {"VECTOR": "Linear", "AUTO_CLAMPED": "MonotoneCubic", "AUTO": "CatmullRom"}
RAMP_NODES = {"color": "ShaderNodeValToRGB", "float": "ShaderNodeFloatCurve"}


def _stored_basis(parm):
    return stored.parse(parm.ramp_basis, [])


def _main_basis(bases):
    """The interpolation most keys use (the last key's shapes nothing). On a tie, the one
    met first in the order of the keys (max keeps the first of equal items), so a ramp
    always gets the same one."""
    relevant = bases[:-1] or bases
    return max(relevant, key=relevant.count) if relevant else "Linear"


def ramp_node(tree, parm):
    return tree.nodes.get(parm.node) if tree is not None and parm.node else None


def read_ramp(node, parm):
    stored = _stored_basis(parm)
    if parm.ramp == "color":
        ramp = node.color_ramp
        elements = sorted(ramp.elements, key=lambda element: element.position)
        keys = [element.position for element in elements]
        values = [[round(component, 6) for component in element.color[:3]] for element in elements]
        if len(stored) == len(keys) and COLOR_INTERPOLATION.get(_main_basis(stored)) == ramp.interpolation:
            basis = stored
        else:
            basis = [COLOR_BASIS.get(ramp.interpolation, "Linear")] * len(keys)
    else:
        points = sorted(node.mapping.curves[0].points, key=lambda point: point.location[0])
        keys = [point.location[0] for point in points]
        values = [round(point.location[1], 6) for point in points]
        basis = []
        for index, point in enumerate(points):
            original = stored[index] if len(stored) == len(points) else None
            if original is not None and HANDLE_TYPES.get(original, "AUTO") == point.handle_type:
                basis.append(original)
            else:
                basis.append(HANDLE_BASIS.get(point.handle_type, "Linear"))
    return {"basis": basis, "keys": [round(key, 6) for key in keys], "values": values}


def write_ramp(node, parm, value):
    # A list shorter than the others (a value sent by a script) cuts the keys to its length.
    entries = sorted(zip(value.get("keys", []), value.get("values", []), value.get("basis", []), strict=False),
                     key=lambda entry: entry[0])
    if not entries:
        return
    keys = [entry[0] for entry in entries]
    values = [entry[1] for entry in entries]
    bases = [entry[2] for entry in entries]
    parm.ramp_basis = json.dumps(bases)
    if parm.ramp == "color":
        ramp = node.color_ramp
        elements = ramp.elements
        # New elements are inserted in order: keep one, then add the others.
        while len(elements) > 1:
            elements.remove(elements[-1])
        elements[0].position = keys[0]
        elements[0].color = (*values[0][:3], 1.0)
        for key, color in zip(keys[1:], values[1:], strict=False):
            elements.new(key).color = (*color[:3], 1.0)
        ramp.interpolation = COLOR_INTERPOLATION.get(_main_basis(bases), "LINEAR")
        return
    mapping = node.mapping
    if len(keys) == 1:
        keys, values, bases = keys * 2, values * 2, bases * 2
    curve = mapping.curves[0]
    while len(curve.points) > 2:
        curve.points.remove(curve.points[-1])
    curve.points[0].location = (keys[0], values[0])
    curve.points[1].location = (keys[-1], values[-1])
    for key, height in zip(keys[1:-1], values[1:-1], strict=False):
        curve.points.new(key, height)
    for point, basis in zip(sorted(curve.points, key=lambda point: point.location[0]), bases, strict=False):
        point.handle_type = HANDLE_TYPES.get(basis, "AUTO")
    # The widget shows the clipping rectangle: it covers every key.
    mapping.use_clip = True
    mapping.clip_max_x, mapping.clip_max_y = max(1.0, max(keys)), max(1.0, max(values))
    mapping.clip_min_x, mapping.clip_min_y = min(0.0, min(keys)), min(0.0, min(values))
    mapping.update()


def node_name(parm):
    return "Ramp · %s" % parm.name


def ramp_node_for(tree, parm, index):
    """The node holding a ramp, made when missing (kept across rebuilds: it holds the value)."""
    name = node_name(parm)
    wanted = RAMP_NODES.get(parm.ramp, "ShaderNodeFloatCurve")
    node = tree.nodes.get(name)
    if node is not None and node.bl_idname != wanted:
        tree.nodes.remove(node)
        node = None
    if node is None:
        node = tree.nodes.new(wanted)
        node.name = name
    node.label = parm.label
    node.location = (-500 + 300 * index, -420)
    return node
