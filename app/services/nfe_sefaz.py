"""SOAP 1.2 NF-e 4.00 transport. One signed NF-e is sent per asynchronous lot."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree

from .fiscal_certificate import CertificateMaterial, FiscalCertificateError

NS = "http://www.portalfiscal.inf.br/nfe"
SOAP = "http://www.w3.org/2003/05/soap-envelope"


class SefazConfigurationError(ValueError):
    pass


class SefazTransportError(Exception):
    """The request may have reached SEFAZ. Reconcile before any retransmission."""


@dataclass(frozen=True)
class SefazReply:
    code: str
    message: str
    receipt: str | None = None
    protocol: str | None = None
    access_key: str | None = None
    environment: str | None = None
    protocol_xml: str | None = None
    checksum: str = ""


def _xml(data: bytes | str):
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    return etree.fromstring(data.encode() if isinstance(data, str) else data, parser)


def signed_identity(signed_xml: str) -> tuple[str, str, str]:
    root = _xml(signed_xml)
    inf = root.find(f"{{{NS}}}infNFe")
    key = (inf.get("Id") or "").removeprefix("NFe") if inf is not None else ""
    state = root.findtext(f".//{{{NS}}}ide/{{{NS}}}cUF") or ""
    environment = root.findtext(f".//{{{NS}}}ide/{{{NS}}}tpAmb") or ""
    if not re.fullmatch(r"\d{44}", key) or not re.fullmatch(r"\d{2}", state):
        raise SefazConfigurationError("Chave ou UF ausente do XML assinado.")
    if environment != "1":
        raise SefazConfigurationError("A transmissão exige XML de produção (tpAmb=1).")
    return key, state, environment


def parse_reply(payload: bytes, expected_key: str, *, kind: str) -> SefazReply:
    try:
        root = _xml(payload)
        if root.find(f".//{{{SOAP}}}Fault") is not None:
            raise SefazTransportError("A SEFAZ retornou uma falha SOAP; consulte a chave.")
        tag = {"authorization": "retEnviNFe", "receipt": "retConsReciNFe", "protocol": "retConsSitNFe"}[kind]
        result = root.find(f".//{{{NS}}}{tag}")
        if result is None and root.tag == f"{{{NS}}}{tag}":
            result = root
        if result is None:
            raise SefazTransportError("Resposta SEFAZ sem o retorno esperado; consulte a chave.")
        code = result.findtext(f"{{{NS}}}cStat") or ""
        message = result.findtext(f"{{{NS}}}xMotivo") or ""
        environment = result.findtext(f"{{{NS}}}tpAmb")
        if environment != "1" or not code:
            raise SefazTransportError("Ambiente ou código SEFAZ inválido; consulte a chave.")
        receipt = result.findtext(f".//{{{NS}}}nRec")
        protocol = result.find(f".//{{{NS}}}protNFe")
        info = protocol.find(f"{{{NS}}}infProt") if protocol is not None else None
        if info is not None:
            code = info.findtext(f"{{{NS}}}cStat") or code
            message = info.findtext(f"{{{NS}}}xMotivo") or message
            key = info.findtext(f"{{{NS}}}chNFe")
            if key != expected_key:
                raise SefazTransportError("Chave do protocolo não corresponde ao XML enviado.")
            environment = info.findtext(f"{{{NS}}}tpAmb") or environment
            if environment != "1":
                raise SefazTransportError("Protocolo retornado em ambiente diferente.")
        else:
            key = None
        number = info.findtext(f"{{{NS}}}nProt") if info is not None else None
        if code in {"100", "150"} and (not number or protocol is None):
            raise SefazTransportError("Autorização sem protocolo verificável; consulte a chave.")
        return SefazReply(
            code=code, message=message, receipt=receipt, protocol=number,
            access_key=key, environment=environment,
            protocol_xml=etree.tostring(protocol, encoding="unicode") if protocol is not None else None,
            checksum=hashlib.sha256(payload).hexdigest(),
        )
    except (etree.XMLSyntaxError, ValueError, KeyError) as exc:
        raise SefazTransportError("Resposta SEFAZ ilegível; consulte a chave.") from exc


def authorized_xml(signed_xml: str, reply: SefazReply) -> str:
    if reply.code not in {"100", "150"} or not reply.protocol_xml:
        raise ValueError("Autorização sem protocolo.")
    root = etree.Element(f"{{{NS}}}nfeProc", nsmap={None: NS}, versao="4.00")
    root.append(_xml(signed_xml))
    root.append(_xml(reply.protocol_xml))
    return etree.tostring(root, encoding="UTF-8", xml_declaration=True).decode()


class SefazClient:
    def __init__(self, endpoints: dict, *, timeout: float = 20, session=None):
        self.endpoints = endpoints
        self.timeout = timeout
        self.session = session or requests.Session()

    @classmethod
    def from_config(cls, config):
        raw = config.get("NFE_SEFAZ_PRODUCTION_ENDPOINTS_JSON") or "{}"
        try:
            endpoints = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError) as exc:
            raise SefazConfigurationError("Configuração de endpoints SEFAZ inválida.") from exc
        if not isinstance(endpoints, dict):
            raise SefazConfigurationError("Configuração de endpoints SEFAZ inválida.")
        return cls(endpoints, timeout=float(config.get("NFE_SEFAZ_TIMEOUT_SECONDS", 20)))

    def endpoint(self, state: str, operation: str) -> str:
        url = (self.endpoints.get(state) or {}).get(operation)
        parsed = urlsplit(url or "")
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise SefazConfigurationError(f"Endpoint HTTPS de {operation} não configurado para cUF {state}.")
        return url

    def authorize(self, signed_xml: str, material: CertificateMaterial) -> SefazReply:
        key, state, _ = signed_identity(signed_xml)
        payload = etree.Element(f"{{{NS}}}enviNFe", nsmap={None: NS}, versao="4.00")
        etree.SubElement(payload, f"{{{NS}}}idLote").text = str(int.from_bytes(os.urandom(6), "big"))
        etree.SubElement(payload, f"{{{NS}}}indSinc").text = "0"
        payload.append(_xml(signed_xml))
        return self._request("authorization", "NFeAutorizacao4", "nfeAutorizacaoLote", payload, state, key, material)

    def query_receipt(self, key: str, state: str, receipt: str, material: CertificateMaterial) -> SefazReply:
        payload = etree.Element(f"{{{NS}}}consReciNFe", nsmap={None: NS}, versao="4.00")
        etree.SubElement(payload, f"{{{NS}}}tpAmb").text = "1"
        etree.SubElement(payload, f"{{{NS}}}nRec").text = receipt
        return self._request("receipt", "NFeRetAutorizacao4", "nfeRetAutorizacaoLote", payload, state, key, material)

    def query_protocol(self, key: str, state: str, material: CertificateMaterial) -> SefazReply:
        payload = etree.Element(f"{{{NS}}}consSitNFe", nsmap={None: NS}, versao="4.00")
        etree.SubElement(payload, f"{{{NS}}}tpAmb").text = "1"
        etree.SubElement(payload, f"{{{NS}}}xServ").text = "CONSULTAR"
        etree.SubElement(payload, f"{{{NS}}}chNFe").text = key
        return self._request("protocol", "NFeConsultaProtocolo4", "nfeConsultaNF", payload, state, key, material)

    def _request(self, operation, service, method, payload, state, key, material):
        endpoint = self.endpoint(state, operation)
        namespace = f"{NS}/wsdl/{service}"
        envelope = etree.Element(f"{{{SOAP}}}Envelope", nsmap={"soap12": SOAP})
        body = etree.SubElement(envelope, f"{{{SOAP}}}Body")
        call = etree.SubElement(body, f"{{{namespace}}}nfeDadosMsg")
        call.append(payload)
        try:
            private_key, certificate, chain = pkcs12.load_key_and_certificates(material.pkcs12_bytes, material.password)
            if not private_key or not certificate:
                raise FiscalCertificateError("A1 sem chave e certificado para TLS.")
            with tempfile.TemporaryDirectory(prefix="nfe-sefaz-") as directory:
                cert_path, key_path = Path(directory) / "cert.pem", Path(directory) / "key.pem"
                cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM) + b"".join(c.public_bytes(serialization.Encoding.PEM) for c in chain or []))
                key_path.write_bytes(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                os.chmod(key_path, 0o600)
                response = self.session.post(
                    endpoint, data=etree.tostring(envelope, encoding="UTF-8", xml_declaration=True),
                    headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{namespace}/{method}"'},
                    cert=(str(cert_path), str(key_path)), timeout=self.timeout,
                    allow_redirects=False,
                )
            if response.status_code != 200:
                raise SefazTransportError(f"SEFAZ retornou HTTP {response.status_code}; consulte a chave.")
            return parse_reply(response.content, key, kind=operation)
        except (requests.RequestException, OSError, ValueError) as exc:
            if isinstance(exc, SefazConfigurationError):
                raise
            raise SefazTransportError("Falha de comunicação com a SEFAZ; consulte a chave.") from exc
