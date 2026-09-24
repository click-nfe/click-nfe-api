from __future__ import annotations

from datetime import date, datetime
import unicodedata
from typing import Any, Mapping

from app.extensions import db
from app.models.fiscal_reference import (
    FiscalCountry,
    FiscalCustomsUnit,
    FiscalMunicipality,
)


def _search_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip())
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


class FiscalReferenceService:
    """Consulta catálogos fiscais sem depender de extensões do PostgreSQL."""

    @staticmethod
    def search_municipalities(
        *,
        query: str = "",
        state: str | None = None,
        limit: int = 20,
    ) -> list[FiscalMunicipality]:
        rows_query = FiscalMunicipality.query.filter(
            FiscalMunicipality.active.is_(True)
        )
        normalized_state = (state or "").strip().upper()
        if normalized_state:
            rows_query = rows_query.filter(
                FiscalMunicipality.state == normalized_state
            )

        normalized_query = _search_text(query)
        rows = rows_query.order_by(
            FiscalMunicipality.name.asc(),
            FiscalMunicipality.state.asc(),
        ).all()
        if normalized_query:
            rows = [
                row
                for row in rows
                if normalized_query in _search_text(row.name)
                or normalized_query in row.code
            ]
        return rows[:limit]

    @staticmethod
    def search_countries(
        *,
        query: str = "",
        active_on: date,
        limit: int = 20,
    ) -> list[FiscalCountry]:
        rows = (
            FiscalCountry.query.filter(FiscalCountry.active.is_(True))
            .order_by(FiscalCountry.name.asc())
            .all()
        )
        normalized_query = _search_text(query)
        eligible = []
        for row in rows:
            if row.valid_from and row.valid_from > active_on:
                continue
            if row.valid_until and row.valid_until < active_on:
                continue
            if normalized_query and not any(
                normalized_query in value
                for value in (
                    _search_text(row.name),
                    _search_text(row.bacen_code),
                    _search_text(row.iso_alpha_2),
                    _search_text(row.iso_alpha_3),
                )
            ):
                continue
            eligible.append(row)
        return eligible[:limit]

    @staticmethod
    def find_country(
        *,
        iso_alpha_2: str | None = None,
        bacen_code: str | None = None,
        name: str | None = None,
        active_on: date | None = None,
    ) -> FiscalCountry | None:
        reference_date = active_on or date.today()
        rows = FiscalCountry.query.filter(FiscalCountry.active.is_(True)).all()
        normalized_iso = (iso_alpha_2 or "").strip().upper()
        normalized_code = (bacen_code or "").strip().zfill(4)
        normalized_name = _search_text(name)
        for row in rows:
            if row.valid_from and row.valid_from > reference_date:
                continue
            if row.valid_until and row.valid_until < reference_date:
                continue
            if normalized_iso and (row.iso_alpha_2 or "").upper() == normalized_iso:
                return row
            if normalized_code and row.bacen_code == normalized_code:
                return row
            if normalized_name and _search_text(row.name) == normalized_name:
                return row
        return None

    @staticmethod
    def find_customs_unit(code: str | None) -> FiscalCustomsUnit | None:
        normalized = str(code or "").strip()
        if not normalized:
            return None
        return FiscalCustomsUnit.query.filter(
            FiscalCustomsUnit.code == normalized,
            FiscalCustomsUnit.active.is_(True),
        ).first()

    @classmethod
    def cache_customs_unit_from_tabx(
        cls,
        *,
        code: str,
        payload: Any,
        code_field: str = "CODIGO",
        description_field: str = "NOME",
        state_field: str = "UF",
        municipality_field: str = "CODIGO_MUNICIPIO",
    ) -> FiscalCustomsUnit | None:
        row = cls._first_tabx_row(payload)
        if not row:
            return None
        resolved_code = str(cls._tabx_value(row, code_field) or code or "").strip()
        description = str(cls._tabx_value(row, description_field) or "").strip()
        state = str(cls._tabx_value(row, state_field) or "").strip().upper()
        municipality_code = str(
            cls._tabx_value(row, municipality_field) or ""
        ).strip() or None
        if not resolved_code or not description or len(state) != 2:
            return None

        item = db.session.get(FiscalCustomsUnit, resolved_code)
        if item is None:
            item = FiscalCustomsUnit(code=resolved_code)
            db.session.add(item)
        item.description = description
        item.state = state
        item.municipality_code = municipality_code
        item.active = True
        item.source = "portal_unico_tabx"
        item.fetched_at = datetime.utcnow()
        db.session.flush()
        return item

    @classmethod
    def cache_country_from_tabx(
        cls,
        *,
        iso_alpha_2: str,
        payload: Any,
        code_field: str = "CODIGO",
        name_field: str = "NOME",
        iso_field: str = "SIGLA_ISO2",
    ) -> FiscalCountry | None:
        row = cls._first_tabx_row(payload)
        if not row:
            return None
        code = str(cls._tabx_value(row, code_field) or "").strip().zfill(4)
        name = str(cls._tabx_value(row, name_field) or "").strip()
        iso2 = str(
            cls._tabx_value(row, iso_field) or iso_alpha_2 or ""
        ).strip().upper()
        if len(code) != 4 or not code.isdigit() or not name or len(iso2) != 2:
            return None

        item = db.session.get(FiscalCountry, code)
        if item is None:
            item = FiscalCountry(bacen_code=code)
            db.session.add(item)
        item.name = name
        item.iso_alpha_2 = iso2
        item.active = True
        item.updated_at = datetime.utcnow()
        db.session.flush()
        return item

    @staticmethod
    def _first_tabx_row(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, Mapping):
            return None
        rows = payload.get("dados")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], Mapping):
            return None
        row = rows[0]
        fields = row.get("campos")
        if not isinstance(fields, list):
            return dict(row)
        return {
            str(field["nome"]).upper(): field.get("valor")
            for field in fields
            if isinstance(field, Mapping) and field.get("nome")
        }

    @staticmethod
    def _tabx_value(row: Mapping[str, Any], field_name: str) -> Any:
        wanted = str(field_name or "").upper()
        return next(
            (value for key, value in row.items() if str(key).upper() == wanted),
            None,
        )
