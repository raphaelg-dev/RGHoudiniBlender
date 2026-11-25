"""Mesh conversion between Blender and Houdini, with numpy.

- convert.py: axes, face winding and digests;
- read.py: Blender to Houdini (groups, attributes, a mesh, an evaluated object);
- write.py: Houdini to Blender (a mesh, its attributes and groups, point cloud and curves attributes).
"""

from .convert import blender_to_houdini, face_reversal, houdini_to_blender, topology_digest
from .read import TO_HOUDINI, read_attributes, read_groups, read_mesh, read_object
from .write import (
    ensure_attribute,
    follow_materials,
    forget_written,
    set_materials,
    write_attributes,
    write_extras,
    write_mesh,
)

__all__ = [
    # axes, winding, digests
    "blender_to_houdini", "houdini_to_blender", "face_reversal", "topology_digest", "TO_HOUDINI",
    # Blender to Houdini
    "read_object", "read_mesh", "read_groups", "read_attributes",
    # Houdini to Blender
    "write_mesh", "write_extras", "write_attributes", "ensure_attribute", "set_materials", "forget_written",
    # a result that shows its owner's materials
    "follow_materials",
]
