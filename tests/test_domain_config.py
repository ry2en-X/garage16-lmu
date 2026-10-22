"""
garage16.de (V0.9.0): the dashboard and the API live on one address,
https://garage16.de. This is configuration and documentation, so these tests
pin that every place that has to know the address agrees, that the variable
names and service names the admin guide tells you to use really exist in the
stack, and that the client-migration path the guide relies on behaves the way
the guide says.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = "https://garage16.de"

ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
COMPOSE_TEXT = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
COMPOSE = yaml.safe_load(COMPOSE_TEXT)
ADMIN_GUIDE = (ROOT / "docs" / "ADMIN_GUIDE.md").read_text(encoding="utf-8")
CADDYFILE = (ROOT / "docker" / "Caddyfile").read_text(encoding="utf-8")


def _active_env(name: str) -> str:
    match = re.search(rf"^{name}=(.*)$", ENV_EXAMPLE, re.MULTILINE)
    assert match, f"{name} is not set (uncommented) in .env.example"
    return match.group(1).strip()


def _chapter_10() -> str:
    start = ADMIN_GUIDE.index("## 10. ")
    return ADMIN_GUIDE[start:]


# ---------------------------------------------------------------------------
# One address everywhere
# ---------------------------------------------------------------------------

def test_env_example_points_at_the_domain():
    assert _active_env("LMU_GARAGE_CORS_ORIGINS") == DOMAIN
    assert _active_env("LMU_GARAGE_FRONTEND_URL") == DOMAIN


def test_compose_defaults_point_at_the_domain_and_no_placeholder_is_left():
    environment = COMPOSE["services"]["server"]["environment"]
    assert environment["LMU_GARAGE_CORS_ORIGINS"].endswith(f":-{DOMAIN}}}")
    assert environment["LMU_GARAGE_FRONTEND_URL"].endswith(f":-{DOMAIN}}}")
    assert "your-domain.example" not in COMPOSE_TEXT
    active = [line for line in ENV_EXAMPLE.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert not [line for line in active if "your-domain.example" in line or "example.com" in line]


def test_the_web_app_stays_same_origin_so_one_address_serves_both():
    config_js = (ROOT / "web" / "config.js").read_text(encoding="utf-8")
    assert re.search(r'^window\.LMU_GARAGE_API_BASE_URL\s*=\s*"";', config_js, re.MULTILINE)


def test_client_build_defaults_to_the_domain_and_can_still_be_overridden():
    script = (ROOT / "packaging" / "build_windows.ps1").read_text(encoding="utf-8")
    # V0.9.2: the param block gained -Edition, so it spans several lines now; the default is unchanged
    assert f'[string]$ServerUrl = "{DOMAIN}"' in script
    assert "if ($ServerUrl) { $env:GARAGE16_SERVER_URL = $ServerUrl }" in script      # "" still means: ask the friend

    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build-client.yml").read_text(encoding="utf-8"))
    steps = [step.get("run", "") for job in workflow["jobs"].values() for step in job.get("steps", [])]  # V0.9.7: a job may call a workflow
    build = next(run for run in steps if "build_windows.ps1" in run)
    assert "vars.GARAGE16_SERVER_URL" in build and DOMAIN in build                     # variable wins, domain is the fallback

    guide = (ROOT / "docs" / "CLIENT_BUILD.md").read_text(encoding="utf-8")
    assert DOMAIN in guide
    friends = (ROOT / "docs" / "FRIENDS_GUIDE.md").read_text(encoding="utf-8")
    assert DOMAIN in friends


def test_the_client_code_itself_still_contains_no_server_address():
    """The address is baked in at BUILD time (garage16_server.txt), never hard-coded in the
    client — so moving the server again needs no code change. (Docstrings and comments may
    mention it; string values the code actually uses may not.)"""
    import ast

    offenders = []
    for path in (ROOT / "client").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                if "garage16.de" in node.value:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


# ---------------------------------------------------------------------------
# What the admin guide tells you to type must exist
# ---------------------------------------------------------------------------

def test_every_variable_in_the_domain_chapter_exists_in_the_stack():
    chapter = _chapter_10()
    blocks = re.findall(r"^[ ]*```[a-z]*\n(.*?)^[ ]*```", chapter, re.DOTALL | re.MULTILINE)
    assert len(blocks) == 4          # .env, Gmail, docker compose, MIGRATED_TO
    names = set()
    for block in blocks:
        names.update(re.findall(r"^\s*([A-Z][A-Z0-9_]+)=", block, re.MULTILINE))
    assert {"CLOUDFLARE_TUNNEL_TOKEN", "LMU_GARAGE_CORS_ORIGINS", "LMU_GARAGE_FRONTEND_URL",
            "LMU_GARAGE_EMAIL_PROVIDER", "LMU_GARAGE_SMTP_HOST", "LMU_GARAGE_SMTP_PORT",
            "LMU_GARAGE_SMTP_USER", "LMU_GARAGE_SMTP_PASSWORD", "LMU_GARAGE_SMTP_FROM",
            "LMU_GARAGE_MIGRATED_TO"} <= names
    for name in names:
        assert name in COMPOSE_TEXT, f"chapter 10 tells you to set {name}, but docker-compose.yml never passes it on"
    inline = set(re.findall(r"`(LMU_GARAGE_[A-Z_]+|CLOUDFLARE_TUNNEL_TOKEN|CADDY_SITE_ADDRESS)", chapter))
    for name in inline:
        assert name in COMPOSE_TEXT, name


def test_the_tunnel_target_in_the_guide_is_the_real_proxy_service_and_port():
    chapter = _chapter_10()
    assert "reverse_proxy:80" in chapter
    assert "reverse_proxy" in COMPOSE["services"]
    assert "{$CADDY_SITE_ADDRESS:80}" in CADDYFILE                                      # port 80 unless overridden
    assert "CADDY_SITE_ADDRESS: ${CADDY_SITE_ADDRESS:-:80}" in COMPOSE_TEXT
    assert COMPOSE["services"]["cloudflared"]["profiles"] == ["tunnel"]                 # what `--profile tunnel` starts
    assert "${CLOUDFLARE_TUNNEL_TOKEN" in COMPOSE_TEXT
    assert "--profile tunnel up -d --build" in chapter


def test_the_gmail_block_in_the_guide_matches_the_template():
    chapter = _chapter_10()
    for needle in ("LMU_GARAGE_EMAIL_PROVIDER=smtp", "LMU_GARAGE_SMTP_HOST=smtp.gmail.com", "LMU_GARAGE_SMTP_PORT=587",
                   "LMU_GARAGE_SMTP_USER=garage16.lmu@gmail.com", "LMU_GARAGE_SMTP_FROM=garage16.lmu@gmail.com"):
        assert needle in chapter, needle
        assert needle in ENV_EXAMPLE, needle        # the commented Gmail example in .env.example


def test_the_guide_warns_about_the_old_dns_records_and_keeps_the_mail_records():
    chapter = _chapter_10()
    assert "A-/AAAA-/CNAME-Einträge für `@` und `www`" in chapter
    assert "MX- und TXT-Einträge" in chapter
    assert "IP-Adresse des A-Eintrags für `@`" in chapter           # needed to move the webspace
    assert "DNS only" in chapter
    assert "Subdomain **leer lassen**" in chapter                    # the apex hostname


# ---------------------------------------------------------------------------
# The client-migration path the guide relies on
# ---------------------------------------------------------------------------

@pytest.fixture()
def migration(monkeypatch):
    from client import migration as module

    saved = []
    monkeypatch.setattr(module, "save_config", lambda config: saved.append(config))
    monkeypatch.setattr(module, "_verify_new_server_reachable", lambda url, timeout=5: True)
    return module, saved


def test_a_client_on_the_old_quick_tunnel_address_follows_the_move_to_the_domain(migration):
    module, saved = migration
    from client.config import ClientConfig
    old = ClientConfig("https://kde-calls-stronger-php.trycloudflare.com", "tok", "secret")
    result = module.apply_migration_if_safe(old, {"status": "ok", "migrated_to": DOMAIN})
    assert result.migrated is True
    assert result.config.api_url == DOMAIN
    assert (result.config.auth_token, result.config.client_secret) == ("tok", "secret")   # credentials travel unchanged
    assert saved and saved[0].api_url == DOMAIN


def test_a_client_already_on_the_domain_does_nothing_while_the_flag_is_still_set(migration):
    module, saved = migration
    from client.config import ClientConfig
    current = ClientConfig(DOMAIN, "tok", "secret")
    result = module.apply_migration_if_safe(current, {"status": "ok", "migrated_to": DOMAIN})
    assert result.migrated is False and result.message is None and result.config == current
    assert saved == []


def test_a_client_on_plain_http_is_never_moved_automatically(migration):
    module, saved = migration
    from client.config import ClientConfig
    result = module.apply_migration_if_safe(ClientConfig("http://192.168.178.104:8000", "tok", "secret"),
                                            {"status": "ok", "migrated_to": DOMAIN})
    assert result.migrated is False and saved == []
    assert result.message and "ask whoever runs your Garage16 server" in result.message


def test_the_server_advertises_the_move_through_health(client, monkeypatch):
    from server.config import settings
    monkeypatch.setattr(settings, "migrated_to_url", DOMAIN)
    body = client.get("/health").json()
    assert body["migrated_to"] == DOMAIN
    monkeypatch.setattr(settings, "migrated_to_url", None)
    assert "migrated_to" not in client.get("/health").json()
