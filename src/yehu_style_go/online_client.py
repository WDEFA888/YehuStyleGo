"""Threaded client for the self-hosted online Go protocol."""
from __future__ import annotations

import json
import queue
import socket
import threading
import uuid
from typing import Any, Callable


class OnlineClientError(RuntimeError):
    pass


# Hard cap on a single JSON message. Guards the reader against a hostile or
# broken server that never sends a newline: the buffer cannot grow without bound.
MAX_MESSAGE = 64 * 1024


def parse_address(address: str) -> tuple[str, int]:
    text = address.strip()
    if not text:
        raise OnlineClientError("请填写服务器地址")
    if ":" in text:
        host, port_text = text.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError as error:
            raise OnlineClientError("服务器端口无效") from error
    else:
        host, port = text, 8765
    if not host or not 1 <= port <= 65535:
        raise OnlineClientError("服务器地址或端口无效")
    return host, port


class OnlineClient:
    def __init__(self, address: str, on_event: Callable[[dict[str, Any]], None] | None = None,
                 timeout: float = 15.0) -> None:
        self.host, self.port = parse_address(address)
        self.on_event = on_event
        self.timeout = timeout
        self.pending: dict[str, queue.Queue] = {}
        self.pending_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.closed = threading.Event()
        try:
            self.socket = socket.create_connection((self.host, self.port), timeout=timeout)
            self.socket.settimeout(None)
        except OSError as error:
            raise OnlineClientError(f"无法连接 {self.host}:{self.port}：{error}") from error
        self.reader = threading.Thread(target=self._reader, daemon=True,
                                       name="online-client-reader")
        self.reader.start()

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def _reader(self) -> None:
        buffer = b""
        try:
            while not self.closed.is_set():
                chunk = self.socket.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_MESSAGE:
                    break
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        message = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(message, dict):
                        continue
                    request_id = message.get("request_id")
                    if isinstance(request_id, str) and request_id:
                        with self.pending_lock:
                            waiter = self.pending.get(request_id)
                        if waiter is not None:
                            # Non-blocking: a duplicate or late response must
                            # never stall the reader thread on a full queue.
                            try:
                                waiter.put_nowait(message)
                            except queue.Full:
                                pass
                            continue
                    if message.get("type") == "event":
                        if self.on_event is not None:
                            try:
                                self.on_event(message)
                            except Exception:
                                pass
        except OSError:
            pass
        finally:
            self.closed.set()
            with self.pending_lock:
                waiters = list(self.pending.values())
                self.pending.clear()
            for waiter in waiters:
                try:
                    waiter.put_nowait(None)
                except queue.Full:
                    pass
            if self.on_event is not None:
                try:
                    self.on_event({"type": "event", "event": "disconnected"})
                except Exception:
                    pass

    def request(self, kind: str, timeout: float | None = None, **fields: Any) -> dict[str, Any]:
        if self.closed.is_set():
            raise OnlineClientError("连接已断开")
        request_id = uuid.uuid4().hex
        waiter: queue.Queue = queue.Queue(maxsize=1)
        with self.pending_lock:
            self.pending[request_id] = waiter
        payload = {**fields, "type": kind, "request_id": request_id}
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self.send_lock:
                self.socket.sendall(data)
        except OSError as error:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise OnlineClientError("发送失败，连接可能已断开") from error
        try:
            response = waiter.get(timeout=timeout or self.timeout)
        except queue.Empty as error:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise OnlineClientError("服务器响应超时") from error
        finally:
            with self.pending_lock:
                self.pending.pop(request_id, None)
        if response is None:
            raise OnlineClientError("连接已断开")
        if not response.get("ok"):
            raise OnlineClientError(str(response.get("error") or "服务器拒绝了请求"))
        return response

    def close(self) -> None:
        if self.closed.is_set():
            return
        self.closed.set()
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.socket.close()
        except OSError:
            pass
