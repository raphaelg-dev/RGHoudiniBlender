"""Blender to Houdini: the groups, attributes and mesh of an object, as the wire carries them."""

import hashlib

import numpy as np
from mathutils import Matrix

from .convert import blender_to_houdini, face_reversal, topology_digest

# Blender (Z up) to Houdini (Y up), on column vectors: (x, y, z) -> (x, z, -y).
TO_HOUDINI = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))

DOMAIN_CLASSES = {"POINT": "point", "EDGE": "edge", "FACE": "prim"}
# Boolean attributes that are Blender bookkeeping rather than groups.
NOT_GROUPS = {"sharp_face"}


def _member_indices(attribute):
    """Indices of the elements where a BOOLEAN attribute is set."""
    values = np.empty(len(attribute.data), bool)
    attribute.data.foreach_get("value", values)
    return np.flatnonzero(values).astype(np.int32)


def read_groups(mesh, prefix):
    """Blender groups to send to Houdini: [name, class, block] and their arrays.

    A group is a boolean attribute on points, edges or faces: the groups of the Groups
    panel, sharp_edge, Geometry Nodes selections... Edges travel as pairs of point numbers."""
    candidates = []
    for attribute in mesh.attributes:
        if (attribute.data_type == "BOOLEAN" and attribute.domain in DOMAIN_CLASSES
                and not attribute.name.startswith(".") and attribute.name not in NOT_GROUPS):
            candidates.append((attribute.name, DOMAIN_CLASSES[attribute.domain], attribute))

    groups, arrays = [], {}
    edge_points = None
    for name, group_class, attribute in candidates:
        members = _member_indices(attribute)
        if group_class == "edge":
            if edge_points is None:
                edge_points = np.empty(len(mesh.edges) * 2, np.int32)
                mesh.edges.foreach_get("vertices", edge_points)
                edge_points = edge_points.reshape(-1, 2)
            members = edge_points[members].ravel()
        block = "g%d" % len(groups)
        arrays[prefix + block] = members
        groups.append([name, group_class, block])
    return groups, arrays


ATTRIBUTE_CLASSES = {"POINT": "point", "FACE": "prim", "CORNER": "vertex"}
# Blender data type → (foreach field, components, numpy type, Houdini type).
READABLE_TYPES = {
    "FLOAT": ("value", 1, np.float32, "float"),
    "INT": ("value", 1, np.int32, "int"),
    "INT8": ("value", 1, np.int32, "int"),
    "FLOAT2": ("vector", 2, np.float32, "float"),
    "INT16_2D": ("value", 2, np.int32, "int"),
    "INT32_2D": ("value", 2, np.int32, "int"),
    "FLOAT_VECTOR": ("vector", 3, np.float32, "float"),
    "FLOAT4": ("vector", 4, np.float32, "float"),
    "FLOAT_COLOR": ("color", 4, np.float32, "float"),
    "BYTE_COLOR": ("color", 4, np.float32, "float"),
}
# Blender bookkeeping that is not sent as an attribute (normals travel as N).
NOT_ATTRIBUTES = {"position", "material_index", "custom_normal"}


def _attribute_array(attribute):
    field, size, dtype, _ = READABLE_TYPES[attribute.data_type]
    values = np.empty(len(attribute.data) * size, dtype)
    attribute.data.foreach_get(field, values)
    return values.reshape(-1, size)


def _vertex_group_weights(mesh, names):
    """Painted weights, one column per vertex group (0 where a vertex is not in it).

    Vertex groups are not attributes: they are read vertex by vertex."""
    weights = np.zeros((len(mesh.vertices), len(names)), np.float32)
    for vertex in mesh.vertices:
        for element in vertex.groups:
            if element.group < len(names):
                weights[vertex.index, element.group] = element.weight
    return weights


