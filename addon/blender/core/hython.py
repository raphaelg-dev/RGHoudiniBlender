"""Starting the hython server and exchanging requests with it."""

import collections
import ctypes
import glob
import os
import re
import secrets
import socket
import subprocess
import threading
import time

from ...shared import contract, protocol

# The root of the extension, where hython_entry.py sits: this file is in blender/core/.
ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENTRY_SCRIPT = os.path.join(ADDON_DIR, "hython_entry.py")
LOG_LINES = 200  # lines of hython's output kept, for the errors and the log
STARTUP_LOG_TAIL = 15  # lines of that output shown when hython stops during startup
PORT_POLL = 0.2  # seconds between two checks that the port line came
CONNECT_TIMEOUT = 10  # seconds the socket waits for the server
STOP_TIMEOUT = 3  # seconds stop() waits for hython before it kills it
STARTUP_TIMEOUT = 180  # seconds start() waits for the port line: the default of the preference


PATTERNS = [
    r"C:\Program Files\Side Effects Software\Houdini *\bin\hython.exe",
    "/opt/hfs*/bin/hython",
    "/Applications/Houdini/Houdini*/Frameworks/Houdini.framework/Versions/Current/Resources/bin/hython",
]


def _version(path):
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", path)
    return tuple(int(x) for x in found.groups()) if found else (0, 0, 0)


def _registered_folders():
    """Install folders of the Houdini versions SideFX's installer registered (Windows)."""
    try:
        import winreg
    except ImportError:
        return []
    folders = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Side Effects Software\Houdini")
    except OSError:
        return []
    with key:
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if re.match(r"^\d+\.\d+", name) and isinstance(value, str):
                folders.append(value)
    return folders


def find_installations():
    """[(version, hython path)] of every installed Houdini, newest first."""
    candidates = set()
    for pattern in PATTERNS:
        candidates.update(os.path.normpath(path) for path in glob.glob(pattern))
    for folder in _registered_folders():
        hython = os.path.normpath(os.path.join(folder, "bin", "hython.exe"))
        if os.path.exists(hython):
            candidates.add(hython)
    found = {}
    for path in candidates:
        found.setdefault(os.path.normcase(path), path)
    installations = [(".".join(str(part) for part in _version(path)), path) for path in found.values()]
    return sorted(installations, key=lambda item: _version(item[1]), reverse=True)


def find_hython():
    """The hython of the most recent installed Houdini, or None."""
    installations = find_installations()
    return installations[0][1] if installations else None


class ServerError(RuntimeError):
    pass


# --- hython dies with this process (Windows) ------------------------------------------

JOB_EXTENDED_LIMITS = 9  # JOBOBJECTINFOCLASS JobObjectExtendedLimitInformation
KILL_ON_JOB_CLOSE = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
SILENT_BREAKAWAY_OK = 0x1000  # JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK: hython's own children stay out
PROCESS_ACCESS = 0x0100 | 0x0001  # PROCESS_SET_QUOTA | PROCESS_TERMINATE, what AssignProcessToJobObject needs
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # what GetExitCodeProcess needs
STILL_ACTIVE = 259  # the exit code of a process that runs
ERROR_ACCESS_DENIED = 5
_job = {"handle": None, "kernel32": None}  # created once, its handle never closed


