-- Relação 1:1 entre clients e scopes.
--
-- Este script não altera escopos com client_id NULL. O PostgreSQL permite
-- múltiplos valores NULL em uma constraint UNIQUE comum.
--
-- Execute somente depois de confirmar que a consulta de duplicidades retorna
-- zero linhas. O bloco também repete essa validação e interrompe a operação se
-- encontrar algum conflito.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM scopes
        WHERE client_id IS NOT NULL
        GROUP BY client_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Não foi possível criar uq_scopes_client_id: existem clientes vinculados a mais de um escopo.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_scopes_client_id'
          AND conrelid = 'scopes'::regclass
    ) THEN
        ALTER TABLE scopes
            ADD CONSTRAINT uq_scopes_client_id UNIQUE (client_id);
    END IF;
END
$$;

-- Rollback manual, se necessário:
-- ALTER TABLE scopes DROP CONSTRAINT IF EXISTS uq_scopes_client_id;
