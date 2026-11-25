"""The protocol contract (shared/contract.py): building and reading requests, the code that
must go through it, and the table of docs/dev/protocol.md written from it."""

import ast
import os
import re
import socket
import threading
import unittest

import numpy as np

from tests import support

contract = support.source("contract")
protocol = support.source("protocol")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADDON = os.path.join(ROOT, "addon")
CONTRACT_FILE = "shared/contract.py"
PROTOCOL_DOCUMENT = os.path.join(ROOT, "docs", "dev", "protocol.md")


class Values(unittest.TestCase):
    """The strings on the wire are the ones the two sides have always exchanged."""

    def test_commands(self):
        self.assertEqual(contract.COMMANDS, ("hello", "definitions", "cook", "catalog", "write_volume", "logo",
                                             "delete", "clear", "ping"))

    def test_input_states_and_output_modes(self):
        self.assertEqual(contract.INPUT_STATES, ("full", "positions", "unchanged", "none", "chain"))
        self.assertEqual(contract.OUTPUT_MODES, ("native", "mesh"))

    def test_streams(self):
        self.assertEqual(contract.STREAMS, ("instances", "curves", "points", "volume"))
        self.assertEqual((contract.INSTANCES, contract.CURVES, contract.POINTS, contract.VOLUME), contract.STREAMS)

    def test_parameter_kinds_and_fields(self):
        self.assertEqual(contract.PARM_KINDS, ("float", "int", "toggle", "string", "menu", "button", "ramp"))
        self.assertEqual((contract.FLOAT, contract.INT, contract.TOGGLE, contract.STRING, contract.MENU,
                          contract.BUTTON, contract.RAMP), contract.PARM_KINDS)
        self.assertEqual(list(contract.PARM_FIELDS), [
            "name", "label", "folder", "folder_types", "help", "size", "hidden", "disabled", "kind", "value",
            "expression", "min", "max", "min_strict", "max_strict", "is_color", "multiparm", "is_file", "editor",
            "suggestions", "toggle", "group_input",
            "group_types", "group_type_parm", "menu", "ramp"])
        for field, (kinds, specification) in contract.PARM_FIELDS.items():
            self.assertTrue(kinds and set(kinds) <= set(contract.PARM_KINDS) and specification, field)

    def test_folder_types(self):
        self.assertEqual(contract.FOLDER_TYPES, ("collapsible", "simple", "tab", "instance"))
        self.assertEqual((contract.FOLDER_COLLAPSIBLE, contract.FOLDER_SIMPLE, contract.FOLDER_TAB,
                          contract.FOLDER_INSTANCE), contract.FOLDER_TYPES)

    def test_shared_values(self):
        self.assertEqual(contract.PROTOCOL_VERSION, 2)
        self.assertEqual(contract.MAX_INPUTS, 4)
        self.assertEqual(contract.SPHERE_FREQUENCY, 4)
        self.assertEqual(contract.SPHERE_FREQUENCY_RANGE, (1, 12))
        low, high = contract.SPHERE_FREQUENCY_RANGE
        self.assertTrue(low <= contract.SPHERE_FREQUENCY <= high)
        self.assertEqual(contract.INPUT_PREFIX % 2, "in2_")
        self.assertEqual((contract.PORT_LINE, contract.TOKEN_VARIABLE), ("HOUDINI_BRIDGE_PORT", "HOUDINI_BRIDGE_TOKEN"))
        self.assertEqual((contract.ERROR, contract.MISSING_INPUTS, contract.MISSING_CHAINS),
                         ("error", "missing_inputs", "missing_chains"))
        self.assertEqual(contract.RESERVED, ("command", "blocks"))


