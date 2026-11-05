from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yehu_style_go.models import GameRecord
from yehu_style_go.sgf import parse_sgf, parse_sgf_file


def default_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return base / "YehuStyleGo"


def _record_order(source: str) -> int:
    path = Path(source)
    match = re.search(r"(\d{15,24})\.sgf$", path.name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    if path.name.lower() == "record.sgf" and re.fullmatch(r"\d{15,24}", path.parent.name):
        return int(path.parent.name)
    return 0


class Library:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_data_dir()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "library.sqlite3"
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY,
                    sha256 TEXT UNIQUE NOT NULL,
                    source_path TEXT NOT NULL,
                    sgf TEXT NOT NULL,
                    played_at TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis (
                    game_id INTEGER NOT NULL REFERENCES games(id),
                    engine_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (game_id, engine_key)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def import_paths(self, paths: list[Path]) -> dict[str, Any]:
        added = 0
        duplicates = 0
        errors: list[str] = []
        for path in paths:
            try:
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("棋谱超过 8 MB 限制")
                game = parse_sgf_file(path)
                if game.board_size not in {9, 13, 19}:
                    raise ValueError("仅支持 9、13、19 路棋盘")
                with closing(self._connect()) as connection, connection:
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO games "
                        "(sha256, source_path, sgf, played_at, imported_at) VALUES (?,?,?,?,?)",
                        (game.sha256, str(game.source_path), game.raw_sgf, game.played_at,
                         datetime.now(timezone.utc).isoformat()),
                    )
                    if cursor.rowcount:
                        added += 1
                    else:
                        duplicates += 1
            except (OSError, ValueError, RecursionError) as error:
                errors.append(f"{path.name}: {error}")
        return {"added": added, "duplicates": duplicates, "errors": errors}

    def games(self, player: str | None = None, limit: int | None = 100) -> list[GameRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM games").fetchall()
        # Fox dates have day precision; record IDs preserve order within the day.
        rows.sort(key=lambda row: (row["played_at"], _record_order(row["source_path"]),
                                   row["imported_at"], row["id"]), reverse=True)
        games = []
        for row in rows:
            game = self._game_from_row(row)
            if player and game.color_for(player) is None:
                continue
            games.append(game)
            if limit is not None and len(games) >= limit:
                break
        return games

    def game(self, game_id: int) -> GameRecord:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
        if row is None:
            raise ValueError("棋谱不存在")
        return self._game_from_row(row)

    def game_by_sha(self, sha256: str) -> GameRecord | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM games WHERE sha256=?", (sha256,)).fetchone()
        return self._game_from_row(row) if row else None

    def game_count(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM games").fetchone()[0])

    def delete_games(self, game_ids: list[int]) -> int:
        """Delete selected games together with their cached engine analyses."""
        ids = sorted({int(game_id) for game_id in game_ids})
        deleted = 0
        with closing(self._connect()) as connection, connection:
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                connection.execute(
                    f"DELETE FROM analysis WHERE game_id IN ({placeholders})", batch)
                cursor = connection.execute(
                    f"DELETE FROM games WHERE id IN ({placeholders})", batch)
                deleted += cursor.rowcount
        return deleted

    def clear_games(self) -> dict[str, int]:
        """Remove every game and every cached analysis, leaving settings untouched."""
        with closing(self._connect()) as connection:
            with connection:
                analyses = connection.execute("DELETE FROM analysis").rowcount
                games = connection.execute("DELETE FROM games").rowcount
            # Reclaim the large analysis payload pages instead of leaving deleted data
            # in free pages after a handover cleanup. A locked/read-only database must
            # not turn an already-committed clear into an apparent failure.
            try:
                connection.execute("VACUUM")
            except sqlite3.Error:
                pass
        return {"games": games, "analyses": analyses}

    @staticmethod
    def _game_from_row(row: sqlite3.Row) -> GameRecord:
        game = parse_sgf(row["sgf"], row["source_path"])
        game.sha256 = row["sha256"]
        game.database_id = row["id"]
        return game

    def save_analysis(self, game_id: int, key: str, payload: dict[str, Any]) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO analysis (game_id, engine_key, payload) VALUES (?,?,?)",
                (game_id, key, json.dumps(payload, ensure_ascii=False)),
            )

    def analysis(self, game_id: int, key: str | None = None) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            if key is None:
                row = connection.execute(
                    "SELECT payload FROM analysis WHERE game_id=? ORDER BY rowid DESC LIMIT 1",
                    (game_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT payload FROM analysis WHERE game_id=? AND engine_key=?", (game_id, key)
                ).fetchone()
        return json.loads(row[0]) if row else None

    def analyzed_game_ids(self, engine_key: str) -> set[int]:
        """Return cached game ids in one query for list rendering."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT game_id FROM analysis WHERE engine_key=?", (engine_key,)
            ).fetchall()
        return {int(row[0]) for row in rows}

    def setting(self, name: str, default: Any = None) -> Any:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT payload FROM settings WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, name: str, value: Any) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings (name, payload) VALUES (?,?)",
                (name, json.dumps(value, ensure_ascii=False)),
            )


def scan_folder(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise ValueError("棋谱文件夹不存在")
    return sorted(
        (path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() == ".sgf"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
