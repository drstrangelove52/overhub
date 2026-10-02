from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import operations, scheduler, tailscale_ops
from app.bootstrap import init_db
from app.config import settings
from app.database import get_db
from app.routers.apps import router as apps_router
from app.routers.auth import current_user, limiter, router as auth_router
from app.routers.backup import router as backup_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if settings.scheduler_enabled:
        scheduler.start()
    yield


app = FastAPI(title="OverHub", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.include_router(auth_router)
app.include_router(apps_router)
app.include_router(backup_router)


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})
    return {"status": "ok"}


@app.get("/api/version")
def version():
    return {"version": settings.version}


@app.get("/api/system", dependencies=[Depends(current_user)])
def system():
    st = tailscale_ops.status()
    self_ = st.get("Self") or {}
    return {
        "version": settings.version,
        "arch": operations.host_arch(),
        "ram_mb": operations.host_ram_mb(),
        "tailscale": {
            "state": st.get("BackendState"),
            "dns_name": tailscale_ops.dns_name(st),
            "key_expiry": self_.get("KeyExpiry"),
            "cert_domains": st.get("CertDomains") or [],
        },
    }


# The built Vue app; every non-API path falls back to index.html (client-side routing).
if settings.static_dir and settings.static_dir.is_dir():
    _static = settings.static_dir.resolve()
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        candidate = (_static / path).resolve()
        if path and candidate.is_file() and _static in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_static / "index.html")
