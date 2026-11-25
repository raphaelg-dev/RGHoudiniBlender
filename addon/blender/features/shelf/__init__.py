"""The shelf: the catalog of Houdini tools with its sidebar list and Shift+A menu
(catalog.py), and the asset library, asset shelf and favorites pie made from it
(assets.py), and the Add Houdini Node button of the modifier panel (add_node.py)."""

from . import add_node, assets, catalog

# Registered in this order, unregistered in the reverse one: the catalog first, since
# the asset library and the Add Houdini Node search are built from it.
MODULES = (catalog, assets, add_node)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
