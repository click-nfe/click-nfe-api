import pytest

from app import create_app
from app.auth import ACCESS_TAGS
from app.extensions import db
from app.models import Organization, User


class ProductionConfig:
    TESTING = True
    APP_ENV = "production"
    SECRET_KEY = "bootstrap-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


@pytest.fixture
def app():
    app = create_app(ProductionConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def bootstrap(app, password="uma-senha-forte-com-20-caracteres"):
    return app.test_cli_runner().invoke(
        args=[
            "bootstrap-admin",
            "--organization-name", "Click NFe",
            "--organization-slug", "click-nfe",
            "--name", "Admin Inicial",
            "--email", "ADMIN@EXAMPLE.COM",
        ],
        env={"BOOTSTRAP_ADMIN_PASSWORD": password},
    )


def test_bootstrap_creates_first_admin_and_can_login(app):
    result = bootstrap(app)

    assert result.exit_code == 0, result.output
    organization = Organization.query.one()
    user = User.query.one()
    assert user.organization_id == organization.id
    assert user.email == "admin@example.com"
    assert user.role == "admin"
    assert user.access_tags == list(ACCESS_TAGS)
    assert user.check_password("uma-senha-forte-com-20-caracteres")
    assert "uma-senha-forte-com-20-caracteres" not in result.output
    response = app.test_client().post(
        "/auth/login",
        json={"email": user.email, "password": "uma-senha-forte-com-20-caracteres"},
    )
    assert response.status_code == 200


def test_bootstrap_refuses_second_run_without_changing_password(app):
    assert bootstrap(app).exit_code == 0
    second = bootstrap(app, password="outra-senha-forte-para-teste")

    assert second.exit_code != 0
    assert "Bootstrap recusado" in second.output
    assert Organization.query.count() == 1
    assert User.query.one().check_password("uma-senha-forte-com-20-caracteres")


def test_bootstrap_refuses_existing_organization_even_without_user(app):
    db.session.add(Organization(nome="Existente", slug="existente", ativo=True))
    db.session.commit()

    result = bootstrap(app)

    assert result.exit_code != 0
    assert User.query.count() == 0


def test_bootstrap_refuses_short_password(app):
    result = bootstrap(app, password="12345678")

    assert result.exit_code != 0
    assert Organization.query.count() == 0


def test_bootstrap_refuses_development():
    class DevelopmentConfig(ProductionConfig):
        APP_ENV = "development"

    app = create_app(DevelopmentConfig)
    result = bootstrap(app)

    assert result.exit_code != 0
    assert "somente em produção" in result.output


def test_bootstrap_runs_with_cli_app_context():
    app = create_app(ProductionConfig)
    with app.app_context():
        db.create_all()

    result = bootstrap(app)

    assert result.exit_code == 0, result.output
    with app.app_context():
        assert User.query.count() == 1
        db.session.remove()
        db.drop_all()
