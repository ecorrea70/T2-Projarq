# Critérios de aceitação — T2

Cada critério é uma afirmação verificável: ou a evidência existe e é reproduzível por quem corrige, ou
o critério não está atendido. Nenhum critério conta como pronto sem o **como verificar** funcionando a
partir de um clone limpo do repositório.

Legenda: **[O]** obrigatório pelo enunciado · **[10]** barra de nota 10 (os "desejáveis" do enunciado e o
rigor que separa um trabalho que funciona de um trabalho excelente).

Instruções de execução ficam no [README](../README.md) e nos READMEs de cada serviço. As decisões estão
em [`adr.md`](./adr.md) e [`adr-moodle-service.md`](./adr-moodle-service.md).

## A. Execução e ambiente

| # | Critério | Como verificar |
| --- | --- | --- |
| CA-01 **[O]** | Em um clone limpo, `docker compose up -d --build` sobe **toda** a solução: os serviços, o RabbitMQ, os bancos e as instâncias ou simuladores de i-Educar e Moodle. Nenhum passo manual além de `cp .env.example .env` e preencher os segredos. | Clonar em uma pasta nova, seguir o quickstart do README e executar o roteiro de sincronização. |
| CA-02 **[O]** | O README traz instruções de execução e um roteiro que reproduz uma sincronização completa de ponta a ponta. | Uma pessoa que nunca viu o projeto segue o README e o `rest-client.http` e chega ao curso criado no Moodle. |
| CA-03 **[10]** | A subida é determinística: sem ordem manual de comandos, sem "espere e tente de novo". Dependências entre contêineres resolvidas por `depends_on` com `condition: service_healthy`. | `docker compose down -v && docker compose up -d --build` duas vezes seguidas, com o mesmo resultado. |
| CA-04 **[10]** | Nenhum segredo versionado. O `.env.example` documenta todas as variáveis e os campos obrigatórios chegam vazios (`MOODLE_TOKEN`, `SYNC_API_TOKEN`, senhas). | `git log -p` não contém tokens nem senhas; `git check-ignore .env` responde. |

## B. Decomposição

| # | Critério | Como verificar |
| --- | --- | --- |
| CA-05 **[O]** | A solução tem **pelo menos três** serviços no ar, cada um com uma responsabilidade clara e coesa: `api-gateway` (borda e autenticação), `ieducar-service` (extração), `sync-service` (domínio e mapeamento) e `moodle-service` (adaptador de destino). | `docker compose ps` lista os quatro; a responsabilidade de cada um cabe em uma frase sem "e também". |
| CA-06 **[O]** | Cada serviço é **dono dos próprios dados**. Nenhum serviço abre conexão com o banco de outro. | `sync_service` e `moodle_service` têm banco, usuário e senha próprios no Compose; nenhuma string de conexão aponta para o banco de um vizinho. |
| CA-07 **[O]** | O critério de decomposição está justificado em ADR, com as alternativas consideradas e os trade-offs aceitos. | ADR-001 em [`adr.md`](./adr.md): três opções analisadas e consequências negativas assumidas. |
| CA-08 **[O]** | O mapeamento de identificadores i-Educar ↔ Moodle tem dono único e explícito: o `sync-service`. | O mapeamento vive em `services/sync-service/app/domain/mapping.py` e no banco do `sync-service`; nenhum outro serviço escreve nessa tabela. |
| CA-09 **[10]** | Nenhum serviço precisa de outro no ar para aceitar trabalho: a indisponibilidade de um vizinho adia a sincronização, não derruba quem chamou. | Derrubar `moodle-service` e confirmar que o `api-gateway` continua respondendo e que a requisição é aceita e retida. |

## C. Comunicação

| # | Critério | Como verificar |
| --- | --- | --- |
| CA-10 **[O]** | Pelo menos um fluxo da sincronização passa pelo **RabbitMQ**. | Observar as mensagens na UI do RabbitMQ (porta `RABBITMQ_MGMT_PORT`) durante uma sincronização. |
| CA-11 **[O]** | Os eventos têm **nome, formato e versão documentados** em `docs/architecture.md`: nome, publicador, consumidores, payload de exemplo e versão. | Comparar o payload real capturado no broker com o exemplo do catálogo: devem coincidir campo a campo. |
| CA-12 **[O]** | Onde há chamada síncrona (o salto `api-gateway -> sync-service` e os adaptadores), a escolha está justificada: por que ali não cabia mensagem. | O ADR de comunicação nomeia cada chamada síncrona e o motivo. |
| CA-13 **[10]** | Todo evento carrega metadados de rastreio e deduplicação: `eventId`, `correlationId`, `occurredAt` e versão do schema. | Inspecionar qualquer mensagem no broker e comparar com `app/infrastructure/messaging/schemas.py`. |
| CA-14 **[10]** | Filas, exchanges e chaves de roteamento são criadas pela própria aplicação na subida, não à mão na UI do broker. | Após `docker compose down -v && docker compose up -d`, a topologia existe sem intervenção (`messaging/topology.py`, `messaging/bootstrap.py`). |

## D. Resiliência

