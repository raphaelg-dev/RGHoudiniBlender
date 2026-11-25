"""Native streams of a Houdini result, on the Blender side: instances, open curves,
loose points, volumes.

STREAMS is the registry: the Stream of each file (instances.py, curves.py,
points.py, volumes.py), in the order of contract.STREAMS; common.py defines
Stream and what the streams share. What only one stream does (the instances in
the viewport, the volume files and their save handler) is a function of its
file, imported below.

The modifier's node group shows the holders through the "Houdini Streams" group
node (_streams_tree): one Object Info per stream, in the registry's order, then
Join Geometry.
"""

import json

import bpy
from mathutils import Matrix

from .. import stored
from . import common, curves, instances, points, volumes
from .common import remove_data
from .instances import INSTANCER_MODIFIER, apply_instance_display
from .volumes import (
    applied_volume_mode,
    is_waiting,
    on_save,
    register,
    remove_orphan_volume_files,
    unregister,
    volume_dir,
    volume_file_gone,
)

# The registry: one Stream per native stream, in the order of contract.STREAMS
# (tests/unit/test_stream_registry.py checks it).
STREAMS = (instances.STREAM, curves.STREAM, points.STREAM, volumes.STREAM)
STREAM_NAMES = tuple(stream.name for stream in STREAMS)
STREAMS_NODE = common.STREAMS_NODE

__all__ = [
    # the registry
    "STREAMS", "STREAM_NAMES",
    # a result
    "write", "clear", "summary", "describe",
    # the instances in the viewport
    "apply_instance_display", "INSTANCER_MODIFIER",
    # the modifier applied, removed, duplicated
    "apply_to_objects", "release", "detach", "remove_data",
    # volume files
    "volume_dir", "is_waiting", "applied_volume_mode", "volume_file_gone", "on_save", "remove_orphan_volume_files",
    # registration
    "register", "unregister",
]


def summary(settings):
    """{stream: count} of the last result."""
    return stored.parse(settings.streams_json, {})


def describe(settings):
    """A short text of the streams, for the status line."""
    found = summary(settings)
    parts = [text for text in (stream.text(found) for stream in STREAMS) if text]
    return " · ".join(parts)


# --- node groups ----------------------------------------------------------------------

# Raised when the layout of _streams_tree changes: _streams_node rebuilds a group of an
# earlier layout, which a file saved before 1.0 may hold.
STREAMS_TREE_VERSION = 3


def _streams_tree(settings):
    """Mesh in, mesh joined with every stream out."""
    tree = common.geometry_tree("Houdini Streams · %s" % settings.holder.name)
    nodes, links = tree.nodes, tree.links
    group_input = nodes.new("NodeGroupInput")
    group_input.location = (-600, 200)
    join = nodes.new("GeometryNodeJoinGeometry")
    join.location = (200, 0)
    group_output = nodes.new("NodeGroupOutput")
    group_output.location = (400, 0)
    links.new(group_input.outputs[0], join.inputs[0])
    links.new(join.outputs[0], group_output.inputs[0])
    for row, stream in enumerate(STREAMS):
        info = nodes.new("GeometryNodeObjectInfo")
        info.name = info.label = stream.name
        info.transform_space = "ORIGINAL"
        info.location = (-600, -200 * row)
        links.new(stream.show(tree, info, row), join.inputs[0])
    tree["houdini_bridge_version"] = STREAMS_TREE_VERSION
    tree.description = "RG Houdini Bridge: the instances, curves, points and volume of the Houdini result"
    return tree


def _streams_node(settings, create):
    """The streams group node of the modifier's node group (added when `create`)."""
    tree = settings.id_data
    node = tree.nodes.get(STREAMS_NODE)
    if node is not None and node.node_tree is not None and node.node_tree.get(
            "houdini_bridge_version", 1) < STREAMS_TREE_VERSION:
        # An earlier layout saved in the file: rebuild it with the same holders.
        old = node.node_tree
        node.node_tree = _streams_tree(settings)
        for stream in STREAMS:
            node.node_tree.nodes[stream.name].inputs["Object"].default_value = getattr(settings, stream.holder)
        if old.users == 0:
            bpy.data.node_groups.remove(old)
    if node is not None or not create:
        return node
    node = tree.nodes.new("GeometryNodeGroup")
    node.name = node.label = STREAMS_NODE
    node.location = (-50, -150)
    node.node_tree = _streams_tree(settings)
    group_output = next(n for n in tree.nodes if n.bl_idname == "NodeGroupOutput")
    # Between whatever reaches the output (the mesh, through the usage chain) and the output.
    incoming = [node_link.from_socket for node_link in tree.links if node_link.to_node == group_output]
    if incoming:
        tree.links.new(incoming[0], node.inputs[0])
    tree.links.new(node.outputs[0], group_output.inputs[0])
    for stream in STREAMS:
        node.node_tree.nodes[stream.name].inputs["Object"].default_value = getattr(settings, stream.holder)
    return node


