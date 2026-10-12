BEGIN;

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS dw;
CREATE SCHEMA IF NOT EXISTS mart;

COMMIT;

SELECT schema_name
FROM information_schema.schemata
WHERE schema_name IN ('staging', 'dw', 'mart')
ORDER BY schema_name;