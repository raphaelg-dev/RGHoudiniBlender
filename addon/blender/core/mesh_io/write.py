"""Houdini to Blender: the mesh of a result, its attributes and groups, and the
attributes of point clouds and curves."""

import math

import bpy
import numpy as np

from .convert import face_reversal, houdini_to_blender, topology_digest

# Digest of the topology last written into each output mesh (key: Mesh pointer). An
# address can be reused by another mesh once a file is loaded or an undo frees the
# old one: forget_written() empties it then (cook/handlers.py).
_written_topologies = {}


def forget_written():
    """Forget which topology each mesh was given: the next write of every mesh is a full one."""
    _written_topologies.clear()


def write_mesh(mesh, header, arrays, own_materials=None):
    """Update a mesh with the received geometry.

    When the topology did not change since the last write, only the positions and
    attributes are rewritten (a parameter being dragged). `own_materials` are the
    materials of the object the result shows on (Target.own_materials): the mesh takes
    them when Houdini names none, and follows them afterwards (follow_materials). With
    None, such a mesh has no material.
    """
    positions = houdini_to_blender(arrays["P"].reshape(-1, 3))
    counts = arrays["counts"].astype(np.int32)
    reversal = face_reversal(counts)
    corners = arrays["indices"][reversal].astype(np.int32)
    _write_topology(mesh, positions, counts, corners)
    _write_uv(mesh, arrays, reversal)
    _write_color(mesh, arrays, reversal)
    _write_materials(mesh, header.get("materials") if "material" in arrays else None, arrays, own_materials)
    extras = write_extras(mesh, header, arrays, reversal)
    _write_normals(mesh, arrays, reversal)
    mesh.update()
    return extras


def _write_topology(mesh, positions, counts, corners):
    """The points and faces; only the positions when the topology is the one last written."""
    digest = topology_digest(counts, corners)
    key = mesh.as_pointer()
    same_topology = (
        _written_topologies.get(key) == digest
        and len(mesh.vertices) == len(positions)
        and len(mesh.loops) == len(corners)
        and len(mesh.polygons) == len(counts)
    )
    if same_topology:
        mesh.vertices.foreach_set("co", positions.astype(np.float32).ravel())
        return
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.int32) if len(counts) else counts
    mesh.clear_geometry()
    mesh.vertices.add(len(positions))
    mesh.vertices.foreach_set("co", positions.astype(np.float32).ravel())
    mesh.loops.add(len(corners))
    mesh.loops.foreach_set("vertex_index", corners)
    mesh.polygons.add(len(counts))
    mesh.polygons.foreach_set("loop_start", starts)
    mesh.update(calc_edges=True)
    _written_topologies[key] = digest


def _write_uv(mesh, arrays, reversal):
    uv_layer = mesh.uv_layers.get("uv")
    if "uv" in arrays:
        uv_layer = uv_layer or mesh.uv_layers.new(name="uv")
        uv_layer.uv.foreach_set("vector", arrays["uv"].reshape(-1, 2)[reversal].ravel())
    elif uv_layer is not None:
        mesh.uv_layers.remove(uv_layer)


COLOR_BLOCKS = (("Cd_point", "POINT"), ("Cd_corner", "CORNER"))  # the first one sent is written


def _write_color(mesh, arrays, reversal):
    """Houdini's Cd as the color attribute "Cd", on the points or on the corners."""
    attribute = mesh.color_attributes.get("Cd")
    found = next(((name, domain) for name, domain in COLOR_BLOCKS if name in arrays), None)
    if found is None:
        if attribute is not None:
            mesh.color_attributes.remove(attribute)
        return
    name, domain = found
    colors = arrays[name].reshape(-1, 3)
    if domain == "CORNER":
        colors = colors[reversal]
    rgba = np.ones((len(colors), 4), np.float32)
    rgba[:, :3] = colors
    if attribute is not None and attribute.domain != domain:
        mesh.color_attributes.remove(attribute)
        attribute = None
    attribute = attribute or mesh.color_attributes.new("Cd", "FLOAT_COLOR", domain)
    attribute.data.foreach_set("color", rgba.ravel())
    mesh.color_attributes.active_color = attribute


def _write_normals(mesh, arrays, reversal):
    """Houdini's N as custom normals; without N, smooth up to 30°, like Houdini's default display."""
    custom = mesh.attributes.get("custom_normal")
    if "N" in arrays and len(mesh.loops):
        normals = houdini_to_blender(arrays["N"].reshape(-1, 3))[reversal].astype(np.float32)
        mesh.shade_smooth()
        # Write the "custom_normal" corner attribute directly: the lnor-space
        # computation of normals_split_custom_set crashed Blender 5.2 on some
        # boolean results.
        if custom is not None and (custom.domain != "CORNER" or custom.data_type != "FLOAT_VECTOR"):
            mesh.attributes.remove(custom)
            custom = None
        custom = custom or mesh.attributes.new("custom_normal", "FLOAT_VECTOR", "CORNER")
        custom.data.foreach_set("vector", np.nan_to_num(normals).ravel())
        return
    if custom is not None:
        mesh.attributes.remove(custom)
    mesh.set_sharp_from_angle(angle=math.radians(30.0))


