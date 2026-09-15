"""Versioned business API router foundation."""

from fastapi import APIRouter

from media_recommender.web.routes.media import router as media_router
from media_recommender.web.routes.recommendations import router as recommendations_router

router = APIRouter(prefix="/api/v1")
router.include_router(media_router)
router.include_router(recommendations_router)
