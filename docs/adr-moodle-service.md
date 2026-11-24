# ADR-002: `moodle-service` (Adaptador Assíncrono do Moodle)

> Template **GIG Cymru / NHS Wales**. Escopo: microsserviço de destino do T2, consumidor de comandos RabbitMQ e publicador no Moodle via Web Service REST.

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito (*Accepted*) |
| **Data** | 2026-11-24 |
| **Decisores** | Responsável pelo `moodle-service` (T2) |
| **Relacionado** | [ADR-001](./adr.md), catálogo de eventos v1 (`integration.commands`) |

---

## 1. Summary (Resumo)

O `moodle-service` é o **adaptador de destino** da integração: consome comandos versionados no RabbitMQ (`integration.commands`), aplica idempotência e reconciliação no **banco próprio** (`moodle-service-db`) e chama as **8 funções** do Web Service REST do Moodle. Não expõe escrita síncrona para o `sync-service`; a orquestração fala com este serviço **apenas por mensageria** (comandos + eventos de resultado), complementada por HTTP interno só para *preflight* (`/internal/setup-check`) e auditoria (`/internal/runs/{correlation_id}`, DLQ replay).

---

## 2. Issue / Drivers

- Isolar `MOODLE_TOKEN`, timeouts, circuit breaker e semântica RPC-over-REST do Moodle.
- Garantir que indisponibilidade do Moodle (**Cenário 2**) não derrube o processo: filas absorvem carga, retry com TTL e DLQ.
- Preservar invariantes do T1: `idnumber` determinístico, reconciliação pós-commit (**Cenário 3**), Inbox por `message_id` (**Cenário 4**).
- Atender rubrica: banco por serviço, consumidor idempotente, logs com `correlation_id`, métricas e health checks.

---

## 3. Options (Opções)

1. **REST síncrono** exposto ao `sync-service` (proxy direto ao Moodle).
2. **Worker apenas AMQP** (sem API HTTP).
3. **Híbrido**: AMQP para escrita + HTTP mínimo para saúde, métricas, *setup-check* e inspeção operacional.

---

## 4. Options Analysis

| Opção | Prós | Contras |
| :--- | :--- | :--- |
| REST síncrono | Simples de orquestrar num primeiro desenho | Acoplamento temporal ao Moodle; falha síncrona antes da fila anula demo do Cenário 2; duplica retry em dois lados |
| Só AMQP | Máximo desacoplamento | Dificulta *preflight* e debug na apresentação sem consumir fila |
| **Híbrido (escolhida)** | Fila para carga e resiliência; HTTP para observabilidade e validação opcional de WS | Dois protocolos a documentar (já cobertos no catálogo v1) |

---

## 5. Recommendation (Decisão)

Adotar a **Opção 3 — Híbrido**:

| Aspecto | Decisão |
| :--- | :--- |
| **Stack** | Python 3.12, FastAPI, Uvicorn, `httpx`, `aio-pika`, SQLAlchemy 2 + Alembic, PostgreSQL 16, Prometheus |
| **Comunicação entrada** | Fila `moodle.commands` ← `integration.commands` (`moodle.#.requested.v1`) |
| **Comunicação saída** | `integration.events` (`*.synced.v1`, `*.failed.v1`, `*.enrolled.v1`) |
| **Retry** | Local (`LOCAL_MAX_RETRIES`) + `integration.retry` → TTL → devolve à commands com mesma routing key + DLQ |
| **Estado** | `processed_messages` (Inbox), `moodle_entities`, `moodle_enrolments` |
| **Resiliência Moodle** | Timeout, backoff local, circuit breaker (só falhas transitórias) |
| **HTTP `:8003`** | `/health`, `/ready`, `/metrics`, `/internal/setup-check`, `/internal/runs/{correlation_id}`, `/internal/dlq/replay` |
| **Deploy** | Imagem própria; no compose raiz **não** `depends_on` do `moodle` healthy |

---

## 6. Consequences / Implicações

**Positivas**

- `sync-service` publica comandos sem conhecer o Moodle; contrato v1 estável.
- Cenários 2–4 demonstráveis com compose, flags de falha e DLQ replay.
- Evolução do cliente Moodle fica contida neste repositório.

**Negativas / trade-offs**

- O `sync-service` deve declarar fila/consumidor em `integration.events` e correlacionar por `idempotency_key`.
- *Setup-check* no início da sync pode falhar com Moodle offline — no sync isso é **best-effort** para não bloquear o fluxo assíncrono.
- Topologia RabbitMQ é declarada idempotentemente por este serviço; outros produtores/consumidores devem reutilizar os mesmos nomes de exchange/fila.

---

## 7. Related (Artefatos)

- Código: [`services/moodle-service/`](../services/moodle-service/)
- README operacional: [`services/moodle-service/README.md`](../services/moodle-service/README.md)
- Testes de cenário: [`services/moodle-service/tests/test_failure_scenarios.py`](../services/moodle-service/tests/test_failure_scenarios.py)