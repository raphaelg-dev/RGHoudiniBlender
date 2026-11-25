"""The link with the hython server: starting it, the worker thread, the queues.

One Houdini session at a time, held by `server`. The main thread asks for a start (in the
background by default: ensure_running), posts requests (post) and takes the
answers (take_results); the worker thread does the round trips, so the
interface does not wait for Houdini. What must be answered before going on (the
definitions of an HDA file, a synchronous cook) uses request_sync, which waits
while the worker is busy (one request at a time on the socket).

A new session is announced to the main thread (take_new_session): see cook/scheduler.py.

The threads, all daemons named houdini-bridge-*:

- houdini-bridge-startup: one background start at a time. A start that ends
  after stop() or shutdown() (or after a blocking start published another
  connection) stops the hython it started at once;
- houdini-bridge-cook: the worker, from the first start until shutdown(),
  which stops it (join bounded by WORKER_JOIN seconds);
- houdini-bridge-output: reads hython's output (hython.Connection) and ends
  when hython exits.

Nothing starts at registration: hython starts on first use, or with Blender
(cook/handlers.py), and the cook engine's unregister() shuts the link down.
"""

import collections
import threading
import time

import bpy

from . import feedback, hython, preferences

WORKER_JOIN = 5.0  # seconds shutdown() waits for the worker
MAX_STATUS_TEXT = 120  # characters of a startup error shown as the status


