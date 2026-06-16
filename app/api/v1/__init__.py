from fastapi import APIRouter

from app.api.v1.routes_cortex import router as cortex_router
from app.api.v1.routes_health import router as health_router
from app.api.v1.routes_intake import router as intake_router
from app.api.v1.routes_layer3 import router as layer3_router
from app.api.v1.routes_layer4 import router as layer4_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(intake_router)
api_router.include_router(cortex_router)
api_router.include_router(layer3_router)
api_router.include_router(layer4_router)
