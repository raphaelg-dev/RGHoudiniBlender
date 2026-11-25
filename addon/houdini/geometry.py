"""Geometry in and out of Houdini: the meshes Blender sends, with their groups and
attributes, the Volume objects it sends as .vdb files, and the mesh of a cooked result.

Everything here is in Houdini space (Y up, clockwise faces): conversion from and to
Blender happens on the Blender side.
"""

import re

import hou
import numpy as np

# The Run Over menu of an Attribute Wrangle ("class").
RUN_OVER_PRIMITIVES = 1
RUN_OVER_POINTS = 2
RUN_OVER_VERTICES = 3
# The Group Type menu of a Blast ("grouptype").
BLAST_PRIMITIVES = 4


def _valid_name(name):
    """A valid Houdini group or attribute name: letters, digits and underscores, not starting
    with a digit."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name.strip()) or "group"
    return "_" + cleaned if cleaned[0].isdigit() else cleaned


def _valid_names(entries):
    """The valid name of each (name, class), unique within its class: a name already valid
    keeps it, and one that cleans to a name taken gets a suffix ("poids.1" next to "poids_1"
    becomes "poids_1_2")."""
    taken = {(name, kind) for name, kind in entries if _valid_name(name) == name}
    names = []
    for name, kind in entries:
        cleaned = base = _valid_name(name)
        number = 1
        while cleaned != name and (cleaned, kind) in taken:
            number += 1
            cleaned = "%s_%d" % (base, number)
        taken.add((cleaned, kind))
        names.append(cleaned)
    return names


def _import_groups(geometry, arrays, prefix, groups):
    """Blender groups (boolean attributes on points, edges, faces) as Houdini groups.

    `groups` lists [name, class, block]: the block holds point or primitive
    numbers, or point pairs for an edge group."""
    points = geometry.points()
    primitives = geometry.prims()
    names = _valid_names([(name, group_class) for name, group_class, _ in groups])
    for name, (_, group_class, block) in zip(names, groups, strict=True):
        values = arrays.get(prefix + block)
        if values is None:
            continue
        if group_class == "point":
            group = geometry.findPointGroup(name) or geometry.createPointGroup(name)
            group.add([points[i] for i in values if i < len(points)])
        elif group_class == "prim":
            group = geometry.findPrimGroup(name) or geometry.createPrimGroup(name)
            group.add([primitives[i] for i in values if i < len(primitives)])
        elif group_class == "edge":
            group = geometry.findEdgeGroup(name) or geometry.createEdgeGroup(name)
            for start, end in values.reshape(-1, 2):
                edge = geometry.findEdge(points[start], points[end])
                if edge is not None:
                    group.add(edge)


ATTRIBUTE_TYPES = {"point": hou.attribType.Point, "prim": hou.attribType.Prim, "vertex": hou.attribType.Vertex}
# Names an attribute from Blender must not take: P is the positions, uv is sent on its own.
RESERVED_INPUT_NAMES = {"P", "Pw", "uv"}


def _set_values(geometry, attribute_class, kind, name, values):
    setter = getattr(geometry, "set%s%sAttribValuesFromString" % (
        attribute_class.capitalize(), "Float" if kind == "float" else "Int"))
    setter(name, values.astype(np.float32 if kind == "float" else np.int32).tobytes())


def set_uv(geometry, uv):
    if uv is None or not len(uv):
        return
    uv3 = np.zeros((len(uv) // 2, 3), np.float32)
    uv3[:, :2] = uv.reshape(-1, 2)
    if geometry.findVertexAttrib("uv") is None:
        geometry.addAttrib(hou.attribType.Vertex, "uv", (0.0, 0.0, 0.0)).setOption("type", "texturecoord")
    geometry.setVertexFloatAttribValuesFromString("uv", uv3.tobytes())


def import_attributes(geometry, arrays, prefix, attributes):
    """Blender attributes as Houdini attributes, created when missing.

    `attributes` lists [name, class, type, size, block, qualifier]; the
    qualifier (color, vector, normal, texturecoord) becomes the attribute's
    type option, as Houdini's own nodes set it."""
    find = {"point": geometry.findPointAttrib, "prim": geometry.findPrimAttrib, "vertex": geometry.findVertexAttrib}
    names = _valid_names([(entry[0], entry[1]) for entry in attributes])
    for name, (_, attribute_class, kind, size, block, qualifier) in zip(names, attributes, strict=True):
        values = arrays.get(prefix + block)
        attribute_type = ATTRIBUTE_TYPES.get(attribute_class)
        if values is None or attribute_type is None or name in RESERVED_INPUT_NAMES:
            continue
        attribute = find[attribute_class](name)
        if attribute is None:
            default = (0.0,) * size if kind == "float" else (0,) * size
            attribute = geometry.addAttrib(attribute_type, name, default)
            if qualifier:
                attribute.setOption("type", qualifier)
        if attribute.size() != size:
            continue
        _set_values(geometry, attribute_class, kind, name, values)


