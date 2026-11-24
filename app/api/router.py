from fastapi import APIRouter

from app.api import apartments, chat, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(apartments.router, prefix="/api/v1")
api_router.include_router(chat.router, prefix="/api/v1")
