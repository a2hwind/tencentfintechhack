#!/usr/bin/env python3
"""The tamper-evidence demo beat, in three commands.

    python -m internal_brain.audit.verify --db data/brain.db        # OK
    python scripts/tamper_demo.py --db data/brain.db edit           # edits the newest answer in place
    python -m internal_brain.audit.verify --db data/brain.db        # chain broken at seq N
    python scripts/tamper_demo.py --db data/brain.db delete         # deletes a row
    python -m internal_brain.audit.verify --db data/brain.db        # gap after seq N
    python scripts/tamper_demo.py --db data/brain.db restore        # puts the backup back

`edit` and `delete` first copy the database to <db>.pre-tamper.bak (once); `restore` copies it back.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["edit", "delete", "restore"])
    parser.add_argument("--db", default="data/brain.db")
    parser.add_argument("--seq", type=int, default=None, help="row to tamper with (default: the newest query entry)")
    args = parser.parse_args()
    db = Path(args.db)
    backup = db.with_suffix(db.suffix + ".pre-tamper.bak")

    if args.action == "restore":
        if not backup.exists():
            print("no backup to restore", file=sys.stderr)
            return 1
        shutil.copyfile(backup, db)
        for sidecar in (db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
            if sidecar.exists():
                sidecar.unlink()
        print(f"restored {db} from {backup}")
        return 0

    if not db.exists():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    if not backup.exists():
        shutil.copyfile(db, backup)
        print(f"backup written to {backup}")

    conn.row_factory = sqlite3.Row
    if args.seq is None:
        row = conn.execute("SELECT seq FROM audit_entries WHERE kind = 'query' ORDER BY seq DESC LIMIT 1").fetchone()
        if row is None:
            print("no query entries yet: ask a question first", file=sys.stderr)
            return 1
        seq = row["seq"]
    else:
        seq = args.seq

    if args.action == "edit":
        row = conn.execute("SELECT entry_json FROM audit_entries WHERE seq = ?", (seq,)).fetchone()
        entry = json.loads(row["entry_json"])
        entry["answer"] = "This answer was edited directly in the database."
        conn.execute("UPDATE audit_entries SET entry_json = ? WHERE seq = ?", (json.dumps(entry, sort_keys=True, separators=(",", ":")), seq))
        conn.commit()
        print(f"edited the answer stored at seq {seq}; run verify to see the chain break there")
    else:
        conn.execute("DELETE FROM audit_entries WHERE seq = ?", (seq,))
        conn.commit()
        print(f"deleted seq {seq}; run verify to see the gap after seq {seq - 1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
