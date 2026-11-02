-- Catálogo operacional da conta; identity_users é excluída da portabilidade.
-- Apenas referências: o segredo continua exclusivamente no cofre.
ALTER TABLE identity_users ADD COLUMN llm_credentials_json TEXT NOT NULL DEFAULT '[]'
    CHECK(json_valid(llm_credentials_json) AND json_type(llm_credentials_json) = 'array');

UPDATE identity_users SET llm_credentials_json = (
    SELECT coalesce(json_group_array(json(credential)), '[]') FROM (
        SELECT DISTINCT json_object(
            'id', json_extract(model_config_json, '$.secret_ref'),
            'kind', json_extract(model_config_json, '$.kind'),
            'endpoint', rtrim(json_extract(model_config_json, '$.endpoint'), '/')
        ) AS credential
        FROM agents
        WHERE json_extract(model_config_json, '$.secret_ref') LIKE 'vault:%'
          AND json_extract(model_config_json, '$.kind') = 'openai_compatible'
          AND json_type(model_config_json, '$.endpoint') = 'text'
    )
);
