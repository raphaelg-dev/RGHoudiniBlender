"""Run by test_server.py in hython, as `hython tests/hython_probe.py`: mounts the addon/
folder as hython_entry.py does before it starts the server, then prints, on one line
starting with BRIDGE_PROBE, what this hython can import, what a cook of a node type
Houdini does not know answers and leaves in /obj, and what a cook does with parameters that
hold an expression (none of which a request can read or set).
"""

import importlib.util
import json
import os
import sys

import hou
import support  # hython put this script's folder, tests/, first on sys.path

# Bare names the extension's files and folders would have if its folder were on sys.path:
# what its root holds (hython_entry.py and the shared/, houdini/ and blender/ packages).
BARE_NAMES = ("blender", "shared", "houdini", "hython_entry")
# The modules hython imports to run the server (keys of support.MODULES).
SERVER_MODULES = ("protocol", "contract", "server", "instance", "parms", "geometry", "server_streams", "server_catalog")


def _inside_extension(path):
    folder = os.path.normcase(os.path.abspath(support.SOURCE_DIR))
    path = os.path.normcase(os.path.abspath(path or os.curdir))
    return path == folder or path.startswith(folder + os.sep)


def _unknown_type():
    """The error a cook of a node type Houdini does not know answers, and the nodes it leaves in /obj."""
    server, contract = support.hython_module("server"), support.hython_module("contract")
    before = len(hou.node("/obj").children())
    header = contract.request(contract.COOK, instance="unknown", type="bridge::nothing")
    try:
        answer, _ = server.Server().handle(header, {})
        error = answer.get(contract.ERROR)
    except Exception as exception:  # reported, not raised: the suite decides
        error = "raised %r" % exception
    return {"error": error, "left": len(hou.node("/obj").children()) - before}


def _driven():
    """A channel reference (Box's Size Y reading Size X) and a string expression (Font's Text),
    set by hand between two cooks: what the second cook writes and describes."""
    server, contract = support.hython_module("server"), support.hython_module("contract")
    cooks = server.Server()
    report = {}
    try:
        cooks.handle(contract.request(contract.COOK, instance="box", type="box"), {})
        box = cooks.instances["box"].hda
        box.parm("sizey").setExpression('ch("sizex")')
        cooks.handle(contract.request(contract.COOK, instance="box", type="box", parms={"size": [2.0, 1.0, 1.0]}), {})
        report["size"] = [parm.eval() for parm in box.parmTuple("size")]
        report["sizey_reads"] = box.parm("sizey").getReferencedParm().name()
        cooks.handle(contract.request(contract.COOK, instance="font", type="font"), {})
        cooks.instances["font"].hda.parm("text").setExpression('"from an expression"')
        answer, _ = cooks.handle(contract.request(contract.COOK, instance="font", type="font"), {})
        text = next((entry for entry in answer.get("parms", []) if entry["name"] == "text"), {})
        report.update(error=answer.get(contract.ERROR), text=text.get("value"), driven=text.get("expression"))
    except Exception as exception:  # reported, not raised: the suite decides
        report["error"] = "raised %r" % exception
    return report


def main():
    package = support.mount_like_hython()
    report = {"package": package, "on_path": [entry for entry in sys.path if _inside_extension(entry)],
              "bare": {}, "imported": {}}
    for name in BARE_NAMES:
        spec = importlib.util.find_spec(name)
        if spec is not None:
            locations = [spec.origin or ""] + list(spec.submodule_search_locations or [])
            report["bare"][name] = {"origin": spec.origin,
                                    "extension": any(_inside_extension(place) for place in locations if place)}
    for name in SERVER_MODULES:
        try:
            report["imported"][name] = support.hython_module(name).__name__
        except Exception as error:  # reported, not raised: the suite decides
            report["imported"][name] = "error: %r" % error
    report["unknown_type"] = _unknown_type()
    report["driven"] = _driven()
    print("BRIDGE_PROBE " + json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
