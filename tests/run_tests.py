"""Run every test with network denied and real Cloudflare credentials removed."""
import sys
sys.dont_write_bytecode = True

import argparse
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report")
    args = ap.parse_args()
    os.environ.pop("CF_API_TOKEN", None)
    os.environ.pop("CF_ACCOUNT_ID", None)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network_disabled")), \
         patch.object(socket.socket, "connect_ex", side_effect=AssertionError("network_disabled")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network_disabled")):
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"tests_run": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skipped": len(result.skipped),
              "network": "denied", "cloudflare_credentials": "synthetic_only",
              "original_muse_suite": "unrecovered_and_unverified"}
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
