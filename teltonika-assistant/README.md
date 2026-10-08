# Asistente GPS - base de ingesta (FMB920)

## Que hay aqui
- `db/schema.sql`: esquema PostgreSQL + pgvector (catalogo global, datos por empresa con RLS, evaluacion).
- `ingest/`: pipeline de ingesta (conector MediaWiki, conector PDF, parseo, persistencia).
- `crawl_plan.yaml`: plan de rastreo acotado al FMB920 y paginas de su familia.
- `tests/`: pruebas del parseo (HTML sintetico).

## Primera ejecucion (en tu maquina)
```bash
pip install -r requirements.txt
# 1) Edita user_agent en crawl_plan.yaml con un contacto real
# 2) Simulacro: no escribe nada en la base
python -m ingest.pipeline --plan crawl_plan.yaml --dry-run
# 3) Base de datos + ingesta real
createdb assistant && psql assistant -f db/schema.sql
python -m ingest.pipeline --plan crawl_plan.yaml --dsn postgresql://user:pass@localhost/assistant
```

## Que NO esta verificado todavia (revisar con el dry-run)
1. Que `/api.php` este habilitado en la wiki.
2. Los titulos de las paginas semilla (son suposiciones por patron; el dry-run avisa las que no existen).
3. Que la ruta "Main Page > ..." salga en el HTML que devuelve la API (si no, `extract_breadcrumb` devuelve vacio y el modelo no se detecta).
4. Como se renderizan los "Yes/No" de las tablas de comandos (texto o iconos). Si son iconos, hay que leer el atributo alt.
5. Tablas con celdas combinadas (rowspan/colspan): no se manejan aun.
6. Dimension del embedding (`vector(1024)`) hasta elegir el modelo de embeddings.

## Acceso por empresa (marcas/modelos)
- `company_entitlement`: lo contratado (marca completa, familia o modelo). Solo lo cambia la consola del proveedor.
- `company_device_selection`: con que equipos trabaja la empresa hoy, siempre dentro de lo contratado.
- `company_visible_model`: vista que expande permisos a modelos concretos (respeta RLS).
- `retrieve_chunks(modelo, embedding)`: la recuperacion solo devuelve contenido de modelos habilitados.
- La API debe ejecutar `SET LOCAL app.company_id = '<uuid>'` en cada peticion.

## Regla de equipo en contexto
- Todo chat nuevo empieza sin equipo (`conversation.model_id` NULL) y el asistente SIEMPRE pregunta con cual trabaja,
  mostrando solo los modelos habilitados para la empresa (no se auto-selecciona ni con un unico modelo).
- El equipo se elige con un selector, no lo deduce el LLM. El backend rechaza respuestas tecnicas si `model_id` es NULL.
- Si el usuario menciona otro equipo a mitad del chat, el asistente pide confirmar el cambio antes de aplicarlo.

## Reglas de diseno que no se rompen
- El modelo se deduce de la ruta, nunca de menciones en el texto.
- Paginas de familia entran como `draft`; solo pasan a uso tras revision.
- Comandos y parametros solo se usan si `status = 'published'`.
- Se guarda el HTML/PDF original de cada version.

## Licencia
La wiki muestra copyright de Teltonika: confirmar permiso de uso comercial antes de vender.