class ServerLink:
    """The connection to hython, the threads around it and their queues.

    Who writes what (`lock` guards the fields marked so):

    - connection (lock): the thread that started it, when it publishes it
      (_connect: the startup thread, or the main thread for a blocking start);
      the main thread when it stops it (stop);
    - generation (lock): the main thread, at each stop and blocking start; a start
      launched under an older generation is undone when it ends;
    - starting: set by the main thread, cleared by the startup thread when it ends;
    - startup_error: the startup thread (its own failure, if still current), the
      main thread (cleared);
    - outbox (lock): the main thread appends (post), the worker takes;
    - results: the worker appends, the main thread takes (take_results);
    - last_activity, idle_stop: the main thread, and the thread that publishes a
      connection (lock);
    - announced: the main thread (take_new_session);
    - worker, worker_stop (lock): the thread that publishes a connection, and
      the main thread in shutdown.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.wakeup = threading.Event()
        self.outbox = collections.deque()  # (key, connection, header, arrays) for the worker
        self.results = collections.deque()  # (key, answer, output, error, duration) for the main thread
        self.worker = None
        self.worker_stop = None
        self.connection = None
        self.generation = 0
        self.starting = False
        self.startup_error = ""
        self.announced = None  # the connection last announced to the main thread
        self.last_activity = time.monotonic()  # last time Houdini was asked for something
        self.idle_stop = 0  # minutes without a cook after which it was stopped, 0 otherwise

    def is_running(self):
        connection = self.connection
        return connection is not None and connection.alive

    def touch(self):
        self.last_activity = time.monotonic()
        self.idle_stop = 0

    def ensure_running(self, blocking=False):
        """True when the server answers; otherwise start it, in the background by default
        (False then), or now with `blocking` (raises hython.ServerError when it fails). After
        a failed background start, False until startup_error is cleared: no restart loop."""
        if self.is_running():
            return True
        if blocking:
            self.startup_error = ""
            with self.lock:
                self.generation += 1  # a background start under way is undone when it ends
                generation = self.generation
            self._connect(generation, _launch_options())
            return True
        if self.startup_error:
            return False  # no restart loop: the Start button clears the error
        if not self.starting:
            self.starting = True
            self.startup_error = ""
            with self.lock:
                generation = self.generation
            threading.Thread(target=self._start_in_background, args=(generation, _launch_options()),
                             name="houdini-bridge-startup", daemon=True).start()
        return False

    def _start_in_background(self, generation, options):
        try:
            self._connect(generation, options)
        except Exception as error:  # shown in the panel
            with self.lock:
                if generation == self.generation:
                    self.startup_error = str(error)
        finally:
            self.starting = False

    def _connect(self, generation, options):
        """Start hython, then publish its connection and make sure the worker runs, unless
        the generation changed meanwhile: that hython is then stopped at once."""
        path, skip_packages, timeout = options
        if not path:
            raise hython.ServerError("hython not found: set its path in the add-on preferences")
        connection = hython.Connection(path, skip_packages=skip_packages)
        connection.start(timeout=timeout)
        with self.lock:
            current = generation == self.generation
            if current:
                self.touch()
                self.connection = connection
                self._start_worker()
        if not current:
            connection.stop()

    def _start_worker(self):
        # Called with the lock held.
        if self.worker is not None and self.worker.is_alive() and not self.worker_stop.is_set():
            return
        self.worker_stop = threading.Event()
        self.worker = threading.Thread(target=self._work, args=(self.worker_stop,), name="houdini-bridge-cook",
                                       daemon=True)
        self.worker.start()

    def _work(self, stop):
        """The worker: send each request to the connection it was posted for and queue the
        answer (an error for a connection stopped meanwhile), until `stop` is set."""
        while True:
            self.wakeup.wait()
            if stop.is_set():
                return
            with self.lock:
                if not self.outbox:
                    self.wakeup.clear()
                    continue
                key, connection, header, arrays = self.outbox.popleft()
            start = time.perf_counter()
            try:
                if connection is None:
                    raise hython.ServerError("server stopped")
                answer, output = connection.request(header, arrays)
                self.results.append((key, answer, output, "", time.perf_counter() - start))
            except hython.ServerError as error:
                self.results.append((key, {}, {}, str(error), time.perf_counter() - start))
            except Exception as error:  # a fault of this code: the request still gets its answer
                feedback.log.exception("a request could not be sent")
                self.results.append((key, {}, {}, str(error), time.perf_counter() - start))

    def post(self, key, header, arrays=None):
        """Queue a request for the worker, bound to the connection running now (main thread).
        Its answer comes back through take_results() under `key` (None: nobody waits for it);
        a request whose connection stopped meanwhile is answered with an error."""
        self.touch()
        with self.lock:
            self.outbox.append((key, self.connection, header, arrays or {}))
        self.wakeup.set()

    def take_results(self):
        """The answers arrived, oldest first, as (key, answer, output, error, seconds), including
        those arriving while they are read (main thread)."""
        while self.results:
            yield self.results.popleft()

    def take_new_session(self):
        """True once for each new connection that answers (main thread)."""
        connection = self.connection
        if connection is not None and connection.alive and connection is not self.announced:
            self.announced = connection
            return True
        return False

    def request_sync(self, header, arrays=None):
        """Send one request and wait for its answer: (answer, output). Raises hython.ServerError
        when no server runs, the server answers an error or the connection is lost."""
        connection = self.connection
        if connection is None:
            raise hython.ServerError("server stopped")
        return connection.request(header, arrays)

    def stop(self):
        """Stop hython (main thread). A start under way is undone when it ends; the worker
        stays, and answers what is still queued with a connection error."""
        with self.lock:
            self.generation += 1
            connection, self.connection = self.connection, None
        if connection is not None:
            connection.stop()

    def shutdown(self):
        """Stop hython, then the worker, and empty the queues (unregister). A background start
        still under way stops the hython it started as soon as it ends."""
        self.stop()
        with self.lock:
            worker, stop = self.worker, self.worker_stop
            self.worker = self.worker_stop = None
        if stop is not None:
            stop.set()
            self.wakeup.set()
        if worker is not None:
            worker.join(WORKER_JOIN)
        with self.lock:
            self.outbox.clear()
        self.results.clear()
        self.startup_error = ""
        self.announced = None
        self.idle_stop = 0
        self.last_activity = time.monotonic()

    def status(self):
        """(text, icon) for the panel."""
        connection = self.connection
        if connection is not None and connection.alive:
            return ("Houdini %s ready (started in %.1f s)" % (connection.houdini_version, connection.startup_time),
                    "CHECKMARK")
        if self.starting:
            return "Starting hython…", "TIME"
        if self.startup_error:
            return self.startup_error.splitlines()[0][:MAX_STATUS_TEXT], "ERROR"
        if self.idle_stop:
            return "Stopped after %d min without a cook: starts again on the next one" % self.idle_stop, \
                "RADIOBUT_OFF"
        return "Server stopped", "RADIOBUT_OFF"

    def running_hython(self):
        """The path of the hython running now, or None."""
        connection = self.connection
        return connection.hython if connection is not None and connection.alive else None

    def log(self):
        """The lines hython printed, [] when it never started."""
        connection = self.connection
        return list(connection.log) if connection else []

    def idle_seconds(self):
        """Seconds since Houdini was last asked for something (a post or a start)."""
        return time.monotonic() - self.last_activity


def _launch_options():
    """(hython path, skip packages, startup timeout) from the preferences, read on the main thread."""
    prefs = preferences.get()
    path = prefs.hython_path
    return (bpy.path.abspath(path) if path else hython.find_hython(), bool(prefs.skip_packages),
            float(prefs.startup_timeout))


server = ServerLink()
