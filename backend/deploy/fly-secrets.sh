#!/usr/bin/env bash
# Push secrets to Fly, reading provider credentials from the local .env.
#
# JWT_SECRET and FERNET_KEY are generated fresh rather than copied: dev secrets
# tend to end up in shell history, screenshots and backups, and production
# should not inherit that exposure. The production database is empty, so there
# is nothing encrypted under the old key to lose.
#
# Usage:  ./deploy/fly-secrets.sh <app-name>
set -euo pipefail

APP="${1:-}"
if [ -z "$APP" ]; then
  echo "usage: $0 <fly-app-name>" >&2
  exit 1
fi

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "no .env found — run this from a machine that has the local dev config" >&2
  exit 1
fi

read_env() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }

require() {
  if [ -z "$2" ]; then
    echo "missing $1 in .env — set it before deploying" >&2
    exit 1
  fi
}

ANTHROPIC=$(read_env ANTHROPIC_API_KEY)
OURA_ID=$(read_env OURA_CLIENT_ID)
OURA_SECRET=$(read_env OURA_CLIENT_SECRET)
BUNDLE_ID=$(read_env APPLE_BUNDLE_ID)
TEAM_ID=$(read_env APPLE_TEAM_ID)

require ANTHROPIC_API_KEY "$ANTHROPIC"
require OURA_CLIENT_ID "$OURA_ID"
require OURA_CLIENT_SECRET "$OURA_SECRET"
require APPLE_BUNDLE_ID "$BUNDLE_ID"

JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Note: OURA_REDIRECT_URI is deliberately not set. Leaving it empty makes the
# callback derive from PUBLIC_BASE_URL, so the two can never drift apart.
fly secrets set --app "$APP" --stage \
  JWT_SECRET="$JWT_SECRET" \
  FERNET_KEY="$FERNET_KEY" \
  ANTHROPIC_API_KEY="$ANTHROPIC" \
  OURA_CLIENT_ID="$OURA_ID" \
  OURA_CLIENT_SECRET="$OURA_SECRET" \
  APPLE_BUNDLE_ID="$BUNDLE_ID" \
  APPLE_TEAM_ID="$TEAM_ID" \
  OURA_REDIRECT_URI="" \
  PUBLIC_BASE_URL="https://${APP}.fly.dev"

echo
echo "Staged. They apply on the next deploy:  fly deploy --app $APP"
echo
echo "FERNET_KEY encrypts every stored provider token. If it is lost, every"
echo "connected account must be reconnected. Save it somewhere durable:"
echo
echo "  FERNET_KEY=$FERNET_KEY"
echo
