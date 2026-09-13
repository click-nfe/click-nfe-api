import pytest

from app.cnae import company_cnaes, format_cnae, format_cnae_code, normalize_cnae


def test_formats_cnae_code_and_value():
    assert format_cnae_code("4623109") == "4623-1/09"
    assert format_cnae("4623-1/09", "  Comércio   atacadista  ") == (
        "4623-1/09 - Comércio atacadista"
    )


def test_normalizes_legacy_separator_without_guessing_missing_code():
    assert normalize_cnae("4520-0/07 | Serviços de manutenção") == (
        "4520-0/07 - Serviços de manutenção"
    )
    assert normalize_cnae("Descrição sem código") == "Descrição sem código"


def test_rejects_invalid_code():
    with pytest.raises(ValueError, match="7 dígitos"):
        format_cnae_code("123")


def test_maps_brasil_api_company_payload():
    principal, secondary = company_cnaes(
        {
            "cnae_fiscal": 4623109,
            "cnae_fiscal_descricao": "Comércio atacadista de alimentos para animais",
            "cnaes_secundarios": [
                {"codigo": 4647802, "descricao": "Comércio atacadista de livros"},
                {"codigo": 5211799, "descricao": "Depósitos de mercadorias"},
            ],
        }
    )
    assert principal == "4623-1/09 - Comércio atacadista de alimentos para animais"
    assert secondary.splitlines() == [
        "4647-8/02 - Comércio atacadista de livros",
        "5211-7/99 - Depósitos de mercadorias",
    ]
