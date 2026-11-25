"""The cook engine.

    from ..core import cook
    cook.cooks.request(tree)

queue.py holds the cooks asked for, scheduler.py the timer that sends them,
request.py builds a request, result.py applies the answer, state.py remembers
what was sent, handlers.py holds the Blender handlers. DEVELOPMENT.md (The path of a cook)
follows one cook through them. All the state here is main thread only; core/link.py
holds what the threads share.
"""

from .handlers import register, unregister
from .queue import can_cook, cooks, cooks_by_itself, mark_animated_inputs
from .request import chained_above, parm_values, peek_request, prepare_request
from .result import apply_description
from .scheduler import (
    activity,
    allow_file,
    cook_now,
    delete_instance,
    ensure_server,
    post_cook,
    release_modifier,
    send_command,
    start_server,
    stop_server,
)
from .state import sent

__all__ = [
    # the cooks asked for, paused and being cached (queue.CookQueue), and what was sent (state.CookState)
    "cooks", "sent",
    # which modifiers may cook
    "can_cook", "cooks_by_itself",
    # cooking now, or for a feature (the cache)
    "cook_now", "prepare_request", "peek_request", "post_cook", "apply_description", "parm_values",
    "mark_animated_inputs",
    # the stack as a cook reads it (the viewport group selection)
    "chained_above",
    # other commands, the server as the engine sees it
    "send_command", "ensure_server", "start_server", "stop_server",
    # the open file allowed to cook (core/trust.py)
    "allow_file",
    # modifiers going away
    "release_modifier", "delete_instance",
    # the viewport feedback
    "activity",
    # registration
    "register", "unregister",
]
