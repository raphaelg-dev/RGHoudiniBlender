"""RG Houdini Bridge server: runs inside hython and cooks HDAs for Blender.

Started by the add-on as `hython hython_entry.py`, with the token in the
HOUDINI_BRIDGE_TOKEN environment variable. Prints `HOUDINI_BRIDGE_PORT <n>` on stdout
once ready, serves a single client, and exits when that client disconnects, or
when its hello carries another token or another protocol version.

All geometry exchanged here is in Houdini space (Y up, clockwise faces):
conversion from and to Blender happens on the Blender side.
"""

import os
import socket
import time
import traceback

import hou

from ..shared import contract, protocol
from . import catalog
from .geometry import export_mesh, import_mesh, import_volume, write_volume
from .instance import Instance
from .output import log
from .parms import set_parm, set_parms


class RequestError(Exception):
    """A request the server cannot serve, for a reason its message says in one line: answered
    as the error, without a traceback."""


class Server:
    def __init__(self):
        self.instances = {}
        self.hda_dates = {}

    def install(self, path):
        """Install an HDA file once, and again when it changes on disk."""
        path = os.path.normpath(path)
        if not os.path.isfile(path):
            raise RequestError("HDA file not found: %s" % path)
        date = os.path.getmtime(path)
        known = self.hda_dates.get(path)
        if known is None:
            hou.hda.installFile(path)
        elif known != date:
            hou.hda.reloadFile(path)
        self.hda_dates[path] = date

    def handle(self, header, arrays):
        command = contract.field(header, contract.COMMAND)
        handler = HANDLERS.get(command)
        if handler is None:
            return {contract.ERROR: "unknown command: %s" % command}, {}
        undeclared = contract.undeclared(header)
        if undeclared:
            return {contract.ERROR: "%s: undeclared field %s" % (command, ", ".join(undeclared))}, {}
        try:
            return handler(self, header, arrays)
        except RequestError as error:
            return {contract.ERROR: str(error)}, {}

    def definitions(self, header, arrays):
        path = contract.field(header, "path")
        self.install(path)
        result = []
        for definition in hou.hda.definitionsInFile(path):
            category = definition.nodeTypeCategory().name()
            result.append({
                "type": definition.nodeTypeName(),
                "label": definition.description(),
                "category": category,
                "supported": category == "Sop",
                "max_inputs": min(definition.maxNumInputs(), contract.MAX_INPUTS),
            })
        return {"definitions": result}, {}

    def catalog(self, header, arrays):
        start = time.perf_counter()
        tools = catalog.build(
            contract.field(header, "folders"),
            contract.field(header, "packages"),
            contract.field(header, "icon_dir"),
            self.install,
            native=contract.field(header, "native"),
            all_versions=contract.field(header, "all_versions"),
        )
        return {"tools": tools, "duration": time.perf_counter() - start}, {}

    def write_volume(self, header, arrays):
        destination = contract.field(header, "destination")
        write_volume(contract.field(header, "source"), destination, contract.field(header, "matrix"))
        return {"ok": os.path.exists(destination)}, {}

    def logo(self, header, arrays):
        path = contract.field(header, "path")
        catalog.render_logo(path)
        return {"ok": os.path.exists(path)}, {}

    def delete(self, header, arrays):
        instance = self.instances.pop(contract.field(header, "instance"), None)
        if instance:
            instance.destroy()
        return {"ok": True}, {}

    def clear(self, header, arrays):
        for instance in self.instances.values():
            instance.destroy()
        self.instances.clear()
        return {"ok": True}, {}

    def ping(self, header, arrays):
        return {"ok": True}, {}

    def cook(self, header, arrays):
        start = time.perf_counter()
        instance = self._instance_for(header)
        missing, missing_chains = self._set_inputs(instance, header, arrays)
        if missing:
            return {contract.MISSING_INPUTS: missing, contract.MISSING_CHAINS: missing_chains}, {}
        import_time = time.perf_counter() - start
        _set_time(header)
        set_parms(instance.hda, contract.field(header, "parms"))
        for name in contract.field(header, "buttons"):
            set_parm(instance.hda, name, None)
        instance.set_output_mode(contract.field(header, "output"))
        instance.configure_streams(header)
        return _cook_and_export(instance, header, import_time)

    def _instance_for(self, header):
        """The Instance of a cook request, made again when its type or its file changed, or when
        its HDA was reloaded since it was made (by this cook or by another instance's)."""
        key = contract.field(header, "instance")
        # A native Houdini node (PolyReduce…) has no HDA file to install.
        path = contract.field(header, "path")
        path = os.path.normpath(path) if path else ""
        if path:
            self.install(path)
        hda_date = self.hda_dates.get(path)
        node_type = contract.field(header, "type")
        instance = self.instances.get(key)
        if instance is not None and (instance.node_type != node_type or instance.path != path
                                     or instance.hda_date != hda_date):
            instance.destroy()
            self.instances.pop(key)
            instance = None
        if instance is None:
            instance = _new_instance(path, node_type, hda_date)
            self.instances[key] = instance
        return instance

    def _set_inputs(self, instance, header, arrays):
        """Give the instance the inputs of a cook request. Returns (missing, missing_chains): the
        inputs it should already have and does not (a restart, a reloaded HDA), and the
        instances above that feed them, which Blender must send again."""
        groups = contract.field(header, "input_groups")
        attributes = contract.field(header, "input_attributes")
        chains = contract.field(header, "chains")
        volumes = contract.field(header, "input_volumes")
        missing = []
        missing_chains = []
        for index, state in enumerate(contract.field(header, "inputs")[: instance.input_count]):
            chained = chains.get(str(index))
            if chained is not None:
                # The Houdini modifier above, linked as it is: VDBs, packed
                # primitives and curves pass without going through Blender.
                source = self.instances.get(chained)
                if source is None or source is instance:
                    missing.append(index)
                    missing_chains.append(chained)
                elif state == contract.CHAIN or instance.chained.get(index) is not source:
                    instance.set_chain(index, source)
                continue
            volume = volumes.get(str(index))
            prefix = contract.INPUT_PREFIX % index
            if state == contract.FULL and volume is not None:
                instance.set_input(index, import_volume(volume["path"], volume["matrix"]))
            elif state == contract.FULL:
                instance.set_input(index, import_mesh(arrays, prefix, groups.get(str(index), []),
                                                      attributes.get(str(index), [])))
            elif state == contract.POSITIONS and index in instance.geometries:
                instance.update_input(index, arrays, prefix, attributes.get(str(index), []))
            elif state == contract.NONE:
                instance.set_input(index, None)
            elif index not in instance.received_inputs:
                missing.append(index)
        return missing, missing_chains


