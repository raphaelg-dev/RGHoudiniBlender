"""The state icons (blender/core/icons.py), without Blender: the PNG files, the
state dots and the bypass flag."""

import os
import struct
import tempfile
import unittest
import zlib

from tests import support

icons = support.source("icons")
SIZE = icons.ICON_SIZE


def alpha(pixels, x, y, size=SIZE):
    return pixels[(y * size + x) * 4 + 3]


def at(u, v):
    """The pixel at (u, v) of the icon, both from 0 to 1."""
    return int(u * SIZE), int(v * SIZE)


class Png(unittest.TestCase):
    def test_a_png_reads_back(self):
        pixels = icons.dot(icons.COLORS["READY"], hollow=False)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "dot.png")
            icons.write_png(path, SIZE, pixels)
            with open(path, "rb") as file:
                data = file.read()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width, height, depth, color_type = struct.unpack(">IIBB", data[16:26])
        self.assertEqual((width, height, depth, color_type), (SIZE, SIZE, 8, 6))  # 8-bit RGBA
        start = data.index(b"IDAT") + 4
        length = struct.unpack(">I", data[start - 8:start - 4])[0]
        rows = zlib.decompress(data[start:start + length])
        self.assertEqual(len(rows), SIZE * (1 + SIZE * 4))
        self.assertEqual(rows[1:1 + SIZE * 4], bytes(pixels[:SIZE * 4]))

    def test_written_once_under_its_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path = icons.write_state_icon(os.path.join(folder, "states"), "BYPASS_ON")
            self.assertEqual(os.path.basename(path), "bypass_on.png")
            self.assertTrue(os.path.exists(path))
            before = os.path.getmtime(path)
            os.utime(path, (before - 100, before - 100))
            icons.write_state_icon(os.path.join(folder, "states"), "BYPASS_ON")
            self.assertEqual(os.path.getmtime(path), before - 100, "an icon on disk is not written again")
            self.assertEqual(os.path.basename(os.path.dirname(path)), "v%d" % icons.DRAWING_VERSION,
                             "but a new version of the drawings writes into a folder of its own")


