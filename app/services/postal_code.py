from __future__ import annotations

from datetime import datetime, timedelta
import unicodedata

from app.extensions import db
from app.integrations.postal_code import (
    PostalCodeNotFoundError,
    PostalCodeProvider,
    PostalCodeProviderError,
)
from app.models.fiscal_reference import FiscalMunicipality, PostalCodeCache


class PostalCodeUnavailableError(RuntimeError):
    """Nenhuma fonte conseguiu responder à consulta."""


def normalize_zip_code(value: str) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def _search_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip())
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).casefold()


class PostalCodeLookupService:
    def __init__(
        self,
        *,
        providers: list[PostalCodeProvider],
        cache_ttl_seconds: int = 2_592_000,
        now_factory=datetime.utcnow,
    ) -> None:
        self.providers = providers
        self.cache_ttl_seconds = max(0, cache_ttl_seconds)
        self.now_factory = now_factory

    def lookup(self, value: str) -> dict[str, object]:
        zip_code = normalize_zip_code(value)
        if len(zip_code) != 8:
            raise ValueError("CEP inválido. Informe 8 dígitos.")

        cached = db.session.get(PostalCodeCache, zip_code)
        if cached and self._fresh(cached):
            return self._serialize(cached, cache_hit=True, stale=False)

        not_found_count = 0
        for provider in self.providers:
            try:
                address = provider.lookup(zip_code)
                normalized = self._complete_address(address)
                row = self._save(zip_code, provider.name, normalized, cached)
                return self._serialize(row, cache_hit=False, stale=False)
            except PostalCodeNotFoundError:
                not_found_count += 1
            except PostalCodeProviderError:
                continue

        if cached:
            return self._serialize(cached, cache_hit=True, stale=True)
        if self.providers and not_found_count == len(self.providers):
            raise PostalCodeNotFoundError("CEP não encontrado.")
        raise PostalCodeUnavailableError(
            "A consulta de CEP está temporariamente indisponível."
        )

    def _fresh(self, row: PostalCodeCache) -> bool:
        return row.fetched_at >= self.now_factory() - timedelta(
            seconds=self.cache_ttl_seconds
        )

    def _complete_address(self, address: dict[str, str]) -> dict[str, str]:
        city_code = normalize_zip_code(address.get("city_code", ""))[:7]
        state = (address.get("state") or "").strip().upper()
        city_name = (address.get("city_name") or "").strip()
        if not city_code:
            municipality = self._municipality(city_name, state)
            if municipality:
                city_code = municipality.code
                city_name = municipality.name
                state = municipality.state
        if len(city_code) != 7 or not city_name or len(state) != 2:
            raise PostalCodeProviderError(
                "O provedor não retornou um município fiscal válido."
            )
        return {
            "street": (address.get("street") or "").strip(),
            "complement": (address.get("complement") or "").strip(),
            "district": (address.get("district") or "").strip(),
            "city_code": city_code,
            "city_name": city_name,
            "state": state,
            "country_code": "1058",
            "country_name": "Brasil",
        }

    @staticmethod
    def _municipality(city_name: str, state: str) -> FiscalMunicipality | None:
        if not city_name or len(state) != 2:
            return None
        candidates = FiscalMunicipality.query.filter(
            FiscalMunicipality.active.is_(True),
            FiscalMunicipality.state == state,
        ).all()
        normalized_name = _search_text(city_name)
        return next(
            (row for row in candidates if _search_text(row.name) == normalized_name),
            None,
        )

    def _save(
        self,
        zip_code: str,
        provider: str,
        address: dict[str, str],
        existing: PostalCodeCache | None,
    ) -> PostalCodeCache:
        row = existing or PostalCodeCache(zip_code=zip_code)
        for field, value in address.items():
            normalized_value = (
                value or None
                if field in {"street", "complement", "district"}
                else value
            )
            setattr(row, field, normalized_value)
        row.provider = provider
        row.fetched_at = self.now_factory()
        db.session.add(row)
        db.session.flush()
        return row

    @staticmethod
    def _serialize(
        row: PostalCodeCache,
        *,
        cache_hit: bool,
        stale: bool,
    ) -> dict[str, object]:
        return {
            "zip_code": row.zip_code,
            "street": row.street or "",
            "complement": row.complement or "",
            "district": row.district or "",
            "city_code": row.city_code,
            "city_name": row.city_name,
            "state": row.state,
            "country_code": row.country_code,
            "country_name": row.country_name,
            "provider": row.provider,
            "cache_hit": cache_hit,
            "stale": stale,
            "fetched_at": row.fetched_at.isoformat(),
        }
