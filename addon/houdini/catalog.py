"""HDA catalog for the shelf.

For each SOP HDA: label, category, help summary, input counts, and its icon
rendered to PNG (Houdini icons are almost all SVG, which Blender cannot show).
"""

import ctypes
import hashlib
import html
import os
import re
import subprocess
import tempfile
import zipfile

import hou

from ..shared import contract
from .output import log

ICON_SIZE = 128
FALLBACK_ICON = "COMMON_subnet"
MAX_ALIAS_DEPTH = 8  # an icon alias that leads to another, at most this many times
MAX_SUMMARY_TEXT = 300  # characters kept of an HDA's help summary
DIGEST_SIZE = 10  # bytes of the digest that names a tool's icon
_icon_zip = None
_icon_aliases = None
_qt = None


def _qt_application():
    """Off-screen Qt, to render SVGs without a window."""
    global _qt
    if _qt is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtCore, QtGui, QtSvg

        application = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication([])
        _qt = (QtCore, QtGui, QtSvg, application)
    return _qt


def long_path(path):
    """Normalized path without Windows short names (C:/PROGRA~1/… → C:/Program Files/…)."""
    path = os.path.normpath(path)
    if os.name == "nt" and "~" in path:
        buffer = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetLongPathNameW(path, buffer, len(buffer)):
            path = buffer.value
    return os.path.normcase(path)


def _resolve_alias(name):
    """Follow the aliases of Houdini's IconMapping file ("SOP_delete := SOP_blast;")."""
    global _icon_aliases
    if _icon_aliases is None:
        _icon_aliases = {}
        try:
            mapping_files = hou.findFiles("config/Icons/IconMapping")
        except hou.OperationFailed:
            mapping_files = ()
        for path in mapping_files:
            try:
                with open(path, encoding="utf-8", errors="replace") as file:
                    for alias, target in re.findall(r"^\s*(\S+)\s*:=\s*(\S+?)\s*;", file.read(), re.M):
                        _icon_aliases.setdefault(alias, target)
            except OSError:
                continue
    for _ in range(MAX_ALIAS_DEPTH):
        if name not in _icon_aliases:
            break
        name = _icon_aliases[name]
    return name


def _houdini_icon(name):
    """SVG bytes of an icon shipped with Houdini ("SOP_mountain" → SOP/mountain.svg)."""
    global _icon_zip
    if _icon_zip is None:
        path = hou.text.expandString("$HFS/houdini/config/Icons/icons.zip")
        _icon_zip = zipfile.ZipFile(path) if os.path.exists(path) else False
    name = _resolve_alias(name)
    if not _icon_zip or "_" not in name:
        return None
    folder, _, rest = name.partition("_")
    for candidate in ("%s/%s.svg" % (folder, rest), "%s/%s.svg" % (folder.upper(), rest)):
        try:
            return _icon_zip.read(candidate)
        except KeyError:
            continue
    return None


def _read_file(path):
    with open(path, "rb") as file:
        return file.read()


def _icon_bytes(definition, node_type=None):
    """(bytes, is_svg) of an HDA's or a native node's icon, or (None, False)."""
    icon = (definition.icon() if definition is not None else node_type.icon()) or ""
    if icon.startswith("opdef:"):
        if definition is None:
            return None, False
        section = definition.sections().get(icon.split("?", 1)[1] if "?" in icon else "")
        if section is not None:
            return bytes(section.binaryContents()), icon.lower().endswith("svg")
        return None, False
    if not icon:
        return None, False
    # A path, a file in a package's config/Icons folder, or a Houdini icon name.
    try:
        in_package = hou.findFile("config/Icons/" + icon)
    except hou.OperationFailed:  # findFile raises when nothing is found
        in_package = None
    for path in (hou.text.expandString(icon), in_package):
        if path and os.path.isfile(path):
            return _read_file(path), path.lower().endswith(".svg")
    raw = _houdini_icon(icon)
    return (raw, True) if raw else (None, False)


