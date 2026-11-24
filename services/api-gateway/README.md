# `api-gateway` — Borda pública (`:8000`)

Único ponto de entrada **externo** para disparar e acompanhar sincronizações. Encaminha somente as rotas de sync para o `sync-service` interno (`:8002`).

## Rotas expostas

| Método | Rota | Auth |
| :--- | :--- | :--- |
| `GET` | `/health` | Não |
| `GET` | `/metrics` | Não |
| `POST` | `/sync/teachers` | `Authorization: Bearer <SYNC_API_TOKEN>` |
| `POST` | `/sync/students` | Bearer |
| `GET` | `/sync/{correlation_id}` | Bearer |

Headers propagados ao `sync-service`: `X-Correlation-Id` (gerado pelo gateway se ausente).

## Variáveis

| Variável | Descrição |
| :--- | :--- |
| `SYNC_API_TOKEN` | Token obrigatório na borda (senão `503`) |
| `SYNC_SERVICE_URL` | Base interna (`http://sync-service:8002`) |

## Testes

```bash
cd services/api-gateway
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest -v
```