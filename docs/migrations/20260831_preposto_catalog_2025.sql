-- Estrutura necessária para tarifas condicionais e despachantes credenciados.
--
-- Este script cria somente tabelas e índices. A carga aprovada da Relação 2025
-- é executada separadamente pelo comando:
--
--   flask preposto-catalog import-2025 <ARQUIVO_PRIVADO.json> --organization-id <UUID>
--   flask preposto-catalog import-2025 <ARQUIVO_PRIVADO.json> --organization-id <UUID> --apply
--
-- Execute primeiro em homologação e mantenha o resultado do dry-run para
-- comparar com o retorno da execução efetiva.

BEGIN;

CREATE TABLE IF NOT EXISTS preposto_tarifas (
    id UUID PRIMARY KEY,
    localidade_id UUID NOT NULL REFERENCES preposto_localidades(id) ON DELETE CASCADE,
    codigo VARCHAR(64) NOT NULL,
    operacao VARCHAR(20) NOT NULL,
    tipo VARCHAR(64) NOT NULL,
    valor NUMERIC(12, 2),
    valor_descricao VARCHAR(255),
    condicao VARCHAR(500),
    principal BOOLEAN NOT NULL DEFAULT FALSE,
    moeda VARCHAR(8) NOT NULL DEFAULT 'BRL',
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    observacoes TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    CONSTRAINT uq_preposto_tarifa_localidade_codigo
        UNIQUE (localidade_id, codigo),
    CONSTRAINT ck_preposto_tarifa_operacao
        CHECK (operacao IN ('IMPORTACAO', 'EXPORTACAO'))
);

CREATE INDEX IF NOT EXISTS ix_preposto_tarifas_localidade_id
    ON preposto_tarifas (localidade_id);
CREATE INDEX IF NOT EXISTS ix_preposto_tarifas_operacao
    ON preposto_tarifas (operacao);
CREATE INDEX IF NOT EXISTS ix_preposto_tarifas_operacao_ativo
    ON preposto_tarifas (operacao, ativo);

CREATE TABLE IF NOT EXISTS preposto_credenciados (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    nome VARCHAR(255) NOT NULL,
    cpf VARCHAR(11) NOT NULL,
    registro_rfb VARCHAR(32),
    categoria VARCHAR(20) NOT NULL DEFAULT 'DESPACHANTE',
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    observacoes TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    CONSTRAINT uq_preposto_credenciado_organization_cpf
        UNIQUE (organization_id, cpf),
    CONSTRAINT ck_preposto_credenciado_categoria
        CHECK (categoria IN ('DESPACHANTE', 'AJUDANTE')),
    CONSTRAINT ck_preposto_credenciado_cpf
        CHECK (cpf ~ '^[0-9]{11}$')
);

CREATE INDEX IF NOT EXISTS ix_preposto_credenciados_organization_id
    ON preposto_credenciados (organization_id);
CREATE INDEX IF NOT EXISTS ix_preposto_credenciados_nome
    ON preposto_credenciados (nome);

CREATE TABLE IF NOT EXISTS preposto_credenciado_vinculos (
    id UUID PRIMARY KEY,
    credenciado_id UUID NOT NULL REFERENCES preposto_credenciados(id) ON DELETE CASCADE,
    preposto_id UUID NOT NULL REFERENCES prepostos(id) ON DELETE CASCADE,
    localidade_id UUID NOT NULL REFERENCES preposto_localidades(id) ON DELETE CASCADE,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    observacoes TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    CONSTRAINT uq_preposto_credenciado_vinculo
        UNIQUE (credenciado_id, preposto_id, localidade_id)
);

CREATE INDEX IF NOT EXISTS ix_preposto_credenciado_vinculos_credenciado_id
    ON preposto_credenciado_vinculos (credenciado_id);
CREATE INDEX IF NOT EXISTS ix_preposto_credenciado_vinculos_preposto_id
    ON preposto_credenciado_vinculos (preposto_id);
CREATE INDEX IF NOT EXISTS ix_preposto_credenciado_vinculos_localidade_id
    ON preposto_credenciado_vinculos (localidade_id);

COMMIT;

-- Rollback manual (somente se nenhuma aplicação já depender dessas tabelas):
-- DROP TABLE IF EXISTS preposto_credenciado_vinculos;
-- DROP TABLE IF EXISTS preposto_credenciados;
-- DROP TABLE IF EXISTS preposto_tarifas;
