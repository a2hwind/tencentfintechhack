"""`brain-verify`: walk the audit chain from genesis and report the first broken link.

    python -m internal_brain.audit.verify --db data/brain.db --pubkey data/audit_public.key

Exit code 0 when the chain and every checkpoint signature verify, 1 otherwise. The
demo moment: verify says OK; edit one answer directly in the database; verify says
"chain broken at seq N"; delete a row; verify says "gap after seq N".
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .log import load_public_key, verify_chain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brain-verify", description="Verify the Internal Brain audit chain.")
    parser.add_argument("--db", default="data/brain.db", help="path to the SQLite database")
    parser.add_argument("--pubkey", default=None, help="path to audit_public.key (default: next to the database)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"no database at {db_path}", file=sys.stderr)
        return 2
    pub_path = Path(args.pubkey) if args.pubkey else db_path.parent / "audit_public.key"
    public_key = load_public_key(pub_path) if pub_path.exists() else None

    conn = sqlite3.connect(str(db_path))
    report = verify_chain(conn, public_key)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        status = "OK" if report.ok else "FAILED"
        print(f"audit chain: {status}  entries={report.entries}  checkpoints={report.checkpoints_verified}/{report.checkpoints} verified")
        if report.head_hash:
            print(f"head: seq {report.entries} {report.head_hash[:16]}...")
        if public_key is None:
            print("(no public key found: checkpoint signatures were not checked)")
        for problem in report.problems:
            print(f"  - {problem}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
