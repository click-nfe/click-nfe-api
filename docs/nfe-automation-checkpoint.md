# Checkpoint: contexto automatizado e regras fiscais da NF-e de importação

Este checkpoint reduz o payload digitado a cada processo sem transformar
inferências em fatos fiscais. As integrações CCT, PCCE e TABX executam apenas
consultas `GET`. Campos que não possam ser confirmados permanecem em
`missing_fields` e devem ser informados explicitamente antes do rascunho.

## 1. Migração

O model `ClientImportTaxRule` cria a tabela `client_import_tax_rules`. Conforme
o fluxo de migrações do projeto, gere e revise a migration no ambiente local:

```bash
alembic revision --autogenerate -m "add client import tax rules"
alembic upgrade head
```

Nenhuma migration Alembic foi incluída neste checkpoint.

## 2. Configuração opcional da conexão Portal Único

O `config_json` da conexão aceita estas chaves para enriquecimento TABX:

```json
{
  "role_type": "IMPEXP",
  "tabx_customs_unit_table": "NOME_CONFIRMADO_NA_TABX",
  "tabx_customs_unit_code_field": "CODIGO",
  "tabx_customs_unit_description_field": "NOME",
  "tabx_customs_unit_state_field": "UF",
  "tabx_country_table": "NOME_CONFIRMADO_NA_TABX",
  "tabx_country_iso_field": "SIGLA_ISO2",
  "tabx_country_code_field": "CODIGO",
  "tabx_country_name_field": "NOME",
  "country_code_map": {
    "US": "2496"
  }
}
```

Os nomes de tabela/campo devem ser confirmados nos metadados TABX disponíveis
para a credencial. Se não estiverem configurados, a API usa somente referências
oficiais incorporadas e identificadas como `builtin_official_reference`. Neste
checkpoint estão cadastrados `0927800 -> ALF/PORTO DE ITAJAI, SC` e
`US -> 2496, ESTADOS UNIDOS`. Outros códigos continuam pendentes até serem
resolvidos pelo TABX, pela configuração da conexão ou pelo operador.

## 3. Cadastro único de regra fiscal por cliente

Exemplo para uma operação de revenda, importação direta, emitente do Paraná:

```http
POST /clients/{client_id}/import-tax-rules
Content-Type: application/json
```

```json
{
  "name": "PR - revenda - direta - padrão",
  "issuer_state": "PR",
  "import_purpose": "resale",
  "import_modality": "direct",
  "tax_regime": "3",
  "priority": 100,
  "configuration_json": {
    "icms_rate": "12",
    "icms_origin": "1",
    "icms_cst": "90",
    "ipi_cst": "49",
    "pis_cst": "98",
    "cofins_cst": "98"
  },
  "transport_defaults": {
    "freight_mode": "9"
  },
  "payment_defaults": {
    "method": "90",
    "value": "0.00"
  },
  "active": true
}
```

Use `ncm_pattern` quando uma regra for válida somente para um NCM ou prefixo.
A regra mais específica é selecionada por prioridade, tamanho do padrão NCM,
modalidade e regime tributário. As alíquotas e CSTs precisam ser validados pelo
responsável fiscal antes de habilitar a regra.

Endpoints de manutenção:

- `GET /clients/{client_id}/import-tax-rules`
- `PUT /clients/{client_id}/import-tax-rules/{rule_id}`
- `DELETE /clients/{client_id}/import-tax-rules/{rule_id}` (inativa; não apaga)

## 4. Fluxo de uma nova NF-e

Crie o processo e capture a DUIMP como já feito hoje. Uma DUIMP real normalmente
é consultada em `production`, mesmo quando a NF-e será gerada em homologação:

```http
POST /import-processes/{process_id}/duimp/fetch
```

```json
{
  "provider_environment": "production",
  "enrich_catalog": true
}
```

Em seguida resolva e persista o contexto:

```http
POST /import-processes/{process_id}/nfe-context/resolve
```

```json
{
  "duimp_snapshot_id": "UUID_DO_SNAPSHOT",
  "import_purpose": "resale",
  "provider_environment": "production",
  "refresh_external": true,
  "overrides": {}
}
```

