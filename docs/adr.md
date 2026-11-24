# Registro de Decisões Arquiteturais (ADR) — T2: Microsserviços i-Educar × Moodle

> Estrutura baseada no template **GIG Cymru / NHS Wales** ([decision-record-template-by-gig-cymru-nhs-wales](https://github.com/architecture-decision-record/architecture-decision-record/tree/main/locales/en-001/templates/decision-record-template-by-gig-cymru-nhs-wales)).

---

## ADR-001: Decomposição do Monolito em API Gateway e Três Microsserviços por Fronteira de Responsabilidade

| Metadado | Valor |
| :--- | :--- |
| **Status** | Aceito (*Accepted*) |
| **Data** | 2026-11-19 |
| **Decisores** | Equipe do Projeto (T2 — Projeto e Arquitetura de Software) |
| **Referência Base** | Monolito T1 (`/monolith`) |

### 1. Summary (Resumo)

No T1, a integração unidirecional `i-Educar -> Moodle` (sincronização de 177 cursos, 21 professores, 210 alunos ativos e 1.770 matrículas) foi implementada como um monolito Python/FastAPI estruturado em MVC + Clean Architecture. Para o T2, decidiu-se decompor o monolito em **1 API Gateway (`api-gateway`) e 3 microsserviços (`ieducar-service`, `sync-service` e `moodle-service`)**, mantidos em um único repositório (*monorepo*) e orquestrados em conjunto via Docker Compose.

### 2. Issue / Drivers (Problema e Direcionadores Arquiteturais)

O monolito do T1 concentra no mesmo processo a autenticação da API pública, o controle de concorrência/progresso, as regras de domínio e validação *fail-fast*, além dos clientes HTTP específicos para duas APIs distintas do i-Educar (REST v2 e legada `/module/Api/*`) e para o Web Service REST do Moodle (`/webservice/rest/server.php`).

Os principais direcionadores (*drivers*) para a decisão de decomposição no T2 são:

* **Preservação do contrato externo (`:8000`)**: Manter compatibilidade com os endpoints públicos existentes (`GET /health`, `POST /sync/teachers` e `POST /sync/students` autenticados via `Authorization: Bearer <SYNC_API_TOKEN>`).
* **Isolamento de sistemas externos e credenciais (*Information Hiding*)**:
  * Apenas o serviço adaptador do i-Educar deve conhecer `IEDUCAR_TOKEN`, `IEDUCAR_ACCESS_KEY` e as peculiaridades da API legada do i-Educar.
  * Apenas o serviço adaptador do Moodle deve conhecer `MOODLE_TOKEN` e a assinatura das 8 funções do Web Service REST do Moodle.
  * Apenas a borda externa deve validar o `SYNC_API_TOKEN`.
* **Alinhamento com as fronteiras da Clean Architecture do T1**: Reaproveitar a segregação de interfaces já estabelecida em `app/application/contracts.py` (`StudentSource`, `AccountRepository`, `CourseRepository`, `TeacherEnrolmentDestination`, `StudentEnrolmentDestination`, `UserDestination`) como fronteira natural entre serviços.
* **Manutenção das garantias de negócio**: Preservar identidades determinísticas (`idnumber`), idempotência, validação *fail-fast* antes de qualquer escrita no destino, matrículas de alunos em lotes de até 100 por curso e bloqueio de execução concorrente (`409 Conflict` com snapshot de progresso).
* **Simplicidade operacional de desenvolvimento e avaliação**: Subir toda a topologia (i-Educar, Moodle, bancos, microsserviços e gateway) de forma reproduzível com um único `docker-compose.yml`.

### 3. Options (Opções Consideradas)

* **Opção 1 — Manter o Monolito Modular (Baseline T1)**: Conservar um único serviço FastAPI contendo rotas, autenticação, orquestrador e clientes HTTP para i-Educar e Moodle.
* **Opção 2 — Decomposição por Fluxo Funcional (`api-gateway` + `teacher-sync-service` + `student-sync-service`)**: Separar um microsserviço para sincronização de professores/cursos e outro para sincronização de alunos/matrículas.
* **Opção 3 — Decomposição por Fronteira de Integração e Orquestração (`api-gateway` + `ieducar-service` + `sync-service` + `moodle-service`)**: Separar um ponto único de entrada (`api-gateway`), um microsserviço extrator da origem (`ieducar-service`), um microsserviço coordenador de domínio (`sync-service`) e um microsserviço adaptador de destino (`moodle-service`).

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
  * **Mapeamento 1:1 com os contratos do T1**: Os `Protocol`s de `contracts.py` deixam de chamar clientes diretos do i-Educar/Moodle e passam a consumir endpoints internos limpos de `ieducar-service` e `moodle-service`.
  * **Segurança e isolamento de configuração**: Credenciais de cada sistema ficam restritas ao respectivo microsserviço no `docker-compose.yml`.
* **Contras**:
  * Introduz saltos HTTP internos (`api-gateway -> sync-service -> ieducar-service / moodle-service`), exigindo timeouts adequados e propagação clara de erros (`IntegrationError` / códigos HTTP `502`/`503`).

### 5. Recommendation (Recomendação e Decisão)

Adotar a **Opção 3**: decompor o sistema em **3 microsserviços + 1 API Gateway** organizados em um *monorepo* com a seguinte divisão de responsabilidades:

1. **`api-gateway/`**: Ponto único de entrada na porta `:8000`, responsável pela autenticação Bearer (`SYNC_API_TOKEN`) e pelo roteamento das requisições públicas (`/health`, `POST /sync/teachers`, `POST /sync/students`) para o `sync-service`.
2. **`ieducar-service/`**: Microsserviço de extração que encapsula o `IEducarClient` (API REST `/api/registration` e API legada `/module/Api/*`), expondo endpoints internos padronizados para consulta de alunos, professores e catálogo de turmas/disciplinas.
3. **`sync-service/`**: Microsserviço central de domínio que contém Models (`account`, `course`, `enrollment`), Views (`sync_view`), Orquestrador/Strategies (`TeacherSyncStrategy`, `StudentSyncStrategy`), validação *fail-fast*, controle de concorrência (`SyncExecutionLock` / `409 Conflict`) e tracking de progresso (`SyncProgress`), consumindo `ieducar-service` e `moodle-service` via HTTP.
4. **`moodle-service/`**: Microsserviço adaptador que encapsula o `MoodleClient` (`/webservice/rest/server.php`), expondo endpoints internos para verificação de setup, busca/criação idempotente de usuários e cursos, e matrícula de professores e alunos em lote.

### 6. Consequences / Implications (Consequências e Implicações)

* **Positivas**:
  * Transição limpa a partir do código do T1 (`/monolith`), preservando as regras de negócio, modelos e testes de unidade/contrato.
  * Isolamento completo das complexidades da API legada do i-Educar e do protocolo RPC-over-REST do Moodle atrás de APIs REST internas semânticas.
  * Contrato externo inalterado na porta `:8000`, permitindo validar o T2 com o mesmo `rest-client.http` do T1.
* **Negativas / Trade-offs a gerenciar**:
  * Necessidade de definir contratos HTTP/JSON explícitos entre `sync-service` e os serviços `ieducar-service` e `moodle-service`.
  * Necessidade de configurar `healthcheck` e `depends_on` no `docker-compose.yml` para garantir a ordem de inicialização e diagnóstico rápido de falhas entre containers.

### 7. Related (Artefatos Relacionados)

* Código de referência do Monolito (T1): [`/monolith`](../monolith)
* Diretórios dos novos componentes (T2):
  * [`/api-gateway`](../api-gateway)
  * [`/ieducar-service`](../ieducar-service)
  * [`/sync-service`](../sync-service)
  * [`/moodle-service`](../moodle-service)
