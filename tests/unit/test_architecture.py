"""Architecture rules, checked on the source with `ast` (nothing is imported or run).
Every rule holds with no tolerance: a breach fails, and its message names it.

- Processes: shared/ imports the standard library and numpy only, houdini/ (hython)
  never imports Blender, and nothing outside houdini/ imports hou. Nothing changes
  sys.path (Blender's extension rules forbid it).
- Layers (IMPORT_RULES): shared/ imports only shared/, houdini/ only shared/ and
  houdini/, hython_entry.py nothing of the package; on the Blender side shared ->
  blender.core -> blender.features -> blender.ui, each feature on its own but for the
  declared exceptions; api.py above them all, imported by nothing of the package.
- No cycle in the imports at the top of the files, and no module of the package imported
  inside a function, where the cycle check would not see it.
- Outside blender/core/cook/, nothing imports a file of the cook engine nor reaches one
  through the package: only its facade, cook/__init__.py.
- No nested function that calls itself on the Blender side: a reference cycle that holds
  Blender structs until the garbage collector runs, which crashed Blender 5.2.
- Every operator named by its bl_idname ("houdini_bridge.name", bpy.ops.houdini_bridge.name)
  is defined: Blender draws a button for an unknown operator without an error.
"""

import ast
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADDON = os.path.join(ROOT, "addon")

BLENDER_FAMILY = {"bpy", "bmesh", "mathutils", "gpu", "gpu_extras", "blf", "bpy_extras", "addon_utils",
                  "rna_keymap_ui", "bl_ui", "bl_operators"}
HOUDINI = {"hou"}
SHARED_MAY_IMPORT = set(sys.stdlib_module_names) | {"numpy"}

# Which part of addon/ may import which: [(importing part, (parts it may import, ...))],
# parts as dotted package paths ("" is the root of the package). The first entry whose part
# holds the importing module applies.
IMPORT_RULES = [
    ("hython_entry", ()),  # nothing of the package, statically
    ("shared", ("shared",)),
    ("houdini", ("shared", "houdini")),
    ("blender.core", ("shared", "blender.core")),
    ("blender.features.cache", ("shared", "blender.core", "blender.features.cache")),
    ("blender.features.code_editor", ("shared", "blender.core", "blender.features.code_editor")),
    ("blender.features.display", ("shared", "blender.core", "blender.features.display")),
    ("blender.features.groups", ("shared", "blender.core", "blender.features.groups")),
    ("blender.features.shelf", ("shared", "blender.core", "blender.features.shelf")),
    # Declared exception: the viewport group selection draws its badge with the overlay's.
    ("blender.features.selection", ("shared", "blender.core", "blender.features.selection",
                                    "blender.features.viewport.overlay")),
    # Declared exceptions: the overlay draws the groups, the visualizers read the cached frame.
    ("blender.features.viewport", ("shared", "blender.core", "blender.features.viewport",
                                   "blender.features.groups", "blender.features.cache")),
    ("blender.ui", ("shared", "blender")),
    ("api", ("blender",)),  # no entry allows "api": nothing of the package imports it
    ("", ("blender",)),  # the root __init__.py: Blender's entry point
]

COOK = "blender.core.cook"  # the cook engine, whose __init__.py is the facade
OPERATOR_ID = re.compile(r"^houdini_bridge\.[a-z0-9_]+$")


def _python_files(folder):
    for directory, subfolders, files in os.walk(folder):
        subfolders[:] = sorted(name for name in subfolders if name != "__pycache__")
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(directory, name)


def _relative(path, start):
    return os.path.relpath(path, start).replace(os.sep, "/")


