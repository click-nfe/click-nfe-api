from io import BytesIO
from copy import deepcopy

import pytest
from pypdf import PdfReader

from app.services.nfe_danfe_preview import (
    DanfePreviewError,
    render_unnumbered_danfe_preview,
)
from app.services.nfe_xml_builder import NfeXmlBuilder
from tests.unit.test_nfe_xml_builder import payload


def test_standard_preview_does_not_print_temporary_fiscal_identifiers():
    data = payload()
    for field in ("number", "cnf", "check_digit"):
        data["document"].pop(field)
    xml = NfeXmlBuilder().build_unnumbered(data)

    pdf = render_unnumbered_danfe_preview(xml)
    assert pdf.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(pdf))
    content = "\n".join(page.extract_text() for page in reader.pages)
    assert "DANFE" in content
    assert "Produto sanitizado para teste" in content
    assert "SEM VALOR FISCAL" in content
    assert "Nº PENDENTE" in content
    assert "CHAVE NÃO GERADA" in content
    assert "000.000.000" not in content
    assert "0000 0000 0000 0000" not in content
    assert xml == NfeXmlBuilder().build_unnumbered(data)


def test_unnumbered_preview_refuses_numbered_xml():
    xml = NfeXmlBuilder().build(
        payload(), access_key="41260700000000000191550010000144221763362375",
    )
    with pytest.raises(DanfePreviewError):
        render_unnumbered_danfe_preview(xml)


def test_unnumbered_preview_keeps_every_page_marked_without_a_number():
    data = payload()
    for field in ("number", "cnf", "check_digit"):
        data["document"].pop(field)
    item = data["items"][0]
    data["items"] = [dict(deepcopy(item), item_number=i, product_code=f"PROD-{i}") for i in range(1, 81)]

    pdf = render_unnumbered_danfe_preview(NfeXmlBuilder().build_unnumbered(data))
    pages = PdfReader(BytesIO(pdf)).pages
    assert len(pages) > 1
    for page in pages:
        content = page.extract_text()
        assert "SEM VALOR FISCAL" in content
        assert "Nº PENDENTE" in content
        assert "000.000.000" not in content
