# T2 — Integração i-Educar × Moodle (microsserviços)

## Serviços disponíveis neste repositório

| Serviço | Porta | Documentação |
| :--- | :--- | :--- |
| **`api-gateway`** (público) | **8000** | [`services/api-gateway/README.md`](services/api-gateway/README.md) |
| `sync-service` (interno) | 8002 | [`services/sync-service/README.md`](services/sync-service/README.md) |
| `moodle-service` | 8003 | [`services/moodle-service/README.md`](services/moodle-service/README.md) |
| `ieducar-service` | 8004 | [`services/ieducar-service/README.md`](services/ieducar-service/README.md) |

## Atenção: o repositório agora tem submodule

O i-Educar real entra como **submodule** em `services/i-educar`. Em um clone novo:

```bash
git clone --recurse-submodules git@github.com:ecorrea70/T2-Projarq.git
```

**Quem já tem o clone** precisa rodar isto uma vez depois de puxar:

```bash
git submodule update --init services/i-educar
```

Sem esse comando, `services/i-educar` fica vazio e o `docker compose up` não sobe a instância do
i-Educar. O `services/ieducar-init/init.sh` detecta o diretório vazio e aborta dizendo qual comando
rodar, em vez de falhar com um erro obscuro de build.

## Subindo o ambiente

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
| [`docs/demo-guide.md`](docs/demo-guide.md) | Guia rápido de URLs, logins e ordem dos scripts da demonstração |
| [`docs/adr.md`](docs/adr.md) | Decisões arquiteturais (ADR-001 a ADR-005) |
