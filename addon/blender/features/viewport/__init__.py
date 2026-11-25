"""Drawing over the 3D view: the cooking feedback and the legends (overlay.py, which
draws the visualizers of visualize.py), the ghost wireframes of the Template flag
(template.py), and the connection button of the header (status.py)."""

from . import overlay, status, template

# Registered in this order, unregistered in the reverse one. visualize.py registers
# nothing: overlay.py draws it.
MODULES = (overlay, template, status)


def register():
    for module in MODULES:
        module.register()


def unregister():
    for module in reversed(MODULES):
        module.unregister()