class Declarations(unittest.TestCase):
    def test_every_command_declares_its_fields_and_answer(self):
        self.assertEqual(sorted(contract.REQUESTS), sorted(contract.COMMANDS))
        self.assertEqual(sorted(contract.ANSWERS), sorted(contract.COMMANDS))
        for command, fields in contract.REQUESTS.items():
            for name, spec in fields.items():
                self.assertEqual(len(spec), 2, "%s.%s: (default, specification)" % (command, name))
                self.assertTrue(isinstance(spec[1], str) and spec[1], "%s.%s has its specification" % (command, name))
                self.assertNotIn(name, contract.RESERVED, "%s.%s is a reserved key" % (command, name))
            for name, text in contract.ANSWERS[command].items():
                self.assertTrue(isinstance(text, str) and text, "answer %s.%s has its specification" % (command, name))

    def test_the_cook_fields_cooks_send_today(self):
        self.assertEqual(sorted(contract.REQUESTS[contract.COOK]), sorted([
            "instance", "path", "type", "inputs", "parms", "buttons", "input_groups", "input_attributes", "chains",
            "input_volumes", "extras", "frame", "fps", "output", "volume_dir", "sphere_frequency", "resend"]))
        self.assertIs(contract.REQUESTS[contract.COOK]["instance"][0], contract.REQUIRED)
        self.assertEqual(contract.REQUESTS[contract.COOK]["output"][0], contract.MESH)
        self.assertEqual(contract.REQUESTS[contract.COOK]["sphere_frequency"][0], contract.SPHERE_FREQUENCY)

    def test_control_keys_are_declared(self):
        self.assertIn(contract.MISSING_INPUTS, contract.ANSWERS[contract.COOK])
        self.assertIn(contract.MISSING_CHAINS, contract.ANSWERS[contract.COOK])
        self.assertIn(contract.VERSION, contract.ANSWERS[contract.HELLO])
        self.assertIn(contract.VERSION, contract.REQUESTS[contract.HELLO])
        self.assertIn(contract.ERROR, contract.ANY_ANSWER)


class Request(unittest.TestCase):
    def test_the_header_is_the_command_then_the_fields_as_given(self):
        header = contract.request(contract.COOK, instance="7", type="bridge::rock", inputs=["full"])
        self.assertEqual(list(header.items()), [("command", "cook"), ("instance", "7"), ("type", "bridge::rock"),
                                                ("inputs", ["full"])])
        self.assertEqual(contract.request(contract.CLEAR), {"command": "clear"})

    def test_undeclared_field_refused(self):
        with self.assertRaisesRegex(ValueError, "sphere_frequncy"):
            contract.request(contract.COOK, instance="7", type="t", sphere_frequncy=8)

    def test_missing_required_field_refused(self):
        with self.assertRaisesRegex(ValueError, "instance"):
            contract.request(contract.COOK, type="bridge::rock")
        with self.assertRaisesRegex(ValueError, "path"):
            contract.request(contract.DEFINITIONS)

    def test_unknown_command_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown command: explode"):
            contract.request("explode")

    def test_reserved_keys_are_no_fields(self):
        for name in contract.RESERVED:
            with self.assertRaises((ValueError, TypeError), msg=name):
                contract.request(contract.PING, **{name: 1})


class Field(unittest.TestCase):
    def test_value_or_declared_default(self):
        header = {"command": "cook", "instance": "7", "type": "t", "fps": 24.0}
        self.assertEqual(contract.field(header, "fps"), 24.0)
        self.assertIsNone(contract.field(header, "frame"))
        self.assertEqual(contract.field(header, "output"), "mesh")
        self.assertEqual(contract.field(header, "sphere_frequency"), contract.SPHERE_FREQUENCY)
        self.assertIs(contract.field(header, "extras"), True)
        self.assertEqual(contract.field(header, "inputs"), [])
        self.assertEqual(contract.field(header, "command"), "cook")
        self.assertIsNone(contract.field({}, "command"))

    def test_mutable_default_is_a_copy(self):
        header = {"command": "cook", "instance": "7", "type": "t"}
        contract.field(header, "inputs").append("full")
        contract.field(header, "parms")["height"] = 2
        self.assertEqual(contract.field(header, "inputs"), [])
        self.assertEqual(contract.field(header, "parms"), {})
        self.assertEqual(contract.REQUESTS["cook"]["inputs"][0], [])

    def test_undeclared_name_raises(self):
        with self.assertRaisesRegex(KeyError, "sphere_frequncy"):
            contract.field({"command": "cook"}, "sphere_frequncy")
        with self.assertRaises(KeyError):
            contract.field({"command": "ping"}, "instance")  # declared by another command only

    def test_missing_required_field_raises(self):
        with self.assertRaisesRegex(KeyError, "instance"):
            contract.field({"command": "cook"}, "instance")


