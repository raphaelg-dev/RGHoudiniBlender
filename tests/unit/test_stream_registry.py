"""The registries of the native streams, read from the source with `ast` (the server's
needs hou, Blender's needs bpy): same names and order as contract.STREAMS on both sides,
the kinds of the classification, and the stream names written only where they belong."""

import ast
import os
import re
import unittest

from tests import support

contract = support.source("contract")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADDON = os.path.join(ROOT, "addon")
SERVER_STREAMS = "houdini/streams.py"
BLENDER_STREAMS = "blender/core/streams"
PROPERTIES = "blender/core/properties.py"
# The same strings meaning something else: (file, enclosing function, string, why).
OTHER_MEANINGS = [
    ("blender/core/cook/request.py", "_volume_input", "volume",
     "the kind of the key of an input read from a Volume object (source uid, 'volume', digest)"),
    ("blender/ui/preferences.py", "_draw_output", "instances",
     "the id of a section of the preferences (layout.panel), whose open state Blender keeps"),
    ("blender/ui/preferences.py", "_draw_output", "curves",
     "the id of a section of the preferences (layout.panel), whose open state Blender keeps"),
]
# The name of a holder property, as a new stream would name its own (closed_curves_holder).
HOLDER = re.compile(r"^[a-z][a-z0-9_]*_holder$")


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


def blender_stream_files():
    """The file of each Stream of the Blender registry (STREAMS of streams/__init__.py: one
    `<file>.STREAM` each), in the registry's order."""
    return tuple("%s/%s.py" % (BLENDER_STREAMS, element.value.id)
                 for element in _assigned(_parse(BLENDER_STREAMS + "/__init__.py"), "STREAMS").elts)


# Read from the registry, not written again here: the files of the streams, and where a
# stream may be named by its string (the contract, the file of each stream on both sides,
# and the stored properties).
BLENDER_STREAM_FILES = blender_stream_files()
NAMED_IN = {"shared/contract.py", SERVER_STREAMS, *BLENDER_STREAM_FILES, PROPERTIES}


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


def server_registry():
    """[(class name, stream name, kind)] of houdini/streams.py, in the order of STREAMS."""
    tree = _parse(SERVER_STREAMS)
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    found = []
    for element in _assigned(tree, "STREAMS").elts:
        attributes = {target.id: statement.value for statement in classes[element.id].body
                      if isinstance(statement, ast.Assign) for target in statement.targets}
        kind = attributes.get("kind")
        if isinstance(kind, ast.Name):  # a KIND_* constant of the module
            kind = _assigned(tree, kind.id)
        found.append((element.id, _contract_value(attributes["name"]),
                      kind.value if isinstance(kind, ast.Constant) else None))
    return found


def classification_kinds():
    """The numbers VEX_CLASSIFY gives __kind."""
    snippet = _assigned(_parse(SERVER_STREAMS), "VEX_CLASSIFY").value
    return [int(number) for number in re.findall(r"i@__kind\s*=\s*(\d+)\s*;", snippet)]


def blender_registry():
    """[(file, keywords of its common.Stream(...))] in the order of STREAMS of streams/__init__.py."""
    found = []
    for element in _assigned(_parse(BLENDER_STREAMS + "/__init__.py"), "STREAMS").elts:
        name = "%s/%s.py" % (BLENDER_STREAMS, element.value.id)
        call = _assigned(_parse(name), element.attr)
        found.append((name, {keyword.arg: keyword.value for keyword in call.keywords}))
    return found


def stored_settings():
    """The property names of HoudiniBridgeSettings in core/properties.py."""
    tree = _parse(PROPERTIES)
    settings = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == "HoudiniBridgeSettings")
    return {statement.target.id for statement in settings.body if isinstance(statement, ast.AnnAssign)}


def misplaced_names(name, tree):
    """["file:line what"] of one file of addon/: stream names, KIND_ constants, literal
    minlength and lists of holders where they do not belong."""
    found = []
    streams = set(contract.STREAMS)
    enclosing = _functions(tree)
    other = {(file, function, value) for file, function, value, _ in OTHER_MEANINGS}
    holders_here = []
    for node in ast.walk(tree):
        where = "%s:%d" % (name, getattr(node, "lineno", 0))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if (node.value in streams and name not in NAMED_IN
                    and (name, enclosing.get(id(node)), node.value) not in other):
                found.append(where + " names the stream %r" % node.value)
            if HOLDER.match(node.value):
                holders_here.append(node.value)
                if name not in BLENDER_STREAM_FILES:
                    found.append(where + " names the holder %r" % node.value)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
            elements = node.keys + node.values if isinstance(node, ast.Dict) else node.elts
            names = [e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)
                     and (HOLDER.match(e.value) or e.value in streams)]
            if len(names) > 1 and name != "shared/contract.py":
                found.append(where + " lists streams or holders %s" % names)
        identifier = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
        if identifier.startswith("KIND_") and name != SERVER_STREAMS:
            found.append(where + " uses %s" % identifier)
        if isinstance(node, ast.keyword) and node.arg == "minlength" and isinstance(node.value, ast.Constant):
            found.append("%s:%d minlength=%r" % (name, node.value.lineno, node.value.value))
    if len(holders_here) > 1:
        found.append("%s names %d holders %s" % (name, len(holders_here), holders_here))
    return found