def _new_instance(path, node_type, hda_date):
    """An Instance, or a RequestError when Houdini knows no such node type."""
    try:
        return Instance(path, node_type, hda_date)
    except hou.OperationFailed:
        if hou.nodeType(hou.sopNodeTypeCategory(), node_type) is None:
            raise RequestError("node type %s not found: is its HDA or package installed?" % node_type) from None
        raise


def _set_time(header):
    """Blender's frame and frame rate: frame 1 is time 0 on both sides, so $F, $T and
    keyframes inside the HDA follow Blender's timeline."""
    fps = contract.field(header, "fps")
    if fps and abs(hou.fps() - fps) > 1e-6:
        hou.setFps(fps, False)
    frame = contract.field(header, "frame")
    if frame is not None and hou.frame() != frame:
        hou.setFrame(frame)


def _cook_and_export(instance, header, import_time):
    """Cook the instance and export its result: (answer, output), the answer with its
    timings, `import_time` among them."""
    cook_start = time.perf_counter()
    output = {}
    export_time = 0.0
    try:
        instance.export.cook()
    except hou.OperationFailed:
        pass  # the errors are read back from the nodes just below
    cook_time = time.perf_counter() - cook_start
    # After the cook: menus built from the input, such as group fields, read its cooked geometry.
    answer = instance.describe()
    answer["errors"] = instance.errors()
    # True as soon as anything upstream reads the time ($F, $T, keyframes, a solver).
    answer["time_dependent"] = instance.export.isTimeDependent()
    geometry = instance.export.geometry()
    extras = contract.field(header, "extras")
    if geometry is not None and not answer["errors"]:
        export_start = time.perf_counter()
        fields, output = export_mesh(geometry, extras)
        answer.update(fields)
        answer["renumbered"] = instance.renumbered()
        export_time = time.perf_counter() - export_start
    stream_timings = {}
    if contract.field(header, "output") == contract.NATIVE and "P" in output:
        stream_timings = instance.export_streams(answer, output, contract.field(header, "volume_dir"), extras,
                                                 contract.field(header, "resend"))
        answer["errors"] = instance.errors()
    answer["warnings"] = instance.warnings()
    answer["timings"] = dict(stream_timings, **{"import": import_time, "cook": cook_time, "export": export_time})
    return answer, output


