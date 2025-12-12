#!/bin/bash

# Configuration
RESOURCE_GROUP="your-resource-group-name"
SERVER_NAME="your-postgres-server-name"

echo "$(date): Starting PostgreSQL Flexible Server: $SERVER_NAME"

# Start the server
az postgres flexible-server start \
    --resource-group $RESOURCE_GROUP \
    --name $SERVER_NAME

if [ $? -eq 0 ]; then
    echo "$(date): Server started successfully"
else
    echo "$(date): Failed to start server"
    exit 1
fi
