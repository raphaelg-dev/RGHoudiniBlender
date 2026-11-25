"""The protocol between Blender and the hython server, imported by both.

shared/protocol.py frames a message (a JSON header, then binary blocks); this file says
what a header holds. REQUESTS lists the fields of each command's request, with a default
(or REQUIRED) and a one-line specification; ANSWERS lists the keys of its answer. Blender
builds every request with request(), which refuses an undeclared field; the server reads
every field with field(), which gives the declared default. The table of
docs/dev/protocol.md follows this file (tests/unit/test_contract.py).
"""

import copy

# Bumped whenever one side would misread the other. The client sends its version in
# hello; a server of another version answers an error and exits, and the client
# stops it and says so.
PROTOCOL_VERSION = 2

# --- commands -------------------------------------------------------------------------

HELLO = "hello"
DEFINITIONS = "definitions"
COOK = "cook"
CATALOG = "catalog"
WRITE_VOLUME = "write_volume"
LOGO = "logo"
DELETE = "delete"
CLEAR = "clear"
PING = "ping"
COMMANDS = (HELLO, DEFINITIONS, COOK, CATALOG, WRITE_VOLUME, LOGO, DELETE, CLEAR, PING)

# --- reserved header keys, never fields -----------------------------------------------

COMMAND = "command"  # the command of a request, put there by request()
BLOCKS = "blocks"  # [[name, numpy dtype, byte count], ...] of the binary blocks, put there by shared/protocol.py
RESERVED = (COMMAND, BLOCKS)

# --- input states: one per input of a cook, in its "inputs" field ---------------------

FULL = "full"  # the whole mesh is attached (blocks in<i>_*), or a Volume object (input_volumes)
POSITIONS = "positions"  # values only (P, uv, attributes) on the topology already received
UNCHANGED = "unchanged"  # what the server received last time stands
NONE = "none"  # nothing is plugged in
CHAIN = "chain"  # the Houdini modifier above (chains), to link again
INPUT_STATES = (FULL, POSITIONS, UNCHANGED, NONE, CHAIN)

# --- output modes: the "output" field of a cook ---------------------------------------

NATIVE = "native"  # instances, curves, points and volumes in their own streams
MESH = "mesh"  # everything realized as one mesh
OUTPUT_MODES = (NATIVE, MESH)

# --- native streams -------------------------------------------------------------------
# Keys of the cook answer, in the order both sides go through them. Also stored in .blend
# files (the modifier's streams_json), so never renamed.

INSTANCES = "instances"  # packed primitives and quadric spheres, with their prototypes
CURVES = "curves"  # open curves
POINTS = "points"  # the points no primitive uses
VOLUME = "volume"  # VDB grids, in a .vdb file
STREAMS = (INSTANCES, CURVES, POINTS, VOLUME)

# --- parameter kinds ------------------------------------------------------------------
# The "kind" of a parameter description (the "parms" of a cook answer). Also stored in
# .blend files (HoudiniBridgeParm.kind), so never renamed. houdini/parms.py (kind_of) maps
# a Houdini template to its kind.

FLOAT = "float"  # a Float template: one number per component (Float, Vector or Color input)
INT = "int"  # an Int template without a one-component menu, or the count of a multiparm
TOGGLE = "toggle"  # a Toggle: true or false
STRING = "string"  # a String template: text; free text with suggestions for a group field
MENU = "menu"  # one item of a menu: a Menu template, or an Int or String one with a one-component menu
BUTTON = "button"  # a Button: no value, pressed through the "buttons" of a cook
RAMP = "ramp"  # a float or color ramp: keys, values and one interpolation per key
PARM_KINDS = (FLOAT, INT, TOGGLE, STRING, MENU, BUTTON, RAMP)