# The commands answered after hello: command -> method (header, arrays) -> (answer, arrays).
HANDLERS = {
    contract.DEFINITIONS: Server.definitions,
    contract.COOK: Server.cook,
    contract.CATALOG: Server.catalog,
    contract.WRITE_VOLUME: Server.write_volume,
    contract.LOGO: Server.logo,
    contract.DELETE: Server.delete,
    contract.CLEAR: Server.clear,
    contract.PING: Server.ping,
}


def hello_refusal(header, token):
    """Why the first message of the client is refused, or None. It must be a hello with
    the token hython was given and the protocol version of this server."""
    if contract.field(header, contract.COMMAND) != contract.HELLO:
        return "token refused"
    undeclared = contract.undeclared(header)
    if undeclared:
        return "hello: undeclared field %s" % ", ".join(undeclared)
    try:
        given = contract.field(header, "token")
    except KeyError:  # no token at all
        given = None
    if given != token:
        return "token refused"
    version = contract.field(header, contract.VERSION)
    if version != contract.PROTOCOL_VERSION:
        return "protocol version %s refused: this server speaks version %d" % (version, contract.PROTOCOL_VERSION)
    return None


def main():
    token = os.environ.get(contract.TOKEN_VARIABLE, "")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(120)
    log.info("%s %d", contract.PORT_LINE, listener.getsockname()[1])  # what the client waits for
    try:
        client, _ = listener.accept()
    except socket.timeout:
        log.info("no client, exiting")
        return
    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.close()

    header, _ = protocol.receive(client)
    refusal = hello_refusal(header, token)
    if refusal is not None:
        log.info("%s, exiting", refusal)
        protocol.send(client, {contract.ERROR: refusal, contract.VERSION: contract.PROTOCOL_VERSION})
        client.close()
        return
    protocol.send(client, {"ok": True, "houdini": hou.applicationVersionString(),
                           contract.VERSION: contract.PROTOCOL_VERSION})

    server = Server()
    while True:
        try:
            header, arrays = protocol.receive(client)
        except (ConnectionError, OSError):
            log.info("client disconnected, exiting")
            for instance in server.instances.values():
                instance.destroy()  # also removes its .vdb files
            return
        try:
            answer, output = server.handle(header, arrays)
        except Exception:  # a faulty HDA must not bring the server down
            answer, output = {contract.ERROR: traceback.format_exc()}, {}
        try:
            protocol.send(client, answer, output)
        except (TypeError, ValueError):  # an answer JSON or the wire cannot take fails before a byte leaves
            protocol.send(client, {contract.ERROR: traceback.format_exc()})


if __name__ == "__main__":
    main()
