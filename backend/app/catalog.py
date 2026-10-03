"""Catalog manifests (Over-App-Vertrag, manifest schema 1).

The catalog ships inside the OverHub image (catalog/<id>/manifest.yml +
compose.yml), so a catalog update is an OverHub update.
"""
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from app.config import settings


class GeneratedSecret(BaseModel):
    name: str
    generate: str  # password | token | fernet
    show_once: bool = False
    persistent: bool = False


class Setting(BaseModel):
    name: str
    label: str
    default: str = ""
    only_at_install: bool = False


class EnvSpec(BaseModel):
    generated: list[GeneratedSecret] = []
    fixed: dict[str, str] = {}
    settings: list[Setting] = []


class Component(BaseModel):
    label: str
    description: str = ""
    default: bool = False
    env: EnvSpec = EnvSpec()


class BackupCommand(BaseModel):
    service: str
    command: str  # run as `docker compose exec -T <service> <command>` (shlex-split)


class BackupSpec(BaseModel):
    dump: BackupCommand | None = None  # writes the dump to stdout
    restore: BackupCommand | None = None  # reads the dump from stdin
    volumes: list[str] = []  # compose volume names with user data besides the dump


class EmergencyLogin(BaseModel):
    """Local admin account for when OverHub is down (contract rule 8). OverHub
    sets a new password: `docker compose exec -T <service> <command>` with the
    password on stdin (never on a command line)."""

    service: str
    username: str
    command: str  # creates or resets <username> as admin, password from stdin
    # Older app versions may not read stdin (OverCook < 0.2.3 would set "-" as password).
    min_version: str = "0.0.0"


class Manifest(BaseModel):
    schema_: int = Field(alias="schema")
    id: str
    name: str
    description: str = ""
    version: str
    min_overhub: str = "0.0.0"
    port: int
    arch: list[str]
    min_ram_mb: int = 0
    pwa: bool = False
    icon: str | None = None
    images: dict[str, str]  # repository -> digest of the multi-arch index
    env: EnvSpec = EnvSpec()
    components: dict[str, Component] = {}
    health: str = "/api/health"
    version_endpoint: str = "/api/version"
    backup: BackupSpec | None = None  # None: no server-side data (manifest says `backup: none`)
    emergency_login: EmergencyLogin | None = None  # None: app has no login of its own

    @field_validator("backup", mode="before")
    @classmethod
    def _backup_none(cls, value):
        return None if value in (None, "none") else value

    @property
    def internal_port(self) -> int:
        return self.port + 10000

    @property
    def dir(self) -> Path:
        return settings.catalog_dir / self.id

    @property
    def compose_file(self) -> Path:
        return self.dir / "compose.yml"


def _load(path: Path) -> Manifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest = Manifest.model_validate(data)
    if manifest.id != path.parent.name:
        raise ValueError(f"{path}: id '{manifest.id}' does not match folder name")
    if manifest.schema_ != 1:
        raise ValueError(f"{path}: unsupported manifest schema {manifest.schema_}")
    if not manifest.compose_file.is_file():
        raise ValueError(f"{path}: compose.yml missing")
    return manifest


@lru_cache
def load_catalog() -> dict[str, Manifest]:
    manifests = [_load(p) for p in sorted(settings.catalog_dir.glob("*/manifest.yml"))]
    ports = [m.port for m in manifests]
    if len(ports) != len(set(ports)):
        raise ValueError("catalog: two apps share a port")
    return {m.id: m for m in manifests}


def get_manifest(app_id: str) -> Manifest | None:
    return load_catalog().get(app_id)


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("-")[0].split("."))
