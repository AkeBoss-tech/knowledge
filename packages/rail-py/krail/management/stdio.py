"""Newline-delimited JSON-over-stdio transport for management v1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from krail.management.v1 import CONTRACT, PROTOCOL_VERSION, ManagementService


MAX_REQUEST_BYTES = 1024 * 1024


def serve(workspace: str | Path, input_stream=None, output_stream=None) -> int:
    source = input_stream or sys.stdin
    sink = output_stream or sys.stdout
    service = ManagementService(workspace)
    for line in source:
        if len(line.encode("utf-8")) > MAX_REQUEST_BYTES:
            response = {
                "contract": CONTRACT,
                "protocol_version": PROTOCOL_VERSION,
                "request_id": None,
                "operation": None,
                "ok": False,
                "error": {"code": "REQUEST_TOO_LARGE", "message": "request exceeds the 1 MiB transport limit", "details": {}},
            }
        else:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                response = {
                    "contract": CONTRACT,
                    "protocol_version": PROTOCOL_VERSION,
                    "request_id": None,
                    "operation": None,
                    "ok": False,
                    "error": {"code": "INVALID_JSON", "message": "request is not valid JSON", "details": {"line": exc.lineno, "column": exc.colno}},
                }
            else:
                response = service.handle(payload)
        sink.write(json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n")
        sink.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KRAIL management-v1 JSON-over-stdio server")
    parser.add_argument("--workspace", required=True, help="single workspace controlled by this isolated process")
    args = parser.parse_args(argv)
    return serve(args.workspace)


if __name__ == "__main__":
    raise SystemExit(main())
