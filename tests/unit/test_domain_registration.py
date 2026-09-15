from app import create_app
from app.extensions import db


class TestConfig:
    TESTING = True
    SECRET_KEY = "domain-registration-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


def test_legacy_scope_and_preposto_tables_are_not_registered():
    create_app(TestConfig)

    legacy_tables = {
        "organization_settings",
        "scopes",
        "scope_versions",
        "scope_assignments",
        "service_catalog",
        "scope_services",
        "scope_template",
        "prepostos",
        "preposto_contatos",
        "preposto_localidades",
        "preposto_tarifas",
        "preposto_credenciados",
        "preposto_credenciado_vinculos",
        "scope_prepostos",
    }

    assert legacy_tables.isdisjoint(db.metadata.tables)
