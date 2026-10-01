"""Verify that the bundled authorizer snapshot is installed by Alembic in a test DB."""

from flask_migrate import upgrade

from app import create_app
from app.extensions import db
from app.models.sefaz_endpoint import SefazEndpoint


def test_sefaz_migration_loads_all_production_authorizers(tmp_path):
    class Config:
        TESTING = True
        SECRET_KEY = "sefaz-catalog-test"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'sefaz.db'}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    app = create_app(Config)
    with app.app_context():
        upgrade(revision="e62a7b9c104d")
        assert db.session.query(SefazEndpoint).count() == 27
        assert db.session.get(SefazEndpoint, "35").authorizer == "SP"
        assert db.session.get(SefazEndpoint, "23").authorizer == "SVRS"
        assert db.session.get(SefazEndpoint, "21").authorizer == "SVAN"
