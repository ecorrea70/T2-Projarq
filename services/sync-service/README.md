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

## Caminho automático: resumo de varredura

Além do `POST /sync/*`, o serviço reage sozinho a mudanças na origem. O `ieducar-service` publica um
resumo por varredura (`ieducar.scan.changed.v1`) e aqui ele é consumido em **fila própria**, separada
da fila dos resultados do Moodle:

| Fila | Binding | Papel |
| :--- | :--- | :--- |
| `sync.events` | `moodle.#` | Resultados do `moodle-service`. Não mudou nada aqui |
| `sync.ieducar.notices` | `ieducar.#` | Resumos de varredura, com consumidor próprio |
| `sync.ieducar.retry` | `#` em `integration.ieducar.retry` | Estacionamento com TTL quando a trava rejeita |
| `sync.ieducar.dlq` | `sync.ieducar.dlq` em `integration.dlx` | Resumo que esgotou as tentativas |

São duas filas porque em uma só daria deadlock: um tratamento que esperasse o fim da execução
travaria a fila onde chegam os resultados que encerram essa execução.

O resumo diz em quais tipos houve mudança. `teacher` dispara professores, `student` dispara alunos e
`catalog` dispara **os dois**, porque os dois planners leem o catálogo. Se a trava de execução única
rejeitar (`conflict`) ou a origem estiver fora (`origin_unavailable`), o resumo volta para a fila de
retry carregando **só o que ainda falta**, e o broker o devolve quando o TTL vencer. Ver ADR-006 e
ADR-007 em `docs/adr.md`.

```env
IEDUCAR_NOTICES_ENABLED=true
IEDUCAR_NOTICE_RETRY_TTL_MS=15000
IEDUCAR_NOTICE_MAX_ATTEMPTS=10
```

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