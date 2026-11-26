# Registro de Decisões Arquiteturais (ADR) — T2: Microsserviços i-Educar × Moodle

> Estrutura baseada no template **GIG Cymru / NHS Wales** ([decision-record-template-by-gig-cymru-nhs-wales](https://github.com/architecture-decision-record/architecture-decision-record/tree/main/locales/en-001/templates/decision-record-template-by-gig-cymru-nhs-wales)).

---

## ADR-001: Decomposição do Monolito em API Gateway e Três Microsserviços por Fronteira de Responsabilidade

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito (*Accepted*) |
| **Data** | 2026-11-19 (atualizado em 2026-11-25) |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Referência Base** | Monolito T1 (`/monolith`) |

### 1. Summary (Resumo)

No T1, a integração unidirecional `i-Educar -> Moodle` (sincronização de 177 cursos, 21 professores, 210 alunos ativos e 1.770 matrículas) foi implementada como um monolito Python/FastAPI estruturado em MVC + Clean Architecture. Para o T2, decidiu-se decompor o monolito em **1 API Gateway (`api-gateway`) e 3 microsserviços (`ieducar-service`, `sync-service` e `moodle-service`)**, cada microsserviço com **seu próprio banco PostgreSQL isolado** e comunicação assíncrona via **RabbitMQ** para a publicação no Moodle, mantidos em um único repositório (*monorepo*) e orquestrados via Docker Compose.

### 2. Issue / Drivers (Problema e Direcionadores Arquiteturais)

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

### 3. Options (Opções Consideradas)

* **Opção 1 — Manter o Monolito Modular (Baseline T1)**: Conservar um único serviço FastAPI contendo rotas, autenticação, orquestrador e clientes HTTP para i-Educar e Moodle.
* **Opção 2 — Decomposição por Fluxo Funcional (`api-gateway` + `teacher-sync-service` + `student-sync-service`)**: Separar um microsserviço para sincronização de professores/cursos e outro para sincronização de alunos/matrículas.
* **Opção 3 — Decomposição por Fronteira de Integração e Orquestração (`api-gateway` + `ieducar-service` + `sync-service` + `moodle-service`)**: Separar um ponto único de entrada (`api-gateway`), um microsserviço extrator/detector da origem (`ieducar-service`), um microsserviço coordenador de domínio (`sync-service`) e um microsserviço adaptador de destino (`moodle-service`).

### 4. Options Analysis (Análise das Opções)

#### Opção 1 — Manter o Monolito Modular (Baseline T1)
* **Prós**:
  * Zero latência de rede entre o orquestrador e os clientes de integração.
  * Menor complexidade operacional no Docker Compose.
* **Contras**:
  * Não atende ao objetivo arquitetural do T2 de evolução para microsserviços.
  * Acopla mudanças de protocolo do i-Educar ou do Moodle ao mesmo artefato de deploy que contém as regras de domínio e autenticação pública.

#### Opção 2 — Decomposição por Fluxo Funcional (`teacher-sync-service` + `student-sync-service`)
* **Prós**:
  * Isola a execução do fluxo de professores do fluxo de alunos em processos distintos.
* **Contras**:
  * **Alta duplicação de código e acoplamento**: Ambos os serviços precisariam acessar tanto o i-Educar quanto o Moodle, duplicando clientes HTTP, credenciais (`IEDUCAR_*` e `MOODLE_*`), mapeamento de identidades (`idnumber`) e resolução de cursos.
  * Mudanças na API do i-Educar ou do Moodle exigiriam alterar e reimplantar múltiplos microsserviços simultaneamente.

#### Opção 3 — Decomposição por Fronteira de Integração e Orquestração (`api-gateway` + `ieducar-service` + `sync-service` + `moodle-service`)
* **Prós**:
  * **Alta coesão e baixo acoplamento**: Cada microsserviço tem uma única razão para mudar (`ieducar-service` muda se o i-Educar mudar; `moodle-service` muda se o Moodle mudar; `sync-service` muda se a regra de sincronização/mapeamento mudar; `api-gateway` concentra autenticação e roteamento de borda).
  * **Propriedade clara de dados**: Cada serviço gerencia exclusivamente seu próprio banco PostgreSQL em rede isolada.
  * **Desacoplamento temporal na escrita**: Permite usar RabbitMQ entre `sync-service` e `moodle-service`, absorvendo indisponibilidade do Moodle com retry e DLQ.
  * **Segurança e isolamento de configuração**: Credenciais de cada sistema ficam restritas ao respectivo microsserviço no `docker-compose.yml`.
* **Contras**:
  * Introduz comunicação distribuída (HTTP interno + RabbitMQ), exigindo contratos versionados (`v1`), *correlation ID* fim-a-fim, health checks e tratamento explícito de consistência eventual.

### 5. Recommendation (Recomendação e Decisão)

Adotar a **Opção 3**: decompor o sistema em **3 microsserviços com bancos independentes + 1 API Gateway**, integrados via HTTP interno (borda e leitura da origem) e **RabbitMQ** (comandos e eventos de publicação no Moodle):

1. **`services/api-gateway/` (`:8000`)**: Ponto único de entrada público, responsável pela autenticação Bearer (`SYNC_API_TOKEN`), geração/propagação de `X-Correlation-Id`, métricas de borda e roteamento para o `sync-service`.
2. **`services/ieducar-service/` (`:8004` + `ieducar-service-db`)**: Encapsula as APIs REST e legada do i-Educar com timeout, retry e Circuit Breaker, expõe endpoints internos de leitura (`/internal/catalog`, `/internal/teachers`, `/internal/students`) e executa varreduras periódicas com detecção de mudanças por impressão digital (*fingerprint*) publicando avisos em `integration.events`.
3. **`services/sync-service/` (`:8002` interno + `sync-service-db`)**: Coordenador de domínio que executa validações *fail-fast*, mapeamento determinístico (`idnumber`), controle de concorrência (`409 Conflict`), orquestração em duas fases via RabbitMQ (`integration.commands` → `integration.events`) e acompanhamento de progresso (`GET /sync/{correlation_id}`).
4. **`services/moodle-service/` (`:8003` + `moodle-service-db`)**: Adaptador assíncrono do Moodle que consome comandos em `moodle.commands`, aplica idempotência (*Inbox Pattern*), reconciliação e Circuit Breaker contra as 8 funções Web Service do Moodle, publica resultados em `integration.events` e encaminha falhas para retry/DLQ.

### 6. Consequences / Implications (Consequências e Implicações)

* **Positivas**:
  * Transição limpa a partir do código do T1 (`/monolith`), preservando regras de negócio e invariantes testadas.
  * Isolamento completo das complexidades da API legada do i-Educar e do protocolo RPC-over-REST do Moodle.
  * Escritas no Moodle toleram quedas temporárias, timeouts pós-commit e reinícios de containers sem perda nem duplicação.
* **Negativas / Trade-offs a gerenciar**:
  * Consistência eventual entre o `202 Accepted` e a conclusão final no Moodle, exigindo persistência de progresso por item no `sync-service-db`.
  * Necessidade de manter contratos de envelope/eventos (`v1`) sincronizados entre os serviços e documentados em [`architecture.md`](./architecture.md).
  * Necessidade de `healthcheck`, `depends_on` criterioso (sem acoplar adaptadores à saúde do sistema externo) e propagação de `X-Correlation-Id` para depuração distribuída.

### 7. Related (Artefatos Relacionados)

* Código de referência do Monolito (T1): [`/monolith`](../monolith)
* Diretórios dos novos componentes (T2):
  * [`services/api-gateway`](../services/api-gateway)
  * [`services/ieducar-service`](../services/ieducar-service)
  * [`services/sync-service`](../services/sync-service)
  * [`services/moodle-service`](../services/moodle-service)
* [ADR-002: `moodle-service` (adaptador Moodle)](./adr-moodle-service.md)
* [Arquitetura e Catálogo de Eventos](./architecture.md)
* [Cenários de Falha](./failure-scenarios.md)

---

## ADR-003: Estratégia de Comunicação — Modelo Híbrido (HTTP/REST Síncrono + RabbitMQ Assíncrono)

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito (*Accepted*) |
| **Data** | 2026-11-26 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Relacionado** | [ADR-001](#adr-001-decomposição-do-monolito-em-api-gateway-e-três-microsserviços-por-fronteira-de-responsabilidade), [ADR-002](./adr-moodle-service.md), [Catálogo de Eventos](./architecture.md) |

### 1. Summary (Resumo)

Adotar comunicação **híbrida**: **HTTP/REST síncrono** na borda pública (`api-gateway -> sync-service`) e na leitura sob demanda da origem (`sync-service -> ieducar-service`), combinada com **mensageria assíncrona via RabbitMQ** no fluxo de escrita pesada no destino (`sync-service <-> moodle-service`) e nos avisos de mudança publicados pelo `ieducar-service` em `integration.events`.

### 2. Issue / Drivers (Problema e Direcionadores)

* **Natureza distinta das operações**: a leitura do i-Educar para planejamento é uma consulta *read-only* que exige o retrato completo do catálogo para validação *fail-fast* antes de criar uma execução; já a publicação no Moodle envolve centenas/milhares de escritas lentas (177 cursos, 21 professores, 210 alunos e 1.770 matrículas) sujeitas a falhas parciais e indisponibilidade.
* **Desacoplamento temporal no destino**: o `POST /sync/*` deve responder `202 Accepted` sem travar o cliente esperando o Moodle processar todos os lotes.
* **Baixa complexidade operacional**: toda a solução precisa subir localmente com um único `docker compose up`, com volume moderado (~2 mil mensagens por sincronização completa) e observabilidade fácil na demonstração.
* **Isolamento da rede interna**: apenas o `api-gateway` (`:8000`) constitui a borda pública da sincronização; a comunicação HTTP interna trafega pela rede privada `integration` do Docker (`sync-service` sem porta publicada no host).

### 3. Options (Opções Consideradas)

* **Opção 1 — 100% HTTP/REST síncrono entre todos os serviços**.
* **Opção 2 — Apache Kafka como broker de eventos**.
* **Opção 3 — Modelo Híbrido com RabbitMQ (HTTP síncrono para borda/leitura + RabbitMQ para escrita no Moodle e avisos de mudança)**.

### 4. Options Analysis (Análise das Opções)

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

### 5. Recommendation (Decisão)

Adotar a **Opção 3 (Híbrido com RabbitMQ)**:
* `api-gateway -> sync-service` e `sync-service -> ieducar-service`: **HTTP/REST síncrono** na rede interna Docker `integration` com propagação obrigatória de `X-Correlation-Id`.
* `sync-service <-> moodle-service`: **RabbitMQ assíncrono** usando os exchanges duráveis `integration.commands`, `integration.events`, `integration.retry` e `integration.dlx`, com eventos versionados `.v1` documentados em [`architecture.md`](./architecture.md).

### 6. Consequences / Implications (Consequências e Implicações)

* **Positivas**: Execução não bloqueante (`202 Accepted` + `GET /sync/{correlation_id}`), absorção de indisponibilidade do Moodle na fila e manutenção simples do broker.
* **Negativas / Trade-offs**: Consistência eventual entre a aceitação da requisição e a efetivação no Moodle; em ambiente de produção, a comunicação HTTP interna deverá adicionar autenticação serviço-a-serviço ou mTLS (*Zero Trust*) além do isolamento de rede.

---

## ADR-004: Estratégia de Resiliência e Tolerância a Falhas

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito (*Accepted*) |
| **Data** | 2026-11-26 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Relacionado** | [ADR-002](./adr-moodle-service.md), [Cenários de Falha](./failure-scenarios.md) |

### 1. Summary (Resumo)

Combinar **idempotência ponta a ponta** (*Inbox Pattern* + identidades determinísticas `idnumber`), **timeouts explícitos**, **retry com backoff exponencial e reconciliação prévia**, **Circuit Breaker** (sensível apenas a falhas transitórias), **manual ACK pós-commit** e **Dead-Letter Queue (DLQ)** com *replay* controlado.

### 2. Issue / Drivers (Problema e Direcionadores)

* **Entrega *at-least-once* do RabbitMQ**: mensagens podem ser reentregues em quedas de container ou reconexões; processar duas vezes não pode duplicar cursos, usuários ou matrículas.
* **Quatro cenários de falha obrigatórios**: i-Educar fora do ar (Cenário 1), Moodle fora do ar (Cenário 2), resposta HTTP perdida após o Moodle criar o curso (Cenário 3) e reinício abrupto de serviço no meio da sincronização (Cenário 4).
* **Isolamento de falhas parciais**: um aluno sem e-mail válido ou uma mensagem malformada (*poison pill*) não pode travar o lote nem a fila.

### 3. Options (Opções Consideradas)

* **Opção 1 — Retry simples sem estado local (requeue imediato no RabbitMQ)**.
* **Opção 2 — Resiliência em camadas com estado próprio (Inbox + Reconciliação + Retry em 2 níveis + Circuit Breaker + DLQ)**.

### 4. Options Analysis (Análise das Opções)

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

### 5. Recommendation (Decisão)

Adotar a **Opção 2** em toda a cadeia:
* `moodle-service`: `MoodleClient` com `connect`/`read` timeouts + Circuit Breaker + retry local com reconciliação + `manual ACK` pós-commit + `moodle.commands.retry` + `moodle.commands.dlq` + hook opcional `FAULT_INJECT_DROP_AFTER_COURSE_CREATE` para reprodução determinística do Cenário 3.
* `ieducar-service`: `IEducarClient` com timeout + retry com backoff + Circuit Breaker + varredura periódica com recuperação automática após queda (Cenário 1a).
* `sync-service` e `api-gateway`: `asyncio.Lock` + `claim_phase2_publication` atômico no banco + respostas `503` sanitizadas quando a origem ou o serviço interno está indisponível.

### 6. Consequences / Implications (Consequências e Implicações)

* **Positivas**: Todos os 4 cenários de falha são demonstráveis ao vivo via [`scripts/demo_failure_scenarios.sh`](../scripts/demo_failure_scenarios.sh) e cobertos por testes automatizados.
* **Negativas / Trade-offs**: Quando o i-Educar está fora no momento exato de um `POST /sync/*` manual (Cenário 1b), o `sync-service` retorna `503` imediato sem enfileirar a tentativa (o cliente precisa reexecutar após a volta da origem, enquanto a varredura periódica do `ieducar-service` retoma sozinha).

---

## ADR-005: Escolha da Stack Tecnológica

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito (*Accepted*) |
| **Data** | 2026-11-26 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Relacionado** | [ADR-001](#adr-001-decomposição-do-monolito-em-api-gateway-e-três-microsserviços-por-fronteira-de-responsabilidade), [ADR-002](./adr-moodle-service.md) |

### 1. Summary (Resumo)

Padronizar os 4 serviços do T2 (`api-gateway`, `ieducar-service`, `sync-service` e `moodle-service`) em **Python 3.12 + FastAPI + Uvicorn**, utilizando **`httpx`** (HTTP assíncrono), **`aio-pika`** (AMQP/RabbitMQ), **SQLAlchemy 2.0 + Alembic + PostgreSQL 16** (um banco por microsserviço), **Pydantic v2** (validação de contratos), **logs JSON estruturados** e **Prometheus (`prometheus-client`)**.

### 2. Issue / Drivers (Problema e Direcionadores)

* **Reaproveitamento do T1**: todo o domínio validado do T1 (mapeamento de identidades, 8 funções WS do Moodle, paginação e API legada do i-Educar, regras *fail-fast* e casos de borda testados) já estava em Python/FastAPI.
* **Experiência do grupo com FastAPI**: a equipe já desenvolveu e operou o T1 nesse ecossistema; manter **FastAPI** reduz atrito e permite focar decomposição, mensageria e resiliência em vez de aprender outro framework na semana do T2.
* **Simplicidade adequada a microsserviços pequenos**: cada serviço expõe poucas rotas HTTP, *lifespan* claro (broker, scan, consumidor) e validação com Pydantic — sem a cerimônia de um framework maior por processo, o que combina com adaptadores finos (`api-gateway`, `ieducar-service`, `moodle-service`) e um coordenador (`sync-service`) enxuto.
* **Prazo curto de evolução**: entregar 4 serviços independentes, testes automatizados, cenários de falha e documentação completa em ~9 dias.
* **Uniformidade entre integrantes**: facilita que todos os membros do grupo dominem a base inteira para a apresentação individual (75% da nota).

### 3. Options (Opções Consideradas)

* **Opção 1 — Reescrever em NestJS / TypeScript** (stack dos exemplos da disciplina).
* **Opção 2 — Stack poliglota** (ex.: NestJS no gateway/sync e Python nos adaptadores).
* **Opção 3 — Python 3.12 + FastAPI uniforme nos 4 serviços**.

### 4. Options Analysis (Análise das Opções)

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

### 5. Recommendation (Decisão)

Adotar a **Opção 3 (Python 3.12 + FastAPI + `httpx` + `aio-pika` + PostgreSQL 16 + SQLAlchemy/Alembic + Prometheus)** em todos os serviços de integração, mantendo **RabbitMQ 3.13** como broker e **Docker Compose** como orquestrador único.

### 6. Consequences / Implications (Consequências e Implicações)

* **Positivas**: Alta produtividade, código legível e consistente nos 4 serviços, observabilidade padronizada (`correlation_id` em JSON + `/health` + `/ready` + `/metrics`) e execução 100% reproduzível via `docker compose up`.
* **Negativas / Trade-offs**: Contratos de eventos são validados por schemas Pydantic espelhados em cada serviço (em evolução futura poderiam ser extraídos para um pacote compartilhado ou Schema Registry).
