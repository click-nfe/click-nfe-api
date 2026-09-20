from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree import ElementTree as ET


class NfeXmlBuildError(ValueError):
    pass


class NfeXmlBuilder:
    """Monta uma NF-e 4.00 de importação ainda sem assinatura digital."""

    NS = "http://www.portalfiscal.inf.br/nfe"

    def build(self, payload: dict[str, Any], *, access_key: str) -> str:
        if len(str(access_key)) != 44 or not str(access_key).isdigit():
            raise NfeXmlBuildError("A chave de acesso deve conter 44 dígitos.")

        ET.register_namespace("", self.NS)
        nfe = ET.Element(self._tag("NFe"))
        inf_nfe = ET.SubElement(
            nfe,
            self._tag("infNFe"),
            {"Id": f"NFe{access_key}", "versao": "4.00"},
        )

        self._build_ide(inf_nfe, payload)
        self._build_issuer(inf_nfe, payload.get("issuer") or {})
        self._build_recipient(inf_nfe, payload.get("recipient") or {})
        for item in payload.get("items") or []:
            self._build_item(inf_nfe, payload, item)
        self._build_totals(inf_nfe, payload.get("totals") or {})
        self._build_transport(inf_nfe, payload.get("transport") or {})
        self._build_payment(inf_nfe, payload.get("payment") or {}, payload.get("totals") or {})
        self._build_additional_info(inf_nfe, payload.get("additional_info") or {})

        return ET.tostring(
            nfe,
            encoding="utf-8",
            xml_declaration=True,
            short_empty_elements=True,
        ).decode("utf-8")

    def _build_ide(self, parent: ET.Element, payload: dict[str, Any]) -> None:
        document = payload.get("document") or {}
        issuer_address = (payload.get("issuer") or {}).get("address") or {}
        required = {
            "state_code": document.get("state_code"),
            "cnf": document.get("cnf"),
            "number": document.get("number"),
            "issue_datetime": document.get("issue_datetime"),
            "check_digit": document.get("check_digit"),
            "city_code": issuer_address.get("city_code"),
        }
        missing = [name for name, value in required.items() if value in (None, "")]
        if missing:
            raise NfeXmlBuildError(
                "Campos obrigatórios de identificação ausentes: " + ", ".join(missing)
            )

        ide = ET.SubElement(parent, self._tag("ide"))
        self._text(ide, "cUF", document["state_code"])
        self._text(ide, "cNF", document["cnf"])
        self._text(ide, "natOp", document.get("operation_nature") or "Importação de mercadoria")
        self._text(ide, "mod", "55")
        self._text(ide, "serie", str(document["series"]).lstrip("0") or "0")
        self._text(ide, "nNF", document["number"])
        self._text(ide, "dhEmi", self._datetime(document["issue_datetime"]))
        if document.get("exit_entry_datetime"):
            self._text(
                ide,
                "dhSaiEnt",
                self._datetime(document["exit_entry_datetime"]),
            )
        self._text(ide, "tpNF", "0")
        self._text(ide, "idDest", "3")
        self._text(ide, "cMunFG", issuer_address["city_code"])
        self._text(ide, "tpImp", document.get("print_type") or "1")
        self._text(ide, "tpEmis", document.get("tp_emis") or "1")
        self._text(ide, "cDV", document["check_digit"])
        self._text(ide, "tpAmb", "1" if document.get("environment") == "production" else "2")
        self._text(ide, "finNFe", "1")
        self._text(ide, "indFinal", document.get("final_consumer") or "0")
        self._text(ide, "indPres", document.get("presence_indicator") or "9")
        self._optional(
            ide,
            "indIntermed",
            document.get("intermediary_indicator") or "0",
        )
        self._text(ide, "procEmi", "0")
        self._text(ide, "verProc", document.get("application_version") or "Triagem Aduaneira")

    def _build_issuer(self, parent: ET.Element, issuer: dict[str, Any]) -> None:
        address = dict(issuer.get("address") or {})
        address["phone"] = (issuer.get("contact") or {}).get("phone")
        emit = ET.SubElement(parent, self._tag("emit"))
        self._text(emit, "CNPJ", issuer.get("cnpj"))
        self._text(emit, "xNome", issuer.get("legal_name"))
        self._optional(emit, "xFant", issuer.get("trade_name"))
        ender = ET.SubElement(emit, self._tag("enderEmit"))
        self._address(ender, address, foreign=False)
        self._text(emit, "IE", issuer.get("state_registration"))
        self._optional(emit, "IM", issuer.get("municipal_registration"))
        self._text(emit, "CRT", issuer.get("tax_regime"))

    def _build_recipient(self, parent: ET.Element, recipient: dict[str, Any]) -> None:
        address = recipient.get("address") or {}
        dest = ET.SubElement(parent, self._tag("dest"))
        foreign_id = recipient.get("foreign_id")
        self._text(dest, "idEstrangeiro", foreign_id if foreign_id is not None else "")
        self._text(dest, "xNome", recipient.get("legal_name"))
        ender = ET.SubElement(dest, self._tag("enderDest"))
        self._address(ender, address, foreign=True)
        self._text(dest, "indIEDest", "9")

    def _address(self, parent: ET.Element, address: dict[str, Any], *, foreign: bool) -> None:
        self._text(parent, "xLgr", address.get("street"))
        self._text(parent, "nro", address.get("number"))
        self._optional(parent, "xCpl", address.get("complement"))
        self._text(parent, "xBairro", address.get("district"))
        self._text(parent, "cMun", "9999999" if foreign else address.get("city_code"))
        self._text(parent, "xMun", address.get("city_name"))
        self._text(parent, "UF", "EX" if foreign else address.get("state"))
        if not foreign:
            self._optional(parent, "CEP", address.get("zip_code"))
        self._text(parent, "cPais", address.get("country_code"))
        self._text(parent, "xPais", address.get("country_name"))
        if not foreign:
            self._optional(parent, "fone", address.get("phone"))

    def _build_item(self, parent: ET.Element, payload: dict[str, Any], item: dict[str, Any]) -> None:
        det = ET.SubElement(parent, self._tag("det"), {"nItem": str(item["item_number"])})
        prod = ET.SubElement(det, self._tag("prod"))
        self._text(prod, "cProd", item.get("product_code"))
        self._text(prod, "cEAN", item.get("gtin") or "SEM GTIN")
        self._text(prod, "xProd", item.get("description"))
        self._text(prod, "NCM", item.get("ncm"))
        self._optional(prod, "CEST", item.get("cest"))
        self._optional(prod, "cBenef", item.get("benefit_code"))
        self._text(prod, "CFOP", item.get("cfop"))
        self._text(prod, "uCom", item.get("commercial_unit"))
        self._text(prod, "qCom", self._quantity(item.get("commercial_quantity")))
        self._text(prod, "vUnCom", self._unit_value(item.get("commercial_unit_value")))
        self._text(prod, "vProd", self._money(item.get("product_value")))
        self._text(prod, "cEANTrib", item.get("taxable_gtin") or "SEM GTIN")
        self._text(prod, "uTrib", item.get("taxable_unit"))
        self._text(prod, "qTrib", self._quantity(item.get("taxable_quantity")))
        self._text(prod, "vUnTrib", self._unit_value(item.get("taxable_unit_value")))
        self._optional_money(prod, "vFrete", item.get("freight_value"))
        self._optional_money(prod, "vSeg", item.get("insurance_value"))
        self._optional_money(prod, "vDesc", item.get("discount_value"))
        self._optional_money(prod, "vOutro", item.get("other_value"))
        self._text(prod, "indTot", "1")
        self._build_import_declaration(prod, payload.get("duimp") or {}, item.get("import_payload") or {})
        self._build_taxes(det, item.get("tax_payload") or {})
        self._optional(det, "infAdProd", item.get("additional_info"))
        if (item.get("tax_payload") or {}).get("ibs_cbs"):
            self._text(det, "vItem", (item.get("tax_payload") or {}).get("rtc_invoice_value") or self._rtc_item_value(item))

    def _build_import_declaration(self, parent: ET.Element, duimp: dict[str, Any], data: dict[str, Any]) -> None:
        di = ET.SubElement(parent, self._tag("DI"))
        self._text(di, "nDI", duimp.get("api_number") or str(duimp.get("number") or "").replace("-", ""))
        self._text(di, "dDI", duimp.get("registration_date"))
        self._text(di, "xLocDesemb", duimp.get("clearance_location"))
        self._text(di, "UFDesemb", duimp.get("clearance_state"))
        self._text(di, "dDesemb", duimp.get("clearance_date"))
        self._text(di, "tpViaTransp", duimp.get("transport_mode_code"))
        self._optional_money(di, "vAFRMM", data.get("afrmm_value"))
        intermediation = duimp.get("intermediation_type") or "1"
        self._text(di, "tpIntermedio", intermediation)
        third_party = str(duimp.get("third_party_tax_id") or "")
        if intermediation in {"2", "3"} and third_party:
            self._text(di, "CNPJ" if len(third_party) == 14 else "CPF", third_party)
            self._text(di, "UFTerceiro", duimp.get("third_party_state"))
        self._text(di, "cExportador", data.get("exporter_code") or duimp.get("exporter_code"))
        addition = ET.SubElement(di, self._tag("adi"))
        self._optional(addition, "nAdicao", data.get("addition_number"))
        self._text(addition, "nSeqAdic", data.get("sequence_number") or "1")
        self._text(addition, "cFabricante", data.get("manufacturer_code") or "0000")
        self._optional(addition, "nDraw", data.get("drawback_number"))

    def _build_taxes(self, parent: ET.Element, taxes: dict[str, Any]) -> None:
        required = [name for name in ("icms", "ipi", "ii", "pis", "cofins") if not taxes.get(name)]
        if required:
            raise NfeXmlBuildError("Tributos obrigatórios ausentes no item: " + ", ".join(required))
        imposto = ET.SubElement(parent, self._tag("imposto"))

        icms_data = taxes["icms"]
        icms_cst = str(icms_data.get("cst") or "").zfill(2)
        if icms_cst not in {
            "00", "10", "20", "30", "40", "41", "50", "51", "60", "70", "90"
        }:
            raise NfeXmlBuildError(
                "Nesta etapa, o gerador suporta ICMS CST 00, 10, 20, 30, 40, "
                "41, 50, 51, 60, 70 ou 90 para importação."
            )
        icms = ET.SubElement(imposto, self._tag("ICMS"))
        if icms_cst == "00":
            icms00 = ET.SubElement(icms, self._tag("ICMS00"))
            self._text(icms00, "orig", icms_data.get("origin") or "1")
            self._text(icms00, "CST", "00")
            self._text(icms00, "modBC", icms_data.get("base_method") or "3")
            self._text(icms00, "vBC", self._money(icms_data.get("base")))
            self._text(icms00, "pICMS", self._rate(icms_data.get("rate")))
            self._text(icms00, "vICMS", self._money(icms_data.get("value")))
        elif icms_cst == "10":
            icms10 = ET.SubElement(icms, self._tag("ICMS10"))
            self._text(icms10, "orig", icms_data.get("origin") or "1")
            self._text(icms10, "CST", "10")
            self._build_icms_own_fields(icms10, icms_data)
            self._build_icms_st_fields(icms10, icms_data)
        elif icms_cst == "20":
            icms20 = ET.SubElement(icms, self._tag("ICMS20"))
            self._text(icms20, "orig", icms_data.get("origin") or "1")
            self._text(icms20, "CST", "20")
            self._build_icms_own_fields(
                icms20,
                icms_data,
                require_reduction=True,
            )
        elif icms_cst == "30":
            icms30 = ET.SubElement(icms, self._tag("ICMS30"))
            self._text(icms30, "orig", icms_data.get("origin") or "1")
            self._text(icms30, "CST", "30")
            self._build_icms_st_fields(icms30, icms_data)
        elif icms_cst in {"40", "41", "50"}:
            icms40 = ET.SubElement(icms, self._tag("ICMS40"))
            self._text(icms40, "orig", icms_data.get("origin") or "1")
            self._text(icms40, "CST", icms_cst)
        elif icms_cst == "51":
            icms51 = ET.SubElement(icms, self._tag("ICMS51"))
            self._text(icms51, "orig", icms_data.get("origin") or "1")
            self._text(icms51, "CST", "51")
            self._optional(icms51, "modBC", icms_data.get("base_method"))
            if icms_data.get("base_reduction_rate") not in (None, ""):
                self._text(
                    icms51,
                    "pRedBC",
                    self._rate(icms_data["base_reduction_rate"]),
                )
            self._optional(icms51, "cBenefRBC", icms_data.get("base_benefit_code"))
            self._text(icms51, "vBC", self._money(icms_data.get("base")))
            if icms_data.get("rate") not in (None, ""):
                self._text(icms51, "pICMS", self._rate(icms_data["rate"]))
            if icms_data.get("operation_value") not in (None, ""):
                self._text(
                    icms51,
                    "vICMSOp",
                    self._money(icms_data["operation_value"]),
                )
            if icms_data.get("deferment_rate") not in (None, ""):
                self._text(
                    icms51,
                    "pDif",
                    self._rate(icms_data["deferment_rate"]),
                )
            if icms_data.get("deferred_value") not in (None, ""):
                self._text(
                    icms51,
                    "vICMSDif",
                    self._money(icms_data["deferred_value"]),
                )
            if icms_data.get("value") not in (None, ""):
                self._text(
                    icms51,
                    "vICMS",
                    self._money(icms_data["value"]),
                )
        elif icms_cst == "60":
            icms60 = ET.SubElement(icms, self._tag("ICMS60"))
            self._text(icms60, "orig", icms_data.get("origin") or "1")
            self._text(icms60, "CST", "60")
            self._text(
                icms60,
                "vBCSTRet",
                self._money(icms_data.get("retained_st_base")),
            )
            self._text(
                icms60,
                "pST",
                self._rate(icms_data.get("retained_st_rate")),
            )
            self._text(
                icms60,
                "vICMSSTRet",
                self._money(icms_data.get("retained_st_value")),
            )
        elif icms_cst == "70":
            icms70 = ET.SubElement(icms, self._tag("ICMS70"))
            self._text(icms70, "orig", icms_data.get("origin") or "1")
            self._text(icms70, "CST", "70")
            self._build_icms_own_fields(
                icms70,
                icms_data,
                require_reduction=True,
            )
            self._build_icms_st_fields(icms70, icms_data)
        else:
            icms90 = ET.SubElement(icms, self._tag("ICMS90"))
            for tag, key, default in (
                ("orig", "origin", "1"),
                ("CST", "cst", "90"),
                ("modBC", "base_method", "3"),
            ):
                self._text(icms90, tag, icms_data.get(key) or default)
            self._text(icms90, "vBC", self._money(icms_data.get("base")))
            self._text(icms90, "pICMS", self._rate(icms_data.get("rate")))
            self._text(icms90, "vICMS", self._money(icms_data.get("value")))
            self._text(icms90, "modBCST", icms_data.get("st_base_method") or "6")
            self._text(icms90, "vBCST", self._money(icms_data.get("st_base")))
            self._text(icms90, "pICMSST", self._rate(icms_data.get("st_rate")))
            self._text(icms90, "vICMSST", self._money(icms_data.get("st_value")))

        ipi_data = taxes["ipi"]
        ipi = ET.SubElement(imposto, self._tag("IPI"))
        self._text(ipi, "cEnq", ipi_data.get("enquiry_code") or "999")
        ipi_cst = str(ipi_data.get("cst") or "49").zfill(2)
        if ipi_cst in {"01", "02", "03", "04", "05", "51", "52", "53", "54", "55"}:
            ipi_nt = ET.SubElement(ipi, self._tag("IPINT"))
            self._text(ipi_nt, "CST", ipi_cst)
        elif ipi_cst in {"00", "49", "50", "99"}:
            ipi_trib = ET.SubElement(ipi, self._tag("IPITrib"))
            self._text(ipi_trib, "CST", ipi_cst)
            self._text(ipi_trib, "vBC", self._money(ipi_data.get("base")))
            self._text(ipi_trib, "pIPI", self._rate(ipi_data.get("rate")))
            self._text(ipi_trib, "vIPI", self._money(ipi_data.get("value")))
        else:
            raise NfeXmlBuildError(f"CST de IPI não suportado: {ipi_cst}.")

        ii_data = taxes["ii"]
        ii = ET.SubElement(imposto, self._tag("II"))
        self._text(ii, "vBC", self._money(ii_data.get("base")))
        self._text(ii, "vDespAdu", self._money(ii_data.get("customs_expenses")))
        self._text(ii, "vII", self._money(ii_data.get("value")))
        self._text(ii, "vIOF", self._money(ii_data.get("iof")))

        self._build_contribution(imposto, "PIS", "PISOutr", "pPIS", "vPIS", taxes["pis"])
        self._build_contribution(imposto, "COFINS", "COFINSOutr", "pCOFINS", "vCOFINS", taxes["cofins"])
        if taxes.get("ibs_cbs"):
            self._build_ibs_cbs(imposto, taxes["ibs_cbs"])

    def _build_icms_own_fields(
        self,
        parent: ET.Element,
        data: dict[str, Any],
        *,
        require_reduction: bool = False,
    ) -> None:
        self._text(parent, "modBC", data.get("base_method") or "3")
        if require_reduction:
            self._text(
                parent,
                "pRedBC",
                self._rate(data.get("base_reduction_rate")),
            )
        self._text(parent, "vBC", self._money(data.get("base")))
        self._text(parent, "pICMS", self._rate(data.get("rate")))
        self._text(parent, "vICMS", self._money(data.get("value")))

    def _build_icms_st_fields(
        self,
        parent: ET.Element,
        data: dict[str, Any],
    ) -> None:
        self._text(parent, "modBCST", data.get("st_base_method") or "6")
        if data.get("st_mva_rate") not in (None, ""):
            self._text(parent, "pMVAST", self._rate(data["st_mva_rate"]))
        if data.get("st_base_reduction_rate") not in (None, ""):
            self._text(
                parent,
                "pRedBCST",
                self._rate(data["st_base_reduction_rate"]),
            )
        self._text(parent, "vBCST", self._money(data.get("st_base")))
        self._text(parent, "pICMSST", self._rate(data.get("st_rate")))
        self._text(parent, "vICMSST", self._money(data.get("st_value")))

    def _build_contribution(self, parent: ET.Element, group: str, subtype: str, rate_tag: str, value_tag: str, data: dict[str, Any]) -> None:
        outer = ET.SubElement(parent, self._tag(group))
        inner = ET.SubElement(outer, self._tag(subtype))
        self._text(inner, "CST", data.get("cst") or "98")
        self._text(inner, "vBC", self._money(data.get("base")))
        self._text(inner, rate_tag, self._rate(data.get("rate")))
        self._text(inner, value_tag, self._money(data.get("value")))

    def _build_ibs_cbs(self, parent: ET.Element, data: dict[str, Any]) -> None:
        ibs = ET.SubElement(parent, self._tag("IBSCBS"))
        self._text(ibs, "CST", data.get("cst") or "000")
        self._text(ibs, "cClassTrib", data.get("classification"))
        group = ET.SubElement(ibs, self._tag("gIBSCBS"))
        self._text(group, "vBC", self._money(data.get("base")))
        uf = ET.SubElement(group, self._tag("gIBSUF"))
        self._text(uf, "pIBSUF", self._rate(data.get("ibs_uf_rate")))
        self._text(uf, "vIBSUF", self._money(data.get("ibs_uf_value")))
        mun = ET.SubElement(group, self._tag("gIBSMun"))
        self._text(mun, "pIBSMun", self._rate(data.get("ibs_mun_rate")))
        self._text(mun, "vIBSMun", self._money(data.get("ibs_mun_value")))
        self._text(group, "vIBS", self._money(data.get("ibs_value")))
        cbs = ET.SubElement(group, self._tag("gCBS"))
        self._text(cbs, "pCBS", self._rate(data.get("cbs_rate")))
        self._text(cbs, "vCBS", self._money(data.get("cbs_value")))

    def _build_totals(self, parent: ET.Element, totals: dict[str, Any]) -> None:
        total = ET.SubElement(parent, self._tag("total"))
        icms = ET.SubElement(total, self._tag("ICMSTot"))
        values = (
            ("vBC", "icms_base"), ("vICMS", "icms_value"), ("vICMSDeson", None),
            ("vFCP", None), ("vBCST", "icms_st_base"),
            ("vST", "icms_st_value"), ("vFCPST", None),
            ("vFCPSTRet", None), ("vProd", "products_value"), ("vFrete", "freight_value"),
            ("vSeg", "insurance_value"), ("vDesc", "discount_value"), ("vII", "ii_value"),
            ("vIPI", "ipi_value"), ("vIPIDevol", None), ("vPIS", "pis_value"),
            ("vCOFINS", "cofins_value"), ("vOutro", "other_value"), ("vNF", "invoice_value"),
        )
        for tag, key in values:
            self._text(icms, tag, self._money(totals.get(key) if key else 0))

        if self._decimal(totals.get("ibs_cbs_base")) or self._decimal(totals.get("cbs_value")):
            rtc = ET.SubElement(total, self._tag("IBSCBSTot"))
            self._text(rtc, "vBCIBSCBS", self._money(totals.get("ibs_cbs_base")))
            gibs = ET.SubElement(rtc, self._tag("gIBS"))
            guf = ET.SubElement(gibs, self._tag("gIBSUF"))
            self._text(guf, "vDif", "0.00"); self._text(guf, "vDevTrib", "0.00")
            self._text(guf, "vIBSUF", self._money(totals.get("ibs_uf_value")))
            gmun = ET.SubElement(gibs, self._tag("gIBSMun"))
            self._text(gmun, "vDif", "0.00"); self._text(gmun, "vDevTrib", "0.00")
            self._text(gmun, "vIBSMun", self._money(totals.get("ibs_mun_value")))
            self._text(gibs, "vIBS", self._money(totals.get("ibs_value")))
            self._text(gibs, "vCredPres", "0.00"); self._text(gibs, "vCredPresCondSus", "0.00")
            gcbs = ET.SubElement(rtc, self._tag("gCBS"))
            self._text(gcbs, "vDif", "0.00"); self._text(gcbs, "vDevTrib", "0.00")
            self._text(gcbs, "vCBS", self._money(totals.get("cbs_value")))
            self._text(gcbs, "vCredPres", "0.00"); self._text(gcbs, "vCredPresCondSus", "0.00")
            self._text(total, "vNFTot", self._money(totals.get("rtc_invoice_value")))

    def _build_transport(self, parent: ET.Element, data: dict[str, Any]) -> None:
        transport = ET.SubElement(parent, self._tag("transp"))
        self._text(transport, "modFrete", data.get("freight_mode") or "9")
        carrier = data.get("carrier") or {}
        if carrier:
            carrier_node = ET.SubElement(transport, self._tag("transporta"))
            tax_id = re.sub(r"\D", "", str(carrier.get("tax_id") or ""))
            if len(tax_id) == 14:
                self._text(carrier_node, "CNPJ", tax_id)
            elif len(tax_id) == 11:
                self._text(carrier_node, "CPF", tax_id)
            self._optional(carrier_node, "xNome", carrier.get("name"))
            self._optional(
                carrier_node,
                "IE",
                carrier.get("state_registration"),
            )
            self._optional(carrier_node, "xEnder", carrier.get("address"))
            self._optional(carrier_node, "xMun", carrier.get("city_name"))
            self._optional(carrier_node, "UF", carrier.get("state"))
        volume = data.get("volume") or {}
        if volume:
            vol = ET.SubElement(transport, self._tag("vol"))
            self._optional(vol, "qVol", volume.get("quantity"))
            self._optional(vol, "esp", volume.get("species"))
            self._optional(vol, "marca", volume.get("brand"))
            self._optional(vol, "nVol", volume.get("numbering"))
            if volume.get("net_weight") not in (None, ""):
                self._text(
                    vol,
                    "pesoL",
                    self._weight(volume.get("net_weight")),
                )
            if volume.get("gross_weight") not in (None, ""):
                self._text(
                    vol,
                    "pesoB",
                    self._weight(volume.get("gross_weight")),
                )

    def _build_payment(self, parent: ET.Element, data: dict[str, Any], totals: dict[str, Any]) -> None:
        payment = ET.SubElement(parent, self._tag("pag"))
        detail = ET.SubElement(payment, self._tag("detPag"))
        self._text(detail, "indPag", data.get("payment_indicator") or "1")
        method = data.get("method") or "90"
        self._text(detail, "tPag", method)
        self._optional(detail, "xPag", data.get("description") if method == "99" else None)
        value = data.get("value") if data.get("value") is not None else ("0.00" if method == "90" else totals.get("invoice_value"))
        self._text(detail, "vPag", self._money(value))

    def _build_additional_info(self, parent: ET.Element, data: dict[str, Any]) -> None:
        if not data.get("fiscal") and not data.get("complementary"):
            return
        additional = ET.SubElement(parent, self._tag("infAdic"))
        self._optional(additional, "infAdFisco", data.get("fiscal"))
        self._optional(additional, "infCpl", data.get("complementary"))

    def _rtc_item_value(self, item: dict[str, Any]) -> str:
        taxes = item.get("tax_payload") or {}
        value = (
            self._decimal(item.get("product_value"))
            + self._decimal(item.get("freight_value"))
            + self._decimal(item.get("insurance_value"))
            + self._decimal(item.get("other_value"))
            + self._decimal((taxes.get("ii") or {}).get("value"))
            + self._decimal((taxes.get("ipi") or {}).get("value"))
            - self._decimal(item.get("discount_value"))
        )
        return self._money(value)

    def _optional_money(self, parent: ET.Element, tag: str, value: Any) -> None:
        if self._decimal(value) != 0:
            self._text(parent, tag, self._money(value))

    def _optional(self, parent: ET.Element, tag: str, value: Any) -> None:
        if value not in (None, ""):
            self._text(parent, tag, value)

    def _text(self, parent: ET.Element, tag: str, value: Any) -> ET.Element:
        if value is None:
            raise NfeXmlBuildError(f"Campo XML obrigatório ausente: {tag}.")
        element = ET.SubElement(parent, self._tag(tag))
        element.text = str(value)
        return element

    def _tag(self, name: str) -> str:
        return f"{{{self.NS}}}{name}"

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value if value not in (None, "") else 0))
        except (InvalidOperation, TypeError, ValueError):
            return Decimal("0")

    def _money(self, value: Any) -> str:
        return f"{self._decimal(value):.2f}"

    def _rate(self, value: Any) -> str:
        return f"{self._decimal(value):.4f}"

    def _quantity(self, value: Any) -> str:
        return f"{self._decimal(value):.4f}"

    def _weight(self, value: Any) -> str:
        return f"{self._decimal(value):.3f}"

    def _unit_value(self, value: Any) -> str:
        return f"{self._decimal(value):.10f}"

    @staticmethod
    def _datetime(value: Any) -> str:
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value or "")
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return text
        return parsed.isoformat(timespec="seconds")
