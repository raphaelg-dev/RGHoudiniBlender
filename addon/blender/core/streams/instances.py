"""The instance stream: a point cloud carrying one FLOAT4X4 `transform` and one
`proto` index per instance, turned into real instances by its own Geometry
Nodes modifier (Collection Info of the prototypes -> Instance on Points -> Set
Instance Transform). Each shape is sent once; the matrices arrive in Blender's
convention, so writing them is a plain copy.
"""

import bpy
import numpy as np

from ....shared import contract
from .. import mesh_io, preferences
from . import common

INSTANCER_MODIFIER = "Houdini Instancer"
KEY_PROPERTY = "houdini_bridge_key"


def _instancer_tree(settings):
    """Prototypes on the instance points; one point each in the viewport when asked."""
    tree = common.geometry_tree("Houdini Instancer · %s" % settings.holder.name)
    nodes, links = tree.nodes, tree.links
    group_input = nodes.new("NodeGroupInput")
    shapes = nodes.new("GeometryNodeCollectionInfo")
    shapes.name = "Prototypes"
    shapes.transform_space = "ORIGINAL"
    shapes.inputs["Collection"].default_value = settings.prototypes
    shapes.inputs["Separate Children"].default_value = True
    shapes.inputs["Reset Children"].default_value = True
    on_points = nodes.new("GeometryNodeInstanceOnPoints")
    on_points.inputs["Pick Instance"].default_value = True
    proto = nodes.new("GeometryNodeInputNamedAttribute")
    proto.data_type = "INT"
    proto.inputs["Name"].default_value = "proto"
    transform = nodes.new("GeometryNodeInputNamedAttribute")
    transform.data_type = "FLOAT4X4"
    transform.inputs["Name"].default_value = "transform"
    set_transform = nodes.new("GeometryNodeSetInstanceTransform")
    links.new(group_input.outputs[0], on_points.inputs["Points"])
    links.new(shapes.outputs[0], on_points.inputs["Instance"])
    links.new(proto.outputs["Attribute"], on_points.inputs["Instance Index"])
    links.new(on_points.outputs[0], set_transform.inputs["Instances"])
    links.new(transform.outputs["Attribute"], set_transform.inputs["Transform"])
    # Drawing a million instances takes Blender over 100 ms; their points, 1 ms.
    is_viewport = nodes.new("GeometryNodeIsViewport")
    proxy = nodes.new("FunctionNodeBooleanMath")
    proxy.name = "Points in Viewport"
    proxy.operation = "AND"
    links.new(is_viewport.outputs[0], proxy.inputs[0])
    switch = nodes.new("GeometryNodeSwitch")
    switch.input_type = "GEOMETRY"
    links.new(proxy.outputs[0], switch.inputs["Switch"])
    links.new(set_transform.outputs[0], switch.inputs["False"])
    links.new(group_input.outputs[0], switch.inputs["True"])
    group_output = nodes.new("NodeGroupOutput")
    links.new(switch.outputs[0], group_output.inputs[0])
    for column, node in enumerate((group_input, on_points, set_transform, switch, group_output)):
        node.location = (column * 220 - 500, 0)
    shapes.location = (-500, -200)
    proto.location = (-500, -380)
    transform.location = (-280, -300)
    is_viewport.location = (-60, 260)
    proxy.location = (160, 260)
    tree.description = "RG Houdini Bridge: the instances of the Houdini result"
    return tree


def apply_instance_display(settings):
    holder = settings.instances_holder
    modifier = holder.modifiers.get(INSTANCER_MODIFIER) if holder else None
    if modifier is None or modifier.node_group is None:
        return
    count = len(holder.data.points) if holder.data else 0
    threshold = preferences.get().points_above
    points = settings.instance_display == "POINTS" or (settings.instance_display == "AUTO" and count > threshold)
    proxy = modifier.node_group.nodes["Points in Viewport"]
    if proxy.inputs[1].default_value != points:
        proxy.inputs[1].default_value = points


def _sub_arrays(output, prefix):
    return {name[len(prefix):]: values for name, values in output.items() if name.startswith(prefix)}