# The fields of a parameter description: field -> (the kinds that send it, specification).
# Every kind sends the first nine; a multiparm is no kind but a structure of the walk: its
# count is an int with "multiparm", its instances follow as folders.
PARM_FIELDS = {
    "name": (PARM_KINDS, "the Houdini name; in a multiparm instance, each # replaced by its index"),
    "label": (PARM_KINDS, "the label, or the name when there is none"),
    "folder": (PARM_KINDS, "the folder path, a list of labels (a multiparm instance is a folder '<title> <n>')"),
    "folder_types": (PARM_KINDS, "one [type, tab set, name] per level of folder: type one of FOLDER_TYPES, tab "
                     "set the name of the set a tab belongs to (two sets may follow each other), else empty, "
                     "name the folder's Houdini name, unique in the node, which tells apart two folders of "
                     "one label (a multiparm instance: '<count> <n>')"),
    "help": (PARM_KINDS, "the help text, empty when there is none"),
    "size": (PARM_KINDS, "the number of components"),
    "hidden": (PARM_KINDS, "hidden now (the template, or its hide-when condition)"),
    "disabled": (PARM_KINDS, "disabled now (its disable-when condition)"),
    "kind": (PARM_KINDS, "one of PARM_KINDS"),
    "value": ((FLOAT, INT, TOGGLE, STRING, MENU, RAMP),
              "the current value: a list of numbers (float, int), a bool, the unexpanded text (the evaluated "
              "text when Houdini drives it), the token of the item (menu), {basis, keys, values} (ramp)"),
    "expression": (PARM_KINDS, "true when Houdini drives a component with keyframes or an expression (a channel "
                   "reference is one): Blender does not send its value until it changes there, and a value "
                   "sent replaces the expression of each component it changes"),
    "min": ((FLOAT, INT), "the minimum of the slider range"),
    "max": ((FLOAT, INT), "the maximum of the slider range"),
    "min_strict": ((FLOAT, INT), "the minimum cannot be crossed (the lock of the parameter editor)"),
    "max_strict": ((FLOAT, INT), "the maximum cannot be crossed"),
    "is_color": ((FLOAT,), "shown as a color"),
    "multiparm": ((INT,), "true: the count of a multiparm"),
    "is_file": ((STRING,), "a file path (a string without suggestions, not a code editor)"),
    "editor": ((STRING,), "sent only for a code editor (the editor tag of its template): the language of its "
               "editorlang tag in lowercase (vex, python, opencl...), 'text' when the tag names none; such a "
               "string sends no suggestions (a wrangle's presets are code)"),
    "suggestions": ((STRING,), "a group field: the menu tokens, values offered while the text stays free"),
    "toggle": ((STRING,), "with suggestions: they add up (a StringToggle menu)"),
    "group_input": ((STRING,), "with suggestions: the input whose elements its viewport selection numbers, "
                    "as the script_action tag writes it (-n: input n when it is connected, else the first); "
                    "None when the field cannot be selected in the viewport"),
    "group_types": ((STRING,), "with suggestions: the element types (GROUP_TYPES) its selection takes, "
                    "the first one first; empty when the tag does not say"),
    "group_type_parm": ((STRING,), "with suggestions: the parameter whose menu token gives the element type "
                        "(GROUP_TYPE_TOKENS), empty when there is none"),
    "menu": ((MENU,), "[[token, label], ...] of the items"),
    "ramp": ((RAMP,), "float or color"),
}

# --- folder types ---------------------------------------------------------------------
# The first item of each level of a description's "folder_types": what the parameter pane
# draws (hou.folderType, reduced to what Blender can draw). Also stored in .blend files
# (HoudiniBridgeParm.folder_types), so never renamed.

FOLDER_COLLAPSIBLE = "collapsible"  # a folder that opens and closes
FOLDER_SIMPLE = "simple"  # a group box with its title (an import block too)
FOLDER_TAB = "tab"  # one tab of a set of tabs (radio folders too): one tab shows at a time
FOLDER_INSTANCE = "instance"  # one instance of a multiparm block (any of the three kinds of blocks)
FOLDER_TYPES = (FOLDER_COLLAPSIBLE, FOLDER_SIMPLE, FOLDER_TAB, FOLDER_INSTANCE)

# --- element types of a group field ---------------------------------------------------
# Its "group_types", as soputils.selectGroupParm names them (hou.geometryType), and the
# tokens of a group type menu that give each one, in the order
# soputils.groupTypeParmFromGeometryType prefers them. Another token ("guess") gives none.

