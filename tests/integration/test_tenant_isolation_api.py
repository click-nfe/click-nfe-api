from datetime import datetime, timedelta

import jwt
import pytest

from app import create_app
from app.extensions import db
from app.models import (
    Client,
    ImportProcess,
    ImportProcessStatus,
    Organization,
    RefreshToken,
    User,
)


class TestConfig:
    TESTING = True
    SECRET_KEY = "tenant-isolation-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


def _token(app, user: User, token_type: str = "access") -> str:
    now = datetime.utcnow()
    return jwt.encode(
        {
            "sub": str(user.id),
            "email": user.email,
            "role": user.role,
            "principal_type": "user",
            "type": token_type,
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        app.config["SECRET_KEY"],
        algorithm="HS256",
    )


def _user(organization, *, name: str, email: str, role: str = "admin") -> User:
    user = User(
        organization_id=organization.id,
        nome=name,
        email=email,
        role=role,
        ativo=True,
    )
    user.set_password("test-password")
    db.session.add(user)
    return user


@pytest.fixture
def tenant_api():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        organization_a = Organization(nome="Organização A", slug="org-a")
        organization_b = Organization(nome="Organização B", slug="org-b")
        inactive_organization = Organization(
            nome="Organização Inativa",
            slug="org-inativa",
            ativo=False,
        )
        db.session.add_all(
            [organization_a, organization_b, inactive_organization]
        )
        db.session.flush()

        admin_a = _user(
            organization_a,
            name="Admin A",
            email="admin-a@example.invalid",
        )
        member_a = _user(
            organization_a,
            name="Operador A",
            email="operador-a@example.invalid",
            role="operacao",
        )
        admin_b = _user(
            organization_b,
            name="Admin B",
            email="admin-b@example.invalid",
        )
        member_b = _user(
            organization_b,
            name="Operador B",
            email="operador-b@example.invalid",
            role="operacao",
        )
        inactive_admin = _user(
            inactive_organization,
            name="Admin Inativo",
            email="admin-inativo@example.invalid",
        )
        client_a = Client(
            organization_id=organization_a.id,
            cnpj="00000000000191",
            razao_social="Cliente da Organização A",
        )
        client_b = Client(
            organization_id=organization_b.id,
            cnpj="03114340000131",
            razao_social="Cliente da Organização B",
        )
        db.session.add_all([client_a, client_b])
        inactive_refresh_token = _token(
            app,
            inactive_admin,
            token_type="refresh",
        )
        db.session.add(
            RefreshToken(
                user_id=inactive_admin.id,
                token=inactive_refresh_token,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
        )
        db.session.commit()

        yield {
            "app": app,
            "client": app.test_client(),
            "organization_a": organization_a,
            "organization_b": organization_b,
            "admin_a": admin_a,
            "member_a": member_a,
            "admin_b": admin_b,
            "member_b": member_b,
            "inactive_admin": inactive_admin,
            "inactive_refresh_token": inactive_refresh_token,
            "client_a": client_a,
            "client_b": client_b,
            "headers_a": {
                "Authorization": f"Bearer {_token(app, admin_a)}"
            },
            "headers_inactive": {
                "Authorization": (
                    f"Bearer {_token(app, inactive_admin)}"
                )
            },
        }

        db.session.remove()
        db.drop_all()


def test_public_registration_is_not_available(tenant_api):
    response = tenant_api["client"].post(
        "/auth/register",
        json={
            "nome": "Novo Administrador",
            "email": "novo-admin@example.invalid",
            "password": "senha-segura",
            "organization_nome": "Nova Organização",
            "organization_slug": "nova-organizacao",
        },
    )

    assert response.status_code == 404
    assert Organization.query.filter_by(slug="nova-organizacao").first() is None
    assert User.query.filter_by(email="novo-admin@example.invalid").first() is None


def test_user_lists_only_include_current_organization(tenant_api):
    response = tenant_api["client"].get(
        "/users",
        headers=tenant_api["headers_a"],
    )

    assert response.status_code == 200
    returned_ids = {row["id"] for row in response.get_json()}
    assert returned_ids == {
        str(tenant_api["admin_a"].id),
        str(tenant_api["member_a"].id),
    }
    assert str(tenant_api["admin_b"].id) not in returned_ids


def test_admin_creates_additional_user_only_in_own_organization(tenant_api):
    response = tenant_api["client"].post(
        "/users",
        headers=tenant_api["headers_a"],
        json={
            "nome": "Novo Admin A",
            "email": "novo-admin-a@example.invalid",
            "password": "senha-segura",
            "role": "admin",
            "organization_id": str(tenant_api["organization_b"].id),
        },
    )

    assert response.status_code == 201
    user = User.query.filter_by(email="novo-admin-a@example.invalid").one()
    assert user.organization_id == tenant_api["organization_a"].id
    assert user.organization_id != tenant_api["organization_b"].id


@pytest.mark.parametrize("method", ["put", "delete"])
def test_admin_cannot_change_user_from_another_organization(
    tenant_api,
    method,
):
    request_method = getattr(tenant_api["client"], method)
    response = request_method(
        f'/users/user/{tenant_api["member_b"].id}',
        headers=tenant_api["headers_a"],
        json={
            "nome": "Nome Alterado",
            "email": "alterado@example.invalid",
            "ativo": False,
        },
    )

    assert response.status_code == 404
    db.session.refresh(tenant_api["member_b"])
    assert tenant_api["member_b"].nome == "Operador B"
    assert tenant_api["member_b"].ativo is True


def test_client_from_another_organization_is_not_found(tenant_api):
    response = tenant_api["client"].get(
        f'/clients/{tenant_api["client_b"].id}',
        headers=tenant_api["headers_a"],
    )

    assert response.status_code == 404


def test_dashboard_summary_counts_only_current_organization(tenant_api):
    organization_a = tenant_api["organization_a"]
    organization_b = tenant_api["organization_b"]
    client_a = tenant_api["client_a"]
    client_b = tenant_api["client_b"]
    admin_a = tenant_api["admin_a"]
    admin_b = tenant_api["admin_b"]
    now = datetime.utcnow()

    statuses_a = [
        ImportProcessStatus.CREATED,
        ImportProcessStatus.DRAFT_READY,
        ImportProcessStatus.XML_VALIDATION_FAILED,
        ImportProcessStatus.AUTHORIZED,
        ImportProcessStatus.CANCELLED,
    ]
    for index, status in enumerate(statuses_a, start=1):
        db.session.add(
            ImportProcess(
                organization_id=organization_a.id,
                importer_id=client_a.id,
                reference_code=f"ORG-A-{index}",
                status=status.value,
                source="manual",
                created_by_user_id=admin_a.id,
                created_at=now,
                updated_at=now,
            )
        )
    db.session.add(
        ImportProcess(
            organization_id=organization_b.id,
            importer_id=client_b.id,
            reference_code="ORG-B-1",
            status=ImportProcessStatus.DRAFT_READY.value,
            source="manual",
            created_by_user_id=admin_b.id,
            created_at=now,
            updated_at=now,
        )
    )
    db.session.commit()

    response = tenant_api["client"].get(
        "/import-processes/dashboard-summary",
        headers=tenant_api["headers_a"],
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["total"] == 5
    assert body["in_progress"] == 3
    assert body["ready_for_emission"] == 1
    assert body["attention_required"] == 1
    assert body["completed"] == 1
    assert body["by_status"]["draft_ready"] == 1
    assert body["by_status"]["authorized"] == 1


def test_inactive_organization_cannot_login_or_use_existing_token(tenant_api):
    login = tenant_api["client"].post(
        "/auth/login",
        json={
            "email": tenant_api["inactive_admin"].email,
            "password": "test-password",
        },
    )
    authenticated = tenant_api["client"].get(
        "/auth/me",
        headers=tenant_api["headers_inactive"],
    )
    refreshed = tenant_api["client"].post(
        "/auth/refresh",
        json={"refreshToken": tenant_api["inactive_refresh_token"]},
    )

    assert login.status_code == 401
    assert authenticated.status_code == 403
    assert authenticated.get_json()["error"] == "organization_inactive"
    assert refreshed.status_code == 401