A resposta apresenta cada campo com `value`, `source` e `status`, além de
`missing_fields`, `tax_rule`, referências fiscais do PCCE e sugestões. Falhas
isoladas nas fontes externas aparecem em `external.errors`.

Se ainda houver pendências, envie somente os dados confirmados pelo operador:

```json
{
  "duimp_snapshot_id": "UUID_DO_SNAPSHOT",
  "import_purpose": "resale",
  "refresh_external": false,
  "overrides": {
    "clearance_location": "PORTO DE PARANAGUA",
    "clearance_state": "PR",
    "clearance_date": "2026-07-15",
    "transport_mode_code": "1",
    "foreign_supplier": {
      "country_code": "1600",
      "country_name": "CHINA"
    }
  }
}
```

Para consultar o diagnóstico sem persistir alterações:

```http
GET /import-processes/{process_id}/nfe-context?import_purpose=resale&duimp_snapshot_id={snapshot_id}
```

Quando `ready_for_draft=true`, o rascunho pode ser criado com payload mínimo:

```http
POST /import-processes/{process_id}/nfe-draft/from-duimp
```

```json
{
  "environment": "homologation",
  "series": "1",
  "import_purpose": "resale",
  "duimp_snapshot_id": "UUID_DO_SNAPSHOT"
}
```

`tax_configuration` continua aceito como override explícito para exceções. Sem
ele, a API exige uma regra fiscal aplicável. O draft registra
`source.tax_configuration_source` e `source.tax_rule_id` para auditoria.

Depois, prossiga com os endpoints já existentes:

1. `POST /nfe-drafts/{draft_id}/generate-access-key`
2. `POST /nfe-drafts/{draft_id}/generate-xml`
3. `POST /nfe-drafts/{draft_id}/xml-versions/{xml_version_id}/validate-xsd`
4. conferir reconciliação, totais e referências do processo;
5. somente depois assinar e transmitir em homologação.

## 5. XML diagnóstico com ICMS CST 51

Quando o Portal/PCCE e a documentação fiscal comprovarem diferimento integral,
mas a alíquota nominal e o enquadramento do TTD ainda não estiverem confirmados,
é possível cadastrar uma regra exclusiva de diagnóstico:

```http
POST /clients/daba0bf1-43b4-42be-a8a5-b4326307366e/import-tax-rules
Content-Type: application/json
```

```json
{
  "name": "SC - industrialização direta - diferimento integral diagnóstico",
  "issuer_state": "SC",
  "import_purpose": "industrialization",
  "import_modality": "direct",
  "tax_regime": "3",
  "ncm_pattern": "84",
  "priority": 100,
  "configuration_json": {
    "icms_origin": "1",
    "icms_cst": "51",
    "icms_base_method": "3",
    "icms_deferment_rate": "100",
    "icms_base_allocation": "proportional_customs_value",
    "ipi_cst": "49",
    "ipi_enquiry_code": "999",
    "pis_cst": "98",
    "cofins_cst": "98"
  },
  "additional_cost_defaults": {
    "afrmm": "0",
    "thc": "0",
    "other": "0"
  },
  "transport_defaults": {
    "freight_mode": "9"
  },
  "payment_defaults": {
    "method": "90",
    "value": "0.00"
  },
  "active": true,
  "effective_from": "2026-01-01"
}
```

Sem `icms_rate`, o XML contém `ICMS51`, `vBC`, `pDif=100` e `vICMS=0`,
omitindo `pICMS`, `vICMSOp` e `vICMSDif`. O draft recebe:

```json
{
  "authorization": {
    "ready": false,
    "mode": "diagnostic",
    "blockers": [
      {
        "code": "missing_nominal_icms_rate"
      }
    ]
  }
}
```

Esse bloqueio deve ser obrigatoriamente verificado pelo futuro endpoint de
transmissão. A geração, o download e a validação XSD do XML diagnóstico
continuam disponíveis para conferência. A assinatura também é recusada
enquanto o bloqueio fiscal estiver ativo.

Para a DUIMP `26BR0000684087-7`, persista a data confirmada no histórico:

