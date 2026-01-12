#!/bin/bash

ID="RM_kgDaACQSNnEWYMM4Mi0zMZzJLTQ5YzgtYjVjYS02NzVmNmFlZmFlM2E"
APP="gei-cli"

# Enable colors only if stdout is a terminal
if [[ -t 1 ]]; then
  GREEN="\033[0;32m"
  YELLOW="\033[0;33m"
  RED="\033[0;31m"
  RESET="\033[0m"
else
  GREEN=""; YELLOW=""; RED=""; RESET=""
fi

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  level="$1"
  color="$2"
  msg="$3"
  printf "%s [%b%s%b] [%s] %s\n" \
    "$(timestamp)" "$color" "$level" "$RESET" "$APP" "$msg"
}

states=("PENDING_VALIDATION" "QUEUED" "IN_PROGRESS")
errors=(
  "Network timeout contacting GitHub"
  "Rate limit exceeded"
  "Protected branch policy violation"
)

log INFO "$GREEN" "Starting migration monitor (ID=$ID)"

while true; do
  state="${states[$RANDOM % ${#states[@]}]}"

  log INFO "$GREEN" "Migration in progress (ID=$ID). State=$state. Waiting 10 seconds..."

  # Random warning
  if (( RANDOM % 5 == 0 )); then
    log WARN "$YELLOW" "GitHub API latency detected"
  fi

  # Random error
  if (( RANDOM % 8 == 0 )); then
    log ERROR "$RED" "${errors[$RANDOM % ${#errors[@]}]}"
    log INFO  "$GREEN" "Retrying operation"
  fi

  sleep 10
done
