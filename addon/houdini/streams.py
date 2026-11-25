"""Native outputs: the classification of a result's primitives into streams, and the
export of each stream Blender has a native type for (instances and their prototypes, open
curves, loose points, volumes). STREAMS holds one class per stream, which builds its
branch of nodes in the network of houdini/instance.py.
"""

import os
import time

import hou
import numpy as np

from ..shared import contract
from .geometry import (
    BLAST_PRIMITIVES,
    RUN_OVER_POINTS,
    RUN_OVER_PRIMITIVES,
    RUN_OVER_VERTICES,
    VEX_EXPORT_TOPOLOGY,
    export_mesh,
    floats,
    ints,
    material_paths,
    numeric_attributes,
)

# Native output: each primitive goes to the stream Blender has a native type for.
# Group expressions cannot combine conditions with && in a Blast, hence one flag.
# Every VDB, fog or level set (signed distance), stays a volume: Blender has SDF
# grid nodes. Its class travels with it (see export_volume).
# Each primitive gets exactly one kind, and the order of the tests matters (an open
# curve, a height field that stays a mesh): kind 0 is the mesh path, and every other
# kind is the `kind` of one stream below.
KIND_MESH = 0
KIND_INSTANCES = 1
KIND_CURVES = 2
KIND_VOLUME = 3
VEX_CLASSIFY = """\
string primitiveType = primintrinsic(0, "typename", @primnum);
int isClosed = primintrinsic(0, "closed", @primnum);
i@__kind = 0;
if (startswith(primitiveType, "Packed") || primitiveType == "AlembicRef" || primitiveType == "Sphere")
    i@__kind = 1;
else if ((primitiveType == "Poly" || primitiveType == "NURBCurve" || primitiveType == "BezierCurve") && !isClosed)
    i@__kind = 2;
else if (primitiveType == "VDB")
    i@__kind = 3;
else if (primitiveType == "Volume") {
    // A height field is a flat volume: it stays on the mesh path.
    vector resolution = primintrinsic(0, "voxelresolution", @primnum);
    if (min(resolution) > 1)
        i@__kind = 3;
}
"""

# Removes from the mesh path what goes to another stream, with its points.
VEX_REMOVE_STREAMS = """\
if (i@__kind != 0)
    removeprim(0, @primnum, 1);
"""

VEX_KEEP_LOOSE_POINTS = """\
if (len(pointprims(0, @ptnum)) > 0)
    removepoint(0, @ptnum);
"""

# One matrix and one shape key per instance (packed primitive or quadric sphere).
# The key tells which instances share a shape: the shared geometry (geometryid),
# the piece of it (fragments share one geometry and differ by fragmentname),
# the file and the Alembic path. A sphere is a unit sphere placed by its
# 3×3 transform and its point.
# __xform is already in Blender's convention: Houdini row vectors with Y up
# become column vectors with Z up (C·M·Cᵀ), whose bytes are exactly what a
# FLOAT4X4 attribute expects. __houdini keeps the matrix as Houdini has it.
VEX_INSTANCES = """\
string primitiveType = primintrinsic(0, "typename", @primnum);
// A material set on the instance itself applies to a shape that has none: it is part of the key.
string material = prim(0, "shop_materialpath", @primnum);
if (material == "")
    material = point(0, "shop_materialpath", primpoint(0, @primnum, 0));
s@__material = material;
matrix fullTransform = ident();
if (primitiveType == "Sphere") {
    matrix3 sphereShape = primintrinsic(0, "transform", @primnum);
    vector centre = point(0, "P", primpoint(0, @primnum, 0));
    fullTransform = matrix(sphereShape);
    setcomp(fullTransform, centre.x, 3, 0);
    setcomp(fullTransform, centre.y, 3, 1);
    setcomp(fullTransform, centre.z, 3, 2);
    // The detail of the polygon sphere is part of the key: a new detail sends the shape again.
    i@__key = random_shash(sprintf("unit sphere|%d|%s", chi("../unit_sphere/freq"), material));
} else {
    fullTransform = primintrinsic(0, "packedfulltransform", @primnum);
    int geometryId = primintrinsic(0, "geometryid", @primnum);
    string fragment = primintrinsic(0, "fragmentname", @primnum);
    string filePath = primintrinsic(0, "unexpandedfilename", @primnum);
    string objectPath = primintrinsic(0, "abcobjectpath", @primnum);
    i@__key = random_shash(sprintf("%s|%d|%s|%s|%s|%s", primitiveType, geometryId, fragment, filePath, objectPath, material));
}
matrix toBlender = set(1, 0, 0, 0,  0, 0, -1, 0,  0, 1, 0, 0,  0, 0, 0, 1);
4@__houdini = fullTransform;
4@__xform = toBlender * fullTransform * transpose(toBlender);
i@__point = primpoint(0, @primnum, 0);
"""  # noqa: E501

