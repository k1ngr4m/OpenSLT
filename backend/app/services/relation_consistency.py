from __future__ import annotations

import typing
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import ScenarioWorkflowNode, ScenarioWorkflowVersion, TestPlan, TestRun, TestScenario
from app.services.resource_relations import (
    sync_node_contract_files,
    sync_plan_resources,
    sync_run_resources,
    sync_scenario_resources,
    sync_workflow_resources,
)


@dataclass(frozen=True)
class RelationDrift:
    relation: str
    owner_id: int
    legacy_ids: typing.List[int]
    normalized_ids: typing.List[int]

    def as_dict(self) -> typing.Dict[str, typing.Any]:
        return asdict(self)


def _ids(value: typing.Any) -> typing.List[int]:
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            item_id = int(item)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in seen:
            result.append(item_id)
            seen.add(item_id)
    return result


def _link_ids(
    links: typing.Iterable[typing.Any],
    value_name: str = "resource_id",
) -> typing.List[int]:
    return [
        getattr(link, value_name)
        for link in sorted(links, key=lambda item: item.position)
    ]


def find_relation_drifts(db: Session) -> typing.List[RelationDrift]:
    drifts = []
    owners: typing.Iterable[typing.Tuple[str, typing.Any, str]] = (
        (
            "plan_resources",
            db.scalars(select(TestPlan).options(selectinload(TestPlan.resource_links))).all(),
            "default_resource_ids",
        ),
        (
            "scenario_resources",
            db.scalars(
                select(TestScenario).options(selectinload(TestScenario.resource_links))
            ).all(),
            "default_resource_ids",
        ),
        (
            "workflow_version_resources",
            db.scalars(
                select(ScenarioWorkflowVersion).options(
                    selectinload(ScenarioWorkflowVersion.resource_links)
                )
            ).all(),
            "resource_ids",
        ),
        (
            "run_resources",
            db.scalars(select(TestRun).options(selectinload(TestRun.resource_links))).all(),
            "resource_ids",
        ),
    )
    for relation, rows, legacy_name in owners:
        for owner in rows:
            legacy_ids = _ids(getattr(owner, legacy_name))
            normalized_ids = _link_ids(owner.resource_links)
            if legacy_ids != normalized_ids:
                drifts.append(
                    RelationDrift(relation, owner.id, legacy_ids, normalized_ids)
                )

    nodes = db.scalars(
        select(ScenarioWorkflowNode).options(
            selectinload(ScenarioWorkflowNode.contract_file_links)
        )
    ).all()
    for node in nodes:
        legacy_ids = _ids((node.config or {}).get("contract_file_ids"))
        normalized_ids = _link_ids(node.contract_file_links, "contract_file_id")
        if legacy_ids != normalized_ids:
            drifts.append(
                RelationDrift(
                    "workflow_node_contract_files",
                    node.id,
                    legacy_ids,
                    normalized_ids,
                )
            )
    return drifts


def repair_relation_drifts(db: Session, source: str = "relations") -> int:
    if source not in {"relations", "json"}:
        raise ValueError("source must be 'relations' or 'json'")
    drifts = find_relation_drifts(db)
    for drift in drifts:
        values = drift.normalized_ids if source == "relations" else drift.legacy_ids
        if drift.relation == "plan_resources":
            owner = db.get(TestPlan, drift.owner_id)
            sync_plan_resources(owner, values, db)
        elif drift.relation == "scenario_resources":
            owner = db.get(TestScenario, drift.owner_id)
            sync_scenario_resources(owner, values, db)
        elif drift.relation == "workflow_version_resources":
            owner = db.get(ScenarioWorkflowVersion, drift.owner_id)
            sync_workflow_resources(owner, values, db)
        elif drift.relation == "run_resources":
            owner = db.get(TestRun, drift.owner_id)
            sync_run_resources(owner, values, db)
        else:
            owner = db.get(ScenarioWorkflowNode, drift.owner_id)
            sync_node_contract_files(owner, values, db)
    db.flush()
    return len(drifts)
