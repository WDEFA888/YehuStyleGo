"""Run the guarded native-client exporter and import only verified SGFs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yehu_style_go.fox_sync import FoxExporter
from yehu_style_go.storage import Library


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--destination", type=Path,
                        default=Path.home() / "Documents" / "YehuStyleGo" / "FoxRecords")
    args = parser.parse_args()
    library = Library()
    player = library.setting("player", "")
    if not player:
        raise SystemExit("请先在影棋设置中填写野狐用户名")
    exporter = FoxExporter(player, args.destination)
    def progress(done, total):
        print(f"Verified local exports: {done}/{total}", flush=True)
    result = exporter.sync(args.limit, progress)
    imported = library.import_paths(result.paths)
    if imported["errors"]:
        raise RuntimeError(str(imported["errors"]))
    library.set_setting("fox_export_folder", str(args.destination.resolve()))
    print(json.dumps({"exported": result.exported, "already_saved": result.existing,
                      "available_rows": result.available_rows, "import": imported,
                      "owner_games": len(library.games(player, args.limit))}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
