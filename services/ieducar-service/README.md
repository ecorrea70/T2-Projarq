# `ieducar-service` — Adaptador de origem e detector de mudanças

Microsserviço que **lê o i-Educar** e **avisa quando algo muda**. É o único serviço do T2 que conhece
as duas APIs do i-Educar e suas credenciais.

> Decisões: [ADR de decomposição](../../docs/adr.md) · arquitetura geral em
> [`docs/architecture.md`](../../docs/architecture.md)

## Responsabilidades e fronteiras

- **Responde** às três leituras que o `sync-service` consome quando `IEDUCAR_SOURCE=http`.
- **Avisa**: varre a origem de tempo em tempo e publica um aviso magro por mudança em
  `integration.events`.
- **Não acessa** o banco do i-Educar, nem o Moodle, nem o banco de outro serviço. Só a API do
  i-Educar e o seu próprio Postgres.
- Banco próprio (`ieducar-service-db`):
  - `source_fingerprints` — a memória da última leitura de cada registro (hash, snapshot, quando
    mudou). É o que permite dizer "isso mudou" em vez de reenviar tudo.
  - `scan_runs` — auditoria de cada varredura: gatilho, contadores, duração e erro.

## As duas APIs do i-Educar

| API | Autenticação | O que traz |
| --- | --- | --- |
| REST v2 `/api/registration` | `Authorization: Bearer IEDUCAR_TOKEN`, paginada (100 por página) | alunos e suas enturmações |
| Legada `/module/Api/Servidor` | `access_key` + `oper=get` | vínculos docentes e dados do professor |
| Legada `/module/Api/Turma` | idem | turmas vigentes da escola |
| Legada `/module/Api/ComponenteCurricular` | idem | disciplinas de cada turma |

A normalização dessas respostas está em `app/domain/payloads.py`, portada do
`app/adapters/ieducar_payloads.py` do T1: enturmação transferida ou remanejada não sincroniza, mapa
vazio que o PHP serializa como lista, paginação conferida, aluno divergente entre páginas recusado.

## Detecção de mudanças

A cada varredura o serviço lê catálogo, professores e alunos, reduz cada registro a um **sha256 do
conteúdo canônico** e compara com o hash guardado:

| Situação | Resultado |
| --- | --- |
| Chave nova | `created` |
| Hash diferente do guardado | `updated` |
| Chave guardada que não apareceu na leitura | `removed` |
| Chave que volta depois de removida | `created` |
| Mesma informação em outra ordem de campos | nenhuma mudança |

Não dependemos de nenhum campo de data do i-Educar: o hash também detecta exclusão, ao custo de ler
tudo em cada ciclo.

**A primeira varredura é um baseline silencioso.** Ela grava as impressões digitais e publica zero
avisos, para o primeiro ciclo não despejar centenas de mensagens sobre coisas que já existiam. A
partir da segunda, só o que mudou é publicado.

## O aviso publicado

Exchange `integration.events`, routing keys `ieducar.catalog.changed.v1`,
`ieducar.teacher.changed.v1` e `ieducar.student.changed.v1`. Envelope v1 igual ao dos outros serviços.

```json
{
  "message_id": "7c1e...",
  "event_type": "ieducar.teacher.changed",
  "event_version": "1.0",
  "correlation_id": "scan-001",
  "causation_id": "<scan_id>",
  "producer": "ieducar-service",
  "occurred_at": "2026-11-24T12:00:00+00:00",
  "idempotency_key": "ieducar:teacher:7:3f9a...",
  "payload": {
    "entity_type": "teacher",
    "entity_key": "7",
    "change_type": "updated",
    "identity": {"professor_id": 7},
    "fingerprint": "3f9a...",
    "fetch_from": "/internal/teachers"
  }
}
```

É um **aviso magro**: diz o que mudou e onde buscar, não leva o registro dentro. Assim a mensagem
nunca fica velha em relação à origem, e o consumidor usa `fetch_from` para pegar o dado atual.

