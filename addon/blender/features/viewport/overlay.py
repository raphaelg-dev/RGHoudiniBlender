"""Viewport feedback: shows that Houdini is cooking, on the object being cooked.

While a cook is in flight, the object gets a pulsing Houdini-orange bounding box and a
badge with the Houdini logo, the HDA name and the elapsed time; a summary sits at the
bottom of the viewport. A failed cook leaves a red badge. Also draws the visualizers
(visualize.py) and the legends.
"""

import math
import os
import time

import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ....shared import contract
from ...core import cook, feedback, link, paths, preferences, targets
from .. import groups
from . import visualize

HOUDINI_ORANGE = (1.0, 0.45, 0.1)
ERROR_RED = (0.95, 0.27, 0.22)
BACKGROUND = (0.07, 0.07, 0.07, 0.86)
BOX_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
FONT = 0
BADGE_ROW = 28  # pixels between two stacked badges, at a UI scale of 1

_handles = []
_logo_texture = None  # the Houdini logo, once loaded
_logo_asked = False  # whether Houdini was asked to render it


def _enabled():
    return preferences.get().show_feedback


def _legend_shown():
    return preferences.get().show_legend


def _shown_object(target):
    obj = target.owner
    if obj is None:
        return None
    try:
        return obj if obj.visible_get() else None
    except RuntimeError:  # not in the current view layer
        return None


def _world_corners(obj):
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]


def _rectangle(x, y, width, height, color):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(
        shader, "TRIS",
        {"pos": [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]},
        indices=[(0, 1, 2), (0, 2, 3)],
    )
    shader.uniform_float("color", color)
    batch.draw(shader)


def _load_logo():
    """Timer: load the Houdini logo, rendered by hython from the local install.

    Asked for once Houdini is running (it is never started just for this), and loaded here
    rather than in a draw callback, where Blender data cannot be added."""
    global _logo_texture, _logo_asked
    path = paths.houdini_logo()
    if not os.path.exists(path):
        if not _logo_asked and link.server.is_running():
            _logo_asked = True
            os.makedirs(os.path.dirname(path), exist_ok=True)
            cook.send_command("__logo__", contract.request(contract.LOGO, path=path), lambda answer, error: None)
        return 1.0
    try:
        image = bpy.data.images.load(path, check_existing=True)
        image.name = ".houdini_bridge_logo"
        # Drawn as is, not converted to linear ("Non-Color", named "Raw" by some configurations).
        names = {item.identifier for item in image.colorspace_settings.bl_rna.properties["name"].enum_items}
        image.colorspace_settings.name = "Non-Color" if "Non-Color" in names else "Raw"
        _logo_texture = gpu.texture.from_image(image)
    except (RuntimeError, SystemError) as error:  # no GPU in background mode
        feedback.log.warning("logo not loaded (%s)", error)
    return None


def _draw_logo(centre_x, centre_y, size):
    texture = _logo_texture
    if texture is None:
        return False
    half = size / 2
    corners = [(centre_x - half, centre_y - half), (centre_x + half, centre_y - half),
               (centre_x + half, centre_y + half), (centre_x - half, centre_y + half)]
    shader = gpu.shader.from_builtin("IMAGE")
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": corners, "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1))})
    shader.uniform_sampler("image", texture)
    batch.draw(shader)
    return True


def badge(centre_x, bottom, text, scale, text_color=(1.0, 1.0, 1.0)):
    """A dark label with the Houdini logo and a text, centred on `centre_x`."""
    blf.size(FONT, 12 * scale)
    text_width = blf.dimensions(FONT, text)[0]
    text_height = blf.dimensions(FONT, "Ag")[1]  # constant height, whatever the text
    padding = 6 * scale
    logo_size = 14 * scale
    has_logo = _logo_texture is not None
    icon_width = logo_size + 6 * scale if has_logo else 0
    width = text_width + 2 * padding + icon_width
    height = max(text_height, logo_size) + 2 * padding - 2 * scale
    left = centre_x - width / 2
    _rectangle(left, bottom, width, height, BACKGROUND)
    if has_logo:
        _draw_logo(left + padding + logo_size / 2, bottom + height / 2, logo_size)
    blf.position(FONT, left + padding + icon_width, bottom + (height - text_height) / 2 + 1 * scale, 0)
    blf.color(FONT, *text_color, 0.95)
    blf.draw(FONT, text)


def _anchor(region, view, obj, scale):
    """Screen point just above the object's projected bounding box."""
    points = [location_3d_to_region_2d(region, view, corner) for corner in _world_corners(obj)]
    points = [point for point in points if point is not None]
    if not points:
        return None
    x = sum(point.x for point in points) / len(points)
    y = max(point.y for point in points) + 10 * scale
    return x, min(y, region.height - 40 * scale)


