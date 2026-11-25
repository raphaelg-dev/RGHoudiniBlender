"""The server's logger, `log`, written on hython's standard output.

The client reads that output: the line `HOUDINI_BRIDGE_PORT <n>` (contract.PORT_LINE) tells
it the server listens, and every line shows in "Server Log". So each message is one line of
bare text, flushed at once, on whatever sys.stdout currently is. The logger does not
propagate, so handlers Houdini sets on the root logger never see it.
"""

import logging
import sys


class _StandardOutput(logging.Handler):
    def emit(self, record):
        try:
            sys.stdout.write(self.format(record) + "\n")
            sys.stdout.flush()
        except Exception:
            self.handleError(record)


# Named after its package (rg_houdini_bridge.houdini in hython).
log = logging.getLogger(__name__.rpartition(".")[0] or __name__)
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    _handler = _StandardOutput()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_handler)
