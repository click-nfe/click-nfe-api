"""Small, explicit client template contract; process values never become defaults."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation

from app.models.import_process import ImportPurpose

DOCUMENT = {"operation_nature", "presence_indicator", "intermediary_indicator"}
TRANSPORT = {"freight_mode", "carrier_name", "carrier_tax_id", "carrier_state_registration", "carrier_address", "carrier_city", "carrier_state"}
ITEM = {"reference", "product_code", "description", "ncm", "cfop", "unit", "import_purpose", "tax_configuration"}
TAX = {"ii_rate", "ipi_rate", "pis_rate", "cofins_rate", "icms_cst", "icms_origin", "ipi_cst", "pis_cst", "cofins_cst", "icms_rate", "icms_base_reduction_rate", "icms_deferment_rate", "icms_st_base_method", "icms_st_mva_rate", "icms_st_base_reduction_rate", "icms_st_rate", "icms_st_retained_base", "icms_st_retained_rate", "icms_st_retained_value", "icms_tax_treatment_confirmed"}
PAYMENT = {"payment_indicator", "method", "description", "value"}
VOLUME = {"quantity", "species", "brand", "numbering", "net_weight", "gross_weight"}


def normalize_config(data: object) -> dict:
    if not isinstance(data, dict) or set(data) - {"document", "transport", "items", "additional_info", "series", "payment", "volume"}:
        raise ValueError("Configuração da emissão simplificada inválida.")
    result = {"document": {}, "transport": {}, "items": [], "additional_info": {}, "payment": {}, "volume": {}}
    series = data.get("series") or ""
    if not isinstance(series, str) or (series and (not series.isdigit() or not 1 <= int(series) <= 999)):
        raise ValueError("Série da NF-e inválida.")
    result["series"] = series
    for section, allowed in (("document", DOCUMENT), ("transport", TRANSPORT), ("additional_info", {"fiscal", "complementary", "legal_text"})):
        values = data.get(section) or {}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError(f"Campos inválidos em {section}.")
        for key, value in values.items():
            if not isinstance(value, str) or len(value) > 1000:
                raise ValueError(f"Valor inválido em {section}.{key}.")
            result[section][key] = value.strip()
    document = result["document"]
    transport = result["transport"]
    if len(document.get("operation_nature", "")) > 60:
        raise ValueError("A natureza da operação aceita até 60 caracteres.")
    if document.get("presence_indicator", "") not in ("", "0", "1", "2", "3", "4", "5", "9"):
        raise ValueError("Indicador de presença inválido.")
    if document.get("intermediary_indicator", "") not in ("", "0", "1"):
        raise ValueError("Indicador de intermediador inválido.")
    if transport.get("freight_mode", "") not in ("", "0", "1", "2", "3", "4", "9"):
        raise ValueError("Modalidade do frete inválida; use 0, 1, 2, 3, 4 ou 9.")
    if len(transport.get("carrier_name", "")) > 60 or len(transport.get("carrier_state", "")) not in (0, 2):
        raise ValueError("Nome ou UF da transportadora inválido.")
    for section, allowed in (("payment", PAYMENT), ("volume", VOLUME)):
        values = data.get(section) or {}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ValueError(f"Campos inválidos em {section}.")
        for key, value in values.items():
            if not isinstance(value, str) or len(value) > 100:
                raise ValueError(f"Valor inválido em {section}.{key}.")
            value = value.strip()
            if value and key in {"quantity", "net_weight", "gross_weight", "value"}:
                try:
                    number = Decimal(value)
                except InvalidOperation as exc:
                    raise ValueError(f"Valor numérico inválido em {section}.{key}.") from exc
                if not number.is_finite() or number < 0 or (key == "quantity" and (number < 1 or number != int(number))):
                    raise ValueError(f"Valor numérico inválido em {section}.{key}.")
            result[section][key] = value
    if result["payment"].get("payment_indicator", "") not in ("", "0", "1"):
        raise ValueError("Indicador de pagamento inválido.")
    method = result["payment"].get("method", "")
    if method and (len(method) != 2 or not method.isdigit()):
        raise ValueError("Meio de pagamento inválido.")
    items = data.get("items") or []
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError("A configuração aceita até 500 referências de produtos.")
    for index, raw in enumerate(items, 1):
        if not isinstance(raw, dict) or set(raw) - ITEM:
            raise ValueError(f"Campos inválidos no produto {index}.")
        item = {}
        for key in ITEM - {"tax_configuration"}:
            value = raw.get(key, "")
            if not isinstance(value, str) or len(value) > (500 if key == "description" else 80):
                raise ValueError(f"Valor inválido no produto {index}: {key}.")
            item[key] = value.strip()
        if item["ncm"] and (len(item["ncm"]) != 8 or not item["ncm"].isdigit()):
            raise ValueError(f"NCM inválido no produto {index}.")
        if item["cfop"] and (len(item["cfop"]) != 4 or not item["cfop"].isdigit()):
            raise ValueError(f"CFOP inválido no produto {index}.")
        if item["product_code"] and len(item["product_code"]) > 60:
            raise ValueError(f"Código NF-e muito longo no produto {index}.")
        if len(item["unit"]) > 6:
            raise ValueError(f"Unidade do produto {index} deve ter até 6 caracteres.")
        if item["import_purpose"] and item["import_purpose"] not in ImportPurpose.values():
            raise ValueError(f"Finalidade inválida no produto {index}.")
        taxes = raw.get("tax_configuration") or {}
        if not isinstance(taxes, dict) or set(taxes) - TAX:
            raise ValueError(f"Tributação inválida no produto {index}.")
        item["tax_configuration"] = {}
        for key, value in taxes.items():
            if key == "icms_tax_treatment_confirmed":
                if value not in (True, False, None):
                    raise ValueError(f"Confirmação fiscal inválida no produto {index}.")
                if value is not None:
                    item["tax_configuration"][key] = value
                continue
            if not isinstance(value, str):
                raise ValueError(f"Alíquota inválida no produto {index}: {key}.")
            value = value.strip()
            if value:
                if key in {"icms_cst", "ipi_cst", "pis_cst", "cofins_cst", "icms_origin", "icms_st_base_method"}:
                    if not value.isdigit() or len(value) > (1 if key in {"icms_origin", "icms_st_base_method"} else 2):
                        raise ValueError(f"CST inválido no produto {index}.")
                    if key == "icms_origin" and value not in "012345678":
                        raise ValueError(f"Origem do ICMS inválida no produto {index}.")
                    if key == "icms_st_base_method" and value not in {"4", "6"}:
                        raise ValueError(f"Modalidade da base de ICMS ST inválida no produto {index}.")
                else:
                    try:
                        rate = Decimal(value)
                    except InvalidOperation as exc:
                        raise ValueError(f"Alíquota inválida no produto {index}: {key}.") from exc
                    ceiling = 999999999 if key in {"icms_st_retained_base", "icms_st_retained_value"} else 999.9999 if key == "icms_st_mva_rate" else 100
                    if not rate.is_finite() or not 0 <= rate <= Decimal(str(ceiling)):
                        raise ValueError(f"Alíquota fora do intervalo no produto {index}: {key}.")
            item["tax_configuration"][key] = value
        if any(item[key] for key in ("reference", "product_code", "description", "ncm")):
            result["items"].append(item)
    return result


def reconcile(config: dict, normalized: dict, links: dict | None = None) -> list[dict]:
    """Only exact unique reference + NCM matches can be applied automatically."""
    templates = config.get("items") or []
    links = links or {}
    results = []
    for source in normalized.get("items") or []:
        number = str(source.get("number") or "")
        ncm = str(source.get("ncm") or "")
        catalog_code = source.get("catalog_product_code") or source.get("product_code")
        code = str(catalog_code or "").strip().casefold()
        candidates = [i for i, item in enumerate(templates) if item.get("ncm") == ncm and code and code in {str(item.get("reference") or "").strip().casefold(), str(item.get("product_code") or "").strip().casefold()}]
        selected = links.get(number)
        if selected is not None and (not isinstance(selected, int) or selected < 0 or selected >= len(templates) or templates[selected].get("ncm") != ncm):
            selected = None
        if selected is None and len(candidates) == 1:
            selected = candidates[0]
        results.append({"duimp_item_number": number, "catalog_product_code": catalog_code, "ncm": ncm, "description": source.get("description"), "template_index": selected, "status": "linked" if number in links and selected is not None else "matched" if selected is not None else "ambiguous" if len(candidates) > 1 else "unmatched", "candidates": candidates})
    return results


def draft_defaults(config: dict) -> dict:
    transport = deepcopy(config.get("transport") or {})
    carrier = {"tax_id": transport.pop("carrier_tax_id", ""), "name": transport.pop("carrier_name", ""), "state_registration": transport.pop("carrier_state_registration", ""), "address": transport.pop("carrier_address", ""), "city_name": transport.pop("carrier_city", ""), "state": transport.pop("carrier_state", "")}
    transport["carrier"] = {key: value for key, value in carrier.items() if value}
    if not transport["carrier"]:
        transport.pop("carrier")
    volume = config.get("volume") or {}
    if volume:
        transport["volume"] = {k: int(v) if k == "quantity" else v for k, v in volume.items() if v}
    return {"document": {k: v for k, v in config.get("document", {}).items() if v}, "transport": {k: v for k, v in transport.items() if v}, "payment": {k: v for k, v in config.get("payment", {}).items() if v}, "additional_info": {k: v for k, v in config.get("additional_info", {}).items() if v}}
