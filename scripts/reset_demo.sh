#!/usr/bin/env bash
set -euo pipefail

# Script seguro de preparação/limpeza para demonstração do T2.
#
# O que faz (PRESERVANDO moodle-db, ieducar-db e todos os tokens do .env):
#   1) Garante os containers no ar com FAULT_INJECT_DROP_AFTER_COURSE_CREATE=false.
#   2) Limpa apenas os bancos independentes dos microsserviços
#      (sync-service-db, moodle-service-db e ieducar-service-db, preservando alembic_version).
#   3) Purga as filas do RabbitMQ (moodle.commands, retry, dlq e sync.events).
#   4) Executa o seed de demonstração do i-Educar (idempotente).
#   5) Valida /health e /ready dos serviços.
#
# Uso (a partir da raiz do repositório):
#   bash scripts/reset_demo.sh

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '4,16p' "$0"
  exit 0
fi
if [[ $# -gt 0 ]]; then
  echo "Uso: bash scripts/reset_demo.sh" >&2
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

has_service() {
  "${compose[@]}" config --services 2>/dev/null | grep -qx "$1"
}

truncate_service_db() {
  local svc="$1"
  local db_user="$2"
  local db_name="$3"
  if ! has_service "$svc"; then
    return 0
  fi
  echo "    Limpando tabelas de $svc ($db_name) preservando alembic_version..."
  "${compose[@]}" exec -T "$svc" psql -X -U "$db_user" -d "$db_name" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE
    tbls text;
BEGIN
    SELECT string_agg(format('%I.%I', schemaname, tablename), ', ')
    INTO tbls
    FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename <> 'alembic_version';
    IF tbls IS NOT NULL THEN
        EXECUTE 'TRUNCATE TABLE ' || tbls || ' RESTART IDENTITY CASCADE';
    END IF;
END $$;
SQL
}

echo "==> [1/4] Garantindo containers no ar (preservando moodle-db, ieducar-db e tokens)..."
FAULT_INJECT_DROP_AFTER_COURSE_CREATE=false "${compose[@]}" up -d

echo "==> [2/4] Limpando bancos independentes dos microsserviços e filas RabbitMQ..."
truncate_service_db "sync-service-db" "${SYNC_SERVICE_DB_USER:-sync_service}" "${SYNC_SERVICE_DB_NAME:-sync_service}"
truncate_service_db "moodle-service-db" "${MOODLE_SERVICE_DB_USER:-moodle_service}" "${MOODLE_SERVICE_DB_NAME:-moodle_service}"
truncate_service_db "ieducar-service-db" "${IEDUCAR_SERVICE_DB_USER:-ieducar_service}" "${IEDUCAR_SERVICE_DB_NAME:-ieducar_service}"

for queue in moodle.commands moodle.commands.retry moodle.commands.dlq sync.events; do
  "${compose[@]}" exec -T rabbitmq rabbitmqctl purge_queue "$queue" >/dev/null 2>&1 || true
done
echo "    Filas RabbitMQ purgadas."

echo "==> [3/4] Populando dados de demonstração no i-Educar..."
if has_service "ieducar-db"; then
  bash "$repo_root/seeds/ieducar/seed_demo.sh" --apply
else
  echo "    Serviço 'ieducar-db' ainda não está no docker-compose.yml (modo mock ativo). Pulando seed do i-Educar."
fi

echo "==> [4/4] Verificando saúde dos serviços..."
gateway_port="${API_GATEWAY_PORT:-8000}"
moodle_svc_port="${MOODLE_SERVICE_PORT:-8003}"
curl -fsS "http://localhost:${gateway_port}/health" && echo ""
curl -fsS "http://localhost:${moodle_svc_port}/ready" && echo ""
if has_service "ieducar-service"; then
  ieducar_svc_port="${IEDUCAR_SERVICE_PORT:-8004}"
  curl -fsS "http://localhost:${ieducar_svc_port}/ready" && echo ""
fi

echo "OK: reset_demo concluído."
