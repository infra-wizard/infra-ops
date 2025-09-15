#!/bin/bash

# Test script for specific Java application: twx-twx-thingworx
# This script helps verify the application and JMX setup before deployment

set -e

# Configuration
APP_NAME="twx-twx-thingworx"
APP_LABEL="thingworx-server"
APP_NAMESPACE="twx"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}[TEST]${NC} $1"
}

print_header "Testing application: $APP_NAME in namespace: $APP_NAMESPACE"

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    print_error "kubectl is not installed. Please install kubectl first."
    exit 1
fi

# Check if we're connected to a Kubernetes cluster
if ! kubectl cluster-info &> /dev/null; then
    print_error "Not connected to a Kubernetes cluster. Please configure kubectl first."
    exit 1
fi

# Check if namespace exists
print_status "Checking if namespace $APP_NAMESPACE exists..."
if kubectl get namespace $APP_NAMESPACE &> /dev/null; then
    print_status "✅ Namespace $APP_NAMESPACE exists"
else
    print_error "Namespace $APP_NAMESPACE does not exist"
    print_status "Available namespaces:"
    kubectl get namespaces
    exit 1
fi

# First check if pods with the label exist
print_status "Checking if pods with label app=$APP_LABEL exist..."
LABEL_PODS=$(kubectl get pods -n $APP_NAMESPACE -l app=$APP_LABEL --no-headers 2>/dev/null | wc -l)
if [ "$LABEL_PODS" -gt 0 ]; then
    print_status "✅ Found $LABEL_PODS pod(s) with label app=$APP_LABEL"
else
    print_warning "No pods found with label app=$APP_LABEL"
fi

# Check if StatefulSet exists
print_status "Checking if StatefulSet $APP_NAME exists..."
if kubectl get statefulset $APP_NAME -n $APP_NAMESPACE &> /dev/null; then
    print_status "✅ StatefulSet $APP_NAME exists"
    
    # Get StatefulSet details
    print_status "StatefulSet details:"
    kubectl get statefulset $APP_NAME -n $APP_NAMESPACE -o wide
    
    # Get StatefulSet status
    print_status "StatefulSet status:"
    kubectl describe statefulset $APP_NAME -n $APP_NAMESPACE | grep -A 10 "Status:"
    
else
    print_warning "StatefulSet $APP_NAME not found"
    
    # Check for Deployment
    print_status "Checking for Deployment $APP_NAME..."
    if kubectl get deployment $APP_NAME -n $APP_NAMESPACE &> /dev/null; then
        print_status "✅ Deployment $APP_NAME exists"
        kubectl get deployment $APP_NAME -n $APP_NAMESPACE -o wide
    else
        print_warning "Neither StatefulSet nor Deployment $APP_NAME found"
        print_status "Available StatefulSets and Deployments in namespace $APP_NAMESPACE:"
        kubectl get statefulsets,deployments -n $APP_NAMESPACE
    fi
fi

# Check pods
print_status "Checking pods for $APP_NAME with label app=$APP_LABEL..."
PODS=$(kubectl get pods -n $APP_NAMESPACE -l app=$APP_LABEL --no-headers 2>/dev/null | awk '{print $1}' || echo "")

if [ -z "$PODS" ]; then
    # Try alternative selectors
    PODS=$(kubectl get pods -n $APP_NAMESPACE -l name=$APP_LABEL --no-headers 2>/dev/null | awk '{print $1}' || echo "")
fi

if [ -z "$PODS" ]; then
    # Try by name pattern
    PODS=$(kubectl get pods -n $APP_NAMESPACE --field-selector metadata.name=$APP_NAME --no-headers 2>/dev/null | awk '{print $1}' || echo "")
fi

if [ -z "$PODS" ]; then
    print_warning "No pods found with label app=$APP_LABEL or name=$APP_LABEL"
    print_status "Available pods in namespace $APP_NAMESPACE:"
    kubectl get pods -n $APP_NAMESPACE
    print_status "Trying to find pods by name pattern..."
    kubectl get pods -n $APP_NAMESPACE | grep -i thingworx || echo "No pods found with 'thingworx' in name"
else
    print_status "✅ Found pods for $APP_NAME with label app=$APP_LABEL:"
    echo "$PODS" | while read pod; do
        if [ -n "$pod" ]; then
            echo "  - $pod"
        fi
    done
