"""The documents name what exists: README.md, CONTRIBUTING.md, SECURITY.md, DEVELOPMENT.md,
docs/GUIDE.md and the files of docs/dev/, read as text (nothing of the extension is imported
or run).

- every file or folder of the repository named between backquotes exists: a name
  that starts with a folder of the repository (`tests/support.py`, `cook/request.py`)
  or that ends with a source extension (`support.py`), found by its path or by the end
  of one (`cook/request.py` is addon/blender/core/cook/request.py);
- every `module.name` between backquotes whose module is a module of the extension
  (`link.ServerLink.request_sync`, `cook/scheduler.tick`, `houdini.geometry.numeric_attributes`)
  names a function, a class, a constant or an imported name of that module, read with
  `ast`; `module.Class.member` and `Class.member`, for a class of the extension, also
  check the member of the class;
- the tree of the "Repository map" of DEVELOPMENT.md is the content of addon/: every
  folder and every file but the empty __init__.py files, nothing more.

The table of docs/dev/protocol.md is checked by test_contract.py.
"""

import ast
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ADDON = os.path.join(ROOT, "addon")
DOCUMENTS = ("README.md", "CONTRIBUTING.md", "SECURITY.md", "DEVELOPMENT.md", "docs/GUIDE.md", "docs/dev/protocol.md",
             "docs/dev/parameters.md", "docs/dev/features.md", "docs/dev/testing.md", "docs/dev/pitfalls.md")
SKIPPED_FOLDERS = {".git", ".ruff_cache", "__pycache__", "dist"}
# A name ending so is a file, never a `module.name`.
FILE_EXTENSIONS = {"py", "md", "toml", "ps1", "yml", "json", "blend", "hda", "png", "gif", "mp4", "txt", "zip",
                   "vdb", "exe"}
# What the documents name with such an extension but is not in the repository: the files
# the extension writes in its user folder, Blender's and Houdini's own, and what a run of
# docs/capture_images.py writes or reads but the repository does not keep.
OUTSIDE = {"catalog.json", "favorites.json", "index.json", "houdini_bridge_hdas.blend", "blender_assets.cats.txt",
           "startup.blend", "recent-files.txt", "hython.exe", "blender.exe", "icons.zip", "live_edit.mp4",
           "parameter-before.png", "parameter-after.png", "timeline-cache.png"}
# Folders the runs write, which git ignores: the screenshots of test_ui.py, the built zip.
WRITTEN = {"tests/screenshots/", "dist/"}
TREE_TITLE = "## Repository map"
DESCRIPTION_COLUMN = 24  # the descriptions of the tree start here; names and folders before it


def _repository_files():
    """The files of the repository, "a/b/c.py": what git tracks or would add (the ignored
    files, which a clone does not have, left out), or every file when git cannot tell."""
    try:
        listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", check=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        listed = []
        for directory, folders, files in os.walk(ROOT):
            folders[:] = [name for name in folders if name not in SKIPPED_FOLDERS]
            relative = os.path.relpath(directory, ROOT).replace(os.sep, "/")
            listed.extend(name if relative == "." else relative + "/" + name for name in files)
    return {path for path in listed if os.path.exists(os.path.join(ROOT, *path.split("/")))}


def _repository_paths():
    """Every file and folder of the repository, as "a/b/c.py" and "a/b/"."""
    found = set()
    for path in _repository_files():
        found.add(path)
        parts = path.split("/")
        found.update("/".join(parts[:depth]) + "/" for depth in range(1, len(parts)))
    return found


def _top_folders():
    return {path.split("/")[0] for path in _repository_files() if "/" in path}


def _addon_modules():
    """{dotted module name: path} of every file of addon/ ("blender.core.link", "api")."""
    modules = {}
    for directory, folders, files in os.walk(ADDON):
        folders[:] = [name for name in folders if name != "__pycache__"]
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(directory, name)
                parts = os.path.relpath(path, ADDON).replace(os.sep, "/")[:-3].split("/")
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                if parts:
                    modules[".".join(parts)] = path
    return modules


