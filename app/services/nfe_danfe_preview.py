"""Render the standard DANFE layout as a clearly marked, unauthorized preview."""

from __future__ import annotations

import re
from io import BytesIO

from brazilfiscalreport.danfe import Danfe, DanfeConfig, FooterStamp
from lxml import etree


NAMESPACE = "http://www.portalfiscal.inf.br/nfe"
N = f"{{{NAMESPACE}}}"


class DanfePreviewError(ValueError):
    """The XML cannot be presented as a fiscal-document preview."""


def _value(node, path: str) -> str:
    return (node.findtext(path) or "").strip() if node is not None else ""


class _UnnumberedDanfe(Danfe):
    """Keep the standard layout while suppressing the renderer's temporary key/number.

    BrazilFiscalReport requires both fields to lay out the page. They exist only
    in the in-memory rendering copy and are replaced before anything is drawn.
    """

    _temporary_key = "0" * 44
    _grouped_key = " ".join(["0000"] * 11)

    def cell(self, *args, **kwargs):
        content = kwargs.get("text", args[2] if len(args) > 2 else None)
        if isinstance(content, str) and re.fullmatch(r"Nº\s*0[0.]*", content):
            if len(args) > 2:
                args = (*args[:2], "Nº PENDENTE", *args[3:])
            else:
                kwargs["text"] = "Nº PENDENTE"
        return super().cell(*args, **kwargs)

    def multi_cell(self, *args, **kwargs):
        if kwargs.get("text") == self._grouped_key:
            kwargs["text"] = "CHAVE NÃO GERADA"
        return super().multi_cell(*args, **kwargs)

    def image(self, name=None, *args, **kwargs):
        # Only the header barcode is an SVG BytesIO with this exact height.
        if isinstance(name, BytesIO) and kwargs.get("h") == 8.5 and b"<svg" in name.getvalue()[:500]:
            return None
        return super().image(name, *args, **kwargs)


def _preview_config(*, unnumbered: bool = False) -> DanfeConfig:
    label = "SEM NUMERO E CHAVE - " if unnumbered else ""
    return DanfeConfig(
        footer_stamp=FooterStamp(
            text=f"PREVIA - {label}SEM VALOR FISCAL - NAO TRANSMITIDA A SEFAZ",
            height=5,
        ),
    )


def render_unnumbered_danfe_preview(xml_content: str) -> bytes:
    """Render the working XML without presenting fictitious fiscal identifiers."""
    try:
        root = etree.fromstring(xml_content.encode("utf-8"), parser=etree.XMLParser(
            resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False,
        ))
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise DanfePreviewError("O XML de trabalho não pôde ser lido.") from exc

    inf = root.find(N + "infNFe") if root.tag == N + "NFe" else None
    ide = inf.find(N + "ide") if inf is not None else None
    if (
        root.find(N + "protNFe") is not None
        or root.find("{http://www.w3.org/2000/09/xmldsig#}Signature") is not None
        or inf is None or inf.get("Id") or ide is None
        or any(ide.find(N + tag) is not None for tag in ("nNF", "cNF", "cDV"))
        or _value(ide, N + "mod") != "55"
        or _value(ide, N + "tpAmb") != "1"
        or _value(ide, N + "tpImp") != "1"
        or _value(ide, N + "tpEmis") != "1"
        or inf.find(N + "emit") is None
        or inf.find(N + "dest") is None
        or not inf.findall(N + "det")
        or inf.find(N + "total") is None
    ):
        raise DanfePreviewError("O XML de trabalho não possui os dados necessários para a prévia padrão.")

    # Only the private rendering copy receives placeholders required by the
    # third-party layout engine. The downloaded XML is never changed or stored.
    inf.set("Id", "NFe" + _UnnumberedDanfe._temporary_key)
    etree.SubElement(ide, N + "nNF").text = "0"
    try:
        return bytes(_UnnumberedDanfe(
            xml=etree.tostring(root, encoding="utf-8"),
            config=_preview_config(unnumbered=True),
        ).output())
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise DanfePreviewError("O leiaute da prévia não pôde ser gerado a partir do XML de trabalho.") from exc


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
    try:
        sanitized_xml = etree.tostring(root, encoding="utf-8")
        return bytes(Danfe(xml=sanitized_xml, config=_preview_config()).output())
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise DanfePreviewError("O leiaute da prévia não pôde ser gerado a partir do XML.") from exc