# Attributes that place an instance: they are in its matrix already.
PLACEMENT_ATTRIBUTES = {"P", "Pw", "orient", "pscale", "scale", "N", "up", "rot", "trans", "pivot", "transform"}


def _add_attributes(prefix, entries, arrays):
    """Blocks <prefix>a<n> for (description, values) entries, all on the same elements."""
    descriptions = []
    for description, values in entries:
        block = "%sa%d" % (prefix, len(descriptions))
        arrays[block] = np.ascontiguousarray(values).ravel()
        descriptions.append(dict(description, block=block))
    return descriptions


def export_instances(geometry, known_keys, prototype_of):
    """Instances of the instance branch, plus the prototypes Blender does not have.

    `known_keys` is the set of shape keys already sent to Blender (updated
    here); `prototype_of(primitive number)` returns (fields, arrays) of the
    shape of one instance, in its own space."""
    arrays = {}
    keys = ints(geometry.primIntAttribValuesAsString("__key"))
    arrays["inst_xform"] = floats(geometry.primFloatAttribValuesAsString("__xform"))
    unique, first, slots = np.unique(keys, return_index=True, return_inverse=True)
    arrays["inst_proto"] = slots.astype(np.int32)

    # Copy to Points puts the template attributes on the packed primitive or
    # on its point: both are read, the primitive wins.
    entries = {}
    point_numbers = ints(geometry.primIntAttribValuesAsString("__point"))
    for description, values in numeric_attributes(geometry, "point", PLACEMENT_ATTRIBUTES):
        entries[description["name"]] = (description, values[point_numbers])
    for description, values in numeric_attributes(geometry, "prim", PLACEMENT_ATTRIBUTES):
        entries[description["name"]] = (description, values)
    attributes = _add_attributes("inst_", entries.values(), arrays)

    prototypes = []
    for key, representative in zip(unique.tolist(), first.tolist(), strict=False):
        if key in known_keys:
            continue
        fields, proto_arrays = prototype_of(representative)
        prefix = "proto%d_" % len(prototypes)
        arrays.update((prefix + name, values) for name, values in proto_arrays.items())
        prototypes.append(dict(fields, key=key, prefix=prefix))
        known_keys.add(key)
    return {"count": len(keys), "keys": unique.tolist(), "attributes": attributes, "prototypes": prototypes}, arrays


def localize(fields, arrays, matrix):
    """A prototype unpacked where its representative stands, brought back to
    the shape's own space (P by the inverse matrix, N by the transpose)."""
    matrix = np.array(matrix, np.float64).reshape(4, 4)
    if np.allclose(matrix, np.eye(4)):
        return fields, arrays
    positions = arrays["P"].reshape(-1, 3).astype(np.float64)
    arrays["P"] = (np.c_[positions, np.ones(len(positions))] @ np.linalg.inv(matrix))[:, :3].astype(np.float32).ravel()
    if "N" in arrays:
        normals = arrays["N"].reshape(-1, 3).astype(np.float64) @ matrix[:3, :3].T
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        arrays["N"] = (normals / np.where(lengths > 0, lengths, 1.0)).astype(np.float32)
    return fields, arrays


def export_curves(geometry):
    """Open polylines: points per curve in vertex order, curve and point attributes."""
    arrays = {}
    primitive = ints(geometry.vertexIntAttribValuesAsString("__primitive"))
    rank = ints(geometry.vertexIntAttribValuesAsString("__rank"))
    point = ints(geometry.vertexIntAttribValuesAsString("__point"))
    order = np.lexsort((rank, primitive))
    points = point[order]
    counts = np.bincount(primitive, minlength=geometry.intrinsicValue("primitivecount")).astype(np.int32)
    positions = floats(geometry.pointFloatAttribValuesAsString("P")).reshape(-1, 3)
    arrays["curve_counts"] = counts
    arrays["curve_P"] = np.ascontiguousarray(positions[points]).ravel()
    # Curve points are the vertices: point and vertex attributes both land there.
    on_points = {}
    for description, values in numeric_attributes(geometry, "point", {"P", "Pw"}):
        on_points[description["name"]] = (description, values[points])
    for description, values in numeric_attributes(geometry, "vertex"):
        on_points[description["name"]] = (description, values[order])
    point_attributes = _add_attributes("curve_p", on_points.values(), arrays)
    curve_attributes = _add_attributes("curve_c", numeric_attributes(geometry, "prim"), arrays)
    fields = {"count": len(counts), "point_attributes": point_attributes, "curve_attributes": curve_attributes}
    materials = material_paths(geometry, "prim")
    if materials:
        fields["materials"], arrays["curve_material"] = materials
    return fields, arrays


