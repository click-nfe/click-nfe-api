from urllib.error import URLError

from app.integrations.portal_unico import availability
from app.integrations.portal_unico.client import (
    PortalUnicoServiceUnavailableError,
    UrllibJsonTransport,
)


class FakeResponse:
    status = 200
    headers = {"Content-Type": "text/html"}

    def __init__(self, url, body):
        self.url = url
        self.body = body

    def geturl(self):
        return self.url

    def read(self, *_args):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_maintenance_redirect_is_unavailable_and_not_a_credentials_error(monkeypatch):
    response = FakeResponse(
        "https://portalunico.siscomex.gov.br/portal/manutencao.html",
        b"<html>manutencao</html>",
    )
    monkeypatch.setattr(availability, "urlopen", lambda *_args, **_kwargs: response)
    monkeypatch.setattr("app.integrations.portal_unico.client.urlopen", lambda *_args, **_kwargs: response)

    assert availability._probe() == "unavailable"
    from pytest import raises
    with raises(PortalUnicoServiceUnavailableError):
        UrllibJsonTransport().request(
            "POST", "https://portalunico.siscomex.gov.br/portal/api/autenticar/chave-acesso"
        )


def test_network_error_does_not_claim_portal_is_offline(monkeypatch):
    def fail(*_args, **_kwargs):
        raise URLError("temporary local network error")

    monkeypatch.setattr(availability, "urlopen", fail)
    assert availability._probe() == "unknown"


def test_upstream_validation_is_available_but_not_valid_credentials(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    from pytest import raises
    from app.integrations.portal_unico import PortalUnicoApiError

    def reject(*_args, **_kwargs):
        raise HTTPError(
            availability.PROBE_URL, 422, "validation",
            {"Content-Type": "application/json"}, BytesIO(b'{"code":"PUCX-ER0102"}'),
        )

    monkeypatch.setattr(availability, "urlopen", reject)
    monkeypatch.setattr("app.integrations.portal_unico.client.urlopen", reject)

    assert availability._probe() == "available"
    with raises(PortalUnicoApiError) as failure:
        UrllibJsonTransport().request("POST", availability.PROBE_URL)
    assert failure.value.status_code == 422


def test_html_http_422_keeps_validation_classification(monkeypatch):
    from io import BytesIO
    from urllib.error import HTTPError
    from pytest import raises
    from app.integrations.portal_unico import PortalUnicoApiError

    def reject(*_args, **_kwargs):
        raise HTTPError(
            availability.PROBE_URL, 422, "validation",
            {"Content-Type": "text/html"}, BytesIO(b"<html>validation error</html>"),
        )

    monkeypatch.setattr("app.integrations.portal_unico.client.urlopen", reject)
    with raises(PortalUnicoApiError) as failure:
        UrllibJsonTransport().request("POST", availability.PROBE_URL)
    assert failure.value.status_code == 422
