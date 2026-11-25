#!/usr/bin/env bash
set -euo pipefail

# Script executável para demonstrar ao vivo os 4 cenários de falha exigidos no T2.
#
# Uso (a partir da raiz do repositório):
#   bash scripts/demo_failure_scenarios.sh 1   # Cenário 1: i-Educar fora do ar
#   bash scripts/demo_failure_scenarios.sh 2   # Cenário 2: Moodle fora do ar (CB + retry + DLQ + replay)
#   bash scripts/demo_failure_scenarios.sh 3   # Cenário 3: Moodle cria curso mas resposta HTTP não chega
#   bash scripts/demo_failure_scenarios.sh 4   # Cenário 4: Serviço reinicia no meio da sincronização

scenario="${1:-}"
if [[ ! "$scenario" =~ ^[1-4]$ ]]; then
  cat >&2 <<'EOF'
Uso: bash scripts/demo_failure_scenarios.sh <1|2|3|4>

  1 - Cenário 1: O i-Educar está fora do ar
  2 - Cenário 2: O Moodle está fora do ar (Circuit Breaker, Retry, DLQ e recuperação)
  3 - Cenário 3: O Moodle cria o curso, mas a resposta HTTP não chega (reconciliação sem duplicar)
  4 - Cenário 4: Um serviço (moodle-service) reinicia no meio da sincronização (manual ACK + idempotência)
EOF
  exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
compose=(docker compose --project-directory "$repo_root" -f "$repo_root/docker-compose.yml")

if [[ -f "$repo_root/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$repo_root/.env"
  set +a
fi

token="${SYNC_API_TOKEN:-}"
if [[ -z "$token" ]]; then
  echo "ERRO: SYNC_API_TOKEN não encontrado. Preencha o .env antes de rodar a demo." >&2
  exit 1
fi

gateway_url="http://localhost:${API_GATEWAY_PORT:-8000}"
moodle_svc_url="http://localhost:${MOODLE_SERVICE_PORT:-8003}"
stamp="$(date +%s)"
corr_id="demo-cenario${scenario}-${stamp}"

has_service() {
  "${compose[@]}" config --services 2>/dev/null | grep -qx "$1"
}

trigger_teachers_sync() {
  local cid="$1"
  curl -sS -w "\nHTTP_STATUS:%{http_code}\n" \
    -X POST "${gateway_url}/sync/teachers" \
    -H "Authorization: Bearer ${token}" \
    -H "X-Correlation-Id: ${cid}"
}

poll_sync() {
  local cid="$1"
  local max_wait="${2:-60}"
  local elapsed=0
  while (( elapsed < max_wait )); do
    local body
    body=$(curl -sS "${gateway_url}/sync/${cid}" -H "Authorization: Bearer ${token}" || true)
    local st
    st=$(printf '%s' "$body" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
    echo "    [${elapsed}s] status=${st:-desconhecido}"
    if [[ "$st" == "completed" || "$st" == "completed_with_errors" || "$st" == "failed" ]]; then
      echo "    Resultado final:"
      printf '%s\n' "$body" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d.get(k) for k in ('correlation_id','kind','status','phase','counters')}, indent=2, ensure_ascii=False))"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "    Tempo limite de espera (${max_wait}s) atingido; consulte GET ${gateway_url}/sync/${cid}."
}

