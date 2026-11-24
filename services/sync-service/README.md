# `sync-service` — Orquestrador (versão inicial)

Coordena a sincronização **i-Educar → Moodle** publicando comandos no RabbitMQ (`integration.commands`) e acompanhando respostas do `moodle-service` em `integration.events`.

## Estado atual

| Integração | Status |
| :--- | :--- |
| **Moodle** (`moodle-service`) | Ativo via RabbitMQ + *preflight* HTTP best-effort |
| **i-Educar** (`ieducar-service`) | **Pendente** — `IEDUCAR_SOURCE=mock` usa dataset de demonstração |

## Endpoints (rede interna `:8002`)

Na stack completa, o cliente **não** deve chamar esta porta: use o [`api-gateway`](../api-gateway/) em `:8000`.

- `POST /sync/teachers` — `202 Accepted` + `correlation_id`
- `POST /sync/students` — `202 Accepted` + `correlation_id`
- `GET /sync/{correlation_id}` — progresso e itens
- `GET /health`, `GET /ready`, `GET /metrics`

## Fluxo por fases

1. **Fase 1**: publica `moodle.course.sync.requested.v1` e `moodle.user.sync.requested.v1` (professores) ou só usuários (alunos).
2. Consome `*.synced.v1` / `*.failed.v1` do Moodle.
3. **Fase 2**: quando a fase 1 terminar, publica matrículas (`moodle.teacher.enrol.requested.v1` ou lotes `moodle.students.enrol.requested.v1`).

## Trocar para o ieducar-service (quando pronto)

No `.env`:

```env
IEDUCAR_SOURCE=http
IEDUCAR_SERVICE_URL=http://ieducar-service:8004
```

Contrato HTTP esperado:

- `GET /internal/catalog`
- `GET /internal/teachers`
- `GET /internal/students`

## Teste rápido (com compose na raiz)

```bash
curl -X POST http://localhost:8002/sync/teachers -H "X-Correlation-Id: $(uuidgen)"
curl http://localhost:8002/sync/<correlation_id>
```

## Testes

```bash
cd services/sync-service
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest -v
```