"""Fail closed unless the bootstrap's always-running validation succeeded."""

import json
import os
import sys


def main():
    try:
        needs = json.loads(os.environ["NEEDS_JSON"])
    except (KeyError, ValueError):
        print("ci-ok: missing or malformed job evidence", file=sys.stderr)
        return 1
    if not isinstance(needs, dict) or set(needs) != {"lint"}:
        print("ci-ok: expected scaffold validation evidence", file=sys.stderr)
        return 1
    if not isinstance(needs["lint"], dict) or needs["lint"].get("result") != "success":
        print("ci-ok: scaffold validation did not succeed", file=sys.stderr)
        return 1
    print("ci-ok: scaffold validation succeeded; no candidate implementation tested")
    return 0


if __name__ == "__main__":
    sys.exit(main())
