#!/usr/bin/env bash
set -euo pipefail

# Remove APENAS os cursos (turmas/disciplinas) e usuários sincronizados pela integração
# (identificados por idnumber LIKE 'ieducar:%') usando a API PHP oficial do Moodle.
#
# Preserva 100%: usuário admin, Web Services, funções habilitadas e MOODLE_TOKEN.
#
# Uso:
#   bash scripts/clean_moodle_synced.sh            # Apaga cursos e usuários sincronizados (ieducar:*)
#   bash scripts/clean_moodle_synced.sh --courses  # Apaga apenas os cursos (ieducar:curso:*)

mode="${1:---all}"
case "$mode" in
  --all|--courses) ;;
  *)
    echo "Uso: bash scripts/clean_moodle_synced.sh [--all|--courses]" >&2
    exit 2
    ;;
esac

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
compose=(docker compose --project-directory "$repo_root" -f "$repo_root/docker-compose.yml")

delete_users="1"
if [[ "$mode" == "--courses" ]]; then
  delete_users="0"
fi

echo "==> Removendo dados sincronizados no Moodle (modo=$mode; preservando admin e MOODLE_TOKEN)..."
"${compose[@]}" exec -T -e DELETE_USERS="$delete_users" moodle php <<'PHP'
<?php
define('CLI_SCRIPT', true);
require('/bitnami/moodle/config.php');
require_once($CFG->libdir . '/moodlelib.php');
require_once($CFG->dirroot . '/user/lib.php');

$course_like = $DB->sql_like('idnumber', ':prefix');
$courses = $DB->get_records_select('course', "id > 1 AND {$course_like}", ['prefix' => 'ieducar:curso:%']);
$courses_deleted = 0;
foreach ($courses as $course) {
    delete_course($course->id, false);
    $courses_deleted++;
}
if ($courses_deleted > 0) {
    fix_course_sortorder();
}

$users_deleted = 0;
if (getenv('DELETE_USERS') === '1') {
    $user_like = $DB->sql_like('idnumber', ':prefix');
    $users = $DB->get_records_select('user', "deleted = 0 AND {$user_like}", ['prefix' => 'ieducar:%']);
    foreach ($users as $user) {
        user_delete_user($user);
        $users_deleted++;
    }
}

echo "Cursos removidos: {$courses_deleted}\n";
echo "Usuários removidos: {$users_deleted}\n";
PHP

echo "OK: clean_moodle_synced concluído."
