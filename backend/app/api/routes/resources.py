from fastapi import APIRouter

from app.api.routes import database_resources, database_updates, resource_configs, resource_core

router = APIRouter()
router.include_router(resource_core.router)
router.include_router(database_resources.router)
router.include_router(resource_configs.router)
router.include_router(database_updates.router)