fi

# Check containers in pods
if [ -n "$PODS" ]; then
    local pod_name=$(echo $PODS | head -1)
    print_status "Analyzing pod: $pod_name"
    
    # Get container information
    print_status "Containers in pod:"
    kubectl get pod $pod_name -n $APP_NAMESPACE -o jsonpath='{.spec.containers[*].name}' | tr ' ' '\n' | sed 's/^/  - /'
    
    # Check for JMX sidecar containers
    print_status "Checking for JMX sidecar containers..."
    JMX_CONTAINERS=$(kubectl get pod $pod_name -n $APP_NAMESPACE -o jsonpath='{.spec.containers[?(@.name=~".*jmx.*|.*exporter.*")].name}' 2>/dev/null || echo "")
    
    if [ -n "$JMX_CONTAINERS" ]; then
        print_status "✅ Found JMX sidecar containers: $JMX_CONTAINERS"
        
        # Check JMX ports
        JMX_PORTS=$(kubectl get pod $pod_name -n $APP_NAMESPACE -o jsonpath='{.spec.containers[?(@.name=~".*jmx.*|.*exporter.*")].ports[?(@.name=~".*jmx.*|.*metrics.*")].containerPort}' 2>/dev/null || echo "")
        
        if [ -n "$JMX_PORTS" ]; then
            print_status "✅ JMX ports: $JMX_PORTS"
        else
            print_warning "No JMX ports found in container spec"
        fi
        
        # Test JMX metrics endpoint
        for container in $JMX_CONTAINERS; do
            print_status "Testing JMX metrics endpoint for container: $container"
            
            for port in 9404 8080 9090 8081; do
                if kubectl exec $pod_name -n $APP_NAMESPACE -c $container -- curl -s http://localhost:$port/metrics >/dev/null 2>&1; then
                    print_status "✅ JMX metrics accessible on port $port for container $container"
                    
                    # Get sample metrics
                    print_status "Sample metrics from $container:"
                    kubectl exec $pod_name -n $APP_NAMESPACE -c $container -- curl -s http://localhost:$port/metrics 2>/dev/null | head -5 | sed 's/^/  /'
                    break
                fi
            done
        done
    else
        print_warning "No JMX sidecar containers found"
        
        # Check if JMX is running on main container
        print_status "Checking if JMX is running on main container..."
        MAIN_CONTAINER=$(kubectl get pod $pod_name -n $APP_NAMESPACE -o jsonpath='{.spec.containers[0].name}' 2>/dev/null || echo "")
        
        if [ -n "$MAIN_CONTAINER" ]; then
            print_status "Testing JMX on main container: $MAIN_CONTAINER"
            for port in 9404 8080 9090 8081; do
                if kubectl exec $pod_name -n $APP_NAMESPACE -c $MAIN_CONTAINER -- curl -s http://localhost:$port/metrics >/dev/null 2>&1; then
                    print_status "✅ JMX metrics accessible on port $port for main container $MAIN_CONTAINER"
                    
                    # Get sample metrics
                    print_status "Sample metrics from main container:"
                    kubectl exec $pod_name -n $APP_NAMESPACE -c $MAIN_CONTAINER -- curl -s http://localhost:$port/metrics 2>/dev/null | head -5 | sed 's/^/  /'
                    break
                fi
            done
        fi
    fi
    
    # Check pod labels
    print_status "Pod labels:"
    kubectl get pod $pod_name -n $APP_NAMESPACE --show-labels --no-headers | awk '{print $NF}' | tr ',' '\n' | sed 's/^/  /'
    
    # Check pod status
    print_status "Pod status:"
    kubectl get pod $pod_name -n $APP_NAMESPACE -o wide
fi

# Summary
print_header "Summary"
print_status "Application: $APP_NAME"
print_status "Label: app=$APP_LABEL"
print_status "Namespace: $APP_NAMESPACE"
print_status "Pods found: $(echo $PODS | wc -w)"

if [ -n "$JMX_CONTAINERS" ]; then
    print_status "JMX sidecar containers: $JMX_CONTAINERS"
    print_status "✅ Ready for monitoring with existing JMX setup"
else
    print_warning "No JMX sidecar containers found"
    print_status "You may need to add JMX monitoring or use the zero-code-changes approach"
fi

print_status "Test complete!"
