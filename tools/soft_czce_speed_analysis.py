#!/usr/bin/env python3
"""Generate a simulated CZCE parser CSV for the OpenSLT parser node."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from typing import List, Optional, Sequence, Tuple


OUTPUT_FILENAME = "rem_client_new_to_market_speed_20260521.csv"
ROW_COUNT = 100
HEADER = (
    "logger_time",
    "account_id",
    "msg1",
    "msg1_sec",
    "msg1_ns",
    "order_ref_number",
    "msg2",
    "msg2_sec",
    "msg2_ns",
    "order_internal_id",
    "diff",
    "package1",
    "package2",
    "userid",
    "arbi_symbol",
)


def _split_nanoseconds(value: int) -> Tuple[int, int]:
    return divmod(value, 1_000_000_000)


def _row(index: int) -> List[object]:
    sequence = index + 1
    seed = hashlib.sha256(str(sequence).encode("ascii")).digest()
    jitter = 0 if index == 0 else int.from_bytes(seed[:3], "big") % 500_000
    latency = 5_000 + int.from_bytes(seed[3:6], "big") % 5_000
    msg2_total = 1_779_355_199_911_518_858 + index * 50_000_000 + jitter
    msg1_total = msg2_total + latency
    msg1_sec, msg1_ns = _split_nanoseconds(msg1_total)
    msg2_sec, msg2_ns = _split_nanoseconds(msg2_total)
    package1 = 14_679 + index * 97
    package2 = package1 - 1
    return [
        "[2026-05-21 17:25:45.858]",
        100001,
        "rem_new_to_mkt",
        msg1_sec,
        msg1_ns,
        sequence,
        "clt_new_to_rem",
        msg2_sec,
        msg2_ns,
        sequence,
        latency,
        package1,
        package2,
        "01210001",
        "0121     34339355CF001",
    ]


def generate_csv(directory: Optional[Path] = None) -> Path:
    workdir = (directory or Path.cwd()).resolve()
    target = workdir / OUTPUT_FILENAME
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=".%s." % OUTPUT_FILENAME,
            suffix=".tmp",
            dir=str(workdir),
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(HEADER)
            for index in range(ROW_COUNT):
                writer.writerow(_row(index))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary_path), str(target))
        temporary_path = None
        return target
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soft_czce_speed_analysis",
        description="Generate one simulated rem_client_new_to_market_speed CSV.",
    )
    parser.add_argument(
        "analysis_config",
        nargs="?",
        help="accepted for compatibility with the real parser and intentionally ignored",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    build_parser().parse_args(argv)
    try:
        target = generate_csv()
    except Exception as exc:
        print("soft_czce_speed_analysis: %s" % exc, file=sys.stderr, flush=True)
        return 1
    print("[SIMULATION ONLY] generated %s with %d rows" % (target.name, ROW_COUNT), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
