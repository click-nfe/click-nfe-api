from sqlalchemy.exc import SQLAlchemyError

from app import create_app
from app.extensions import db


class TestConfig:
    TESTING = True
    APP_ENV = "testing"
    SECRET_KEY = "health-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


def test_liveness_does_not_depend_on_database():
    app = create_app(TestConfig)
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_readiness_checks_database_connection():
    app = create_app(TestConfig)
    response = app.test_client().get("/health/ready")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "database": "ok"}


def test_readiness_returns_503_without_exposing_database_error(monkeypatch):
    app = create_app(TestConfig)

    with app.app_context():
        def unavailable_database(*_args, **_kwargs):
            raise SQLAlchemyError("sensitive connection details")

        monkeypatch.setattr(db.session, "execute", unavailable_database)
        response = app.test_client().get("/health/ready")

    assert response.status_code == 503
    assert response.get_json() == {
        "status": "unavailable",
        "database": "unavailable",
    }
    assert "sensitive" not in response.get_data(as_text=True)
