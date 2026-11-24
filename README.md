# T2 — Integração i-Educar × Moodle (microsserviços)

## Serviços disponíveis neste repositório

| Serviço | Porta | Documentação |
| :--- | :--- | :--- |
| **`api-gateway`** (público) | **8000** | [`services/api-gateway/README.md`](services/api-gateway/README.md) |
| `sync-service` (interno) | 8002 | [`services/sync-service/README.md`](services/sync-service/README.md) |
| `moodle-service` | 8003 | [`services/moodle-service/README.md`](services/moodle-service/README.md) |

```bash
cp .env.example .env
# Preencha SYNC_API_TOKEN, senhas Moodle e MOODLE_TOKEN
docker compose up -d --build
curl -X POST http://localhost:8000/sync/teachers \
  -H "Authorization: Bearer $SYNC_API_TOKEN" \
  -H "X-Correlation-Id: demo-001"
```

Roteiro HTTP na raiz: [`rest-client.http`](rest-client.http).

Origem i-Educar: o `sync-service` usa `IEDUCAR_SOURCE=mock` até o `ieducar-service` do colega ficar pronto (`IEDUCAR_SOURCE=http`).
