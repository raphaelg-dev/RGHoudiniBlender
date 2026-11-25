"""Finding hython (the version a path names, the order of the installations), and the
Connection's life: a failed start leaves no process, stop() from two threads at once; whether
a process runs."""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from tests import support

client = support.source("hython")

HOUDINI = r"C:\Program Files\Side Effects Software\Houdini %s\bin\hython.exe"


class Version(unittest.TestCase):
    def test_reads_the_version_of_the_folder(self):
        self.assertEqual(support.hython_version(client, HOUDINI % "22.0.429"), (22, 0, 429))
        self.assertEqual(support.hython_version(client, "/opt/hfs21.5.603/bin/hython"), (21, 5, 603))

    def test_compares_numbers_not_text(self):
        self.assertGreater(support.hython_version(client, HOUDINI % "20.5.10"),
                           support.hython_version(client, HOUDINI % "20.5.9"))

    def test_no_version(self):
        self.assertEqual(support.hython_version(client, r"D:\tools\hython.exe"), (0, 0, 0))


class Installations(unittest.TestCase):
    def test_newest_first(self):
        found = [HOUDINI % "20.5.613", HOUDINI % "22.0.429", HOUDINI % "21.0.440"]
        with support.fake_installations(client, found):
            installations = client.find_installations()
        self.assertEqual([version for version, _ in installations], ["22.0.429", "21.0.440", "20.5.613"])
        self.assertEqual(installations[0][1], HOUDINI % "22.0.429")

    def test_registered_folders_join_the_list_once(self):
        registered = [r"C:\Program Files\Side Effects Software\Houdini 21.0.440",
                      r"E:\Houdini 22.0.429"]
        # The path the client makes of a registered folder, with the separators of this
        # platform: the registry is Windows', the test runs anywhere.
        found = [os.path.normpath(os.path.join(registered[0], "bin", "hython.exe"))]
        with support.fake_installations(client, found, registered):
            installations = client.find_installations()
            newest = client.find_hython()
        self.assertEqual([version for version, _ in installations], ["22.0.429", "21.0.440"])
        self.assertEqual(newest, os.path.normpath(os.path.join(registered[1], "bin", "hython.exe")))

    def test_nothing_installed(self):
        with support.fake_installations(client, []):
            self.assertEqual(client.find_installations(), [])
            self.assertIsNone(client.find_hython())


# A stand-in for the server, run by this Python as hython would run hython_entry.py: it
# prints its port, accepts the client, closes the socket before any hello answer, then
# stays alive for a while as a stuck hython would.
FAKE_SERVER = """\
import socket, time
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(1)
print("HOUDINI_BRIDGE_PORT %d" % listener.getsockname()[1], flush=True)
client, _ = listener.accept()
client.close()
time.sleep(30)
"""


class Start(unittest.TestCase):
    def test_a_failed_hello_stops_hython(self):
        launched = []
        real_popen = subprocess.Popen

        def recording(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.append(process)
            return process

        with tempfile.TemporaryDirectory() as folder:
            script = os.path.join(folder, "fake_server.py")
            with open(script, "w", encoding="utf-8") as file:
                file.write(FAKE_SERVER)
            connection = client.Connection(sys.executable)
            try:
                entry = mock.patch.object(client, "ENTRY_SCRIPT", script)
                popen = mock.patch.object(client.subprocess, "Popen", recording)
                with entry, popen, self.assertRaises((ConnectionError, OSError, client.ServerError)):
                    connection.start(timeout=60)
                self.assertEqual(len(launched), 1)
                self.assertIsNone(connection.process)
                self.assertIsNone(connection.sock)
                # stop() waited for it, then killed it: it is gone, long before its 30 s.
                launched[0].wait(timeout=10)
            finally:
                for process in launched:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    process.stdout.close()


class FakeProcess:
    def __init__(self):
        self.waits = 0

    def wait(self, timeout=None):
        self.waits += 1
        time.sleep(0.2)  # long enough for every other thread to reach stop()
        return 0

    def kill(self):
        pass


class FakeSocket:
    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1


class Stop(unittest.TestCase):
    def test_stop_from_several_threads_at_once(self):
        connection = client.Connection("hython")
        process, sock = FakeProcess(), FakeSocket()
        connection.process, connection.sock = process, sock
        count = 8
        barrier = threading.Barrier(count)
        errors = []

        def stop():
            barrier.wait()
            try:
                connection.stop()
            except Exception as error:  # the defect: process set to None between the test and wait()
                errors.append(error)

        threads = [threading.Thread(target=stop) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual((process.waits, sock.closes), (1, 1))
        self.assertIsNone(connection.process)
        self.assertIsNone(connection.sock)


class StoppedDuringRequest(unittest.TestCase):
    def test_a_stop_between_send_and_receive_is_a_lost_connection(self):
        """stop() on another thread, while the worker waits for an answer: the socket the
        request uses is closed under it, and the connection's is None."""
        connection = client.Connection("hython")

        class StoppedSocket(FakeSocket):
            def sendall(self, data):
                connection.sock = None  # what stop() does meanwhile
                self.close()

            def recv_into(self, view, size):
                raise OSError(10038, "An operation was attempted on something that is not a socket")

        connection.sock = StoppedSocket()
        with self.assertRaisesRegex(client.ServerError, "lost connection"):
            connection.request(client.contract.request(client.contract.PING))


class UnreadableAnswer(unittest.TestCase):
    def test_an_answer_that_cannot_be_read_stops_hython(self):
        """Any error between send and receive leaves the stream out of step: the next request
        would read blocks as a header. The connection is stopped instead."""
        connection = client.Connection("hython")
        sock = FakeSocket()
        sock.sendall = lambda data: None
        connection.sock = sock
        with mock.patch.object(client.protocol, "receive", side_effect=MemoryError()):
            with self.assertRaisesRegex(client.ServerError, "could not be read"):
                connection.request(client.contract.request(client.contract.PING))
        self.assertIsNone(connection.sock)
        self.assertEqual(sock.closes, 1)


class ProcessAlive(unittest.TestCase):
    def test_a_running_process_and_an_ended_one(self):
        self.assertTrue(client.process_alive(os.getpid()))
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait()
        self.assertFalse(client.process_alive(process.pid))


if __name__ == "__main__":
    unittest.main()
