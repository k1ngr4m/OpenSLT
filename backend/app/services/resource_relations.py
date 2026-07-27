from __future__ import annotations

import typing

from sqlalchemy.orm import Session

from app.models import (
    PlanResource,
    RunResource,
    ScenarioWorkflowNode,
    ScenarioResource,
    ScenarioWorkflowVersion,
    TestPlan,
    TestRun,
    TestScenario,
    WorkflowNodeContractFile,
    WorkflowVersionResource,
)


def _ordered_ids(
    links: typing.Iterable[typing.Any],
    value_name: str = "resource_id",
) -> typing.List[int]:
    return [
        getattr(link, value_name)
        for link in sorted(links, key=lambda item: item.position)
    ]


def plan_resource_ids(plan: TestPlan) -> typing.List[int]:
    normalized = _ordered_ids(plan.resource_links)
    return normalized if normalized else list(plan.default_resource_ids or [])


def scenario_resource_ids(scenario: TestScenario) -> typing.List[int]:
    normalized = _ordered_ids(scenario.resource_links)
    return normalized if normalized else list(scenario.default_resource_ids or [])


def workflow_resource_ids(version: ScenarioWorkflowVersion) -> typing.List[int]:
    normalized = _ordered_ids(version.resource_links)
    return normalized if normalized else list(version.resource_ids or [])


def run_resource_ids(run: TestRun) -> typing.List[int]:
    normalized = _ordered_ids(run.resource_links)
    return normalized if normalized else list(run.resource_ids or [])


def node_contract_file_ids(node: ScenarioWorkflowNode) -> typing.List[int]:
    normalized = _ordered_ids(node.contract_file_links, "contract_file_id")
    legacy = list((node.config or {}).get("contract_file_ids") or [])
    return normalized if normalized else legacy


def node_config_with_relations(node: ScenarioWorkflowNode) -> typing.Dict[str, typing.Any]:
    config = dict(node.config or {})
    if node.node_type == "order_preparation":
        config["contract_file_ids"] = node_contract_file_ids(node)
    return config


def sync_plan_resources(
    plan: TestPlan,
    resource_ids: typing.Iterable[int],
    db: typing.Optional[Session] = None,
) -> None:
    ordered = list(resource_ids)
    plan.default_resource_ids = ordered
    _replace_links(plan, "resource_links", PlanResource, ordered, db=db)


def sync_scenario_resources(
    scenario: TestScenario,
    resource_ids: typing.Iterable[int],
    db: typing.Optional[Session] = None,
) -> None:
    ordered = list(resource_ids)
    scenario.default_resource_ids = ordered
    _replace_links(scenario, "resource_links", ScenarioResource, ordered, db=db)


def sync_workflow_resources(
    version: ScenarioWorkflowVersion,
    resource_ids: typing.Iterable[int],
    db: typing.Optional[Session] = None,
) -> None:
    ordered = list(resource_ids)
    version.resource_ids = ordered
    _replace_links(version, "resource_links", WorkflowVersionResource, ordered, db=db)


def sync_run_resources(
    run: TestRun,
    resource_ids: typing.Iterable[int],
    db: typing.Optional[Session] = None,
) -> None:
    ordered = list(resource_ids)
    run.resource_ids = ordered
    _replace_links(run, "resource_links", RunResource, ordered, db=db)


def sync_node_contract_files(
    node: ScenarioWorkflowNode,
    contract_file_ids: typing.Iterable[int],
    db: typing.Optional[Session] = None,
) -> None:
    ordered = list(contract_file_ids) if node.node_type == "order_preparation" else []
    config = dict(node.config or {})
    if node.node_type == "order_preparation":
        config["contract_file_ids"] = ordered
    else:
        config.pop("contract_file_ids", None)
    node.config = config
    _replace_links(
        node,
        "contract_file_links",
        WorkflowNodeContractFile,
        ordered,
        value_name="contract_file_id",
        db=db,
    )


def _replace_links(
    owner: typing.Any,
    relationship_name: str,
    link_type: typing.Type[typing.Any],
    values: typing.List[int],
    value_name: str = "resource_id",
    db: typing.Optional[Session] = None,
) -> None:
    current = list(getattr(owner, relationship_name))
    if _ordered_ids(current, value_name) == values:
        for position, link in enumerate(current, 1):
            link.position = position
        return
    if current and db is not None:
        setattr(owner, relationship_name, [])
        db.flush()
    setattr(
        owner,
        relationship_name,
        [
            link_type(**{value_name: value, "position": position})
            for position, value in enumerate(values, 1)
        ],
    )