```json
{
  "duimp_snapshot_id": "d4a60485-8309-47d7-b535-7bb3fe2136cc",
  "import_purpose": "industrialization",
  "refresh_external": false,
  "overrides": {
    "clearance_date": "2026-05-27"
  }
}
```

## 6. XML diagnóstico com ICMS CST 40, 41 ou 50

Quando o PCCE comprovar exoneração integral, mas a natureza fiscal ainda
depender de confirmação do operador, informe `tax_configuration` diretamente
na criação do draft. Isso evita cadastrar como padrão do cliente uma regra que
só se aplica a uma reimportação ou operação específica.

```json
{
  "environment": "homologation",
  "series": "1",
  "import_purpose": "resale",
  "duimp_snapshot_id": "UUID_DO_SNAPSHOT",
  "tax_configuration": {
    "icms_origin": "1",
    "icms_cst": "40",
    "icms_tax_treatment_confirmed": false,
    "ipi_cst": "49",
    "ipi_enquiry_code": "999",
    "pis_cst": "98",
    "cofins_cst": "98"
  }
}
```

O operador pode selecionar:

- `40`: isenta;
- `41`: não tributada;
- `50`: suspensão.

Esses CSTs não aceitam `icms_rate`. O XML usa o grupo `ICMS40` somente com
`orig` e `CST`, omitindo base, alíquota e valor desonerado que não tenham sido
comprovados. Enquanto `icms_tax_treatment_confirmed=false`, o draft contém o
bloqueio `unconfirmed_icms_tax_treatment`; ainda é possível gerar, baixar e
validar o XML não assinado, mas não assiná-lo ou transmiti-lo.

Altere o booleano para `true` somente após a equipe fiscal confirmar o CST e o
fundamento aplicável à operação de entrada. A confirmação da NF-e de saída não
deve ser reaproveitada automaticamente, pois saída temporária e retorno de
importação são fatos fiscais distintos.

## 6.1. CSTs com redução e substituição tributária

O motor também aceita os grupos do regime normal `10`, `20`, `30`, `60` e
`70`. Eles permanecem em modo diagnóstico até que a regra informe
`icms_tax_treatment_confirmed=true` após homologação fiscal.

| CST | Tratamento | Parâmetros adicionais obrigatórios |
| --- | --- | --- |
| `10` | ICMS próprio e ICMS-ST | `icms_rate`, `icms_st_rate` e modalidade ST |
| `20` | redução da base própria | `icms_rate` e `icms_base_reduction_rate` |
| `30` | isenção/não tributação com ICMS-ST | `icms_st_rate` e modalidade ST |
| `60` | ICMS-ST retido anteriormente | base, alíquota e valor retidos |
| `70` | redução da base própria e ICMS-ST | parâmetros dos CSTs `20` e `10` |

Para a base de ICMS-ST, esta etapa implementa `icms_st_base_method=4` (MVA) e
`6` (valor da operação). A modalidade `4` exige `icms_st_mva_rate`; ambas
aceitam `icms_st_base_reduction_rate`. O valor de ST devido é a diferença não
negativa entre o imposto calculado sobre a base ST e o ICMS próprio do item.

Na CST `60`, os campos `icms_st_retained_base`, `icms_st_retained_rate` e
`icms_st_retained_value` são reproduzidos no XML como dados da retenção
anterior. Eles não entram novamente em `vST` nem no valor total da nota.

O bloqueio `unconfirmed_icms_cst_calculation` impede assinatura e futura
transmissão enquanto qualquer item desses CSTs estiver sem confirmação. A
validação XSD garante somente a estrutura do XML; MVA, redução, alíquota,
benefício e aplicabilidade por UF/NCM precisam ser confrontados com documentos
reais e aprovados pela equipe fiscal antes do uso oficial.

Após o novo `duimp/fetch`, a normalização também:

- consulta o CCT pelo AWB do documento de instrução tipo `30`, antes da RUC;
- deriva adição e sequência a partir de `dadosGerais.adicoes`;
- normaliza `UNIDADE` para `UN`;
- conserva a descrição CATP completa em `infAdProd` quando `xProd` ultrapassa
  120 caracteres;
