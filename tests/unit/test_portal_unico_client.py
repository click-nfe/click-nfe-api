from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pytest
from cryptography.fernet import Fernet

from app.integrations.portal_unico import (
    DefaultPortalCredentialResolver,
    DuimpIdentifier,
    GcpSecretManagerPortalCredentialResolver,
    LocalEncryptedFilePortalCredentialStore,
    PortalUnicoIntegrationError,
    PortalUnicoCredentials,
    PortalUnicoDuimpGateway,
    PortalUnicoResponse,
)


class SecretPayload:
    def __init__(self, data: bytes) -> None:
        self.data = data


class SecretResponse:
    def __init__(self, data: bytes) -> None:
        self.payload = SecretPayload(data)


class FakeSecretManagerClient:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values
        self.requests: list[str] = []

    def access_secret_version(self, *, request: dict[str, str]) -> SecretResponse:
        name = request["name"]
        self.requests.append(name)
        return SecretResponse(self.values[name])


@dataclass
class RecordedRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    query: Mapping[str, Any] | None


class FakeTransport:
    def __init__(self, responses: list[PortalUnicoResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[RecordedRequest] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        query: Mapping[str, Any] | None = None,
        timeout_seconds: float = 30,
    ) -> PortalUnicoResponse:
        self.requests.append(
            RecordedRequest(method, url, dict(headers or {}), query)
        )
        return self.responses.pop(0)


def response(payload: Any, csrf: str | None = None, **headers: str):
    if csrf:
        headers["X-CSRF-Token"] = csrf
    return PortalUnicoResponse(200, headers, payload)


@pytest.mark.parametrize(
    ("raw", "compact", "formatted"),
    [
        ("26BR0000000000-1", "26BR00000000001", "26BR0000000000-1"),
        (" 26br00000000001 ", "26BR00000000001", "26BR0000000000-1"),
    ],
)
def test_duimp_identifier_accepts_operator_and_api_formats(raw, compact, formatted):
    identifier = DuimpIdentifier.parse(raw)
    assert identifier.compact == compact
    assert identifier.formatted == formatted


@pytest.mark.parametrize(
    "raw", ["", "26BR0001106747", "26BR0001106747-X", "2026BR00000000001"]
)
def test_duimp_identifier_rejects_invalid_values(raw):
    with pytest.raises(ValueError, match="Número da DUIMP inválido"):
        DuimpIdentifier.parse(raw)


def test_gcp_resolver_reads_separate_versioned_secrets():
    client = FakeSecretManagerClient(
        {
            "projects/project-test/secrets/PORTAL_UNICO_CLIENT_ID/versions/7": b"id-1\n",
            "projects/project-test/secrets/PORTAL_UNICO_CLIENT_SECRET/versions/7": (
                b"secret-1\n"
            ),
        }
    )
    resolver = GcpSecretManagerPortalCredentialResolver(
        client=client,
        project_id="project-test",
        secret_version="7",
    )

    credentials = resolver.resolve("gcp:PORTAL_UNICO", role_type="IMPEXP")

    assert credentials.client_id == "id-1"
    assert credentials.client_secret == "secret-1"
    assert client.requests == [
        "projects/project-test/secrets/PORTAL_UNICO_CLIENT_ID/versions/7",
        "projects/project-test/secrets/PORTAL_UNICO_CLIENT_SECRET/versions/7",
    ]


def test_gcp_resolver_does_not_expose_secret_value_on_failure():
    class FailingClient:
        def access_secret_version(self, *, request):
            raise RuntimeError("provider failure containing-sensitive-value")

    resolver = GcpSecretManagerPortalCredentialResolver(
        client=FailingClient(),
        project_id="project-test",
        secret_version="1",
    )

    with pytest.raises(PortalUnicoIntegrationError) as exc_info:
        resolver.resolve("gcp:PORTAL_UNICO", role_type="IMPEXP")

    assert "containing-sensitive-value" not in str(exc_info.value)
    assert "PORTAL_UNICO_CLIENT_ID" in str(exc_info.value)


def test_default_resolver_rejects_unknown_provider():
    resolver = DefaultPortalCredentialResolver()

    with pytest.raises(
        PortalUnicoIntegrationError,
        match="local:, env: ou gcp:",
    ):
        resolver.resolve("database:PORTAL_UNICO", role_type="IMPEXP")


def test_local_store_encrypts_resolves_and_deletes_credentials(tmp_path):
    store = LocalEncryptedFilePortalCredentialStore(
        root_dir=tmp_path,
        encryption_key=Fernet.generate_key(),
    )
    credentials = PortalUnicoCredentials(
        client_id="client-id-local",
        client_secret="client-secret-local",
    )

    reference = store.store(
        organization_id="7ec07ff4-d0f7-4f00-a8ad-279e1d1bbdcb",
        credentials=credentials,
    )

    assert reference.startswith(
        "local:7ec07ff4-d0f7-4f00-a8ad-279e1d1bbdcb/"
    )
    encrypted_files = list(tmp_path.rglob("*.enc"))
    assert len(encrypted_files) == 1
    encrypted = encrypted_files[0].read_bytes()
    assert b"client-id-local" not in encrypted
    assert b"client-secret-local" not in encrypted
    resolved = store.resolve(reference, role_type="IMPEXP")
    assert resolved == credentials

    store.delete(reference)

    assert list(tmp_path.rglob("*.enc")) == []


def test_local_store_rejects_invalid_or_cross_directory_reference(tmp_path):
    store = LocalEncryptedFilePortalCredentialStore(
        root_dir=tmp_path,
        encryption_key=Fernet.generate_key(),
    )

    with pytest.raises(
        PortalUnicoIntegrationError,
        match="referência local",
    ):
        store.resolve(
            "local:../../etc/passwd",
            role_type="IMPEXP",
        )


def test_gateway_authenticates_gets_current_version_and_paginates_items():
    pages = [
        [{"identificacao": {"numeroItem": number}} for number in range(1, 101)],
        [{"identificacao": {"numeroItem": number}} for number in range(101, 201)],
        [{"identificacao": {"numeroItem": number}} for number in range(201, 206)],
    ]
    transport = FakeTransport(
        [
            response({}, **{"Set-Token": "jwt-1", "X-CSRF-Token": "csrf-1"}),
            response({"versao": "1"}, csrf="csrf-2"),
            response({"quantidadeItens": 205}, csrf="csrf-3"),
            response(pages[0], csrf="csrf-4"),
            response(pages[1], csrf="csrf-5"),
            response(pages[2], csrf="csrf-6"),
        ]
    )
    gateway = PortalUnicoDuimpGateway(
        credentials=PortalUnicoCredentials("client-id", "client-secret"),
        environment="production",
        transport=transport,
    )

    result = gateway.fetch_duimp(
        duimp_number="26BR0000000000-1",
        enrich_catalog=False,
    )

    assert result["numero"] == "26BR0000000000-1"
    assert result["numeroApi"] == "26BR00000000001"
    assert result["versao"] == "1"
    assert len(result["itens"]) == 205

    auth = transport.requests[0]
    assert auth.url.endswith("/portal/api/autenticar/chave-acesso")
    assert auth.headers == {
        "Client-Id": "client-id",
        "Client-Secret": "client-secret",
        "Role-Type": "IMPEXP",
    }
    assert transport.requests[1].url.endswith(
        "/duimp-api/api/ext/duimp/26BR00000000001/versoes"
    )
    assert transport.requests[2].url.endswith(
        "/duimp-api/api/ext/duimp/26BR00000000001/1"
    )
    assert [request.query["inicial"] for request in transport.requests[3:]] == [
        1,
        101,
        201,
    ]
    assert [request.headers["X-CSRF-Token"] for request in transport.requests[1:]] == [
        "csrf-1",
        "csrf-2",
        "csrf-3",
        "csrf-4",
        "csrf-5",
    ]


def test_gateway_rejects_item_count_mismatch():
    transport = FakeTransport(
        [
            response({}, **{"Set-Token": "jwt", "X-CSRF-Token": "csrf"}),
            response({"versao": "1"}),
            response({"quantidadeItens": 2}),
            response([{"identificacao": {"numeroItem": 1}}]),
        ]
    )
    gateway = PortalUnicoDuimpGateway(
        credentials=PortalUnicoCredentials("id", "secret"),
        environment="homologation",
        transport=transport,
    )

    with pytest.raises(Exception, match="informa 2 itens, mas a API retornou 1"):
        gateway.fetch_duimp(
            duimp_number="26BR00000000001",
            enrich_catalog=False,
        )


def test_gateway_enriches_products_and_operators_from_catalog():
    item = {
        "identificacao": {"numeroItem": 1},
        "produto": {
            "codigo": "215",
            "versao": "1",
            "ncm": "87087090",
            "niResponsavel": "00000000",
        },
        "exportador": {
            "codigo": "OPE_TEST_1",
            "versao": "1",
            "niOperador": "00000000",
            "pais": {"codigo": "CN"},
        },
        "fabricante": {
            "codigo": "OPE_TEST_1",
            "versao": "1",
            "niOperador": "00000000",
            "pais": {"codigo": "CN"},
        },
    }
    transport = FakeTransport(
        [
            response({}, **{"Set-Token": "jwt", "X-CSRF-Token": "csrf-1"}),
            response({"versao": "1"}, csrf="csrf-2"),
            response(
                {
                    "quantidadeItens": 1,
                    "identificacao": {
                        "importador": {"ni": "00000000000191"},
                    },
                },
                csrf="csrf-3",
            ),
            response([item], csrf="csrf-4"),
            response(
                {
                    "denominacao": "Roda automotiva detalhada",
                    "codigosInterno": ["PROD-INT-001"],
                },
                csrf="csrf-5",
            ),
            response(
                {
                    "nome": "FOREIGN SUPPLIER TEST LTD",
                    "tin": "FOREIGN-TAX-ID-001",
                    "logradouro": "TEST STREET 100",
                    "nomeCidade": "TEST CITY",
                    "codigoPais": "CN",
                },
                csrf="csrf-6",
            ),
        ]
    )
    gateway = PortalUnicoDuimpGateway(
        credentials=PortalUnicoCredentials("id", "secret"),
        environment="production",
        transport=transport,
    )

    result = gateway.fetch_duimp(duimp_number="26BR0000000000-1")

    enriched = result["itens"][0]
    assert enriched["produto"]["denominacao"] == "Roda automotiva detalhada"
    assert (
        enriched["produto"]["codigoInternoNfe"]
        == "PROD-INT-001"
    )
    assert enriched["exportador"]["nome"] == (
        "FOREIGN SUPPLIER TEST LTD"
    )
    assert enriched["exportador"]["endereco"]["city_name"] == "TEST CITY"
    assert enriched["exportador"]["pais"]["codigo"] == "CN"
    assert enriched["fabricante"]["nome"] == (
        "FOREIGN SUPPLIER TEST LTD"
    )
    assert result["catalogEnrichment"] == {
        "products_requested": 1,
        "products_enriched": 1,
        "operators_requested": 1,
        "operators_enriched": 1,
        "failures": [],
    }
    assert transport.requests[4].url.endswith(
        "/catp/api/ext/produto/00000000/215/1"
    )
    assert transport.requests[5].url.endswith(
        "/catp/api/ext/operador-estrangeiro/00000000/CN/OPE_TEST_1/1"
    )


def test_gateway_reads_cct_pcce_and_tabx_without_mutating_operations():
    transport = FakeTransport(
        [
            response({}, **{"Set-Token": "jwt", "X-CSRF-Token": "csrf-1"}),
            response([{"identificacao": "RUC-1", "tipo": "AWB"}], csrf="csrf-2"),
            response(
                {"numeroDuimp": "26BR00000000001", "ufFavorecida": "PR"},
                csrf="csrf-3",
            ),
            response(
                {
                    "nomeTabela": "UNIDADE_ADUANEIRA",
                    "dados": [{"campos": [{"nome": "CODIGO", "valor": "0927800"}]}],
                },
                csrf="csrf-4",
            ),
        ]
    )
    gateway = PortalUnicoDuimpGateway(
        credentials=PortalUnicoCredentials("id", "secret"),
        environment="production",
        transport=transport,
    )

    cct = gateway.fetch_cargo_knowledge(knowledge_number="RUC-1")
    pcce = gateway.fetch_icms_declaration(duimp_number="26BR0000000000-1")
    tabx = gateway.fetch_comex_table(
        table_name="UNIDADE_ADUANEIRA",
        filters=[
            {
                "nomeTabela": "UNIDADE_ADUANEIRA",
                "nome": "CODIGO",
                "valores": ["0927800"],
            }
        ],
    )

    assert cct[0]["tipo"] == "AWB"
    assert pcce["ufFavorecida"] == "PR"
    assert tabx["nomeTabela"] == "UNIDADE_ADUANEIRA"
    assert transport.requests[1].method == "GET"
    assert transport.requests[1].url.endswith("/ccta/api/ext/conhecimentos")
    assert transport.requests[1].query == {"numeroConhecimento": "RUC-1"}
    assert transport.requests[2].url.endswith(
        "/pcce/api/ext/priv/icms/26BR00000000001"
    )
    assert transport.requests[3].url.endswith(
        "/tabx/api/ext/tabela/UNIDADE_ADUANEIRA"
    )
    assert transport.requests[3].query["nivel"] == "0"
    assert '"valores":["0927800"]' in transport.requests[3].query["filtros"]