def _module_name(path, addon):
    """Dotted name of a file of addon/ inside the package ("hython_entry", "blender.core.link")."""
    parts = _relative(path, addon)[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _parse(path):
    with open(path, encoding="utf-8") as file:
        return ast.parse(file.read(), filename=path)


class _Imports(ast.NodeVisitor):
    """Every import of a file: `external` (top-level module, in a function) and `internal`
    (package module, imported name or None, name bound, in a function)."""

    def __init__(self, module, package_modules, is_package):
        self.package = module if is_package else module.rsplit(".", 1)[0] if "." in module else ""
        self.package_modules = package_modules
        self.depth = 0  # how many functions enclose the current node
        self.external = []
        self.internal = []

    def visit_FunctionDef(self, node):
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name in self.package_modules:  # an absolute import of a sibling, as hython does
                self.internal.append((alias.name, None, alias.asname or alias.name.split(".")[0], self.depth > 0))
            else:
                self.external.append((alias.name.split(".")[0], self.depth > 0))

    def visit_ImportFrom(self, node):
        if node.level == 0 and node.module not in self.package_modules:
            self.external.extend((node.module.split(".")[0], self.depth > 0) for _ in node.names)
            return
        if node.level == 0:
            base = node.module
        else:
            parts = self.package.split(".") if self.package else []
            parts = parts[:len(parts) - (node.level - 1)]
            base = ".".join(parts + ([node.module] if node.module else []))
        for alias in node.names:
            candidate = ".".join(part for part in (base, alias.name) if part)
            bound = alias.asname or alias.name
            if candidate in self.package_modules:
                self.internal.append((candidate, None, bound, self.depth > 0))
            else:
                self.internal.append((base, alias.name, bound, self.depth > 0))


def _scan(addon):
    """{module: (path, tree, _Imports)} of every file of a folder laid out as addon/."""
    paths = list(_python_files(addon))
    modules = {_module_name(path, addon) for path in paths}
    found = {}
    for path in paths:
        module, tree = _module_name(path, addon), _parse(path)
        imports = _Imports(module, modules, os.path.basename(path) == "__init__.py")
        imports.visit(tree)
        found[module] = (path, tree, imports)
    return found


def _within(module, part):
    return part == "" or module == part or module.startswith(part + ".")


def _allowed(module, imported):
    for part, allowed in IMPORT_RULES:
        if _within(module, part):
            return any(_within(imported, other) for other in allowed)
    return True


def _recursive_closures(tree):
    """[(function, nested function)] of the nested functions that call themselves."""
    found = []
    for outer in ast.walk(tree):
        if isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(outer):
                if (inner is not outer and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and any(isinstance(node, ast.Name) and node.id == inner.name for node in ast.walk(inner))):
                    found.append((outer.name, inner.name))
    return found


def _operators(tree):
    """(defined, named): the bl_idnames a file defines (`bl_idname = "houdini_bridge.…"` in a
    class) and those it names (a string, or bpy.ops.houdini_bridge.name)."""
    defining = {id(statement.value): statement.value.value
                for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
                for statement in node.body if isinstance(statement, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "bl_idname" for t in statement.targets)
                and isinstance(statement.value, ast.Constant)}
    named = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str) and OPERATOR_ID.match(node.value)
                and id(node) not in defining):
            named.add(node.value)
        if isinstance(node, ast.Attribute) and ast.unparse(node.value) == "bpy.ops.houdini_bridge":
            named.add("houdini_bridge." + node.attr)
    return set(defining.values()), named


def breaches(addon=ADDON):
    """Sorted ["file: what"] of every breach of the rules above, files named from addon/."""
    scan = _scan(addon)
    cook_files = {module for module in scan if module.startswith(COOK + ".")}
    defined, named = set(), {}
    found = set()
    for module, (path, tree, imports) in scan.items():
        file = _relative(path, addon)
        external = {top for top, _ in imports.external}
        if _within(module, "shared"):
            forbidden = external - SHARED_MAY_IMPORT
        elif _within(module, "houdini"):
            forbidden = external & BLENDER_FAMILY
        else:
            forbidden = external & HOUDINI
        found.update("%s: imports %s" % (file, top) for top in forbidden)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("insert", "append", "extend")
                    and ast.unparse(node.func.value) == "sys.path"):
                found.add("%s:%d changes sys.path" % (file, node.lineno))
        cook_bound = set()
        for imported, name, bound, deferred in imports.internal:
            if deferred:
                found.add("%s: imports %s inside a function" % (file, imported))
            if not _allowed(module, imported):
                found.add("%s: %s imports %s" % (file, module, imported))
            if not _within(module, COOK):
                if imported in cook_files:
                    found.add("%s: imports %s past the facade" % (file, imported))
                if imported == COOK and name is None:
                    cook_bound.add(bound)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in cook_bound
                    and COOK + "." + node.attr in cook_files):
                found.add("%s:%d reaches %s.%s past the facade" % (file, node.lineno, node.value.id, node.attr))
        if _within(module, "blender"):
            found.update("%s: %s inside %s calls itself" % (file, inner, outer)
                         for outer, inner in _recursive_closures(tree))
        own, names = _operators(tree)
        defined |= own
        for name in names:
            named.setdefault(name, file)
    found.update("%s: names %s, which no module defines" % (file, name)
                 for name, file in named.items() if name not in defined)
    return sorted(found)


