"""What the streams share: the Stream record, the node group helpers, removing a
holder, materials.

Next to the mesh (the holder), each stream lives on its own hidden object,
linked to no collection, in Blender's native type. The modifier's node group
shows them through a "Houdini Streams" group node placed between the mesh and
the Group Output (Object Info of each stream -> Join Geometry). It is only
added when a result first holds a stream, so plain mesh HDAs keep a minimal
node group.
"""

from collections.abc import Callable
from dataclasses import dataclass

import bpy
import numpy as np

from .. import mesh_io, parameters

STREAMS_NODE = parameters.STREAMS_NODE


def _nothing(settings):
    pass


@dataclass(frozen=True)
class Stream:
    """One native stream on the Blender side: its names and its functions, with no state
    (that lives in the modifier's settings). Each stream's file defines one, STREAM.
    `settings` is the modifier's HoudiniBridgeSettings throughout."""

    name: str  # its key in the cook answer and in streams_json (contract.STREAMS)
    holder: str  # the property of the settings that holds its hidden object
    new_data: Callable  # (name) -> a new empty datablock of the holder's type
    write: Callable  # (settings, holder, info, output) -> False when the server must send the result again
    found: Callable  # (info) -> what it counts in streams_json ({name: ...}, more keys if it needs)
    text: Callable  # (summary) -> its part of the status line, "" when it has nothing
    empty: Callable  # (settings): absent from a result, its holder is emptied (kept for the next cook)
    show: Callable  # (tree, info, row) -> the socket its Object Info node gives the Join of "Houdini Streams"
    apply: Callable  # (settings, owner, place): its object at Apply, made with place(holder, label, data, matrix)
    owned: tuple = ()  # other properties of the settings it owns: a copied modifier forgets them
    release: Callable = _nothing  # (settings): what it removes besides its holder when the modifier is gone


def as_geometry(tree, info, row):
    """How most holders enter the "Houdini Streams" group: their geometry as it is."""
    info.inputs["As Instance"].default_value = False
    return info.outputs["Geometry"]


def geometry_tree(name):
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    return tree


def remove_object(obj):
    data = obj.data
    bpy.data.objects.remove(obj)
    if data is not None and data.users == 0:
        remove_data(data)


def remove_data(data):
    collection = {"MESH": bpy.data.meshes, "POINTCLOUD": bpy.data.pointclouds,
                  "CURVES": bpy.data.hair_curves, "VOLUME": bpy.data.volumes}.get(data.id_type)
    if collection is not None:
        collection.remove(data)


def materials(data, paths, indices=None, domain="POINT"):
    """Set Houdini material paths as the datablock's materials (as for the mesh), with a
    material_index per element when there are several. As the mesh, a stream without
    materials loses its material_index."""
    names = mesh_io.set_materials(data, paths)
    if indices is not None and names:
        mesh_io.ensure_attribute(data, "material_index", "INT", domain).data.foreach_set(
            "value", indices.astype(np.int32))
    elif hasattr(data, "attributes") and "material_index" in data.attributes:  # a Volume has no attributes
        data.attributes.remove(data.attributes["material_index"])


def remove_streams_node(tree):
    """Take the streams node out, linking what fed it straight to the output."""
    node = tree.nodes.get(STREAMS_NODE)
    if node is None:
        return
    incoming = [node_link.from_socket for node_link in tree.links if node_link.to_node == node]
    group_output = next(n for n in tree.nodes if n.bl_idname == "NodeGroupOutput")
    tree.nodes.remove(node)
    if incoming:
        tree.links.new(incoming[0], group_output.inputs[0])
