"""Versioned business API router foundation."""

from fastapi import APIRouter

from media_recommender.web.routes.media import router as media_router

router = APIRouter(prefix="/api/v1")
router.include_router(media_router)
