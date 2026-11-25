"""Viewport visualizers: an attribute of a Houdini result, on the object.

Like Houdini's visualizers, a scalar attribute is a color ramp (blue → red) over its
range, and a 3-component attribute on points or faces is drawn as arrows. The drawing
reads the hidden holder mesh (the cooked result, in the owner's local space) and is cached
until the next cook.
"""

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from ...core import properties
from .. import cache

RAMP = np.array([
    (0.05, 0.25, 0.85),
    (0.10, 0.75, 0.90),
    (0.20, 0.80, 0.30),
    (0.95, 0.85, 0.15),
    (0.90, 0.20, 0.15),
], np.float32)
ARROW_COLOR = (1.0, 0.85, 0.2, 1.0)
_cache = {}


def _ramp(values):
    """Colors for values over their range; returns (rgba, low, high)."""
    # The 2nd–98th percentiles: a few extreme values (curvature on sharp corners)
    # would otherwise squeeze everything else into one color.
    low = float(np.percentile(values, 2)) if len(values) else 0.0
    high = float(np.percentile(values, 98)) if len(values) else 0.0
    span = high - low if high > low else 1.0
    t = np.clip((values - low) / span, 0.0, 1.0) * (len(RAMP) - 1)
    rgb = np.stack([np.interp(t, np.arange(len(RAMP)), RAMP[:, c]) for c in range(3)], 1)
    rgba = np.concatenate([rgb, np.full((len(values), 1), 0.85, np.float32)], 1)
    return rgba.astype(np.float32), low, high


def _mesh_arrays(mesh):
    positions = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get("co", positions)
    positions = positions.reshape(-1, 3)
    mesh.calc_loop_triangles()
    triangles = np.empty(len(mesh.loop_triangles) * 3, np.int32)
    mesh.loop_triangles.foreach_get("loops", triangles)
    polygons = np.empty(len(mesh.loop_triangles), np.int32)
    mesh.loop_triangles.foreach_get("polygon_index", polygons)
    loop_points = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get("vertex_index", loop_points)
    normals = np.empty(len(mesh.loops) * 3, np.float32)
    mesh.corner_normals.foreach_get("vector", normals)
    return positions, triangles.reshape(-1, 3), polygons, loop_points, normals.reshape(-1, 3)


def _attribute_values(attribute):
    size = len(attribute.data)
    if attribute.data_type in ("FLOAT", "INT", "BOOLEAN"):
        values = np.empty(size, np.float32 if attribute.data_type == "FLOAT" else np.int32)
        attribute.data.foreach_get("value", values)
        return values.astype(np.float32).reshape(-1, 1)
    components = {"FLOAT2": 2, "INT32_2D": 2, "FLOAT_VECTOR": 3, "FLOAT4": 4,
                  "FLOAT_COLOR": 4}.get(attribute.data_type, 0)
    if not components:
        return None
    field = {"FLOAT2": "vector", "INT32_2D": "value", "FLOAT_VECTOR": "vector", "FLOAT4": "vector",
             "FLOAT_COLOR": "color"}[attribute.data_type]
    values = np.empty(size * components, np.float32 if attribute.data_type != "INT32_2D" else np.int32)
    attribute.data.foreach_get(field, values)
    return values.astype(np.float32).reshape(-1, components)


def build(mesh, output):
    """(batches drawing one output of a mesh, legend text)."""
    attribute = mesh.attributes.get(output["name"])
    if attribute is None or not len(mesh.vertices):
        return [], ""
    positions, triangles, polygons, loop_points, normals = _mesh_arrays(mesh)
    size = float(np.linalg.norm(positions.max(0) - positions.min(0))) or 1.0
    # Lifted along the normals, so the overlay does not fight with the surface.
    lifted = positions[loop_points] + normals * size * 0.0015
    values = _attribute_values(attribute)
    domain = attribute.domain
    batches = []

    if values.shape[1] == 3 and domain in ("POINT", "FACE"):
        if domain == "POINT":
            origins = positions
        else:
            centers = np.empty(len(mesh.polygons) * 3, np.float32)
            mesh.polygons.foreach_get("center", centers)
            origins = centers.reshape(-1, 3)
        lengths = np.linalg.norm(values, axis=1)
        longest = float(lengths.max()) if len(lengths) else 0.0
        scale = size * 0.04 / longest if longest > 0 else 0.0
        ends = origins + values * scale
        lines = np.stack([origins, ends], 1).reshape(-1, 3)
        batches.append(("LINES", {"pos": lines}, ARROW_COLOR))
        return batches, "%s · length %.3g → %.3g" % (output["name"], float(lengths.min()), longest)

    scalars = values[:, 0] if values.shape[1] == 1 else np.linalg.norm(values, axis=1)
    colors, low, high = _ramp(scalars)
    if domain == "POINT":
        corner_colors = colors[loop_points]
    elif domain == "FACE":
        totals = np.empty(len(mesh.polygons), np.int32)
        mesh.polygons.foreach_get("loop_total", totals)
        corner_colors = colors[np.repeat(np.arange(len(totals)), totals)]
    elif domain == "CORNER":
        corner_colors = colors
    else:
        return [], ""
    chosen = triangles.ravel()
    batches.append(("SMOOTH", {"pos": lifted[chosen], "color": corner_colors[chosen]}, None))
    return batches, "%s · %.3g → %.3g (2–98%%)" % (output["name"], low, high)


def _cached(target):
    settings = target.settings
    output = next((o for o in properties.outputs(settings) if o["name"] == settings.visualize), None)
    if output is None:
        return [], ""
    # In cache mode, the frame being played rather than the last live result.
    mesh = cache.shown_mesh(settings, bpy.context.scene.frame_current)
    if mesh is None:
        return [], ""
    key = (target.key, settings.visualize, settings.timings, settings.status, mesh.name)
    entry = _cache.get(target.key)
    if entry is None or entry[0] != key:
        entry = (key,) + build(mesh, output)
        _cache[target.key] = entry
    return entry[1], entry[2]


def draw(target, region):
    batches, _ = _cached(target)
    if not batches:
        return
    gpu.matrix.push()
    gpu.matrix.multiply_matrix(target.owner.matrix_world)
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    for kind, data, color in batches:
        if kind == "SMOOTH":
            shader = gpu.shader.from_builtin("SMOOTH_COLOR")
            batch_for_shader(shader, "TRIS", data).draw(shader)
        elif kind == "LINES":
            shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
            shader.uniform_float("viewportSize", (region.width, region.height))
            shader.uniform_float("lineWidth", 3.0)
            shader.uniform_float("color", color)
            batch_for_shader(shader, "LINES", data).draw(shader)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")
    gpu.matrix.pop()


def legend(target):
    return _cached(target)[1]
