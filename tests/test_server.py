"""Tests the hython server without Blender (Blender's Python or any Python with numpy).

    "C:/Program Files/Blender Foundation/Blender 5.2/5.2/python/bin/python.exe" tests/test_server.py
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import support  # noqa: E402
from support import HDA, ROOT  # noqa: E402

client = support.source("hython")  # from the repository, without bpy
contract = support.source("contract")  # the module client reads its version from
protocol = support.source("protocol")
TEST_TYPES = ["bridge::rock", "bridge::rings", "bridge::groups", "bridge::animated", "bridge::scatter",
              "bridge::grass", "bridge::fireflies", "bridge::fog", "bridge::shatter", "bridge::ramps"]
failures = []


def grid(n):
    """Grid of n×n quads in the XZ plane (Houdini space), as input 0."""
    xs, zs = np.meshgrid(np.linspace(-1, 1, n + 1, dtype=np.float32), np.linspace(-1, 1, n + 1, dtype=np.float32))
    positions = np.stack([xs.ravel(), np.zeros(xs.size, np.float32), zs.ravel()], 1)
    i = np.arange(n)
    columns, rows = np.meshgrid(i, i)
    a = (rows * (n + 1) + columns).ravel()
    quads = np.stack([a, a + n + 1, a + n + 2, a + 1], 1).astype(np.int32)
    return {
        "in0_P": positions.ravel(),
        "in0_counts": np.full(len(quads), 4, np.int32),
        "in0_indices": quads.ravel(),
    }


def cook(base, **fields):
    """A cook request: the fields of `base`, then `fields`."""
    return contract.request(contract.COOK, **dict(base, **fields))


def check(condition, message):
    print(("  ok    " if condition else "  FAIL  ") + message)
    if not condition:
        failures.append(message)


def check_hython_launch(hython):
    """In a hython of its own (never two at once: the main connection is closed by then),
    tests/hython_probe.py mounts the extension as hython_entry.py does and reports."""
    print("hython: the extension is a package, not a folder on sys.path")
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH"):  # as the client starts hython
        environment.pop(name, None)
    result = subprocess.run([hython, os.path.join(ROOT, "tests", "hython_probe.py")], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", env=environment, timeout=300,
                            stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    line = next((line for line in result.stdout.splitlines() if line.startswith("BRIDGE_PROBE ")), None)
    check(line is not None, "the probe ran in hython (exit code %d)" % result.returncode)
    if line is None:
        print("  |", (result.stdout + result.stderr).strip()[-2000:])
        return
    report = json.loads(line.split(" ", 1)[1])
    check(not report["on_path"], "the extension's folder is not on sys.path %s" % report["on_path"])
    ours = sorted(name for name, found in report["bare"].items() if found["extension"])
    check(not ours, "no module of the extension imports by its bare name %s" % ours)
    elsewhere = sorted(name for name, found in report["bare"].items() if not found["extension"])
    if elsewhere:
        print("  bare names found outside the extension: %s" % elsewhere)
    package = report["package"]
    expected = {"protocol": package + ".shared.protocol", "contract": package + ".shared.contract",
                "server": package + ".houdini.server",
                "instance": package + ".houdini.instance", "parms": package + ".houdini.parms",
                "geometry": package + ".houdini.geometry", "server_streams": package + ".houdini.streams",
                "server_catalog": package + ".houdini.catalog"}
    check(report["imported"] == expected, "%s import %s" % (", ".join(sorted(expected.values())), report["imported"]))
    driven = report["driven"]
    check(driven.get("size") == [2.0, 2.0, 1.0] and driven.get("sizey_reads") == "sizex",
          "a value sent for Box's Size X leaves Size Y reading it by its channel reference %s" % driven)
    check(driven.get("error") is None and driven.get("text") == "from an expression" and driven.get("driven") is True,
          "a string Houdini drives with an expression is described, evaluated and as driven %s" % driven)
    unknown = report["unknown_type"]
    check(unknown["left"] == 0, "a cook of a node type Houdini does not know leaves no node in /obj (%d left, %s)"
          % (unknown["left"], unknown["error"]))


def refused_hello(hython, token, version):
    """In a hython of its own (never two at once), started as the client starts it: send it
    one hello, then wait for it to exit by itself. Returns (answer, exited, the lines hython
    printed); a hython still running after 60 s is killed."""
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH"):
        environment.pop(name, None)
    environment[contract.TOKEN_VARIABLE] = "the right token"
    process = subprocess.Popen([hython, client.ENTRY_SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, env=environment, text=True, encoding="utf-8",
                               errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    answer, exited, printed = {}, False, []
    try:
        port = None
        for line in process.stdout:
            printed.append(line.rstrip("\n"))
            if line.startswith(contract.PORT_LINE + " "):
                port = int(line.split()[1])
                break
        if port is not None:
            with socket.create_connection(("127.0.0.1", port), timeout=60) as sock:
                fields = {"token": token} if version is None else {"token": token, "version": version}
                protocol.send(sock, contract.request(contract.HELLO, **fields))
                answer, _ = protocol.receive(sock)
        try:
            process.wait(timeout=60)
            exited = True
            printed.extend(line.rstrip("\n") for line in process.stdout)
        except subprocess.TimeoutExpired:
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    return answer, exited, printed


def check_refused_hellos(hython):
    """A hello with another protocol version, none, or a wrong token: the server answers a
    clear error and exits; the client says which versions differ and stops its hython.
    One hython at a time, after the main connection is closed."""
    ours = contract.PROTOCOL_VERSION
    print("hello: another protocol version, no version, a wrong token")
    answer, exited, printed = refused_hello(hython, "the right token", ours + 1)
    error = answer.get("error", "")
    check("version %d" % (ours + 1) in error and "version %d" % ours in error,
          "another version: the server names both (%s)" % error)
    check(answer.get("version") == ours, "the refusal carries the server's version %s" % answer.get("version"))
    check(exited, "another version: hython exits by itself")
    port_lines = [line for line in printed if line.startswith(contract.PORT_LINE)]
    check(len(port_lines) == 1 and re.fullmatch(re.escape(contract.PORT_LINE) + r" \d+", port_lines[0]),
          "the server prints its port alone on a line, as the client reads it %s" % port_lines)
    check("%s, exiting" % error in printed,
          "and its refusal as a line of its own, the text alone %s" % printed[-3:])
    answer, exited, _ = refused_hello(hython, "the right token", None)
    check("version None" in answer.get("error", "") and exited,
          "no version: refused (%s), hython exits by itself" % answer.get("error"))
    answer, exited, printed = refused_hello(hython, "a wrong token", ours)
    check(answer.get("error") == "token refused" and exited and "token refused, exiting" in printed,
          "a wrong token: refused (%s), hython exits by itself and says so" % answer.get("error"))

    print("client: a server of another protocol version")
    contract.PROTOCOL_VERSION = ours + 1  # as if Blender ran this extension's next version
    connection = client.Connection(hython)
    try:
        connection.start()
        message = ""
    except client.ServerError as error:
        message = str(error)
    finally:
        contract.PROTOCOL_VERSION = ours
        connection.stop()
    check("version %d" % (ours + 1) in message and "version %d" % ours in message and "Reinstall" in message,
          "the client names both versions and says to reinstall (%s)" % message)
    check(connection.process is None, "the client stopped the hython it started")
    left = support.hython_processes()
    check(not left, "no hython is left %s" % left)


# The rock of the first cooks, its instance kept for the next ones.
ROCK = {"instance": "rock", "path": HDA, "type": "bridge::rock"}


def check_definitions_and_catalog(connection):
    """The definitions of the test HDA, and the catalog of its folder and the packages."""
    answer, _ = connection.request(contract.request(contract.DEFINITIONS, path=HDA))
    check({d["type"] for d in answer["definitions"]} == set(TEST_TYPES),
          "%d definitions read" % len(TEST_TYPES))

    print("catalog: test folder + packages")
    icon_dir = os.path.join(ROOT, "tests", "screenshots", "icons")
    answer, _ = connection.request(contract.request(
        contract.CATALOG,
        folders=[os.path.dirname(HDA)],
        packages=True,
        icon_dir=icon_dir,
    ))
    tools = answer["tools"]
    groups = sorted({t["group"] for t in tools})
    print("  %d tools in %.1f s, groups: %s" % (len(tools), answer["duration"], groups[:8]))
    from_folder = [t for t in tools if t["group"] == "hda"]
    check(sorted(t["type"] for t in from_folder) == sorted(TEST_TYPES),
          "every HDA of the test folder, group 'hda'")
    check(all(t["icon"] and os.path.exists(t["icon"]) for t in tools), "one PNG icon per tool")
    check(not [t for t in tools if "OPlib" in t["file"]], "no HDA shipped with Houdini (OPlib*.hda)")
    rings = next(t for t in from_folder if t["type"] == "bridge::rings")
    check(rings["max_inputs"] == 0, "rings: generator (no input)")
    with_summary = [t for t in tools if t["summary"]]
    print("  example:", next(((t["label"], t["group"], t["summary"][:80]) for t in with_summary), None))


def check_rock(connection):
    """The rock: its parameters, folders and materials, then a cook of other values."""
    print("rock on a 10×10 grid")
    answer, output = connection.request(cook(ROCK, inputs=["full"]), grid(10))
    names = [p["name"] for p in answer["parms"]]
    check(names == ["height", "noise_size", "subdivisions", "effect", "color", "smooth_normals"],
          "parms: %s" % names)
    color = next(p for p in answer["parms"] if p["name"] == "color")
    check(color["is_color"] and color["folder"] == ["Look"], "color in the Look folder")
    check(len(color["folder_types"]) == 1 and color["folder_types"][0][0] == contract.FOLDER_TAB
          and color["folder_types"][0][1], "the folder Look is a tab, of a named set %s" % color["folder_types"])
    effect = next(p for p in answer["parms"] if p["name"] == "effect")
    check(effect["kind"] == "menu" and effect["value"] == "mountain", "effect menu = mountain")
    check(answer["inputs"] == ["Input 1"], "one input, unlabelled in the HDA: %s" % answer["inputs"])
    check(len(output["counts"]) == 400, "1 subdivision: 400 faces (%d)" % len(output["counts"]))
    check("N" in output and "Cd_point" in output, "normals and color present")
    check(answer.get("materials") == ["/mat/rock_dark", "/mat/rock_light"],
          "two materials %s" % answer.get("materials"))
    check(not answer["errors"], "no error %s" % answer["errors"])
    height = np.ptp(output["P"].reshape(-1, 3)[:, 1])
    check(height > 0.01, "the mountain moves the points (%.3f)" % height)

    answer, output = connection.request(
        cook(ROCK, inputs=["unchanged"], parms={"subdivisions": [2], "smooth_normals": False, "effect": "inflate"})
    )
    check(len(output["counts"]) == 1600, "2 subdivisions: 1600 faces (%d)" % len(output["counts"]))
    check("N" not in output, "normals removed")
    effect = next(p for p in answer["parms"] if p["name"] == "effect")
    check(effect["value"] == "inflate", "effect menu = inflate")
    check(np.allclose(output["Cd_point"].reshape(-1, 3)[0], [0.6, 0.5, 0.4]), "default color")


def check_rings(connection):
    print("rings (generator, packed)")
    answer, output = connection.request(
        contract.request(contract.COOK, instance="rings", path=HDA, type="bridge::rings",
                         inputs=[], parms={"count": [5]})
    )
    check(answer["inputs"] == [], "no input")
    faces_per_torus = len(output["counts"]) // 5
    check(len(output["counts"]) == 5 * faces_per_torus > 0, "5 unpacked tori (%d faces)" % len(output["counts"]))
    check("uv" in output, "UVs present")


def check_groups_and_attributes(connection):
    print("groups and attributes, both ways (bridge::groups)")
    arrays = grid(4)  # 16 quads
    arrays["in0_g0"] = np.array([0, 1, 2, 3], np.int32)          # faces of the first row
    arrays["in0_g1"] = np.array([0, 1, 1, 2], np.int32)          # two edges, as point pairs
    groups_header = {"0": [["Top Row", "prim", "g0"], ["seam", "edge", "g1"]]}
    base_groups = {"instance": "groups", "path": HDA, "type": "bridge::groups"}
    answer, output = connection.request(cook(base_groups, inputs=["full"], input_groups=groups_header), arrays)
    group_parm = next(p for p in answer["parms"] if p["name"] == "group")
    check(group_parm["kind"] == "string" and "Top_Row" in group_parm["suggestions"],
          "the Group parameter stays free text and suggests the input groups %s" % group_parm.get("suggestions"))
    answer, output = connection.request(cook(base_groups, inputs=["unchanged"],
                                             parms={"group": "Top_Row", "distance": [-0.5]}))
    names = {(g["name"], g["class"]) for g in answer["groups"]}
    check({("high", "point"), ("right", "prim"), ("right_edges", "edge"), ("Top_Row", "prim"), ("seam", "edge")}
          <= names,
          "groups come back with their class %s" % sorted(names))
    high = next(g for g in answer["groups"] if g["name"] == "high")
    raised = output["P"].reshape(-1, 3)[output[high["block"]]]
    check(len(raised) > 0 and raised[:, 1].min() > 0.05,
          "the 'high' points are the ones the input group raised (%d)" % len(raised))
    attributes = {(a["name"], a["class"], a["type"], a["size"]) for a in answer["attributes"]}
    check({("height", "point", "float", 1), ("id", "point", "int", 1), ("facing", "prim", "float", 3)}
          <= attributes,
          "numeric attributes come back %s" % sorted(attributes))
    edges = next(g for g in answer["groups"] if g["name"] == "right_edges")
    check(len(output[edges["block"]]) % 2 == 0 and len(output[edges["block"]]) > 0,
          "edge groups come back as point pairs")

    print("attributes from Blender (bridge::groups lets them through)")
    arrays = grid(2)  # 9 points, 4 quads, 16 corners
    arrays["in0_a0"] = np.linspace(0, 1, 9, dtype=np.float32)                 # painted weight
    arrays["in0_a1"] = np.tile(np.float32([0.2, 0.4, 0.6]), 9)                 # active color → Cd
    arrays["in0_a2"] = np.arange(4, dtype=np.int32)                             # face int
    arrays["in0_a3"] = np.tile(np.float32([0.0, 1.0, 0.0]), 16)                 # corner vector
    arrays["in0_a4"] = np.full(4, 0.5, np.float32)                              # face float, "face id" cleaned
    layout = [["weight", "point", "float", 1, "a0", ""], ["Cd", "point", "float", 3, "a1", "color"],
              ["face id", "prim", "int", 1, "a2", ""], ["up", "vertex", "float", 3, "a3", "vector"],
              ["face.id", "prim", "float", 1, "a4", ""]]
    base_attributes = dict(base_groups, instance="attributes")
    answer, output = connection.request(cook(base_attributes, inputs=["full"], parms={"distance": [0.0]},
                                             input_attributes={"0": layout}), arrays)
    back = {a["name"]: a for a in answer["attributes"]}
    check({"weight", "face_id", "up"} <= set(back), "the attributes reach Houdini and come back %s" % sorted(back))
    check(back.get("weight", {}).get("class") == "point"
          and np.allclose(output[back["weight"]["block"]], arrays["in0_a0"]),
          "point weights keep their values")
    check(back.get("face_id", {}).get("type") == "int", "an int stays an int, with a valid Houdini name")
    check(back.get("face_id_2", {}).get("type") == "float" and np.allclose(output[back["face_id_2"]["block"]], 0.5),
          "a second name that cleans the same gets a suffix and its own values")
    check(back.get("up", {}).get("qualifier") == "Vector",
          "a vector gets the Vector type (%s)" % back.get("up", {}).get("qualifier"))
    check("Cd_point" in output and np.allclose(output["Cd_point"].reshape(-1, 3)[0], [0.2, 0.4, 0.6]),
          "the active color is Cd")
    values = dict(arrays)
    values["in0_a0"] = np.full(9, 0.25, np.float32)
    values["in0_P"] = arrays["in0_P"] + np.tile(np.float32([0, 0.1, 0]), 9)
    for key in ("in0_counts", "in0_indices"):
        values.pop(key)
    answer, output = connection.request(cook(base_attributes, inputs=["positions"],
                                             input_attributes={"0": layout}), values)
    back = {a["name"]: a for a in answer["attributes"]}
    check(np.allclose(output[back["weight"]["block"]], 0.25) and output["P"].reshape(-1, 3)[:, 1].min() > 0.05,
          "the light path updates P and the attribute values")


def check_time(connection):
    print("time: the HDA follows Blender's frame")
    base_animated = {"instance": "animated", "path": HDA, "type": "bridge::animated",
                     "inputs": []}
    answer, first = connection.request(cook(base_animated, frame=1, fps=24.0))
    check(answer.get("time_dependent") is True, "an HDA reading $T is time dependent")
    answer, later = connection.request(cook(base_animated, frame=25, fps=24.0))
    check(not np.allclose(first["P"], later["P"]), "another frame gives another shape")
    answer, again = connection.request(cook(base_animated, frame=1, fps=24.0))
    check(np.allclose(first["P"], again["P"]), "the same frame gives the same shape")
    answer, at_30 = connection.request(cook(base_animated, frame=31, fps=30.0))
    check(np.allclose(later["P"], at_30["P"], atol=1e-4), "frame 25 at 24 fps = frame 31 at 30 fps (both 1 s)")
    answer, _ = connection.request(cook(ROCK, inputs=["unchanged"], frame=1, fps=24.0))
    check(answer.get("time_dependent") is False, "the rock is not time dependent")


def check_lost_instance(connection):
    print("lost instance: the server asks for the input")
    connection.request(contract.request(contract.DELETE, instance="rock"))
    answer, _ = connection.request(cook(ROCK, inputs=["unchanged"]))
    check(answer.get("missing_inputs") == [0], "input 0 requested")


def check_timing(connection):
    print("timing: 500×500 grid (250,000 quads), no subdivision")
    for _ in range(2):
        start = time.perf_counter()
        answer, output = connection.request(
            cook(ROCK, inputs=["full"], parms={"subdivisions": [0], "smooth_normals": True}), grid(500)
        )
        total = time.perf_counter() - start
        t = answer["timings"]
        print("  round trip %.3f s | import %.3f  cook %.3f  export %.3f | %d faces"
              % (total, t["import"], t["cook"], t["export"], len(output["counts"])))
    print("timing: same 250,000 quads, positions only")
    positions = grid(500)["in0_P"].reshape(-1, 3).copy()
    for offset in (0.5, 1.0):
        positions[:, 1] = offset
        start = time.perf_counter()
        answer, output = connection.request(cook(ROCK, inputs=["positions"]), {"in0_P": positions.ravel()})
        total = time.perf_counter() - start
        t = answer["timings"]
        print("  round trip %.3f s | import %.3f  cook %.3f  export %.3f"
              % (total, t["import"], t["cook"], t["export"]))
    lowest = output["P"].reshape(-1, 3)[:, 1].min()
    check(lowest > 0.4, "the new positions are used (lowest y %.2f)" % lowest)


def check_undeclared_field(connection):
    print("contract: a request carrying an undeclared field is refused")
    try:  # a typo of sphere_frequency, slipped past contract.request
        connection.request(dict(cook(ROCK, inputs=["unchanged"]), sphere_frequncy=8))
        refused = ""
    except client.ServerError as error:
        refused = str(error)
    check("sphere_frequncy" in refused, "the error names the undeclared field (%s)" % refused)
    answer, _ = connection.request(contract.request(contract.PING))
    check(answer.get("ok") is True, "the server goes on answering")


def request_error(connection, header, arrays=None):
    """The error the server answers to a request, "" when it answers none."""
    try:
        connection.request(header, arrays)
    except client.ServerError as error:
        return str(error)
    return ""


def check_server_errors(connection):
    print("errors: a missing HDA file or node type is one line, with no traceback")
    missing = os.path.join(ROOT, "tests", "hda", "missing.hda")
    error = request_error(connection, cook(dict(ROCK, path=missing, instance="missing file"), inputs=["full"]),
                          grid(2))
    check(error == "HDA file not found: %s" % os.path.normpath(missing), "a missing HDA file: %r" % error)
    error = request_error(connection, cook(dict(path="", instance="missing type", type="bridge::nothing")))
    check(error == "node type bridge::nothing not found: is its HDA or package installed?",
          "a node type Houdini does not know: %r" % error)
    answer, _ = connection.request(contract.request(contract.PING))
    check(answer.get("ok") is True, "the server goes on answering")


def check_degenerate_polygons(connection):
    print("polygons of fewer than 3 points do not reach Blender")
    snippet = ('int first = addpoint(0, {0, 0, 0});\nint second = addpoint(0, {1, 0, 0});\n'
               'addprim(0, "poly", first, second);\naddprim(0, "poly", first);\n')
    _, output = connection.request(cook({"instance": "degenerate", "path": "", "type": "attribwrangle"},
                                        inputs=["full"], parms={"class": ["detail"], "snippet": [snippet]}), grid(2))
    check(list(output["counts"]) == [4] * 4, "the 4 quads alone come back %s" % list(output["counts"]))


def check_input_labels(connection):
    print("a node's inputs keep the labels Houdini gives them")
    answer, _ = connection.request(cook({"instance": "labels", "path": "", "type": "polyreduce::2.0"},
                                        inputs=["full"]), grid(2))
    check(answer["inputs"] == ["Geometry to Reduce", "Rest Geometry", "View Geometry"],
          "PolyReduce's three inputs by their names: %s" % answer["inputs"])


def check_driven_parms(connection):
    print("parameters Houdini drives: Time Shift's frame follows $F until a value is sent for it")
    base = {"instance": "time shift", "path": "", "type": "timeshift"}

    def frame(answer):
        entry = next(p for p in answer["parms"] if p["name"] == "frame")
        return entry["value"][0], entry.get("expression", False)

    answer, _ = connection.request(cook(base, inputs=["full"], frame=10, fps=24.0), grid(2))
    check(frame(answer) == (10.0, True), "described as driven, the frame its value %s" % (frame(answer),))
    answer, _ = connection.request(cook(base, inputs=["unchanged"], frame=20, fps=24.0))
    check(frame(answer) == (20.0, True), "no value sent: it follows $F %s" % (frame(answer),))
    answer, _ = connection.request(cook(base, inputs=["unchanged"], frame=20, fps=24.0, parms={"frame": [5.0]}))
    check(frame(answer) == (5.0, False), "a value sent replaces the expression %s" % (frame(answer),))
    answer, _ = connection.request(cook(base, inputs=["unchanged"], frame=30, fps=24.0, parms={"frame": [5.0]}))
    check(frame(answer) == (5.0, False), "and holds at another frame %s" % (frame(answer),))


def check_small_values(connection):
    print("a value a millionth from the current one is still written, a 32 bit round trip is not")
    base = {"instance": "small values", "path": "", "type": "xform"}

    def translate(answer):
        return next(p for p in answer["parms"] if p["name"] == "t")["value"]

    connection.request(cook(base, inputs=["full"]), grid(2))
    answer, _ = connection.request(cook(base, inputs=["unchanged"], parms={"t": [5e-7, 0.0, 0.0]}))
    check(abs(translate(answer)[0] - 5e-7) < 1e-12, "0 to 5e-7 is written %s" % translate(answer))
    connection.request(cook(base, inputs=["unchanged"], parms={"t": [0.1, 0.0, 0.0]}))
    answer, _ = connection.request(cook(base, inputs=["unchanged"], parms={"t": [float(np.float32(0.1)), 0.0, 0.0]}))
    check(translate(answer)[0] == 0.1, "0.1 sent back in 32 bits keeps Houdini's 0.1 %s" % translate(answer))


def check_reloaded_hda(connection):
    print("reloaded HDA: every instance of it is made again, not only the first to cook")
    path = os.path.join(tempfile.mkdtemp(prefix="bridge_reload_"), "reloaded.hda")
    shutil.copyfile(HDA, path)
    for name in ("reloaded a", "reloaded b"):
        connection.request(cook(dict(ROCK, path=path, instance=name), inputs=["full"]), grid(2))
    later = os.path.getmtime(path) + 10
    os.utime(path, (later, later))  # as when the HDA is saved again from Houdini
    for name in ("reloaded a", "reloaded b"):
        answer, _ = connection.request(cook(dict(ROCK, path=path, instance=name), inputs=["unchanged"]))
        check(answer.get("missing_inputs") == [0],
              "%s, made again, asks for its input %s" % (name, answer.get("missing_inputs")))


def check_server_log(connection):
    print("server log: a value Houdini refuses is a line of its own, the text alone")
    connection.log.clear()  # what the server prints from here on
    answer, _ = connection.request(cook(ROCK, inputs=["unchanged"], parms={"height": "not a number"}))
    printed = list(connection.log)
    refused = [line for line in printed if line.startswith("parameter height: ")]
    check(not answer.get("error") and len(refused) == 1,
          "the cook goes on, and the log holds one line for it %s" % printed[-3:])


def check_renumbered(connection):
    print("renumbered: whether the mesh sent keeps the numbers of the HDA's output (what a chain reads)")
    answer, _ = connection.request(cook(dict(path=HDA, instance="numbers rock", type="bridge::rock"),
                                        inputs=["full"], output=contract.MESH), grid(4))
    check(answer.get("renumbered") is False, "a rock, polygons only: the numbers stay (%r)"
          % answer.get("renumbered"))
    for output_mode in (contract.NATIVE, contract.MESH):
        answer, _ = connection.request(cook(dict(path=HDA, instance="numbers scatter " + output_mode,
                                                 type="bridge::scatter"), inputs=["full"],
                                            output=output_mode), grid(4))
        check(answer.get("renumbered") is True,
              "a scatter of instances, %s: renumbered (%r)" % (output_mode, answer.get("renumbered")))


def check_parm_descriptions(connection):
    print("parameter descriptions: the kinds and fields of contract.PARM_KINDS and PARM_FIELDS")
    descriptions = []
    for node_type in TEST_TYPES:
        answer, _ = connection.request(cook(dict(path=HDA, instance="kinds " + node_type, type=node_type),
                                            inputs=["full"], output=contract.MESH, extras=False), grid(4))
        descriptions.extend(answer["parms"])
    kinds = {entry["kind"] for entry in descriptions}
    check(kinds == set(contract.PARM_KINDS) - {contract.BUTTON},
          "the test HDAs describe every kind but the button %s" % sorted(kinds))
    common = {field for field, (owners, _) in contract.PARM_FIELDS.items()
              if set(owners) == set(contract.PARM_KINDS)}
    undeclared = sorted({"%s.%s" % (entry["kind"], field) for entry in descriptions for field in entry
                         if entry["kind"] not in contract.PARM_FIELDS.get(field, ((),))[0]})
    missing = sorted({"%s.%s" % (entry["kind"], field) for entry in descriptions for field in common
                      if field not in entry})
    check(not undeclared and not missing,
          "each of the %d descriptions has the common fields and only those declared for its kind "
          "(undeclared %s, missing %s)" % (len(descriptions), undeclared, missing))


def check_hide_when_and_folders(connection):
    print("hide-when and disable-when: the states of the values written in the same cook")

    def states(answer):
        return {p["name"]: (p["hidden"], p["disabled"]) for p in answer["parms"]}

    base_extrude = {"instance": "extrude", "path": "", "type": "polyextrude::2.0", "output": contract.MESH}
    front = ("xformspace", "rst")  # HideWhen { xformfront == 0 }
    answer, _ = connection.request(cook(base_extrude, inputs=["full"]), grid(4))
    found = states(answer)
    check(all(found.get(name) == (True, False) for name in front) and found.get("xformfront") == (False, False),
          "Poly Extrude: xformspace and rst hidden while xformfront is off %s" % [found.get(n) for n in front])
    check(found.get("splitgroup") == (False, True), "splitgroup disabled while usesplitgroup is off %s"
          % (found.get("splitgroup"),))
    # Pivot Transform and Pre-Transform: folders with HideWhen { xformfront == 0 }, whose parameters
    # have no condition of their own; Houdini hides them with their folder.
    held = ("pivot", "pivotrotate", "prexform_rst", "prexform_xyz", "prexform_translate", "prexform_rotate",
            "prexform_scale", "prexform_shear")
    check(all(found.get(name, (False,))[0] for name in held),
          "Poly Extrude: the parameters of Pivot Transform and Pre-Transform hidden with their folders %s"
          % [found.get(n) for n in held])
    print("folder types: one [type, tab set] per level of the folder path")
    levels = [(p["name"], level) for p in answer["parms"] for level in p["folder_types"]]
    check(all(len(p["folder_types"]) == len(p["folder"]) for p in answer["parms"])
          and all(level[0] in contract.FOLDER_TYPES and bool(level[1]) == (level[0] == contract.FOLDER_TAB)
                  for _, level in levels),
          "every level has its type, and a tab the name of its set")
    top = {(p["folder"][0], tuple(p["folder_types"][0])) for p in answer["parms"] if p["folder"]}
    tab_sets = {level[1] for label, level in top if level[0] == contract.FOLDER_TAB}
    check({label for label, _ in top} == {"Extrusion", "Spine Control", "Local Control"} and len(tab_sets) == 1,
          "Poly Extrude: its three tabs are one set %s" % sorted(top))
    names = {}  # the Houdini name of each folder, from the labels of its path
    for p in answer["parms"]:
        for depth, level in enumerate(p["folder_types"]):
            names.setdefault(tuple(p["folder"][:depth + 1]), set()).add(level[2] if len(level) > 2 else "")
    check(all(len(found) == 1 and "" not in found for found in names.values())
          and len({next(iter(found)) for found in names.values()}) == len(names),
          "every folder has its own Houdini name, unique in the node %s" % sorted(
              (path[-1], sorted(found)) for path, found in names.items())[:4])
    answer, _ = connection.request(cook(base_extrude, inputs=["unchanged"],
                                        parms={"xformfront": True, "usesplitgroup": True}))
    found = states(answer)
    check(all(found.get(name) == (False, False) for name in front) and found.get("splitgroup") == (False, False),
          "xformfront and usesplitgroup on in the same request: shown and enabled %s"
          % [found.get(n) for n in front + ("splitgroup",)])
    check(all(found.get(name) == (False, False) for name in held),
          "xformfront on: the parameters of both folders shown %s" % [found.get(n) for n in held])
    answer, _ = connection.request(cook(base_extrude, inputs=["unchanged"], parms={"xformfront": False}))
    check(all(states(answer).get(name) == (True, False) for name in front), "xformfront off again: hidden again")
    check(all(states(answer).get(name, (False,))[0] for name in held),
          "xformfront off again: the parameters of both folders hidden again")

    base_sphere = {"instance": "sphere", "path": "", "type": "sphere", "inputs": [], "output": contract.MESH}
    details = ("freq", "rows", "cols")
    disabled = {}
    for value in ("prim", "polymesh", "poly"):
        answer, _ = connection.request(cook(base_sphere, parms={"type": value}))
        disabled[value] = [states(answer)[name][1] for name in details]
    check(disabled == {"prim": [True, True, True], "polymesh": [True, False, False], "poly": [False, True, True]},
          "Sphere: freq, rows and cols disabled as its type says (DisableWhen) %s" % disabled)


def check_code_editors(connection):
    print("code editors: the language of their editorlang tag, and no suggestions")
    languages = {}
    for node_type, name in (("attribwrangle", "snippet"), ("python", "python"), ("font", "text")):
        answer, _ = connection.request(cook({"instance": "code_" + node_type, "path": "", "type": node_type,
                                             "output": contract.MESH}, inputs=["full"]), grid(4))
        entry = next(entry for entry in answer["parms"] if entry["name"] == name)
        languages[name] = (entry.get("editor"), "suggestions" in entry)
    check(languages == {"snippet": ("vex", False), "python": ("python", False), "text": ("text", False)},
          "a wrangle's snippet is vex, a Python SOP's script python, Font's text plain text %s" % languages)


def check_group_fields(connection):
    print("group fields: what their viewport selection takes, from their script_action tag")
    expected = {
        ("groupcreate", "basegroup"): (0, [], "grouptype"),
        ("groupcreate", "edgeptgrp"): (0, [contract.GROUP_POINTS], ""),
        ("polyextrude::2.0", "group"): (0, [contract.GROUP_PRIMITIVES, contract.GROUP_EDGES], ""),
        ("blast", "group"): (0, [], "grouptype"),
        ("groupdelete", "selectiongroup"): (0, [], "selectiongrouptype"),
        ("fuse::2.0", "targetgroup"): (-1, [contract.GROUP_POINTS], ""),  # input 2 when connected
        ("dissolve::2.0", "group"): (0, [contract.GROUP_EDGES], ""),
    }
    found = {}
    for node_type in sorted({node_type for node_type, _ in expected}):
        # The grid has no group: a field Houdini selects in its viewport is a group field anyway.
        answer, _ = connection.request(cook({"instance": "group_" + node_type, "path": "", "type": node_type,
                                             "output": contract.MESH}, inputs=["full"]), grid(4))
        for entry in answer["parms"]:
            if (node_type, entry["name"]) in expected:
                found[(node_type, entry["name"])] = (entry.get("group_input"), entry.get("group_types"),
                                                     entry.get("group_type_parm"))
    check(found == expected, "groupcreate, polyextrude, blast, groupdelete, fuse, dissolve %s"
          % {"%s.%s" % key: value for key, value in found.items() if expected.get(key) != value})
    answer, _ = connection.request(cook({"instance": "group_fuse::2.0", "path": "", "type": "fuse::2.0",
                                         "output": contract.MESH}, inputs=["unchanged"]))
    attribute_field = next(entry for entry in answer["parms"] if entry["name"] == "posattrib")
    check(attribute_field.get("group_input", 0) is None,
          "an attribute field with suggestions has no viewport selection (no selectGroupParm)")


# Run by a Python of its own, as Blender would: start hython, print its process id, wait to be killed.
DOOMED_CLIENT = """
import sys, threading, time
sys.path.insert(0, {tests!r})
import support
connection = support.source("hython").Connection({hython!r})
threading.Thread(target=connection.start, daemon=True).start()
while connection.process is None:
    time.sleep(0.05)
