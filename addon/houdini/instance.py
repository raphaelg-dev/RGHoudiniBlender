"""The Houdini network of one Blender modifier: the HDA, its inputs, the mesh path and
the branches of the native streams.

Under /obj/houdini_bridge (one geo per instance): stash (inputs) -> HDA ->
classify -> unpack -> convert -> keep_polygons -> export_topology, and one
branch per native stream (houdini/streams.py, STREAMS), cooked only when the
classification finds something.
"""

import re
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
    import_attributes,
    set_uv,
)
from .parms import describe_parms
from .streams import STREAMS, VEX_CLASSIFY, VEX_REMOVE_STREAMS, classified

MAX_ERROR_TEXT = 400  # characters kept of one node error
MAX_MESSAGES = 8  # errors, and warnings, sent back with a cook
DEFAULT_INPUT_LABEL = re.compile(r"Sub-Network Input #\d+")  # what Houdini shows for an HDA input left unlabelled

# Removes what Blender cannot receive: anything but closed polygons of 3 points or more.
# Unconnected points stay (they become loose vertices in Blender).
VEX_KEEP_POLYGONS = """\
string primitiveType = primintrinsic(0, "typename", @primnum);
int isClosed = primintrinsic(0, "closed", @primnum);
int pointCount = len(primpoints(0, @primnum));
if (primitiveType != "Poly" || !isClosed || pointCount < 3)
    removeprim(0, @primnum, 0);
"""

VEX_REMOVE_LOOSE_POINTS = """\
if (len(pointprims(0, @ptnum)) == 0)
    removepoint(0, @ptnum);
"""


def input_labels(labels, count):
    """The names of a node's first `count` inputs, as Houdini shows them; "Input 2" for an
    input with no label, or with the default label of an HDA ("Sub-Network Input #2")."""
    names = []
    for index in range(count):
        label = labels[index].strip() if index < len(labels) else ""
        names.append("Input %d" % (index + 1) if not label or DEFAULT_INPUT_LABEL.fullmatch(label) else label)
    return names


