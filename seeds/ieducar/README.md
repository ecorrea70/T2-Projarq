# Seeds de Demonstração do i-Educar (T2)

Utilitários manuais para popular o banco do **i-Educar** em ambiente local de demonstração, facilitando o teste completo do fluxo `i-Educar -> ieducar-service -> sync-service -> RabbitMQ -> moodle-service -> Moodle`.

> **Importante**: estes scripts existem apenas para criar dados fictícios no banco do i-Educar. A sincronização dos microsserviços continua 100% pelas APIs HTTP e RabbitMQ.

## O que é populado (escopo padrão: instituição `1`, escolas `2,3`, ano `2026`)

1. **`DemoSeeder` (nativo do i-Educar)**:
   - **2 escolas**: Ensino Fundamental (`cod_escola=2`, 18 turmas, 180 alunos) e Educação Infantil (`cod_escola=3`, 3 turmas, 30 alunos).
   - **21 turmas ativas**, **177 pares turma/disciplina** e **210 alunos** com matrículas e enturmações ativas.
2. **`professores.sql` (seed de docentes)**:
   - **21 professores ativos** (`Professor Demo Turma {código}`, `seed-professor-turma-{código}@example.org`), um por turma, vinculados a todas as disciplinas da respectiva série.

## Como executar

Com o ambiente do T2 no ar (`docker compose up -d` após o merge do `ieducar-service` e `git submodule update --init services/i-educar`):

### Opção recomendada — Tudo em um comando (idempotente)

```bash
bash seeds/ieducar/seed_demo.sh
```

- Se o i-Educar ainda não tiver escolas ativas, executa o `DemoSeeder` automaticamente.
- Em seguida, executa `seeds/ieducar/run.sh --apply` para criar os 21 professores e seus vínculos.
- Reexecutar o comando é seguro: pula o `DemoSeeder` se já houver escolas e não duplica professores.

### Apenas professores (simulação ou aplicação)

```bash
# Simula os INSERTs em transação com ROLLBACK
bash seeds/ieducar/run.sh --dry-run

# Aplica em transação com COMMIT
bash seeds/ieducar/run.sh --apply
```

## Variáveis opcionais

```bash
SEED_INSTITUTION_ID=1 SEED_SCHOOL_IDS=2,3 SEED_YEAR=2026 \
SEED_DB_USER=ieducar SEED_DB_NAME=ieducar \
  bash seeds/ieducar/seed_demo.sh
```

## Depois de popular o i-Educar

1. Confira no `.env` que `IEDUCAR_SOURCE=http`, `IEDUCAR_TOKEN`, `IEDUCAR_ACCESS_KEY`, `MOODLE_TOKEN` e `SYNC_API_TOKEN` estão preenchidos.
2. Dispare a sincronização pelo `api-gateway` (`:8000`) usando [`rest-client.http`](../../rest-client.http):
   - `POST http://localhost:8000/sync/teachers` (cria os 177 cursos, 21 professores e 177 vínculos docentes no Moodle)
   - `POST http://localhost:8000/sync/students` (cria os 210 alunos e 1.770 matrículas em lotes no Moodle)
   - `GET http://localhost:8000/sync/{correlation_id}` (acompanha progresso e resultado consolidado)
