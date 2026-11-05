import hashlib
import queue
from contextlib import closing
import re
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from yehu_style_go.online_client import OnlineClient
from yehu_style_go.online_server import OnlineError, OnlineServer, OnlineService


class AccountServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "online.sqlite3"
        self.service = OnlineService(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_register_login_uid_and_password_storage(self):
        alice, token = self.service.register("Alice", "secret1")
        self.assertRegex(alice.uid, r"^\d{9}$")
        self.assertTrue(token)
        with self.assertRaisesRegex(OnlineError, "已被注册"):
            self.service.register("alice", "secret2")
        with self.assertRaisesRegex(OnlineError, "用户名或密码错误"):
            self.service.login("Alice", "wrong-password")
        account, new_token = self.service.login("Alice", "secret1")
        self.assertEqual(account.uid, alice.uid)
        self.assertTrue(new_token)
        with self.assertRaisesRegex(OnlineError, "登录已失效"):
            self.service.account_for_token(token)
        with closing(sqlite3.connect(self.db)) as connection:
            salt, digest = connection.execute(
                "SELECT salt, password_hash FROM users WHERE username='Alice'").fetchone()
        self.assertNotEqual(digest, "secret1")
        self.assertEqual(len(digest), 64)
        self.assertEqual(
            hashlib.pbkdf2_hmac("sha256", b"secret1", bytes.fromhex(salt), 200_000).hex(),
            digest,
        )

    def test_unique_uids_across_accounts(self):
        uids = {self.service.register(f"user{index}", "secret1")[0].uid for index in range(12)}
        self.assertEqual(len(uids), 12)
        self.assertTrue(all(re.fullmatch(r"\d{9}", uid) for uid in uids))


class RoomServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = OnlineService(Path(self.temp.name) / "online.sqlite3")
        self.alice, _ = self.service.register("Alice", "secret1")
        self.bob, _ = self.service.register("Bob", "secret2")

    def tearDown(self):
        self.temp.cleanup()

    def enter_game(self):
        room = self.service.create_room(self.alice, 9, 7.5, "B")
        joined = self.service.join_room(self.bob, room.code)
        self.assertEqual(joined.status, "playing")
        return joined

    def test_moves_turn_validation_and_scoring(self):
        room = self.enter_game()
        self.assertEqual(room.board.to_play, "B")
        self.service.move(self.alice, 2, 2)
        with self.assertRaisesRegex(OnlineError, "还没轮到"):
            self.service.move(self.alice, 3, 3)
        with self.assertRaisesRegex(OnlineError, "坐标无效"):
            self.service.move(self.bob, 6.0, 6.0)
        with self.assertRaisesRegex(OnlineError, "已有棋子"):
            self.service.move(self.bob, 2, 2)
        self.service.move(self.bob, 6, 6)
        self.service.move(self.alice, None, None)
        self.service.move(self.bob, None, None)
        state = self.service.state(self.alice)
        self.assertEqual(state["status"], "scoring")
        self.assertIn("W+", state["result"])
        self.assertIn("未自动标记死子", state["warning"])
        first = self.service.confirm_score(self.alice)
        self.assertEqual(first.status, "scoring")
        finished = self.service.confirm_score(self.bob)
        self.assertEqual(finished.status, "finished")
        self.assertEqual(self.service.state(self.bob)["result"], finished.result)

    def test_resume_after_two_passes_removes_only_those_passes(self):
        self.enter_game()
        self.service.move(self.alice, 2, 2)
        self.service.move(self.bob, 6, 6)
        self.service.move(self.alice, None, None)
        self.service.move(self.bob, None, None)
        resumed = self.service.resume_game(self.alice)
        self.assertEqual(resumed.status, "playing")
        self.assertEqual(len(resumed.board.move_history), 2)
        self.assertEqual(resumed.board.to_play, "B")

    def test_resign_and_leave(self):
        self.enter_game()
        finished = self.service.resign(self.alice)
        self.assertEqual(finished.result, "W+R")
        self.service.leave_room(self.alice)
        self.assertIsNone(self.service.state(self.alice))
        self.assertIsNone(self.service.leave_room(self.bob))
        self.assertIsNone(self.service.state(self.bob))

    def test_join_requires_existing_waiting_room(self):
        with self.assertRaisesRegex(OnlineError, "房间不存在"):
            self.service.join_room(self.bob, "000000")
        room = self.service.create_room(self.alice, 19, 7.5, "W")
        joined = self.service.join_room(self.bob, room.code)
        colors = {seat.account.username: seat.color for seat in joined.seats.values()}
        self.assertEqual(colors, {"Alice": "W", "Bob": "B"})


class SocketProtocolTests(unittest.TestCase):
    def test_two_online_clients_complete_a_room(self):
        with tempfile.TemporaryDirectory() as directory:
            service = OnlineService(Path(directory) / "online.sqlite3")
            server = OnlineServer(("127.0.0.1", 0), service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            events = queue.Queue()
            first = second = None
            try:
                address = f"127.0.0.1:{server.port}"
                first = OnlineClient(address, events.put)
                second = OnlineClient(address, events.put)
                alice = first.request("register", username="Alice", password="secret1")
                bob = second.request("register", username="Bob", password="secret2")
                room = first.request("create_room", token=alice["token"], size=9,
                                     komi=7.5, color="B")["room"]
                second.request("join_room", token=bob["token"], room=room["room"])
                first.request("move", token=alice["token"], x=2, y=2)
                state = second.request("state", token=bob["token"])["room"]
                self.assertEqual(state["move_count"], 1)
                self.assertEqual(state["to_play"], "W")
                event = events.get(timeout=3)
                self.assertEqual(event["event"], "state")
                self.assertEqual(event["state"]["room"], room["room"])
                self.assertNotIn("password", str(event["state"]))
                second.close()
                second = OnlineClient(address, events.put)
                relogin = second.request("login", username="Bob", password="secret2")
                self.assertEqual(relogin["room"]["room"], room["room"])
                self.assertEqual(relogin["room"]["status"], "playing")
            finally:
                if first is not None:
                    first.close()
                if second is not None:
                    second.close()
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
