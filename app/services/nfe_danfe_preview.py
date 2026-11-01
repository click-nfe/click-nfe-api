"""Render the standard DANFE layout as a clearly marked, unauthorized preview."""

from __future__ import annotations

import re

from brazilfiscalreport.danfe import Danfe, DanfeConfig, FooterStamp
from lxml import etree


NAMESPACE = "http://www.portalfiscal.inf.br/nfe"
N = f"{{{NAMESPACE}}}"


class DanfePreviewError(ValueError):
    """The XML cannot be presented as a fiscal-document preview."""


def _value(node, path: str) -> str:
    return (node.findtext(path) or "").strip() if node is not None else ""


def render_danfe_preview(xml_content: str) -> bytes:
    """Use the authorized DANFE's renderer, without inventing a SEFAZ protocol."""
    try:
        root = etree.fromstring(
            xml_content.encode("utf-8"),
            parser=etree.XMLParser(
                resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False,
            ),
        )
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise DanfePreviewError("O XML da prévia não pôde ser lido.") from exc

    if root.tag != N + "NFe" or root.find(N + "protNFe") is not None:
        raise DanfePreviewError("A prévia exige a NF-e sem protocolo da SEFAZ.")

    inf = root.find(N + "infNFe")
    ide = inf.find(N + "ide") if inf is not None else None
    key = (inf.get("Id") or "").removeprefix("NFe") if inf is not None else ""
    if (
        not re.fullmatch(r"\d{44}", key)
        or ide is None
        or _value(ide, N + "mod") != "55"
        or _value(ide, N + "tpAmb") != "1"
        or _value(ide, N + "tpImp") != "1"
        or _value(ide, N + "tpEmis") != "1"
        or inf.find(N + "emit") is None
        or inf.find(N + "dest") is None
        or not inf.findall(N + "det")
        or inf.find(N + "total") is None
    ):
        raise DanfePreviewError("A NF-e não possui os dados necessários para a prévia padrão.")

    # The library marks every page SEM VALOR FISCAL when protNFe is absent.
    # An additional footer makes the non-transmitted state explicit in print.
    config = DanfeConfig(
        footer_stamp=FooterStamp(
            text="PREVIA - SEM VALOR FISCAL - NAO TRANSMITIDA A SEFAZ",
            height=5,
        ),
    )
    try:
        sanitized_xml = etree.tostring(root, encoding="utf-8")
        return bytes(Danfe(xml=sanitized_xml, config=config).output())
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise DanfePreviewError("O leiaute da prévia não pôde ser gerado a partir do XML.") from exc
