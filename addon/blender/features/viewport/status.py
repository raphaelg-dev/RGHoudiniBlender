"""Houdini connection indicator in the 3D Viewport header, after the Object menu.

A "Houdini Bridge" button with the Houdini logo and a small colored dot in its corner
tells at a glance whether Houdini runs: grey stopped, amber starting, green connected,
Houdini orange cooking, red error. Clicking it opens a popover with the server controls.
Blender icons cannot be tinted, so the icons are small PNGs written into the extension's
user folder. The logo is rendered by hython from the local install (never shipped): until
Houdini has run once, the button shows the dot alone.
"""

import os

import bpy
import bpy.utils.previews
import numpy

from ...core import cook, feedback, icons, link, paths, preferences, trust

COLORS = {key: icons.COLORS[key] for key in ("STOPPED", "STARTING", "READY", "BUSY", "ERROR")}
LOGO_ICON_SIZE = 64
_previews = None
_logo_ready = False


def _read_logo(path):
    """The logo as a (size, size, 4) float array, top row first, resized to LOGO_ICON_SIZE."""
    image = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = image.size
        pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    pixels = pixels.reshape(height, width, 4)[::-1]
    step = max(1, width // LOGO_ICON_SIZE)
    if step > 1:  # box filter, on premultiplied colors
        pixels = pixels[:LOGO_ICON_SIZE * step, :LOGO_ICON_SIZE * step].copy()
        pixels[..., :3] *= pixels[..., 3:]
        pixels = pixels.reshape(LOGO_ICON_SIZE, step, LOGO_ICON_SIZE, step, 4).mean(axis=(1, 3))
        pixels[..., :3] /= numpy.maximum(pixels[..., 3:], 1e-6)
    return pixels


def _logo_with_dot(logo, color, hollow, dimmed):
    """RGBA bytes of the logo with a status dot in its bottom right corner, cut out of the logo."""
    size = logo.shape[0]
    rgb = logo[..., :3].copy()
    alpha = logo[..., 3].copy()
    if dimmed:  # stopped: grey and faded logo
        rgb[:] = rgb.mean(axis=2, keepdims=True)
        alpha *= 0.55
    y, x = numpy.mgrid[0:size, 0:size] + 0.5
    distance = numpy.hypot(x - size * 0.78, y - size * 0.78)
    outer = size * 0.22
    gap = numpy.clip(outer + size * 0.06 + 0.5 - distance, 0.0, 1.0)
    alpha *= 1.0 - gap
    dot = numpy.clip(outer + 0.5 - distance, 0.0, 1.0)
    if hollow:
        dot = numpy.minimum(dot, numpy.clip(distance - (outer - size * 0.08) + 0.5, 0.0, 1.0))
    # "over" with straight alpha
    out_alpha = dot + alpha * (1.0 - dot)
    out_rgb = (numpy.array(color) * dot[..., None] + rgb * (alpha * (1.0 - dot))[..., None]) / numpy.maximum(
        out_alpha[..., None], 1e-6
    )
    pixels = numpy.dstack([out_rgb, out_alpha])
    return bytearray((numpy.clip(pixels, 0.0, 1.0) * 255 + 0.5).astype(numpy.uint8).tobytes())


def _load_logo_icons():
    """Timer: once hython has rendered the logo, build the logo icons (one per state). Not
    in register(), where Blender data cannot be loaded."""
    global _logo_ready
    if _previews is None:
        return None
    logo_path = paths.houdini_logo()
    if not os.path.exists(logo_path):
        return 2.0
    try:
        logo = _read_logo(logo_path)
        folder = paths.status_icons_dir()
        for key, color in COLORS.items():
            path = os.path.join(folder, "logo_%s.png" % key.lower())
            hollow = key in icons.HOLLOW
            icons.write_png(path, LOGO_ICON_SIZE, _logo_with_dot(logo, color, hollow, dimmed=key == "STOPPED"))
            icons.load_preview(_previews, "LOGO_" + key, path)
    except (RuntimeError, OSError, ValueError) as error:
        feedback.log.warning("header logo not built (%s)", error)
        return None
    _logo_ready = True
    feedback.redraw()
    return None


def state():
    """(state key, text) of the connection."""
    cooking, _ = cook.activity()
    text, icon = link.server.status()
    if icon == "ERROR":
        return "ERROR", text
    if icon == "TIME":
        return "STARTING", text
    if icon == "CHECKMARK":
        if cooking:
            longest = max(seconds for _, seconds, _ in cooking)
            return "BUSY", "Cooking %d HDA%s · %.1f s" % (len(cooking), "s" if len(cooking) > 1 else "", longest)
        return "READY", text
    return "STOPPED", "Houdini is not running (it starts with the first cook)"


def _icon(key):
    if _logo_ready:
        key = "LOGO_" + key
    preview = _previews.get(key) if _previews is not None else None
    return preview.icon_id if preview is not None else 0


class HOUDINI_BRIDGE_PT_status(bpy.types.Panel):
    bl_label = "Houdini"
    bl_idname = "HOUDINI_BRIDGE_PT_status"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        key, text = state()
        layout.label(text=text, icon_value=_icon(key))
        blocked = trust.is_blocked()
        if blocked:
            layout.label(text="Auto Run Python Scripts is off: this file does not cook", icon="ERROR")
        row = layout.row(align=True)
        if blocked:  # in place of Start, which would allow the file too
            row.operator("houdini_bridge.allow_file", icon="CHECKMARK")
            if key not in ("STOPPED", "ERROR"):
                row.operator("houdini_bridge.server", text="Stop", icon="X").action = "STOP"
        elif key in ("STOPPED", "ERROR"):
            row.operator("houdini_bridge.server", text="Start", icon="PLAY").action = "START"
        else:
            row.operator("houdini_bridge.server", text="Restart", icon="FILE_REFRESH").action = "RESTART"
            row.operator("houdini_bridge.server", text="Stop", icon="X").action = "STOP"
        row = layout.row(align=True)
        row.operator("houdini_bridge.server_log", icon="TEXT")
        row.operator("houdini_bridge.open_preferences", text="Preferences", icon="PREFERENCES")


def _draw_header(self, context):
    if not preferences.get().show_indicator:
        return
    key, _ = state()
    self.layout.popover(panel=HOUDINI_BRIDGE_PT_status.bl_idname, text="Houdini Bridge", icon_value=_icon(key))


def register():
    global _previews
    folder = paths.status_icons_dir()
    _previews = bpy.utils.previews.new()
    for key in COLORS:
        icons.load_preview(_previews, key, icons.write_state_icon(folder, key))
    bpy.utils.register_class(HOUDINI_BRIDGE_PT_status)
    bpy.types.VIEW3D_MT_editor_menus.append(_draw_header)
    bpy.app.timers.register(_load_logo_icons, first_interval=0.1, persistent=True)


def unregister():
    global _previews, _logo_ready
    if bpy.app.timers.is_registered(_load_logo_icons):
        bpy.app.timers.unregister(_load_logo_icons)
    _logo_ready = False
    bpy.types.VIEW3D_MT_editor_menus.remove(_draw_header)
    bpy.utils.unregister_class(HOUDINI_BRIDGE_PT_status)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