- aplica `cFabricante=0000` apenas quando o Portal retornou explicitamente o
  fabricante sem código, registrando a origem do fallback e um aviso.

## 7. DUIMP 26BR0000704908-1 — HAFELE

O snapshot dessa DUIMP comprovou oito itens e os mesmos totais federais do XML
autorizado usado como comparação: II `2691.98`, IPI `810.52`, PIS `353.57` e
COFINS `1624.69`. A normalização passa a discriminar os componentes fiscais da
operação em vez de registrar todo o valor aduaneiro como `vProd`:

- mercadorias: `14873.13`;
- frete: `1945.27`;
- seguro: `17.76`;
- valor aduaneiro: `16836.17`;
- taxa de utilização Siscomex: `285.34`.

Os valores atuais do Portal diferem em poucos centavos dos usados na NF-e
histórica. A reconciliação deve registrar essa diferença, sem substituir os
valores oficiais atuais pelo conteúdo da nota de comparação.

Após atualizar a API, execute novamente `duimp/fetch` para criar um snapshot
normalizado com a decomposição nova. Depois persista o contexto:

```http
POST /import-processes/93210732-52a9-4425-ab5d-cb5ae29396e8/nfe-context/resolve
Content-Type: application/json
```

```json
{
  "duimp_snapshot_id": "NOVO_UUID_DO_SNAPSHOT",
  "import_purpose": "resale",
  "refresh_external": false,
  "overrides": {
    "clearance_date": "2026-05-27",
    "transport_mode_code": "1"
  }
}
```

A unidade `0917900` é resolvida para `TCP - TERMINAL / PR` e o país `DE` para
`0230 / ALEMANHA`. A data de desembaraço permanece override explícito porque o
retorno consultado não a forneceu e o histórico e a NF-e de comparação não
coincidem entre si.

Cadastre a regra de diagnóstico do cliente:

```http
POST /clients/08e19c74-8815-4eee-ab0d-42a0cbbaf312/import-tax-rules
Content-Type: application/json
```

```json
{
  "name": "PR - revenda direta - redução integral diagnóstico",
  "issuer_state": "PR",
  "import_purpose": "resale",
  "import_modality": "direct",
  "tax_regime": "3",
  "priority": 100,
  "configuration_json": {
    "icms_origin": "1",
    "icms_cst": "51",
    "icms_base_method": "3",
    "icms_base_reduction_rate": "100",
    "icms_base_allocation": "per_item",
    "icms_benefit_code": "PR839999",
    "ipi_cst": "00",
    "ipi_zero_rate_cst": "01",
    "ipi_enquiry_code": "999",
    "pis_cst": "99",
    "cofins_cst": "99"
  },
  "transport_defaults": {
    "freight_mode": "1"
  },
  "payment_defaults": {
    "method": "90",
    "value": "0.00"
  },
  "active": true,
  "effective_from": "2026-01-01"
}
```

Essa regra produz `ICMS51` com `pRedBC=100`, omite os valores nominais de ICMS,
leva `PR839999` para `cBenef`, usa `IPITrib/00` nos itens tributados e `IPINT/01`
no item com alíquota zero. A transmissão permanece bloqueada por
`missing_nominal_icms_rate`; o XML não assinado e a validação XSD continuam
disponíveis.

Crie o draft usando somente o novo snapshot e a regra persistida:

```json
{
  "environment": "homologation",
  "series": "1",
  "import_purpose": "resale",
  "duimp_snapshot_id": "NOVO_UUID_DO_SNAPSHOT"
}
```

O CEST `1007600` visto no XML histórico não é inferido apenas pelo NCM. Se a
equipe fiscal confirmar que ele se aplica ao item `342.79.752`, use o endpoint
de edição do item do draft antes de gerar a chave de acesso.

## Referências oficiais

- [CCT Importação — consultas e operações](https://docs.portalunico.siscomex.gov.br/api/ccta/consultas/)
- [Pagamento Centralizado do Comércio Exterior (PCCE)](https://docs.portalunico.siscomex.gov.br/api/pcce/)
- [Tabelas Comex (TABX)](https://docs.portalunico.siscomex.gov.br/api/tabx/)
