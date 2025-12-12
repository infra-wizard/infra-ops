#!/bin/bash

# Configuration
RESOURCE_GROUP="your-resource-group-name"
SERVER_NAME="your-postgres-server-name"

echo "$(date): Stopping PostgreSQL Flexible Server: $SERVER_NAME"

# Stop the server
az postgres flexible-server stop \
    --resource-group $RESOURCE_GROUP \
    --name $SERVER_NAME

if [ $? -eq 0 ]; then
    echo "$(date): Server stopped successfully"
else
    echo "$(date): Failed to stop server"
    exit 1
fi
