"""Small self-hosted online Go service: accounts, UID, rooms and server-side moves.

The same service powers both the embedded "host this computer" mode and the
standalone central server. Passwords are stored only as salted PBKDF2 hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import socketserver
import sqlite3
import sys
import threading
import time
import traceback
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yehu_style_go.board import GoBoard, IllegalMove, opposite
from yehu_style_go.scoring import AreaScore, score_area

PBKDF2_ROUNDS = 200_000
MAX_LINE = 64 * 1024
USERNAME_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{2,20}$")
ALLOWED_SIZES = {9, 13, 19}
# Best-effort login throttle: each PBKDF2 run is 200k rounds, so an attacker
# who can reach the server must not be able to burn CPU by guessing passwords.
AUTH_WINDOW_SECONDS = 60.0
AUTH_MAX_FAILURES = 10


class OnlineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Account:
    uid: str
    username: str


@dataclass
class Seat:
    account: Account
    color: str
    connected: bool = False


@dataclass
class Room:
    code: str
    size: int
    komi: float
    host_uid: str
    board: GoBoard
    status: str = "waiting"
    result: str = ""
    score_summary: str = ""
    score: AreaScore | None = None
    confirmed: set[str] = field(default_factory=set)
    seats: dict[str, Seat] = field(default_factory=dict)


def default_server_db() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "YehuStyleGo" / "online-server.sqlite3"


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS).hex()


class OnlineService:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.sessions: dict[str, Account] = {}
        self.connections: dict[str, Any] = {}
        self.rooms: dict[str, Room] = {}
        self.user_room: dict[str, str] = {}
        self.auth_failures: dict[str, list[float]] = {}
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    uid TEXT NOT NULL UNIQUE,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

    # ------------------------------------------------------------------ accounts
    def register(self, username: str, password: str) -> tuple[Account, str]:
        username = str(username or "").strip()
        password = str(password or "")
        if not USERNAME_RE.fullmatch(username):
            raise OnlineError("用户名需为 2–20 个字符，且不能包含空格或控制字符")
        if not 6 <= len(password) <= 128:
            raise OnlineError("密码长度需为 6–128 个字符")
        salt = secrets.token_bytes(16)
        digest = _hash_password(password, salt)
        with self.lock, closing(self._connect()) as connection, connection:
            for _ in range(12):
                uid = str(secrets.randbelow(900_000_000) + 100_000_000)
                try:
                    connection.execute(
                        "INSERT INTO users (username, uid, salt, password_hash, created_at) "
                        "VALUES (?,?,?,?,?)",
                        (username, uid, salt.hex(), digest,
                         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                    )
                    break
                except sqlite3.IntegrityError as error:
                    if "username" in str(error).lower():
                        raise OnlineError("该用户名已被注册") from error
                    continue
            else:
                raise OnlineError("暂时无法生成 UID，请重试")
        account = Account(uid, username)
        return account, self._new_session(account)

    def login(self, username: str, password: str, address: str = "") -> tuple[Account, str]:
        username = str(username or "").strip()
        password = str(password or "")
        if self._throttled(address):
            raise OnlineError("登录尝试过于频繁，请稍后再试")
        with self.lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT username, uid, salt, password_hash FROM users WHERE username=?",
                (username,),
            ).fetchone()
        if row is None:
            time.sleep(0.25)
            self._record_failure(address)
            raise OnlineError("用户名或密码错误")
        expected = _hash_password(password, bytes.fromhex(row["salt"]))
        if not hmac.compare_digest(expected, row["password_hash"]):
            time.sleep(0.25)
            self._record_failure(address)
            raise OnlineError("用户名或密码错误")
        self._clear_failures(address)
        account = Account(str(row["uid"]), str(row["username"]))
        return account, self._new_session(account)

    def _throttled(self, address: str) -> bool:
        with self.lock:
            recent = [t for t in self.auth_failures.get(address, [])
                      if time.monotonic() - t < AUTH_WINDOW_SECONDS]
            self.auth_failures[address] = recent
            return len(recent) >= AUTH_MAX_FAILURES

    def _record_failure(self, address: str) -> None:
        if not address:
            return
        with self.lock:
            self.auth_failures.setdefault(address, []).append(time.monotonic())

    def _clear_failures(self, address: str) -> None:
        with self.lock:
            self.auth_failures.pop(address, None)

    def _new_session(self, account: Account) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            # Invalidate older sessions for this account; one active login is enough.
            for old, owner in list(self.sessions.items()):
                if owner.uid == account.uid:
                    self.sessions.pop(old, None)
            self.sessions[token] = account
        return token

    def account_for_token(self, token: str) -> Account:
        with self.lock:
            account = self.sessions.get(str(token or ""))
        if account is None:
            raise OnlineError("登录已失效，请重新登录")
        return account

    # ---------------------------------------------------------------- connections
    def attach(self, account: Account, connection: Any) -> None:
        with self.lock:
            self.connections[account.uid] = connection
            code = self.user_room.get(account.uid)
            room = self.rooms.get(code) if code else None
            if room is not None:
                seat = room.seats.get(account.uid)
                if seat is not None:
                    seat.connected = True
        if room is not None:
            self._broadcast(room)

    def detach(self, uid: str, connection: Any) -> None:
        with self.lock:
            if self.connections.get(uid) is not connection:
                return
            self.connections.pop(uid, None)
            code = self.user_room.get(uid)
            room = self.rooms.get(code) if code else None
            if room is not None:
                seat = room.seats.get(uid)
                if seat is not None:
                    seat.connected = False
        if room is not None:
            self._broadcast(room)

    # --------------------------------------------------------------------- rooms
    def _new_room_code(self) -> str:
        while True:
            code = f"{secrets.randbelow(900000) + 100000}"
            if code not in self.rooms:
                return code

    def create_room(self, account: Account, size: int, komi: float, color: str) -> Room:
        try:
            size = int(size)
            komi = float(komi)
        except (TypeError, ValueError) as error:
            raise OnlineError("棋盘或贴目参数无效") from error
        if size not in ALLOWED_SIZES:
            raise OnlineError("棋盘只能是 9、13 或 19 路")
        if not -100 <= komi <= 100:
            raise OnlineError("贴目参数无效")
        if color not in {"B", "W", "random"}:
            raise OnlineError("执色只能是黑、白或随机")
        if color == "random":
            color = secrets.choice(("B", "W"))
        with self.lock:
            if account.uid in self.user_room:
                raise OnlineError("你已经在房间中，请先离开")
            code = self._new_room_code()
            room = Room(code=code, size=size, komi=komi, host_uid=account.uid,
                        board=GoBoard(size, "SIMPLE"))
            room.seats[account.uid] = Seat(account, color, account.uid in self.connections)
            self.rooms[code] = room
            self.user_room[account.uid] = code
        self._broadcast(room)
        return room

    def join_room(self, account: Account, code: str) -> Room:
        code = str(code or "").strip()
        with self.lock:
            room = self.rooms.get(code)
            if room is None:
                raise OnlineError("房间不存在或已关闭")
            if account.uid in room.seats:
                seat = room.seats[account.uid]
                seat.connected = account.uid in self.connections
            else:
                if account.uid in self.user_room:
                    raise OnlineError("你已经在另一个房间中，请先离开")
                if len(room.seats) >= 2 or room.status != "waiting":
                    raise OnlineError("房间已满或对局已经开始")
                host = next(iter(room.seats.values()))
                color = opposite(host.color)
                room.seats[account.uid] = Seat(account, color, account.uid in self.connections)
                self.user_room[account.uid] = code
                room.status = "playing"
        self._broadcast(room)
        return room

    def _room_for(self, account: Account) -> Room:
        with self.lock:
            code = self.user_room.get(account.uid)
            room = self.rooms.get(code) if code else None
        if room is None:
            raise OnlineError("你还没有加入房间")
        return room

    def _seat_for(self, room: Room, account: Account) -> Seat:
        seat = room.seats.get(account.uid)
        if seat is None:
            raise OnlineError("你不在这个房间中")
        return seat

    def leave_room(self, account: Account) -> None:
        with self.lock:
            code = self.user_room.pop(account.uid, None)
            room = self.rooms.get(code) if code else None
            if room is None:
                return
            seat = room.seats.get(account.uid)
            if seat is not None and room.status in {"playing", "scoring"}:
                other = next((item for uid, item in room.seats.items() if uid != account.uid), None)
                if other is not None:
                    room.status = "finished"
                    room.result = other.color + "+R"
                    room.score_summary = "对方离开房间，按认输处理"
            room.seats.pop(account.uid, None)
            if not room.seats:
                self.rooms.pop(code, None)
                for uid in list(self.user_room):
                    if self.user_room[uid] == code:
                        self.user_room.pop(uid, None)
        if room is not None:
            self._broadcast(room)

    def state(self, account: Account) -> dict[str, Any] | None:
        with self.lock:
            code = self.user_room.get(account.uid)
            room = self.rooms.get(code) if code else None
        return self._snapshot(room, account.uid) if room else None

    # --------------------------------------------------------------------- moves
    def move(self, account: Account, x: int | None, y: int | None) -> Room:
        with self.lock:
            room = self._room_for(account)
            seat = self._seat_for(room, account)
            if room.status != "playing":
                raise OnlineError("当前不在行棋阶段")
            if room.board.to_play != seat.color:
                raise OnlineError("还没轮到你落子")
            if x is None and y is None:
                pass
            elif type(x) is not int or type(y) is not int:
                raise OnlineError("落子坐标无效")
            try:
                room.board.play(seat.color, x, y)
            except (IllegalMove, ValueError) as error:
                raise OnlineError(str(error)) from error
            self._after_move(room)
        self._broadcast(room)
        return room

    def _after_move(self, room: Room) -> None:
        history = room.board.move_history
        if len(history) >= 2 and history[-1].is_pass and history[-2].is_pass:
            room.score = score_area(room.board, komi=room.komi)
            room.status = "scoring"
            room.result = room.score.result
            room.score_summary = room.score.summary()
            room.confirmed.clear()

    def confirm_score(self, account: Account) -> Room:
        with self.lock:
            room = self._room_for(account)
            self._seat_for(room, account)
            if room.status != "scoring" or room.score is None:
                raise OnlineError("当前没有待确认的数子结果")
            room.confirmed.add(account.uid)
            if len(room.confirmed) >= len(room.seats):
                room.status = "finished"
                room.result = room.score.result
        self._broadcast(room)
        return room

    def resume_game(self, account: Account) -> Room:
        with self.lock:
            room = self._room_for(account)
            self._seat_for(room, account)
            if room.status != "scoring":
                raise OnlineError("当前对局不在数子确认阶段")
            moves = list(room.board.move_history)
            if len(moves) >= 2 and moves[-1].is_pass and moves[-2].is_pass:
                moves = moves[:-2]
            board = GoBoard(room.size, "SIMPLE")
            for move in moves:
                try:
                    board.play(move.color, move.x, move.y)
                except IllegalMove as error:
                    raise OnlineError("无法恢复棋局：" + str(error)) from error
            room.board = board
            room.status = "playing"
            room.result = ""
            room.score = None
            room.score_summary = ""
            room.confirmed.clear()
        self._broadcast(room)
        return room

    def resign(self, account: Account) -> Room:
        with self.lock:
            room = self._room_for(account)
            seat = self._seat_for(room, account)
            if room.status not in {"playing", "scoring"}:
                raise OnlineError("当前对局已经结束")
            room.status = "finished"
            room.result = opposite(seat.color) + "+R"
            room.score_summary = "对方认输"
        self._broadcast(room)
        return room

    # ------------------------------------------------------------------ snapshots
    def _snapshot(self, room: Room, viewer_uid: str) -> dict[str, Any]:
        viewer = room.seats.get(viewer_uid)
        last = room.board.move_history[-1] if room.board.move_history else None
        return {
            "room": room.code,
            "size": room.size,
            "komi": room.komi,
            "status": room.status,
            "result": room.result,
            "score_summary": room.score_summary,
            "confirmed": sorted(room.confirmed),
            "to_play": room.board.to_play,
            "captures": dict(room.board.captures),
            "grid": [list(row) for row in room.board.grid],
            "moves": [
                {"color": move.color, "x": move.x, "y": move.y, "pass": move.is_pass}
                for move in room.board.move_history
            ],
            "move_count": len(room.board.move_history),
            "last_move": (
                {"color": last.color, "x": last.x, "y": last.y, "pass": last.is_pass}
                if last else None
            ),
            "players": [
                {
                    "uid": seat.account.uid,
                    "username": seat.account.username,
                    "color": seat.color,
                    "connected": seat.account.uid in self.connections,
                }
                for seat in sorted(room.seats.values(), key=lambda item: item.color)
            ],
            "viewer_uid": viewer_uid,
            "viewer_color": viewer.color if viewer else None,
            "warning": (
                "当前按盘面数子，未自动标记死子。如不认可，可点“继续下棋”把最后两次停一手撤回。"
                if room.status == "scoring" else ""
            ),
        }

    def _broadcast(self, room: Room) -> None:
        with self.lock:
            targets = []
            for uid in room.seats:
                connection = self.connections.get(uid)
                if connection is not None:
                    targets.append((connection, self._snapshot(room, uid)))
        for connection, snapshot in targets:
            connection.send({"type": "event", "event": "state", "state": snapshot})

    # --------------------------------------------------------------- request layer
    def handle_request(self, request: dict[str, Any], connection: Any) -> dict[str, Any]:
        request_id = request.get("request_id")
        kind = request.get("type")
        try:
            if kind == "register":
                account, token = self.register(request.get("username", ""),
                                               request.get("password", ""))
                if getattr(connection, "uid", None) and connection.uid != account.uid:
                    self.detach(connection.uid, connection)
                connection.uid = account.uid
                self.attach(account, connection)
                return {"ok": True, "request_id": request_id, "token": token,
                        "account": {"uid": account.uid, "username": account.username},
                        "room": self.state(account)}
            if kind == "login":
                account, token = self.login(
                    request.get("username", ""), request.get("password", ""),
                    getattr(connection, "client_address", ("", 0))[0])
                if getattr(connection, "uid", None) and connection.uid != account.uid:
                    self.detach(connection.uid, connection)
                connection.uid = account.uid
                self.attach(account, connection)
                return {"ok": True, "request_id": request_id, "token": token,
                        "account": {"uid": account.uid, "username": account.username},
                        "room": self.state(account)}
            if kind == "ping":
                return {"ok": True, "request_id": request_id}
            account = self.account_for_token(request.get("token", ""))
            if getattr(connection, "uid", None) != account.uid:
                if getattr(connection, "uid", None):
                    self.detach(connection.uid, connection)
                connection.uid = account.uid
                self.attach(account, connection)
            if kind == "state":
                return {"ok": True, "request_id": request_id, "room": self.state(account)}
            if kind == "create_room":
                room = self.create_room(account, request.get("size", 19),
                                        request.get("komi", 7.5), request.get("color", "B"))
                return {"ok": True, "request_id": request_id,
                        "room": self._snapshot(room, account.uid)}
            if kind == "join_room":
                room = self.join_room(account, request.get("room", ""))
                return {"ok": True, "request_id": request_id,
                        "room": self._snapshot(room, account.uid)}
            if kind == "leave_room":
                self.leave_room(account)
                return {"ok": True, "request_id": request_id, "room": None}
            if kind == "move":
                room = self.move(account, request.get("x"), request.get("y"))
                return {"ok": True, "request_id": request_id,
                        "room": self._snapshot(room, account.uid)}
            if kind == "pass":
                room = self.move(account, None, None)
                return {"ok": True, "request_id": request_id,
                        "room": self._snapshot(room, account.uid)}
            if kind == "resign":
                room = self.resign(account)
                return {"ok": True, "request_id": request_id,
                        "room": self._snapshot(room, account.uid)}
            if kind == "confirm_score":
                room = self.confirm_score(account)
                return {"ok": True, "request_id": request_id,
                        "room": self._snapshot(room, account.uid)}
            if kind == "resume_game":
                room = self.resume_game(account)
                return {"ok": True, "request_id": request_id,
                        "room": self._snapshot(room, account.uid)}
            raise OnlineError("未知请求类型")
        except OnlineError as error:
            return {"ok": False, "request_id": request_id, "error": str(error)}
        except Exception:
            traceback.print_exc()
            return {"ok": False, "request_id": request_id, "error": "服务器内部错误"}


def _decode_request(line: bytes) -> dict[str, Any] | None:
    """Parse one JSON line into a request dict, or None if it is malformed.

    Deeply nested JSON makes the C decoder raise RecursionError; that must be
    treated as malformed input rather than crashing the handler thread.
    """
    try:
        request = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    return request if isinstance(request, dict) else None


class _Handler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.send_lock = threading.Lock()
        self.uid: str | None = None
        self.server: OnlineServer

    def send(self, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self.send_lock:
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except (OSError, ValueError):
                pass

    def handle(self) -> None:
        while True:
            try:
                line = self.rfile.readline(MAX_LINE + 1)
            except OSError:
                break
            if not line:
                break
            if len(line) > MAX_LINE:
                self.send({"ok": False, "error": "请求过大"})
                break
            request = _decode_request(line)
            if request is None:
                self.send({"ok": False, "error": "请求格式错误"})
                continue
            response = self.server.service.handle_request(request, self)
            self.send(response)

    def finish(self) -> None:
        if self.uid:
            self.server.service.detach(self.uid, self)
        try:
            super().finish()
        except OSError:
            pass


class OnlineServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: OnlineService) -> None:
        self.service = service
        super().__init__(address, _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])


def local_addresses(port: int) -> list[str]:
    addresses: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        primary = probe.getsockname()[0]
        probe.close()
        addresses.append(primary)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET, socket.SOCK_STREAM):
            address = info[4][0]
            if address not in addresses and not address.startswith("127."):
                addresses.append(address)
    except OSError:
        pass
    return [f"{item}:{port}" for item in addresses]


def main() -> None:
    parser = argparse.ArgumentParser(description="影棋联机服务器")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    db = args.db or default_server_db()
    service = OnlineService(db)
    try:
        server = OnlineServer((args.host, args.port), service)
    except OSError as error:
        print(f"无法监听 {args.host}:{args.port}：{error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"影棋联机服务器已启动，账号数据库：{db}", flush=True)
    print(f"监听端口：{server.port}", flush=True)
    for address in local_addresses(server.port):
        print(f"局域网地址：{address}", flush=True)
    print("这是明文 TCP 服务。公网使用请放在 VPN、SSH 隧道或 TLS 终止之后。", flush=True)
    print("按 Ctrl+C 停止。", flush=True)
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