print("HYTHON", connection.process.pid, flush=True)
time.sleep(600)
"""


def _hython_running(pid):
    listed = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"], capture_output=True,
                            text=True, errors="replace").stdout
    return "hython" in listed.lower()


def check_killed_client(hython):
    """A client killed while hython starts (taskkill /F, a crash): hython must end with it.
    Without the job object, it waited two minutes for a connection, holding its licence.
    One hython at a time, after the main connection is closed."""
    print("a client killed while hython starts: hython ends with it")
    if os.name != "nt":
        print("  skipped: the job object is Windows only")
        return
    code = DOOMED_CLIENT.format(tests=os.path.dirname(os.path.abspath(__file__)), hython=hython)
    doomed = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    line = doomed.stdout.readline().split()
    pid = int(line[1]) if len(line) == 2 and line[0] == "HYTHON" else None
    doomed.kill()  # TerminateProcess, as taskkill /F: no unregister, no stop()
    doomed.wait()
    doomed.stdout.close()
    check(pid is not None, "the client started hython")
    if pid is None:
        return
    deadline = time.perf_counter() + 15
    while _hython_running(pid) and time.perf_counter() < deadline:
        time.sleep(0.2)
    running = _hython_running(pid)
    if running:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    check(not running, "no hython is left once its client is killed")


def main():
    hython = client.find_hython()
    print("hython:", hython)
    connection = client.Connection(hython)
    connection.start()
    print("startup: %.1f s, Houdini %s" % (connection.startup_time, connection.houdini_version))
    try:
        check_definitions_and_catalog(connection)
        check_rock(connection)
        check_rings(connection)
        check_groups_and_attributes(connection)
        check_time(connection)
        check_lost_instance(connection)
        check_timing(connection)
        check_undeclared_field(connection)
        check_server_log(connection)
        check_server_errors(connection)
        check_renumbered(connection)
        check_parm_descriptions(connection)
        check_hide_when_and_folders(connection)
        check_code_editors(connection)
        check_group_fields(connection)
        check_driven_parms(connection)
        check_small_values(connection)
        check_degenerate_polygons(connection)
        check_input_labels(connection)
        check_reloaded_hda(connection)  # last: it installs a second copy of the test HDAs
    finally:
        connection.stop()
        messages = [line for line in connection.log if not line.startswith(contract.PORT_LINE)]
        if messages:
            print("server log:")
            for line in messages[:10]:
                print("  |", line)
    check_hython_launch(hython)
    check_refused_hellos(hython)
    check_killed_client(hython)
    print("failures:", len(failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
