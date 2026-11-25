# T2 — Integração i-Educar × Moodle (microsserviços)

## Serviços disponíveis neste repositório

| Serviço | Porta | Documentação |
| :--- | :--- | :--- |
| **`api-gateway`** (público) | **8000** | [`services/api-gateway/README.md`](services/api-gateway/README.md) |
| `sync-service` (interno) | 8002 | [`services/sync-service/README.md`](services/sync-service/README.md) |
| `moodle-service` | 8003 | [`services/moodle-service/README.md`](services/moodle-service/README.md) |
| `ieducar-service` | 8004 | [`services/ieducar-service/README.md`](services/ieducar-service/README.md) |

O i-Educar real entra como submodule em `services/i-educar`, então clone com:

```bash
git clone --recurse-submodules <url-do-repo>
```

Em um clone que já existe: `git submodule update --init services/i-educar`.

```bash
cp .env.example .env
# Preencha SYNC_API_TOKEN, senhas Moodle e MOODLE_TOKEN
docker compose up -d --build
curl -X POST http://localhost:8000/sync/teachers \
  -H "Authorization: Bearer $SYNC_API_TOKEN" \
  -H "X-Correlation-Id: demo-001"
```

Roteiro HTTP na raiz: [`rest-client.http`](rest-client.http).

Origem i-Educar: o `sync-service` usa `IEDUCAR_SOURCE=mock` por padrão. Para ler o i-Educar de verdade,
preencha `IEDUCAR_TOKEN` e `IEDUCAR_ACCESS_KEY` no `.env` e troque para `IEDUCAR_SOURCE=http`. A
primeira subida instala a instância do i-Educar sozinha e demora.

## Documentação

| Documento | Conteúdo |
| :--- | :--- |
| [`docs/architecture.md`](docs/architecture.md) | Visão da arquitetura, catálogo de eventos e comparação com o T1 |
| [`docs/failure-scenarios.md`](docs/failure-scenarios.md) | Os quatro cenários de falha e como reproduzir cada um |
| [`docs/acceptance-criteria.md`](docs/acceptance-criteria.md) | Critérios de aceitação da entrega |
| [`docs/adr.md`](docs/adr.md) · [`docs/adr-moodle-service.md`](docs/adr-moodle-service.md) | Decisões arquiteturais (ADR-001 e ADR-002) |
