"""Role permissions from the spec's `roles` section, checked on every protected endpoint.

A call is 'allowed' when it is not answered with 403 (it may still be 404/422 for dummy data).
"""
import uuid

import pytest

from tests.conftest import make_user

ID = str(uuid.uuid4())
OWNER, ADMIN, MANAGER, SALES = "owner", "admin", "manager", "sales"
ALL = {OWNER, ADMIN, MANAGER, SALES}

# (method, path, roles that may call it)
MATRIX = [
    ("GET", "/api/v1/me", ALL),
    ("GET", "/api/v1/leads", {OWNER, MANAGER, SALES}),
    ("POST", "/api/v1/leads", {OWNER, MANAGER, SALES}),
    ("GET", f"/api/v1/leads/{ID}", {OWNER, MANAGER, SALES}),
    ("GET", f"/api/v1/leads/{ID}/messages", {OWNER, MANAGER, SALES}),
    ("PATCH", f"/api/v1/leads/{ID}", {OWNER, MANAGER, SALES}),
    ("POST", f"/api/v1/leads/{ID}/assign", {OWNER, MANAGER}),
    ("POST", f"/api/v1/leads/{ID}/takeover", {OWNER, MANAGER, SALES}),
    ("POST", f"/api/v1/leads/{ID}/release", {OWNER, MANAGER, SALES}),
    ("POST", f"/api/v1/leads/{ID}/ai-pause", {OWNER, MANAGER, SALES}),
    ("POST", f"/api/v1/leads/{ID}/send", {OWNER, MANAGER, SALES}),
    ("GET", "/api/v1/approvals", {OWNER, MANAGER, SALES}),
    ("POST", f"/api/v1/messages/{ID}/approve", {OWNER, MANAGER, SALES}),
    ("POST", f"/api/v1/messages/{ID}/reject", {OWNER, MANAGER, SALES}),
    ("GET", "/api/v1/quotations", {OWNER, MANAGER, SALES}),
    ("POST", f"/api/v1/quotations/{ID}/approve", {OWNER, MANAGER}),
    ("POST", f"/api/v1/quotations/{ID}/reject", {OWNER, MANAGER}),
    ("GET", "/api/v1/handoffs", {OWNER, MANAGER, SALES}),
    ("POST", f"/api/v1/handoffs/{ID}/resolve", {OWNER, MANAGER, SALES}),
    ("GET", "/api/v1/products", ALL),
    ("POST", "/api/v1/products", {OWNER, ADMIN}),
    ("PATCH", f"/api/v1/products/{ID}", {OWNER, ADMIN}),
    ("POST", f"/api/v1/products/{ID}/rules", {OWNER, ADMIN}),
    ("DELETE", f"/api/v1/price-rules/{ID}", {OWNER, ADMIN}),
    ("POST", "/api/v1/price/test", ALL),
    ("GET", "/api/v1/knowledge", {OWNER, ADMIN, MANAGER}),
    ("POST", "/api/v1/knowledge", {OWNER, ADMIN}),
    ("PATCH", f"/api/v1/knowledge/{ID}", {OWNER, ADMIN}),
    ("DELETE", f"/api/v1/knowledge/{ID}", {OWNER, ADMIN}),
    ("POST", "/api/v1/knowledge/test-search", {OWNER, ADMIN, MANAGER}),
    ("GET", "/api/v1/analytics/summary", {OWNER, MANAGER}),
    ("GET", "/api/v1/analytics/stuck-leads", {OWNER, MANAGER}),
    ("POST", "/api/v1/reports/daily/send-now", {OWNER, MANAGER}),
    ("GET", "/api/v1/export/leads", {OWNER, MANAGER}),
    ("POST", "/api/v1/leads/import", {OWNER, MANAGER}),
    ("POST", "/api/v1/export/webhook-test", {OWNER, ADMIN}),
    ("GET", "/api/v1/settings", {OWNER, ADMIN}),
    ("PATCH", "/api/v1/settings", {OWNER, ADMIN}),
    ("GET", "/api/v1/settings/widget-embed", {OWNER, ADMIN}),
    ("GET", "/api/v1/users", {OWNER, ADMIN}),
    ("POST", "/api/v1/users/invite", {OWNER, ADMIN}),
    ("PATCH", f"/api/v1/users/{ID}", {OWNER, ADMIN}),
    ("GET", "/api/v1/team", {OWNER, MANAGER}),
    ("GET", "/api/v1/notifications", ALL),
    ("POST", "/api/v1/notifications/read-all", ALL),
    ("POST", f"/api/v1/notifications/{ID}/read", ALL),
]


@pytest.mark.parametrize("role", sorted(ALL))
def test_every_endpoint_enforces_the_role_table(client, db, role):
    _, headers = make_user(db, role)
    wrong = []
    for method, path, allowed in MATRIX:
        r = client.request(method, path, headers=headers, json={} if method in ("POST", "PATCH", "PUT") else None)
        denied = r.status_code == 403
        if (role in allowed) == denied:
            wrong.append(f"{role} {method} {path} -> {r.status_code}, expected {'allowed' if role in allowed else '403'}")
    assert not wrong, "\n".join(wrong)


def test_every_endpoint_rejects_anonymous_callers(client):
    open_paths = {"/api/v1/leads/import"}  # multipart: checked separately below
    for method, path, _ in MATRIX:
        r = client.request(method, path, json={} if method in ("POST", "PATCH") else None)
        assert r.status_code in (401, 422), f"{method} {path} -> {r.status_code}"
        if path not in open_paths:
            assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
    assert client.post("/api/v1/leads/import", files={"file": ("a.csv", b"phone\n1")}).status_code == 401


def test_every_dashboard_route_is_in_the_role_table(client):
    """A new route added without a row in MATRIX fails here, so permissions are never forgotten."""
    from app.main import app

    covered = {(m, p.replace(ID, "{id}")) for m, p, _ in MATRIX}
    public = ("/api/v1/public", "/api/v1/webhooks", "/health", "/docs", "/openapi.json", "/redoc")
    import re

    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith(public) or not path.startswith("/api/v1"):
            continue
        norm = re.sub(r"\{[^}]+\}", "{id}", path)
        for m in route.methods - {"HEAD", "OPTIONS"}:
            if (m, norm) not in covered:
                missing.append(f"{m} {path}")
    assert not missing, "Add these routes to MATRIX: " + ", ".join(missing)
