"""Small colored icons, written as PNG files: state dots and the flags of a Houdini node
(the bypass flag, the lock flag as a padlock, the template flag as a triangle, the display
flag as an eye).

Blender icons cannot be tinted, but an icon can be an image: the callers write these
icons into the extension's user folder and load them as previews.
"""

import os
import struct
import zlib

ICON_SIZE = 32  # pixels; Blender scales an icon down to the size of a button
# The version of the drawings: raise it when a shape or a color changes, so that the icons
# written once into the user folder are written again, into a folder of the new version.
DRAWING_VERSION = 1

# The colors of a state, as the header button and the sidebar show it.
COLORS = {
    "STOPPED": (0.45, 0.45, 0.45),  # grey: nothing runs, or not cooked yet
    "STARTING": (0.95, 0.75, 0.25),  # amber: Houdini is starting
    "READY": (0.30, 0.80, 0.40),  # green: connected, or a result up to date
    "BUSY": (1.00, 0.45, 0.10),  # Houdini orange: cooking
    "ERROR": (0.95, 0.27, 0.22),  # red: an error
    "BYPASSED": (1.00, 0.82, 0.10),  # yellow, as Houdini's bypass flag
}
# The states drawn as a ring rather than a disc: something is missing or still to come.
HOLLOW = ("STOPPED", "STARTING")

FLAG_OFF_COLOR = (0.80, 0.80, 0.80)  # a flag that is not set: a light outline
LOCK_COLOR = (0.86, 0.42, 0.38)  # the lock flag of a Houdini node, red
TEMPLATE_COLOR = (0.93, 0.42, 0.72)  # the template flag of a Houdini node, pink
DISPLAY_COLOR = (0.30, 0.62, 1.00)  # the display flag of a Houdini node, blue
SAMPLES = 4  # sub-pixels per side, for the anti-aliased edges of the flag and the padlock


def write_png(path, size, pixels):
    """Write a square RGBA PNG: `pixels` holds size * size * 4 bytes, top row first."""
    rows = b"".join(b"\x00" + bytes(pixels[y * size * 4:(y + 1) * size * 4]) for y in range(size))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    with open(path, "wb") as file:
        file.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows))
                   + chunk(b"IEND", b""))


def dot(color, hollow, size=ICON_SIZE):
    """An anti-aliased dot (or ring, when `hollow`) centered in a square icon: RGBA bytes."""
    pixels = bytearray(size * size * 4)
    centre = (size - 1) / 2
    outer = size * 0.30
    inner = outer - size * 0.09 if hollow else -1.0
    for y in range(size):
        for x in range(size):
            distance = ((x - centre) ** 2 + (y - centre) ** 2) ** 0.5
            coverage = max(0.0, min(1.0, outer + 0.5 - distance))
            if hollow:
                coverage = min(coverage, max(0.0, min(1.0, distance - inner + 0.5)))
            index = (y * size + x) * 4
            pixels[index:index + 4] = bytes([int(c * 255) for c in color] + [int(coverage * 255)])
    return pixels


def _in_flag(u, v, hollow):
    """Whether the point (u, v) of the icon, both from 0 to 1 and v downwards, is on the flag:
    a pole on the left, and a cloth at its top, filled or drawn as an outline."""
    pole = 0.22 <= u <= 0.30 and 0.10 <= v <= 0.90
    cloth = 0.30 <= u <= 0.84 and 0.12 <= v <= 0.56
    if cloth and hollow:
        border = 0.08
        cloth = not (0.30 + border <= u <= 0.84 - border and 0.12 + border <= v <= 0.56 - border)
    return pole or cloth


def _in_padlock(u, v, hollow):
    """Whether the point (u, v) of the icon is on the padlock: a body, filled or drawn as an
    outline, under a shackle (a half ring)."""
    body = 0.24 <= u <= 0.76 and 0.46 <= v <= 0.88
    if body and hollow:
        border = 0.08
        body = not (0.24 + border <= u <= 0.76 - border and 0.46 + border <= v <= 0.88 - border)
    distance = ((u - 0.5) ** 2 + (v - 0.44) ** 2) ** 0.5
    shackle = 0.15 <= distance <= 0.23 and v <= 0.47
    return body or shackle


TRIANGLE = ((0.50, 0.14), (0.88, 0.82), (0.12, 0.82))  # its corners, (u, v) with v downwards
TRIANGLE_BORDER = 0.09


