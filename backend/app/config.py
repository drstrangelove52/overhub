from pathlib import Path

from pydantic_settings import BaseSettings

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = {"env_prefix": "OVERHUB_"}

    # Everything OverHub writes lives here: overhub.db and apps/<id>/{.env,compose.yml}.
    # Mounted at the same path in the container, so `docker compose` paths match the host.
    data_dir: Path = Path("/opt/overhub")
    catalog_dir: Path = _REPO_ROOT / "catalog"
    static_dir: Path | None = None  # built frontend (dist); None in tests/dev

    version: str = "dev"
    tz: str = "Europe/Zurich"

    session_cookie_name: str = "overhub_session"
    session_cookie_secure: bool = True
    session_max_age_seconds: int = 1209600  # 14 days

    # First-start bootstrap of the single admin (only while no user exists).
    admin_username: str = "admin"
    admin_password: str | None = None

    # How long an install/update may wait for the app to turn healthy. A first
    # pull plus MariaDB init on a Pi takes a few minutes.
    health_timeout_seconds: int = 600

    scheduler_enabled: bool = True  # off in tests

    @property
    def apps_dir(self) -> Path:
        return self.data_dir / "apps"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'overhub.db'}"


settings = Settings()
