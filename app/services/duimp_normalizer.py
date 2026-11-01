from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..integrations.portal_unico import DuimpIdentifier


class DuimpNormalizer:
    """Converte respostas do Portal Único e fixtures manuais para um contrato interno."""

    MODALITY_MAP = {
        "IMPORTACAO_DIRETA": "direct",
        "IMPORTACAO_POR_CONTA_E_ORDEM": "on_behalf",
        "IMPORTACAO_CONTA_ORDEM": "on_behalf",
        "CONTA_E_ORDEM": "on_behalf",
        "IMPORTACAO_POR_ENCOMENDA": "by_order",
        "IMPORTACAO_ENCOMENDA": "by_order",
        "ENCOMENDA": "by_order",
    }

    def normalize(self, raw_payload: dict[str, Any]) -> dict[str, Any]:
        if raw_payload.get("provider") == "portal_unico" and raw_payload.get(
            "dadosGerais"
        ):
            return self._normalize_portal_unico(raw_payload)
        return self._normalize_legacy(raw_payload)

    def _normalize_portal_unico(self, payload: dict[str, Any]) -> dict[str, Any]:
        general = payload.get("dadosGerais") or {}
        identification = general.get("identificacao") or {}
        cargo = general.get("carga") or {}
        general_taxes = (general.get("tributos") or {}).get(
            "tributosCalculados"
        ) or []
        importer = identification.get("importador") or {}
        raw_items = payload.get("itens") or []

        identifier = DuimpIdentifier.parse(
            payload.get("numero") or identification.get("numero")
        )
        version = payload.get("versao") or identification.get("versao")
        items = [self._normalize_portal_item(item) for item in raw_items]
        modalities = {item.get("import_modality") for item in items if item.get("import_modality")}
        third_party_ids = {
            item.get("third_party_tax_id")
            for item in items
            if item.get("third_party_tax_id")
        }
        exporters = [item.get("exporter") for item in items if item.get("exporter")]
        unique_exporters = self._unique_foreign_operators(exporters)

        if len(modalities) > 1:
            raise ValueError("A DUIMP possui itens com modalidades de importação diferentes.")
        if len(third_party_ids) > 1:
            raise ValueError("A DUIMP possui mais de um adquirente/encomendante nos itens.")
        unit = cargo.get("unidadeDeclarada") or {}
        country = cargo.get("paisProcedencia") or {}
        registration_datetime = identification.get("dataRegistro")

        return {
            "number": identifier.formatted,
            "api_number": identifier.compact,
            "version": str(version) if version is not None else None,
            "registration_datetime": registration_datetime,
            "registration_date": self._date_part(registration_datetime),
            "clearance_location_code": self._string(unit.get("codigo")),
            "clearance_location": self._string(
                unit.get("descricao") or general.get("localDesembaraco")
            ),
            "clearance_state": self._string(
                unit.get("uf") or general.get("ufDesembaraco")
            ),
            "clearance_date": self._date_part(general.get("dataDesembaraco")),
            "transport_mode_code": self._string(
                cargo.get("viaTransporteCodigo") or general.get("viaTransporteCodigo")
            ),
            "afrmm_value": str(self._extract_afrmm(cargo)),
            "tax_totals": self._normalize_taxes(general_taxes),
            "intermediation_type": self._string(
                general.get("tipoIntermedio") or "1"
            ),
            "import_modality": next(iter(modalities), None),
            "third_party_tax_id": next(iter(third_party_ids), None),
            "importer": {
                "type": importer.get("tipoImportador"),
                "tax_id": self._digits(importer.get("ni")),
            },
            "country_of_origin": {
                "iso_alpha_2": self._string(country.get("codigo")),
                "name": self._string(country.get("descricao")),
            },
            # Mantido para compatibilidade com o fluxo de exportador único.
            "foreign_supplier": unique_exporters[0] if unique_exporters else None,
            "foreign_suppliers": unique_exporters,
            "exporter_count": len(unique_exporters),
            "items": items,
            "catalog_enrichment": payload.get("catalogEnrichment")
            or {
                "products_requested": 0,
                "products_enriched": 0,
                "operators_requested": 0,
                "operators_enriched": 0,
                "failures": [],
            },
            "raw": payload,
        }

    def _normalize_portal_item(self, raw_item: dict[str, Any]) -> dict[str, Any]:
        identification = raw_item.get("identificacao") or {}
        product = raw_item.get("produto") or {}
        merchandise = raw_item.get("mercadoria") or {}
        sale = raw_item.get("condicaoVenda") or {}
        taxes_block = raw_item.get("tributos") or {}
        calculated_merchandise = taxes_block.get("mercadoria") or {}
        characterization = raw_item.get("caracterizacaoImportacao") or {}
        exporter = raw_item.get("exportador") or {}
        manufacturer = raw_item.get("fabricante") or {}

        quantity = self._decimal(merchandise.get("quantidadeComercial"))
        source_freight = self._decimal((sale.get("frete") or {}).get("valorBRL"))
        source_insurance = self._decimal(
            (sale.get("seguro") or {}).get("valorBRL")
        )
        customs_value = self._decimal(
            calculated_merchandise.get("valorAduaneiroBRL")
            or sale.get("valorBRL")
        )
        local_shipment_value = self._decimal(
            calculated_merchandise.get("valorLocalEmbarqueBRL")
            or sale.get("valorBRL")
            or customs_value
        )
        unit_value = (
            local_shipment_value / quantity if quantity else Decimal("0")
        )
        net_weight = self._decimal(merchandise.get("pesoLiquido"))
        modality_code = self._string(characterization.get("indicador"))
        catalog = product.get("catalogo") or {}
        if not isinstance(catalog, dict):
            catalog = {}
        description, additional_info = self._nfe_product_text(
            product.get("denominacao")
            or product.get("descricao")
            or merchandise.get("descricao"),
            catalog.get("descricao"),
        )
        manufacturer_code = self._string(manufacturer.get("codigo"))
        catalog_codes = catalog.get("codigosInterno")
        if isinstance(catalog_codes, list):
            catalog_code = next((code for code in catalog_codes if code), None)
        else:
            catalog_code = catalog_codes if isinstance(catalog_codes, str) else None

        return {
            "number": str(identification.get("numeroItem") or ""),
            # O código interno do Catálogo não identifica necessariamente o
            # cProd adotado pelo emitente na NF-e. Mantê-lo só como referência.
            "product_code": "",
            "catalog_product_code": self._string(
                product.get("codigoInternoNfe")
                or product.get("codigoInterno")
                or catalog_code
            ),
            "catalog_product_id": self._string(product.get("codigo")),
            "product_version": self._string(product.get("versao")),
            "description": description,
            "additional_info": additional_info,
            "complementary_description": self._string(merchandise.get("descricao")),
            "ncm": self._digits(product.get("ncm")),
            "cest": self._digits(product.get("cest") or catalog.get("cest"))
            or None,
            "commercial_unit": self._nfe_unit(
                merchandise.get("unidadeComercial") or "UN"
            ),
            "quantity": str(quantity),
            "unit_value": str(unit_value),
            "customs_value": str(customs_value),
            "product_value": str(local_shipment_value),
            "taxable_unit": self._nfe_unit(
                merchandise.get("unidadeComercial") or "UN"
            ),
            "taxable_quantity": str(quantity),
            "taxable_unit_value": str(unit_value),
            # Para DUIMP, nAdicao não é informado. nSeqAdic identifica o
            # número real do item da declaração, e não a posição na NF-e.
            "addition_number": None,
            "sequence_number": self._string(identification.get("numeroItem")),
            "manufacturer_code": manufacturer_code,
            "manufacturer_code_missing_from_portal": (
                "fabricante" in raw_item and manufacturer_code is None
            ),
            "exporter_code": self._string(exporter.get("codigo")),
            "drawback_number": self._string(
                (raw_item.get("dadosInsumoDrawbackIsencao") or {}).get(
                    "numeroAtoDuimpInsumo"
                )
            ),
            # A NF-e discrimina o valor local da mercadoria, o frete e o seguro.
            # O valor aduaneiro permanece separado para rateios e conferências.
            "freight_value": str(source_freight),
            "insurance_value": str(source_insurance),
            "customs_freight_value": str(source_freight),
            "customs_insurance_value": str(source_insurance),
            "net_weight": str(net_weight),
            "discount_value": str(self._sale_adjustment(sale, "DEDUCAO")),
            "other_value": str(self._sale_adjustment(sale, "ACRESCIMO")),
            "import_modality": self.MODALITY_MAP.get(modality_code, modality_code.lower() or None),
            "third_party_tax_id": self._digits(characterization.get("ni")) or None,
            "tax_classification_code": self._tax_classification(raw_item),
            "taxes": self._normalize_taxes(taxes_block.get("tributosCalculados") or []),
            "exporter": self._foreign_operator(exporter),
            "manufacturer": self._foreign_operator(manufacturer),
            "raw": raw_item,
        }

    def _normalize_legacy(self, raw_payload: dict[str, Any]) -> dict[str, Any]:
        raw_items = raw_payload.get("itens") or raw_payload.get("items") or []
        normalized_items = []

        for index, raw_item in enumerate(raw_items, start=1):
            quantity = self._decimal(raw_item.get("quantidade") or raw_item.get("quantity"))
            product_value = self._decimal(
                raw_item.get("valorProduto")
                or raw_item.get("productValue")
                or raw_item.get("valor")
            )
            unit_value = self._decimal(
                raw_item.get("valorUnitario") or raw_item.get("unitValue")
            )
            if unit_value == 0 and quantity > 0 and product_value > 0:
                unit_value = product_value / quantity

            normalized_items.append(
                {
                    "number": str(raw_item.get("numeroItem") or raw_item.get("number") or index),
                    "product_code": str(
                        raw_item.get("codigoProduto") or raw_item.get("productCode") or index
                    ),
                    "description": raw_item.get("descricao")
                    or raw_item.get("description")
                    or "Mercadoria importada",
                    "ncm": self._digits(raw_item.get("ncm") or raw_item.get("NCM")),
                    "commercial_unit": raw_item.get("unidade")
                    or raw_item.get("commercialUnit")
                    or "UN",
                    "quantity": str(quantity),
                    "unit_value": str(unit_value),
                    "product_value": str(product_value),
                    "customs_value": str(
                        self._decimal(
                            raw_item.get("valorAduaneiro")
                            or raw_item.get("customsValue")
                            or product_value
                        )
                    ),
                    "taxable_unit": raw_item.get("unidadeTributavel")
                    or raw_item.get("taxableUnit")
                    or raw_item.get("unidade")
                    or "UN",
                    "taxable_quantity": str(
                        self._decimal(
                            raw_item.get("quantidadeTributavel")
                            or raw_item.get("taxableQuantity")
                            or quantity
                        )
                    ),
                    "taxable_unit_value": str(
                        self._decimal(
                            raw_item.get("valorUnitarioTributavel")
                            or raw_item.get("taxableUnitValue")
                            or unit_value
                        )
                    ),
                    "addition_number": raw_item.get("numeroAdicao")
                    or raw_item.get("additionNumber"),
                    "sequence_number": raw_item.get("sequenciaAdicao")
                    or raw_item.get("sequenceNumber"),
                    "manufacturer_code": raw_item.get("codigoFabricante")
                    or raw_item.get("manufacturerCode"),
                    "exporter_code": raw_item.get("codigoExportador")
                    or raw_item.get("exporterCode"),
                    "exporter": raw_item.get("exportador")
                    or raw_item.get("foreignSupplier")
                    or raw_item.get("exporter"),
                    "drawback_number": raw_item.get("numeroDrawback")
                    or raw_item.get("drawbackNumber"),
                    "freight_value": str(
                        self._decimal(raw_item.get("valorFrete") or raw_item.get("freightValue"))
                    ),
                    "insurance_value": str(
                        self._decimal(raw_item.get("valorSeguro") or raw_item.get("insuranceValue"))
                    ),
                    "discount_value": str(
                        self._decimal(raw_item.get("valorDesconto") or raw_item.get("discountValue"))
                    ),
                    "other_value": str(
                        self._decimal(
                            raw_item.get("valorOutrasDespesas") or raw_item.get("otherValue")
                        )
                    ),
                    "net_weight": str(
                        self._decimal(
                            raw_item.get("pesoLiquido")
                            or raw_item.get("netWeight")
                        )
                    ),
                    "taxes": raw_item.get("tributos") or raw_item.get("taxes") or {},
                    "raw": raw_item,
                }
            )

        number = raw_payload.get("numero") or raw_payload.get("number")
        parsed_number = None
        if number:
            parsed_number = DuimpIdentifier.parse(number)
        root_supplier = raw_payload.get("foreignSupplier") or raw_payload.get(
            "fornecedorEstrangeiro"
        )
        item_exporters = [
            item.get("exporter")
            for item in normalized_items
            if isinstance(item.get("exporter"), dict)
        ]
        unique_exporters = self._unique_foreign_operators(
            item_exporters + ([root_supplier] if isinstance(root_supplier, dict) else [])
        )
        return {
            "number": parsed_number.formatted if parsed_number else None,
            "api_number": parsed_number.compact if parsed_number else None,
            "version": raw_payload.get("versao") or raw_payload.get("version"),
            "registration_date": raw_payload.get("dataRegistro")
            or raw_payload.get("registrationDate"),
            "clearance_location": raw_payload.get("localDesembaraco")
            or raw_payload.get("clearanceLocation"),
            "clearance_state": raw_payload.get("ufDesembaraco")
            or raw_payload.get("clearanceState"),
            "clearance_date": raw_payload.get("dataDesembaraco")
            or raw_payload.get("clearanceDate"),
            "transport_mode_code": raw_payload.get("viaTransporteCodigo")
            or raw_payload.get("transportModeCode"),
            "afrmm_value": str(
                self._decimal(raw_payload.get("valorAfrmm") or raw_payload.get("afrmmValue"))
            ),
            "tax_totals": raw_payload.get("totaisTributos")
            or raw_payload.get("taxTotals")
            or {},
            "intermediation_type": raw_payload.get("tipoIntermedio")
            or raw_payload.get("intermediationType")
            or "1",
            "import_modality": raw_payload.get("importModality")
            or raw_payload.get("modalidadeImportacao"),
            "exporter_code": raw_payload.get("codigoExportador")
            or raw_payload.get("exporterCode"),
            "foreign_supplier": unique_exporters[0] if unique_exporters else root_supplier,
            "foreign_suppliers": unique_exporters,
            "exporter_count": len(unique_exporters),
            "items": normalized_items,
            "raw": raw_payload,
        }

    @staticmethod
    def _unique_foreign_operators(
        operators: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for operator in operators:
            if not isinstance(operator, dict):
                continue
            key = str(
                operator.get("code")
                or operator.get("codigo")
                or operator.get("foreign_tax_id")
                or operator.get("foreign_id")
                or operator.get("name")
                or operator.get("legal_name")
                or ""
            ).strip()
            if key and key not in unique:
                unique[key] = operator
        return list(unique.values())

    def _normalize_taxes(self, calculated: list[dict[str, Any]]) -> dict[str, Any]:
        taxes: dict[str, Any] = {}
        for tax in calculated:
            tax_type = self._string(tax.get("tipo")).lower()
            if not tax_type:
                continue
            values = tax.get("valoresBRL") or {}
            calculation = tax.get("memoriaCalculo") or {}
            value = (
                values.get("devido")
                if values.get("devido") is not None
                else (
                    values.get("calculado")
                    if values.get("calculado") is not None
                    else (
                        values.get("aRecolher")
                        if values.get("aRecolher") is not None
                        else values.get("recolhido")
                    )
                )
            )
            taxes[tax_type] = {
                "value": str(self._decimal(value)),
                "base": str(self._decimal(calculation.get("baseCalculoBRL"))),
                "rate": str(
                    self._decimal(
                        calculation.get("valorAliquota")
                        or calculation.get("valorAliquotaReduzida")
                    )
                ),
                "calculation": calculation,
                "raw": tax,
            }
        return taxes

    def _extract_afrmm(self, cargo: dict[str, Any]) -> Decimal:
        references = (
            (cargo.get("multiplosConhecimentosCarga") or {}).get("cargasReferenciadas")
            or []
        )
        total = Decimal("0")
        for reference in references:
            afrmm = reference.get("dadosAfrmmTum") or {}
            total += self._decimal(afrmm.get("valorDevido") or afrmm.get("valorPago"))
        return total

    def _sale_adjustment(self, sale: dict[str, Any], adjustment_type: str) -> Decimal:
        return sum(
            (
                self._decimal(item.get("valorBRL"))
                for item in sale.get("acrescimosDeducoes") or []
                if item.get("tipo") == adjustment_type
            ),
            Decimal("0"),
        )

    def _tax_classification(self, raw_item: dict[str, Any]) -> str | None:
        direct = raw_item.get("cClassTrib") or raw_item.get(
            "codigoClassificacaoTributaria"
        )
        if direct:
            return self._digits(direct).zfill(6)
        for attribute in raw_item.get("atributosDuimp") or []:
            code = self._string(attribute.get("codigo")).lower()
            if "classtrib" in code or "class_trib" in code:
                return self._digits(attribute.get("valor")).zfill(6)
        return None

    def _foreign_operator(self, payload: dict[str, Any]) -> dict[str, Any]:
        country = payload.get("pais") or {}
        if not isinstance(country, dict):
            country = {}
        address = payload.get("endereco") or {}
        if not isinstance(address, dict):
            address = {}
        if payload.get("logradouro"):
            address["logradouro"] = payload["logradouro"]
        if payload.get("nomeCidade"):
            address["city_name"] = payload["nomeCidade"]
        if payload.get("codigoSubdivisaoPais"):
            address["subdivision_code"] = payload["codigoSubdivisaoPais"]
        if payload.get("cep"):
            address["zip_code"] = payload["cep"]
        return {
            "code": self._string(payload.get("codigo")),
            "version": self._string(payload.get("versao")),
            "name": self._string(payload.get("nome") or payload.get("razaoSocial")),
            "foreign_tax_id": self._string(
                payload.get("tin") or payload.get("numeroIdentificacao")
            ),
            "country_iso_alpha_2": self._string(
                country.get("codigo") or payload.get("codigoPais")
            ),
            "country_name": self._string(country.get("descricao")),
            "address": address or None,
        }

    @staticmethod
    def _nfe_product_text(
        denomination: Any,
        complementary_detail: Any = None,
    ) -> tuple[str, str | None]:
        parts = []
        for value in (denomination, complementary_detail):
            normalized = re.sub(r"\s+", " ", str(value or "")).strip()
            if normalized and normalized not in parts:
                parts.append(normalized)

        description = " ".join(parts) or "Mercadoria importada"
        if len(description) <= 120:
            return description, None

        truncated = description[:120].rstrip()
        if (
            len(truncated) == 120
            and not description[120].isspace()
            and " " in truncated
        ):
            truncated = truncated.rsplit(" ", 1)[0]
        return truncated, description[:500]

    @classmethod
    def _nfe_product_description(
        cls,
        denomination: Any,
        complementary_detail: Any = None,
    ) -> str:
        return cls._nfe_product_text(denomination, complementary_detail)[0]

    @classmethod
    def _nfe_unit(cls, value: Any) -> str:
        normalized = re.sub(r"\s+", " ", str(value or "UN")).strip().upper()
        aliases = {
            "UNIDADE": "UN",
            "UNIDADES": "UN",
            "PECA": "PC",
            "PECAS": "PC",
            "QUILOGRAMA": "KG",
            "QUILOGRAMAS": "KG",
        }
        return aliases.get(normalized, normalized[:6] or "UN")

    @staticmethod
    def _date_part(value: Any) -> str | None:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)[:10]

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value if value not in (None, "") else "0"))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    @staticmethod
    def _digits(value: Any) -> str:
        return "".join(filter(str.isdigit, str(value or "")))

    @staticmethod
    def _string(value: Any) -> str | None:
        if value in (None, ""):
            return None
        return str(value).strip()
