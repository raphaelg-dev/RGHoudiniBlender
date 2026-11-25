"""Houdini group strings of element numbers: reading them, and writing them compactly.

Besides group names and patterns, a Houdini group field takes element numbers:

- points and primitives: numbers and ranges, "0-11 15 20-22" (a range may run
  backwards, "11-0", or take a step, "0-11:2");
- edges: paths of point numbers, each pair of neighbours one edge, "p0-1-2 p5-6"
  (the edges 0-1, 1-2 and 5-6), as hou.Selection.selectionString writes them and
  hou.Geometry.globEdges reads them; the direction does not matter.

Blender sends its vertices and faces in their order (blender/core/mesh_io/read.py), so an
index in Blender is the number in Houdini. Corners (Houdini's vertices) are not selected.
"""

import re

from ....shared import contract

# What a selection mode of Blender's Edit Mode selects, as a group type, and back.
MODE_OF_TYPE = {contract.GROUP_POINTS: "VERT", contract.GROUP_EDGES: "EDGE", contract.GROUP_PRIMITIVES: "FACE"}
TYPE_OF_MODE = {mode: group_type for group_type, mode in MODE_OF_TYPE.items()}
# The word a badge uses for the elements of a mode.
ELEMENT_WORDS = {"VERT": contract.GROUP_POINTS, "EDGE": contract.GROUP_EDGES, "FACE": "faces"}

_NUMBER = re.compile(r"^(\d+)(?:-(\d+)(?::(\d+))?)?$")
_EDGE_PATH = re.compile(r"^p(\d+(?:-\d+)+)$")


def read_numbers(text):
    """The sorted element numbers of a point or primitive group string, or None when it holds
    anything other than numbers and ranges (a group name, a pattern such as "@P.y>0", "!3"
    or "*"). An empty string gives []."""
    found = set()
    for token in text.split():
        match = _NUMBER.match(token)
        if match is None:
            return None
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) is not None else first
        step = int(match.group(3)) if match.group(3) is not None else 1
        if step < 1:
            return None
        low, high = min(first, last), max(first, last)
        found.update(range(low, high + 1, step) if first <= last else range(high, low - 1, -step))
    return sorted(found)


def write_numbers(numbers):
    """The compact group string of element numbers: "0-11 15 20-22"."""
    ordered = sorted(set(int(number) for number in numbers))
    parts = []
    start = previous = None
    for number in ordered:
        if previous is not None and number == previous + 1:
            previous = number
            continue
        if start is not None:
            parts.append(_run(start, previous))
        start = previous = number
    if start is not None:
        parts.append(_run(start, previous))
    return " ".join(parts)


def _run(start, end):
    return str(start) if start == end else "%d-%d" % (start, end)


def read_edges(text):
    """The edges of an edge group string as sorted (low, high) point pairs, or None when it
    holds anything other than paths of point numbers ("p0-1-2 p5-6"). An empty string gives []."""
    found = set()
    for token in text.split():
        match = _EDGE_PATH.match(token)
        if match is None:
            return None
        points = [int(number) for number in match.group(1).split("-")]
        for start, end in zip(points, points[1:], strict=False):
            if start != end:
                found.add((min(start, end), max(start, end)))
    return sorted(found)


def write_edges(edges):
    """The compact edge group string of point pairs, consecutive edges joined into one path
    ("p0-1-2 p5-6"). Deterministic: each path starts from the smallest pair left and goes on
    through the smallest neighbour."""
    left = {(min(a, b), max(a, b)) for a, b in edges if a != b}
    neighbours = {}
    for a, b in left:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    parts = []
    while left:
        start, current = min(left)
        path = [start, current]
        _take(left, neighbours, start, current)
        while neighbours.get(current):
            following = min(neighbours[current])
            _take(left, neighbours, current, following)
            path.append(following)
            current = following
        parts.append("p" + "-".join(str(point) for point in path))
    return " ".join(parts)


def _take(left, neighbours, a, b):
    left.discard((min(a, b), max(a, b)))
    neighbours[a].discard(b)
    neighbours[b].discard(a)


def read_selection(text, group_type):
    """What a group string preselects: numbers (points, primitives), point pairs (edges), or
    None when it cannot be read as such (it is then left as it is until a selection is
    confirmed)."""
    if group_type == contract.GROUP_EDGES:
        return read_edges(text)
    return read_numbers(text)


def write_selection(elements, group_type):
    if group_type == contract.GROUP_EDGES:
        return write_edges(elements)
    return write_numbers(elements)


def type_of_token(token):
    """The group type of a group type menu token, or None ("guess", or a token Houdini does
    not map either)."""
    return contract.GROUP_TYPE_TOKENS.get(token)


def token_of_type(group_type, tokens):
    """The menu token for this type, as Houdini sets the menu after a selection
    (soputils.groupTypeParmFromGeometryType), or None when the menu has none."""
    for token, given in contract.GROUP_TYPE_TOKENS.items():
        if given == group_type and token in tokens:
            return token
    return None


def initial_type(types, text, current):
    """The group type a selection starts in, as soputils.selectGroupParm chooses it.

    `types` are the types the field takes, the first one first; `text` its current value;
    `current` the type Blender selects now, or None. A field taking edges and another type
    starts in edges when its text is an edge string (it does not start with a digit), in
    the current type when it is empty and the field takes that type, else in the other type.
    Unknown types (empty) keep the current one."""
    if not types:
        return current
    if contract.GROUP_EDGES in types and len(types) > 1:
        other = next(group_type for group_type in types if group_type != contract.GROUP_EDGES)
        stripped = text.strip()
        if not stripped:
            return current if current in types else other
        return other if stripped[0].isdigit() else contract.GROUP_EDGES
    return types[0]