def _members(body):
    """Names a module or class body binds: defs, classes, assignments, imports."""
    names = set()
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for leaf in ast.walk(target):
                    if isinstance(leaf, ast.Name):
                        names.add(leaf.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.If, ast.Try)):
            names |= _members(node.body)
    return names


def _class_members(node):
    """What a class has: its body, and the attributes its methods set on self."""
    names = _members(node.body)
    for leaf in ast.walk(node):
        if (isinstance(leaf, ast.Attribute) and isinstance(leaf.value, ast.Name) and leaf.value.id == "self"
                and isinstance(leaf.ctx, ast.Store)):
            names.add(leaf.attr)
    return names


class _Module:
    def __init__(self, path):
        with open(path, encoding="utf-8") as file:
            self.tree = ast.parse(file.read())
        self.names = _members(self.tree.body)
        self.classes = {node.name: node for node in self.tree.body if isinstance(node, ast.ClassDef)}

    def has(self, parts):
        if parts[0] not in self.names:
            return False
        if len(parts) > 1 and parts[0] in self.classes:
            return parts[1] in _class_members(self.classes[parts[0]])
        return True


def _documents():
    for name in DOCUMENTS:
        with open(os.path.join(ROOT, *name.split("/")), encoding="utf-8") as file:
            yield name, file.read()


def _code_spans(text):
    """The text between single backquotes, outside the fenced blocks."""
    prose = re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    return re.findall(r"`([^`\n]+)`", prose)


def _is_path(span, top_folders):
    if re.search(r"[\s<>*…$%\\]|^\.?/|^[A-Za-z]:|^\.\w+$", span) or span.startswith(("http", "-")) or span in WRITTEN:
        return False
    if span.split("/")[0] in top_folders | {".github"}:
        return True
    last = span.rstrip("/").rsplit("/", 1)[-1]
    return "." in last and last.rsplit(".", 1)[1] in FILE_EXTENSIONS and last not in OUTSIDE


def _path_exists(span, paths):
    if span.endswith("/"):
        return any(path == span or path.endswith("/" + span) for path in paths)
    return any(path == span or path.endswith("/" + span) or path == span + "/" or path.endswith("/" + span + "/")
               for path in paths)


def _dotted(span):
    """The parts of a `module.name` span (a trailing call and slashes of a path written
    as `cook/scheduler.tick` allowed), or None."""
    span = re.sub(r"\(.*\)$", "", span)
    if not re.fullmatch(r"[A-Za-z_]\w*([./][A-Za-z_]\w*)*\.[A-Za-z_]\w*", span):
        return None
    if span.rsplit(".", 1)[1] in FILE_EXTENSIONS:
        return None
    return span.replace("/", ".").split(".")


def _module_candidates(parts, modules):
    """[(module path, the parts that follow it)] for the longest leading parts that end a
    module name of the extension ("cook.scheduler" ends "blender.core.cook.scheduler")."""
    for length in range(len(parts) - 1, 0, -1):
        name = ".".join(parts[:length])
        found = [path for module, path in modules.items() if module == name or module.endswith("." + name)]
        if found:
            return found, parts[length:]
    return [], parts


def _tree_lines(text):
    """The lines of the fenced block under the "Repository map" title."""
    start = text.index("\n" + TREE_TITLE + "\n")
    fence = text.index("```", start)
    end = text.index("```", fence + 3)
    return text[fence:end].splitlines()[1:]


def _tree_entries(lines):
    """[path] of the tree, folders ending with "/", from the indentation (two spaces a level)."""
    entries, stack = [], []
    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        if not line.strip() or indent >= DESCRIPTION_COLUMN:
            continue  # a description carried over
        name = line.split()[0]
        depth = indent // 2
        stack[depth:] = [name]
        entries.append("".join(stack[:-1]) + name)
    return entries


