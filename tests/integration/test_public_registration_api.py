import pytest

from app import create_app
from app.auth import ACCESS_TAGS
from app.extensions import db
from app.models import Organization, User


class SignupConfig:
    TESTING = True
    SECRET_KEY = "registration-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


@pytest.fixture
def client():
    app = create_app(SignupConfig)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def payload(**changes):
    return {
        "organization_name": "Grupo Casco",
        "organization_slug": "casco-group",
        "name": "Administrador Grupo Casco",
        "email": "CASCogroup@gmail.com",
        "password": "senha-inicial-com-mais-de-16-caracteres",
        **changes,
    }


def test_registration_creates_admin_and_org_atomically(client):
    response = client.post("/auth/register", json=payload())
    assert response.status_code == 201
    assert "password" not in response.get_json()
    organization = Organization.query.one()
    admin = User.query.one()
    assert organization.nome == "Grupo Casco"
    assert organization.email == "cascogroup@gmail.com"
    assert admin.organization_id == organization.id
    assert admin.role == "admin"
    assert admin.access_tags == list(ACCESS_TAGS)
    assert admin.check_password(payload()["password"])
    assert admin.password_hash != payload()["password"]


@pytest.mark.parametrize("changes", [
    {"organization_slug": "INVALIDO"},
    {"organization_slug": "nome com espaço"},
    {"email": "sem-email"},
    {"password": "curta"},
    {"name": " "},
    {"organization_name": " "},
    {"role": "admin"},
])
def test_registration_rejects_bad_payload_without_writing(client, changes):
    response = client.post("/auth/register", json=payload(**changes))
    assert response.status_code == 400
    assert Organization.query.count() == 0
    assert User.query.count() == 0


@pytest.mark.parametrize("changes", [
    {"organization_name": "grupo casco", "organization_slug": "outro", "email": "outro@example.com"},
    {"organization_name": "Outra", "organization_slug": "casco-group", "email": "outro@example.com"},
    {"organization_name": "Outra", "organization_slug": "outro", "email": "CASCogroup@gmail.com"},
])
def test_registration_rejects_existing_identity_without_partial_write(client, changes):
    assert client.post("/auth/register", json=payload()).status_code == 201
    response = client.post("/auth/register", json=payload(**changes))
    assert response.status_code == 409
    assert Organization.query.count() == 1
    assert User.query.count() == 1


def test_registration_allows_another_organization(client):
    assert client.post("/auth/register", json=payload()).status_code == 201
    response = client.post("/auth/register", json=payload(
        organization_name="Outra empresa", organization_slug="outra-empresa",
        email="outra@example.com",
    ))
    assert response.status_code == 201
    assert Organization.query.count() == 2
    assert User.query.count() == 2
