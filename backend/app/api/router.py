from fastapi import APIRouter

from app.api.routes import (
    auth,
    database_config_templates,
    observability,
    plans,
    resources,
    runs,
    smart_cases,
    websockets,
    workflows,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(resources.router)
router.include_router(database_config_templates.router)
router.include_router(plans.router)
router.include_router(workflows.router)
router.include_router(runs.router)
router.include_router(observability.router)
router.include_router(smart_cases.router)
router.include_router(websockets.router)