class Undeclared(unittest.TestCase):
    def test_names_the_undeclared_keys_and_tolerates_blocks(self):
        header = {"command": "cook", "instance": "7", "type": "t", "blocks": [], "sphere_frequncy": 8, "zz": 1}
        self.assertEqual(contract.undeclared(header), ["sphere_frequncy", "zz"])
        self.assertEqual(contract.undeclared({"command": "ping", "blocks": [["P", "<f4", 0]]}), [])

    def test_a_built_request_survives_the_wire(self):
        near, far = socket.socketpair()
        try:
            header = contract.request(contract.COOK, instance="7", type="t", inputs=["full"], output="native")
            sender = threading.Thread(target=protocol.send, args=(near, header, {"in0_P": np.zeros(3, np.float32)}))
            sender.start()
            received, arrays = protocol.receive(far)
            sender.join()
        finally:
            near.close()
            far.close()
        self.assertEqual(received, header)
        self.assertEqual(contract.undeclared(received), [])
        self.assertEqual(list(arrays), ["in0_P"])


# --- the code that must go through the contract ---------------------------------------

WIRE_VALUES = set(contract.COMMANDS) | set(contract.INPUT_STATES) | set(contract.OUTPUT_MODES)
STATES_AND_MODES = set(contract.INPUT_STATES) | set(contract.OUTPUT_MODES)
CONTROL_KEYS = {contract.MISSING_INPUTS, contract.MISSING_CHAINS}


def _python_files():
    for directory, subfolders, files in os.walk(ADDON):
        subfolders[:] = sorted(name for name in subfolders if name != "__pycache__")
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(directory, name)
                yield os.path.relpath(path, ADDON).replace(os.sep, "/"), path


def _strings(node):
    """The string constants of a node that is a constant, a tuple, a list or a set."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [value for element in node.elts for value in _strings(element)]
    return []


def _calls_contract(node, function):
    """A call of contract.<function>(...)."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == function
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "contract")


def _is_header(node):
    return isinstance(node, ast.Name) and node.id == "header"


def literals_outside_the_contract():
    """["file:line what"] for every place of addon/ that writes the protocol by hand."""
    found = []
    for name, path in _python_files():
        if name == CONTRACT_FILE:
            continue
        with open(path, encoding="utf-8") as file:
            found.extend(scan(name, ast.parse(file.read(), filename=path)))
    return sorted(set(found))


def scan(name, tree):
    """["file:line what"] of one file of addon/ (`name` is its path inside addon/)."""
    found = []
    server_side = name.startswith("houdini/")
    # The name of a field read with contract.field() may equal a value ("native" is a catalog field).
    field_names = {id(node.args[1]) for node in ast.walk(tree) if _calls_contract(node, "field") and len(node.args) > 1}
    for node in ast.walk(tree):
        where = "%s:%d" % (name, getattr(node, "lineno", 0))
        if isinstance(node, ast.Dict) and any(_strings(key) == [contract.COMMAND] for key in node.keys if key):
            found.append(where + " a request written as a dict")
        if isinstance(node, ast.Compare):
            for operand in [node.left, *node.comparators]:
                for value in _strings(operand):
                    if value in WIRE_VALUES or value in CONTROL_KEYS or value == contract.ERROR:
                        found.append(where + " compares with %r" % value)
            if server_side and any(_is_header(operand) for operand in node.comparators):
                found.append(where + " looks into header")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in field_names:
            if node.value in STATES_AND_MODES or node.value in CONTROL_KEYS:
                found.append(where + " writes %r" % node.value)
        if _calls_contract(node, "request") and node.args and _strings(node.args[0]):
            found.append(where + " names a command by its string")
        if server_side and isinstance(node, ast.Attribute) and node.attr == "get" and _is_header(node.value):
            found.append(where + " header.get(")
        if server_side and isinstance(node, ast.Subscript) and _is_header(node.value):
            found.append(where + " header[")
    return found