# --- writing a result -----------------------------------------------------------------


def _holder(settings, stream):
    """The stream's hidden object, made when it has none, and shown by the streams node."""
    holder = getattr(settings, stream.holder)
    if holder is None:
        data = stream.new_data("%s · %s" % (settings.holder.name, stream.name))
        holder = bpy.data.objects.new("%s · %s" % (settings.holder.name, stream.name), data)
        holder.houdini_bridge_result = True
        setattr(settings, stream.holder, holder)
    info = _streams_node(settings, create=True).node_tree.nodes[stream.name]
    if info.inputs["Object"].default_value != holder:
        info.inputs["Object"].default_value = holder
    return holder


def write(settings, answer, output):
    """Write every stream of a result; False when the server must send the prototypes
    again (Blender lost some)."""
    found = {}
    complete = True
    for stream in STREAMS:
        if stream.name in answer:
            if not stream.write(settings, _holder(settings, stream), answer[stream.name], output):
                complete = False
            found.update(stream.found(answer[stream.name]))
        else:
            stream.empty(settings)
    text = json.dumps(found) if found else ""
    if settings.streams_json != text:
        settings.streams_json = text
    return complete


# --- applying: the streams become objects of their own --------------------------------


def apply_to_objects(settings, owner):
    """Turn each stream into an object of Blender's own type next to the owner, with no
    link to Houdini left, and return them: a point cloud instancing the shapes, a Curves
    object, a point cloud, a Volume object reading a kept copy of the .vdb."""
    made = []

    def place(holder, label, data, matrix=None):
        obj = bpy.data.objects.new("%s · %s" % (owner.name, label), data)
        for collection in owner.users_collection:
            collection.objects.link(obj)
        obj.matrix_world = owner.matrix_world @ (matrix if matrix is not None else Matrix.Identity(4))
        if holder is not None:
            for modifier in holder.modifiers:
                copy = obj.modifiers.new(modifier.name, modifier.type)
                if modifier.type == "NODES":
                    copy.node_group = modifier.node_group.copy()
        made.append(obj)
        return obj

    found = summary(settings)
    for stream in STREAMS:
        if found.get(stream.name):
            stream.apply(settings, owner, place)
    return made


def clear(settings):
    """Empty every stream (the cache shows realized frames instead)."""
    for stream in STREAMS:
        stream.empty(settings)
    settings.streams_json = ""


def release(settings):
    """Remove the stream holders, the prototypes and the node groups of a modifier that is gone."""
    clear(settings)
    for stream in STREAMS:
        holder = getattr(settings, stream.holder)
        setattr(settings, stream.holder, None)
        if holder is None:
            continue
        for modifier in holder.modifiers:
            if modifier.type == "NODES" and modifier.node_group is not None and modifier.node_group.users <= 1:
                group = modifier.node_group
                modifier.node_group = None
                bpy.data.node_groups.remove(group)
        common.remove_object(holder)
    for stream in STREAMS:
        stream.release(settings)
    node = settings.id_data.nodes.get(STREAMS_NODE)
    group = node.node_tree if node is not None else None
    common.remove_streams_node(settings.id_data)
    if group is not None and group.users == 0:
        bpy.data.node_groups.remove(group)


def detach(settings):
    """Forget the streams a copied modifier (duplicated object) shares with the original;
    its next cook makes its own."""
    for stream in STREAMS:
        setattr(settings, stream.holder, None)
    for stream in STREAMS:
        for name in stream.owned:
            setattr(settings, name, None)
    common.remove_streams_node(settings.id_data)
    settings.streams_json = ""