def import_mesh(arrays, prefix, groups=(), attributes=()):
    """Build a hou.Geometry from the arrays sent by Blender."""
    geometry = hou.Geometry()
    positions = arrays[prefix + "P"].reshape(-1, 3)
    if len(positions):
        geometry.createPoints([(0.0, 0.0, 0.0)] * len(positions))
        geometry.setPointFloatAttribValuesFromString("P", positions.astype(np.float32).tobytes())
    counts = arrays[prefix + "counts"]
    indices = arrays[prefix + "indices"]
    if len(counts):
        polygons = np.split(indices, np.cumsum(counts)[:-1])
        geometry.createPolygons([polygon.tolist() for polygon in polygons])
    set_uv(geometry, arrays.get(prefix + "uv"))
    if attributes:
        import_attributes(geometry, arrays, prefix, attributes)
    if groups:
        _import_groups(geometry, arrays, prefix, groups)
    return geometry


def import_volume(path, matrix):
    """A Blender Volume object's .vdb, brought into Houdini space by `matrix`
    (16 floats, row vectors: its placement, then Z up to Y up)."""
    geometry = hou.Geometry()
    geometry.loadFromFile(path)
    geometry.transform(hou.Matrix4([matrix[i:i + 4] for i in range(0, 16, 4)]))
    return geometry


def write_volume(source, destination, matrix):
    """A copy of a .vdb transformed by `matrix` (row vectors), for an applied Volume object."""
    import_volume(source, matrix).saveToFile(destination)


def floats(raw):
    return np.frombuffer(raw, dtype=np.float32)


def ints(raw):
    return np.frombuffer(raw, dtype=np.int32)


# Gives each vertex its point, its primitive and its rank in the primitive,
# so the topology can be rebuilt with a single sort on the Python side.
VEX_EXPORT_TOPOLOGY = """\
i@__point = vertexpoint(0, @vtxnum);
i@__primitive = vertexprim(0, @vtxnum);
i@__rank = vertexprimindex(0, @vtxnum);
"""


def export_mesh(geometry, extras=True):
    """Return (header fields, arrays) for a cooked geometry."""
    arrays = {}
    fields = {}
    arrays["P"] = floats(geometry.pointFloatAttribValuesAsString("P"))
    primitive_count = geometry.intrinsicValue("primitivecount")
    if primitive_count == 0 or geometry.findVertexAttrib("__primitive") is None:
        arrays["counts"] = np.zeros(0, np.int32)
        arrays["indices"] = np.zeros(0, np.int32)
        return fields, arrays

    primitive = ints(geometry.vertexIntAttribValuesAsString("__primitive"))
    rank = ints(geometry.vertexIntAttribValuesAsString("__rank"))
    point = ints(geometry.vertexIntAttribValuesAsString("__point"))
    order = np.lexsort((rank, primitive))
    indices = point[order]
    primitive_per_vertex = primitive[order]
    arrays["counts"] = np.bincount(primitive, minlength=primitive_count).astype(np.int32)
    arrays["indices"] = indices.astype(np.int32)

    def per_vertex(name, size):
        """Attribute values brought to the vertices (corners), or None."""
        attribute = geometry.findVertexAttrib(name)
        if attribute is not None and attribute.dataType() == hou.attribData.Float:
            values = floats(geometry.vertexFloatAttribValuesAsString(name)).reshape(-1, attribute.size())
            return values[order, :size]
        attribute = geometry.findPointAttrib(name)
        if attribute is not None and attribute.dataType() == hou.attribData.Float:
            values = floats(geometry.pointFloatAttribValuesAsString(name)).reshape(-1, attribute.size())
            return values[indices, :size]
        attribute = geometry.findPrimAttrib(name)
        if attribute is not None and attribute.dataType() == hou.attribData.Float:
            values = floats(geometry.primFloatAttribValuesAsString(name)).reshape(-1, attribute.size())
            return values[primitive_per_vertex, :size]
        return None

    uv = per_vertex("uv", 2)
    if uv is not None and uv.shape[1] == 2:
        arrays["uv"] = np.ascontiguousarray(uv, np.float32)
    normals = per_vertex("N", 3)
    if normals is not None and normals.shape[1] == 3:
        arrays["N"] = np.ascontiguousarray(normals, np.float32)

    color_attribute = geometry.findPointAttrib("Cd")
    if color_attribute is not None and color_attribute.size() >= 3:
        color = floats(geometry.pointFloatAttribValuesAsString("Cd")).reshape(-1, color_attribute.size())
        arrays["Cd_point"] = np.ascontiguousarray(color[:, :3])
    else:
        color = per_vertex("Cd", 3)
        if color is not None and color.shape[1] == 3:
            arrays["Cd_corner"] = np.ascontiguousarray(color, np.float32)

    materials = material_paths(geometry, "prim")
    if materials:
        fields["materials"], arrays["material"] = materials
    if extras:
        _export_extras(geometry, fields, arrays, order)
    return fields, arrays


