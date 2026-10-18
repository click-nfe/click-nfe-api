# Modelo de ameaças do certificado A1

## Ativos protegidos

- arquivo PKCS#12 (`.pfx`/`.p12`);
- senha do PKCS#12;
- XML assinado;
- identidade e CNPJ do titular;
- referência e versão dos secrets;
- protocolos e eventos autorizados.

## Ameaças e controles

| Ameaça | Controle obrigatório |
|---|---|
| Download indevido do A1 | Secret Manager/IAM mínimo e sem endpoint de download |
| Aplicação web lê certificados | service account exclusiva para o assinador |
| Secret de homologação usado em produção | IAM, prefixos e configuração separados |
| Senha aparece em log | redaction e exceções sanitizadas |
| Certificado de outro cliente | conferir CNPJ do certificado contra o emitente |
| Certificado vencido ou revogado | validação antes de cada assinatura e monitor de validade |
| XML alterado após assinatura | checksum antes/depois e verificação XMLDSig |
| Reenvio depois de timeout | reconciliação obrigatória antes de novo envio |
| Exclusão ou troca silenciosa | versões, audit logs e fingerprint SHA-256 |
| Processo comprometido mantém bytes | carregar sob demanda e limitar o tempo em memória |

## Armazenamento

### Desenvolvimento local

O upload local usa `LocalEncryptedFileCertificateVault`. Certificado e senha
são criptografados separadamente com Fernet, recebem nomes aleatórios e ficam no
volume privado `click_nfe_certificate_data`. O PostgreSQL guarda apenas
referências `local:`, fingerprint, titular e vigência.

- nenhuma rota permite download;
- o nome original do arquivo não é preservado;
- referências são validadas para impedir path traversal;
- arquivos temporários são substituídos atomicamente e usam permissão `0600`;
- senha e PKCS#12 são validados antes da gravação;
- falhas de cadastro removem os arquivos recém-criados;
- o provider local não deve ser utilizado em produção.

### Produção

O arquivo PFX/P12 fica em objeto privado no Cloud Storage, com referência `gcs:`
que inclui a geração. Sua senha fica em uma versão do secret preexistente
`click-nfe-a1-passwords`, referenciada como `gcp:NOME@VERSAO`. O limite de
64 KiB por versão no Secret Manager impede armazenar ali todos os A1 aceitos
pelo formulário (até 2 MB). O bucket utiliza acesso uniforme e bloqueio
público. O banco guarda somente referências e metadados, não a senha.

## Acesso

No checkpoint atual, todas as mutações de certificado e a assinatura exigem o
papel `admin`. A separação abaixo deverá ser aplicada quando os papéis fiscais
específicos forem introduzidos:

- operador cadastra uma nova versão sem conseguir emitir;
- aprovador ativa o certificado depois da validação;
- assinador apenas acessa a versão ativa;
- aplicação e frontend nunca acessam o conteúdo;
- auditor consulta metadados e logs, não o A1.

## Rotação e expiração

- alertar antes do vencimento;
- permitir duas versões durante rotação;
- uma única versão ativa por cliente e ambiente;
- desativar a versão anterior após teste da nova;
- preservar fingerprint e período de uso para auditoria;
- revogação bloqueia novas assinaturas imediatamente.