def _convert_houdini_image(raw):
    """Convert an image in a Houdini-only format (.pic…) to PNG with iconvert."""
    iconvert = hou.text.expandString("$HFS/bin/iconvert.exe" if os.name == "nt" else "$HFS/bin/iconvert")
    with tempfile.TemporaryDirectory() as folder:
        source = os.path.join(folder, "icon.pic")
        target = os.path.join(folder, "icon.png")
        with open(source, "wb") as file:
            file.write(raw)
        subprocess.run([iconvert, source, target], capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return _read_file(target) if os.path.exists(target) else None


def _readable_source(raw, is_svg):
    """Return a valid QSvgRenderer or QImage, or None."""
    QtCore, QtGui, QtSvg, _ = _qt_application()
    if not raw:
        return None
    if is_svg:
        renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(raw))
        return renderer if renderer.isValid() else None
    image = QtGui.QImage()
    if image.loadFromData(QtCore.QByteArray(raw)):
        return image
    converted = _convert_houdini_image(raw)
    if converted and image.loadFromData(QtCore.QByteArray(converted)):
        return image
    return None


def _write_png(png_path, paint, source):
    """paint(painter, source) on a transparent ICON_SIZE square, antialiased, saved as a PNG."""
    _, QtGui, _, _ = _qt_application()
    image = QtGui.QImage(ICON_SIZE, ICON_SIZE, QtGui.QImage.Format_ARGB32)
    image.fill(0)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    paint(painter, source)
    painter.end()
    image.save(png_path)


def _paint_fitted(painter, source):
    """A tool's icon, SVG or image, centred within a margin and keeping its proportions."""
    QtCore, QtGui, QtSvg, _ = _qt_application()
    painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
    margin = ICON_SIZE * 0.08
    frame = QtCore.QRectF(margin, margin, ICON_SIZE - 2 * margin, ICON_SIZE - 2 * margin)
    if isinstance(source, QtSvg.QSvgRenderer):
        size = source.defaultSize()
        if size.width() > 0 and size.height() > 0:
            scale = min(frame.width() / size.width(), frame.height() / size.height())
            width, height = size.width() * scale, size.height() * scale
            frame = QtCore.QRectF((ICON_SIZE - width) / 2, (ICON_SIZE - height) / 2, width, height)
        source.render(painter, frame)
    elif source is not None:
        source = source.scaled(
            int(frame.width()), int(frame.height()),
            QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation,
        )
        painter.drawImage(
            QtCore.QPointF((ICON_SIZE - source.width()) / 2, (ICON_SIZE - source.height()) / 2), source
        )


def _paint_whole(painter, source):
    """An SVG of Houdini's over the whole square, as the interface icons and the logo are drawn."""
    QtCore = _qt_application()[0]
    source.render(painter, QtCore.QRectF(0, 0, ICON_SIZE, ICON_SIZE))


def render_icon(definition, png_path, node_type=None):
    """An HDA's or a native node's icon as a PNG; Houdini's subnet icon when it has none
    that Qt can read, so that every tool of the shelf has one."""
    source = _readable_source(*_icon_bytes(definition, node_type))
    if source is None:
        source = _readable_source(_houdini_icon(FALLBACK_ICON), True)
    _write_png(png_path, _paint_fitted, source)


def _tool_icon(icon_dir, key, label, definition, node_type=None):
    """The PNG of a tool's icon in icon_dir, named by a digest of `key`, which changes when the
    icon may: rendered only when that file is missing. "" when it cannot be rendered, since a
    broken icon must not empty the shelf."""
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=DIGEST_SIZE).hexdigest()
    png_path = os.path.join(icon_dir, digest + ".png")
    if not os.path.exists(png_path):
        try:
            render_icon(definition, png_path, node_type)
        except Exception as error:
            log.warning("icon %s: %s", label, error)
            return ""
    return png_path


