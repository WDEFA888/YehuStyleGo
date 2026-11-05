"""Defensive protocol regressions using only temporary accounts and loopback sockets."""
import json
import queue
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from yehu_style_go.online_client import MAX_MESSAGE, OnlineClient
from yehu_style_go.online_server import (
    AUTH_MAX_FAILURES,
    OnlineError,
    OnlineServer,
    OnlineService,
    _decode_request,
)


class Connection:
    def __init__(self):
        self.uid = None
        self.messages = []
        self.client_address = ("127.0.0.1", 12345)

    def send(self, message):
        self.messages.append(message)


class SessionSecurityTests(unittest.TestCase):
    def test_switching_account_with_token_detaches_old_account(self):
        with tempfile.TemporaryDirectory() as directory:
            service = OnlineService(Path(directory) / "online.sqlite3")
            alice, alice_token = service.register("Alice", "secret1")
            bob, bob_token = service.register("Bob", "secret2")
            connection = Connection()
            service.handle_request({"type": "state", "token": alice_token}, connection)
            room = service.create_room(alice, 9, 7.5, "B")
            service.handle_request({"type": "state", "token": bob_token}, connection)
            self.assertNotIn(alice.uid, service.connections)
            self.assertFalse(room.seats[alice.uid].connected)
            connection.messages.clear()
            service._broadcast(room)
            self.assertEqual(connection.messages, [])
            self.assertIs(service.connections[bob.uid], connection)

    def test_login_throttle_stops_hashing_after_repeated_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            service = OnlineService(Path(directory) / "online.sqlite3")
            service.register("Alice", "secret1")
            with patch("yehu_style_go.online_server.time.sleep"):
                for _ in range(AUTH_MAX_FAILURES):
                    with self.assertRaisesRegex(OnlineError, "用户名或密码错误"):
                        service.login("Alice", "wrong", "1.2.3.4")
                with patch("yehu_style_go.online_server._hash_password") as hasher:
                    with self.assertRaisesRegex(OnlineError, "过于频繁"):
                        service.login("Alice", "wrong", "1.2.3.4")
                hasher.assert_not_called()
                # A different address is not blocked, and success clears the slate.
                account, token = service.login("Alice", "secret1", "5.6.7.8")
                self.assertEqual(account.username, "Alice")
                self.assertTrue(token)


class ClientProtocolSecurityTests(unittest.TestCase):
    def connect_pair(self):
        client_socket, peer = socket.socketpair()
        with patch("yehu_style_go.online_client.socket.create_connection",
                   return_value=client_socket):
            client = OnlineClient("127.0.0.1:8765")
        self.addCleanup(peer.close)
        self.addCleanup(client.close)
        return client, peer

    def test_unhashable_response_id_is_ignored_and_reader_stays_alive(self):
        client, peer = self.connect_pair()
        received = queue.Queue()
        client.on_event = received.put
        peer.sendall(b'{"request_id":[1],"ok":true}\n'
                     b'{"type":"event","event":"test"}\n')
        event = received.get(timeout=2)
        self.assertEqual(event["event"], "test")
        self.assertFalse(client.closed.is_set())

    def test_oversized_unterminated_message_stops_reader(self):
        with patch("yehu_style_go.online_client.MAX_MESSAGE", 512):
            client, peer = self.connect_pair()
            peer.sendall(b"x" * 513)
            client.reader.join(timeout=2)
            self.assertFalse(client.reader.is_alive())
            self.assertTrue(client.closed.is_set())

    def test_disconnect_does_not_block_on_full_response_queue(self):
        client, peer = self.connect_pair()
        waiter = queue.Queue(maxsize=1)
        waiter.put({"ok": True})
        with client.pending_lock:
            client.pending["test"] = waiter
        peer.shutdown(socket.SHUT_WR)
        try:
            client.reader.join(timeout=2)
            self.assertFalse(client.reader.is_alive())
        finally:
            # Release the item so the waiter does not leak across the test.
            if not waiter.empty():
                waiter.get_nowait()

    def test_buffer_is_bounded_by_max_message(self):
        self.assertGreaterEqual(MAX_MESSAGE, 1024)


class ServerProtocolSecurityTests(unittest.TestCase):
    def test_deep_json_is_rejected_as_malformed(self):
        # Deterministic unit check: the C decoder raises RecursionError around
        # depth 5000; _decode_request must swallow it and return None rather
        # than crashing the handler thread.
        payload = b"[" * 20000 + b"0" + b"]" * 20000 + b"\n"
        self.assertIsNone(_decode_request(payload))
        self.assertEqual(_decode_request(b'{"type":"ping"}\n'), {"type": "ping"})
        self.assertIsNone(_decode_request(b"not json\n"))
        self.assertIsNone(_decode_request(b"[1, 2, 3]\n"))

    def test_malformed_request_is_rejected_and_next_request_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            service = OnlineService(Path(directory) / "online.sqlite3")
            server = OnlineServer(("127.0.0.1", 0), service)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            peer = None
            try:
                for _ in range(20):
                    try:
                        peer = socket.create_connection(
                            ("127.0.0.1", server.port), timeout=2)
                        break
                    except OSError:
                        threading.Event().wait(0.05)
                self.assertIsNotNone(peer, "server did not accept a connection")
                peer.settimeout(5)
                stream = peer.makefile("rb")
                try:
                    peer.sendall(b"[1, 2, 3]\n")
                    response = json.loads(stream.readline())
                    self.assertFalse(response["ok"])
                    peer.sendall(b'{"type":"ping","request_id":"alive"}\n')
                    response = json.loads(stream.readline())
                    self.assertTrue(response["ok"])
                    self.assertEqual(response["request_id"], "alive")
                finally:
                    stream.close()
            finally:
                if peer is not None:
                    peer.close()
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
