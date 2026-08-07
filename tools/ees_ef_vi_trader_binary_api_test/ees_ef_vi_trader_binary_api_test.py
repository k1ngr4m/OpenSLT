#!/usr/bin/env python3
"""OpenSLT-compatible, network-free simulator for the EF-VI order tool."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import signal
import sys
from typing import Dict, Iterable, Optional, Sequence
import xml.etree.ElementTree as ElementTree


BINARY_NAME = "ees_ef_vi_trader_binary_api_test"
VERSION = "0.1.0"
MAX_XML_BYTES = 1024 * 1024
FORBIDDEN_XML = re.compile(r"<!\s*(DOCTYPE|ENTITY)\b", re.IGNORECASE)
XML_DECLARATION = re.compile(r"^\ufeff?\s*<\?xml[^?]*\?>", re.IGNORECASE)
XML_ENCODING = re.compile(r"\bencoding\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
PROMPT = "ees-sim> "

ORDER_ACTIONS = (
    "new_order",
    "new_order_simple",
    "new_quote",
    "new_quote_simple",
    "new_arbi_order",
    "new_arbi_order_simple",
    "cxl_order",
    "cxl_quote",
    "stop_order",
)

ACTION_ALIASES: Dict[str, str] = {
    "neworder": "new_order",
    "new_ordersimple": "new_order_simple",
    "newordersimple": "new_order_simple",
    "newquote": "new_quote",
    "newquote_simple": "new_quote_simple",
    "newquotesimple": "new_quote_simple",
    "newarbiorder": "new_arbi_order",
    "new_arbiordersimple": "new_arbi_order_simple",
    "newarbiordersimple": "new_arbi_order_simple",
    "cxlorder": "cxl_order",
    "cxlquote": "cxl_quote",
    "stoporder": "stop_order",
}


class ConfigError(Exception):
    """A configuration error which maps to CLI exit code 2."""


class GracefulExit(Exception):
    """Raised by SIGTERM so the interactive loop can exit cleanly."""


@dataclass(frozen=True)
class ConfigSummary:
    filename: str
    sha256: str
    root: str
    elements: int
    value_attributes: int
    action_groups: int
    read_symbol_csv: Optional[int]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _declared_encoding(document: str) -> Optional[str]:
    declaration = XML_DECLARATION.match(document)
    if not declaration:
        return None
    encoding = XML_ENCODING.search(declaration.group(0))
    return encoding.group(1) if encoding else None


def load_config(path: Path) -> ConfigSummary:
    if not path.is_file():
        raise ConfigError("configuration file does not exist or is not a regular file")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError("configuration file cannot be read: %s" % exc) from exc
    if len(raw) > MAX_XML_BYTES:
        raise ConfigError("configuration file exceeds 1 MiB")
    try:
        document = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfigError("configuration file must use UTF-8 encoding") from exc
    declared_encoding = _declared_encoding(document)
    if declared_encoding and declared_encoding.lower().replace("-", "") != "utf8":
        raise ConfigError("XML declaration must specify UTF-8 encoding")
    if FORBIDDEN_XML.search(document):
        raise ConfigError("DOCTYPE and ENTITY declarations are not allowed")
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise ConfigError("invalid XML: %s" % exc) from exc

    elements = list(root.iter())
    value_attributes = sum(1 for element in elements if "value" in element.attrib)
    action_groups = sum(
        1
        for element in elements
        if _local_name(element.tag).casefold().startswith("group_")
        and any(
            token in _local_name(element.tag).casefold()
            for token in ("order", "quote", "arbi")
        )
    )
    read_symbol_csv: Optional[int] = None
    matching_values = [
        element.attrib.get("value", "")
        for element in elements
        if _local_name(element.tag).casefold() == "read_symbol_csv"
    ]
    if len(matching_values) == 1 and matching_values[0] in {"0", "1"}:
        read_symbol_csv = int(matching_values[0])

    return ConfigSummary(
        filename=path.name,
        sha256=hashlib.sha256(raw).hexdigest(),
        root=_local_name(root.tag),
        elements=len(elements),
        value_attributes=value_attributes,
        action_groups=action_groups,
        read_symbol_csv=read_symbol_csv,
    )


def canonical_action(command: str) -> Optional[str]:
    normalized = command.strip().casefold()
    if normalized in ORDER_ACTIONS:
        return normalized
    return ACTION_ALIASES.get(normalized)


def _json_line(prefix: str, payload: Dict[str, object]) -> None:
    print(prefix + " " + json.dumps(payload, ensure_ascii=True, sort_keys=True), flush=True)


def print_help() -> None:
    print("Available simulated actions:", flush=True)
    for action in ORDER_ACTIONS:
        print("  " + action, flush=True)
    print("Control commands: help, version, quit, exit", flush=True)


def _signal_exit(_signum: int, _frame: object) -> None:
    raise GracefulExit()


def run_interactive(summary: ConfigSummary, input_lines: Optional[Iterable[str]] = None) -> int:
    print("=== EES EF-VI TRADER SIMULATOR %s ===" % VERSION, flush=True)
    print(
        "[SIMULATION ONLY] No REM login, network traffic, or real orders are produced.",
        flush=True,
    )
    _json_line("SIM_CONFIG", asdict(summary))
    print("Type 'help' to list commands.", flush=True)

    source = iter(input_lines) if input_lines is not None else None
    event_sequence = 0
    while True:
        try:
            if source is None:
                print(PROMPT, end="", flush=True)
                line = sys.stdin.readline()
            else:
                line = next(source, "")
        except (KeyboardInterrupt, GracefulExit):
            print("\n[SIMULATION ONLY] simulator stopped.", flush=True)
            return 0
        if line == "":
            print("\n[SIMULATION ONLY] input closed; simulator stopped.", flush=True)
            return 0

        command = line.strip().casefold()
        if not command:
            continue
        if command in {"quit", "exit"}:
            print("[SIMULATION ONLY] simulator stopped.", flush=True)
            return 0
        if command == "help":
            print_help()
            continue
        if command == "version":
            print("%s simulator %s" % (BINARY_NAME, VERSION), flush=True)
            continue

        action = canonical_action(command)
        if action is None:
            _json_line(
                "SIM_ERROR",
                {"command": command, "error": "unknown_command", "simulation": True},
            )
            continue

        event_sequence += 1
        _json_line(
            "SIM_EVENT",
            {
                "action": action,
                "event": "action",
                "event_id": "SIM-%06d" % event_sequence,
                "simulation": True,
                "status": "simulated",
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=BINARY_NAME,
        description="Network-free OpenSLT order-tool simulator (never sends real orders).",
    )
    parser.add_argument("config", nargs="?", help="UTF-8 XML configuration file")
    parser.add_argument("--version", action="version", version="%(prog)s simulator " + VERSION)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if not arguments.config:
        parser.error("the following arguments are required: config")

    try:
        summary = load_config(Path(arguments.config))
    except ConfigError as exc:
        print("%s: configuration error: %s" % (BINARY_NAME, exc), file=sys.stderr, flush=True)
        return 2
    except Exception as exc:
        print("%s: unexpected error: %s" % (BINARY_NAME, exc), file=sys.stderr, flush=True)
        return 1

    previous_sigterm = signal.signal(signal.SIGTERM, _signal_exit)
    try:
        return run_interactive(summary)
    except GracefulExit:
        print("\n[SIMULATION ONLY] simulator stopped.", flush=True)
        return 0
    except Exception as exc:
        print("%s: unexpected error: %s" % (BINARY_NAME, exc), file=sys.stderr, flush=True)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    sys.exit(main())