A fila `sync.events` do `sync-service` assina apenas `moodle.#`, então **estes avisos não chegam nela
hoje**. Para consumir, basta acrescentar um binding `ieducar.#` naquela fila. O `idempotency_key`
repete para a mesma mudança, o que permite deduplicar.

## Endpoints (`:8004`)

| Método | Rota | Para quê |
| --- | --- | --- |
| `GET` | `/internal/catalog` | Turmas × disciplinas vigentes no escopo |
| `GET` | `/internal/teachers` | Professores com vínculo vigente |
| `GET` | `/internal/students` | Alunos ativos e suas enturmações |
| `POST` | `/internal/scan` | Varre agora. Aceita `X-Correlation-Id` |
| `POST` | `/internal/scan?full=true` | Republica um aviso por registro vigente |
| `GET` | `/internal/scans` | Histórico das varreduras |
| `GET` | `/internal/scans/{scan_id}` | Detalhe de uma varredura |
| `GET` | `/health` | Liveness, estado do circuit breaker, se há varredura rodando |
| `GET` | `/ready` | Banco e AMQP |
| `GET` | `/metrics` | Métricas Prometheus |

As três leituras vão à origem na hora. Se o i-Educar estiver fora, respondem **503** com a mensagem
sanitizada, nunca um dado velho sem aviso.

## Resiliência

| Mecanismo | Configuração |
| --- | --- |
| Timeout de conexão e de leitura | `IEDUCAR_CONNECT_TIMEOUT`, `IEDUCAR_READ_TIMEOUT` |
| Retry local com backoff e jitter | `LOCAL_MAX_RETRIES`, `LOCAL_RETRY_BASE_DELAY` |
| Circuit breaker (fechado, aberto, meio-aberto) | `CB_FAILURE_THRESHOLD`, `CB_RECOVERY_SECONDS` |
| Varredura concorrente impedida | trava em memória; duas varreduras nunca rodam juntas |
| Varredura interrompida por queda | marcada como `interrupted` na subida, não bloqueia a próxima |
| Origem fora do ar | a varredura termina como `failed` e a próxima tenta de novo |

Erro de transporte e erro de dado são classes diferentes (`app/core/errors.py`): o primeiro merece
retry e abre o breaker, o segundo não, porque repetir não conserta um payload inválido. Um professor
individual que não pode ser lido entra na lista com `erro_origem` em vez de derrubar a varredura
inteira, como no T1.

## Variáveis

| Variável | Para quê |
| --- | --- |
| `IEDUCAR_URL` | Base da instância (no Compose, `http://ieducar`) |
| `IEDUCAR_TOKEN` | Bearer da API REST v2 |
| `IEDUCAR_ACCESS_KEY` | Credencial da API legada |
| `INSTITUTION_ID`, `SCHOOL_ID`, `YEAR` | Escopo da sincronização (`SCHOOL_ID` aceita lista: `2,3`) |
| `SCAN_INTERVAL_SECONDS` | Intervalo da varredura automática |
| `SCAN_ON_STARTUP` | Varrer assim que o serviço sobe |
| `DATABASE_URL`, `RABBITMQ_URL`, `RABBITMQ_ENABLED` | Infraestrutura |

## Rodando

Pela raiz do projeto (sobe i-Educar, banco, Redis, RabbitMQ e o serviço):

```bash
docker compose up -d --build ieducar-service
```

A primeira subida instala o i-Educar sozinha (ver `services/ieducar-init/init.sh`) e por isso demora.

Varrer na hora e ver o resultado:

```bash
curl -s -X POST http://localhost:8004/internal/scan -H "X-Correlation-Id: demo-001"
```

```bash
curl -s http://localhost:8004/internal/scans | head -40
```

## Testes

```bash
docker build -t t2-ieducar-service ./services/ieducar-service
```

```bash
docker run --rm -e RUN_MIGRATIONS=false t2-ieducar-service pytest -v
```

`tests/test_payloads.py` cobre a normalização (funções puras), `tests/test_scanner.py` cobre baseline
silencioso, detecção, remoção, reaparecimento, republish total e falha da origem, e
`tests/test_api.py` cobre as rotas com uma origem falsa.
