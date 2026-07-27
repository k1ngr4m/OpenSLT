from __future__ import annotations

import typing

from sqlalchemy.orm import Session

from app.models import (
    RunResource,
    ScenarioResource,
    ScenarioWorkflowVersion,
    TestRun,
    TestScenario,
    WorkflowVersionResource,
)


def _ordered_ids(links: typing.Iterable[typing.Any]) -> typing.List[int]:
    return [link.resource_id for link in sorted(links, key=lambda item: item.position)]


def scenario_resource_ids(scenario: TestScenario) -> typing.List[int]:
    normalized = _ordered_ids(scenario.resource_links)
    return normalized if normalized else list(scenario.default_resource_ids or [])


def workflow_resource_ids(version: ScenarioWorkflowVersion) -> typing.List[int]:
    normalized = _ordered_ids(version.resource_links)
    return normalized if normalized else list(version.resource_ids or [])


def run_resource_ids(run: TestRun) -> typing.List[int]:
    normalized = _ordered_ids(run.resource_links)
    return normalized if normalized else list(run.resource_ids or [])


def sync_scenario_resources(
    scenario: TestScenario,
    resource_ids: typing.Iterable[int],
    db: typing.Optional[Session] = None,
) -> None:
    ordered = list(resource_ids)
    scenario.default_resource_ids = ordered
    _replace_links(scenario, "resource_links", ScenarioResource, ordered, db)


def sync_workflow_resources(
    version: ScenarioWorkflowVersion,
    resource_ids: typing.Iterable[int],
    db: typing.Optional[Session] = None,
) -> None:
    ordered = list(resource_ids)
    version.resource_ids = ordered
    _replace_links(version, "resource_links", WorkflowVersionResource, ordered, db)


def sync_run_resources(run: TestRun, resource_ids: typing.Iterable[int]) -> None:
    ordered = list(resource_ids)
    run.resource_ids = ordered
    _replace_links(run, "resource_links", RunResource, ordered)


def _replace_links(
    owner: typing.Any,
    relationship_name: str,
    link_type: typing.Type[typing.Any],
    resource_ids: typing.List[int],
    db: typing.Optional[Session] = None,
) -> None:
    current = list(getattr(owner, relationship_name))
    if _ordered_ids(current) == resource_ids:
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
            link_type(resource_id=resource_id, position=position)
            for position, resource_id in enumerate(resource_ids, 1)
        ],
    )
