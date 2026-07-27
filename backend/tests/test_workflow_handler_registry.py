from __future__ import annotations

import pytest

from app.services.workflow_handlers import registry
from app.services.workflows import NODE_TYPES, WorkflowError


def test_registry_covers_every_supported_node_type() -> None:
    assert registry.node_types == frozenset(NODE_TYPES)


def test_registry_exposes_terminal_capabilities() -> None:
    assert registry.get("order_preparation").terminal_kind == "order"
    assert registry.get("slnic_start_capture").terminal_kind == "slnic"
    assert registry.get("parser_parse").terminal_kind is None


def test_registry_rejects_unsupported_node_type() -> None:
    with pytest.raises(WorkflowError) as exc_info:
        registry.get("unknown")
    assert exc_info.value.code == "WORKFLOW_NODE_UNSUPPORTED"
