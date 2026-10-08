-- =====================================================================
-- Asistente GPS - esquema inicial (PostgreSQL 15+ con pgvector)
-- Principios:
--  * El conocimiento del fabricante es GLOBAL y compartido entre empresas.
--  * Lo de cada empresa (usuarios, chats, contenido propio) lleva company_id + RLS.
--  * Todo dato extraido guarda su procedencia (documento, revision, seccion).
--  * Comandos y parametros solo se usan en produccion si status = 'published'.
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE review_status AS ENUM ('draft', 'verified', 'published', 'deprecated');
CREATE TYPE channel_type  AS ENUM ('sms', 'gprs', 'configurator');
CREATE TYPE risk_level    AS ENUM ('low', 'medium', 'high');
CREATE TYPE source_kind   AS ENUM ('wiki', 'pdf');
CREATE TYPE step_kind     AS ENUM ('instruction', 'command', 'verify', 'warning');

-- ---------- Catalogo de dispositivos (global) ------------------------
CREATE TABLE manufacturer (
  id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL UNIQUE
);

CREATE TABLE device_family (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  manufacturer_id uuid NOT NULL REFERENCES manufacturer(id),
  code            text NOT NULL,          -- p.ej. 'FMB'
  name            text,
  UNIQUE (manufacturer_id, code)
);

CREATE TABLE device_model (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  family_id  uuid NOT NULL REFERENCES device_family(id),
  code       text NOT NULL,               -- p.ej. 'FMB920'
  wiki_title text,
  is_eol     boolean NOT NULL DEFAULT false,
  UNIQUE (family_id, code)
);

