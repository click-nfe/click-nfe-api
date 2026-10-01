# Checkpoint SEFAZ — NF-e modelo 55

O serviço envia uma NF-e filha assinada por lote assíncrono (`indSinc=0`). Cada POST salva uma tentativa antes da chamada externa. Resposta ambígua, falha de rede ou queda do processo bloqueia novo POST: use a consulta de recibo ou da chave para reconciliar. Não há repetição automática da autorização.

## Configuração

A transmissão inicia **desabilitada**. A migration `e62a7b9c104d` cria `sefaz_endpoints` e carrega os 27 cUFs com as três URLs HTTPS de NF-e modelo 55. A carga é um snapshot local da [relação oficial de serviços](https://dfe-portal.svrs.rs.gov.br/Nfe/Servicos) e da [relação de UFs por autorizador](https://www.nfe.fazenda.gov.br/portal/webServices.aspx?tipoConteudo=OUC%2FYVNWZfo%3D), consultadas em 2026-10-01. Ceará usa a SVRS, conforme [SEFAZ-CE](https://nfe.sefaz.ce.gov.br/pages/informacoes/web_services.jsf); Maranhão usa a SVAN. A migration é executada pelo serviço de migração no deploy, antes de ligar a transmissão. Para ajustes posteriores, altere as linhas da tabela ou aplique um override pontual no ambiente. Exemplo opcional para PR:

```dotenv
NFE_SEFAZ_PRODUCTION_ENDPOINTS_JSON={"41":{"authorization":"https://nfe.sefa.pr.gov.br/nfe/NFeAutorizacao4","receipt":"https://nfe.sefa.pr.gov.br/nfe/NFeRetAutorizacao4","protocol":"https://nfe.sefa.pr.gov.br/nfe/NFeConsultaProtocolo4"}}
NFE_SEFAZ_TRANSMISSION_ENABLED=false
```

`NFE_SEFAZ_PRODUCTION_ENDPOINTS_JSON` é opcional e sobrescreve campos dos cUFs ativos no catálogo. Não reutilize endpoints de NFC-e. A API não consulta WSDL automaticamente; confirme o endpoint e o credenciamento da UF antes do primeiro envio real. O A1 ativo vinculado à emissão é resolvido pelo cofre já existente e usado também no TLS mútuo. O XML assinado é verificado novamente antes da transmissão. Após revisar a emissão, habilite `NFE_SEFAZ_TRANSMISSION_ENABLED=true`.

## Fluxo por filha

- `GET /nfe-drafts/{id}/sefaz`: situação, últimas respostas, orientação e tentativas, sem material do A1.
- `POST /nfe-drafts/{id}/sefaz/transmit`: somente admin e estado `signed`; pode resultar em autorização, rejeição, recibo ou resultado incerto. **Não repetir este POST após erro de comunicação.**
- `POST /nfe-drafts/{id}/sefaz/reconcile`: consulta recibo quando disponível e chave nos demais casos. Uma consulta `217` não autoriza novo envio automático.
- Com o painel aberto por um administrador, um envio pendente dispara consultas de acompanhamento em intervalos crescentes, com limite. Fechar o painel interrompe as consultas; o estado persistido permite retomá-las ao abrir novamente. A consulta manual continua disponível.
- `GET /nfe-drafts/{id}/sefaz/authorized-xml`: `nfeProc` com protocolo, apenas após autorização.
- `GET /nfe-drafts/{id}/sefaz/danfe`: PDF do DANFE por NF-e filha autorizada, com chave e protocolo conferidos com o `nfeProc` persistido. Aceita modelo 55, retrato, produção e emissão normal; responde 400 antes da autorização ou para outro leiaute.

O PDF da etapa de assinatura continua identificado como prévia sem valor fiscal. O DANFE após autorização é renderizado com BrazilFiscalReport 1.1.1 a partir do XML autorizado; as especificações do MOC 7.0, Anexo II, devem ser confrontadas com uma emissão representativa antes do uso operacional. Cancelamento, carta de correção, contingência, inutilização e recuperação de rejeições com uma nova versão fiscal continuam pendentes. O transporte não foi testado contra a SEFAZ real; testes automatizados usam respostas e conexão simuladas. A migration foi criada para revisão; não foi executada neste trabalho.

Referência fiscal: Portal Nacional da NF-e, Documentos > Manuais > MOC 7.0 > Anexo II — Manual de Especificações Técnicas do DANFE e Código de Barras (https://www.nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=ndIjl+iEFdE%3D).