class Instance:
    """One HDA cooked for one Blender object."""

    def __init__(self, path, node_type, hda_date=None):
        self.path = path
        self.node_type = node_type
        self.hda_date = hda_date  # the date of the HDA file it was made from (Server.hda_dates)
        self.container = hou.node("/obj").createNode("geo", "houdini_bridge")
        try:
            self._build(node_type)
        except BaseException:
            self.container.destroy()  # a node type Houdini does not know leaves nothing in /obj
            raise

    def _build(self, node_type):
        self.hda = self.container.createNode(node_type)
        hda_type = self.hda.type()
        self.input_count = min(hda_type.maxNumInputs(), contract.MAX_INPUTS)
        self.input_labels = input_labels(self.hda.inputLabels(), self.input_count)
        self.stashes = [self.container.createNode("stash", "input_%d" % (i + 1)) for i in range(self.input_count)]
        self.received_inputs = set()
        self.geometries = {}
        self.chain_nodes = {}  # input index -> Object Merge of another instance
        self.chained = {}  # input index -> the instance it reads
        self.output_mode = None

        # Mesh path. In native mode, what another stream takes is removed first
        # (with its points), and loose points leave too; in mesh mode those two
        # wrangles are bypassed and everything is unpacked and converted.
        self.classify = self.wrangle(self.hda, "classify", VEX_CLASSIFY, RUN_OVER_PRIMITIVES)
        self.remove_streams = self.wrangle(self.classify, "remove_streams", VEX_REMOVE_STREAMS,
                                          RUN_OVER_PRIMITIVES)
        unpack = self.container.createNode("unpack")
        unpack.setInput(0, self.remove_streams)
        # NURBS, Bezier and Mesh primitives become polygons; polygons pass through.
        convert = self.container.createNode("convert")
        convert.setInput(0, unpack)
        keep_polygons = self.wrangle(convert, "keep_polygons", VEX_KEEP_POLYGONS, RUN_OVER_PRIMITIVES)
        self.remove_loose = self.wrangle(keep_polygons, "remove_loose_points", VEX_REMOVE_LOOSE_POINTS,
                                       RUN_OVER_POINTS)
        self.export = self.wrangle(self.remove_loose, "export_topology", VEX_EXPORT_TOPOLOGY, RUN_OVER_VERTICES)

        # Each native stream builds its branch, cooked only when the classification finds something.
        self.streams = [stream(self) for stream in STREAMS]

    def wrangle(self, source, name, snippet, run_over):
        node = self.container.createNode("attribwrangle", name)
        node.setInput(0, source)
        node.parm("class").set(run_over)  # one of geometry.RUN_OVER_*
        node.parm("snippet").set(snippet)
        return node

    def keep_kind(self, kind):
        blast = self.container.createNode("blast", "keep_kind_%d" % kind)
        blast.setInput(0, self.classify)
        blast.parm("group").set("@__kind==%d" % kind)
        blast.parm("grouptype").set(BLAST_PRIMITIVES)
        blast.parm("negate").set(True)
        return blast

    def mesh_export(self, source, name):
        """The mesh path for a prototype: convert, closed polygons, topology export."""
        convert = self.container.createNode("convert", name + "_convert")
        convert.setInput(0, source)
        keep = self.wrangle(convert, name + "_keep_polygons", VEX_KEEP_POLYGONS, RUN_OVER_PRIMITIVES)
        return self.wrangle(keep, name + "_export", VEX_EXPORT_TOPOLOGY, RUN_OVER_VERTICES)

    def set_output_mode(self, mode):
        """`mode` is one of contract.OUTPUT_MODES."""
        if mode == self.output_mode:
            return
        self.output_mode = mode
        for node in (self.remove_streams, self.remove_loose):
            node.bypass(mode == contract.MESH)

    def configure_streams(self, header):
        """Settings of the streams read from the cook request (the sphere detail of the instances)."""
        for stream in self.streams:
            stream.configure(header)

    def destroy(self):
        for stream in self.streams:
            stream.destroy()
        try:
            self.container.destroy()
        except hou.ObjectWasDeleted:
            pass

    def export_streams(self, answer, output, folder, extras, resend):
        """Add the native streams the last cook classified to the answer. Returns the
        seconds each exported stream took, by name."""
        if resend:
            for stream in self.streams:
                stream.resend()
        kinds = classified(self.classify.geometry())
        timings = {}
        for stream in self.streams:
            start = time.perf_counter()
            if not stream.has(kinds, folder):
                stream.absent()
                continue
            answer[stream.name], arrays = stream.export(folder, extras)
            output.update(arrays)
            timings[stream.name] = time.perf_counter() - start
        return timings

    def describe(self):
        definition = self.hda.type().definition()
        parms = describe_parms(self.hda)
        return {
            "label": self.hda.type().description(),
            "version": definition.version() if definition else "",
            "inputs": self.input_labels,
            "parms": parms,
        }

    def renumbered(self):
        """Whether the exported mesh has other point or primitive numbers than the HDA's own
        output, which a chained modifier below reads (set_chain): the mesh path unpacks,
        converts and keeps closed polygons only, and in native mode leaves the streams and the
        loose points out. The numbers match only when the output holds polygons alone and
        nothing is left out."""
        source, mesh = self.hda.geometry(), self.export.geometry()
        if source is None or mesh is None:
            return False
        count = source.intrinsicValue("primitivecount")
        return (count != mesh.intrinsicValue("primitivecount")
                or source.intrinsicValue("pointcount") != mesh.intrinsicValue("pointcount")
                or source.countPrimType(hou.primType.Polygon) != count)

    def set_chain(self, index, source):
        """Input `index` is the HDA output of another instance (the modifier above)."""
        node = self.chain_nodes.get(index)
        if node is None:
            node = self.container.createNode("object_merge", "chain_%d" % (index + 1))
            self.chain_nodes[index] = node
        node.parm("objpath1").set(source.hda.path())
        self.hda.setInput(index, node)
        self.chained[index] = source
        self.received_inputs.add(index)
        self.geometries.pop(index, None)

    def set_input(self, index, geometry):
        self.chained.pop(index, None)
        if geometry is None:
            self.hda.setInput(index, None)
            self.received_inputs.discard(index)
            self.geometries.pop(index, None)
            return
        self.geometries[index] = geometry
        self.stashes[index].parm("stash").set(geometry)
        self.hda.setInput(index, self.stashes[index])
        self.received_inputs.add(index)

    def update_input(self, index, arrays, prefix, attributes):
        """Same topology: only the values (P, uv, attributes) are rewritten."""
        geometry = self.geometries[index]
        geometry.setPointFloatAttribValuesFromString("P", arrays[prefix + "P"].astype(np.float32).tobytes())
        set_uv(geometry, arrays.get(prefix + "uv"))
        import_attributes(geometry, arrays, prefix, attributes)
        # Writing P in bulk does not bump the data ids: nodes that cache on them
        # (VDB from Polygons in Edge Damage) would keep using the old shape.
        geometry.incrementAllDataIds()
        self.stashes[index].parm("stash").set(geometry)

    def errors(self):
        # An error propagates as "Invalid source" on every downstream node:
        # only the causes are kept.
        causes = []
        cascade = []
        for node in list(self.container.allSubChildren()):
            for error in node.errors():
                if len(error) > MAX_ERROR_TEXT:
                    error = error[:MAX_ERROR_TEXT] + "…"
                message = "%s: %s" % (node.path().replace(self.container.path() + "/", ""), error)
                (cascade if error.startswith("Invalid source") else causes).append(message)
        return (causes or cascade[:1])[:MAX_MESSAGES]

    def warnings(self):
        return list(self.hda.warnings())[:MAX_MESSAGES]
