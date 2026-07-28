from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile

import pytest


TOOL_DIR = Path(__file__).resolve().parents[1]
SOURCE = TOOL_DIR / "ees_ef_vi_trader_binary_api_test.py"
SAMPLE_CONFIG = TOOL_DIR / "ees_ef_vi_trader_api_test_conf.xml"

SPEC = importlib.util.spec_from_file_location("ees_simulator", SOURCE)
assert SPEC and SPEC.loader
simulator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = simulator
SPEC.loader.exec_module(simulator)


def run_cli(*arguments: str, input_text: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SOURCE), *arguments],
        input=input_text,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_load_config_returns_only_safe_summary() -> None:
    summary = simulator.load_config(SAMPLE_CONFIG)
    serialized = str(summary)
    assert summary.root == "tcp"
    assert summary.elements > 10
    assert summary.value_attributes > 10
    assert summary.action_groups == 1
    assert summary.read_symbol_csv == 0
    assert "CHANGE_ME" not in serialized
    assert "SIM100001" not in serialized
    assert len(summary.sha256) == 64


@pytest.mark.parametrize(
    "content,error",
    [
        (b"<tcp><broken></tcp>", "invalid XML"),
        (b'<?xml version="1.0" encoding="gbk"?><tcp/>', "UTF-8"),
        (b'<!DOCTYPE tcp [<!ENTITY x "secret">]><tcp>&x;</tcp>', "DOCTYPE"),
        (b"<tcp>\xff</tcp>", "UTF-8"),
    ],
)
def test_load_config_rejects_invalid_or_unsafe_xml(content: bytes, error: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.xml"
        path.write_bytes(content)
        with pytest.raises(simulator.ConfigError, match=error):
            simulator.load_config(path)


def test_load_config_rejects_missing_and_oversized_files() -> None:
    with pytest.raises(simulator.ConfigError, match="does not exist"):
        simulator.load_config(Path("definitely-missing.xml"))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "large.xml"
        path.write_bytes(b"<tcp>" + b"x" * simulator.MAX_XML_BYTES + b"</tcp>")
        with pytest.raises(simulator.ConfigError, match="exceeds 1 MiB"):
            simulator.load_config(path)


def test_cli_usage_help_version_and_config_error_exit_codes() -> None:
    missing_argument = run_cli()
    assert missing_argument.returncode == 2
    assert "required: config" in missing_argument.stderr

    help_result = run_cli("--help")
    assert help_result.returncode == 0
    assert "never sends real orders" in help_result.stdout

    version_result = run_cli("--version")
    assert version_result.returncode == 0
    assert "simulator 0.1.0" in version_result.stdout

    missing_file = run_cli("missing.xml")
    assert missing_file.returncode == 2
    assert "configuration error" in missing_file.stderr


def test_unexpected_runtime_error_returns_exit_code_one(monkeypatch, capsys) -> None:
    def fail(_path: Path):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(simulator, "load_config", fail)
    assert simulator.main([str(SAMPLE_CONFIG)]) == 1
    assert "unexpected error: synthetic failure" in capsys.readouterr().err


def test_all_actions_and_legacy_aliases_are_simulated() -> None:
    commands = [
        *simulator.ORDER_ACTIONS,
        "new_ordersimple",
        "newquote",
        "newquote_simple",
        "newarbiorder",
        "cxlorder",
        "exit",
    ]
    result = run_cli(str(SAMPLE_CONFIG), input_text="\n".join(commands) + "\n")
    assert result.returncode == 0
    assert result.stdout.count("SIM_EVENT") == len(commands) - 1
    for action in simulator.ORDER_ACTIONS:
        assert '"action": "%s"' % action in result.stdout
    assert "SIM-000001" in result.stdout
    assert "SIM-000013" in result.stdout
    assert "status\": \"simulated" in result.stdout


def test_unknown_command_does_not_end_session_or_leak_xml_values() -> None:
    result = run_cli(
        str(SAMPLE_CONFIG),
        input_text="not-a-command\nnew_order\nexit\n",
    )
    assert result.returncode == 0
    assert "SIM_ERROR" in result.stdout
    assert "SIM_EVENT" in result.stdout
    assert "CHANGE_ME" not in result.stdout
    assert "SIM100001" not in result.stdout
    assert "192.0.2.10" not in result.stdout


def test_openslt_style_interaction_flushes_output_and_handles_sigint() -> None:
    process = subprocess.Popen(
        [sys.executable, str(SOURCE), str(SAMPLE_CONFIG)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdin and process.stdout
    for _ in range(4):
        process.stdout.readline()
    process.stdin.write("new_order\n")
    process.stdin.flush()
    readable, _, _ = select.select([process.stdout], [], [], 3)
    assert readable, "simulated action output was not flushed"
    action_line = process.stdout.readline()
    assert "SIM_EVENT" in action_line
    assert '"action": "new_order"' in action_line
    process.send_signal(signal.SIGINT)
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0
    assert "simulator stopped" in stdout
    assert stderr == ""


def test_sigterm_exits_cleanly() -> None:
    process = subprocess.Popen(
        [sys.executable, str(SOURCE), str(SAMPLE_CONFIG)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout
    for _ in range(4):
        process.stdout.readline()
    process.terminate()
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0
    assert "simulator stopped" in stdout
    assert stderr == ""


def test_source_has_no_network_or_process_imports() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    forbidden = {
        "asyncio",
        "ftplib",
        "http",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    assert imported_roots.isdisjoint(forbidden)