def _write_prototypes(settings, info, output):
    """Bring the prototype objects in line with the instance keys. Returns False when a key
    has no prototype on either side (it must be sent again)."""
    if settings.prototypes is None:
        settings.prototypes = bpy.data.collections.new("Houdini Prototypes · %s" % settings.holder.name)
    collection = settings.prototypes
    by_key = {obj.get(KEY_PROPERTY): obj for obj in collection.objects}
    for prototype in info["prototypes"]:
        key = prototype["key"]
        mesh = bpy.data.meshes.new("%s · shape" % settings.holder.name)
        mesh_io.write_mesh(mesh, prototype, _sub_arrays(output, prototype["prefix"]))
        obj = by_key.get(key)
        if obj is None:
            obj = bpy.data.objects.new(mesh.name, mesh)
            obj[KEY_PROPERTY] = key
            obj.houdini_bridge_result = True
            collection.objects.link(obj)
            by_key[key] = obj
        else:
            old = obj.data
            obj.data = mesh
            if old is not None and old.users == 0:
                bpy.data.meshes.remove(old)
    keys = info["keys"]
    complete = all(key in by_key for key in keys)
    wanted = set(keys)
    for key, obj in list(by_key.items()):
        if key not in wanted:
            common.remove_object(obj)
            del by_key[key]
    # Collection Info gives its children in name order: the name carries the
    # slot the instances refer to (proto). Two passes avoid clashes.
    for key in keys:
        obj = by_key.get(key)
        if obj is not None:
            obj.name = "~%s" % obj.name
    for slot, key in enumerate(keys):
        obj = by_key.get(key)
        if obj is not None:
            obj.name = "%s · shape %05d" % (settings.holder.name, slot)
            obj.data.name = obj.name
    return complete


def new_data(name):
    return bpy.data.pointclouds.new(name)


def write(settings, holder, info, output):
    if not _write_prototypes(settings, info, output):
        return False
    cloud = holder.data
    count = info["count"]
    if len(cloud.points) != count:
        cloud.resize(count)
    xforms = output["inst_xform"].reshape(-1, 16)
    # Column-major matrices: the translation is the last column.
    cloud.attributes["position"].data.foreach_set("vector", np.ascontiguousarray(xforms[:, 12:15]).ravel())
    transform = mesh_io.ensure_attribute(cloud, "transform", "FLOAT4X4", "POINT")
    transform.data.foreach_set("value", xforms.ravel())
    proto = mesh_io.ensure_attribute(cloud, "proto", "INT", "POINT")
    proto.data.foreach_set("value", output["inst_proto"].astype(np.int32))
    mesh_io.write_attributes(cloud, info["attributes"], output, "POINT", reserved={"transform", "proto", "radius"})
    cloud.update_tag()
    modifier = holder.modifiers.get(INSTANCER_MODIFIER)
    if modifier is None:
        modifier = holder.modifiers.new(INSTANCER_MODIFIER, "NODES")
    if modifier.node_group is None:
        modifier.node_group = _instancer_tree(settings)
    apply_instance_display(settings)
    return True


def found(info):
    return {contract.INSTANCES: info["count"], "shapes": len(info["keys"])}


def text(summary):
    if not summary.get(contract.INSTANCES):
        return ""
    return "%d instances (%d shapes)" % (summary[contract.INSTANCES], summary.get("shapes", 0))


def empty(settings):
    holder = settings.instances_holder
    if holder is not None and len(holder.data.points):
        holder.data.resize(0)
        holder.data.update_tag()
    if settings.prototypes is not None:
        for obj in list(settings.prototypes.objects):
            common.remove_object(obj)


def apply(settings, owner, place):
    holder = settings.instances_holder
    if holder is not None and settings.prototypes is not None:
        obj = place(holder, contract.INSTANCES, holder.data.copy())
        # The shapes now belong to the applied object: the modifier's release must leave them.
        settings.prototypes.name = "%s · shapes" % obj.name
        settings.prototypes = None


def release(settings):
    """Remove the shapes with the modifier (unless Apply handed them over)."""
    if settings.prototypes is not None:
        collection = settings.prototypes
        settings.prototypes = None
        bpy.data.collections.remove(collection)


STREAM = common.Stream(name=contract.INSTANCES, holder="instances_holder", owned=("prototypes",), new_data=new_data,
                       write=write, found=found, text=text, empty=empty, show=common.as_geometry, apply=apply,
                       release=release)
