# Checkpoint SEFAZ — NF-e modelo 55

O serviço envia uma NF-e filha assinada por lote assíncrono (`indSinc=0`). Cada POST salva uma tentativa antes da chamada externa. Resposta ambígua, falha de rede ou queda do processo bloqueia novo POST: use a consulta de recibo ou da chave para reconciliar. Não há repetição automática da autorização.

## Configuração

A transmissão inicia **desabilitada**. Defina `NFE_SEFAZ_PRODUCTION_ENDPOINTS_JSON` por `cUF` do emitente com as três URLs HTTPS de NF-e modelo 55 e, somente quando o fluxo fiscal estiver revisado, `NFE_SEFAZ_TRANSMISSION_ENABLED=true`. Exemplo para PR, conforme a [relação oficial da SVRS](https://dfe-portal.svrs.rs.gov.br/Nfe/Servicos):

```dotenv
NFE_SEFAZ_PRODUCTION_ENDPOINTS_JSON={"41":{"authorization":"https://nfe.sefa.pr.gov.br/nfe/NFeAutorizacao4","receipt":"https://nfe.sefa.pr.gov.br/nfe/NFeRetAutorizacao4","protocol":"https://nfe.sefa.pr.gov.br/nfe/NFeConsultaProtocolo4"}}
NFE_SEFAZ_TRANSMISSION_ENABLED=false
```

Não reutilize endpoints de NFC-e. A API não consulta WSDL automaticamente; URLs de produção devem ser conferidas antes do primeiro envio. O A1 ativo vinculado à emissão é resolvido pelo cofre já existente e usado também no TLS mútuo. O XML assinado é verificado novamente antes da transmissão.

## Fluxo por filha

- `GET /nfe-drafts/{id}/sefaz`: situação e tentativas, sem material do A1.
- `POST /nfe-drafts/{id}/sefaz/transmit`: somente admin e estado `signed`; pode resultar em autorização, rejeição, recibo ou resultado incerto. **Não repetir este POST após erro de comunicação.**
- `POST /nfe-drafts/{id}/sefaz/reconcile`: consulta recibo quando disponível e chave nos demais casos. Uma consulta `217` não autoriza novo envio automático.
- `GET /nfe-drafts/{id}/sefaz/authorized-xml`: `nfeProc` com protocolo, apenas após autorização.

O PDF da etapa de assinatura continua identificado como prévia sem valor fiscal. O layout do DANFE definitivo, cancelamento, carta de correção, contingência, inutilização e recuperação de rejeições com uma nova versão fiscal exigem entregas específicas antes da operação completa. O transporte não foi testado contra a SEFAZ real; testes automatizados usam respostas e conexão simuladas. Não há migration neste PR.
