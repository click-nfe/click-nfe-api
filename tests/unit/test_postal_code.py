from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.integrations.postal_code import (
    BrasilApiCepProvider,
    HttpResponse,
    PostalCodeNotFoundError,
    PostalCodeProviderError,
    ViaCepProvider,
)
from app.models import FiscalMunicipality, PostalCodeCache
from app.services.postal_code import PostalCodeLookupService


class TestConfig:
    TESTING = True
    SECRET_KEY = "postal-code-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False


@dataclass
class FakeTransport:
    response: HttpResponse

    def __post_init__(self):
        self.requests: list[tuple[str, float]] = []

    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        self.requests.append((url, timeout_seconds))
        return self.response


class FakeProvider:
    name = "fake"

    def __init__(self, result=None, error=None, name="fake"):
        self.result = result
        self.error = error
        self.name = name
        self.calls = 0

    def lookup(self, zip_code: str):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        db.session.add(
            FiscalMunicipality(
                code="4106902",
                name="Curitiba",
                state="PR",
                active=True,
                updated_at=datetime.utcnow(),
            )
        )
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


def test_viacep_normalizes_address_and_ibge_code():
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "cep": "80050-530",
                "logradouro": "Rua XV de Novembro",
                "complemento": "",
                "bairro": "Centro",
                "localidade": "Curitiba",
                "uf": "PR",
                "ibge": "4106902",
            },
        )
    )
    provider = ViaCepProvider(
        base_url="https://example.invalid",
        timeout_seconds=3,
        transport=transport,
    )

    result = provider.lookup("80050530")

    assert transport.requests == [
        ("https://example.invalid/ws/80050530/json/", 3)
    ]
    assert result["zip_code"] == "80050530"
    assert result["city_code"] == "4106902"
    assert result["city_name"] == "Curitiba"


def test_brasil_api_normalizes_address_without_inventing_city_code():
    transport = FakeTransport(
        HttpResponse(
            200,
            {
                "cep": "80050530",
                "state": "PR",
                "city": "Curitiba",
                "neighborhood": "Centro",
                "street": "Rua XV de Novembro",
            },
        )
    )
    provider = BrasilApiCepProvider(transport=transport)

    result = provider.lookup("80050530")

    assert result["city_code"] == ""
    assert result["city_name"] == "Curitiba"


def test_lookup_uses_provider_and_local_municipality_then_caches(app):
    provider = FakeProvider(
        result={
            "street": "Rua XV de Novembro",
            "complement": "",
            "district": "Centro",
            "city_code": "",
            "city_name": "Curitiba",
            "state": "PR",
        }
    )
    service = PostalCodeLookupService(providers=[provider])

    first = service.lookup("80050-530")
    second = service.lookup("80050530")

    assert first["city_code"] == "4106902"
    assert first["country_code"] == "1058"
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert provider.calls == 1


def test_lookup_uses_second_provider_when_first_is_unavailable(app):
    primary = FakeProvider(error=PostalCodeProviderError("offline"))
    fallback = FakeProvider(
        result={
            "street": "Rua XV de Novembro",
            "complement": "",
            "district": "Centro",
            "city_code": "",
            "city_name": "Curitiba",
            "state": "PR",
        },
        name="brasil_api",
    )
    service = PostalCodeLookupService(providers=[primary, fallback])

    result = service.lookup("80050530")

    assert result["provider"] == "brasil_api"
    assert result["city_code"] == "4106902"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_lookup_returns_stale_cache_when_providers_are_unavailable(app):
    stale_time = datetime.utcnow() - timedelta(days=60)
    db.session.add(
        PostalCodeCache(
            zip_code="80050530",
            street="Rua XV de Novembro",
            district="Centro",
            city_code="4106902",
            city_name="Curitiba",
            state="PR",
            country_code="1058",
            country_name="Brasil",
            provider="viacep",
            fetched_at=stale_time,
            updated_at=stale_time,
        )
    )
    db.session.commit()
    provider = FakeProvider(error=PostalCodeProviderError("offline"))
    service = PostalCodeLookupService(
        providers=[provider],
        cache_ttl_seconds=1,
    )

    result = service.lookup("80050530")

    assert result["cache_hit"] is True
    assert result["stale"] is True
    assert result["city_code"] == "4106902"


def test_lookup_does_not_return_stale_cache_when_cep_is_not_found(app):
    stale_time = datetime.utcnow() - timedelta(days=60)
    db.session.add(
        PostalCodeCache(
            zip_code="80050530",
            street="Rua desatualizada",
            district="Centro",
            city_code="4106902",
            city_name="Curitiba",
            state="PR",
            country_code="1058",
            country_name="Brasil",
            provider="viacep",
            fetched_at=stale_time,
            updated_at=stale_time,
        )
    )
    db.session.commit()
    providers = [
        FakeProvider(error=PostalCodeNotFoundError("not found")),
        FakeProvider(error=PostalCodeNotFoundError("not found")),
    ]
    service = PostalCodeLookupService(
        providers=providers,
        cache_ttl_seconds=1,
    )

    with pytest.raises(PostalCodeNotFoundError):
        service.lookup("80050530")
