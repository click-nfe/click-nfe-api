from types import SimpleNamespace

from flask import Flask, g

from app.integrations.portal_unico.client import (
    DefaultPortalCredentialResolver,
    GcpManagedPortalCredentialStore,
)
from app.routes import nfe_automation_routes


def test_nfe_context_service_uses_managed_portal_credential_store(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        GCP_PROJECT_ID="click-nfe-project",
        PORTAL_UNICO_CREDENTIAL_STORAGE_PROVIDER="gcp_secret_manager",
        PORTAL_UNICO_MANAGED_SECRET_NAME="click-nfe-portal-unico-credentials",
    )
    monkeypatch.setattr(
        nfe_automation_routes,
        "ImportNfeService",
        lambda **kwargs: kwargs,
    )

    with app.test_request_context():
        g.current_user = SimpleNamespace(id="user-id")
        service_args = nfe_automation_routes._service()

    resolver = service_args["credential_resolver"]
    assert isinstance(resolver, DefaultPortalCredentialResolver)
    assert isinstance(resolver.managed_resolver, GcpManagedPortalCredentialStore)
    assert resolver.managed_resolver.project_id == "click-nfe-project"
    assert service_args["current_user"].id == "user-id"