GROUP_POINTS = "points"
GROUP_PRIMITIVES = "primitives"
GROUP_EDGES = "edges"
GROUP_VERTICES = "vertices"
GROUP_BREAKPOINTS = "breakpoints"
GROUP_TYPES = (GROUP_POINTS, GROUP_PRIMITIVES, GROUP_EDGES, GROUP_VERTICES, GROUP_BREAKPOINTS)
GROUP_TYPE_TOKENS = {
    "points": GROUP_POINTS, "point": GROUP_POINTS,
    "prims": GROUP_PRIMITIVES, "prim": GROUP_PRIMITIVES, "primitives": GROUP_PRIMITIVES, "primitive": GROUP_PRIMITIVES,
    "edges": GROUP_EDGES, "edge": GROUP_EDGES,
    "breakpoints": GROUP_BREAKPOINTS, "breakpoint": GROUP_BREAKPOINTS,
    "vertices": GROUP_VERTICES, "vertex": GROUP_VERTICES,
}

# --- values both sides need -----------------------------------------------------------

MAX_INPUTS = 4  # inputs of an HDA a Houdini modifier offers, at most
SPHERE_FREQUENCY = 4  # detail of the polygon sphere quadric spheres become (the preference's default)
SPHERE_FREQUENCY_RANGE = (1, 12)  # the details the server accepts, and the limits of the preference
INPUT_PREFIX = "in%d_"  # the blocks of input i: in<i>_P, in<i>_counts, in<i>_indices, in<i>_uv, in<i>_g<n>, in<i>_a<n>
PORT_LINE = "HOUDINI_BRIDGE_PORT"  # the server prints "HOUDINI_BRIDGE_PORT <port>" on stdout once it listens
TOKEN_VARIABLE = "HOUDINI_BRIDGE_TOKEN"  # environment variable of hython holding the token hello returns

# --- control keys of the answers ------------------------------------------------------

ERROR = "error"  # any answer: the request failed (traceback, refused request, unknown command)
VERSION = "version"  # hello: the protocol version, in the request and in the answer
MISSING_INPUTS = "missing_inputs"  # cook: the server lost these inputs, send them in full
MISSING_CHAINS = "missing_chains"  # cook: the instances of the chained modifiers it lost

# --- requests: command -> {field: (default or REQUIRED, specification)} ---------------

REQUIRED = object()  # the field has no default: a request without it is refused

REQUESTS = {
    HELLO: {
        "token": (REQUIRED, "the random token passed to hython in TOKEN_VARIABLE"),
        VERSION: (None, "PROTOCOL_VERSION of the client; another one, or none, is refused"),
    },
    DEFINITIONS: {
        "path": (REQUIRED, "an HDA file, installed by the server"),
    },
    COOK: {
        "instance": (REQUIRED, "the server instance, one per Houdini modifier (the session uid of its node group)"),
        "path": ("", "the HDA file; empty for a native Houdini node, which has no file"),
        "type": (REQUIRED, "the node type to cook"),
        "inputs": ([], "one input state per input (INPUT_STATES)"),
        "parms": ({}, "parameter values by name"),
        "buttons": ([], "names of the buttons to press"),
        "input_groups": ({}, "input index (text) -> [[name, class, block]] of the groups sent in full"),
        "input_attributes": ({}, "input index (text) -> [[name, class, type, size, block, qualifier]]"),
        "chains": ({}, "input index (text) -> the instance whose Houdini result feeds it"),
        "input_volumes": ({}, "input index (text) -> {path, matrix} of a Volume object read from its .vdb"),
        "extras": (True, "bring back every numeric attribute and every group of the result"),
        "frame": (None, "Blender's frame, subframe included; none leaves Houdini's frame as it is"),
        "fps": (None, "Blender's frames per second; none leaves Houdini's as it is"),
        "output": (MESH, "the output mode (OUTPUT_MODES)"),
        "volume_dir": ("", "folder of the .vdb files of the volume stream; empty: no volume stream"),
        "sphere_frequency": (SPHERE_FREQUENCY, "detail of the polygon sphere quadric spheres become"),
        "resend": (False, "send every instance prototype again, even those Blender should have"),
    },
    CATALOG: {
        "folders": ([], "folders whose HDA files are read"),
        "packages": (True, "also read the HDAs of the Houdini packages"),
        "icon_dir": (REQUIRED, "folder of the PNG icons"),
        "native": (True, "also list the native Houdini SOP nodes"),
        "all_versions": (False, "keep every version of a tool, not only the latest"),
    },
    WRITE_VOLUME: {
        "source": (REQUIRED, "the .vdb file to copy"),
        "destination": (REQUIRED, "the .vdb file to write"),
        "matrix": (REQUIRED, "16 floats, row vectors: the transform of the copy"),
    },
    LOGO: {
        "path": (REQUIRED, "the PNG file of the Houdini logo to write"),
    },
    DELETE: {
        "instance": (REQUIRED, "the server instance to destroy"),
    },
    CLEAR: {},
    PING: {},
}