def _in_triangle(u, v, hollow):
    """Whether the point (u, v) of the icon is on the triangle, filled or drawn as an outline:
    inside each of its edges, and, for an outline, not inside every edge moved in by the border."""
    distances = []
    for (x0, y0), (x1, y1) in zip(TRIANGLE, TRIANGLE[1:] + TRIANGLE[:1], strict=True):
        length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        # Positive inside: the corners go clockwise on screen (v downwards).
        distances.append(((x1 - x0) * (v - y0) - (y1 - y0) * (u - x0)) / length)
    inside = min(distances) >= 0
    return inside and not (hollow and min(distances) >= TRIANGLE_BORDER)


def _in_eye(u, v, hollow):
    """Whether the point (u, v) of the icon is on the eye: an almond (two circles crossing) with
    a pupil. Set, the almond is filled and the pupil stands apart in it; not set, the almond is
    an outline around the pupil."""
    radius, offset, border = 0.45, 0.24, 0.07
    upper = ((u - 0.5) ** 2 + (v - 0.5 - offset) ** 2) ** 0.5
    lower = ((u - 0.5) ** 2 + (v - 0.5 + offset) ** 2) ** 0.5
    almond = upper <= radius and lower <= radius
    centre = ((u - 0.5) ** 2 + (v - 0.5) ** 2) ** 0.5
    if hollow:
        return (almond and not (upper <= radius - border and lower <= radius - border)) or centre <= 0.09
    return almond and not 0.09 < centre <= 0.16


def _sampled(inside, color, size):
    """RGBA bytes of a square icon of one color, each pixel's alpha the share of its
    SAMPLES x SAMPLES sub-pixels for which inside(u, v) holds."""
    pixels = bytearray(size * size * 4)
    rgb = [int(c * 255) for c in color]
    for y in range(size):
        for x in range(size):
            covered = sum(inside((x + (i + 0.5) / SAMPLES) / size, (y + (j + 0.5) / SAMPLES) / size)
                          for i in range(SAMPLES) for j in range(SAMPLES))
            index = (y * size + x) * 4
            pixels[index:index + 4] = bytes(rgb + [round(255 * covered / SAMPLES ** 2)])
    return pixels


# The flag icons: key -> (shape, color, hollow). The bypass flag is a flag: bypassed, partly
# (off in the viewport or in renders only), or not. The lock flag is a padlock, the template
# flag a triangle (a piece of geometry: the result is drawn as a ghost wireframe), the display
# flag an eye (the viewport shows this node's output).
SHAPES = {
    "BYPASS_ON": (_in_flag, COLORS["BYPASSED"], False),
    "BYPASS_PARTLY": (_in_flag, COLORS["BYPASSED"], True),
    "BYPASS_OFF": (_in_flag, FLAG_OFF_COLOR, True),
    "LOCK_ON": (_in_padlock, LOCK_COLOR, False),
    "LOCK_OFF": (_in_padlock, FLAG_OFF_COLOR, True),
    "TEMPLATE_ON": (_in_triangle, TEMPLATE_COLOR, False),
    "TEMPLATE_OFF": (_in_triangle, FLAG_OFF_COLOR, True),
    "DISPLAY_ON": (_in_eye, DISPLAY_COLOR, False),
    "DISPLAY_OFF": (_in_eye, FLAG_OFF_COLOR, True),
}


def state_pixels(key, size=ICON_SIZE):
    """The RGBA bytes of the icon of `key`, a key of COLORS (a dot) or of SHAPES."""
    if key in COLORS:
        return dot(COLORS[key], hollow=key in HOLLOW, size=size)
    inside, color, hollow = SHAPES[key]
    return _sampled(lambda u, v: inside(u, v, hollow), color, size)


def write_state_icon(folder, key):
    """Write the icon of `key` into `folder` unless it is there already, and return its
    path: <folder>/v<DRAWING_VERSION>/<key in lower case>.png."""
    folder = os.path.join(folder, "v%d" % DRAWING_VERSION)
    path = os.path.join(folder, "%s.png" % key.lower())
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        write_png(path, ICON_SIZE, state_pixels(key))
    return path


def load_preview(previews, key, path):
    """Load an image file into a Blender previews collection under `key`, and return the
    preview. Previews load lazily, in the background: an icon shown for the first time would
    flash Blender's loading icon, so the pixels are read now, which loads them."""
    preview = previews.load(key, path, "IMAGE")
    len(preview.image_pixels)
    len(preview.icon_pixels)
    return preview
