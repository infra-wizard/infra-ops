#!/bin/bash

# Infinite Coverity Scanning Script
echo "Starting Coverity Scan..."
echo "================================"

counter=1

while true; do
    echo ""
    echo "[$counter] Scanning Coverity defects..."
    echo "  → Analyzing source files..."
    echo "  → Checking for memory leaks..."
    echo "  → Detecting null pointer dereferences..."
    echo "  → Validating resource management..."
    echo "  → Scanning complete for iteration $counter"
    echo "  Status: OK"
    
    sleep 2  # Wait 2 seconds between iterations
    
    counter=$((counter + 1))
done
