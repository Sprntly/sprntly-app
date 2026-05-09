from fastapi import APIRouter

from app.config import settings

router = APIRouter()


@router.get("/")
def root():
    return {"service": "sprintly-api", "version": "0.1.1", "status": "ok"}


@router.get("/healthz")
def healthz():
    return {"status": "ok", "env": settings.env}
