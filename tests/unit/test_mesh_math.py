"""The numpy side of the mesh conversion: axes, face winding, topology digests."""

import unittest

import numpy as np

from tests import support

mesh_io = support.source("mesh_io")


class Axes(unittest.TestCase):
    def test_z_up_becomes_y_up(self):
        np.testing.assert_array_equal(mesh_io.blender_to_houdini(np.array([[0.0, 0.0, 1.0]])), [[0.0, 1.0, 0.0]])
        np.testing.assert_array_equal(mesh_io.blender_to_houdini(np.array([[0.0, 1.0, 0.0]])), [[0.0, 0.0, -1.0]])
        np.testing.assert_array_equal(mesh_io.blender_to_houdini(np.array([[1.0, 0.0, 0.0]])), [[1.0, 0.0, 0.0]])

    def test_round_trip(self):
        points = np.random.default_rng(2).normal(size=(100, 3)).astype(np.float32)
        np.testing.assert_array_equal(mesh_io.houdini_to_blender(mesh_io.blender_to_houdini(points)), points)
        np.testing.assert_array_equal(mesh_io.blender_to_houdini(mesh_io.houdini_to_blender(points)), points)

    def test_is_a_rotation(self):
        points = np.random.default_rng(3).normal(size=(50, 3))
        turned = mesh_io.blender_to_houdini(points)
        np.testing.assert_allclose(np.linalg.norm(turned, axis=1), np.linalg.norm(points, axis=1))
        # A rotation keeps the handedness: the cross product turns with the vectors.
        a, b = points[:25], points[25:]
        np.testing.assert_allclose(np.cross(mesh_io.blender_to_houdini(a), mesh_io.blender_to_houdini(b)),
                                   mesh_io.blender_to_houdini(np.cross(a, b)), atol=1e-12)


class FaceReversal(unittest.TestCase):
    def test_reverses_each_face(self):
        counts = np.array([3, 4, 1], np.int32)
        corners = np.arange(8)
        np.testing.assert_array_equal(corners[mesh_io.face_reversal(counts)], [2, 1, 0, 6, 5, 4, 3, 7])

    def test_is_an_involution(self):
        counts = np.random.default_rng(4).integers(1, 9, size=200).astype(np.int32)
        reversal = mesh_io.face_reversal(counts)
        np.testing.assert_array_equal(reversal[reversal], np.arange(int(counts.sum())))

    def test_no_face(self):
        self.assertEqual(len(mesh_io.face_reversal(np.zeros(0, np.int32))), 0)


class TopologyDigest(unittest.TestCase):
    counts = np.array([4, 4, 3], np.int32)
    indices = np.array([0, 1, 2, 3, 1, 4, 5, 2, 3, 2, 6], np.int32)

    def test_stable(self):
        first = mesh_io.topology_digest(self.counts, self.indices)
        again = mesh_io.topology_digest(self.counts.copy(), self.indices.copy())
        self.assertEqual(first, again)
        self.assertEqual(len(first), 32)

    def test_ignores_memory_layout(self):
        strided = np.repeat(self.indices, 2)[::2]
        self.assertEqual(mesh_io.topology_digest(self.counts, self.indices),
                         mesh_io.topology_digest(self.counts, strided))

    def test_changes_with_the_topology(self):
        reference = mesh_io.topology_digest(self.counts, self.indices)
        moved = self.indices.copy()
        moved[-1] = 7
        self.assertNotEqual(reference, mesh_io.topology_digest(self.counts, moved))
        self.assertNotEqual(reference, mesh_io.topology_digest(np.array([4, 3, 4], np.int32), self.indices))
        self.assertNotEqual(reference, mesh_io.topology_digest(self.counts[:2], self.indices[:8]))


if __name__ == "__main__":
    unittest.main()
