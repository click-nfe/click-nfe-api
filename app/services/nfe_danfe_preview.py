"""Prévia de conferência do DANFE para XML assinado, ainda sem autorização."""

from __future__ import annotations

from html import escape
from io import BytesIO

from lxml import etree
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class DanfePreviewError(ValueError):
    """O XML não pode ser apresentado como prévia da NF-e."""


NS = {"n": "http://www.portalfiscal.inf.br/nfe"}
INK = colors.HexColor("#17392a")
PALE = colors.HexColor("#e9f2eb")


def _text(parent, path: str) -> str:
    node = parent.find(path, namespaces=NS)
    return (node.text or "").strip() if node is not None else ""


def _paragraph(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(value or "—"), style)


def _format_money(value: str) -> str:
    try:
        return f"{float(value):,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")
    except (TypeError, ValueError):
        return value or "—"


def _page_decoration(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setFont("Helvetica-Bold", 39)
    canvas.setFillColor(colors.HexColor("#e6e9e6"))
    canvas.translate(width / 2, height / 2)
    canvas.rotate(42)
    canvas.drawCentredString(0, 0, "PRÉVIA • SEM VALOR FISCAL")
    canvas.rotate(-42)
    canvas.translate(-width / 2, -height / 2)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(15 * mm, 11 * mm, "XML assinado, ainda não autorizado pela SEFAZ.")
    canvas.drawRightString(width - 15 * mm, 11 * mm, f"Página {doc.page}")
    canvas.restoreState()


def render_danfe_preview(xml_content: str) -> bytes:
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    try:
        root = etree.fromstring(xml_content.encode("utf-8"), parser=parser)
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise DanfePreviewError("O XML assinado não pôde ser lido.") from exc
    inf = root.find("n:infNFe", namespaces=NS)
    if inf is None:
        raise DanfePreviewError("A identificação da NF-e não foi encontrada no XML.")
    key = inf.get("Id", "").removeprefix("NFe")
    if len(key) != 44 or not key.isdigit():
        raise DanfePreviewError("A chave de acesso da NF-e é inválida.")

    styles = getSampleStyleSheet()
    heading = ParagraphStyle(
        "PreviewHeading", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=14, leading=17, textColor=INK, spaceAfter=4 * mm,
    )
    section = ParagraphStyle(
        "PreviewSection", parent=styles["Heading2"], fontSize=10, leading=13,
        textColor=INK, spaceBefore=5 * mm, spaceAfter=2 * mm,
    )
    body = ParagraphStyle("PreviewBody", parent=styles["Normal"], fontSize=8, leading=11)
    small = ParagraphStyle("PreviewSmall", parent=body, fontSize=7, leading=9)
    right = ParagraphStyle("PreviewRight", parent=small, alignment=TA_RIGHT)
    center = ParagraphStyle("PreviewCenter", parent=small, alignment=TA_CENTER)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=16 * mm, bottomMargin=19 * mm, title=f"Prévia DANFE {key}",
    )
    width = A4[0] - 30 * mm
    story = [
        Paragraph("PRÉVIA DO DANFE — SEM VALOR FISCAL", heading),
        _paragraph("NF-e de entrada de importação. A SEFAZ ainda não concedeu autorização de uso.", body),
        Spacer(1, 3 * mm),
    ]
    ide = inf.find("n:ide", namespaces=NS)
    emit = inf.find("n:emit", namespaces=NS)
    dest = inf.find("n:dest", namespaces=NS)
    total = inf.find("n:total/n:ICMSTot", namespaces=NS)
    transp = inf.find("n:transp", namespaces=NS)
    if ide is None or emit is None or dest is None:
        raise DanfePreviewError("O XML não contém todos os dados de identificação.")

    headline = Table(
        [[_paragraph(f"NF-e nº {_text(ide, 'n:nNF')} · Série {_text(ide, 'n:serie')} · Modelo 55", body),
          _paragraph(f"Emissão: {_text(ide, 'n:dhEmi')}", right)]],
        colWidths=[width * .57, width * .43],
    )
    headline.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), .5, INK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [headline, Spacer(1, 3 * mm),
              _paragraph(f"Chave de acesso: {' '.join(key[i:i+4] for i in range(0, 44, 4))}", body)]
    barcode = code128.Code128(key, barWidth=.27 * mm, barHeight=11 * mm, humanReadable=False)
    story += [barcode, _paragraph("Protocolo de autorização: NÃO DISPONÍVEL", small)]

    def block(title: str, rows: list[tuple[str, str]]) -> None:
        cells = [[_paragraph(label, small), _paragraph(value, body)] for label, value in rows]
        table = Table(cells, colWidths=[width * .27, width * .73], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#c5d2c8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, -1), PALE),
        ]))
        story.extend([Paragraph(title, section), table])

    block("Emitente", [
        ("Razão social", _text(emit, "n:xNome")),
        ("CNPJ / IE", f"{_text(emit, 'n:CNPJ')} / {_text(emit, 'n:IE')}"),
        ("Endereço", " · ".join(filter(None, [
            _text(emit, "n:enderEmit/n:xLgr"), _text(emit, "n:enderEmit/n:nro"),
            _text(emit, "n:enderEmit/n:xMun"), _text(emit, "n:enderEmit/n:UF"),
        ]))),
    ])
    block("Destinatário / fornecedor estrangeiro", [
        ("Nome", _text(dest, "n:xNome")),
        ("País / cidade", " · ".join(filter(None, [
            _text(dest, "n:enderDest/n:xPais"), _text(dest, "n:enderDest/n:xMun"),
        ]))),
        ("Natureza", _text(ide, "n:natOp")),
    ])
    if total is not None:
        block("Totais da NF-e (R$)", [
            ("Produtos / frete", f"{_format_money(_text(total, 'n:vProd'))} / {_format_money(_text(total, 'n:vFrete'))}"),
            ("II / IPI / ICMS", " / ".join(_format_money(_text(total, f"n:{tag}")) for tag in ("vII", "vIPI", "vICMS"))),
            ("Valor total", _format_money(_text(total, "n:vNF"))),
        ])

    items = inf.findall("n:det", namespaces=NS)
    story.append(Paragraph(f"Produtos / serviços ({len(items)} itens)", section))
    header = ["Item", "Código / descrição", "NCM", "CFOP", "Qtd.", "Un.", "Unit. R$", "Total R$"]
    rows = [[_paragraph(label, center) for label in header]]
    for item in items:
        prod = item.find("n:prod", namespaces=NS)
        if prod is None:
            continue
        rows.append([
            _paragraph(item.get("nItem", ""), small),
            _paragraph(f"{_text(prod, 'n:cProd')} · {_text(prod, 'n:xProd')}", small),
            _paragraph(_text(prod, "n:NCM"), small),
            _paragraph(_text(prod, "n:CFOP"), small),
            _paragraph(_text(prod, "n:qCom"), right),
            _paragraph(_text(prod, "n:uCom"), small),
            _paragraph(_format_money(_text(prod, "n:vUnCom")), right),
            _paragraph(_format_money(_text(prod, "n:vProd")), right),
        ])
    products = Table(
        rows, colWidths=[width * x for x in (.05, .37, .1, .075, .095, .065, .115, .13)],
        repeatRows=1, hAlign="LEFT",
    )
    products.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#c5d2c8")),
        ("BACKGROUND", (0, 0), (-1, 0), PALE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(products)

    di_numbers = list(dict.fromkeys(
        _text(item, "n:prod/n:DI/n:nDI") for item in items
    ))
    block("Importação e transporte", [
        ("DUIMP / DI", ", ".join(filter(None, di_numbers))),
        ("Modalidade frete", _text(transp, "n:modFrete") if transp is not None else ""),
    ])
    additional = _text(inf, "n:infAdic/n:infCpl")
    if additional:
        story.extend([Paragraph("Informações complementares", section), _paragraph(additional, body)])
    story.extend([Spacer(1, 5 * mm),
                  _paragraph("Documento de conferência. Não acoberta circulação de mercadoria.", body)])

    document.build(story, onFirstPage=_page_decoration, onLaterPages=_page_decoration)
    return buffer.getvalue()
