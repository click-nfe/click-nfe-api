# Armazenamento fiscal persistente no Cloud Run

Este checkpoint permite cadastrar A1 de clientes e credenciais do Portal Único pelos formulários existentes. O Cloud Storage guarda os bytes originais do PFX/P12 em objetos privados; o Secret Manager guarda a senha do A1 e o par de chaves do Portal Único em **dois secrets preexistentes**. Cada cadastro recebe uma versão numérica própria. O PostgreSQL armazena somente referências e metadados.

O limite por versão do Secret Manager é 64 KiB, enquanto o formulário A1 permite arquivos de até 2 MB. Por isso o PFX não é gravado em um secret. Não faça upload do certificado ou digite senhas no Cloud Shell.

## 1. Preparar recursos antes de aprovar a próxima build

Execute no Cloud Shell do projeto `click-nfe-project`:

```bash
gcloud config set project click-nfe-project
gcloud services enable storage.googleapis.com secretmanager.googleapis.com

gcloud storage buckets create gs://click-nfe-project-prod-a1-881716800915 \
  --project=click-nfe-project --location=southamerica-east1 \
  --default-storage-class=STANDARD \
  --uniform-bucket-level-access --public-access-prevention

gcloud secrets create click-nfe-a1-passwords --replication-policy=automatic
gcloud secrets create click-nfe-portal-unico-credentials --replication-policy=automatic
```

Os secrets começam **sem versões**. A API cria uma nova versão somente quando um administrador faz o cadastro no painel. Se o nome global do bucket estiver ocupado, escolha outro nome e configure a substituição `_CERTIFICATE_BUCKET` na trigger `click-nfe-api-main` com o mesmo valor, antes de aprovar a build.

Conceda à identidade **da API**, não à conta de build nem à Vercel, as permissões para usar apenas esses recursos:

```bash
gcloud storage buckets add-iam-policy-binding gs://click-nfe-project-prod-a1-881716800915 \
  --member='serviceAccount:click-nfe-runtime@click-nfe-project.iam.gserviceaccount.com' \
  --role='roles/storage.objectAdmin'

for CLICK_NFE_SECRET in click-nfe-a1-passwords click-nfe-portal-unico-credentials; do
  gcloud secrets add-iam-policy-binding "$CLICK_NFE_SECRET" \
    --member='serviceAccount:click-nfe-runtime@click-nfe-project.iam.gserviceaccount.com' \
    --role='roles/secretmanager.secretAccessor'
  gcloud secrets add-iam-policy-binding "$CLICK_NFE_SECRET" \
    --member='serviceAccount:click-nfe-runtime@click-nfe-project.iam.gserviceaccount.com' \
    --role='roles/secretmanager.secretVersionManager'
done
```

Não conceda `roles/secretmanager.admin` ao projeto todo. A versão manager é necessária para adicionar versões e destruir uma versão recém-criada quando o cadastro falhar; accessor é necessária para validação, assinatura e DUIMP. A API não lista nem devolve bytes dos objetos ou os segredos ao navegador.

## 2. Publicar a API

O `cloudbuild.yaml` configura `GCP_PROJECT_ID=click-nfe-project`, `NFE_CERTIFICATE_STORAGE_PROVIDER=gcp_cloud_storage`, `NFE_CERTIFICATE_BUCKET`, `PORTAL_UNICO_CREDENTIAL_STORAGE_PROVIDER=gcp_secret_manager` e mantém os secrets existentes de banco e `SECRET_KEY`. A migration continua antes do deploy; este checkpoint não adiciona tabelas. Aprove a build somente após os recursos e IAM acima estarem prontos.

Confirme que a nova revisão do Cloud Run está pronta e que `/health/ready` responde HTTP 200. Se a trigger usar outro nome de bucket, verifique que `_CERTIFICATE_BUCKET` coincide exatamente com o bucket criado. A variável `NFE_SEFAZ_TRANSMISSION_ENABLED` continua desligada.

## 3. Testar pelo painel

1. Na configuração do Portal Único, informe o Client ID e o Client Secret existentes e teste a conexão. Cada rotação recebe uma versão nova **atômica** do par; a versão anterior é mantida para recuperação e não é usada pelo registro ativo.
2. No perfil de um cliente com CNPJ e perfil fiscal, envie o A1 e a senha. A API valida o conteúdo e o CNPJ antes de gravar; depois valide e ative o registro.
3. Consulte o Portal Único, reinicie uma revisão da API e repita as verificações de credenciais e certificado. Verifique que o banco contém referências `gcp-managed:` (Portal), `gcs:` (A1) e `gcp:` (senha), sem valores secretos.

Um registro com referência `local:` anterior não será migrado automaticamente. Cadastre as credenciais novamente pelo painel e reenvie o A1 a partir do arquivo original, sem copiar o volume local para o Cloud Run. As versões antigas de secrets e objetos fiscais não devem ser removidas sem uma política de retenção e conferência de referências.

## Limites e segurança

- Apenas administradores cadastram, validam e ativam certificados ou alteram as credenciais do Portal Único. O bucket bloqueia acesso público; permissões são concedidas no próprio bucket e nos dois secrets.
- O projeto usa uma identidade de execução para a API. Ela acessa os dados fiscais das organizações através dos recursos compartilhados; o isolamento entre organizações depende também das validações da aplicação. Uma separação por serviço e identidade pode ser introduzida depois.
- Em falha após a gravação externa, a API tenta excluir o objeto e destruir a versão nova. Se uma dessas operações de compensação falhar, pode sobrar um recurso órfão; monitore erros e faça reconciliação pelo identificador opaco do recurso, sem acessar ou registrar o payload.
- A ativação do certificado não habilita por si só a transmissão. Faça a assinatura e a reconciliação em produção com cuidado antes de ligar a flag SEFAZ.