def numeric_attributes(geometry, attribute_class, skip=()):
    """[(description, values (n, size))] of the numeric attributes (1 to 4 components) of
    one class ("point", "prim" or "vertex"), except those named in `skip` and the internal
    ones (starting with __). A description holds name, type ("float" or "int"), size and
    qualifier (Color, Texture Coord…, empty for VEX attributes)."""
    found, read_floats, read_ints = {
        "point": (geometry.pointAttribs, geometry.pointFloatAttribValuesAsString,
                  geometry.pointIntAttribValuesAsString),
        "prim": (geometry.primAttribs, geometry.primFloatAttribValuesAsString, geometry.primIntAttribValuesAsString),
        "vertex": (geometry.vertexAttribs, geometry.vertexFloatAttribValuesAsString,
                   geometry.vertexIntAttribValuesAsString),
    }[attribute_class]
    result = []
    for attribute in found():
        name = attribute.name()
        size = attribute.size()
        if name in skip or name.startswith("__") or attribute.isArrayType() or not 1 <= size <= 4:
            continue
        if attribute.dataType() == hou.attribData.Float:
            values, kind = floats(read_floats(name)), "float"
        elif attribute.dataType() == hou.attribData.Int:
            values, kind = ints(read_ints(name)), "int"
        else:
            continue
        description = {"name": name, "type": kind, "size": size, "qualifier": attribute.qualifier()}
        result.append((description, values.reshape(-1, size)))
    return result


def material_paths(geometry, attribute_class):
    """(names, index of each element's name) of shop_materialpath on one class ("point" or
    "prim"), the names sorted, or None when the class has no such string attribute."""
    find, read = {
        "point": (geometry.findPointAttrib, geometry.pointStringAttribValues),
        "prim": (geometry.findPrimAttrib, geometry.primStringAttribValues),
    }[attribute_class]
    attribute = find("shop_materialpath")
    if attribute is None or attribute.dataType() != hou.attribData.String:
        return None
    paths = read("shop_materialpath")
    names = sorted(set(paths))
    index_of = {name: i for i, name in enumerate(names)}
    return names, np.array([index_of[path] for path in paths], np.int32)


# Attributes sent on their own channel, or used internally.
HANDLED_ATTRIBUTES = {"P", "Pw", "N", "uv", "Cd"}


def _export_extras(geometry, fields, arrays, order):
    """Every numeric attribute (1 to 4 components) and every group of the result."""
    attributes = []
    for attribute_class in ("point", "prim", "vertex"):
        for description, values in numeric_attributes(geometry, attribute_class, HANDLED_ATTRIBUTES):
            if attribute_class == "vertex":
                values = values[order]  # corners in face order, as sent to Blender
            block = "attr%d" % len(attributes)
            arrays[block] = np.ascontiguousarray(values).ravel()
            # Unlike a stream attribute (streams.py), a mesh attribute names its class.
            attributes.append({"name": description["name"], "class": attribute_class, "type": description["type"],
                               "size": description["size"], "block": block, "qualifier": description["qualifier"]})

    groups = []

    def add(name, group_class, numbers):
        if name.startswith("__"):
            return
        block = "group%d" % len(groups)
        arrays[block] = np.array(numbers, np.int32).ravel()
        groups.append({"name": name, "class": group_class, "block": block})

    for group in geometry.pointGroups():
        add(group.name(), "point", [point.number() for point in group.points()])
    for group in geometry.primGroups():
        add(group.name(), "prim", [primitive.number() for primitive in group.prims()])
    for group in geometry.edgeGroups():
        pairs = []
        for edge in group.edges():
            start, end = edge.points()
            pairs.append((start.number(), end.number()))
        add(group.name(), "edge", pairs)
    fields["attributes"] = attributes
    fields["groups"] = groups
