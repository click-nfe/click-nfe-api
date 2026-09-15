from __future__ import annotations

import re
from typing import Any


_CNAE_CODE = re.compile(r"^\s*(\d{4})\s*[-.]?\s*(\d)\s*[/.-]?\s*(\d{2})\s*$")
_CNAE_VALUE = re.compile(
    r"^\s*(?P<code>\d{4}\s*[-.]?\s*\d\s*[/.-]?\s*\d{2})\s*(?:-|\|)\s*(?P<description>.+?)\s*$"
)


def format_cnae_code(value: Any) -> str:
    """Normaliza um código CNAE de sete dígitos para ``0000-0/00``."""

    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 7:
        raise ValueError("O código CNAE deve conter exatamente 7 dígitos.")
    return f"{digits[:4]}-{digits[4]}/{digits[5:]}"


def format_cnae(value: Any, description: Any) -> str:
    normalized_description = " ".join(str(description or "").split())
    if not normalized_description:
        raise ValueError("A descrição do CNAE é obrigatória.")
    return f"{format_cnae_code(value)} - {normalized_description}"


def normalize_cnae(value: Any) -> str:
    """Normaliza CNAEs já compostos e preserva textos legados sem código."""

    normalized = str(value or "").strip()
    if not normalized:
        return ""
    match = _CNAE_VALUE.match(normalized)
    if not match:
        return normalized
    return format_cnae(match.group("code"), match.group("description"))


def normalize_cnae_list(value: Any) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    return "\n".join(normalize_cnae(line) for line in lines)


def company_cnaes(payload: dict[str, Any]) -> tuple[str, str]:
    """Converte o payload da BrasilAPI para os campos cadastrais do cliente."""

    principal = format_cnae(
        payload.get("cnae_fiscal"),
        payload.get("cnae_fiscal_descricao"),
    )
    secondary = "\n".join(
        format_cnae(item.get("codigo"), item.get("descricao"))
        for item in (payload.get("cnaes_secundarios") or [])
        if item.get("codigo") and item.get("descricao")
    )
    return principal, secondary