# --- answers: command -> {key: specification}; ERROR may come back from any -----------

ANSWERS = {
    HELLO: {
        "ok": "the token and the version are accepted",
        "houdini": "Houdini's version",
        VERSION: "PROTOCOL_VERSION of the server, also when it refuses",
    },
    DEFINITIONS: {
        "definitions": "per definition of the file: type, label, category, supported, max_inputs",
    },
    COOK: {
        "label": "the HDA's label",
        "version": "the HDA's version",
        "inputs": "the labels of its inputs",
        "parms": "the descriptions of its parameters",
        "errors": "the error messages of the cook (their causes only)",
        "warnings": "the warnings of the HDA",
        "timings": "seconds spent importing, cooking, exporting, and per stream",
        "time_dependent": "whether the result may change with the frame",
        "renumbered": "whether the mesh sent is numbered differently from the HDA's own output, which a chained "
                      "modifier below reads (primitives unpacked, converted or left out, loose points left out)",
        "materials": "material paths, indexed by the material block",
        "attributes": "the numeric attributes of the result and their blocks",
        "groups": "the groups of the result and their blocks",
        INSTANCES: "native mode: the instance stream",
        CURVES: "native mode: the curve stream",
        POINTS: "native mode: the loose points",
        VOLUME: "native mode: the .vdb file of the volume stream",
        MISSING_INPUTS: "instead of a result: the inputs to send again in full",
        MISSING_CHAINS: "with missing_inputs: the chained instances the server does not have",
    },
    CATALOG: {
        "tools": "one entry per tool: file, type, label, group, summary, input counts, icon",
        "duration": "seconds spent",
    },
    WRITE_VOLUME: {"ok": "the destination file exists"},
    LOGO: {"ok": "the PNG file exists"},
    DELETE: {"ok": "always true"},
    CLEAR: {"ok": "always true"},
    PING: {"ok": "always true"},
}
ANY_ANSWER = {ERROR: "the request failed: a traceback, a refused request or an unknown command"}


def request(command, **fields):
    """The header of a request: the command, then the fields as given (no default is added).
    Raises ValueError for an unknown command, a field it does not declare, a reserved key
    or a missing required field."""
    declared = REQUESTS.get(command)
    if declared is None:
        raise ValueError("unknown command: %s" % command)
    undeclared_fields = sorted(name for name in fields if name not in declared)
    if undeclared_fields:
        raise ValueError("%s: undeclared field %s" % (command, ", ".join(undeclared_fields)))
    missing = [name for name, (default, _) in declared.items() if default is REQUIRED and name not in fields]
    if missing:
        raise ValueError("%s: missing field %s" % (command, ", ".join(missing)))
    return dict({COMMAND: command}, **fields)


def undeclared(header):
    """The keys of a received header its command does not declare, sorted (the reserved
    keys are never counted)."""
    declared = REQUESTS.get(header.get(COMMAND), {})
    return sorted(name for name in header if name not in declared and name not in RESERVED)


def field(header, name):
    """A field of a received request: its value, or the declared default (a copy, so that
    a default list or dict is never shared). `field(header, COMMAND)` is the command, or
    None. Raises KeyError for a name the command does not declare, and for a required
    field the request does not carry."""
    if name == COMMAND:
        return header.get(COMMAND)
    command = header.get(COMMAND)
    declared = REQUESTS.get(command, {})
    if name not in declared:
        raise KeyError("%s: undeclared field %s" % (command, name))
    if name in header:
        return header[name]
    default = declared[name][0]
    if default is REQUIRED:
        raise KeyError("%s: missing field %s" % (command, name))
    return copy.deepcopy(default)