# Set on a mesh whose result names no material and shows its owner's instead. Stored in
# .blend files, so a locked result keeps following them once the file is opened again.
FOLLOWS_OWNER_KEY = "houdini_bridge_follows_owner"


def _write_materials(mesh, paths, arrays, own_materials):
    """Houdini's materials and their material_index, or the owner's when Houdini names none:
    then every face takes the first slot, also when the topology is the one of an older
    result that named some (its material_index stays otherwise)."""
    if not paths and "material_index" in mesh.attributes:
        mesh.attributes.remove(mesh.attributes["material_index"])
    if not paths and own_materials is not None:
        mesh[FOLLOWS_OWNER_KEY] = True
        follow_materials(mesh, own_materials)
        return
    if FOLLOWS_OWNER_KEY in mesh:
        del mesh[FOLLOWS_OWNER_KEY]
    if set_materials(mesh, paths):
        mesh.polygons.foreach_set("material_index", arrays["material"].astype(np.int32))


def follow_materials(mesh, materials):
    """Give a mesh that follows its owner (FOLLOWS_OWNER_KEY) the owner's materials. Only a
    change is written: the write updates the owner, whose depsgraph update calls this again."""
    if not mesh.get(FOLLOWS_OWNER_KEY) or list(mesh.materials) == list(materials):
        return
    mesh.materials.clear()
    for material in materials:
        mesh.materials.append(material)


def set_materials(data, paths):
    """Set Houdini material paths (shop_materialpath) as the materials of a mesh, a point
    cloud, curves or a volume, found by name (made when missing). Returns the names."""
    names = [path.rstrip("/").split("/")[-1] or path for path in paths or []]
    if [m.name if m else "" for m in data.materials] != names:
        data.materials.clear()
        for name in names:
            data.materials.append(bpy.data.materials.get(name) or bpy.data.materials.new(name))
    return names


# --- Houdini attributes and groups on the result --------------------------------------

DOMAINS = {"point": "POINT", "prim": "FACE", "vertex": "CORNER", "edge": "EDGE"}
FLOAT_TYPES = {1: "FLOAT", 2: "FLOAT2", 3: "FLOAT_VECTOR", 4: "FLOAT4"}
INT_TYPES = {1: "INT", 2: "INT32_2D"}
WRITE_FIELDS = {"FLOAT": "value", "INT": "value", "FLOAT2": "vector", "FLOAT_VECTOR": "vector",
                "FLOAT4": "vector", "FLOAT_COLOR": "color", "INT32_2D": "value"}


def _blender_type(description):
    """(Blender data type, number of components written) of a Houdini attribute.

    Chosen so that sending the attribute back to Houdini gives it again: a
    color becomes a color attribute, a texture coordinate a UV map (corners) or
    a 2D vector, anything else follows its size."""
    size = description["size"]
    if description["type"] != "float" and size <= 2:
        return INT_TYPES[size], size
    qualifier = description.get("qualifier")
    if qualifier == "Color" and size in (3, 4):
        return "FLOAT_COLOR", 4
    if qualifier == "Texture Coord" and size in (2, 3):
        return "FLOAT2", 2
    return FLOAT_TYPES.get(size), size


# Blender names an attribute from Houdini must not overwrite.
RESERVED = {"position", "sharp_face", "sharp_edge", "material_index", "custom_normal", "uv", "Cd", "UVMap"}
WRITTEN_KEY = "houdini_bridge_extras"


def _blender_name(name, taken, reserved=RESERVED):
    if name in reserved or name.startswith("."):
        name = "houdini_" + name
    while name in taken:
        name += "_"
    taken.add(name)
    return name


def _converted(description, values):
    """(Blender data type, values as Blender stores them) of one Houdini attribute, or None
    when Blender has no type for it. A 3-component float changes axes like P (a velocity,
    a normal, a rest position…): VEX attributes carry no qualifier, so only declared
    colors are kept as they are; a 3-component color gets an alpha of 1. Blender has no
    integer of 3 or 4 components: one becomes a float vector, its axes left as they are
    (an index, not a direction)."""
    data_type, components = _blender_type(description)
    if values is None or data_type is None:
        return None
    size = description["size"]
    values = values.reshape(-1, size)
    if data_type == "FLOAT_VECTOR" and description["type"] == "float":
        values = houdini_to_blender(values)
    elif data_type == "FLOAT_COLOR" and size == 3:
        values = np.concatenate([values, np.ones((len(values), 1), values.dtype)], 1)
    return data_type, values[:, :components]


