from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    attribution,
    auth,
    browser,
    clients,
    connect_google,
    connect_meta,
    manage,
)
from .config import get_settings
from .db import Base, engine

app = FastAPI(title="Atlas Reach Ads Platform + Salescale")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(connect_meta.router)
app.include_router(connect_google.router)
app.include_router(browser.router)
app.include_router(manage.router)
app.include_router(attribution.router)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.on_event("startup")
def _create_tables():
    # Dev convenience; production schema changes go through Alembic.
    Base.metadata.create_all(engine)