LOGO_FILE = "houdini_logo.png"
# Icons of the local install drawn by the Blender interface, never shipped with the add-on:
# file written in the icon folder -> Houdini icon. The sidebar tabs read these file names.
INTERFACE_ICONS = {
    LOGO_FILE: "MISC_logo",
    "tab_modifiers.png": "MISC_digital_asset",
    "tab_groups.png": "SOP_group",
}


def render_houdini_icon(name, png_path):
    """An icon shipped with Houdini, rendered to a PNG (nothing written when it is missing)."""
    source = _readable_source(_houdini_icon(name), True)
    if source is not None:
        _write_png(png_path, _paint_whole, source)


def render_logo(png_path):
    """The Houdini logo of the local install, for the viewport badges (not shipped with the add-on)."""
    render_houdini_icon(INTERFACE_ICONS[LOGO_FILE], png_path)


def _section_text(definition, name):
    section = definition.sections().get(name)
    if section is None:
        return ""
    try:
        return section.contents()
    except hou.Error:
        return ""


def _summary(definition):
    """First help sentence: the triple-quoted line of Houdini's wiki help format."""
    found = re.search(r'"""(.+?)"""', _section_text(definition, "Help"), re.S)
    return " ".join(found.group(1).split())[:MAX_SUMMARY_TEXT] if found else ""


def _tab_submenu(definition):
    """Tab menu category ("Labs/Geometry"), read from the Tools.shelf section."""
    found = re.search(r"<toolSubmenu>(.*?)</toolSubmenu>", _section_text(definition, "Tools.shelf"), re.S)
    return html.unescape(found.group(1).strip()) if found else ""


def _describe(definition, path, group, icon_dir):
    node_type = definition.nodeType()
    date = os.path.getmtime(path) if os.path.exists(path) else 0
    icon = _tool_icon(icon_dir, "%s|%s|%s" % (path, definition.nodeTypeName(), date), definition.nodeTypeName(),
                      definition)
    return {
        "file": path.replace("\\", "/"),
        "type": definition.nodeTypeName(),
        "label": definition.description() or definition.nodeTypeName(),
        "group": group,
        "summary": _summary(definition),
        "min_inputs": node_type.minNumInputs() if node_type else definition.minNumInputs(),
        "max_inputs": min(definition.maxNumInputs(), contract.MAX_INPUTS),
        "icon": icon,
    }


def _sop_definitions(path):
    return [d for d in hou.hda.definitionsInFile(path) if d.nodeTypeCategory().name() == "Sop"]


def build(folders, packages, icon_dir, install, native=True, all_versions=False):
    """List of tools. `install(path)` installs an HDA in the session; without
    `all_versions`, only the latest version of each tool is kept."""
    os.makedirs(icon_dir, exist_ok=True)
    for file_name, icon_name in INTERFACE_ICONS.items():
        path = os.path.join(icon_dir, file_name)
        if not os.path.exists(path):
            try:
                render_houdini_icon(icon_name, path)
            except Exception as error:  # a missing icon only costs Blender's own one
                log.warning("icon %s: %s", icon_name, error)
    tools = []
    seen = set()
    extensions = (".hda", ".otl", ".hdalc", ".hdanc")

    for folder in folders:
        folder = os.path.normpath(folder)
        for root, subfolders, files in os.walk(folder):
            # Houdini keeps its safety copies in "backup" folders.
            subfolders[:] = [d for d in subfolders if d.lower() != "backup"]
            for name in sorted(files):
                if not name.lower().endswith(extensions):
                    continue
                path = os.path.join(root, name)
                try:
                    install(path)
                    definitions = _sop_definitions(path)
                except hou.Error as error:
                    log.warning("unreadable HDA %s: %s", path, error)
                    continue
                group = os.path.relpath(root, os.path.dirname(folder)).replace("\\", "/")
                for definition in definitions:
                    if definition.nodeTypeName() in seen:
                        continue
                    tools.append(_describe(definition, path, group, icon_dir))
                    seen.add(definition.nodeTypeName())

    if packages:
        # HDAs shipped with Houdini (OPlibSop.hda…) are regular nodes: they are left out.
        houdini_root = long_path(hou.text.expandString("$HFS")) + os.sep
        for path in hou.hda.loadedFiles():
            if path == "Embedded" or not os.path.exists(path):
                continue
            if long_path(path).startswith(houdini_root):
                continue
            for definition in _sop_definitions(path):
                node_type = definition.nodeType()
                if (
                    definition.nodeTypeName() in seen
                    or node_type is None
                    or node_type.definition() != definition
                    or node_type.hidden()
                    or node_type.deprecated()
                ):
                    continue
                group = _tab_submenu(definition) or "Packages"
                tools.append(_describe(definition, path, group, icon_dir))
                seen.add(definition.nodeTypeName())

    if native:
        tools.extend(_native_tools(seen, icon_dir))

    if not all_versions:
        tools = latest_versions(tools)
    tools.sort(key=lambda tool: (tool["group"].lower(), tool["label"].lower()))
    return tools