| # | Critério | Como verificar |
| --- | --- | --- |
| CA-15 **[O]** | Os consumidores são **idempotentes**: processar a mesma mensagem duas vezes não cria curso, usuário ou matrícula duplicados. | Republicar a mesma mensagem (mesmo `eventId`) e confirmar no Moodle que nada foi duplicado e que o log registra o descarte. |
| CA-16 **[O]** | As chamadas externas usam **timeout** e **retry com backoff**, configuráveis e documentados. | `MOODLE_CONNECT_TIMEOUT`, `MOODLE_READ_TIMEOUT`, `LOCAL_MAX_RETRIES` e `LOCAL_RETRY_BASE_DELAY` no `.env.example`; o log mostra as tentativas espaçadas com o Moodle fora do ar. |
| CA-17 **[O]** | Mensagens que falham repetidamente vão para uma **dead-letter queue** sem bloquear o restante do fluxo. | Forçar uma falha permanente e confirmar que a mensagem aparece na DLQ após `RABBITMQ_MAX_RETRIES` e que as seguintes continuam sendo processadas. |
| CA-18 **[10]** | **Circuit breaker** nas chamadas externas: depois de `CB_FAILURE_THRESHOLD` falhas o circuito abre e só volta a tentar após `CB_RECOVERY_SECONDS`. | O log mostra a transição de estado (fechado, aberto, meio-aberto) com o Moodle fora do ar. |
| CA-19 **[10]** | Nenhuma mensagem é perdida nem confirmada antes da hora: o `ack` acontece depois do efeito aplicado, e a mensagem volta à fila se o processo morrer no meio. | Cenário 4 executado com a sincronização terminando correta. |
| CA-20 **[10]** | Há um caminho de reprocessamento da DLQ documentado: como reenviar o que falhou depois de corrigir a causa. | Seguir o procedimento de `docs/failure-scenarios.md` e ver a mensagem concluir. |

## E. Cenários de falha

Os quatro cenários do enunciado precisam ter resposta na arquitetura **e** ser demonstráveis.

| # | Critério | Como verificar |
| --- | --- | --- |
| CA-21 **[O]** | **Cenário 1, i-Educar fora do ar:** a sincronização não é perdida; ao voltar, o fluxo retoma de onde parou, sem duplicar. | Com `IEDUCAR_SOURCE=http`, parar o `ieducar-service`, disparar a sincronização, religar e conferir o resultado final. |
| CA-22 **[O]** | **Cenário 2, Moodle fora do ar:** as mensagens ficam retidas com retry e DLQ; nenhum dado é descartado silenciosamente. | `docker compose stop moodle`, disparar, religar e conferir que tudo foi aplicado uma vez só. |
| CA-23 **[O]** | **Cenário 3, o Moodle cria o curso mas a resposta HTTP não chega:** o retry não cria um segundo curso; o serviço reconcilia pelo `idnumber` antes de criar. | `FAULT_INJECT_DROP_AFTER_COURSE_CREATE=true` com `FAULT_INJECT_TARGET_IDNUMBER` apontando para um curso, repetir o fluxo e conferir que existe **um** curso. |
| CA-24 **[O]** | **Cenário 4, serviço reinicia no meio da sincronização:** ao subir, ele retoma o trabalho pendente e o estado final é o mesmo de uma execução sem falha. | `docker compose restart sync-service` durante o fluxo e comparar o estado final com a execução limpa. |
| CA-25 **[O]** | Cada cenário está documentado em `docs/failure-scenarios.md` com: o que acontece, qual mecanismo da arquitetura responde, o comando exato para reproduzir e o resultado esperado. | Executar os quatro comandos do documento e obter os resultados que ele descreve. |
| CA-26 **[10]** | A reprodução é um comando só por cenário (script ou `make`), executável ao vivo na apresentação sem improviso. | Rodar os quatro scripts em sequência. |

## F. Observabilidade

| # | Critério | Como verificar |
| --- | --- | --- |
| CA-27 **[O]** | Logs **estruturados** com um **correlation ID** que acompanha uma sincronização por **todos** os serviços, incluindo o salto pelo broker. | Disparar com `X-Correlation-Id: demo-001`, filtrar os logs de todos os serviços por esse id e ver a história completa. |
| CA-28 **[O]** | **Todos** os serviços expõem health check, e o Compose usa esses endpoints. | `curl` em cada health check; `docker compose ps` mostra os serviços como `healthy`. |
| CA-29 **[O]** | Métricas básicas expostas: mensagens processadas, falhas e tempo de sincronização. | Acessar o endpoint de métricas antes e depois do roteiro e ver os contadores se movendo (`app/core/metrics.py`). |
| CA-30 **[10]** | **Tracing distribuído**: uma sincronização aparece como um trace único atravessando os serviços e o broker. | Abrir o trace no coletor e ver os spans encadeados. |
| CA-31 **[10]** | Os logs distinguem erro esperado (retry em andamento) de erro inesperado, e nenhum log vaza token ou senha. | Ler o log de um cenário de falha completo. |

## G. Documentação e decisões

