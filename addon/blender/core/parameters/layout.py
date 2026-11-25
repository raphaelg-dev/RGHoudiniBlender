"""The parameters as the modifier panel lays them out: Houdini's order and folders, as data.

A plan (plan()), made once per structure, turns settings.parms and their folder types
into nested folders: collapsible, simple, a set of tabs, a multiparm instance. Siblings are
told apart by their Houdini name, unique in the node, so two folders with the same label
(two unlabelled Simple folders used as separators) stay two; a file whose folder types hold
no names yet (saved before a cook brought them, or a file saved before 1.0) uses the labels
until its next cook. walk() goes through the plan for one draw and hands each step to a painter:
ui/parameter_rows.py, or Recorder, which lists the rows without a window.

The open tab of each set lives in memory only: changing tabs writes nothing to the file, so
it neither cooks nor marks the file modified.
"""

import zlib

from ....shared import contract
from .. import preferences, stored
from . import interface, kinds


class Folder:
    """A folder of the plan: its label, type (contract.FOLDER_TYPES), `path` of labels from the
    root (what the panel shows), `key` of Houdini names from the root (what tells it apart),
    and items: folders, tab sets, or indices of settings.parms."""

    __slots__ = ("label", "type", "path", "key", "items", "_found")

    def __init__(self, label, folder_type, path, key):
        self.label, self.type, self.path, self.key = label, folder_type, path, key
        self.items = []
        self._found = {}  # (name or label, type, tab set) -> the folder or tab set already made here

    def child(self, label, folder_type, tab_set, name):
        """The folder of this name (its label when the name is unknown) and type inside this
        one, made when missing. A tab goes into the tab set of its name, made when missing."""
        found = (name or label, folder_type, tab_set)
        if found in self._found:
            return self._found[found]
        folder = Folder(label, folder_type, self.path + (label,), self.key + (name or label,))
        if folder_type == contract.FOLDER_TAB:
            tabs = self._found.get(("", "", tab_set))
            if tabs is None:
                tabs = self._found[("", "", tab_set)] = TabSet(tab_set, self.path, self.key)
                self.items.append(tabs)
            tabs.tabs.append(folder)
        else:
            self.items.append(folder)
        self._found[found] = folder
        return folder


class TabSet:
    """A set of tabs inside a folder: its name (Houdini's, unique in the node), the `path` and
    `key` of the folder holding it, and its tabs (folders)."""

    __slots__ = ("name", "path", "key", "tabs")

    def __init__(self, name, path, key):
        self.name, self.path, self.key, self.tabs = name, path, key, []


class Plan:
    """The folders of a node group's parameters, and what each parameter needs to be drawn
    without reading its description again: its kind and whether it joins the row before."""

    def __init__(self, settings):
        self.structure = settings.structure
        self.root = Folder("", "", (), ())
        self.kinds = []
        for index, parm in enumerate(settings.parms):
            self.kinds.append(kinds.kind_of(parm.kind))
            folder = self.root
            labels = parm.folder.split(interface.FOLDER_SEPARATOR) if parm.folder else []
            for label, (folder_type, tab_set, name) in zip(labels, folder_types(parm, len(labels)), strict=False):
                folder = folder.child(label, folder_type, tab_set, name)
            folder.items.append(index)


def folder_types(parm, depth):
    """[(type, tab set, Houdini name)] of each level of a parameter's folder path. Before the
    first cook of a file saved before 1.0, or for a level the server did not describe, a
    folder is collapsible; a level without a name ("") is told apart by its label."""
    found = stored.parse(parm.folder_types, [])
    levels = []
    for index in range(depth):
        level = found[index] if index < len(found) and isinstance(found[index], list) else []
        folder_type = level[0] if level and level[0] in contract.FOLDER_TYPES else contract.FOLDER_COLLAPSIBLE
        tab_set = str(level[1]) if len(level) > 1 and folder_type == contract.FOLDER_TAB else ""
        name = str(level[2]) if len(level) > 2 and level[2] else ""
        levels.append((folder_type, tab_set, name))
    return levels


_plans = {}  # node group session_uid -> Plan
_open_tabs = {}  # (node group name, key of the folder holding the set, tab set) -> key of the open tab


def plan(tree):
    """The plan of a node group's parameters, made again when its structure changed."""
    settings = tree.houdini_bridge
    found = _plans.get(tree.session_uid)
    if found is None or found.structure != settings.structure or len(found.kinds) != len(settings.parms):
        found = _plans[tree.session_uid] = Plan(settings)
    return found


def forget(tree=None, tabs=False):
    """Make the plan of a node group again at its next draw (other folder types came with a
    cook), or every plan (a file loaded, an undo, unregister); with `tabs`, forget the tabs
    opened too (a file loaded: its node groups may have the names of the old file's)."""
    if tree is None:
        _plans.clear()
    else:
        _plans.pop(tree.session_uid, None)
    if tabs:
        _open_tabs.clear()


def open_tab(tree, tabs):
    """The key (the last Houdini name of its path) of the tab of a set opened last, or None."""
    return _open_tabs.get((tree.name, tabs.key, tabs.name))


