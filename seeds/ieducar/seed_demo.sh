#!/usr/bin/env bash
set -euo pipefail

# Popula o i-Educar para demonstração do T2 em um único comando (idempotente):
#   1) Executa o DemoSeeder do i-Educar (2 escolas, 21 turmas, 177 disciplinas/cursos e 210 alunos)
#      somente se ainda não existirem escolas ativas na instituição.
#   2) Executa seeds/ieducar/run.sh --apply para criar 21 professores e seus vínculos docentes.
#
# Uso:
#   bash seeds/ieducar/seed_demo.sh
#   bash seeds/ieducar/seed_demo.sh --dry-run

mode="${1:---apply}"
case "$mode" in
  --apply|--dry-run) ;;
  *) echo 'Uso: bash seeds/ieducar/seed_demo.sh [--apply|--dry-run]' >&2; exit 2 ;;
esac

institution="${SEED_INSTITUTION_ID:-1}"
schools="${SEED_SCHOOL_IDS:-2,3}"
year="${SEED_YEAR:-2026}"
db_user="${SEED_DB_USER:-ieducar}"
db_name="${SEED_DB_NAME:-ieducar}"

seed_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$seed_dir/../.." && pwd)"
compose=(docker compose --project-directory "$repo_root" -f "$repo_root/docker-compose.yml")

echo "==> [1/3] Verificando escolas existentes no i-Educar (instituição=$institution)..."
school_count=$("${compose[@]}" exec -T ieducar-db \
  psql -X -U "$db_user" -d "$db_name" -tAc \
  "SELECT count(*) FROM pmieducar.escola WHERE ref_cod_instituicao = ${institution} AND ativo = 1")

if [[ "${school_count:-0}" -eq 0 ]]; then
  if [[ "$mode" == "--dry-run" ]]; then
    echo "    [dry-run] Nenhuma escola encontrada; em modo --apply rodaria Database\\Seeders\\DemoSeeder."
    echo "    Encerrando dry-run antes de professores.sql porque as turmas ainda não existem."
    exit 0
  fi
  echo "    Nenhuma escola ativa encontrada. Executando DemoSeeder do i-Educar..."
  "${compose[@]}" run --rm ieducar-php \
    php artisan db:seed --class='Database\Seeders\DemoSeeder' --force
else
  echo "    Já existem ${school_count} escola(s) ativa(s) no i-Educar; pulando DemoSeeder para evitar duplicatas."
fi

echo "==> [2/3] Aplicando seed de professores ($mode)..."
SEED_INSTITUTION_ID="$institution" \
SEED_SCHOOL_IDS="$schools" \
SEED_YEAR="$year" \
SEED_DB_USER="$db_user" \
SEED_DB_NAME="$db_name" \
  bash "$seed_dir/run.sh" "$mode"

echo "==> [3/3] Resumo atual dos dados no i-Educar (escopo escolas=$schools, ano=$year):"
"${compose[@]}" exec -T ieducar-db \
  psql -X -U "$db_user" -d "$db_name" -c "
    SELECT
      (SELECT count(*) FROM pmieducar.escola WHERE ref_cod_instituicao = ${institution} AND ativo = 1) AS escolas_ativas,
      (SELECT count(*) FROM pmieducar.turma WHERE ref_cod_instituicao = ${institution} AND ref_ref_cod_escola = ANY(string_to_array('${schools}', ',')::int[]) AND ano = ${year} AND ativo = 1) AS turmas_ativas,
      (SELECT count(DISTINCT ref_cod_aluno) FROM pmieducar.matricula WHERE ref_ref_cod_escola = ANY(string_to_array('${schools}', ',')::int[]) AND ano = ${year} AND ativo = 1) AS alunos_ativos,
      (SELECT count(DISTINCT servidor_id) FROM modules.professor_turma WHERE instituicao_id = ${institution} AND ano = ${year}) AS professores_vinculados;
  "

echo "OK: seed_demo concluído."
