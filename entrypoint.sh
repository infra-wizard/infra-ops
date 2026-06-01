#!/bin/sh
# entrypoint.sh
# Starts both the background email alerter and the web dashboard in the same pod.

set -e

echo "=== cert-expiry-monitor starting ==="
echo "    Web dashboard : http://0.0.0.0:8080"
echo "    Data file     : ${DATA_FILE:-/data/certs.csv}"

# Start web dashboard in background
python3 /app/web_server.py &
WEB_PID=$!

# Start email alert loop in foreground
python3 /app/cert_expiry_alert.py &
ALERT_PID=$!

# If either process dies, kill the other and exit
wait -n $WEB_PID $ALERT_PID
kill $WEB_PID $ALERT_PID 2>/dev/null