def _tab_locations():
    """Node type name → its Tab menu location, for the Tab tools Houdini declares."""
    locations = {}
    category = hou.sopNodeTypeCategory()
    for tool in hou.shelves.tools().values():
        try:
            if category not in tool.toolMenuCategories(hou.paneTabType.NetworkEditor):
                continue
            menu = tool.toolMenuLocations()
        except hou.Error:
            continue
        if menu:
            name = tool.name()
            locations.setdefault(name[4:] if name.startswith("sop_") else name, menu[0])
    return locations


def _native_tools(seen, icon_dir):
    """Houdini's own SOP nodes (PolyReduce, Boolean, Remesh…): compiled nodes and the
    HDAs shipped with Houdini, without hidden or deprecated ones."""
    houdini_root = long_path(hou.text.expandString("$HFS")) + os.sep
    version = hou.applicationVersionString()
    try:
        locations = _tab_locations()
    except hou.Error:
        locations = {}
    tools = []
    for name, node_type in hou.sopNodeTypeCategory().nodeTypes().items():
        if name in seen or node_type.hidden() or node_type.deprecated():
            continue
        definition = node_type.definition()
        if definition is not None and not long_path(definition.libraryFilePath()).startswith(houdini_root):
            continue  # an HDA from a package or a folder: listed with its own group
        _, _, base, _ = hou.hda.componentsFromFullNodeTypeName(name)
        location = locations.get(name) or locations.get(base)
        icon = _tool_icon(icon_dir, "native|%s|%s" % (name, version), name, None, node_type)
        tools.append({
            "file": "",
            "type": name,
            "label": node_type.description() or name,
            "group": "Houdini/" + location if location else "Houdini",
            "summary": "",
            "min_inputs": node_type.minNumInputs(),
            "max_inputs": min(node_type.maxNumInputs(), contract.MAX_INPUTS),
            "icon": icon,
        })
        seen.add(name)
    return tools


def _version_key(version):
    """"2.10" > "2.9" > "" (an unversioned type counts as the oldest)."""
    parts = []
    for part in version.split("."):
        parts.append(int(part) if part.isdigit() else -1)
    return tuple(parts) if version else (-1,)


def latest_versions(tools):
    """One entry per tool, its highest version, as Houdini's Tab menu shows them.

    labs::quickmaterial, labs::quickmaterial::2.0 and ::2.2 are three node types
    for Houdini, but one tool for the user."""
    best = {}
    order = []
    for tool in tools:
        _, namespace, name, version = hou.hda.componentsFromFullNodeTypeName(tool["type"])
        key = (namespace, name)
        if key not in best:
            order.append(key)
            best[key] = (version, tool)
        elif _version_key(version) > _version_key(best[key][0]):
            best[key] = (version, tool)
    return [best[key][1] for key in order]
