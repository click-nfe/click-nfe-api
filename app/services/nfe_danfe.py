"""Render the fiscal DANFE only from a persisted, authorized nfeProc."""

from __future__ import annotations

import re

from brazilfiscalreport.danfe import Danfe
from lxml import etree


NAMESPACE = "http://www.portalfiscal.inf.br/nfe"
N = f"{{{NAMESPACE}}}"


class DanfeError(ValueError):
    """The stored XML cannot be presented as an authorized DANFE."""


def _value(node, path: str) -> str:
    return (node.findtext(path) or "").strip() if node is not None else ""


def render_authorized_danfe(
    xml_content: str, *, expected_key: str | None = None,
    expected_protocol: str | None = None,
) -> bytes:
    """Check issuer XML and SEFAZ protocol before invoking the DANFE layout engine."""
    try:
        root = etree.fromstring(xml_content.encode("utf-8"), parser=etree.XMLParser(
            resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False,
        ))
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise DanfeError("XML autorizado ilegível.") from exc
    if root.tag != N + "nfeProc":
        raise DanfeError("O DANFE definitivo exige o nfeProc com protocolo.")
    nfe = root.find(N + "NFe")
    inf = nfe.find(N + "infNFe") if nfe is not None else None
    prot = root.find(N + "protNFe/" + N + "infProt")
    if inf is None or prot is None:
        raise DanfeError("NF-e ou protocolo ausente do nfeProc.")
    ide = inf.find(N + "ide")
    if ide is None or inf.find(N + "emit") is None or inf.find(N + "dest") is None:
        raise DanfeError("Dados de identificação, emitente ou destinatário ausentes.")
    key = (inf.get("Id") or "").removeprefix("NFe")
    protocol = _value(prot, N + "nProt")
    if (not re.fullmatch(r"\d{44}", key) or _value(prot, N + "chNFe") != key
            or (expected_key is not None and key != expected_key)):
        raise DanfeError("Chave de acesso incompatível com o protocolo da SEFAZ.")
    if (not re.fullmatch(r"\d{15}", protocol)
            or (expected_protocol is not None and protocol != expected_protocol)
            or _value(prot, N + "cStat") not in {"100", "150"}
            or _value(prot, N + "tpAmb") != "1"
            or _value(ide, N + "tpAmb") != "1"):
        raise DanfeError("Autorização de produção inválida ou incompatível.")
    if (_value(ide, N + "mod") != "55" or _value(ide, N + "tpImp") != "1"
            or _value(ide, N + "tpEmis") != "1"):
        raise DanfeError("Este DANFE atende apenas NF-e modelo 55, retrato e emissão normal.")
    if not inf.findall(N + "det") or inf.find(N + "total") is None:
        raise DanfeError("NF-e autorizada sem itens ou totais.")
    try:
        return bytes(Danfe(xml=xml_content).output())
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise DanfeError("O leiaute não pôde ser gerado a partir do XML autorizado.") from exc
