#!/bin/bash

##############################################
# Azure PostgreSQL Flexible Server Manager
# Stops and starts server on weekend schedule
##############################################

# Configuration
RESOURCE_GROUP="your-resource-group-name"
SERVER_NAME="your-postgres-server-name"
LOG_FILE="/var/log/postgres-schedule.log"

# Function to log messages
log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Function to check server status
check_status() {
    log_message "Checking current server status..."
    az postgres flexible-server show \
        --resource-group "$RESOURCE_GROUP" \
        --name "$SERVER_NAME" \
        --query "state" \
        --output tsv 2>/dev/null
}

# Function to stop server
stop_server() {
    log_message "=== STOP Operation Started ==="
    
    current_status=$(check_status)
    
    if [ "$current_status" == "Stopped" ]; then
        log_message "INFO: Server is already stopped"
        return 0
    fi
    
    log_message "Stopping PostgreSQL Flexible Server: $SERVER_NAME"
    
    az postgres flexible-server stop \
        --resource-group "$RESOURCE_GROUP" \
        --name "$SERVER_NAME" \
        >> "$LOG_FILE" 2>&1
    
    if [ $? -eq 0 ]; then
        log_message "SUCCESS: Server stopped successfully"
        return 0
    else
        log_message "ERROR: Failed to stop server"
        return 1
    fi
}

# Function to start server
start_server() {
    log_message "=== START Operation Started ==="
    
    current_status=$(check_status)
    
    if [ "$current_status" == "Ready" ]; then
        log_message "INFO: Server is already running"
        return 0
    fi
    
    log_message "Starting PostgreSQL Flexible Server: $SERVER_NAME"
    
    az postgres flexible-server start \
        --resource-group "$RESOURCE_GROUP" \
        --name "$SERVER_NAME" \
        >> "$LOG_FILE" 2>&1
    
    if [ $? -eq 0 ]; then
        log_message "SUCCESS: Server started successfully"
        return 0
    else
        log_message "ERROR: Failed to start server"
        return 1
    fi
}

# Function to display usage
usage() {
    echo "Usage: $0 {start|stop|status}"
    echo ""
    echo "Commands:"
    echo "  start   - Start the PostgreSQL server"
    echo "  stop    - Stop the PostgreSQL server"
    echo "  status  - Check current server status"
    echo ""
    echo "Examples:"
    echo "  $0 stop"
    echo "  $0 start"
    exit 1
}

# Main script logic
main() {
    # Check if Azure CLI is installed
    if ! command -v az &> /dev/null; then
        log_message "ERROR: Azure CLI is not installed"
        exit 1
    fi
    
    # Check if user is logged in to Azure
    az account show &> /dev/null
    if [ $? -ne 0 ]; then
        log_message "ERROR: Not logged in to Azure. Run 'az login' first"
        exit 1
    fi
    
    # Parse command line argument
    ACTION=${1:-""}
    
    case "$ACTION" in
        stop)
            stop_server
            exit $?
            ;;
        start)
            start_server
            exit $?
            ;;
        status)
            status=$(check_status)
            log_message "Current server status: $status"
            echo "Server Status: $status"
            exit 0
            ;;
        *)
            usage
            ;;
    esac
}

# Run main function
main "$@"
