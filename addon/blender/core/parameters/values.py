"""Parameter values: the limits a value must stay within, and reading and writing the values
in the server's format."""

from ....shared import contract
from .. import feedback, preferences, targets
from . import inputs, kinds, ramps

INT_MAX = 2**31 - 1  # the widest limits a Blender input takes
FLOAT_MAX = 3.4028234663852886e38  # the largest 32-bit float

# Houdini has strict bounds (the lock of the parameter editor) and a slider
# range: dragging stops at its ends, typing may leave it. A Blender input has
# only hard limits, and dragging goes anywhere: a Step Size whose slider stops
# at 0 in Houdini can go negative in Blender. Which Houdini bounds become limits
# is a preference ("Limits"): the strict ones, plus by default the
# minimum of the slider range (the maximum is often left on purpose: more
# iterations than the slider offers).


def store_range(parm, description):
    parm.has_range = kinds.kind_of(description.get("kind")).ranged and "min" in description
    if not parm.has_range:
        return
    parm.range_min, parm.range_max = float(description["min"]), float(description["max"])
    parm.min_strict = bool(description.get("min_strict"))
    parm.max_strict = bool(description.get("max_strict"))
    # A default outside the slider range means the range is no limit for this parameter.
    defaults = description.get("value") or []
    defaults = defaults if isinstance(defaults, list) else [defaults]
    parm.min_fits = all(value >= parm.range_min for value in defaults)
    parm.max_fits = all(value <= parm.range_max for value in defaults)


def _limit_mode():
    return preferences.get().parameter_limits


def limits(parm, mode=None):
    """(minimum or None, maximum or None) a value of this parameter must stay within."""
    if not parm.has_range or not kinds.kind_of(parm.kind).ranged:
        return None, None
    mode = mode or _limit_mode()
    low = parm.range_min if parm.min_strict or (mode in ("MINIMUM", "RANGE") and parm.min_fits) else None
    high = parm.range_max if parm.max_strict or (mode == "RANGE" and parm.max_fits) else None
    return low, high


def _bounded(parm, value, bounds=None):
    """A value held within the limits of its parameter: a driver, a keyframe or a script may
    set anything."""
    low, high = bounds or limits(parm)
    if low is not None and value < low:
        return type(value)(low)
    if high is not None and value > high:
        return type(value)(high)
    return value


def set_socket_limits(socket, parm):
    """Set the input's own limits, which the modifier panel uses for dragging."""
    if not hasattr(socket, "min_value"):
        return
    low, high = limits(parm)
    convert = int if socket.socket_type == "NodeSocketInt" else float
    big = INT_MAX if convert is int else FLOAT_MAX
    low_value = convert(low) if low is not None else -big
    high_value = convert(high) if high is not None else big
    if socket.min_value != low_value:
        socket.min_value = low_value
    if socket.max_value != high_value:
        socket.max_value = high_value


def refresh_limits():
    """Give every Houdini modifier input the limits of the current preference."""
    for tree in targets.houdini_trees():
        sockets = {item.identifier: item for item in tree.interface.items_tree if item.item_type == "SOCKET"}
        for parm in tree.houdini_bridge.parms:
            if parm.socket in sockets:
                set_socket_limits(sockets[parm.socket], parm)


def read_value(modifier, parm):
    """A parameter's value (its input, or the node holding it) in the format the server
    expects, None when it has none. A value with a range is held within its limits."""
    if parm.kind == contract.RAMP:
        node = ramps.ramp_node(modifier.node_group, parm)
        return ramps.read_ramp(node, parm) if node is not None else None
    kind = kinds.kind_of(parm.kind)
    item = inputs.modifier_input(modifier, parm.socket)
    if item is None or kind.read is None:
        return None
    value = kind.read(parm, item.value)
    if kind.ranged:
        value = [_bounded(parm, number) for number in value]
    return value


def write_value(modifier, parm, value):
    """Set a parameter's input (or the node holding it) from a value in the server's format."""
    if parm.kind == contract.RAMP:
        node = ramps.ramp_node(modifier.node_group, parm)
        if node is not None and isinstance(value, dict):
            ramps.write_ramp(node, parm, value)
        return
    kind = kinds.kind_of(parm.kind)
    converted = kind.convert(parm, value) if kind.convert is not None else None
    if converted is None:
        return
    item = inputs.modifier_input(modifier, parm.socket)
    try:
        if item is not None:
            item.value = converted
    except (TypeError, ValueError) as error:  # the input keeps its value
        feedback.log.warning("parameter %s: %r refused (%s)", parm.name, converted, error)


def hold_within_limits(modifier, settings):
    """Bring back within its limits a modifier input that left them: Blender 5.2 does not
    hold modifier inputs within their limits when a value is typed or set by a script, so
    the field then shows the value that is really used. True when a value was changed."""
    changed = False
    mode = _limit_mode()
    for parm in settings.parms:
        bounds = limits(parm, mode)
        if bounds == (None, None) or not parm.socket:
            continue
        item = inputs.modifier_input(modifier, parm.socket)
        if item is None:
            continue
        raw = item.value
        values = [raw] if isinstance(raw, (int, float)) else list(raw)
        bounded = [_bounded(parm, value, bounds) for value in values]
        if bounded != values:
            write_value(modifier, parm, bounded if len(bounded) > 1 else bounded[0])
            changed = True
    return changed


def read_values(modifier, settings):
    values = {}
    for parm in settings.parms:
        if not (parm.socket or parm.node):
            continue
        value = read_value(modifier, parm)
        if value is not None:
            values[parm.name] = value
    return values


def apply_values(modifier, settings, values):
    for parm in settings.parms:
        if (parm.socket or parm.node) and parm.name in values:
            write_value(modifier, parm, values[parm.name])
