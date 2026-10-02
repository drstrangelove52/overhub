import json

import pytest

from app import docker_ops, runner
from app.catalog import load_catalog, version_tuple
from app.envfile import format_value, read_env, write_env
from app.operations import build_env
from app.security import generate_secret


def test_catalog_loads_both_apps_with_fixed_ports():
    catalog = load_catalog()
    assert catalog["overcook"].port == 8443 and catalog["overcook"].internal_port == 18443
    assert catalog["overstand"].port == 8447 and catalog["overstand"].internal_port == 18447
    for manifest in catalog.values():
        assert manifest.compose_file.is_file()
        assert all(d.startswith("sha256:") and len(d) == 71 for d in manifest.images.values())


def test_catalog_compose_files_reference_only_catalog_images_by_version():
    for manifest in load_catalog().values():
        compose = manifest.compose_file.read_text()
        assert "build:" not in compose
        assert "127.0.0.1:${APP_INTERNAL_PORT}:80" in compose
        for repo in manifest.images:
            assert f"{repo}:${{APP_VERSION}}" in compose


def test_version_tuple():
    assert version_tuple("0.1.10") > version_tuple("0.1.9")
    assert version_tuple("1.0.0-rc1") == (1, 0, 0)


@pytest.mark.parametrize("kind,length", [("password", 24), ("token", 64), ("fernet", 44)])
def test_generate_secret(kind, length):
    a, b = generate_secret(kind), generate_secret(kind)
    assert len(a) == length and a != b


def test_env_quoting_roundtrip(tmp_path):
    env = {"A": "plain-value_1", "B": "has space $HOME", "C": ""}
    write_env(tmp_path / ".env", env)
    text = (tmp_path / ".env").read_text()
    assert "B='has space $HOME'" in text
    assert read_env(tmp_path / ".env") == env


def test_env_rejects_quote_and_newline():
    with pytest.raises(ValueError):
        format_value("it's")
    with pytest.raises(ValueError):
        format_value("a\nb")


def test_build_env_install(host):
    manifest = load_catalog()["overcook"]
    env, creds = build_env(manifest, {"ADMIN_USERNAME": "martin"}, ["mcp"])
    assert env["APP_VERSION"] == manifest.version
    assert env["APP_INTERNAL_PORT"] == "18443"
    assert env["APP_PUBLIC_URL"] == "https://pi.tail1234.ts.net:8443"
    assert env["COMPOSE_PROFILES"] == "mcp"
    assert env["MCP_SERVICE_USERNAME"] == "mcp-agent"
    assert len(env["MYSQL_PASSWORD"]) == 24 and len(env["MCP_API_KEY"]) == 64
    assert creds == {"ADMIN_USERNAME": "martin", "ADMIN_PASSWORD": env["ADMIN_PASSWORD"],
                     "MCP_API_KEY": env["MCP_API_KEY"]}


def test_build_env_never_regenerates_existing_secrets(host):
    manifest = load_catalog()["overcook"]
    first, _ = build_env(manifest, {"ADMIN_USERNAME": "martin"}, [])
    second, creds = build_env(manifest, {"ADMIN_USERNAME": "other"}, [], existing=first)
    for key in ("MYSQL_PASSWORD", "SESSION_SECRET", "ADMIN_PASSWORD"):
        assert second[key] == first[key]
    assert second["ADMIN_USERNAME"] == "martin"
    assert creds == {}


def test_build_env_enabling_component_later_only_adds_its_secrets(host):
    manifest = load_catalog()["overcook"]
    first, _ = build_env(manifest, {}, [])
    assert "COMPOSE_PROFILES" not in first
    second, creds = build_env(manifest, {}, ["mcp"], existing=first)
    assert second["MYSQL_PASSWORD"] == first["MYSQL_PASSWORD"]
    assert set(creds) == {"MCP_API_KEY"}


def test_ps_parses_ndjson_and_array(monkeypatch, tmp_path):
    rows = [{"Service": "db", "State": "running", "Health": "healthy"},
            {"Service": "web", "State": "running", "Health": ""}]
    for output in ("\n".join(json.dumps(r) for r in rows), json.dumps(rows)):
        monkeypatch.setattr(runner, "run", lambda *a, **k: runner.Result(0, output))
        states = docker_ops.ps(tmp_path)
        assert [s.service for s in states] == ["db", "web"]
        assert docker_ops.all_healthy(states)


def test_all_healthy_false_while_starting():
    assert not docker_ops.all_healthy([docker_ops.ServiceState("db", "running", "starting")])
    assert not docker_ops.all_healthy([])


def test_runner_keeps_stderr_warnings_out_of_parsed_output():
    # tailscale prints "client version != tailscaled server version" to stderr
    # when the CLI in the image is newer than the host's daemon (seen on a Pi).
    import sys

    code = "import sys; print('Warning: version mismatch', file=sys.stderr); print('{\"ok\": true}')"
    separate = runner.run([sys.executable, "-c", code], merge_stderr=False)
    assert json.loads(separate.output) == {"ok": True}
    assert "Warning" in separate.stderr
    merged = runner.run([sys.executable, "-c", code])
    assert "Warning" in merged.output
