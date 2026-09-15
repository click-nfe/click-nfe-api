import pytest

from app import create_app
from app.extensions import db
from app.models import Organization, User


class DevelopmentConfig:
    TESTING = True
    APP_ENV = "development"
    SECRET_KEY = "dev-cli-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


class ProductionConfig(DevelopmentConfig):
    APP_ENV = "production"


@pytest.fixture
def dev_app():
    app = create_app(DevelopmentConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _seed_args(password: str = "senha-local-segura") -> list[str]:
    return [
        "dev",
        "seed-admin",
        "--organization-name",
        "Click NFe Demo",
        "--organization-slug",
        "click-nfe-demo",
        "--name",
        "Administrador Local",
        "--email",
        "ADMIN@CLICKNFE.LOCAL",
        "--password",
        password,
    ]


def test_seed_admin_creates_organization_and_login_user(dev_app):
    result = dev_app.test_cli_runner().invoke(args=_seed_args())

    assert result.exit_code == 0, result.output
    organization = Organization.query.filter_by(slug="click-nfe-demo").one()
    user = User.query.filter_by(email="admin@clicknfe.local").one()
    assert user.organization_id == organization.id
    assert user.role == "admin"
    assert user.ativo is True
    assert user.check_password("senha-local-segura")
    assert "senha não exibida" in result.output


def test_seed_admin_is_idempotent_and_can_rotate_local_password(dev_app):
    runner = dev_app.test_cli_runner()
    first = runner.invoke(args=_seed_args())
    second = runner.invoke(args=_seed_args("nova-senha-local"))

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert Organization.query.count() == 1
    assert User.query.count() == 1
    user = User.query.one()
    assert user.check_password("nova-senha-local")
    assert "atualizada" in second.output
    assert "atualizado" in second.output


def test_seeded_admin_completes_login_refresh_and_logout(dev_app):
    seed_result = dev_app.test_cli_runner().invoke(args=_seed_args())
    assert seed_result.exit_code == 0, seed_result.output

    client = dev_app.test_client()
    login_response = client.post(
        "/auth/login",
        json={
            "email": "ADMIN@CLICKNFE.LOCAL",
            "password": "senha-local-segura",
        },
    )
    assert login_response.status_code == 200
    login_body = login_response.get_json()
    assert login_body["user"]["organizationId"]

    access_token = login_body["tokens"]["accessToken"]
    refresh_token = login_body["tokens"]["refreshToken"]
    me_response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_response.status_code == 200
    assert me_response.get_json()["email"] == "admin@clicknfe.local"

    refresh_response = client.post(
        "/auth/refresh",
        json={"refreshToken": refresh_token},
    )
    assert refresh_response.status_code == 200
    refreshed_tokens = refresh_response.get_json()["tokens"]
    assert refreshed_tokens["refreshToken"] != refresh_token

    logout_response = client.post(
        "/auth/logout",
        headers={
            "Authorization": f"Bearer {refreshed_tokens['accessToken']}"
        },
    )
    assert logout_response.status_code == 204

    revoked_response = client.post(
        "/auth/refresh",
        json={"refreshToken": refreshed_tokens["refreshToken"]},
    )
    assert revoked_response.status_code == 401


def test_seed_admin_refuses_non_development_environment():
    app = create_app(ProductionConfig)
    with app.app_context():
        db.create_all()
        result = app.test_cli_runner().invoke(args=_seed_args())

        assert result.exit_code != 0
        assert "somente com APP_ENV=development" in result.output
        assert Organization.query.count() == 0
        assert User.query.count() == 0

        db.session.remove()
        db.drop_all()
