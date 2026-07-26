#!/usr/bin/env bash
# Salje Telegram poruku kad Claude Code zavrsi zadatak (Stop hook).
# Zahteva env varijable TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID.
set -euo pipefail

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  exit 0
fi

REPO_NAME="$(basename "$(pwd)")"
MESSAGE="✅ Claude Code je zavrsio zadatak u ${REPO_NAME}"

curl -s -m 10 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d "chat_id=${TELEGRAM_CHAT_ID}" \
  -d "text=${MESSAGE}" \
  > /dev/null || true

exit 0
