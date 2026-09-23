from datetime import datetime, timedelta

from cryptography.fernet import Fernet
import jwt
import pytest

from app import create_app
from app.extensions import db
from app.integrations.portal_unico import PortalUnicoApiError
from app.models import ExternalProviderConnection, Organization, User


class SuccessfulGateway:
    def healthcheck(self):
        return {"status": "ok", "environment": "production"}


@pytest.fixture
def api(tmp_path):
    class TestConfig:
        TESTING = True
        SECRET_KEY = "portal-settings-test-secret"
        SQLALCHEMY_DATABASE_URI = "sqlite://"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        JWT_ACCESS_EXPIRES_SECONDS = 3600
        JWT_REFRESH_EXPIRES_SECONDS = 604800
        PORTAL_UNICO_CREDENTIAL_STORAGE_PROVIDER = "local_encrypted_file"
        PORTAL_UNICO_LOCAL_SECRET_DIR = str(tmp_path / "portal-unico")
        PORTAL_UNICO_LOCAL_SECRET_KEY = Fernet.generate_key().decode("ascii")
        PORTAL_UNICO_TIMEOUT_SECONDS = 1

    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        first_organization = Organization(
            nome="Organização Portal A",
            slug="org-portal-a",
        )
        second_organization = Organization(
            nome="Organização Portal B",
            slug="org-portal-b",
        )
        db.session.add_all([first_organization, second_organization])
        db.session.flush()
        admin = User(
            organization_id=first_organization.id,
            nome="Administrador A",
            email="admin-a@example.invalid",
            role="admin",
            ativo=True,
        )
        operator = User(
            organization_id=first_organization.id,
            nome="Operador A",
            email="operator-a@example.invalid",
            role="operator",
            ativo=True,
        )
        other_admin = User(
            organization_id=second_organization.id,
            nome="Administrador B",
            email="admin-b@example.invalid",
            role="admin",
            ativo=True,
        )
        for user in (admin, operator, other_admin):
            user.set_password("test-password")
        db.session.add_all([admin, operator, other_admin])
        db.session.commit()

        def headers(user):
            now = datetime.utcnow()
            token = jwt.encode(
                {
                    "sub": str(user.id),
                    "email": user.email,
                    "role": user.role,
                    "principal_type": "user",
                    "type": "access",
                    "iat": now,
                    "exp": now + timedelta(hours=1),
                },
                app.config["SECRET_KEY"],
                algorithm="HS256",
            )
            return {"Authorization": f"Bearer {token}"}

        yield {
            "app": app,
            "client": app.test_client(),
            "admin_headers": headers(admin),
            "operator_headers": headers(operator),
            "other_admin_headers": headers(other_admin),
            "organization_id": str(first_organization.id),
            "secret_dir": tmp_path / "portal-unico",
        }
        db.session.remove()
        db.drop_all()


def _configure(client, headers, *, client_id="portal-client-1234", secret="secret-value"):
    return client.put(
        "/organizations/me/integrations/portal-unico",
        headers=headers,
        json={
            "client_id": client_id,
            "client_secret": secret,
        },
    )


def test_get_reports_missing_configuration(api):
    response = api["client"].get(
        "/organizations/me/integrations/portal-unico",
        headers=api["admin_headers"],
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["configured"] is False
    assert body["state"] == "not_configured"
    assert body["ready_for_duimp"] is False
    assert body["blockers"][0]["code"] == "portal_unico_not_configured"


def test_admin_configures_and_rotates_encrypted_local_credentials(api):
    first = _configure(api["client"], api["admin_headers"])

    assert first.status_code == 200
    body = first.get_json()
    assert body["configured"] is True
    assert body["state"] == "pending_test"
    assert body["client_id_hint"] == "••••1234"
    assert body["credential_storage_provider"] == "local_encrypted_file"
    assert "client_secret" not in body
    assert "credentials_ref" not in body
    files = list(api["secret_dir"].rglob("*.enc"))
    assert len(files) == 1
    assert b"portal-client-1234" not in files[0].read_bytes()
    assert b"secret-value" not in files[0].read_bytes()

    second = _configure(
        api["client"],
        api["admin_headers"],
        client_id="rotated-client-9876",
        secret="rotated-secret",
    )

    assert second.status_code == 200
    assert second.get_json()["client_id_hint"] == "••••9876"
    assert len(list(api["secret_dir"].rglob("*.enc"))) == 1
    with api["app"].app_context():
        rows = ExternalProviderConnection.query.all()
        assert len(rows) == 1
        assert rows[0].organization_id is not None
        assert rows[0].importer_id is None
        assert rows[0].credentials_ref.startswith("local:")


def test_configuration_is_admin_only_and_tenant_scoped(api):
    forbidden = _configure(
        api["client"],
        api["operator_headers"],
    )
    assert forbidden.status_code == 403

    configured = _configure(api["client"], api["admin_headers"])
    assert configured.status_code == 200

    other_tenant = api["client"].get(
        "/organizations/me/integrations/portal-unico",
        headers=api["other_admin_headers"],
    )
    assert other_tenant.status_code == 200
    assert other_tenant.get_json()["configured"] is False


def test_healthcheck_marks_connection_ready_without_exposing_credentials(api):
    captured = {}

    def gateway_factory(credentials, environment, config):
        captured["client_id"] = credentials.client_id
        captured["client_secret"] = credentials.client_secret
        captured["environment"] = environment
        captured["role_type"] = credentials.role_type
        return SuccessfulGateway()

    api["app"].config["PORTAL_UNICO_GATEWAY_FACTORY"] = gateway_factory
    configured = _configure(api["client"], api["admin_headers"])
    assert configured.status_code == 200

    tested = api["client"].post(
        "/organizations/me/integrations/portal-unico/test",
        headers=api["admin_headers"],
    )

    assert tested.status_code == 200
    body = tested.get_json()
    assert body["state"] == "connected"
    assert body["ready_for_duimp"] is True
    assert body["last_healthcheck_at"]
    assert body["last_error"] is None
    assert captured == {
        "client_id": "portal-client-1234",
        "client_secret": "secret-value",
        "environment": "production",
        "role_type": "IMPEXP",
    }
    assert "secret-value" not in tested.get_data(as_text=True)


def test_failed_healthcheck_persists_only_safe_error(api):
    def gateway_factory(credentials, environment, config):
        del credentials, environment, config

        class FailingGateway:
            def healthcheck(self):
                raise PortalUnicoApiError(
                    "provider echoed secret-value",
                    status_code=401,
                )

        return FailingGateway()

    api["app"].config["PORTAL_UNICO_GATEWAY_FACTORY"] = gateway_factory
    configured = _configure(api["client"], api["admin_headers"])
    assert configured.status_code == 200

    tested = api["client"].post(
        "/organizations/me/integrations/portal-unico/test",
        headers=api["admin_headers"],
    )

    assert tested.status_code == 422
    body = tested.get_json()
    assert body["state"] == "error"
    assert body["ready_for_duimp"] is False
    assert body["message"] == (
        "O Portal Único rejeitou o Client-Id ou o Client-Secret."
    )
    assert "secret-value" not in tested.get_data(as_text=True)


def test_healthcheck_requires_configuration(api):
    response = api["client"].post(
        "/organizations/me/integrations/portal-unico/test",
        headers=api["admin_headers"],
    )

    assert response.status_code == 409
    assert "Configure o Client-Id" in response.get_json()["message"]