class Shapes(unittest.TestCase):
    def test_dot_and_ring(self):
        disc = icons.dot(icons.COLORS["ERROR"], hollow=False)
        ring = icons.dot(icons.COLORS["STOPPED"], hollow=True)
        centre = SIZE // 2
        self.assertEqual(alpha(disc, centre, centre), 255)
        self.assertEqual(alpha(ring, centre, centre), 0, "a ring is empty in its middle")
        self.assertEqual(alpha(disc, 0, 0), 0)
        self.assertEqual(disc[(centre * SIZE + centre) * 4:(centre * SIZE + centre) * 4 + 3],
                         bytes(int(c * 255) for c in icons.COLORS["ERROR"]))

    def test_the_flag(self):
        solid = icons.state_pixels("BYPASS_ON")
        outline = icons.state_pixels("BYPASS_PARTLY")
        self.assertEqual(alpha(solid, *at(0.26, 0.80)), 255, "the pole")
        self.assertEqual(alpha(solid, *at(0.57, 0.34)), 255, "the middle of the cloth")
        self.assertEqual(alpha(outline, *at(0.57, 0.34)), 0, "an outline flag is empty in its middle")
        self.assertEqual(alpha(outline, *at(0.80, 0.14)), 255, "but has its border")
        for u, v in ((0.05, 0.05), (0.60, 0.80), (0.95, 0.30)):
            self.assertEqual(alpha(solid, *at(u, v)), 0, "nothing at (%.2f, %.2f)" % (u, v))
        yellow = bytes(int(c * 255) for c in icons.COLORS["BYPASSED"])
        index = (at(0.57, 0.34)[1] * SIZE + at(0.57, 0.34)[0]) * 4
        self.assertEqual(solid[index:index + 3], yellow, "bypassed: Houdini's yellow")

    def test_edges_are_smooth(self):
        solid = icons.state_pixels("BYPASS_ON")
        partial = [alpha(solid, x, y) for y in range(SIZE) for x in range(SIZE) if 0 < alpha(solid, x, y) < 255]
        self.assertTrue(partial, "the edges of the flag are anti-aliased")

    def test_the_padlock(self):
        locked = icons.state_pixels("LOCK_ON")
        open_lock = icons.state_pixels("LOCK_OFF")
        self.assertEqual(alpha(locked, *at(0.50, 0.70)), 255, "the middle of the body")
        self.assertEqual(alpha(open_lock, *at(0.50, 0.70)), 0, "an outline padlock is empty in its middle")
        self.assertEqual(alpha(open_lock, *at(0.26, 0.70)), 255, "but has its border")
        self.assertEqual(alpha(locked, *at(0.50, 0.25)), 255, "the shackle")
        self.assertEqual(alpha(locked, *at(0.50, 0.40)), 0, "inside the shackle")
        for u, v in ((0.05, 0.05), (0.10, 0.95), (0.95, 0.30)):
            self.assertEqual(alpha(locked, *at(u, v)), 0, "nothing at (%.2f, %.2f)" % (u, v))
        red = bytes(int(c * 255) for c in icons.LOCK_COLOR)
        index = (at(0.50, 0.70)[1] * SIZE + at(0.50, 0.70)[0]) * 4
        self.assertEqual(locked[index:index + 3], red, "locked: the red of Houdini's lock flag")

    def test_the_template_triangle(self):
        on = icons.state_pixels("TEMPLATE_ON")
        off = icons.state_pixels("TEMPLATE_OFF")
        self.assertEqual(alpha(on, *at(0.50, 0.60)), 255, "the middle of the triangle")
        self.assertEqual(alpha(off, *at(0.50, 0.60)), 0, "an outline triangle is empty in its middle")
        self.assertEqual(alpha(off, *at(0.50, 0.79)), 255, "but has its base")
        for u, v in ((0.05, 0.05), (0.15, 0.30), (0.85, 0.30), (0.50, 0.95)):
            self.assertEqual(alpha(on, *at(u, v)), 0, "nothing at (%.2f, %.2f)" % (u, v))
        pink = bytes(int(c * 255) for c in icons.TEMPLATE_COLOR)
        index = (at(0.50, 0.60)[1] * SIZE + at(0.50, 0.60)[0]) * 4
        self.assertEqual(on[index:index + 3], pink, "set: the pink of Houdini's template flag")

    def test_the_display_eye(self):
        on = icons.state_pixels("DISPLAY_ON")
        off = icons.state_pixels("DISPLAY_OFF")
        self.assertEqual(alpha(on, *at(0.50, 0.50)), 255, "the pupil")
        self.assertEqual(alpha(off, *at(0.50, 0.50)), 255, "the pupil, also not set")
        self.assertEqual(alpha(on, *at(0.50, 0.36)), 0, "set: the pupil stands apart in the almond")
        self.assertEqual(alpha(on, *at(0.28, 0.50)), 255, "set: the almond is filled")
        self.assertEqual(alpha(off, *at(0.28, 0.50)), 0, "not set: the almond is an outline")
        for u, v in ((0.05, 0.05), (0.50, 0.15), (0.50, 0.85), (0.97, 0.50)):
            self.assertEqual(alpha(on, *at(u, v)), 0, "nothing at (%.2f, %.2f)" % (u, v))
        blue = bytes(int(c * 255) for c in icons.DISPLAY_COLOR)
        index = (at(0.28, 0.50)[1] * SIZE + at(0.28, 0.50)[0]) * 4
        self.assertEqual(on[index:index + 3], blue, "set: the blue of Houdini's display flag")

    def test_every_key_has_an_icon(self):
        for key in list(icons.COLORS) + list(icons.SHAPES):
            self.assertEqual(len(icons.state_pixels(key)), SIZE * SIZE * 4, key)


if __name__ == "__main__":
    unittest.main()