class Registries(unittest.TestCase):
    def test_the_contract_names_them_in_todays_order(self):
        self.assertEqual(contract.STREAMS, ("instances", "curves", "points", "volume"))
        for name in contract.STREAMS:
            self.assertIn(name, contract.ANSWERS[contract.COOK])

    def test_server_registry_follows_the_contract(self):
        self.assertEqual([name for _, name, _ in server_registry()], list(contract.STREAMS))

    def test_every_kind_of_the_classification_is_the_mesh_or_one_stream(self):
        kinds = classification_kinds()
        mesh = _assigned(_parse(SERVER_STREAMS), "KIND_MESH").value
        streams = [kind for _, _, kind in server_registry() if kind is not None]
        self.assertEqual(len(streams), len(set(streams)), "two streams share a kind")
        self.assertNotIn(mesh, streams)
        self.assertEqual(sorted(set(kinds)), sorted([mesh] + streams))
        # The points are no kind of primitive: a stream may have none.
        self.assertIn((contract.POINTS, None), [(name, kind) for _, name, kind in server_registry()])

    def test_blender_registry_follows_the_contract(self):
        registry = blender_registry()
        self.assertEqual([_contract_value(keywords["name"]) for _, keywords in registry], list(contract.STREAMS))
        # One file per stream, each a file of the package.
        self.assertEqual(len(set(BLENDER_STREAM_FILES)), len(contract.STREAMS))
        for name in BLENDER_STREAM_FILES:
            self.assertTrue(os.path.exists(os.path.join(ADDON, *name.split("/"))), name)

    def test_each_holder_is_named_as_the_scan_expects(self):
        for name, keywords in blender_registry():
            self.assertRegex(keywords["holder"].value, HOLDER, name)

    def test_each_holder_and_owned_property_is_stored(self):
        stored = stored_settings()
        for name, keywords in blender_registry():
            self.assertIn(keywords["holder"].value, stored, name)
            for owned in keywords.get("owned", ast.Tuple(elts=[])).elts:
                self.assertIn(owned.value, stored, name)


class Coherence(unittest.TestCase):
    """No tolerance: a stream is named where it is described, and nowhere else."""

    def test_names_kinds_and_holders_only_where_they_belong(self):
        found = []
        for name in _python_files():
            found.extend(misplaced_names(name, _parse(name)))
        self.assertEqual(found, [])

    def test_the_other_meanings_exist(self):
        for file, function, value, _ in OTHER_MEANINGS:
            tree = _parse(file)
            enclosing = _functions(tree)
            self.assertTrue(any(isinstance(node, ast.Constant) and node.value == value
                                and enclosing.get(id(node)) == function for node in ast.walk(tree)),
                            "%s: %r in %s" % (file, value, function))

    def test_the_scan_sees_each_kind(self):
        sample = "\n".join([
            'if found.get("instances"): pass',
            'HOLDERS = ("curves_holder", "points_holder")',
            'kinds = np.bincount(values, minlength=4)',
            'if kinds[KIND_VOLUME]: pass',
            'names = ["points", "volume"]',
            'def _volume_input():\n    key = (uid, "volume", digest)',
            'holder = settings.get("closed_curves_holder")',
        ])
        found = misplaced_names("blender/sample.py", ast.parse(sample))
        for expected in ["blender/sample.py:1 names the stream 'instances'",
                         "blender/sample.py:2 names the holder 'curves_holder'",
                         "blender/sample.py:2 lists streams or holders ['curves_holder', 'points_holder']",
                         "blender/sample.py:3 minlength=4", "blender/sample.py:4 uses KIND_VOLUME",
                         "blender/sample.py:5 lists streams or holders ['points', 'volume']",
                         "blender/sample.py:7 names the stream 'volume'",
                         "blender/sample.py:8 names the holder 'closed_curves_holder'"]:
            self.assertIn(expected, found)
        # The same string with another meaning, in the function declared for it, is not one.
        other_meaning = sample.split("\n")[5:7]  # the two lines of _volume_input
        self.assertEqual(misplaced_names("blender/core/cook/request.py", ast.parse("\n".join(other_meaning))), [])


if __name__ == "__main__":
    unittest.main()
