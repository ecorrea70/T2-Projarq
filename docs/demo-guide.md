# Guia Rápido da Demonstração (T2)

## URLs e Logins

| Serviço | URL | Login / Acesso |
| :--- | :--- | :--- |
| **i-Educar** | http://localhost:8081 | `admin` / `123456789` |
| **Moodle** | http://localhost:8080 | `admin` / `MOODLE_ADMIN_PASSWORD` (`.env`) |
| **API Gateway** | http://localhost:8000/docs | `Authorization: Bearer <SYNC_API_TOKEN>` |
| **RabbitMQ UI** | http://localhost:15672 | `guest` / `guest` |
| **moodle-service** | http://localhost:8003/ready | `/metrics` · `/internal/runs/{correlation_id}` |
| **ieducar-service** | http://localhost:8004/ready | `/metrics` · `/internal/scans` |

---

## Ordem de Execução

### 1. Subir o ambiente (1ª vez ou após reboot)
```bash
docker compose up -d
docker compose ps
```

### 2. Popular o i-Educar e zerar para iniciar a demonstração (preserva tokens)
```bash
# Limpa cursos/usuários sincronizados no Moodle
bash scripts/clean_moodle_synced.sh

# Limpa bancos dos microsserviços + filas RabbitMQ e executa seeds/ieducar/seed_demo.sh
bash scripts/reset_demo.sh

# (Opcional) Rodar apenas a seed do i-Educar isoladamente
bash seeds/ieducar/seed_demo.sh
```

### 3. Disparar a sincronização (via [rest-client.http](../rest-client.http) ou terminal)
```bash
# Professores + cursos + vínculos docentes (gateway gera correlation_id automático)
curl -X POST http://localhost:8000/sync/teachers \
  -H "Authorization: Bearer $SYNC_API_TOKEN"

# Acompanhar (substitua <CORRELATION_ID> pelo retornado acima)
curl http://localhost:8000/sync/<CORRELATION_ID> \
  -H "Authorization: Bearer $SYNC_API_TOKEN"

# Alunos + matrículas em lotes
curl -X POST http://localhost:8000/sync/students \
  -H "Authorization: Bearer $SYNC_API_TOKEN"

curl http://localhost:8000/sync/<CORRELATION_ID> \
  -H "Authorization: Bearer $SYNC_API_TOKEN"
```

### 4. Cenários de falha (demonstração ao vivo)
```bash
bash scripts/demo_failure_scenarios.sh 1   # i-Educar fora do ar
bash scripts/demo_failure_scenarios.sh 2   # Moodle fora do ar (CB + retry + DLQ + replay)
bash scripts/demo_failure_scenarios.sh 3   # Moodle cria curso, resposta HTTP cai (reconciliação)
bash scripts/demo_failure_scenarios.sh 4   # Reinício abrupto do moodle-service (manual ACK)
```

### 5. Logs correlacionados e métricas
```bash
docker compose logs --tail=50 api-gateway sync-service moodle-service ieducar-service | grep demo-teachers-001
curl http://localhost:8000/metrics
curl http://localhost:8003/metrics
```
