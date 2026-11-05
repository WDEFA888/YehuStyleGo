from __future__ import annotations

import hashlib
import re
from pathlib import Path

from yehu_style_go.models import GameRecord, Move


class SgfParseError(ValueError):
    pass


_CA_ENCODINGS = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb18030": "gb18030",
    "cp936": "gb18030",
    "big5": "big5",
    "big-5": "big5",
    "cp950": "big5",
}


def _declared_encoding(data: bytes) -> str | None:
    # CA[] is ASCII by definition, so scanning an ASCII-only projection of the
    # bytes is enough to locate the declared encoding token.
    ascii_projection = data.decode("ascii", errors="ignore")
    match = re.search(r"\bCA\[([^\]]*)\]", ascii_projection)
    if not match:
        return None
    return _CA_ENCODINGS.get(match.group(1).strip().lower())


def decode_sgf(data: bytes) -> str:
    # UTF-32 LE starts with the same two bytes as UTF-16 LE, so check it first.
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return data.decode("utf-32")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    declared = _declared_encoding(data)
    if declared:
        try:
            return data.decode(declared)
        except (UnicodeDecodeError, LookupError):
            pass
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_sgf_file(path: str | Path) -> GameRecord:
    source = Path(path).expanduser().resolve()
    data = source.read_bytes()
    text = decode_sgf(data)
    game = parse_sgf(text, source)
    game.sha256 = hashlib.sha256(data).hexdigest()
    return game


def parse_sgf(text: str, source_path: str | Path = "<memory>") -> GameRecord:
    parser = _SgfParser(text)
    nodes = parser.parse_collection()
    if not nodes:
        raise SgfParseError("棋谱中没有节点")

    root = nodes[0]
    if root.get("GM", ["1"])[0] != "1":
        raise SgfParseError("仅支持围棋（GM[1]）棋谱")
    size_value = root.get("SZ", ["19"])[0]
    if ":" in size_value and len(set(size_value.split(":"))) != 1:
        raise SgfParseError("暂不支持非正方形棋盘")
    size = _board_size(root)
    moves: list[Move] = []
    black_points: set[tuple[int, int]] = set()
    white_points: set[tuple[int, int]] = set()
    for node in nodes:
        has_setup = any(name in node for name in ("AB", "AW", "AE"))
        if has_setup and moves:
            raise SgfParseError("暂不支持在对局中途修改摆子；请导出纯实战主线")
        if not moves:
            removed = set(_parse_setup(node.get("AE", []), size))
            black_points.difference_update(removed)
            white_points.difference_update(removed)
            added_black = set(_parse_setup(node.get("AB", []), size))
            added_white = set(_parse_setup(node.get("AW", []), size))
            black_points.update(added_black)
            black_points.difference_update(added_white)
            white_points.update(added_white)
            white_points.difference_update(added_black)
            if "PL" in node:
                root["PL"] = node["PL"]
        for color in ("B", "W"):
            values = node.get(color)
            if values:
                x, y = sgf_coord_to_xy(values[0], size)
                moves.append(Move(color, x, y))
                break

    setup_black = sorted(black_points)
    setup_white = sorted(white_points)
    raw = text.strip()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return GameRecord(
        source_path=Path(source_path),
        raw_sgf=raw,
        properties=root,
        moves=moves,
        setup_black=setup_black,
        setup_white=setup_white,
        sha256=digest,
    )


def sgf_coord_to_xy(coord: str, board_size: int = 19) -> tuple[int | None, int | None]:
    if not coord or (coord.lower() == "tt" and board_size <= 19):
        return None, None
    if len(coord) != 2:
        raise SgfParseError(f"无效的 SGF 坐标：{coord!r}")
    x = ord(coord[0].lower()) - ord("a")
    y = ord(coord[1].lower()) - ord("a")
    if not (0 <= x < board_size and 0 <= y < board_size):
        raise SgfParseError(f"SGF 坐标超出棋盘：{coord!r}")
    return x, y


def xy_to_sgf_coord(x: int | None, y: int | None) -> str:
    if x is None or y is None:
        return ""
    return chr(ord("a") + x) + chr(ord("a") + y)


def _board_size(root: dict[str, list[str]]) -> int:
    try:
        return int(root.get("SZ", ["19"])[0].split(":", 1)[0])
    except (ValueError, IndexError):
        return 19


def _parse_setup(values: list[str], size: int) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for value in values:
        if ":" in value:
            start, end = value.split(":", 1)
            x1, y1 = sgf_coord_to_xy(start, size)
            x2, y2 = sgf_coord_to_xy(end, size)
            if None in (x1, y1, x2, y2):
                continue
            points.extend(
                (x, y)
                for x in range(min(x1, x2), max(x1, x2) + 1)
                for y in range(min(y1, y2), max(y1, y2) + 1)
            )
        else:
            x, y = sgf_coord_to_xy(value, size)
            if x is not None and y is not None:
                points.append((x, y))
    return points


class _SgfParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0

    def parse_collection(self) -> list[dict[str, list[str]]]:
        self._skip_space()
        if self._peek() != "(":
            raise SgfParseError("不是有效的 SGF：缺少起始括号")
        nodes = self._parse_game_tree()
        return nodes

    def _parse_game_tree(self) -> list[dict[str, list[str]]]:
        self._consume("(")
        sequence: list[dict[str, list[str]]] = []
        self._skip_space()
        while self._peek() == ";":
            sequence.append(self._parse_node())
            self._skip_space()

        children: list[list[dict[str, list[str]]]] = []
        while self._peek() == "(":
            children.append(self._parse_game_tree())
            self._skip_space()

        self._consume(")")
        if children:
            sequence.extend(children[0])
        return sequence

    def _parse_node(self) -> dict[str, list[str]]:
        self._consume(";")
        properties: dict[str, list[str]] = {}
        while True:
            self._skip_space()
            start = self.index
            while self._peek().isalpha() and self._peek().isupper():
                self.index += 1
            if self.index == start:
                break
            identifier = self.text[start : self.index]
            self._skip_space()
            values: list[str] = []
            while self._peek() == "[":
                values.append(self._parse_value())
                self._skip_space()
            if not values:
                raise SgfParseError(f"属性 {identifier} 缺少值")
            properties.setdefault(identifier, []).extend(values)
        return properties

    def _parse_value(self) -> str:
        self._consume("[")
        characters: list[str] = []
        while self.index < len(self.text):
            character = self.text[self.index]
            self.index += 1
            if character == "]":
                return "".join(characters)
            if character == "\\":
                if self.index >= len(self.text):
                    break
                escaped = self.text[self.index]
                self.index += 1
                if escaped == "\r":
                    if self._peek() == "\n":
                        self.index += 1
                    continue
                if escaped == "\n":
                    continue
                characters.append(escaped)
            else:
                characters.append(character)
        raise SgfParseError("SGF 属性值没有结束括号")

    def _skip_space(self) -> None:
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def _peek(self) -> str:
        if self.index >= len(self.text):
            return ""
        return self.text[self.index]

    def _consume(self, expected: str) -> None:
        self._skip_space()
        if self._peek() != expected:
            actual = self._peek() or "文件结尾"
            raise SgfParseError(f"预期 {expected!r}，实际为 {actual!r}")
        self.index += 1
