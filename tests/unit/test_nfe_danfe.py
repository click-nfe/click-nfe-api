import subprocess
from xml.etree import ElementTree as ET

import pytest

from app.services.nfe_danfe import DanfeError, render_authorized_danfe
from app.services.nfe_danfe_preview import DanfePreviewError, render_danfe_preview


NS = "http://www.portalfiscal.inf.br/nfe"
KEY = "41" + "1" * 42
PROTOCOL = "141260000000001"


def authorized_xml(*, items=1):
    details = "".join(
        f'<det nItem="{n}"><prod><cProd>{n}</cProd><xProd>Produto {n}</xProd>'
        '<NCM>85044010</NCM><CFOP>3102</CFOP><uCom>UN</uCom><qCom>1.0000</qCom>'
        '<vUnCom>100.00</vUnCom><vProd>100.00</vProd></prod>'
        '<imposto><ICMS><ICMS00><orig>1</orig><CST>00</CST><vBC>100.00</vBC>'
        '<pICMS>12.00</pICMS><vICMS>12.00</vICMS></ICMS00></ICMS></imposto></det>'
        for n in range(1, items + 1)
    )
    return f'''<nfeProc xmlns="{NS}" versao="4.00"><NFe><infNFe Id="NFe{KEY}">
    <ide><mod>55</mod><tpImp>1</tpImp><tpEmis>1</tpEmis><tpAmb>1</tpAmb>
    <nNF>123</nNF><serie>1</serie><natOp>Importação</natOp></ide>
    <emit><xNome>Emissor</xNome><CNPJ>00000000000191</CNPJ><IE>123</IE>
    <enderEmit><xLgr>Rua Principal</xLgr><nro>10</nro><xMun>Curitiba</xMun><UF>PR</UF></enderEmit></emit>
    <dest><xNome>Fornecedor</xNome><idEstrangeiro>EXT1</idEstrangeiro></dest>
    {details}
    <total><ICMSTot><vProd>100.00</vProd><vNF>100.00</vNF></ICMSTot></total>
    <transp><modFrete>9</modFrete></transp>
    <infAdic><infCpl>DUIMP 123</infCpl></infAdic></infNFe></NFe>
    <protNFe><infProt><tpAmb>1</tpAmb><chNFe>{KEY}</chNFe>
    <dhRecbto>2026-09-30T12:00:00-03:00</dhRecbto><nProt>{PROTOCOL}</nProt>
    <cStat>100</cStat></infProt></protNFe></nfeProc>'''


def test_danfe_contains_authorization_and_all_items_across_pages():
    pdf = render_authorized_danfe(authorized_xml(items=80), expected_key=KEY,
                                  expected_protocol=PROTOCOL)
    assert pdf.startswith(b"%PDF-")
    text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf,
                          capture_output=True, check=True).stdout.decode()
    pages = [page for page in text.split("\f") if page.strip()]
    assert len(pages) > 1
    assert "DANFE" in text and PROTOCOL in text and KEY[:4] in text
    assert "Produto 1" in text and "Produto 80" in text
    assert f"FOLHA 1/{len(pages)}" in text
    assert f"FOLHA {len(pages)}/{len(pages)}" in text


@pytest.mark.parametrize("mutated", [
    lambda xml: xml.replace(f"<chNFe>{KEY}</chNFe>", "<chNFe>0</chNFe>"),
    lambda xml: xml.replace("<cStat>100</cStat>", "<cStat>110</cStat>"),
    lambda xml: xml.replace("<tpAmb>1</tpAmb>", "<tpAmb>2</tpAmb>"),
    lambda xml: xml.replace("<tpImp>1</tpImp>", "<tpImp>2</tpImp>"),
    lambda xml: xml.replace("<nfeProc", "<outro").replace("</nfeProc>", "</outro>"),
])
def test_rejects_unauthorized_or_unsupported_xml(mutated):
    with pytest.raises(DanfeError):
        render_authorized_danfe(mutated(authorized_xml()), expected_key=KEY,
                                expected_protocol=PROTOCOL)


def test_rejects_different_persisted_identity():
    with pytest.raises(DanfeError):
        render_authorized_danfe(authorized_xml(), expected_key="0" * 44)
    with pytest.raises(DanfeError):
        render_authorized_danfe(authorized_xml(), expected_protocol="0" * 15)


def unsigned_preview_xml(*, items=1):
    root = ET.fromstring(authorized_xml(items=items))
    nfe = root.find(f"{{{NS}}}NFe")
    assert nfe is not None
    return ET.tostring(nfe, encoding="unicode")


def test_preview_uses_standard_danfe_layout_and_marks_every_page():
    pdf = render_danfe_preview(unsigned_preview_xml(items=80))
    assert pdf.startswith(b"%PDF-")
    text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf,
                          capture_output=True, check=True).stdout.decode()
    pages = [page for page in text.split("\f") if page.strip()]
    assert len(pages) > 1
    assert "DANFE" in text and "Produto 1" in text and "Produto 80" in text
    assert all("SEM VALOR FISCAL" in page for page in pages)
    assert all("PREVIA" in page for page in pages)
    assert PROTOCOL not in text
    assert f"FOLHA 1/{len(pages)}" in text


def test_preview_rejects_authorized_xml_instead_of_disguising_it():
    with pytest.raises(DanfePreviewError, match="sem protocolo"):
        render_danfe_preview(authorized_xml())


def test_preview_rejects_unsupported_invoice_model():
    root = ET.fromstring(unsigned_preview_xml())
    model = root.find(f".//{{{NS}}}mod")
    assert model is not None
    model.text = "65"
    with pytest.raises(DanfePreviewError, match="dados necessários"):
        render_danfe_preview(ET.tostring(root, encoding="unicode"))