-- ---------- Fuentes y documentos (global) ----------------------------
CREATE TABLE source (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind         source_kind NOT NULL,
  name         text NOT NULL UNIQUE,
  base_url     text,
  license_note text,                      -- estado de permisos/licencia
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE source_document (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id    uuid NOT NULL REFERENCES source(id),
  external_id  text NOT NULL,             -- titulo de pagina wiki o nombre de PDF
  url          text,
  title        text,
  breadcrumb   text[] NOT NULL DEFAULT '{}',
  language     text NOT NULL DEFAULT 'en',
  revision_id  bigint,                    -- ID de revision de MediaWiki (null en PDF)
  content_hash text NOT NULL,
  raw_path     text,                      -- original crudo guardado
  is_current   boolean NOT NULL DEFAULT true,
  fetched_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX source_document_current_uq
  ON source_document (source_id, external_id) WHERE is_current;
CREATE UNIQUE INDEX source_document_version_uq
  ON source_document (source_id, external_id, content_hash);

CREATE TABLE firmware_version (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id           uuid NOT NULL REFERENCES device_model(id),
  version            text NOT NULL,       -- se normaliza cuando veamos el formato real
  released_on        date,
  source_document_id uuid REFERENCES source_document(id),
  UNIQUE (model_id, version)
);

-- ---------- Capa documental (RAG) ------------------------------------
CREATE TABLE chunk (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id  uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  ordinal      int  NOT NULL,
  section_path text[] NOT NULL,
  content      text NOT NULL,
  content_tsv  tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  embedding    vector(1024),              -- ajustar a la dimension del modelo elegido
  UNIQUE (document_id, ordinal)
);
CREATE INDEX chunk_tsv_idx ON chunk USING gin (content_tsv);
CREATE INDEX chunk_embedding_idx ON chunk USING hnsw (embedding vector_cosine_ops);

-- A que modelos aplica cada fragmento. NUNCA se infiere por menciones en el texto.
CREATE TABLE chunk_applicability (
  chunk_id     uuid NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
  model_id     uuid NOT NULL REFERENCES device_model(id),
  firmware_min text,
  firmware_max text,
  basis        text NOT NULL CHECK (basis IN ('breadcrumb', 'family_page', 'manual')),
  status       review_status NOT NULL DEFAULT 'draft',
  PRIMARY KEY (chunk_id, model_id)
);

-- Tablas del documento conservadas como datos (para extraer comandos sin perdida)
CREATE TABLE document_table (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id  uuid NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
  ordinal      int  NOT NULL,
  section_path text[] NOT NULL,
  headers      jsonb NOT NULL,
  rows         jsonb NOT NULL,
  UNIQUE (document_id, ordinal)
);

-- ---------- Capa estructurada (comandos y parametros) ---------------
CREATE TABLE command_definition (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  family_id          uuid NOT NULL REFERENCES device_family(id),
  name               text NOT NULL,       -- p.ej. 'getinfo', 'setdigout'
  description        text,
  syntax_template    text NOT NULL,
  channels           channel_type[] NOT NULL,
  risk               risk_level NOT NULL DEFAULT 'low',
  warning            text,                -- se muestra antes del boton de copiar
  status             review_status NOT NULL DEFAULT 'draft',
  source_document_id uuid REFERENCES source_document(id),
  source_section     text,
  UNIQUE (family_id, name)
);

CREATE TABLE command_parameter (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  command_id  uuid NOT NULL REFERENCES command_definition(id) ON DELETE CASCADE,
  position    int  NOT NULL,
  name        text NOT NULL,
  required    boolean NOT NULL DEFAULT true,
  validation  jsonb NOT NULL DEFAULT '{}',  -- {"type":"int","min":1,"max":65535}
  description text,
  UNIQUE (command_id, position)
);

CREATE TABLE command_applicability (
  command_id         uuid NOT NULL REFERENCES command_definition(id) ON DELETE CASCADE,
  model_id           uuid NOT NULL REFERENCES device_model(id),
  sms_supported      boolean,
  gprs_supported     boolean,
  firmware_min       text,
  firmware_max       text,
  source_document_id uuid REFERENCES source_document(id),
  status             review_status NOT NULL DEFAULT 'draft',
  PRIMARY KEY (command_id, model_id)
);

CREATE TABLE config_parameter (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  family_id          uuid NOT NULL REFERENCES device_family(id),
  param_id           int  NOT NULL,       -- ID numerico del parametro
  name               text NOT NULL,
  value_type         text,
  min_value          text,
  max_value          text,
  default_value      text,
  description        text,
  status             review_status NOT NULL DEFAULT 'draft',
  source_document_id uuid REFERENCES source_document(id),
  UNIQUE (family_id, param_id)
);

CREATE TABLE config_parameter_applicability (
  config_parameter_id uuid NOT NULL REFERENCES config_parameter(id) ON DELETE CASCADE,
  model_id            uuid NOT NULL REFERENCES device_model(id),
  firmware_min        text,
  firmware_max        text,
  status              review_status NOT NULL DEFAULT 'draft',
  PRIMARY KEY (config_parameter_id, model_id)
);

-- ---------- Guias paso a paso ---------------------------------------
CREATE TABLE task_template (
  id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code   text NOT NULL UNIQUE,            -- p.ej. 'set_apn_server'
  title  text NOT NULL,
  goal   text,
  status review_status NOT NULL DEFAULT 'draft'
);

CREATE TABLE task_applicability (
  task_id  uuid NOT NULL REFERENCES task_template(id) ON DELETE CASCADE,
  model_id uuid NOT NULL REFERENCES device_model(id),
  channel  channel_type NOT NULL,
  PRIMARY KEY (task_id, model_id, channel)
);

CREATE TABLE task_step (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id    uuid NOT NULL REFERENCES task_template(id) ON DELETE CASCADE,
  ordinal    int  NOT NULL,
  kind       step_kind NOT NULL,
  text       text NOT NULL,
  command_id uuid REFERENCES command_definition(id),
  params     jsonb,
  UNIQUE (task_id, ordinal)
);

CREATE TABLE review_log (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type text NOT NULL,
  entity_id   uuid NOT NULL,
  from_status review_status,
  to_status   review_status NOT NULL,
  reviewer    text NOT NULL,
  note        text,
  at          timestamptz NOT NULL DEFAULT now()
);

-- ---------- Datos por empresa (aislados con RLS) --------------------
CREATE TABLE company (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES company(id),
  email      text NOT NULL UNIQUE,
  role       text NOT NULL CHECK (role IN ('admin', 'editor', 'member')),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE company_content (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES company(id),
  title      text NOT NULL,
  body       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conversation (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES company(id),
  user_id    uuid NOT NULL REFERENCES app_user(id),
  model_id   uuid REFERENCES device_model(id),   -- NULL hasta que el usuario elige equipo; sin equipo no se genera respuesta tecnica
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE message (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL REFERENCES company(id),
  conversation_id uuid NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('user', 'assistant')),
  model_id        uuid REFERENCES device_model(id),  -- equipo vigente cuando se escribio
  content         text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- Que fuentes respaldaron cada respuesta (trazabilidad)
CREATE TABLE message_citation (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES company(id),
  message_id uuid NOT NULL REFERENCES message(id) ON DELETE CASCADE,
  chunk_id   uuid REFERENCES chunk(id),
  command_id uuid REFERENCES command_definition(id)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['app_user','company_content','conversation','message','message_citation']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (company_id = current_setting(''app.company_id'')::uuid)', t);
  END LOOP;
END $$;
-- La API debe ejecutar al inicio de cada peticion: SET LOCAL app.company_id = '<uuid>';

-- ---------- Evaluacion de precision ---------------------------------
CREATE TABLE eval_question (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id         uuid REFERENCES device_model(id),
  question         text NOT NULL,
  expected_answer  text,
  expected_command text,
  must_refuse      boolean NOT NULL DEFAULT false,  -- casos donde debe responder "no se"
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_run (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  git_sha    text,
  notes      text,
  started_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_result (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id      uuid NOT NULL REFERENCES eval_run(id) ON DELETE CASCADE,
  question_id uuid NOT NULL REFERENCES eval_question(id),
  answer      text,
  passed      boolean NOT NULL,
  cited_ok    boolean,
  notes       text
);

-- =====================================================================
-- Acceso por empresa: que marcas / familias / modelos puede usar cada cliente
--  Capa 1 (company_entitlement): lo CONTRATADO. Lo configura el proveedor (tu).
--  Capa 2 (company_device_selection): con que equipos trabaja la empresa HOY,
--          siempre dentro de lo contratado. Lo puede ajustar la propia empresa.
--  El filtro se aplica en las consultas del backend, no en el prompt del LLM.
-- =====================================================================
CREATE TABLE company_entitlement (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      uuid NOT NULL REFERENCES company(id),
  manufacturer_id uuid NOT NULL REFERENCES manufacturer(id),
  family_id       uuid REFERENCES device_family(id),   -- NULL = toda la marca
  model_id        uuid REFERENCES device_model(id),    -- NULL = toda la familia
  granted_by      text NOT NULL,
  granted_at      timestamptz NOT NULL DEFAULT now(),
  expires_at      timestamptz,
  CHECK (model_id IS NULL OR family_id IS NOT NULL)
);
CREATE UNIQUE INDEX company_entitlement_uq ON company_entitlement (
  company_id, manufacturer_id,
  COALESCE(family_id, '00000000-0000-0000-0000-000000000000'::uuid),
  COALESCE(model_id,  '00000000-0000-0000-0000-000000000000'::uuid)
);

ALTER TABLE company_entitlement ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_entitlement FORCE ROW LEVEL SECURITY;
CREATE POLICY entitlement_read ON company_entitlement FOR SELECT
  USING (company_id = current_setting('app.company_id')::uuid);
-- Sin politicas de escritura: la aplicacion NO puede cambiar permisos.
-- Solo la consola de administracion del proveedor (rol de BD con BYPASSRLS, nunca expuesto a clientes).

-- Expande marca/familia/modelo a la lista plana de modelos permitidos.
-- security_invoker: respeta RLS, cada empresa solo ve sus propios permisos.
CREATE VIEW company_visible_model WITH (security_invoker = true) AS
SELECT DISTINCT e.company_id, m.id AS model_id
FROM company_entitlement e
JOIN device_family f ON f.manufacturer_id = e.manufacturer_id
JOIN device_model  m ON m.family_id = f.id
WHERE (e.expires_at IS NULL OR e.expires_at > now())
  AND (   (e.model_id  IS NOT NULL AND m.id = e.model_id)
       OR (e.model_id  IS NULL AND e.family_id IS NOT NULL AND f.id = e.family_id)
       OR (e.model_id  IS NULL AND e.family_id IS NULL));

CREATE TABLE company_device_selection (
  company_id uuid NOT NULL REFERENCES company(id),
  model_id   uuid NOT NULL REFERENCES device_model(id),
  PRIMARY KEY (company_id, model_id)
);
ALTER TABLE company_device_selection ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_device_selection FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON company_device_selection
  USING (company_id = current_setting('app.company_id')::uuid);

-- La seleccion nunca puede salirse de lo contratado.
CREATE FUNCTION check_selection_allowed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM company_visible_model v
                 WHERE v.company_id = NEW.company_id AND v.model_id = NEW.model_id) THEN
    RAISE EXCEPTION 'El modelo % no esta habilitado para la empresa %', NEW.model_id, NEW.company_id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER company_device_selection_guard
  BEFORE INSERT OR UPDATE ON company_device_selection
  FOR EACH ROW EXECUTE FUNCTION check_selection_allowed();

-- Recuperacion documental: SOLO fragmentos del modelo pedido, aplicables y vigentes,
-- y SOLO si la empresa en sesion tiene ese modelo habilitado (si no, devuelve 0 filas).
CREATE FUNCTION retrieve_chunks(p_model_id uuid, p_query vector(1024), p_limit int DEFAULT 8)
RETURNS TABLE (chunk_id uuid, content text, section_path text[], source_url text, similarity float8)
LANGUAGE sql STABLE AS $$
  SELECT c.id, c.content, c.section_path, d.url, 1 - (c.embedding <=> p_query)
  FROM chunk c
  JOIN chunk_applicability a ON a.chunk_id = c.id
                            AND a.model_id = p_model_id
                            AND a.status IN ('verified', 'published')
  JOIN source_document d ON d.id = c.document_id AND d.is_current
  WHERE c.embedding IS NOT NULL
    AND EXISTS (SELECT 1 FROM company_visible_model v WHERE v.model_id = p_model_id)
  ORDER BY c.embedding <=> p_query
  LIMIT p_limit
$$;
