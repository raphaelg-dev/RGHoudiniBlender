"""The curve stream: open curves as a Curves object (hair curves), radius from Houdini's width."""

import bpy
import numpy as np

from ....shared import contract
from .. import mesh_io, preferences
from . import common


def new_data(name):
    return bpy.data.hair_curves.new(name)


def write(settings, holder, info, output):
    counts = output["curve_counts"].astype(np.int32)
    curves = holder.data
    sizes = np.empty(len(curves.curves), np.int32)
    if len(sizes):
        offsets = np.empty(len(curves.curves) + 1, np.int32)
        curves.curve_offset_data.foreach_get("value", offsets)
        sizes = np.diff(offsets)
    if not np.array_equal(sizes, counts):
        # Another layout: a new datablock is simpler than resizing in place.
        curves = bpy.data.hair_curves.new(holder.data.name)
        curves.add_curves(counts.tolist())
        old = holder.data
        holder.data = curves
        if old.users == 0:
            bpy.data.hair_curves.remove(old)
    curves.position_data.foreach_set("vector", mesh_io.houdini_to_blender(output["curve_P"].reshape(-1, 3)).ravel())
    mesh_io.write_attributes(curves, info["point_attributes"], output, "POINT", reserved={"radius"})
    mesh_io.write_attributes(curves, info["curve_attributes"], output, "CURVE", reserved={"curve_type", "resolution"})
    # Houdini curves are as wide as their width attribute (a diameter).
    width = next((d for d in info["point_attributes"] if d["name"] == "width" and d["size"] == 1), None)
    point_count = len(output["curve_P"]) // 3
    default = preferences.get().curve_radius
    radius = output[width["block"]] * 0.5 if width else np.full(point_count, default, np.float32)
    mesh_io.ensure_attribute(curves, "radius", "FLOAT", "POINT").data.foreach_set("value", radius.astype(np.float32))
    common.materials(curves, info.get("materials"), output.get("curve_material"), "CURVE")
    curves.update_tag()
    return True


def found(info):
    return {contract.CURVES: info["count"]}


def text(summary):
    return "%d curves" % summary[contract.CURVES] if summary.get(contract.CURVES) else ""


def empty(settings):
    holder = settings.curves_holder
    if holder is not None and len(holder.data.curves):
        old = holder.data
        holder.data = bpy.data.hair_curves.new(old.name)
        if old.users == 0:
            bpy.data.hair_curves.remove(old)


def show(tree, info, row):
    # Curves brought into a mesh object as geometry are not rendered
    # (Blender 5.2); as an instance they are.
    info.inputs["As Instance"].default_value = True
    return info.outputs["Geometry"]


def apply(settings, owner, place):
    if settings.curves_holder is not None:
        place(None, contract.CURVES, settings.curves_holder.data.copy())


STREAM = common.Stream(name=contract.CURVES, holder="curves_holder", new_data=new_data, write=write, found=found,
                       text=text, empty=empty, show=show, apply=apply)