class _BasicLimits(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _ExtendedLimits(ctypes.Structure):  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _kernel32():
    kernel32 = _job["kernel32"]
    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        kernel32.SetInformationJobObject.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
        _job["kernel32"] = kernel32
    return kernel32


def _last_error():
    return ctypes.FormatError(ctypes.get_last_error()).strip()


def _job_handle(kernel32):
    """The job hython goes into, which kills what it holds when its last handle closes. This
    process holds the only one and never closes it: the system does when the process ends,
    killed or crashed included. A reloaded extension makes another job; the old one keeps
    only stopped processes."""
    if _job["handle"] is None:
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE | SILENT_BREAKAWAY_OK
        if not kernel32.SetInformationJobObject(job, JOB_EXTENDED_LIMITS, ctypes.byref(limits),
                                                ctypes.sizeof(limits)):
            error = _last_error()
            kernel32.CloseHandle(job)
            raise OSError(error)
        _job["handle"] = job
    return _job["handle"]


def die_with_this_process(pid):
    """On Windows, have a process killed when this one ends, also killed or crashed, where
    stop() never runs and hython would keep its licence. Its children stay out
    (SILENT_BREAKAWAY_OK): an hserver it starts serves other Houdini sessions. Elsewhere,
    nothing. Returns why it failed, "" otherwise."""
    if os.name != "nt":
        return ""
    try:
        kernel32 = _kernel32()
        job = _job_handle(kernel32)
        process = kernel32.OpenProcess(PROCESS_ACCESS, False, pid)
        if not process:
            return _last_error()
        try:
            return "" if kernel32.AssignProcessToJobObject(job, process) else _last_error()
        finally:
            kernel32.CloseHandle(process)
    except OSError as error:
        return str(error)


def process_alive(pid):
    """Whether a process of that id runs. A process whose id was given again to another one
    counts as alive: a caller then keeps what it would have removed."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)  # signal 0 checks, and sends nothing
        except PermissionError:
            return True  # someone else's
        except OSError:
            return False
        return True
    kernel32 = _kernel32()
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = ctypes.c_uint32()
        return not kernel32.GetExitCodeProcess(process, ctypes.byref(code)) or code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(process)


class Connection:
    """One hython server and its socket. One request at a time."""

    def __init__(self, hython, skip_packages=False):
        self.hython = hython
        self.skip_packages = skip_packages
        self.process = None
        self.sock = None
        self.houdini_version = ""
        self.log = collections.deque(maxlen=LOG_LINES)
        self.lock = threading.Lock()
        self.stopping = threading.Lock()  # stop() may be called from two threads at once
        self.startup_time = 0.0

    @property
    def alive(self):
        return self.sock is not None and self.process is not None and self.process.poll() is None

    def start(self, timeout=STARTUP_TIMEOUT):
        start = time.perf_counter()
        token = secrets.token_hex(16)
        environment = dict(os.environ)
        for name in ("PYTHONHOME", "PYTHONPATH"):
            environment.pop(name, None)
        environment[contract.TOKEN_VARIABLE] = token
        if self.skip_packages:
            environment["HOUDINI_PACKAGE_SKIP"] = "1"
        self.process = subprocess.Popen(
            [self.hython, ENTRY_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        failure = die_with_this_process(self.process.pid)
        if failure:
            self.log.append("hython does not end with Blender if Blender is killed: %s" % failure)
        try:
            self._connect(start, token, timeout)
        except BaseException:
            self.stop()  # whatever fails once hython runs, it does not stay alive
            raise

    def _connect(self, start, token, timeout):
        """Wait for the port hython prints, connect, then say hello (start() stops hython
        when this fails)."""
        port_found = threading.Event()
        port = [0]

        def read_output():
            for line in self.process.stdout:
                line = line.rstrip()
                self.log.append(line)
                if line.startswith(contract.PORT_LINE + " "):
                    port[0] = int(line.split()[1])
                    port_found.set()

        threading.Thread(target=read_output, name="houdini-bridge-output", daemon=True).start()

        while not port_found.wait(PORT_POLL):
            if self.process.poll() is not None:
                raise ServerError("hython stopped during startup:\n" + "\n".join(list(self.log)[-STARTUP_LOG_TAIL:]))
            if time.perf_counter() - start > timeout:
                raise ServerError("hython did not answer within %d s" % timeout)

        self.sock = socket.create_connection(("127.0.0.1", port[0]), timeout=CONNECT_TIMEOUT)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(None)
        protocol.send(self.sock, contract.request(contract.HELLO, token=token, version=contract.PROTOCOL_VERSION))
        answer, _ = protocol.receive(self.sock)
        server_version = answer.get(contract.VERSION)
        if server_version != contract.PROTOCOL_VERSION:
            theirs = "no version" if server_version is None else "version %s" % server_version
            raise ServerError("protocol version mismatch: this extension speaks version %d, its hython server %s. "
                              "Reinstall the extension, then restart Blender." % (contract.PROTOCOL_VERSION, theirs))
        if contract.ERROR in answer:
            raise ServerError(answer[contract.ERROR])
        self.houdini_version = answer.get("houdini", "")
        self.startup_time = time.perf_counter() - start

    def request(self, header, arrays=None):
        with self.lock:
            sock = self.sock  # read once: stop(), on another thread, sets it to None without this lock
            if sock is None:
                raise ServerError("server stopped")
            try:
                protocol.send(sock, header, arrays)
                answer, output = protocol.receive(sock)
            except (ConnectionError, OSError) as error:
                self.stop()
                raise ServerError("lost connection to hython: %s" % error) from error
            except Exception as error:  # out of memory, a header misread: the stream is no longer in step
                self.stop()
                raise ServerError("hython stopped, its answer could not be read: %r" % error) from error
        if contract.ERROR in answer:
            raise ServerError(answer[contract.ERROR])
        return answer, output

    def stop(self):
        """Close the socket and wait for hython (STOP_TIMEOUT, then it is killed). Safe to call from
        two threads at once (a failing request on the worker thread, Stop on the main
        thread): the second waits for the first, then finds nothing left to stop."""
        with self.stopping:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
            if self.process is not None:
                try:
                    self.process.wait(timeout=STOP_TIMEOUT)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                self.process = None
