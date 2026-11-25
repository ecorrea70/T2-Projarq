#!/bin/sh
# Instalação automática do i-Educar na primeira subida.
#
# O T1 exigia rodar "composer new-install" e "php artisan db:seed" à mão. Aqui
# isso acontece sozinho, uma única vez, para que o T2 inteiro suba com um
# "docker compose up" como o enunciado pede.
#
# O script é idempotente: se o schema pmieducar já tem tabelas, ele não faz
# nada. Nunca reexecuta o new-install num ambiente já instalado, porque isso
# geraria uma nova APP_KEY e invalidaria os dados existentes.
set -eu

APP_DIR="${APP_DIR:-/var/www/ieducar}"
DB_HOST="${DB_HOST:-ieducar-db}"
DB_PORT="${DB_PORT:-5432}"
DB_DATABASE="${DB_DATABASE:-ieducar}"
DB_USERNAME="${DB_USERNAME:-ieducar}"

cd "$APP_DIR"

log() {
  printf '{"service":"ieducar-init","message":"%s"}\n' "$1"
}

if [ ! -f .env ]; then
  if [ ! -f .env.example ]; then
    log "Submodule do i-Educar parece vazio. Rode: git submodule update --init --recursive"
    exit 1
  fi
  log "Criando .env do i-Educar a partir do .env.example"
  cp .env.example .env
fi

TABLES=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_DATABASE" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'pmieducar'" 2>/dev/null || echo "erro")

if [ "$TABLES" = "erro" ]; then
  log "Banco do i-Educar não respondeu à checagem de instalação"
  exit 1
fi

if [ "$TABLES" -gt 0 ]; then
  log "i-Educar já instalado ($TABLES tabelas em pmieducar); nada a fazer"
  exit 0
fi

log "Banco vazio: instalando o i-Educar (composer new-install)"
composer new-install

log "Semeando dados de referência (php artisan db:seed)"
php artisan db:seed --force

if [ -f /init/seeds/demo.sql ]; then
  log "Aplicando dados de demonstração (seeds/demo.sql)"
  psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USERNAME" -d "$DB_DATABASE" -v ON_ERROR_STOP=1 -f /init/seeds/demo.sql
fi

log "Instalação do i-Educar concluída"
