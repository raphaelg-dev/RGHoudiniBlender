"""The registries of the parameter kinds, read from the source with `ast` (the server's needs
hou, Blender's needs bpy): the names of contract.PARM_KINDS on both sides and in the rows of
the modifier panel, in that order; the fields each kind writes, declared in
contract.PARM_FIELDS; and a kind named by its contract constant, never by its string."""

import ast
import os
import unittest

from tests import support

contract = support.source("contract")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADDON = os.path.join(ROOT, "addon")
SERVER_KINDS = "houdini/parms.py"
BLENDER_KINDS = "blender/core/parameters/kinds.py"
# The rows of the modifier panel, one per kind: the interface's half of the Blender registry.
UI_ROWS = "blender/ui/parameter_rows.py"
CONTRACT = "shared/contract.py"
KIND_NAMES = set(contract.PARM_KINDS)
# The same strings meaning something else, compared with a variable named kind:
# (file, enclosing function, string, why).
OTHER_MEANINGS = [
    ("blender/core/mesh_io/read.py", "add", "float",
     "the type of the values of a mesh attribute sent to the server (float or int)"),
    ("houdini/geometry.py", "_set_values", "float", "the type of the values of an attribute received"),
    ("houdini/geometry.py", "import_attributes", "float", "the type of the values of an attribute received"),
]


def _parse(name):
    with open(os.path.join(ADDON, *name.split("/")), encoding="utf-8") as file:
        return ast.parse(file.read(), filename=name)


def _python_files():
    for directory, subfolders, files in os.walk(ADDON):
        subfolders[:] = sorted(name for name in subfolders if name != "__pycache__")
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.relpath(os.path.join(directory, name), ADDON).replace(os.sep, "/")


def _assigned(tree, name):
    """The value node of the top-level `name = ...` of a module."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    raise KeyError(name)


def _contract_value(node):
    """The value of `contract.NAME`."""
    assert isinstance(node, ast.Attribute) and ast.unparse(node.value) == "contract", ast.unparse(node)
    return getattr(contract, node.attr)


def _functions(tree):
    """{id(node): name of the function that encloses it} for every node of a module."""
    enclosing = {}

    def visit(node, function):
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            enclosing[id(child)] = inner
            visit(child, inner)

    visit(tree, None)
    return enclosing


def declared(kind):
    """The fields contract.PARM_FIELDS declares for a kind."""
    return {field for field, (kinds, _) in contract.PARM_FIELDS.items() if kind in kinds}


# --- the server: houdini/parms.py -----------------------------------------------------


def server_registry():
    """[(kind, describe function name or None, write function name)] of KINDS, in order."""
    found = []
    table = _assigned(_parse(SERVER_KINDS), "KINDS")
    for key, value in zip(table.keys, table.values, strict=False):
        describe, write = value.args
        found.append((_contract_value(key), describe.id if isinstance(describe, ast.Name) else None, write.id))
    return found


def written_fields(tree):
    """{function name: fields it writes into `entry` (a dict literal or a subscript), with those
    of the module's functions it calls}, for the top-level functions of a module."""
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    direct, calls = {}, {}
    for name, function in functions.items():
        direct[name] = {node.slice.value for node in ast.walk(function)
                        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                        and isinstance(node.value, ast.Name) and node.value.id == "entry"
                        and isinstance(node.slice, ast.Constant)}
        direct[name] |= {key.value for node in ast.walk(function)
                         if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                         and any(isinstance(t, ast.Name) and t.id == "entry" for t in node.targets)
                         for key in node.value.keys if isinstance(key, ast.Constant)}
        calls[name] = {node.func.id for node in ast.walk(function)
                       if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions}

    def closure(name, seen):
        fields = set(direct[name])
        for called in calls[name] - seen:
            fields |= closure(called, seen | {called})
        return fields

    return {name: closure(name, {name}) for name in functions}


def multiparm_count_fields(tree):
    """The keys of the dict literals of describe_parms that carry a "kind" (a multiparm's count)."""
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "describe_parms")
    found = []
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            keys = [key.value for key in node.keys if isinstance(key, ast.Constant)]
            if "kind" in keys:
                kind = node.values[keys.index("kind")]
                found.append((_contract_value(kind), set(keys)))
    return found


# --- Blender: blender/core/parameters/kinds.py ----------------------------------------


def blender_registry():
    """[(kind, {keyword: value node})] of the Kind(...) of KINDS, in order."""
    tree = _parse(BLENDER_KINDS)
    names = _assigned(tree, "KINDS").generators[0].iter.elts
    found = []
    for name in names:
        call = _assigned(tree, name.id)
        found.append((_contract_value(call.args[0]), {keyword.arg: keyword.value for keyword in call.keywords}))
    return found


def _strings(node, tree):
    """The strings of a tuple literal, or of a module-level tuple named by it."""
    if isinstance(node, ast.Name):
        node = _assigned(tree, node.id)
    return [element.value for element in node.elts]


# --- the scan -------------------------------------------------------------------------


def _is_kind_expression(node):
    """`x.kind`, `x["kind"]`, `x.get("kind")`, or a variable named kind (or ..._kind)."""
    if isinstance(node, ast.Attribute):
        return node.attr == "kind"
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "kind"
    if isinstance(node, ast.Call):
        return (isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "kind")
    if isinstance(node, ast.Name):
        return node.id == "kind" or node.id.endswith("_kind")
    return False


