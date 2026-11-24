# `moodle-service` — Microsserviço Adaptador do Moodle

Microsserviço responsável por **publicar cursos, contas de usuários e matrículas no Moodle** de forma **assíncrona, idempotente e resiliente**, mantendo seu próprio banco PostgreSQL (`moodle-service-db`) totalmente separado do banco interno do Moodle (`moodle-db`).

## Responsabilidades e Fronteiras

- **Consome comandos do RabbitMQ** e executa as 8 funções Web Service REST do Moodle (`/webservice/rest/server.php`).
- **Não acessa** o banco do Moodle (`moodle-db`), o i-Educar ou outros microsserviços.
- **Banco próprio (`moodle-service-db`)**:
  - `processed_messages` (*Inbox Pattern*): impede processamento duplicado do mesmo `message_id` e registra tentativas/reconciliações por `correlation_id`.
  - `moodle_entities`: mapeia `idnumber` determinístico → `moodle_id` para cursos, professores e alunos.
  - `moodle_enrolments`: audita matrículas confirmadas (`roleid=3` docente, `roleid=5` estudante) e eventuais avisos SMTP reconciliados.

## Como o RabbitMQ está organizado (3 filas)

1. **`moodle.commands`** (ligada a `integration.commands` via `moodle.#.requested.v1`): fila principal de entrada com `manual ACK` após commit no banco e publicação do evento de resposta.
2. **`moodle.commands.retry`** (ligada a `integration.retry` com TTL configurável via `RABBITMQ_RETRY_TTL_MS`): segura mensagens com falha transitória (ex.: Moodle fora do ar) e, ao expirar o TTL, devolve automaticamente para `integration.commands` preservando a *routing key* original.
3. **`moodle.commands.dlq`** (ligada a `integration.dlx`): recebe mensagens com erro permanente de domínio ou que esgotaram `RABBITMQ_MAX_RETRIES`, sem bloquear o restante do fluxo.

Eventos de resultado (`moodle.course.synced.v1`, `moodle.user.synced.v1`, `moodle.teacher.enrolled.v1`, `moodle.students.enrolled.v1` e `*.failed.v1`) são publicados no exchange **`integration.events`**.

## Endpoints HTTP (`:8003`)

- `GET /health` — *Liveness* do serviço e status do consumidor RabbitMQ.
- `GET /ready` — *Readiness* verificando `moodle-service-db`, conexão AMQP e estado do Circuit Breaker.
- `GET /metrics` — Métricas Prometheus.
- `GET /internal/setup-check?kind=teachers|students` — Verifica funções Web Service e categoria no Moodle.
- `GET /internal/runs/{correlation_id}` — Auditoria de todas as mensagens processadas de uma execução.
- `POST /internal/dlq/replay?limit=50` — Reenfileira mensagens da DLQ para reprocessamento após recuperar o Moodle.

## Executando de Forma Independente ou via Raiz

### Pela raiz do projeto (sobe `rabbitmq`, `moodle-db`, `moodle`, `moodle-service-db` e `moodle-service`)

```bash
cp .env.example .env
# Preencha MOODLE_DB_PASSWORD, MOODLE_ADMIN_PASSWORD e MOODLE_TOKEN no .env
docker compose up -d --build
```

### De forma isolada dentro de `services/moodle-service/`

```bash
cd services/moodle-service
docker compose up -d --build
```

*(Neste modo, `MOODLE_URL` aponta por padrão para `http://host.docker.internal:8080`.)*

### Testando o fluxo via script

```bash
# Fluxo feliz completo (curso + professor + aluno + matrículas)
docker compose exec moodle-service python scripts/publish_test_flow.py --scenario happy

# Teste de idempotência (mesmo message_id e mesmo idnumber)
docker compose exec moodle-service python scripts/publish_test_flow.py --scenario duplicate
```

### Reproduzindo os Cenários de Falha (T2)

- **Cenário 2 (Moodle fora do ar)**:
  1. `docker compose stop moodle`
  2. Publique comandos (`publish_test_flow.py`). Observe o Circuit Breaker abrir nos logs e `/ready`, mensagens irem para `moodle.commands.retry` e depois `moodle.commands.dlq`.
  3. `docker compose start moodle` e aguarde ficar saudável; chame `POST http://localhost:8003/internal/dlq/replay` para reprocessar sem perda.
- **Cenário 3 (Moodle cria o curso, mas resposta HTTP não chega)**:
  1. Configure `FAULT_INJECT_DROP_AFTER_COURSE_CREATE=true` no `.env` e reinicie `moodle-service`.
  2. Envie um comando de criação de curso: a 1ª tentativa cria o curso no Moodle e descarta a resposta; o retry automático consulta `idnumber`, encontra o curso existente, grava `reconciled=true` e não duplica.
- **Cenário 4 (Serviço reinicia no meio da sincronização)**:
  1. Dispare `publish_test_flow.py --scenario burst --count 25`.
  2. Durante o processamento, execute `docker compose kill moodle-service` e depois `docker compose start moodle-service`.
  3. As mensagens sem `ACK` voltam à fila automaticamente; os cursos já gravados antes da queda são reconciliados por `idnumber` sem duplicação.

## Rodando os Testes Automatizados

```bash
docker build -t t2-moodle-service ./services/moodle-service
docker run --rm -e RUN_MIGRATIONS=false t2-moodle-service pytest -v
```