def _visualized():
    """Targets with a visualizer on, whose object is shown."""
    found = []
    for target in targets.all_targets():
        if target.valid and target.settings.visualize not in ("", "NONE") and _shown_object(target) is not None:
            found.append(target)
    return found


def draw_boxes():
    """POST_VIEW: visualizers, and a pulsing bounding box around each object being cooked."""
    region = bpy.context.region
    for target in _visualized():
        try:
            visualize.draw(target, region)
        except Exception as error:  # a visualizer must never break the viewport
            feedback.log.warning("visualizer: %s", error)
    if not _enabled():
        return
    cooking, _ = cook.activity()
    if not cooking:
        return
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    pulse = 0.5 + 0.35 * math.sin(time.perf_counter() * 5.0)
    gpu.state.blend_set("ALPHA")
    for target, _, _ in cooking:
        obj = _shown_object(target)
        if obj is None:
            continue
        corners = _world_corners(obj)
        coordinates = [corners[index] for edge in BOX_EDGES for index in edge]
        batch = batch_for_shader(shader, "LINES", {"pos": coordinates})
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("lineWidth", 2.0)
        shader.uniform_float("color", (*HOUDINI_ORANGE, pulse))
        batch.draw(shader)
    gpu.state.blend_set("NONE")


def draw_labels():
    """POST_PIXEL: badges on the objects, and a summary at the bottom of the viewport."""
    feedback, legends = _enabled(), _legend_shown()
    if not feedback and not legends:
        return
    context = bpy.context
    region, view = context.region, context.region_data
    if region is None or view is None:
        return
    scale = context.preferences.system.ui_scale
    cooking, starting = cook.activity() if feedback else ([], False)
    gpu.state.blend_set("ALPHA")

    cooking_keys = set()
    for target, seconds, waiting in cooking:
        cooking_keys.add(target.key)
        obj = _shown_object(target)
        anchor = _anchor(region, view, obj, scale) if obj is not None else None
        if anchor is None:
            continue
        text = "%s   %.1f s" % (target.settings.label or target.settings.node_type, seconds)
        progress = cook.cooks.caching.get(target.key)
        if progress is not None:
            text += "   · caching frame %d of %d" % (min(progress[0] + 1, progress[1]), progress[1])
        if waiting:
            text += "   · newer change waiting"
        badge(anchor[0], anchor[1], text, scale)

    for target in _visualized() if legends else ():
        obj = _shown_object(target)
        anchor = _anchor(region, view, obj, scale)
        text = visualize.legend(target)
        if anchor is not None and text:
            # Above the cooking badge, when there is one.
            offset = BADGE_ROW * scale if target.key in cooking_keys else 0
            badge(anchor[0], anchor[1] + offset, text, scale)

    active = bpy.context.active_object
    text = groups.legend(active) if legends and active is not None and active.type == "MESH" else ""
    if text:
        target = targets.Target(groups.active_result(active))
        obj = _shown_object(target)
        anchor = _anchor(region, view, obj, scale) if obj is not None else None
        if anchor is not None:
            rows = (target.key in cooking_keys) + (target.settings.visualize not in ("", "NONE"))
            badge(anchor[0], anchor[1] + rows * BADGE_ROW * scale, text, scale)

    for target in targets.all_targets() if feedback else ():
        if target.key in cooking_keys or not target.valid or not target.settings.errors:
            continue
        obj = _shown_object(target)
        anchor = _anchor(region, view, obj, scale) if obj is not None else None
        if anchor is not None:
            first_line = target.settings.errors.splitlines()[0]
            badge(anchor[0], anchor[1], "Houdini error: %s" % first_line[:70], scale, text_color=ERROR_RED)

    if cooking or starting:
        if cooking:
            longest = max(seconds for _, seconds, _ in cooking)
            summary = "Houdini is cooking %d HDA%s   %.1f s" % (len(cooking), "s" if len(cooking) > 1 else "", longest)
        else:
            summary = "Starting Houdini…"
        badge(region.width / 2, 24 * scale, summary, scale)
    gpu.state.blend_set("NONE")


def register():
    _handles.append((bpy.types.SpaceView3D.draw_handler_add(draw_boxes, (), "WINDOW", "POST_VIEW"), "POST_VIEW"))
    _handles.append((bpy.types.SpaceView3D.draw_handler_add(draw_labels, (), "WINDOW", "POST_PIXEL"), "POST_PIXEL"))
    if not bpy.app.background:
        bpy.app.timers.register(_load_logo, first_interval=0.5, persistent=True)


def unregister():
    global _logo_texture, _logo_asked
    if bpy.app.timers.is_registered(_load_logo):
        bpy.app.timers.unregister(_load_logo)
    _logo_texture, _logo_asked = None, False
    while _handles:
        handle, _ = _handles.pop()
        bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
