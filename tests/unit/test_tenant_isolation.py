from types import SimpleNamespace

import pytest

from app import create_app
from app.services.fiscal_certificate import FiscalCertificateError
from app.services.fiscal_certificate_registry import FiscalCertificateRegistry
from app.services.import_process import ImportNfeService


class TestConfig:
    TESTING = True
    SECRET_KEY = "tenant-unit-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_EXPIRES_SECONDS = 3600
    JWT_REFRESH_EXPIRES_SECONDS = 604800


def test_import_queries_fail_closed_without_organization():
    app = create_app(TestConfig)
    current_user = SimpleNamespace(organization_id=None, id=None)

    with app.app_context(), pytest.raises(
        ValueError,
        match="não possui organization_id",
    ):
        ImportNfeService(
            current_user=current_user
        ).import_process_query_for_current_user()


def test_certificate_queries_fail_closed_without_organization():
    app = create_app(TestConfig)
    current_user = SimpleNamespace(organization_id=None, id=None)

    with app.app_context(), pytest.raises(
        FiscalCertificateError,
        match="vinculado a uma organização",
    ):
        FiscalCertificateRegistry(current_user=current_user)._query()
