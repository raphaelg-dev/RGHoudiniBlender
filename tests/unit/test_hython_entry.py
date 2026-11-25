"""hython_entry.py mounts the extension's folder as a package, without Houdini or Blender."""

import os
import sys
import unittest

from tests import support


def _same(left, right):
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


class Mount(unittest.TestCase):
    def setUp(self):
        self.path = list(sys.path)
        self.modules = set(sys.modules)

    def tearDown(self):
        sys.path[:] = self.path
        for name in set(sys.modules) - self.modules:
            del sys.modules[name]

    def test_takes_the_folder_off_sys_path(self):
        support.mount_like_hython()
        self.assertFalse([entry for entry in sys.path if _same(entry or os.curdir, support.SOURCE_DIR)])

    def test_protocol_imports_from_the_package(self):
        package = support.mount_like_hython()
        protocol = support.hython_module("protocol")
        self.assertEqual(protocol.__name__, package + ".shared.protocol")
        self.assertTrue(_same(protocol.__file__, os.path.join(support.SOURCE_DIR, "shared", "protocol.py")))
        self.assertTrue(callable(protocol.send) and callable(protocol.receive))

    def test_does_not_run_the_extension_init(self):
        package = support.mount_like_hython()
        support.hython_module("protocol")
        root = sys.modules[package]
        self.assertFalse(hasattr(root, "register"))  # __init__.py defines register() and imports bpy
        self.assertFalse(hasattr(root, "__file__"))
        self.assertNotIn("bpy", set(sys.modules) - self.modules)


class PythonVersion(unittest.TestCase):
    def test_an_older_python_gets_one_line(self):
        entry = support.source("hython_entry")
        self.assertIsNone(entry.python_refusal((3, 10, 0)))
        self.assertIsNone(entry.python_refusal((3, 13, 1)))
        refusal = entry.python_refusal((3, 9, 7))
        self.assertIn("Houdini 20.0 or later", refusal)
        self.assertIn("Python 3.9", refusal)
        self.assertNotIn("\n", refusal)


if __name__ == "__main__":
    unittest.main()