def read_attributes(mesh, prefix, reversal, vertex_groups=(), linear=None):
    """Blender attributes to send to Houdini: [name, class, type, size, block, qualifier]
    and their arrays.

    Every non-boolean attribute of points, faces and corners (Houdini has no
    edge attributes), the painted weights of the vertex groups (point floats),
    the active color (Cd, plus Alpha when it is not opaque) and the custom
    normals (N). Vectors are transformed like P (by `linear`, the 3×3 part of
    the matrix applied to P, then the axes); corner values follow the reversed
    face order, like the UVs."""
    if linear is not None and np.allclose(linear, np.eye(3)):
        linear = None

    def vectors(values):
        return blender_to_houdini(values if linear is None else values @ linear.T)

    def normals(values):
        if linear is not None:
            values = values @ np.linalg.inv(linear)  # inverse transpose, on row vectors
            lengths = np.linalg.norm(values, axis=1, keepdims=True)
            values = values / np.where(lengths > 0, lengths, 1.0)
        return blender_to_houdini(values)

    uv_layer = mesh.uv_layers.active
    active_uv = uv_layer.name if uv_layer is not None else None
    color = mesh.color_attributes.active_color
    active_color = color.name if color is not None else None
    uv_names = {layer.name for layer in mesh.uv_layers}

    attributes, arrays, taken = [], {}, set()

    def add(name, attribute_class, kind, values, qualifier=""):
        if name in taken:
            return
        taken.add(name)
        if attribute_class == "vertex":
            values = values[reversal]
        block = "a%d" % len(attributes)
        arrays[prefix + block] = np.ascontiguousarray(values, np.float32 if kind == "float" else np.int32).ravel()
        attributes.append([name, attribute_class, kind, int(values.shape[1]), block, qualifier])

    if mesh.has_custom_normals and len(mesh.loops):
        corner_normals = np.empty(len(mesh.loops) * 3, np.float32)
        mesh.corner_normals.foreach_get("vector", corner_normals)
        add("N", "vertex", "float", normals(corner_normals.reshape(-1, 3)), "normal")

    for attribute in mesh.attributes:
        name = attribute.name
        if (attribute.domain not in ATTRIBUTE_CLASSES or attribute.data_type not in READABLE_TYPES
                or name.startswith(".") or name in NOT_ATTRIBUTES or name == active_uv):
            continue
        attribute_class = ATTRIBUTE_CLASSES[attribute.domain]
        kind = READABLE_TYPES[attribute.data_type][3]
        values = _attribute_array(attribute)
        if name == active_color:
            add("Cd", attribute_class, kind, values[:, :3], "color")
            if not np.all(values[:, 3] == 1.0):
                add("Alpha", attribute_class, kind, values[:, 3:])
        elif attribute.data_type in ("FLOAT_COLOR", "BYTE_COLOR"):
            # A color whose alpha is fully opaque goes as RGB, the usual Houdini color.
            add(name, attribute_class, kind, values if not np.all(values[:, 3] == 1.0) else values[:, :3], "color")
        elif attribute.data_type == "FLOAT_VECTOR":
            add(name, attribute_class, kind, vectors(values), "vector")
        elif attribute.data_type == "FLOAT2" and attribute.domain == "CORNER" and name in uv_names:
            # Other UV maps: 3 components and a texture qualifier, like Houdini's uv2.
            add(name, attribute_class, kind, np.pad(values, ((0, 0), (0, 1))), "texturecoord")
        else:
            add(name, attribute_class, kind, values)

    if vertex_groups and len(mesh.vertices):
        weights = _vertex_group_weights(mesh, vertex_groups)
        for column, name in enumerate(vertex_groups):
            add(name, "point", "float", weights[:, column:column + 1])
    return attributes, arrays


def read_mesh(mesh, matrix, prefix, vertex_groups=()):
    """A mesh transformed by `matrix` (4×4), in Houdini space, with its groups and attributes.

    Returns (arrays, meta, topology digest, content digest). `meta` holds the
    "groups", the "attributes" and the "values": the arrays that change without
    the topology changing (P, uv, attribute values). The topology digest covers
    the groups and the layout of the attributes; the content digest also covers
    the values: equal digests mean nothing needs to be sent.
    """
    positions = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", positions)
    positions = positions.reshape(-1, 3)
    matrix = np.array(matrix, dtype=np.float32)
    positions = positions @ matrix[:3, :3].T + matrix[:3, 3]

    counts = np.empty(len(mesh.polygons), np.int32)
    mesh.polygons.foreach_get("loop_total", counts)
    corners = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get("vertex_index", corners)
    reversal = face_reversal(counts)
    indices = corners[reversal]

    arrays = {
        prefix + "P": blender_to_houdini(positions).astype(np.float32).ravel(),
        prefix + "counts": counts,
        prefix + "indices": indices,
    }
    uv_layer = mesh.uv_layers.active
    # In Edit Mode the UV data can be empty: skip it.
    if uv_layer is not None and len(corners) and len(uv_layer.uv) == len(corners):
        uv = np.empty(len(corners) * 2, np.float32)
        uv_layer.uv.foreach_get("vector", uv)
        arrays[prefix + "uv"] = uv.reshape(-1, 2)[reversal].ravel()

    groups, group_arrays = read_groups(mesh, prefix)
    arrays.update(group_arrays)
    attributes, attribute_arrays = read_attributes(mesh, prefix, reversal, vertex_groups, matrix[:3, :3])
    arrays.update(attribute_arrays)
    values = [prefix + "P"] + [key for key in [prefix + "uv"] if key in arrays] + list(attribute_arrays)

    # Groups and the attribute layout are sent with the topology: a change needs
    # a full send. Values alone (P, uv, attributes) go by the light path.
    digest = hashlib.blake2b(digest_size=16)
    digest.update(topology_digest(counts, indices).encode())
    for name, group_class, block in groups:
        digest.update(("%s|%s|" % (name, group_class)).encode())
        digest.update(group_arrays[prefix + block].tobytes())
    digest.update(repr([prefix + "uv" in arrays] + attributes).encode())
    topology = digest.hexdigest()
    content = hashlib.blake2b(topology.encode(), digest_size=16)
    for key in values:
        content.update(arrays[key].tobytes())
    meta = {"groups": groups, "attributes": attributes, "values": values}
    return arrays, meta, topology, content.hexdigest()


def read_object(obj, depsgraph, matrix, prefix):
    """The evaluated mesh (modifiers included) of an object; see read_mesh."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        return read_mesh(mesh, matrix, prefix, [group.name for group in evaluated.vertex_groups])
    finally:
        evaluated.to_mesh_clear()
