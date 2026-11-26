# Registro de Decisões Arquiteturais (ADR) — T2: Microsserviços i-Educar × Moodle

> Estrutura baseada no template **GIG Cymru / NHS Wales** ([decision-record-template-by-gig-cymru-nhs-wales](https://github.com/architecture-decision-record/architecture-decision-record/tree/main/locales/en-001/templates/decision-record-template-by-gig-cymru-nhs-wales)).

---

## ADR-001: Decomposição do Monolito em API Gateway e Três Microsserviços por Fronteira de Responsabilidade

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito |
| **Data** | 2026-11-19 (atualizado em 2026-11-25) |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Referência Base** | Monolito T1 (`/monolith`) |

### 1. Resumo

No T1, a integração unidirecional `i-Educar -> Moodle` (sincronização de 177 cursos, 21 professores, 210 alunos ativos e 1.770 matrículas) foi implementada como um monolito Python/FastAPI estruturado em MVC + Clean Architecture. Para o T2, decidiu-se decompor o monolito em **1 API Gateway (`api-gateway`) e 3 microsserviços (`ieducar-service`, `sync-service` e `moodle-service`)**, cada microsserviço com **seu próprio banco PostgreSQL isolado** e comunicação assíncrona via **RabbitMQ** para a publicação no Moodle, mantidos em um único repositório (*monorepo*) e orquestrados via Docker Compose.

### 2. Problema e direcionadores arquiteturais

O monolito do T1 concentra no mesmo processo a autenticação da API pública, o controle de concorrência/progresso, as regras de domínio e validação *fail-fast*, além dos clientes HTTP específicos para duas APIs distintas do i-Educar (REST v2 e legada `/module/Api/*`) e para o Web Service REST do Moodle (`/webservice/rest/server.php`).

Os principais direcionadores (*drivers*) para a decisão de decomposição no T2 são:

* **Preservação do contrato externo (`:8000`)**: Manter compatibilidade com os endpoints públicos (`GET /health`, `POST /sync/teachers` e `POST /sync/students` autenticados via `Authorization: Bearer <SYNC_API_TOKEN>`), evoluindo o retorno para execução assíncrona (`202 Accepted` + `GET /sync/{correlation_id}`).
* **Isolamento de sistemas externos, credenciais e dados (*Information Hiding* + *Database per Service*)**:
  * Apenas o `ieducar-service` conhece `IEDUCAR_TOKEN`, `IEDUCAR_ACCESS_KEY` e a API legada do i-Educar, mantendo seu próprio banco (`ieducar-service-db`).
  * Apenas o `moodle-service` conhece `MOODLE_TOKEN` e as 8 funções do Web Service REST do Moodle, mantendo seu próprio banco (`moodle-service-db`).
  * Apenas o `sync-service` mantém o estado das execuções, lock e fases da sincronização (`sync-service-db`).
  * Apenas o `api-gateway` valida o `SYNC_API_TOKEN` na borda externa.
  * Nenhum serviço acessa diretamente o banco de outro serviço nem os bancos internos do i-Educar ou do Moodle.
* **Alinhamento com as fronteiras da Clean Architecture do T1**: Reaproveitar a segregação de responsabilidades já estabelecida no monolito como fronteira natural entre serviços.
* **Manutenção das garantias de negócio e resiliência**: Preservar identidades determinísticas (`idnumber`), idempotência, validação *fail-fast* antes da publicação, matrículas de alunos em lotes de até 100 por curso, bloqueio de execução concorrente (`409 Conflict`) e tolerância a falhas via RabbitMQ.
* **Simplicidade operacional de desenvolvimento e avaliação**: Subir toda a topologia (i-Educar, Moodle, RabbitMQ, bancos, microsserviços e gateway) de forma reproduzível com um único `docker compose up`.
* **Escopo do T2**: O enunciado pede explicitamente reprojetar a integração do T1 como **microsserviços**; a decisão deste ADR é **como** decompor o monolito, não se manter um único deployável.

### 3. Critério de decomposição

A fronteira escolhida segue o próprio monolito T1 (Clean Architecture) e o enunciado (detecção na origem, orquestração com mapeamento de identificadores, publicação no destino):

* **Alta coesão e baixo acoplamento**: cada processo tem uma razão principal para mudar — borda, origem i-Educar, domínio da sync ou destino Moodle.
* ***Database per service***: estado de execução, inbox/idempotência e baseline de varredura ficam em bancos separados; credenciais de cada sistema externo ficam só no adaptador correspondente.
* **Escrita desacoplada no tempo**: o coordenador publica comandos via RabbitMQ para o adaptador Moodle absorver falhas do destino sem bloquear a API pública.

### 4. Decisão

Decompor o sistema em **3 microsserviços com bancos independentes + 1 API Gateway**, integrados via HTTP interno (borda e leitura da origem) e **RabbitMQ** (comandos e eventos de publicação no Moodle):

1. **`services/api-gateway/` (`:8000`)**: Ponto único de entrada público, responsável pela autenticação Bearer (`SYNC_API_TOKEN`), geração/propagação de `X-Correlation-Id`, métricas de borda e roteamento para o `sync-service`.
2. **`services/ieducar-service/` (`:8004` + `ieducar-service-db`)**: Encapsula as APIs REST e legada do i-Educar com timeout, retry e Circuit Breaker, expõe endpoints internos de leitura (`/internal/catalog`, `/internal/teachers`, `/internal/students`) e executa varreduras periódicas com detecção de mudanças por impressão digital (*fingerprint*) publicando avisos em `integration.events`.
3. **`services/sync-service/` (`:8002` interno + `sync-service-db`)**: Coordenador de domínio que executa validações *fail-fast*, mapeamento determinístico (`idnumber`), controle de concorrência (`409 Conflict`), orquestração em duas fases via RabbitMQ (`integration.commands` → `integration.events`) e acompanhamento de progresso (`GET /sync/{correlation_id}`).
4. **`services/moodle-service/` (`:8003` + `moodle-service-db`)**: Adaptador assíncrono do Moodle que consome comandos em `moodle.commands`, aplica idempotência (*Inbox Pattern*), reconciliação e Circuit Breaker contra as 8 funções Web Service do Moodle, publica resultados em `integration.events` e encaminha falhas para retry/DLQ.

### 5. Consequências e implicações

* **Positivas**:
  * Transição limpa a partir do código do T1 (`/monolith`), preservando regras de negócio e invariantes testadas.
  * Isolamento completo das complexidades da API legada do i-Educar e do protocolo RPC-over-REST do Moodle.
  * Escritas no Moodle toleram quedas temporárias, timeouts pós-commit e reinícios de containers sem perda nem duplicação.
* **Negativas / Trade-offs a gerenciar**:
  * Consistência eventual entre o `202 Accepted` e a conclusão final no Moodle, exigindo persistência de progresso por item no `sync-service-db`.
  * Necessidade de manter contratos de envelope/eventos (`v1`) sincronizados entre os serviços e documentados em [`architecture.md`](./architecture.md).
  * Necessidade de `healthcheck`, `depends_on` criterioso (sem acoplar adaptadores à saúde do sistema externo) e propagação de `X-Correlation-Id` para depuração distribuída.
  * Comunicação distribuída (HTTP interno + RabbitMQ), contratos versionados (`v1`), consistência eventual entre o `202 Accepted` e o efeito final no Moodle.

### 6. Artefatos relacionados

* Código de referência do Monolito (T1): [`/monolith`](../monolith)
* Diretórios dos novos componentes (T2):
  * [`services/api-gateway`](../services/api-gateway)
  * [`services/ieducar-service`](../services/ieducar-service)
  * [`services/sync-service`](../services/sync-service)
  * [`services/moodle-service`](../services/moodle-service)
* [ADR-002](#adr-002-moodle-service-adaptador-assíncrono-do-moodle)
* [Arquitetura e catálogo de eventos](./architecture.md)
* [Cenários de falha](./failure-scenarios.md)

---

## ADR-002: `moodle-service` (adaptador assíncrono do Moodle)

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito |
| **Data** | 2026-11-24 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Relacionado** | [ADR-001](#adr-001-decomposição-do-monolito-em-api-gateway-e-três-microsserviços-por-fronteira-de-responsabilidade), [catálogo de eventos v1](./architecture.md) |

### 1. Resumo

O `moodle-service` é o **adaptador de destino** da integração: consome comandos versionados no RabbitMQ (`integration.commands`), aplica idempotência e reconciliação no **banco próprio** (`moodle-service-db`) e chama as **8 funções** do Web Service REST do Moodle. Não expõe escrita síncrona para o `sync-service`; a orquestração fala com este serviço **apenas por mensageria** (comandos + eventos de resultado), complementada por HTTP interno só para *preflight* (`/internal/setup-check`) e auditoria (`/internal/runs/{correlation_id}`, replay da DLQ).

### 2. Problema e direcionadores

* Isolar `MOODLE_TOKEN`, timeouts, circuit breaker e semântica RPC-over-REST do Moodle.
* Garantir que indisponibilidade do Moodle (**Cenário 2**) não derrube o processo: filas absorvem carga, retry com TTL e DLQ.
* Preservar invariantes do T1: `idnumber` determinístico, reconciliação pós-commit (**Cenário 3**), Inbox por `message_id` (**Cenário 4**).
* Atender a rubrica: banco por serviço, consumidor idempotente, logs com `correlation_id`, métricas e health checks.

### 3. Opções consideradas

1. **REST síncrono** exposto ao `sync-service` (proxy direto ao Moodle).
2. **Worker apenas AMQP** (sem API HTTP).
3. **Híbrido**: AMQP para escrita + HTTP mínimo para saúde, métricas, *setup-check* e inspeção operacional.

### 4. Análise das opções

| Opção | Prós | Contras |
| :--- | :--- | :--- |
| REST síncrono | Simples de orquestrar num primeiro desenho | Acoplamento temporal ao Moodle; falha síncrona antes da fila anula demo do Cenário 2; duplica retry em dois lados |
| Só AMQP | Máximo desacoplamento | Dificulta *preflight* e debug na apresentação sem consumir fila |
| **Híbrido (escolhida)** | Fila para carga e resiliência; HTTP para observabilidade e validação opcional de WS | Dois protocolos a documentar (já cobertos no catálogo v1) |

### 5. Decisão

Adotar o **modelo híbrido**:

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

### 6. Consequências e implicações

**Positivas**

* `sync-service` publica comandos sem conhecer o Moodle; contrato v1 estável.
* Cenários 2–4 demonstráveis com compose, flags de falha e replay da DLQ.
* Evolução do cliente Moodle fica contida neste repositório.

**Negativas / trade-offs**

* O `sync-service` deve declarar fila/consumidor em `integration.events` e correlacionar por `idempotency_key`.
* *Setup-check* no início da sync pode falhar com Moodle offline — no sync isso é **best-effort** para não bloquear o fluxo assíncrono.
* Topologia RabbitMQ é declarada idempotentemente por este serviço; outros produtores/consumidores devem reutilizar os mesmos nomes de exchange/fila.

### 7. Artefatos relacionados

* Código: [`services/moodle-service/`](../services/moodle-service/)
* README operacional: [`services/moodle-service/README.md`](../services/moodle-service/README.md)
* Testes de cenário: [`services/moodle-service/tests/test_failure_scenarios.py`](../services/moodle-service/tests/test_failure_scenarios.py)

---

## ADR-003: Estratégia de Comunicação — Modelo Híbrido (HTTP/REST Síncrono + RabbitMQ Assíncrono)

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito |
| **Data** | 2026-11-26 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Relacionado** | [ADR-001](#adr-001-decomposição-do-monolito-em-api-gateway-e-três-microsserviços-por-fronteira-de-responsabilidade), [ADR-002](#adr-002-moodle-service-adaptador-assíncrono-do-moodle), [catálogo de eventos](./architecture.md) |

### 1. Resumo

Adotar comunicação **híbrida**: **HTTP/REST síncrono** na borda pública (`api-gateway -> sync-service`) e na leitura sob demanda da origem (`sync-service -> ieducar-service`), combinada com **mensageria assíncrona via RabbitMQ** no fluxo de escrita pesada no destino (`sync-service <-> moodle-service`) e nos avisos de mudança publicados pelo `ieducar-service` em `integration.events`.

### 2. Problema e direcionadores

* **Natureza distinta das operações**: a leitura do i-Educar para planejamento é uma consulta *read-only* que exige o retrato completo do catálogo para validação *fail-fast* antes de criar uma execução; já a publicação no Moodle envolve centenas/milhares de escritas lentas (177 cursos, 21 professores, 210 alunos e 1.770 matrículas) sujeitas a falhas parciais e indisponibilidade.
* **Desacoplamento temporal no destino**: o `POST /sync/*` deve responder `202 Accepted` sem travar o cliente esperando o Moodle processar todos os lotes.
* **Baixa complexidade operacional**: toda a solução precisa subir localmente com um único `docker compose up`, com volume moderado (~2 mil mensagens por sincronização completa) e observabilidade fácil na demonstração.
* **Isolamento da rede interna**: apenas o `api-gateway` (`:8000`) constitui a borda pública da sincronização; a comunicação HTTP interna trafega pela rede privada `integration` do Docker (`sync-service` sem porta publicada no host).

### 3. Opções consideradas

* **Opção 1 — 100% HTTP/REST síncrono entre todos os serviços**.
* **Opção 2 — Apache Kafka como broker de eventos**.
* **Opção 3 — Modelo Híbrido com RabbitMQ (HTTP síncrono para borda/leitura + RabbitMQ para escrita no Moodle e avisos de mudança)**.

### 4. Análise das opções

#### Opção 1 — 100% HTTP/REST síncrono
* **Prós**: Sem infraestrutura de broker; fluxo linear simples.
* **Contras**: Não atende ao requisito de comunicação assíncrona via broker; acopla o tempo de resposta do cliente à latência e disponibilidade do Moodle (Cenário 2 derruba toda a requisição).

#### Opção 2 — Apache Kafka
* **Prós**: Altíssima vazão, retenção longa de log e *replay* por *offset*.
* **Contras**: Complexidade operacional desproporcional para ~2 mil mensagens por sincronização (configuração de partições, *consumer groups*, DLQ/retry manual e maior consumo de memória no Docker Compose local).

#### Opção 3 — Modelo Híbrido com RabbitMQ (Escolhida)
* **Prós**:
  * **RabbitMQ** oferece baixa complexidade operacional para este volume, suporte nativo a roteamento por *topic exchanges*, fila de retry com TTL (`integration.retry` → `moodle.commands.retry` preservando *routing key* original), DLQ (`moodle.commands.dlq`) e interface web de gestão (`:15672`).
  * **HTTP síncrono** evita simular RPC sobre filas na leitura *fail-fast* do catálogo, devolvendo `400` ou `503` imediatamente quando a origem está inconsistente ou indisponível.
  * Contratos versionados (`v1`) no envelope JSON (`message_id`, `event_type`, `event_version`, `correlation_id`, `idempotency_key`, `payload`).
* **Contras**:
  * Dois paradigmas de comunicação a documentar e monitorar (HTTP + AMQP).
  * Os avisos `ieducar.*.changed.v1` já publicados pelo `ieducar-service` em `integration.events` ainda não são consumidos pelo `sync-service` (evolução natural para sincronização reativa automática).

### 5. Decisão

Adotar a **Opção 3 (Híbrido com RabbitMQ)**:
* `api-gateway -> sync-service` e `sync-service -> ieducar-service`: **HTTP/REST síncrono** na rede interna Docker `integration` com propagação obrigatória de `X-Correlation-Id`.
* `sync-service <-> moodle-service`: **RabbitMQ assíncrono** usando os exchanges duráveis `integration.commands`, `integration.events`, `integration.retry` e `integration.dlx`, com eventos versionados `.v1` documentados em [`architecture.md`](./architecture.md).

### 6. Consequências e implicações

* **Positivas**: Execução não bloqueante (`202 Accepted` + `GET /sync/{correlation_id}`), absorção de indisponibilidade do Moodle na fila e manutenção simples do broker.
* **Negativas / Trade-offs**: Consistência eventual entre a aceitação da requisição e a efetivação no Moodle; em ambiente de produção, a comunicação HTTP interna deverá adicionar autenticação serviço-a-serviço ou mTLS (*Zero Trust*) além do isolamento de rede.

---

## ADR-004: Estratégia de Resiliência e Tolerância a Falhas

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito |
| **Data** | 2026-11-26 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Relacionado** | [ADR-002](#adr-002-moodle-service-adaptador-assíncrono-do-moodle), [cenários de falha](./failure-scenarios.md) |

### 1. Resumo

Combinar **idempotência ponta a ponta** (*Inbox Pattern* + identidades determinísticas `idnumber`), **timeouts explícitos**, **retry com backoff exponencial e reconciliação prévia**, **Circuit Breaker** (sensível apenas a falhas transitórias), **manual ACK pós-commit** e **Dead-Letter Queue (DLQ)** com *replay* controlado.

### 2. Problema e direcionadores

* **Entrega *at-least-once* do RabbitMQ**: mensagens podem ser reentregues em quedas de container ou reconexões; processar duas vezes não pode duplicar cursos, usuários ou matrículas.
* **Quatro cenários de falha obrigatórios**: i-Educar fora do ar (Cenário 1), Moodle fora do ar (Cenário 2), resposta HTTP perdida após o Moodle criar o curso (Cenário 3) e reinício abrupto de serviço no meio da sincronização (Cenário 4).
* **Isolamento de falhas parciais**: um aluno sem e-mail válido ou uma mensagem malformada (*poison pill*) não pode travar o lote nem a fila.

### 3. Opções consideradas

* **Opção 1 — Retry simples sem estado local (requeue imediato no RabbitMQ)**.
* **Opção 2 — Resiliência em camadas com estado próprio (Inbox + Reconciliação + Retry em 2 níveis + Circuit Breaker + DLQ)**.

### 4. Análise das opções

#### Opção 1 — Retry simples com `requeue` imediato
* **Prós**: Poucas linhas de código.
* **Contras**: Causa *loop* infinito bloqueando o consumidor (*poison pill*), martela o Moodle quando está fora do ar e duplica cursos no Cenário 3 se não houver reconciliação prévia.

#### Opção 2 — Resiliência em camadas com estado próprio (Escolhida)
* **Prós**:
  * **Inbox (`processed_messages`) + `idnumber`**: impede reprocessamento de `message_id` já concluído e permite reconciliar mensagens interrompidas (`status = processing`) no Cenário 4.
  * **Reconciliação antes da escrita**: no retry do Cenário 3, o handler sempre consulta `find_course("idnumber")` antes de criar, encontrando o curso criado na tentativa anterior e marcando `reconciled=true` sem duplicar.
  * **Retry em 2 níveis**: backoff exponencial curto em memória (`LOCAL_MAX_RETRIES=2`) seguido de fila de atraso com TTL (`moodle.commands.retry`, até `RABBITMQ_MAX_RETRIES=3`) e encaminhamento final para `moodle.commands.dlq` com `POST /internal/dlq/replay`.
  * **Circuit Breaker (`CLOSED -> OPEN -> HALF_OPEN`)**: protege Moodle e i-Educar contra cascata de timeouts; erros permanentes de domínio (ex.: conflito de `shortname` ou e-mail) **não** abrem o circuito.
  * **Isolamento em lotes**: na Fase 2 do `sync-service`, alunos que falharam na Fase 1 são filtrados do lote em vez de cancelar a matrícula dos demais alunos válidos.
* **Contras**: Maior quantidade de estados intermediários para persistir e testar (`processing`, `succeeded`, `reconciled`, `dead_lettered`, `skipped`).

### 5. Decisão

Adotar a **Opção 2** em toda a cadeia:
* `moodle-service`: `MoodleClient` com `connect`/`read` timeouts + Circuit Breaker + retry local com reconciliação + `manual ACK` pós-commit + `moodle.commands.retry` + `moodle.commands.dlq` + hook opcional `FAULT_INJECT_DROP_AFTER_COURSE_CREATE` para reprodução determinística do Cenário 3.
* `ieducar-service`: `IEducarClient` com timeout + retry com backoff + Circuit Breaker + varredura periódica com recuperação automática após queda (Cenário 1a).
* `sync-service` e `api-gateway`: `asyncio.Lock` + `claim_phase2_publication` atômico no banco + respostas `503` sanitizadas quando a origem ou o serviço interno está indisponível.

### 6. Consequências e implicações

* **Positivas**: Todos os 4 cenários de falha são demonstráveis ao vivo via [`scripts/demo_failure_scenarios.sh`](../scripts/demo_failure_scenarios.sh) e cobertos por testes automatizados.
* **Negativas / Trade-offs**: Quando o i-Educar está fora no momento exato de um `POST /sync/*` manual (Cenário 1b), o `sync-service` retorna `503` imediato sem enfileirar a tentativa (o cliente precisa reexecutar após a volta da origem, enquanto a varredura periódica do `ieducar-service` retoma sozinha).

---

## ADR-005: Escolha da Stack Tecnológica

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito |
| **Data** | 2026-11-26 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Relacionado** | [ADR-001](#adr-001-decomposição-do-monolito-em-api-gateway-e-três-microsserviços-por-fronteira-de-responsabilidade), [ADR-002](#adr-002-moodle-service-adaptador-assíncrono-do-moodle) |

### 1. Resumo

Padronizar os 4 serviços do T2 (`api-gateway`, `ieducar-service`, `sync-service` e `moodle-service`) em **Python 3.12 + FastAPI + Uvicorn**, utilizando **`httpx`** (HTTP assíncrono), **`aio-pika`** (AMQP/RabbitMQ), **SQLAlchemy 2.0 + Alembic + PostgreSQL 16** (um banco por microsserviço), **Pydantic v2** (validação de contratos), **logs JSON estruturados** e **Prometheus (`prometheus-client`)**.

### 2. Problema e direcionadores

* **Reaproveitamento do T1**: todo o domínio validado do T1 (mapeamento de identidades, 8 funções WS do Moodle, paginação e API legada do i-Educar, regras *fail-fast* e casos de borda testados) já estava em Python/FastAPI.
* **Experiência do grupo com FastAPI**: a equipe já desenvolveu e operou o T1 nesse ecossistema; manter **FastAPI** reduz atrito e permite focar decomposição, mensageria e resiliência em vez de aprender outro framework na semana do T2.
* **Simplicidade adequada a microsserviços pequenos**: cada serviço expõe poucas rotas HTTP, *lifespan* claro (broker, scan, consumidor) e validação com Pydantic — sem a cerimônia de um framework maior por processo, o que combina com adaptadores finos (`api-gateway`, `ieducar-service`, `moodle-service`) e um coordenador (`sync-service`) enxuto.
* **Prazo curto de evolução**: entregar 4 serviços independentes, testes automatizados, cenários de falha e documentação completa em ~9 dias.
* **Uniformidade entre integrantes**: facilita que todos os membros do grupo dominem a base inteira para a apresentação individual (75% da nota).

### 3. Opções consideradas

* **Opção 1 — Reescrever em NestJS / TypeScript** (stack dos exemplos da disciplina).
* **Opção 2 — Stack poliglota** (ex.: NestJS no gateway/sync e Python nos adaptadores).
* **Opção 3 — Python 3.12 + FastAPI uniforme nos 4 serviços**.

### 4. Análise das opções

#### Opção 1 — NestJS / TypeScript
* **Prós**: Alinhado aos exemplos de sala; suporte embutido a módulos de microsserviços.
* **Contras**: Exigiria reescrever do zero toda a lógica de integração com as APIs legadas do i-Educar e do Moodle já validada no T1, aumentando risco de regressões no prazo curto.

#### Opção 2 — Stack poliglota
* **Prós**: Permite escolher a melhor ferramenta por serviço.
* **Contras**: Duplica padrões de logging, métricas, contratos Pydantic/DTOs, Dockerfiles e aumenta a curva de domínio individual de todo o grupo.

#### Opção 3 — Python 3.12 + FastAPI uniforme (Escolhida)
* **Prós**:
  * **FastAPI** escolhido pela familiaridade do grupo e pela simplicidade de montar vários processos pequenos (app `main.py` + rotas + injeção via `app.state` / *lifespan*), em linha com o tamanho real de cada microsserviço.
  * Migração direta e segura das invariantes do T1, com `httpx.AsyncClient` e `aio-pika` nativos no mesmo *event loop* `asyncio` do FastAPI.
  * SQLAlchemy 2.0 + Alembic permite rodar PostgreSQL 16 em Docker e SQLite em memória nos testes `pytest` ultrarrápidos (mais de 100 testes automatizados somando os 4 serviços).
  * Imagens `python:3.12-slim` leves e padronizadas.
* **Contras**: Não utiliza o framework NestJS dos exemplos de aula; exige disciplina manual para manter schemas espelhados (`schemas.py`) entre `sync-service`, `moodle-service` e `ieducar-service`.

### 5. Decisão

Adotar a **Opção 3 (Python 3.12 + FastAPI + `httpx` + `aio-pika` + PostgreSQL 16 + SQLAlchemy/Alembic + Prometheus)** em todos os serviços de integração, mantendo **RabbitMQ 3.13** como broker e **Docker Compose** como orquestrador único.

### 6. Consequências e implicações

* **Positivas**: Alta produtividade, código legível e consistente nos 4 serviços, observabilidade padronizada (`correlation_id` em JSON + `/health` + `/ready` + `/metrics`) e execução 100% reproduzível via `docker compose up`.
* **Negativas / Trade-offs**: Contratos de eventos são validados por schemas Pydantic espelhados em cada serviço (em evolução futura poderiam ser extraídos para um pacote compartilhado ou Schema Registry).

---

## ADR-006: Aviso de Mudança como Resumo por Varredura

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito |
| **Data** | 2026-11-26 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Relacionado** | [ADR-003](#adr-003-estratégia-de-comunicação--modelo-híbrido-httprest-síncrono--rabbitmq-assíncrono), [ADR-007](#adr-007-sincronização-automática-disparada-por-aviso-no-sync-service), [catálogo de eventos](./architecture.md) |

### 1. Resumo

Substituir os avisos `ieducar.{catalog,teacher,student}.changed.v1`, hoje publicados **um por registro alterado**, por **uma única mensagem por varredura** (`ieducar.scan.changed.v1`) que carrega o `scan_id` e, por tipo de entidade, se houve mudança e quantas foram detectadas. A mensagem só é publicada quando algum tipo acusou mudança; a varredura de baseline continua silenciosa.

### 2. Problema e direcionadores

* **Avalanche desproporcional ao trabalho real**: uma varredura que detecta 177 cursos alterados publica 177 mensagens que pedem ao consumidor exatamente a mesma coisa — a sincronização completa. O `planner` replaneja todo o catálogo a cada execução; não existe sincronização parcial por registro.
* **O consumidor executa uma coisa por vez**: `find_active_run` só filtra por `kind` quando `SYNC_LOCK_SCOPE=per_kind`, e o padrão é `global`. As 177 mensagens resultariam em 1 execução e 176 conflitos.
* **A informação agregada já existe**: o `SourceScanner` monta `by_entity` com `records_read`, `changes_detected`, `changes_published` e `baseline` por tipo de entidade no fim de cada varredura, e hoje esse resumo só aparece na resposta HTTP de `POST /internal/scan`.
* **Nenhum consumidor a quebrar**: a fila `sync.events` assina apenas `moodle.#`, portanto nenhuma mensagem `ieducar.*` é roteada hoje. Este é o momento mais barato para mudar o contrato.
* **O aviso é de tiro único**: o `ieducar-service` atualiza a impressão digital de baseline ao varrer, com ou sem consumidor. Um aviso perdido só volta quando aquele registro mudar outra vez, ou por `POST /internal/scan?full=true`.

### 3. Opções consideradas

* **Opção 1 — Manter um aviso por registro e resolver a avalanche no consumidor** (marca durável de "aviso pendente" no banco do `sync-service` e coalescência no fim da execução).
* **Opção 2 — Aviso por registro agrupado em lotes por tipo de entidade**.
* **Opção 3 — Um resumo por varredura com indicador por tipo de entidade**.

### 4. Análise das opções

#### Opção 1 — Aviso por registro com marca durável no consumidor
* **Prós**: Nenhum contrato de mensagem muda; o detalhe de qual registro mudou continua circulando no envelope.
* **Contras**: Exige uma segunda lista de pendências durável no banco do `sync-service`, duplicando o papel que a fila do RabbitMQ já cumpre. A marca não pode ser um booleano: por causa da trava `global`, ela precisa registrar **quais** `kind` estão devendo. E o disparo da execução extra fica amarrado ao caminho de ACK do evento do Moodle — um erro ali devolve a mensagem à fila e a borda de "execução concluída" se perde, porque `try_finalize_run` só a produz uma vez.

#### Opção 2 — Aviso por registro em lotes
* **Prós**: Reduz o número de mensagens sem perder o detalhe por registro.
* **Contras**: Mantém o número de mensagens proporcional ao tamanho da mudança sem nenhum ganho para o consumidor, que replaneja tudo de qualquer forma. Cria uma noção de lote que nada no consumidor usa.

#### Opção 3 — Resumo por varredura (Escolhida)
* **Prós**:
  * Colapsa a informação **onde ela já está agregada**, no fim da varredura, em vez de reconstituí-la no consumidor.
  * A fila do RabbitMQ passa a ser a própria lista de pendências durável: uma mensagem por varredura espera na fila enquanto o `sync-service` estiver ocupado ou fora do ar. Nenhuma tabela nova.
  * O indicador por tipo responde diretamente à pergunta do consumidor: quais execuções estão devendo.
  * O volume de mensagens deixa de depender do tamanho da mudança — no máximo uma por `SCAN_INTERVAL_SECONDS`.
* **Contras**:
  * O detalhe de qual registro mudou deixa de circular, e **não está persistido**: o `ieducar-service` tem apenas `source_fingerprints` e `scan_runs`, sem log de mudanças. Um tratamento futuro de remoção ("qual professor saiu") exigirá persistir as mudanças por varredura. O `scan_id` viaja na mensagem justamente para manter esse caminho aberto.

### 5. Decisão

Adotar a **Opção 3 (resumo por varredura)**:
* Ao fim de cada varredura bem-sucedida, o `ieducar-service` publica **uma** mensagem `ieducar.scan.changed.v1` em `integration.events`, no mesmo envelope versionado dos demais eventos (`message_id`, `event_type`, `event_version`, `correlation_id`, `idempotency_key`, `payload`), com `payload` contendo `scan_id`, `trigger` e, para `catalog`, `teacher` e `student`, se houve mudança e quantas foram detectadas.
* A mensagem **não** é publicada quando nenhum tipo acusou mudança, nem na varredura de baseline (`was_baseline`), preservando o comportamento atual de primeira varredura silenciosa.
* Os avisos por registro `ieducar.{catalog,teacher,student}.changed.v1` são removidos.
* `POST /internal/scan?full=true` continua sendo o procedimento de recuperação de aviso perdido: ignora as impressões digitais e, com este ADR, resulta em um resumo com todos os tipos marcados.

### 6. Consequências e implicações

* **Positivas**: Volume de mensagens limitado e previsível; nenhuma estrutura de pendência nova no `sync-service`; a decisão de o que sincronizar passa a ser tomada com a informação completa da varredura em vez de registro a registro.
* **Negativas / Trade-offs**: Perda do detalhe por registro no fluxo de mensagens — aceitável hoje porque o `planner` não sabe sincronizar parcialmente e porque não existe comando de remoção (`unenrol`, `delete`, `suspend`) em nenhum serviço. A métrica `CHANGES_PUBLISHED`, rotulada por `entity_type` e `change_type`, passa a contar mudanças **detectadas** e não mensagens publicadas, e precisa ser renomeada ou acompanhada de um contador separado de resumos publicados.

---

## ADR-007: Sincronização Automática Disparada por Aviso no `sync-service`

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito |
| **Data** | 2026-11-26 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Relacionado** | [ADR-003](#adr-003-estratégia-de-comunicação--modelo-híbrido-httprest-síncrono--rabbitmq-assíncrono), [ADR-004](#adr-004-estratégia-de-resiliência-e-tolerância-a-falhas), [ADR-006](#adr-006-aviso-de-mudança-como-resumo-por-varredura), [cenários de falha](./failure-scenarios.md) |

### 1. Resumo

Fechar o ciclo "mudou no i-Educar → aparece no Moodle" consumindo os resumos de varredura em uma **fila própria** (`sync.ieducar.notices`, assinando `ieducar.#`), separada da `sync.events` que recebe os resultados do Moodle (`moodle.#`). O consumidor de avisos traduz os indicadores em quais execuções disparar e reaproveita o coordenador existente. Quando a trava de execução única rejeita, a mensagem é **estacionada em uma fila de retry com TTL** contendo apenas os tipos que ainda faltam, e volta ao exchange por conta do broker.

### 2. Problema e direcionadores

* **Não existe ponto de espera no `sync-service`**: `_start` retorna assim que publica a fase 1, e a execução só se encerra quando os resultados do Moodle chegam e `try_finalize_run` a fecha. Não há como "aguardar a execução terminar" dentro do tratamento de uma mensagem.
* **Fila compartilhada com espera é deadlock**: se o aviso dividir a `sync.events` com os resultados do Moodle e o seu tratamento bloquear aguardando o fim da execução, os resultados de que essa execução depende ficam presos atrás dele, na mesma fila, com o mesmo consumidor. A execução nunca termina e o bloqueio nunca se desfaz.
* **Segurar mensagem não é espera confiável**: o RabbitMQ 3.13 (`rabbitmq:3.13-management-alpine`) encerra o canal de uma mensagem sem ACK por tempo prolongado (30 minutos por padrão) e a reenfileira.
* **A trava é global por padrão**: `find_active_run` ignora o `kind` quando `SYNC_LOCK_SCOPE=global`, então qualquer execução em andamento bloqueia qualquer outra.
* **Catálogo afeta os dois lados**: `plan_teacher_sync` cria os cursos de todo o catálogo e `plan_student_sync` indexa as matrículas pelo mesmo catálogo. Mudança de catálogo exige professores **e** alunos, em duas execuções.
* **O mecanismo de espera já existe no projeto**: o `moodle-service` reenvia comandos por uma fila de TTL com dead-letter de volta ao exchange principal (`moodle.commands.retry`, com `x-message-ttl` e `x-dead-letter-exchange`).

### 3. Opções consideradas

* **Opção 1 — Acrescentar o binding `ieducar.#` à fila `sync.events` existente** e ramificar o consumidor atual por routing key.
* **Opção 2 — Fila dedicada, com o consumidor segurando a mensagem sem ACK** até a execução terminar.
* **Opção 3 — Fila dedicada, com estacionamento em fila de retry com TTL** quando a trava rejeitar.

### 4. Análise das opções

#### Opção 1 — Binding adicional na fila existente
* **Prós**: Menos topologia a declarar; um consumidor só.
* **Contras**: Mistura duas famílias de mensagem com tempos de processamento muito diferentes em uma fila com um consumidor. A leitura da origem feita no caminho do aviso atrasa os resultados do Moodle que estão atrás na mesma fila (*head-of-line blocking*). O `_on_message` atual chama `handle_moodle_event` sem olhar a routing key e passaria a acumular duas responsabilidades.

#### Opção 2 — Fila dedicada com espera bloqueante
* **Prós**: A regra "uma execução por vez" fica explícita no código do consumidor.
* **Contras**: Depende de um ponto de espera que não existe no coordenador; esbarra no `consumer_timeout` do broker; e transforma indisponibilidade da origem em mensagem presa por tempo indeterminado.

#### Opção 3 — Fila dedicada com estacionamento em retry (Escolhida)
* **Prós**:
  * Os dois caminhos ficam isolados: o consumidor de resultados não muda em nada e nunca espera pelo lado do i-Educar.
  * A espera é feita pelo broker, com TTL configurável, sem timer no código da aplicação.
  * A mensagem estacionada é durável e sobrevive a reinício do serviço.
  * Republicar **somente os tipos que faltam** evita repetir trabalho já concluído.
  * Reaproveita um padrão de topologia que o projeto já usa e já demonstra.
* **Contras**:
  * Mais topologia a declarar: uma fila de avisos, uma fila de retry e o exchange de retry correspondente.
  * A latência entre detecção e execução passa a ter dois componentes: o intervalo de varredura e o TTL de estacionamento.
  * Mensagens estacionadas repetidamente precisam de limite, para não girar indefinidamente enquanto a origem estiver fora do ar.

### 5. Decisão

Adotar a **Opção 3 (fila dedicada com estacionamento em retry)**:
* **Topologia nova no `sync-service`**: fila durável `sync.ieducar.notices` ligada a `integration.events` pelo binding `ieducar.#`, e fila `sync.ieducar.retry` com `x-message-ttl` e `x-dead-letter-exchange` apontando de volta para `integration.events`, no mesmo padrão de `moodle.commands.retry`.
* O estacionamento usa **exchange próprio**, `integration.ieducar.retry`, e não o `integration.retry` do `moodle-service`: a fila `moodle.commands.retry` assina `#` naquele exchange e levaria o resumo, por dead-letter, para `integration.commands` — isto é, para a fila de comandos do Moodle. O esgotamento vai para `sync.ieducar.dlq`, ligada ao `integration.dlx` já existente pela routing key `sync.ieducar.dlq`.
* A fila `sync.events` continua assinando **apenas** `moodle.#`, e o `MoodleEventsConsumer` atual não é alterado.
* **Consumidor novo, dedicado aos avisos**: lê os indicadores do resumo e monta a lista de execuções devidas — `teacher` → `start_teachers`, `student` → `start_students`, `catalog` → ambas.
* Para cada execução devida, chamar o coordenador existente e tratar o `status` de retorno:
  * `accepted` — segue para a próxima execução devida;
  * `conflict` ou `origin_unavailable` — republica o resumo na fila de retry contendo **apenas** os tipos ainda não iniciados, e dá ACK na mensagem original;
  * `rejected` ou `duplicate_correlation_id` — dá ACK e descarta, registrando o motivo no log com o `correlation_id`.
* O envelope estacionado carrega um contador de tentativas; ao exceder o limite configurável, a mensagem vai para a DLQ em vez de voltar ao exchange.
* O `planner`, o `SyncCoordinator` e as rotas `POST /sync/*` **não mudam**: o caminho automático e o caminho manual chamam o mesmo coordenador.

### 6. Consequências e implicações

* **Positivas**: O ciclo se fecha sem intervenção manual, sem tabela nova e sem laço de fundo no `sync-service`. O caminho que hoje funciona (`POST /sync/*` mais os resultados do Moodle) permanece intacto. A serialização das execuções é delegada ao broker, que já é a peça responsável por isso no resto do sistema.
* **Negativas / Trade-offs**: A latência de propagação passa a ser até `SCAN_INTERVAL_SECONDS` mais o TTL de estacionamento multiplicado pelo número de execuções devidas. Duas filas e um exchange a mais na topologia, na demonstração e no painel do RabbitMQ. Com a trava `global` (padrão), um resumo que marca catálogo custa sempre pelo menos uma volta de estacionamento, porque a segunda execução só pode começar depois que a primeira fechar.
