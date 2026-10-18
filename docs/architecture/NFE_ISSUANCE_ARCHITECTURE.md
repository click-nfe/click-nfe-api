# Arquitetura da emissão de NF-e de importação

Status: checkpoint 3 - XML assinado com A1 e validado no XSD oficial.

## Objetivo

Emitir NF-e de entrada de importação a partir de uma DUIMP registrada, usando
certificado A1 do cliente e os serviços oficiais do Portal Único e da SEFAZ.

O piloto de homologação será uma importação própria com emitente do Paraná. Os
documentos reais usados para conferência não fazem parte do repositório.

## Limites dos componentes

1. `PortalUnicoDuimpGateway`: consulta e captura imutável da DUIMP.
2. `ImportTaxCalculator`: produz valores fiscais revisáveis e determinísticos.
3. `NfeXmlBuilder`: monta versões de XML; não acessa certificados nem a rede.
4. `NfeXsdValidator`: valida contra o pacote oficial versionado
   `PL_010e_v1.02`, com suporte aos grupos IBS/CBS.
5. `CertificateVault`: entrega PKCS#12 e senha somente ao processo assinador,
   a partir de referências versionadas.
6. `NfeXmlSigner`: confere CNPJ, validade e uso da chave, assina e verifica a
   XMLDSig antes de persistir a versão `signed`.
7. `SefazGateway`: futura interface SOAP/mTLS com resolução de autorizador por
   UF e ambiente.

## Regras invariantes

- Homologação e produção nunca compartilham certificado, sequência, endpoint,
  idempotency key ou registro de emissão.
- Uma chave de idempotência não pode representar dois conteúdos diferentes.
- A combinação emitente, ambiente, modelo, série e número é única.
- XML só pode ser assinado depois de aprovado no XSD.
- O CNPJ do certificado deve ser compatível com o emitente.
- `authorized`, `denied` e `cancelled` não podem voltar ao fluxo de edição.
- Timeout após envio não autoriza reenvio imediato: primeiro é obrigatória a
  reconciliação por recibo, protocolo ou chave.
- XML, protocolo e eventos são versionados; não são sobrescritos.
- Senha e bytes do certificado nunca são gravados no banco ou nos logs.

## Máquina de estados

```text
draft
  -> validated
  -> xml_generated
  -> xsd_validated
  -> signed
  -> submission_pending
  -> submitted
  -> processing
  -> authorized
  -> cancellation_pending
  -> cancelled
```

Ramificações controladas:

- `xml_generated -> validation_failed -> draft`
- `submitted|processing -> rejected -> draft`
- `submitted|processing -> denied` (terminal)
- falhas técnicas antes de uma resposta conclusiva vão para `failed` e exigem
  reconciliação operacional; não existe retry genérico.

## Persistência

- `fiscal_certificates`: somente referências e metadados do A1.
- `nfe_issuances`: identidade fiscal, estado e idempotência.
- `nfe_issuance_attempts`: cada chamada XSD, assinatura ou SEFAZ.
- `nfe_issuance_events`: trilha imutável de transições.
- `nfe_protocols`: respostas oficiais e checksums.

Os payloads SOAP não devem ser registrados em logs genéricos. Respostas fiscais
que precisem ser preservadas pertencem a `nfe_protocols`, com controle de acesso.

## Isolamento no mesmo projeto GCP

Embora o projeto GCP seja compartilhado, os recursos devem ser separados:

| Recurso | Homologação | Produção |
|---|---|---|
| Banco/schema | `nfe_homologation` | `nfe_production` |
| Service account | `nfe-homologation-sa` | `nfe-production-sa` |
| Secret prefix | `nfe-hom-*` | `nfe-prod-*` |
| Bucket, se necessário | `*-nfe-hom-certificates` | `*-nfe-prod-certificates` |
| Logs | label `environment=homologation` | label `environment=production` |
| Filas | `nfe-hom-*` | `nfe-prod-*` |

Regras mínimas:

- service account de homologação não acessa secrets de produção;
- aplicação web não recebe `secretAccessor`; somente o serviço assinador;
- produção exige proteção contra exclusão e auditoria de acesso;
- o PFX fica em objeto privado no Cloud Storage e a senha em versão do Secret Manager;
- referência do secret inclui versão ou alias controlado;
- upload valida tamanho, PKCS#12, CNPJ e validade antes de ativar;
- nenhum endpoint retorna `certificate_ref` completo a usuários comuns.

## Estratégia de branches e ambientes

- `feat/nfe-issuance-pipeline`: implementação incremental.
- testes unitários: sem rede e sem certificado real;
- testes de contrato: fixtures sanitizadas;
- homologação: certificado e endpoints SEFAZ de homologação;
- produção: habilitada somente após checklist e canário manual.

O primeiro PR desta branch será empilhado sobre `feat/nfe-xml-api`. Depois que o
PR base for integrado, o PR de emissão poderá ser redirecionado para `main`.
