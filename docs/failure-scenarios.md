# Cenários de falha — T2

Os quatro cenários exigidos pelo enunciado, com o mecanismo da arquitetura que responde a cada um, o
comando exato para reproduzir e o resultado esperado. Visão geral dos mecanismos em
[`architecture.md`](./architecture.md#8-resiliência).

## Preparação

```bash
cp .env.example .env
# Preencha MOODLE_DB_PASSWORD, MOODLE_ADMIN_PASSWORD, MOODLE_TOKEN e SYNC_API_TOKEN
docker compose up -d --build
docker compose ps
```

Aguarde todos os serviços como `healthy` (o Moodle tem `start_period` de 120 s na primeira subida).
Em outra aba, deixe os logs abertos:

```bash
docker compose logs -f sync-service moodle-service ieducar-service
```

> A primeira subida instala o i-Educar sozinha (`services/ieducar-init/init.sh`) e por isso demora
> bastante. O `sync-service` segue com `IEDUCAR_SOURCE=mock` até você preencher `IEDUCAR_TOKEN` e
> `IEDUCAR_ACCESS_KEY` e trocar para `IEDUCAR_SOURCE=http`. O cenário 1 pode ser demonstrado pelo
> `ieducar-service` em qualquer um dos dois modos, porque ele sempre lê a origem real.

Para disparar pela borda pública, exporte o token do `.env`:

```bash
export SYNC_API_TOKEN=$(grep '^SYNC_API_TOKEN=' .env | cut -d= -f2-)
```

Dispara e acompanha:

```bash
curl -s -X POST http://localhost:8000/sync/teachers -H "Authorization: Bearer $SYNC_API_TOKEN" -H "X-Correlation-Id: cen-001"
```

```bash
curl -s http://localhost:8000/sync/cen-001 -H "Authorization: Bearer $SYNC_API_TOKEN"
```

Para publicar comandos direto no broker, sem passar pelo `sync-service` (útil nos cenários 2, 3 e 4):

```bash
docker compose exec moodle-service python scripts/publish_test_flow.py --scenario happy --rabbitmq-url amqp://guest:guest@rabbitmq:5672/
```

> O `--rabbitmq-url` é necessário: o padrão do script é `localhost`, que dentro do contêiner não é o
> broker. A UI do RabbitMQ fica em `http://localhost:15672` (usuário e senha do `.env`).

---

## Cenário 1 — O i-Educar está fora do ar

Há dois caminhos que leem o i-Educar, e eles se comportam de formas diferentes. Vale demonstrar os
dois.

### 1a. A varredura do `ieducar-service` (retoma sozinha)

**O que a arquitetura faz.** Quem fala com o i-Educar é o `ieducar-service`, e ele não depende da
origem para existir: no Compose depende apenas do próprio banco e do RabbitMQ. Com a origem fora:

1. A chamada estoura `IEDUCAR_CONNECT_TIMEOUT` / `IEDUCAR_READ_TIMEOUT` e vira
   `TransientIntegrationError`.
2. **Retry local** curto (`LOCAL_MAX_RETRIES`, backoff exponencial com jitter).
3. O **circuit breaker** abre depois de `CB_FAILURE_THRESHOLD` falhas e passa a falhar rápido por
   `CB_RECOVERY_SECONDS`, sem martelar a origem caída.
4. A varredura termina como `failed` em `scan_runs`, com o erro sanitizado, e **a próxima roda no
   intervalo normal** (`SCAN_INTERVAL_SECONDS`).
5. Como nenhuma impressão digital foi gravada nessa tentativa, quando a origem volta a varredura
   seguinte detecta exatamente as mudanças pendentes — nada se perde e nada é publicado duas vezes.

Se o próprio `ieducar-service` cair no meio de uma varredura, a varredura ficaria `running` para
sempre; na subida ele marca essas execuções como `interrupted` e a trava não bloqueia a próxima.

**Como reproduzir:**

```bash
docker compose stop ieducar
```

```bash
curl -s -X POST http://localhost:8004/internal/scan -H "X-Correlation-Id: cen1a-001"
```

```bash
curl -s http://localhost:8004/health
```

```bash
docker compose start ieducar
```

```bash
curl -s -X POST http://localhost:8004/internal/scan -H "X-Correlation-Id: cen1a-002" && curl -s http://localhost:8004/internal/scans
```

**Resultado esperado:** a primeira varredura responde `503` e fica `failed` no histórico;
`/health` mostra o circuit breaker em `open`; as leituras `/internal/catalog`, `/internal/teachers` e
`/internal/students` respondem `503` em vez de devolver dado velho sem avisar; com a origem de volta, a
varredura seguinte termina `succeeded` e publica os avisos acumulados.

**Teste automatizado:** em `services/ieducar-service/tests/`,
`test_origem_fora_do_ar_registra_falha_e_nao_publica`,
`test_falha_nao_corrompe_o_baseline_seguinte`, `test_varredura_interrompida_nao_bloqueia_a_proxima`
(scanner) e `test_origem_fora_do_ar_responde_503`, `test_scan_com_origem_fora_responde_503` (rotas).

### 1b. O `POST /sync/*` disparado na hora (ainda perde a tentativa)

**O que acontece.** Com `IEDUCAR_SOURCE=http`, o `sync-service` lê a origem dentro do `POST /sync/*`,
**antes** de criar a run. O `IEducarServiceClient` tem timeout, mas **não** tem retry nem circuit
breaker, e a exceção sobe pela rota: o cliente recebe erro, nada é publicado e nada fica persistido.

**Consequência honesta:** nenhum dado é corrompido nem duplicado, mas essa tentativa específica é
**perdida** — quem chamou precisa chamar de novo. É a lacuna conhecida do T2 e deve ser dita na
apresentação, não escondida.

**Como fechar a lacuna** (ordem de esforço crescente):

1. Envolver as chamadas de origem no mesmo `run_with_local_retry` + circuit breaker já usados no
   `moodle-service` e no `ieducar-service`, devolvendo `503` com `Retry-After`.
2. Criar a run **antes** de ler a origem, com status `awaiting_source`, e mover o planejamento para uma
   tarefa que relê a origem quando ela voltar. Aí o `correlation_id` já existe, o cliente acompanha por
   `GET /sync/{id}` e a execução retoma sozinha.

**Como reproduzir:**

```bash
docker compose stop ieducar-service
```

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/sync/teachers -H "Authorization: Bearer $SYNC_API_TOKEN" -H "X-Correlation-Id: cen1b-001"
```

```bash
docker compose start ieducar-service
```

**Resultado esperado hoje:** erro na chamada e nenhuma run criada (`GET /sync/cen1b-001` responde 404).
**Resultado esperado depois da correção:** `202 Accepted`, run em `awaiting_source` e, quando a origem
voltar, a fase 1 publicada sem intervenção e sem duplicar nada.

**Teste automatizado:** não existe ainda, deste lado.

---

## Cenário 2 — O Moodle está fora do ar

**O que a arquitetura faz.** O comando já está publicado e durável no RabbitMQ, então nada se perde. A
chamada ao Moodle estoura o timeout e vira `TransientIntegrationError`:

1. **Retry local** curto (`LOCAL_MAX_RETRIES`, backoff exponencial com jitter).
2. **Circuit breaker** abre depois de `CB_FAILURE_THRESHOLD` falhas consecutivas e passa a falhar
   rápido por `CB_RECOVERY_SECONDS`, sem martelar o Moodle caído.
3. A mensagem vai para **`moodle.commands.retry`**, espera `RABBITMQ_RETRY_TTL_MS` e volta para
   `integration.commands` com `x-retry-count` incrementado.
4. Esgotado `RABBITMQ_MAX_RETRIES`, vai para a **DLQ** `moodle.commands.dlq`, e um evento
   `moodle.*.failed.v1` informa o `sync-service`. O restante da fila continua sendo processado.

**Como reproduzir:**

```bash
docker compose stop moodle
```

```bash
docker compose exec moodle-service python scripts/publish_test_flow.py --scenario happy --rabbitmq-url amqp://guest:guest@rabbitmq:5672/
```

Observe o circuito abrir e as mensagens circularem entre a fila principal e a de retry:

```bash
curl -s http://localhost:8003/ready
```

```bash
curl -s http://localhost:8003/metrics | grep -E 'moodle_circuit_breaker_state|moodle_messages_processed_total'
```

Religue o Moodle, espere ficar `healthy` e reprocesse o que caiu na DLQ:

```bash
docker compose start moodle
```

```bash
curl -s -X POST 'http://localhost:8003/internal/dlq/replay?limit=50'
```

**Resultado esperado:** `moodle_circuit_breaker_state` vai a `2` (aberto) e volta a `0`;
`moodle_messages_processed_total` mostra `outcome="retried"` e depois `outcome="dlq"`; o replay devolve
as mensagens com `x-replayed: true`, elas são processadas e **cada curso, conta e matrícula existe uma
única vez** no Moodle. Nenhuma mensagem foi descartada em silêncio.

**Teste automatizado:**
`services/moodle-service/tests/test_failure_scenarios.py::TestFailureScenarios::test_scenario_2_moodle_down_opens_circuit_retries_and_routes_to_dlq`

---

## Cenário 3 — O Moodle cria o curso, mas a resposta HTTP não chega

**O que a arquitetura faz.** Este é o caso em que o efeito aconteceu e o serviço não sabe. A resposta
não é "tentar de novo e esperar o melhor": o handler **procura antes de criar**. `handle_course_sync`
chama `find_course("idnumber", ...)`; se o curso já existe, adota o `moodle_id`, grava
`last_sync_status = reconciled` e devolve `status: existente` com `reconciled: true`. Criar só acontece
quando a busca não encontra nada. Mais de um curso com a mesma identidade é erro permanente e vai para
a DLQ, em vez de virar um terceiro curso.

O `idnumber` determinístico (`ieducar:curso:{instituicao}:{ano}:{turma}:{disciplina}`) é o que torna
isso possível: a identidade não depende da resposta do Moodle.

**Como reproduzir.** O `moodle-service` traz injeção de falha controlada para exatamente este caso: ele
cria o curso de verdade e descarta a resposta.

```bash
docker compose stop moodle-service
```

Edite o `.env`:

```env
FAULT_INJECT_DROP_AFTER_COURSE_CREATE=true
FAULT_INJECT_TARGET_IDNUMBER=ieducar:curso:1:2026:10:2
```

```bash
docker compose up -d moodle-service
```

```bash
docker compose exec moodle-service python scripts/publish_test_flow.py --scenario happy --rabbitmq-url amqp://guest:guest@rabbitmq:5672/
```

Confira no Moodle que existe **um** curso com aquele `idnumber`:

```bash
curl -s 'http://localhost:8003/internal/runs/<correlation_id>'
```

**Resultado esperado:** o log mostra `Injeção de falha (Cenário 3): resposta HTTP simuladamente
perdida após criação do curso`; na tentativa seguinte o evento `moodle.course.synced.v1` sai com
`status: "existente"` e `reconciled: true`; o Moodle tem **um único** curso. Deixe
`FAULT_INJECT_DROP_AFTER_COURSE_CREATE=false` depois da demo.

**Teste automatizado:**
`test_scenario_3_http_response_lost_after_course_create_reconciles_without_duplicate`

---

## Cenário 4 — Um serviço reinicia no meio da sincronização

**O que a arquitetura faz.** Duas garantias se somam:

- **ACK manual depois do efeito.** O consumidor só dá `ack` depois de aplicar a mudança no Moodle e
  gravar no banco. Se o processo morre antes, o broker devolve a mensagem para a fila (`redelivered`).
- **Estado fora do processo.** O progresso da run vive no `sync-service-db` e o Inbox no
  `moodle-service-db`. Reiniciar não perde o que já foi feito: a mensagem redelivered cai na
  reconciliação do cenário 3 e é marcada `reconciled`, ou é descartada como `duplicate` pelo Inbox se
  já tinha concluído.

O consumidor também reconecta sozinho ao broker (tentativa a cada 2 s) em vez de subir quebrado, e o
`moodle-service` não depende do Moodle estar `healthy` para iniciar.

**Como reproduzir:**

```bash
docker compose exec moodle-service python scripts/publish_test_flow.py --scenario burst --count 25 --rabbitmq-url amqp://guest:guest@rabbitmq:5672/
```

Durante o processamento, mate o serviço sem encerramento limpo e suba de novo:

```bash
docker compose kill moodle-service && docker compose start moodle-service
```

Para a mesma prova do lado do orquestrador, reinicie o `sync-service` com uma run em andamento:

```bash
docker compose restart sync-service
```

```bash
curl -s http://localhost:8000/sync/cen-001 -H "Authorization: Bearer $SYNC_API_TOKEN"
```

**Resultado esperado:** nenhuma mensagem perdida e nenhum duplicado; os itens já concluídos aparecem
como `duplicate` ou `reconciled` nos logs e em `GET /internal/runs/{correlation_id}`; a run termina com
o mesmo estado final de uma execução sem falha, e `GET /sync/{correlation_id}` mostra os contadores
fechando (`pending` chega a zero).

**Teste automatizado:** `test_scenario_4_service_crash_mid_flight_reconciles_on_redelivery`

---

## Resumo

| Cenário | Mecanismo principal | Reprodutível | Teste |
| --- | --- | --- | --- |
| 1a. i-Educar fora, varredura | timeout, retry local, circuit breaker e nova varredura no intervalo | sim | sim |
| 1b. i-Educar fora, `POST /sync/*` | timeout na leitura da origem; **sem retry e sem retomada** | sim | não |
| 2. Moodle fora | retry local, circuit breaker, fila de retry com TTL, DLQ e replay | sim | sim |
| 3. Resposta perdida | busca por `idnumber` antes de criar, com `reconciled: true` | sim, por injeção de falha | sim |
| 4. Restart no meio | ACK manual após o efeito, Inbox e estado no banco | sim | sim |

Rodando os testes dos cenários:

```bash
docker build -t t2-moodle-service ./services/moodle-service
```

```bash
docker run --rm -e RUN_MIGRATIONS=false t2-moodle-service pytest -v tests/test_failure_scenarios.py
```

```bash
docker build -t t2-ieducar-service ./services/ieducar-service
```

```bash
docker run --rm -e RUN_MIGRATIONS=false t2-ieducar-service pytest -v
```