| # | Critério | Como verificar |
| --- | --- | --- |
| CA-32 **[O]** | `docs/architecture.md` traz a visão da arquitetura, o catálogo de eventos e uma **comparação com o T1**: o que mudou, o que melhorou e o que ficou mais caro. | Ler o documento: a comparação discute custos, não só vantagens. |
| CA-33 **[O]** | Existem ADRs cobrindo **decomposição, comunicação, resiliência e stack**, cada um com contexto, decisão, alternativas e consequências. | Os quatro temas aparecem em `docs/`; nenhum ADR termina sem consequências negativas assumidas. |
| CA-34 **[O]** | A escolha de stack está justificada em ADR, inclusive por ser a mesma do T1 (Python/FastAPI) e pela escolha do RabbitMQ. | Ler o ADR de stack. |
| CA-35 **[10]** | Um diagrama mostra os serviços, os bancos, o broker e os sistemas externos, com os eventos nomeados nas setas. | O diagrama corresponde ao que o `docker-compose.yml` sobe. O nível 1 (Context) está no Miro. |
| CA-36 **[10]** | Os ADRs registram as decisões **rejeitadas** que o grupo chegou a considerar: é aí que o trade-off fica visível. | ADR-001 já faz isso com as opções 1 e 2; os demais ADRs seguem o mesmo padrão. |

## H. Apresentação individual

Metade da nota do T2 é a apresentação individual, e um quarto é a avaliação dos pares.

| # | Critério | Como verificar |
| --- | --- | --- |
| CA-37 **[O]** | Cada integrante apresenta a arquitetura **do grupo** e responde perguntas sobre o código e as decisões, inclusive sobre partes que não implementou. | Ensaio interno em que cada um apresenta o trabalho inteiro e os colegas perguntam. |
| CA-38 **[O]** | A apresentação inclui demonstração **ao vivo** com pelo menos um cenário de falha. | A demo roda do zero, com ambiente preparado e tempo medido. |
| CA-39 **[10]** | Cada integrante sabe justificar, de cabeça, os ADRs e o que o grupo aceitou perder em cada decisão. | Rodada de perguntas cruzadas no ensaio. |
| CA-40 **[10]** | Existe plano B para a demo: ambiente já aquecido e gravação de reserva. | A gravação existe e o ambiente sobe antes da apresentação. |

## Mapa dos critérios para a rubrica

| Critério da rubrica | Avaliador | Nota | Peso | Critérios |
| --- | --- | --- | --- | --- |
| Arquitetura e implementação | Professor | Grupo | 25% | CA-01 a CA-36 |
| Avaliação dos pares | Turma | Individual | 25% | CA-37 a CA-40 |
| Apresentação | Professor | Individual | 50% | CA-37 a CA-40, sobre CA-21 a CA-31 |

## Estado em 2026-11-24

O que falta para fechar os critérios obrigatórios, na ordem em que bloqueia mais gente:

- **CA-21 (cenário 1) está parcialmente atendido.** O `ieducar-service` existe: tem timeout, retry
  local, circuit breaker, varredura automática que roda de novo no intervalo e auditoria em
  `scan_runs` — desse lado nada se perde nem duplica. Mas o `POST /sync/*` disparado na hora ainda
  **perde a tentativa** quando a origem está fora, porque o `sync-service` lê a origem antes de criar
  a run e sem retry. As duas metades estão documentadas em
  [`failure-scenarios.md`](./failure-scenarios.md) (cenários 1a e 1b), com as duas correções possíveis.
- **Faltam os ADRs de comunicação, resiliência e stack.** Bloqueia CA-12, CA-33 e CA-34. Decomposição
  (ADR-001) e o adaptador Moodle (ADR-002) já estão escritos; falta também um ADR para o
  `ieducar-service` (detecção de mudança por impressão digital, aviso magro, i-Educar como submodule).
- **ADR-001 descreve a comunicação interna só por HTTP** (`sync-service` consumindo `ieducar-service` e
  `moodle-service`), mas o caminho `sync-service` → `moodle-service` hoje é todo por mensagem. O ADR de
  comunicação precisa registrar o que de fato passa pelo broker e por quê, senão a documentação
  contradiz o código na hora da pergunta.
- **Os avisos `ieducar.*.changed.v1` não têm consumidor.** A fila `sync.events` assina apenas
  `moodle.#`. Não bloqueia nenhum CA obrigatório, mas é a pergunta óbvia da banca sobre o evento que é
  publicado e ninguém lê.
- **Falta o comando único por cenário para a demo** (CA-26, nota 10).
- **Tracing distribuído não implementado** (CA-30, desejável).

Já cobertos: CA-05 e CA-06 com os quatro serviços no ar, cada um com banco, usuário e rede próprios
(`ieducar-service` em `:8004` com `ieducar-service-db`, e o i-Educar real como submodule em
`services/i-educar`, instalado automaticamente na primeira subida);
[`architecture.md`](./architecture.md) com o catálogo de eventos e a comparação com o T1 (CA-11, CA-32,
CA-35); [`failure-scenarios.md`](./failure-scenarios.md) com os quatro cenários (CA-25); e testes
automatizados para os cenários 1a, 2, 3 e 4 — 61 testes do `ieducar-service` passando no contêiner.