class Paths(unittest.TestCase):
    def test_every_path_named_exists(self):
        paths, top_folders = _repository_paths(), _top_folders()
        missing = ["%s: %s" % (name, span) for name, text in _documents() for span in _code_spans(text)
                   if _is_path(span, top_folders) and not _path_exists(span, paths)]
        self.assertEqual(missing, [])

    def test_the_scan_tells_paths_apart(self):
        top_folders = {"addon", "tests", "docs"}
        for span in ("tests/support.py", "addon/", "support.py", "cook/request.py", "docs/images/"):
            self.assertTrue(_is_path(span, top_folders), span)
        for span in ("catalog.json", "<user dir>/icons", "C:/PROGRA~1/…", "/obj/houdini_bridge", "link.post",
                     "COMMON/subnet.svg", ".vdb", "$HFS"):
            self.assertFalse(_is_path(span, top_folders), span)
        paths = {"addon/", "addon/blender/", "addon/blender/core/", "addon/blender/core/cook/request.py"}
        self.assertTrue(_path_exists("cook/request.py", paths))
        self.assertTrue(_path_exists("core/", paths))
        self.assertFalse(_path_exists("engine.py", paths))


class Names(unittest.TestCase):
    def test_every_module_name_named_exists(self):
        modules = _addon_modules()
        parsed = {path: _Module(path) for path in modules.values()}
        classes = {}  # class name -> [its ClassDef], for `Class.member`
        for module in parsed.values():
            for class_name, node in module.classes.items():
                classes.setdefault(class_name, []).append(node)
        wrong = []
        for name, text in _documents():
            for span in _code_spans(text):
                parts = _dotted(span)
                if parts is None:
                    continue
                candidates, rest = _module_candidates(parts, modules)
                if candidates:
                    found = any(parsed[path].has(rest) for path in candidates)
                elif parts[0] in classes and len(parts) == 2:
                    found = any(parts[1] in _class_members(node) for node in classes[parts[0]])
                else:
                    continue
                if not found:
                    wrong.append("%s: %s" % (name, span))
        self.assertEqual(wrong, [])

    def test_the_scan_reads_module_names(self):
        modules = {"blender.core.link": "", "blender.core.cook.scheduler": "", "houdini.geometry": ""}
        self.assertEqual(_dotted("cook/scheduler.tick"), ["cook", "scheduler", "tick"])
        self.assertEqual(_dotted("contract.request(command, **fields)"), ["contract", "request"])
        self.assertIsNone(_dotted("support.py"))
        self.assertIsNone(_dotted("modifier.properties.inputs.<identifier>.value"))
        self.assertEqual(_module_candidates(["cook", "scheduler", "tick"], modules), ([""], ["tick"]))
        self.assertEqual(_module_candidates(["houdini", "geometry", "x"], modules), ([""], ["x"]))
        self.assertEqual(_module_candidates(["hou", "Geometry", "x"], modules), ([], ["hou", "Geometry", "x"]))


class Tree(unittest.TestCase):
    def test_the_tree_is_the_content_of_addon(self):
        with open(os.path.join(ROOT, "DEVELOPMENT.md"), encoding="utf-8") as file:
            entries = _tree_entries(_tree_lines(file.read()))
        listed = {entry for entry in entries if entry.startswith("addon/")}
        actual = set()
        for directory, folders, files in os.walk(ADDON):
            folders[:] = [name for name in folders if name != "__pycache__"]
            relative = "addon/" + os.path.relpath(directory, ADDON).replace(os.sep, "/") + "/"
            relative = relative.replace("addon/./", "addon/")
            actual.add(relative)
            for name in files:
                path = os.path.join(directory, name)
                if name == "__init__.py" and os.path.getsize(path) == 0:
                    continue
                if name == "LICENSE":  # build.ps1 copies it for the zip and removes it
                    continue
                actual.add(relative + name)
        self.assertEqual(sorted(listed - actual), [], "listed, but not in addon/")
        self.assertEqual(sorted(actual - listed), [], "in addon/, but not listed")
        others = [entry for entry in entries if not entry.startswith("addon/")]
        paths = _repository_paths()
        self.assertEqual([entry for entry in others if entry not in paths], [], "listed, but not in the repository")

    def test_the_tree_reader(self):
        lines = ["addon/                  the extension", "  shared/               shared",
                 "    protocol.py         frames", "                        carried over", "build.ps1"]
        self.assertEqual(_tree_entries(lines), ["addon/", "addon/shared/", "addon/shared/protocol.py", "build.ps1"])


if __name__ == "__main__":
    unittest.main()
