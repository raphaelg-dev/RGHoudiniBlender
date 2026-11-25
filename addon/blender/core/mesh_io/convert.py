"""Mesh conversion between Blender and Houdini: axes, winding and digests.

Blender: Z up, counter-clockwise faces.
Houdini: Y up, clockwise faces.
Going from one to the other is a 90° rotation around X, and the corners of
every face are taken in reverse order.
"""

import hashlib

import numpy as np


def blender_to_houdini(positions):
    x, y, z = positions[:, 0], positions[:, 1], positions[:, 2]
    return np.stack([x, z, -y], 1)


def houdini_to_blender(positions):
    x, y, z = positions[:, 0], positions[:, 1], positions[:, 2]
    return np.stack([x, -z, y], 1)


def face_reversal(counts):
    """Indices that reverse the corner order inside every face (an involution)."""
    total = int(counts.sum())
    if total == 0:
        return np.zeros(0, np.int64)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    start_per_corner = np.repeat(starts, counts)
    end_per_corner = np.repeat(starts + counts - 1, counts)
    return start_per_corner + end_per_corner - np.arange(total)


def topology_digest(counts, indices):
    digest = hashlib.blake2b(digest_size=16)
    digest.update(np.ascontiguousarray(counts).tobytes())
    digest.update(np.ascontiguousarray(indices).tobytes())
    return digest.hexdigest()
