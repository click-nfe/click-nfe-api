"""The country snapshot must load through the real Alembic chain."""

from datetime import datetime

from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models import FiscalCountry
from app.services.fiscal_reference import FiscalReferenceService


def test_country_migration_seeds_and_preserves_existing_metadata(tmp_path):
    class Config:
        TESTING = True
        SECRET_KEY = "country-catalog-test"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'catalog.db'}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    app = create_app(Config)
    with app.app_context():
        upgrade(revision="c8f4d1e7a2b9")
        db.session.add(FiscalCountry(
            bacen_code="1600", name="China antiga", iso_alpha_2="CN",
            iso_alpha_3="CHN", active=False, updated_at=datetime.utcnow(),
        ))
        db.session.commit()

        upgrade(revision="d3c9f7a21e84")
        assert db.session.query(FiscalCountry).count() == 253
        assert db.session.get(FiscalCountry, "1058").name == "BRASIL"
        china = db.session.get(FiscalCountry, "1600")
        assert (china.name, china.active, china.iso_alpha_2, china.iso_alpha_3) == (
            "CHINA, REPUBLICA POPULAR", True, "CN", "CHN",
        )
        assert [row.bacen_code for row in FiscalReferenceService.search_countries(
            query="china", active_on=datetime.utcnow().date(),
        )] == ["1600"]
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            "d3c9f7a21e84"
        )

        # A data-only downgrade leaves pre-existing and referenced rows intact.
        downgrade(revision="c8f4d1e7a2b9")
        upgrade(revision="d3c9f7a21e84")
        assert db.session.query(FiscalCountry).count() == 253
        assert db.session.get(FiscalCountry, "1600").iso_alpha_2 == "CN"
