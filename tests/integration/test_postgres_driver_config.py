import pytest

from app import create_app
from app.extensions import db


@pytest.mark.parametrize(
    "database_uri",
    [
        "postgresql://postgres:password@localhost:5432/postgres",
        "postgresql+psycopg://postgres:password@localhost:5432/postgres",
    ],
)
def test_postgres_driver_is_available_for_cloud_run(database_uri):
    class TestConfig:
        TESTING = True
        APP_ENV = "testing"
        SECRET_KEY = "test-secret"
        SQLALCHEMY_DATABASE_URI = database_uri
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    app = create_app(TestConfig)

    with app.app_context():
        assert db.engine.dialect.name == "postgresql"