def _store(data, name, data_type, domain, values):
    """Write converted values into the attribute `name` of a datablock (made, or made again
    with the right type). Returns it, or None when Blender refuses it or the counts differ."""
    attribute = ensure_attribute(data, name, data_type, domain)
    if attribute is None or len(attribute.data) != len(values):
        return None
    dtype = np.int32 if data_type.startswith("INT") else np.float32
    attribute.data.foreach_set(WRITE_FIELDS[data_type], np.ascontiguousarray(values, dtype).ravel())
    return attribute


def _edge_indices(mesh, pairs):
    """Blender edge indices of point pairs (pairs whose edge does not exist are dropped)."""
    edges = np.empty(len(mesh.edges) * 2, np.int64)
    mesh.edges.foreach_get("vertices", edges)
    edges = np.sort(edges.reshape(-1, 2), axis=1)
    count = len(mesh.vertices) + 1
    keys = edges[:, 0] * count + edges[:, 1]
    order = np.argsort(keys)
    pairs = np.sort(pairs.reshape(-1, 2).astype(np.int64), axis=1)
    wanted = pairs[:, 0] * count + pairs[:, 1]
    positions = np.searchsorted(keys[order], wanted)
    positions = np.clip(positions, 0, len(order) - 1)
    found = keys[order][positions] == wanted
    return order[positions[found]]


def write_extras(mesh, header, arrays, reversal):
    """Write the Houdini attributes and groups as Blender attributes.

    Groups become boolean attributes on points, faces or edges; attributes keep
    their name, domain and size. Attributes written by a previous cook and
    absent from this one are removed. Returns their description, for the UI."""
    previous = set(mesh.get(WRITTEN_KEY, []))
    taken = set()
    written = []

    for description in header.get("attributes", []):
        converted = _converted(description, arrays.get(description["block"]))
        if converted is None:
            continue
        data_type, values = converted
        if description["class"] == "vertex":
            values = values[reversal]  # the corners in Blender's winding
        name = _blender_name(description["name"], taken)
        attribute = _store(mesh, name, data_type, DOMAINS[description["class"]], values)
        if attribute is None:
            continue
        written.append({"name": name, "source": description["name"], "kind": "attribute",
                        "domain": attribute.domain, "size": description["size"], "type": description["type"]})

    for description in header.get("groups", []):
        members = arrays.get(description["block"])
        if members is None or not len(members):
            continue  # an empty group exists in Houdini, but holds nothing worth keeping here
        domain = DOMAINS[description["class"]]
        name = _blender_name(description["name"], taken)
        attribute = ensure_attribute(mesh, name, "BOOLEAN", domain)
        if attribute is None:
            continue
        mask = np.zeros(len(attribute.data), bool)
        indices = _edge_indices(mesh, members) if domain == "EDGE" else members
        mask[indices[indices < len(mask)]] = True
        if not mask.any():
            mesh.attributes.remove(attribute)
            continue
        attribute.data.foreach_set("value", mask)
        written.append({"name": name, "source": description["name"], "kind": "group",
                        "domain": domain, "size": 1, "type": "bool", "count": int(mask.sum())})

    for name in previous - {item["name"] for item in written}:
        stale = mesh.attributes.get(name)
        if stale is not None:
            mesh.attributes.remove(stale)
    mesh[WRITTEN_KEY] = [item["name"] for item in written]
    return written


def write_attributes(data, descriptions, arrays, domain, reserved=()):
    """Write Houdini attributes on one domain of a point cloud or curves datablock.

    The same conversion as on meshes, with no winding to follow; names in `reserved` get
    a houdini_ prefix. Attributes written by a previous call and absent now are removed."""
    previous = set(data.get(WRITTEN_KEY + "_" + domain, []))
    taken = set()
    written = []
    reserved = {"position"} | set(reserved)  # not the mesh names: Cd, uv are plain attributes here
    for description in descriptions:
        converted = _converted(description, arrays.get(description["block"]))
        if converted is None:
            continue
        data_type, values = converted
        name = _blender_name(description["name"], taken, reserved)
        if _store(data, name, data_type, domain, values) is None:
            continue
        written.append(name)
    for name in previous - set(written):
        stale = data.attributes.get(name)
        if stale is not None:
            data.attributes.remove(stale)
    data[WRITTEN_KEY + "_" + domain] = written
    return written


def ensure_attribute(mesh, name, data_type, domain):
    attribute = mesh.attributes.get(name)
    if attribute is not None and (attribute.data_type != data_type or attribute.domain != domain):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        try:
            attribute = mesh.attributes.new(name, data_type, domain)
        except RuntimeError:
            return None
    return attribute