def import_cycles(addon=ADDON):
    """Sorted ["a -> b -> a"] of the cycles of the imports at the top of the files, module by
    module, each from its smallest module. Empty when the graph has no cycle."""
    graph = {module: {imported for imported, _, _, deferred in imports.internal if not deferred and imported != module}
             for module, (_, _, imports) in _scan(addon).items()}
    cycles, done = set(), set()

    def visit(module, trail):
        for imported in sorted(graph.get(module, ())):
            if imported in trail:
                loop = trail[trail.index(imported):]
                start = loop.index(min(loop))
                loop = loop[start:] + loop[:start]
                cycles.add(" -> ".join(loop + [loop[0]]))
            elif imported not in done:
                visit(imported, trail + [imported])
        done.add(module)

    for module in sorted(graph):
        if module not in done:
            visit(module, [module])
    return sorted(cycles)


class Rules(unittest.TestCase):
    def test_no_breach(self):
        self.assertEqual(breaches(), [])

    def test_no_import_cycle(self):
        self.assertEqual(import_cycles(), [])

    def test_the_rules_name_existing_modules(self):
        modules = set(_scan(ADDON))
        self.assertIn(COOK, modules)
        for part, allowed in IMPORT_RULES:
            for name in (part, *allowed):
                self.assertIn(name, modules)

    def test_the_scan_sees_each_rule(self):
        """The scan on a small tree of its own: one breach of each rule, and nothing else."""
        sources = {
            "shared/__init__.py": "import bpy\n",
            "houdini/__init__.py": "import hou\nimport mathutils\n",
            "blender/__init__.py": "", "blender/core/__init__.py": "import sys\nsys.path.append('x')\n",
            "blender/core/low.py": "def f():\n    from . import high\n\nX = 'houdini_bridge.nothing'\n",
            "blender/core/high.py": "from . import low\n",
            "blender/core/cook/__init__.py": "", "blender/core/cook/queue.py": "",
            "blender/features/__init__.py": "",
            "blender/features/cache.py": "import hou\n\n\nclass Op:\n    bl_idname = 'houdini_bridge.cache_op'\n",
            "blender/features/groups.py": "from . import cache\nfrom ..core import cook\n"
                                          "from ..core.cook import queue\ncook.queue\n\n\n"
                                          "def g():\n    def h(n):\n        return h(n - 1)\n",
            "blender/ui/__init__.py": "x = 'houdini_bridge.cache_op'\n",
        }
        with tempfile.TemporaryDirectory() as folder:
            for name, text in sources.items():
                path = os.path.join(folder, *name.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as file:
                    file.write(text)
            found, cycles = breaches(folder), import_cycles(folder)
        self.assertEqual(found, [
            "blender/core/__init__.py:2 changes sys.path",
            "blender/core/low.py: imports blender.core.high inside a function",
            "blender/core/low.py: names houdini_bridge.nothing, which no module defines",
            "blender/features/cache.py: imports hou",
            "blender/features/groups.py: blender.features.groups imports blender.features.cache",
            "blender/features/groups.py: h inside g calls itself",
            "blender/features/groups.py: imports blender.core.cook.queue past the facade",
            "blender/features/groups.py:4 reaches cook.queue past the facade",
            "houdini/__init__.py: imports mathutils",
            "shared/__init__.py: imports bpy",
        ])
        self.assertEqual(cycles, [])


if __name__ == "__main__":
    unittest.main()
