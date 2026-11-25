"""The point stream: the loose points as a point cloud, radius from pscale."""

import bpy
import numpy as np

from ....shared import contract
from .. import mesh_io, preferences
from . import common


def new_data(name):
    return bpy.data.pointclouds.new(name)


def write(settings, holder, info, output):
    cloud = holder.data
    count = info["count"]
    if len(cloud.points) != count:
        cloud.resize(count)
    cloud.attributes["position"].data.foreach_set(
        "vector", mesh_io.houdini_to_blender(output["points_P"].reshape(-1, 3)).ravel())
    mesh_io.write_attributes(cloud, info["attributes"], output, "POINT", reserved={"radius"})
    scale = next((d for d in info["attributes"] if d["name"] == "pscale" and d["size"] == 1), None)
    default = preferences.get().point_radius
    radius = output[scale["block"]] if scale else np.full(count, default, np.float32)
    mesh_io.ensure_attribute(cloud, "radius", "FLOAT", "POINT").data.foreach_set("value", radius.astype(np.float32))
    common.materials(cloud, info.get("materials"), output.get("points_material"), "POINT")
    cloud.update_tag()
    return True


def found(info):
    return {contract.POINTS: info["count"]}


def text(summary):
    return "%d points" % summary[contract.POINTS] if summary.get(contract.POINTS) else ""


def empty(settings):
    holder = settings.points_holder
    if holder is not None and len(holder.data.points):
        holder.data.resize(0)
        holder.data.update_tag()


def apply(settings, owner, place):
    if settings.points_holder is not None:
        place(None, contract.POINTS, settings.points_holder.data.copy())


STREAM = common.Stream(name=contract.POINTS, holder="points_holder", new_data=new_data, write=write, found=found,
                       text=text, empty=empty, show=common.as_geometry, apply=apply)