case "$scenario" in
  1)
    echo "============================================================"
    echo "CENÁRIO 1: O i-Educar está fora do ar (correlation_id=$corr_id)"
    echo "============================================================"
    if ! has_service "ieducar"; then
      echo "ERRO: serviço 'ieducar' não está presente no docker-compose.yml (puxe o merge do ieducar-service primeiro)." >&2
      exit 1
    fi
    cleanup_c1() {
      echo "==> Restaurando container 'ieducar'..."
      "${compose[@]}" start ieducar >/dev/null 2>&1 || true
    }
    trap cleanup_c1 EXIT

    echo "==> [1/3] Parando o container ieducar..."
    "${compose[@]}" stop ieducar
    echo "==> [2/3] Disparando POST /sync/teachers (esperado: 503 sanitizado, sem crash)..."
    trigger_teachers_sync "$corr_id"
    echo "==> [3/3] Logs filtrados por correlation_id=$corr_id:"
    "${compose[@]}" logs --tail=30 sync-service ieducar-service 2>/dev/null | grep "$corr_id" || true
    ;;

  2)
    echo "============================================================"
    echo "CENÁRIO 2: O Moodle está fora do ar (correlation_id=$corr_id)"
    echo "============================================================"
    cleanup_c2() {
      echo "==> Garantindo que o container 'moodle' volte ao ar..."
      "${compose[@]}" start moodle >/dev/null 2>&1 || true
    }
    trap cleanup_c2 EXIT

    echo "==> [1/5] Parando o container moodle..."
    "${compose[@]}" stop moodle
    echo "==> [2/5] Disparando POST /sync/teachers (deve retornar 202 Accepted mesmo com Moodle fora)..."
    trigger_teachers_sync "$corr_id"
    echo "==> [3/5] Aguardando 12s para observar Circuit Breaker, fila de retry e DLQ..."
    sleep 12
    echo "    Readiness do moodle-service:"
    curl -sS "${moodle_svc_url}/ready" && echo ""
    echo "    Filas RabbitMQ:"
    "${compose[@]}" exec -T rabbitmq rabbitmqctl list_queues name messages messages_unacknowledged 2>/dev/null || true

    echo "==> [4/5] Religando o Moodle e aguardando ficar disponível..."
    "${compose[@]}" start moodle
    for _ in $(seq 1 30); do
      if curl -fsS "http://localhost:${MOODLE_HTTP_PORT:-8080}/login/index.php" >/dev/null 2>&1; then
        break
      fi
      sleep 2
    done

    echo "==> [5/5] Reprocessando eventual DLQ (POST /internal/dlq/replay) e acompanhando conclusão..."
    curl -sS -X POST "${moodle_svc_url}/internal/dlq/replay?limit=200" && echo ""
    poll_sync "$corr_id" 60
    ;;

  3)
    echo "============================================================"
    echo "CENÁRIO 3: Moodle cria curso, mas resposta HTTP não chega ($corr_id)"
    echo "============================================================"
    cleanup_c3() {
      echo "==> Restaurando moodle-service com FAULT_INJECT_DROP_AFTER_COURSE_CREATE=false..."
      FAULT_INJECT_DROP_AFTER_COURSE_CREATE=false "${compose[@]}" up -d --no-deps moodle-service >/dev/null 2>&1 || true
    }
    trap cleanup_c3 EXIT

    echo "==> [1/4] Ativando injeção de falha controlada no moodle-service..."
    FAULT_INJECT_DROP_AFTER_COURSE_CREATE=true "${compose[@]}" up -d --no-deps moodle-service
    sleep 3
    echo "==> [2/4] Disparando POST /sync/teachers..."
    trigger_teachers_sync "$corr_id"
    echo "==> [3/4] Aguardando conclusão da reconciliação automática..."
    poll_sync "$corr_id" 60
    echo "==> [4/4] Auditoria do moodle-service (/internal/runs/$corr_id) — confira reconciled >= 1:"
    curl -sS "${moodle_svc_url}/internal/runs/${corr_id}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({'correlation_id':d.get('correlation_id'),'summary':d.get('summary')}, indent=2, ensure_ascii=False))"
    echo "    Logs de injeção/reconciliação:"
    "${compose[@]}" logs --tail=80 moodle-service | grep "$corr_id" | grep -E "Injeção|reconciled" | head -n 10 || true
    ;;

  4)
    echo "============================================================"
    echo "CENÁRIO 4: Reinício abrupto do moodle-service no meio da sync ($corr_id)"
    echo "============================================================"
    echo "==> [1/4] Disparando POST /sync/teachers..."
    trigger_teachers_sync "$corr_id"
    echo "==> [2/4] Aguardando 1s e matando moodle-service abruptamente (docker compose kill)..."
    sleep 1
    "${compose[@]}" kill moodle-service
    echo "    Filas RabbitMQ enquanto moodle-service está fora:"
    "${compose[@]}" exec -T rabbitmq rabbitmqctl list_queues name messages messages_unacknowledged 2>/dev/null || true
    echo "==> [3/4] Subindo moodle-service novamente (mensagens sem ACK são reentregues)..."
    "${compose[@]}" start moodle-service
    echo "==> [4/4] Acompanhando conclusão e reconciliação automática..."
    poll_sync "$corr_id" 60
    echo "    Auditoria do moodle-service (/internal/runs/$corr_id):"
    curl -sS "${moodle_svc_url}/internal/runs/${corr_id}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({'correlation_id':d.get('correlation_id'),'summary':d.get('summary')}, indent=2, ensure_ascii=False))"
    ;;
esac
