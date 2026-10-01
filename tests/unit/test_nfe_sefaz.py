import pytest

from app.services.nfe_sefaz import NS, SefazClient, SefazTransportError, parse_reply
from tests.helpers import certificate_material
from lxml import etree


KEY = "41" + "1" * 42


def reply(code, protocol=""):
    return (f'<retConsReciNFe xmlns="{NS}"><tpAmb>1</tpAmb>'
            f'<cStat>{code}</cStat><xMotivo>Resultado</xMotivo>{protocol}'
            '</retConsReciNFe>').encode()


def test_receipt_requires_matching_key_and_protocol_for_authorization():
    protocol = (f'<protNFe><infProt><tpAmb>1</tpAmb><chNFe>{KEY}</chNFe>'
                '<nProt>141260000000001</nProt><cStat>100</cStat>'
                '<xMotivo>Autorizado</xMotivo></infProt></protNFe>')
    parsed = parse_reply(reply("104", protocol), KEY, kind="receipt")
    assert parsed.code == "100" and parsed.protocol == "141260000000001"
    with pytest.raises(SefazTransportError):
        parse_reply(reply("104", protocol.replace(KEY, "42" + "1" * 42)), KEY, kind="receipt")
    with pytest.raises(SefazTransportError):
        parse_reply(reply("100"), KEY, kind="receipt")


def test_receipt_pending_and_https_endpoints():
    assert parse_reply(reply("105"), KEY, kind="receipt").code == "105"
    client = SefazClient({"41": {"authorization": "http://insecure.example"}})
    with pytest.raises(ValueError):
        client.endpoint("41", "authorization")


def test_soap_request_uses_signed_xml_and_tls_without_network():
    class Session:
        def post(self, url, *, data, headers, cert, timeout, allow_redirects):
            assert url == "https://sefaz.example.invalid/authorize"
            assert timeout == 5 and allow_redirects is False
            assert "nfeAutorizacaoLote" in headers["Content-Type"]
            assert b"<indSinc>0</indSinc>" in data and KEY.encode() in data
            assert etree.fromstring(data).find(f".//{{{NS}}}NFe") is not None
            assert all(__import__("pathlib").Path(path).exists() for path in cert)
            return type("Response", (), {"status_code": 200, "content": (
                f'<retEnviNFe xmlns="{NS}"><tpAmb>1</tpAmb><cStat>103</cStat>'
                '<xMotivo>Lote recebido</xMotivo><infRec><nRec>141000000001234</nRec>'
                '</infRec></retEnviNFe>').encode()})()

    signed = (f'<NFe xmlns="{NS}"><infNFe Id="NFe{KEY}"><ide>'
              '<cUF>41</cUF><tpAmb>1</tpAmb></ide></infNFe></NFe>')
    client = SefazClient({"41": {"authorization": "https://sefaz.example.invalid/authorize"}},
                         session=Session(), timeout=5)
    result = client.authorize(signed, certificate_material("00000000000191"))
    assert result.code == "103" and result.receipt == "141000000001234"