def export_points(geometry):
    arrays = {"points_P": floats(geometry.pointFloatAttribValuesAsString("P"))}
    attributes = _add_attributes("points_", numeric_attributes(geometry, "point", {"P", "Pw"}), arrays)
    fields = {"count": len(arrays["points_P"]) // 3, "attributes": attributes}
    materials = material_paths(geometry, "point")
    if materials:
        fields["materials"], arrays["points_material"] = materials
    return fields, arrays


def export_volume(geometry, folder, stem, previous):
    """VDB grids written to a .vdb file, the format Blender reads natively.

    A new file name at every cook makes Blender load it again; the previous
    file is removed when possible (Blender may still hold it)."""
    os.makedirs(folder, exist_ok=True)
    name_attribute = geometry.findPrimAttrib("name")
    grids = [primitive.attribValue("name") if name_attribute else "density" for primitive in geometry.prims()]
    # "fog volume", "level set"…: the .vdb file keeps it, but Blender does not expose it.
    classes = [primitive.intrinsicValue("vdb_class") for primitive in geometry.prims()]
    path = os.path.join(folder, "%s_%d.vdb" % (stem, int(time.time() * 1000)))
    geometry.saveToFile(path)
    for old in previous:
        try:
            os.remove(old)
        except OSError:
            pass
    fields = {"path": path, "grids": grids, "classes": classes}
    materials = material_paths(geometry, "prim")
    if materials:
        fields["materials"] = materials[0][:1]  # a Volume object takes one material
    return fields


# --- streams --------------------------------------------------------------------------


class Stream:
    """One native stream of one Instance: its branch of nodes and its own state.

    `name` is its key in the cook answer (contract.STREAMS); `kind` the number
    VEX_CLASSIFY gives its primitives, or None for a stream that does not come
    from the classification (the loose points). The constructor takes the Instance,
    whose nodes and helpers it uses (container, classify, wrangle, keep_kind,
    mesh_export); each method is called at one step of the cook and does nothing
    unless the stream needs it."""

    name = None
    kind = None

    def configure(self, header):
        """Before the cook: settings of the stream read from the cook request."""

    def has(self, kinds, folder):
        """After the cook: whether there is something to export. `kinds` counts the
        primitives of each kind (classified); `folder` is where volume files go ("" for none)."""
        return bool(self.kind is not None and kinds[self.kind])

    def export(self, folder, extras):
        """(description, blocks) of the stream for the cook answer."""
        raise NotImplementedError

    def absent(self):
        """The result has nothing for this stream."""

    def resend(self):
        """Blender asks for everything again (it lost some prototypes)."""

    def destroy(self):
        """The instance is deleted."""


class InstanceStream(Stream):
    """Packed primitives and quadric spheres: one matrix and one shape key each, and the
    prototypes Blender does not have yet."""

    name = contract.INSTANCES
    kind = KIND_INSTANCES

    def __init__(self, instance):
        self.sent_keys = set()  # shape keys whose prototype Blender has
        self.instances = instance.wrangle(instance.keep_kind(self.kind), "export_instances", VEX_INSTANCES,
                                         RUN_OVER_PRIMITIVES)
        self.representative = instance.container.createNode("blast", "representative")
        self.representative.setInput(0, self.instances)
        self.representative.parm("grouptype").set(BLAST_PRIMITIVES)
        self.representative.parm("negate").set(True)
        unpack_one = instance.container.createNode("unpack", "unpack_prototype")
        unpack_one.setInput(0, self.representative)
        unpack_one.parm("limit_iterations").set(False)  # packed inside packed
        self.prototype_export = instance.mesh_export(unpack_one, "prototype")
        # The shape of quadric spheres: polygons on the unit sphere, finer than Convert's.
        self.sphere = instance.container.createNode("sphere", "unit_sphere")
        self.sphere.parm("type").set("poly")
        self.sphere.parm("freq").set(contract.SPHERE_FREQUENCY)
        self.unit_sphere = instance.mesh_export(self.sphere, "unit_sphere")

    def configure(self, header):
        self.set_sphere_frequency(contract.field(header, "sphere_frequency"))

    def set_sphere_frequency(self, frequency):
        """Detail of the polygon sphere quadric spheres become (a preference), within
        contract.SPHERE_FREQUENCY_RANGE."""
        low, high = contract.SPHERE_FREQUENCY_RANGE
        frequency = max(low, min(int(frequency), high))
        if self.sphere.parm("freq").evalAsInt() != frequency:
            self.sphere.parm("freq").set(frequency)

    def prototype(self, primitive_number, extras):
        """(fields, arrays) of the shape of one instance, in its own space."""
        geometry = self.instances.geometry()
        primitive = geometry.prim(primitive_number)
        if primitive.type() == hou.primType.Sphere:
            self.unit_sphere.cook()
            fields, arrays = export_mesh(self.unit_sphere.geometry(), extras)
        else:
            self.representative.parm("group").set(str(primitive_number))
            self.prototype_export.cook()
            fields, arrays = localize(*export_mesh(self.prototype_export.geometry(), extras),
                                      primitive.attribValue("__houdini"))
        material = primitive.attribValue("__material")
        if material and "material" not in arrays:
            fields["materials"] = [material]
            arrays["material"] = np.zeros(len(arrays["counts"]), np.int32)
        return fields, arrays

    def export(self, folder, extras):
        self.instances.cook()
        return export_instances(self.instances.geometry(), self.sent_keys,
                                lambda number: self.prototype(number, extras))

    def absent(self):
        self.sent_keys.clear()

    def resend(self):
        self.sent_keys.clear()


class CurveStream(Stream):
    """Open polylines, NURBS and Bezier curves, converted to polylines."""

    name = contract.CURVES
    kind = KIND_CURVES

    def __init__(self, instance):
        curves = instance.container.createNode("convert", "curves_to_polylines")
        curves.setInput(0, instance.keep_kind(self.kind))
        self.curves = instance.wrangle(curves, "export_curves", VEX_EXPORT_TOPOLOGY, RUN_OVER_VERTICES)

    def export(self, folder, extras):
        self.curves.cook()
        return export_curves(self.curves.geometry())


class PointStream(Stream):
    """The points no primitive uses. They have no kind: this stream reads the
    classification's output directly and is cooked at every export."""

    name = contract.POINTS

    def __init__(self, instance):
        self.points = instance.wrangle(instance.classify, "export_points", VEX_KEEP_LOOSE_POINTS, RUN_OVER_POINTS)

    def has(self, kinds, folder):
        self.points.cook()
        return bool(self.points.geometry().intrinsicValue("pointcount"))

    def export(self, folder, extras):
        return export_points(self.points.geometry())


class VolumeStream(Stream):
    """VDB grids and Houdini volumes that are not flat, written to a .vdb file of `folder`."""

    name = contract.VOLUME
    kind = KIND_VOLUME

    def __init__(self, instance):
        self.stem = "volume_%s" % id(instance)
        self.volume_files = []
        to_vdb = instance.container.createNode("convertvdb", "volumes_to_vdb")
        to_vdb.setInput(0, instance.keep_kind(self.kind))
        to_vdb.parm("conversion").set("vdb")
        self.volumes = to_vdb

    def has(self, kinds, folder):
        return bool(kinds[self.kind] and folder)

    def export(self, folder, extras):
        self.volumes.cook()
        fields = export_volume(self.volumes.geometry(), folder, self.stem, self.volume_files)
        self.volume_files = [path for path in self.volume_files if os.path.exists(path)] + [fields["path"]]
        return fields, {}

    def destroy(self):
        for path in self.volume_files:
            try:
                os.remove(path)
            except OSError:
                pass


# In the order of contract.STREAMS (tests/unit/test_stream_registry.py checks it).
STREAMS = (InstanceStream, CurveStream, PointStream, VolumeStream)
# The kinds VEX_CLASSIFY gives: KIND_MESH, then the kind of each stream that has one.
KIND_COUNT = 1 + max(stream.kind for stream in STREAMS if stream.kind is not None)


def classified(geometry):
    """How many primitives of each kind the classification found: KIND_COUNT numbers."""
    if geometry.findPrimAttrib("__kind") is None:
        return np.zeros(KIND_COUNT, np.int64)
    return np.bincount(ints(geometry.primIntAttribValuesAsString("__kind")), minlength=KIND_COUNT)
