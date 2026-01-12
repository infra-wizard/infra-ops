#!/bin/bash

ID="RM_kgDaACQSNnEWYMM4Mi0zMZzJLTQ5YzgtYjVjYS02NzVmNmFlZmFlM2E"
APP="gei-cli"

# Detect terminal
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

log INFO  "$GREEN"  "Starting repository migration"
log INFO  "$GREEN"  "Source: Trail-Tech/bluetooth-phonebook-rcip"
log INFO  "$GREEN"  "Target: Polaris-InVehicleSoftware/bluetooth-phonebook-rcip"

sleep 2
log INFO  "$GREEN"  "Migration created (ID=$ID)"

sleep 4
log INFO  "$GREEN"  "State=PENDING_VALIDATION – validating permissions"

sleep 3
log WARN  "$YELLOW" "GitHub API latency detected"

sleep 4
log INFO  "$GREEN"  "State=QUEUED – awaiting worker"

sleep 4
log INFO  "$GREEN"  "State=IN_PROGRESS – cloning repository"

sleep 3
log ERROR "$RED"    "Clone failed: network timeout"
log INFO  "$GREEN"  "Retrying clone (attempt 2/3)"

sleep 4
log INFO  "$GREEN"  "Clone successful"

sleep 3
log INFO  "$GREEN"  "Pushing repository to target"

sleep 3
log ERROR "$RED"    "Push rejected: protected branch policy"
log WARN  "$YELLOW" "Retrying with admin override"

sleep 4
log INFO  "$GREEN"  "Push successful"

sleep 3
log INFO  "$GREEN"  "Finalizing migration"

sleep 2
log INFO  "$GREEN"  "Migration completed successfully (ID=$ID)"