class ThroughTheContract(unittest.TestCase):
    """No tolerance: commands, input states, output modes and control keys are written in
    shared/contract.py only, and the server reads a request through contract.field()."""

    def test_no_protocol_literal_outside_the_contract(self):
        self.assertEqual(literals_outside_the_contract(), [])

    def test_the_scan_sees_each_kind(self):
        sample = "\n".join([
            'post({"command": "delete", "instance": key})',
            'if mode in ("native", "other"): pass',
            'if "missing_inputs" in answer: pass',
            'states.append("unchanged")',
            'contract.request("ping")',
            'value = header.get("inputs", [])',
            'value = header["inputs"]',
            'if "frame" in header: pass',
            'native = contract.field(header, "native")',
        ])
        found = scan("houdini/sample.py", ast.parse(sample))
        for line, what in enumerate(["a request written as a dict", "compares with 'native'",
                                     "compares with 'missing_inputs'", "writes 'unchanged'",
                                     "names a command by its string", "header.get(", "header[",
                                     "looks into header"], 1):
            self.assertIn("houdini/sample.py:%d %s" % (line, what), found)
        blender_side = [entry for entry in scan("blender/sample.py", ast.parse(sample)) if "header" in entry]
        self.assertEqual(blender_side, [])
        self.assertFalse([entry for entry in found if entry.startswith("houdini/sample.py:9 ")], found)


# --- the table of docs/dev/protocol.md ------------------------------------------------


def protocol_section():
    with open(PROTOCOL_DOCUMENT, encoding="utf-8") as file:
        return file.read()


def protocol_table():
    """{command: (request field names, answer key names)} from the rows of the table."""
    rows = {}
    for line in protocol_section().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.startswith("| `") or len(cells) != 3:
            continue
        names = [re.findall(r"`([^`]+)`", cell) for cell in cells]
        if len(names[0]) != 1:
            continue
        rows[names[0][0]] = (names[1], names[2])
    return rows


class Documentation(unittest.TestCase):
    """The table of the Protocol section names what the contract declares, and nothing else."""

    def test_one_row_per_command(self):
        self.assertEqual(sorted(protocol_table()), sorted(contract.COMMANDS))

    def test_each_row_names_the_declared_fields_and_answer_keys(self):
        for command, (fields, keys) in protocol_table().items():
            self.assertEqual(sorted(fields), sorted(contract.REQUESTS.get(command, {})), "request of %s" % command)
            self.assertEqual(len(fields), len(set(fields)), "request of %s: a field named twice" % command)
            self.assertEqual(sorted(keys), sorted(contract.ANSWERS.get(command, {})), "answer of %s" % command)
            self.assertEqual(len(keys), len(set(keys)), "answer of %s: a key named twice" % command)

    def test_the_answer_of_any_command(self):
        section = protocol_section()
        for key in contract.ANY_ANSWER:
            self.assertIn("`%s`" % key, section)
        self.assertIn("PROTOCOL_VERSION", section)
        self.assertIn("STREAMS", section)
        self.assertIn("shared/contract.py", section)

    def test_the_parameter_descriptions(self):
        """The passage on parameter descriptions, up to the next titled paragraph, names every kind
        and every field."""
        section = protocol_section()
        start = section.index("**Parameter descriptions**")
        end = section.index("\n\n**", start)
        names = set(re.findall(r"`([^`]+)`", section[start:end]))
        self.assertIn("PARM_KINDS", names)
        self.assertIn("PARM_FIELDS", names)
        self.assertLessEqual(set(contract.PARM_KINDS), names)
        self.assertLessEqual(set(contract.PARM_FIELDS), names)


if __name__ == "__main__":
    unittest.main()
