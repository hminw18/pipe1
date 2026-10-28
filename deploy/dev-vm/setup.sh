#!/bin/sh
# Generates .env with random secrets (once). Keep it private; it is git-ignored.
set -eu
cd "$(dirname "$0")"
[ -f .env ] && { echo ".env already exists"; exit 0; }
umask 077
rand() { python3 -c 'import secrets;print(secrets.token_urlsafe(32))'; }
cat > .env <<ENV
POSTGRES_PASSWORD=$(rand)
PIPE1_MIGRATOR_DB_PASSWORD=$(rand)
PIPE1_APP_DB_PASSWORD=$(rand)
PIPE1_CSRF_KEY=$(rand)
PIPE1_TOTP_LOCAL_KEY=$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')
ENV
echo "wrote .env"
