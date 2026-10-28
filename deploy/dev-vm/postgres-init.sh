#!/bin/sh
# Same roles as Cloud SQL: pipe1_migrator owns the schema, pipe1_app is the runtime role.
set -eu
psql -v ON_ERROR_STOP=1 --username postgres <<SQL
CREATE ROLE pipe1_migrator LOGIN PASSWORD '${PIPE1_MIGRATOR_DB_PASSWORD}';
CREATE ROLE pipe1_app LOGIN PASSWORD '${PIPE1_APP_DB_PASSWORD}';
CREATE DATABASE pipe1 OWNER pipe1_migrator;
SQL