def _kind_names(node):
    """The kind names a node writes as strings: one string, or a tuple, list or set of them."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in KIND_NAMES:
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [name for element in node.elts for name in _kind_names(element)]
    return []


def misplaced_kinds(name, tree):
    """["file:line what"] of one file of addon/: a kind written as a string where its contract
    constant belongs (a comparison, a table keyed by kind, a registry looked up)."""
    if name == CONTRACT:
        return []
    found = []
    enclosing = _functions(tree)
    other = {(file, function, value) for file, function, value, _ in OTHER_MEANINGS}
    for node in ast.walk(tree):
        where = "%s:%d" % (name, getattr(node, "lineno", 0))
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(_is_kind_expression(operand) for operand in operands):
                for operand in operands:
                    for value in _kind_names(operand):
                        if (name, enclosing.get(id(node)), value) not in other:
                            found.append(where + " compares a kind with %r" % value)
        if isinstance(node, ast.Dict):
            keys = [value for key in node.keys if key is not None for value in _kind_names(key)]
            if len(keys) > 1:
                found.append(where + " a table keyed by kind %s" % keys)
        if isinstance(node, ast.Subscript) and "kind" in ast.unparse(node.value).lower() and _kind_names(node.slice):
            found.append(where + " looks up the kind %s" % _kind_names(node.slice))
    return found


class Registries(unittest.TestCase):
    def test_server_registry_follows_the_contract(self):
        self.assertEqual([kind for kind, _, _ in server_registry()], list(contract.PARM_KINDS))

    def test_blender_registry_follows_the_contract(self):
        self.assertEqual([kind for kind, _ in blender_registry()], list(contract.PARM_KINDS))

    def test_every_field_the_server_writes_is_declared_for_its_kind(self):
        tree = _parse(SERVER_KINDS)
        fields = written_fields(tree)
        common = fields["_describe_template"]
        self.assertEqual(common, {field for field, (kinds, _) in contract.PARM_FIELDS.items()
                                  if set(kinds) == KIND_NAMES} - {"value"})
        written = set(common)
        for kind, describe, _ in server_registry():
            own = fields[describe] if describe else set()
            written |= own
            self.assertLessEqual(own | common, declared(kind), "%s writes undeclared fields" % kind)
        for kind, keys in multiparm_count_fields(tree):
            self.assertLessEqual(keys, declared(kind), "the count of a multiparm")
            written |= keys
        # And the contract declares nothing the server does not write.
        self.assertEqual(written, set(contract.PARM_FIELDS))

    def test_what_blender_reads_of_a_description_is_declared_for_its_kind(self):
        tree = _parse(BLENDER_KINDS)
        for kind, keywords in blender_registry():
            for field in _strings(keywords["signature"], tree) if "signature" in keywords else []:
                self.assertIn(field, declared(kind), "the signature of %s" % kind)

    def test_every_kind_draws_its_row(self):
        """The modifier panel draws each kind's row from ROWS of the interface, one Row per kind in
        the order of the contract; the core's Kind draws nothing, and UNKNOWN, a kind of a later
        version, has no row, since this version does not know what it holds."""
        table = _assigned(_parse(UI_ROWS), "ROWS")
        self.assertEqual([_contract_value(key) for key in table.keys], list(contract.PARM_KINDS))
        self.assertTrue(all(isinstance(value, ast.Call) and ast.unparse(value.func) == "Row" for value in table.values))
        for kind, keywords in blender_registry():
            self.assertNotIn("draw", keywords, kind)
        unknown = _assigned(_parse(BLENDER_KINDS), "UNKNOWN")
        self.assertFalse([keyword.arg for keyword in unknown.keywords], "UNKNOWN does nothing")


class Coherence(unittest.TestCase):
    """No tolerance: a kind is named by its contract constant (contract.RAMP), which a typo
    cannot miss, and never by its string."""

    def test_no_kind_written_as_a_string(self):
        found = []
        for name in _python_files():
            found.extend(misplaced_kinds(name, _parse(name)))
        self.assertEqual(found, [])

    def test_the_other_meanings_exist(self):
        for file, function, value, _ in OTHER_MEANINGS:
            tree = _parse(file)
            enclosing = _functions(tree)
            self.assertTrue(any(isinstance(node, ast.Compare) and value in [n for o in [node.left, *node.comparators]
                                                                             for n in _kind_names(o)]
                                and enclosing.get(id(node)) == function for node in ast.walk(tree)),
                            "%s: %r in %s" % (file, value, function))

    def test_the_scan_sees_each_case(self):
        sample = "\n".join([
            'if parm.kind == "ramp": pass',
            'if description["kind"] in ("float", "int"): pass',
            'if p.get("kind") != "menu": pass',
            'kind = parm.kind\nif kind == "button": pass',
            'SOCKETS = {"int": 1, "toggle": 2}',
            'node = KINDS["ramp"]',
            'if parm.kind == contract.RAMP: pass',
            'if output["kind"] != "group": pass',
            'if parm.ramp == "color": pass',
            'value = description["menu"]',
        ])
        found = misplaced_kinds("blender/sample.py", ast.parse(sample))
        self.assertEqual(sorted(found), sorted([
            "blender/sample.py:1 compares a kind with 'ramp'",
            "blender/sample.py:2 compares a kind with 'float'",
            "blender/sample.py:2 compares a kind with 'int'",
            "blender/sample.py:3 compares a kind with 'menu'",
            "blender/sample.py:5 compares a kind with 'button'",
            "blender/sample.py:6 a table keyed by kind ['int', 'toggle']",
            "blender/sample.py:7 looks up the kind ['ramp']",
        ]))
        # A declared other meaning is not reported.
        self.assertEqual(misplaced_kinds("houdini/geometry.py",
                                         ast.parse('def _set_values(kind):\n    x = 1 if kind == "float" else 2')), [])


if __name__ == "__main__":
    unittest.main()