def set_open_tab(tree_name, key, tab_set, tab):
    """Open a tab of a set: `key` is the key of the folder holding the set, `tab` the tab's
    own name. Kept in memory only."""
    _open_tabs[(tree_name, tuple(key), tab_set)] = tab


def tab_key(tabs):
    """The key of the folder holding a tab set as the tab operator carries it (names separated by tabs)."""
    return interface.FOLDER_SEPARATOR.join(tabs.key)


def clear():
    _plans.clear()
    _open_tabs.clear()


def panel_id(tree, folder):
    """The id of a collapsible folder's layout panel: one per modifier (its node group) and
    folder (its key). Blender keeps the open state of a layout panel in the panel drawing it,
    which the Properties editor reuses for the modifier at the same place of another object:
    the id must tell modifiers apart."""
    key = "%s\x1f%s" % (tree.name, interface.FOLDER_SEPARATOR.join(folder.key))
    return "hb_%08x" % zlib.crc32(key.encode("utf-8"))


def listed(tree):
    """[(kind, parm)] of the parameters of a node group the panel may draw, in Houdini's order:
    those Houdini does not hide (is_hidden), of a kind this version knows."""
    follow = preferences.get().follow_hide_when
    found = plan(tree)
    return [(kind, parm) for kind, parm in zip(found.kinds, tree.houdini_bridge.parms, strict=False)
            if kind is not kinds.UNKNOWN and not interface.is_hidden(parm, follow)]


# --- one draw: what shows, then the painter -------------------------------------------


def walk(tree, painter, layout):
    """Go through the parameters of a node group as the panel draws them, handing each
    folder, tab set and parameter to `painter`, into `layout`. A painter has `every_tab`
    (whether every tab of a set shows its content, or the open one only), `joined_row`,
    `parm`, `folder` and `tabs`; `folder` and `tabs` return the layout of their content, or
    None when it is folded."""
    _Walk(tree, painter).items(layout, plan(tree).root)


class _Walk:
    """One walk of a plan. Methods, not recursive closures: a closure that calls itself keeps
    the Blender structs it saw (the node group, the layouts of a draw) alive until the
    garbage collector frees them, at any later time."""

    def __init__(self, tree, painter):
        self.tree, self.painter = tree, painter
        self.found = plan(tree)
        self.parms = tree.houdini_bridge.parms
        follow = preferences.get().follow_hide_when
        self.shown = [kind is not kinds.UNKNOWN and not interface.is_hidden(parm, follow)
                      for kind, parm in zip(self.found.kinds, self.parms, strict=False)]
        self.visible = {}

    def has_content(self, node):
        key = id(node)
        if key not in self.visible:
            if isinstance(node, TabSet):
                self.visible[key] = any(self.has_content(tab) for tab in node.tabs)
            else:
                self.visible[key] = any(self.shown[item] if isinstance(item, int) else self.has_content(item)
                                        for item in node.items)
        return self.visible[key]

    def items(self, container, node):
        tree, painter = self.tree, self.painter
        joined = None  # the row of the parameters joined so far (buttons)
        for item in node.items:
            if isinstance(item, int):
                if not self.shown[item]:
                    continue
                kind = self.found.kinds[item]
                if kind.name != contract.BUTTON:  # consecutive buttons share a row, as Houdini draws them
                    joined = None
                elif joined is None:
                    joined = painter.joined_row(container)
                painter.parm(joined or container, tree, self.parms[item], kind)
                continue
            joined = None
            if not self.has_content(item):
                continue
            if isinstance(item, TabSet):
                tabs = [tab for tab in item.tabs if self.has_content(tab)]
                wanted = open_tab(tree, item)
                current = next((tab for tab in tabs if tab.key[-1] == wanted), tabs[0])
                inside = painter.tabs(container, tree, item, tabs, current)
                if inside is not None:
                    for tab in tabs if painter.every_tab else (current,):
                        self.items(inside, tab)
                continue
            inside = painter.folder(container, tree, item)
            if inside is not None:
                self.items(inside, item)


class Recorder:
    """Lists what the panel would draw, in order: ("folder", type, path), ("tabs", path, tab
    set, [labels], open label, key, [tab keys]), ("parm", name, dimmed), paths in labels and
    keys in Houdini names. Every collapsible folder counts as open; inside a set of tabs, only
    the open tab, or every tab with `every_tab`."""

    def __init__(self, every_tab=False):
        self.rows = []
        self.every_tab = every_tab

    def joined_row(self, layout):
        return layout

    def parm(self, layout, tree, parm, kind):
        self.rows.append(("parm", parm.name, parm.disabled))

    def folder(self, layout, tree, folder):
        self.rows.append(("folder", folder.type, folder.path))
        return layout

    def tabs(self, layout, tree, tabs, shown, current):
        self.rows.append(("tabs", tabs.path, tabs.name, [tab.label for tab in shown], current.label, tabs.key,
                          [tab.key[-1] for tab in shown]))
        return layout


def drawn(tree, every_tab=False):
    """What the panel would draw of a node group's parameters, with the Houdini interface on
    (Recorder.rows); with `every_tab`, the content of every tab, as if each were open."""
    recorder = Recorder(every_tab)
    walk(tree, recorder, recorder.rows)  # any layout but None: a folder it returns counts as open
    return recorder.rows
