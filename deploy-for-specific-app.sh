#!/bin/bash

# Deploy Grafana Alloy for a specific Java application
# This script targets a specific app for testing

set -e

# Configuration
APP_NAME="twx-twx-thingworx"
APP_LABEL="thingworx-server"
APP_NAMESPACE="twx"
MONITORING_NAMESPACE="monitoring"

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
    echo -e "${BLUE}[DEPLOY]${NC} $1"
}

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

# Function to check if the specific app exists
check_app_exists() {
    print_status "Checking if $APP_NAME exists in namespace $APP_NAMESPACE..."
    
    # First check if pods with the label exist
    local pods=$(kubectl get pods -n $APP_NAMESPACE -l app=$APP_LABEL --no-headers 2>/dev/null | wc -l)
    if [ "$pods" -gt 0 ]; then
        print_status "✅ Found $pods pod(s) with label app=$APP_LABEL in namespace: $APP_NAMESPACE"
        return 0
    fi
    
    # Check if StatefulSet exists
    if kubectl get statefulset $APP_NAME -n $APP_NAMESPACE &> /dev/null; then
        print_status "✅ Found StatefulSet: $APP_NAME in namespace: $APP_NAMESPACE"
        return 0
    fi
    
    # Check if Deployment exists
    if kubectl get deployment $APP_NAME -n $APP_NAMESPACE &> /dev/null; then
        print_status "✅ Found Deployment: $APP_NAME in namespace: $APP_NAMESPACE"
        return 0
    fi
    
    print_error "Application $APP_NAME not found in namespace $APP_NAMESPACE"
    print_status "Available applications in namespace $APP_NAMESPACE:"
    kubectl get statefulsets,deployments -n $APP_NAMESPACE
    print_status "Available pods with labels:"
    kubectl get pods -n $APP_NAMESPACE --show-labels
    return 1
}

# Function to detect JMX configuration for the specific app
detect_app_jmx() {
    print_status "Detecting JMX configuration for $APP_NAME..."
    
    # Get pods for the specific app using the correct label
    local pods=$(kubectl get pods -n $APP_NAMESPACE -l app.kubernetes.io/name=$APP_LABEL --no-headers 2>/dev/null | awk '{print $1}' || echo "")
    
    if [ -z "$pods" ]; then
        # Try alternative selectors
        pods=$(kubectl get pods -n $APP_NAMESPACE -l app=$APP_LABEL --no-headers 2>/dev/null | awk '{print $1}' || echo "")
    fi
    
    if [ -z "$pods" ]; then
        # Try name selector
        pods=$(kubectl get pods -n $APP_NAMESPACE -l name=$APP_LABEL --no-headers 2>/dev/null | awk '{print $1}' || echo "")
    fi
    
    if [ -z "$pods" ]; then
        # Try by name pattern
        pods=$(kubectl get pods -n $APP_NAMESPACE --field-selector metadata.name=$APP_NAME --no-headers 2>/dev/null | awk '{print $1}' || echo "")
    fi
    
    if [ -z "$pods" ]; then
        print_error "No pods found for $APP_NAME with label app=$APP_LABEL in namespace $APP_NAMESPACE"
        print_status "Available pods in namespace $APP_NAMESPACE:"
        kubectl get pods -n $APP_NAMESPACE
        return 1
    fi
    
    local pod_name=$(echo $pods | head -1)
    print_status "Found pod: $pod_name"
    
    # Check for JMX sidecar containers
    local jmx_containers=$(kubectl get pod $pod_name -n $APP_NAMESPACE -o jsonpath='{.spec.containers[?(@.name=~".*jmx.*|.*exporter.*")].name}' 2>/dev/null || echo "")
    
    if [ -n "$jmx_containers" ]; then
        print_status "Found JMX sidecar containers: $jmx_containers"
        
        # Check JMX ports
        local jmx_ports=$(kubectl get pod $pod_name -n $APP_NAMESPACE -o jsonpath='{.spec.containers[?(@.name=~".*jmx.*|.*exporter.*")].ports[?(@.name=~".*jmx.*|.*metrics.*")].containerPort}' 2>/dev/null || echo "")
        
        if [ -n "$jmx_ports" ]; then
            print_status "JMX ports: $jmx_ports"
            echo $jmx_ports | head -1
            return 0
        fi
    fi
    
    # Check if JMX is running on main container
    print_status "Checking if JMX is running on main container..."
    local main_container=$(kubectl get pod $pod_name -n $APP_NAMESPACE -o jsonpath='{.spec.containers[0].name}' 2>/dev/null || echo "")
    
    if [ -n "$main_container" ]; then
        for port in 5556 9404 8080 9090 8081; do
            if kubectl exec $pod_name -n $APP_NAMESPACE -c $main_container -- curl -s http://localhost:$port/metrics >/dev/null 2>&1; then
                print_status "✅ JMX metrics accessible on port $port for main container $main_container"
                echo $port
                return 0
            fi
        done
    fi
    
    print_warning "No JMX metrics found. Will use default port 9404"
    echo "9404"
    return 1
}

# Function to create targeted Alloy configuration
create_targeted_config() {
    local jmx_port=$1
    
    print_status "Creating targeted Alloy configuration for $APP_NAME..."
    
    cat > k8s-manifests/grafana-alloy-specific-app.yaml << EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $MONITORING_NAMESPACE
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: grafana-alloy
  namespace: $MONITORING_NAMESPACE
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: grafana-alloy
rules:
- apiGroups: [""]
  resources: ["nodes", "nodes/proxy", "services", "endpoints", "pods"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["extensions"]
  resources: ["ingresses"]
  verbs: ["get", "list", "watch"]
- nonResourceURLs: ["/metrics"]
  verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: grafana-alloy
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: grafana-alloy
subjects:
- kind: ServiceAccount
  name: grafana-alloy
  namespace: $MONITORING_NAMESPACE
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-alloy-config
  namespace: $MONITORING_NAMESPACE
data:
  alloy.river: |
    // Grafana Alloy configuration for specific Java application: $APP_NAME
    // This configuration targets $APP_NAME in namespace $APP_NAMESPACE

    // Logging configuration
    logging {
      level = "info"
      format = "logfmt"
    }

    // Kubernetes service discovery for specific Java application
    discovery.kubernetes "specific_java_app" {
      role = "pod"
      
      // Target specific application using the correct label
      selectors {
        app.kubernetes.io/name = "$APP_LABEL"
      }
      
      // Alternative selectors for StatefulSet
      selectors {
        app = "$APP_LABEL"
      }
      
      // Additional selectors for StatefulSet
      selectors {
        name = "$APP_LABEL"
      }
    }

    // JMX metrics collection from the specific application
    prometheus.scrape "jmx_metrics" {
      targets = discovery.kubernetes.specific_java_app.targets
      scrape_interval = "30s"
      metrics_path = "/metrics"  // JMX exporter endpoint
      
      // Relabel to use JMX exporter sidecar port
      relabel_config {
        source_labels = ["__address__"]
        regex = "([^:]+):(.*)"
        target_label = "__address__"
        replacement = "\${1}:$jmx_port"  // Use detected JMX port
      }
      
      relabel_config {
        source_labels = ["__meta_kubernetes_pod_name"]
        target_label  = "pod"
      }
      
      relabel_config {
        source_labels = ["__meta_kubernetes_namespace"]
        target_label  = "namespace"
      }
      
      relabel_config {
        source_labels = ["__meta_kubernetes_pod_label_app"]
        target_label  = "app"
      }
      
      relabel_config {
        source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_name"]
        target_label  = "app_name"
      }
      
      // Add specific labels for this application
      relabel_config {
        source_labels = []
        target_label  = "target_app"
        replacement = "$APP_NAME"
      }
    }

    // Container metrics collection for the specific application
    prometheus.scrape "container_metrics" {
      targets = discovery.kubernetes.specific_java_app.targets
      scrape_interval = "30s"
      metrics_path = "/metrics/cadvisor"
      
      relabel_config {
        source_labels = ["__meta_kubernetes_pod_name"]
        target_label  = "pod"
      }
      
      relabel_config {
        source_labels = ["__meta_kubernetes_namespace"]
        target_label  = "namespace"
      }
      
      relabel_config {
        source_labels = ["__meta_kubernetes_pod_label_app"]
        target_label  = "app"
      }
    }

    // Log collection from the specific Java application
    loki.source.kubernetes "java_logs" {
      targets = discovery.kubernetes.specific_java_app.targets
      forward_to = [loki.process.java_logs.receiver]
    }

    // Advanced log processing for the specific Java application
    loki.process "java_logs" {
      forward_to = [loki.write.default.receiver]
      
      // Parse standard Java log formats
      stage.regex {
        expression = "(?P<timestamp>\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2}\\.\\d{3})\\s+(?P<level>\\w+)\\s+(?P<thread>\\[.*?\\])\\s+(?P<logger>\\S+)\\s+-\\s+(?P<message>.*)"
      }
      
      // Parse ISO timestamp format
      stage.regex {
        expression = "(?P<timestamp>\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{3}Z)\\s+(?P<level>\\w+)\\s+(?P<thread>\\[.*?\\])\\s+(?P<logger>\\S+)\\s+-\\s+(?P<message>.*)"
      }
      
      // Add labels for better organization
      stage.labels {
        values = {
          level = "",
          logger = "",
          thread = "",
        }
      }
      
      // Add Kubernetes metadata labels
      stage.labels {
        values = {
          app = "\$__meta_kubernetes_pod_label_app",
          namespace = "\$__meta_kubernetes_namespace",
          pod = "\$__meta_kubernetes_pod_name",
          node = "\$__meta_kubernetes_node_name",
          container = "\$__meta_kubernetes_pod_container_name",
          target_app = "$APP_NAME",
        }
      }
      
      // Filter out health check and noise logs
      stage.match {
        selector = "{level=\"INFO\"} |= \"health\" |= \"heartbeat\""
        action = "drop"
      }
      
      // Extract error patterns
      stage.regex {
        expression = "(?P<error_type>Exception|Error|FATAL|CRITICAL|OutOfMemoryError|StackOverflowError)"
        action = "keep"
      }
    }

    // Prometheus remote write configuration
    prometheus.remote_write "default" {
      endpoint {
        url = "https://prometheus-us-central1.grafana.net/api/prom/push"
        
        // Add your Grafana Cloud credentials here
        // basic_auth {
        //   username = env("GRAFANA_CLOUD_USERNAME")
        //   password = env("GRAFANA_CLOUD_API_KEY")
        // }
      }
      
      // Write configuration
      write_relabel_config {
        source_labels = ["__name__"]
        regex = "up"
        action = "drop"
      }
    }

    // Loki write configuration
    loki.write "default" {
      endpoint {
        url = "https://logs-prod-us-central1.grafana.net/loki/api/v1/push"
        
        // Add your Grafana Cloud credentials here
        // basic_auth {
        //   username = env("GRAFANA_CLOUD_USERNAME")
        //   password = env("GRAFANA_CLOUD_API_KEY")
        // }
      }
    }

    // Export metrics for debugging
    prometheus.exporter.self "alloy" {}

    prometheus.scrape "alloy" {
      targets    = prometheus.exporter.self.alloy.targets
      scrape_interval = "30s"
      forward_to = [prometheus.remote_write.default.receiver]
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana-alloy
  namespace: $MONITORING_NAMESPACE
  labels:
    app: grafana-alloy
    target-app: $APP_NAME
spec:
  replicas: 1
  selector:
    matchLabels:
      app: grafana-alloy
  template:
    metadata:
      labels:
        app: grafana-alloy
        target-app: $APP_NAME
    spec:
      serviceAccountName: grafana-alloy
      containers:
      - name: grafana-alloy
        image: grafana/alloy:latest
        args:
          - run
          - /etc/alloy/alloy.river
          - --server.http.listen-addr=0.0.0.0:12345
          - --storage.path=/var/lib/alloy/data
        ports:
        - containerPort: 12345
          name: http
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        volumeMounts:
        - name: config
          mountPath: /etc/alloy
        - name: storage
          mountPath: /var/lib/alloy
        env:
        - name: GRAFANA_CLOUD_USERNAME
          valueFrom:
            secretKeyRef:
              name: grafana-cloud-credentials
              key: username
        - name: GRAFANA_CLOUD_API_KEY
          valueFrom:
            secretKeyRef:
              name: grafana-cloud-credentials
              key: api-key
      volumes:
      - name: config
        configMap:
          name: grafana-alloy-config
      - name: storage
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: grafana-alloy
  namespace: $MONITORING_NAMESPACE
  labels:
    app: grafana-alloy
    target-app: $APP_NAME
spec:
  selector:
    app: grafana-alloy
  ports:
  - port: 12345
    targetPort: 12345
    name: http
  type: ClusterIP
EOF
}

# Main deployment function
deploy_for_specific_app() {
    print_header "Deploying Grafana Alloy for specific application: $APP_NAME"
    
    # Check if the app exists
    if ! check_app_exists; then
        exit 1
    fi
    
    # Detect JMX configuration
    local jmx_port=$(detect_app_jmx)
    
    # Use port 5556 if detection fails
    if [ -z "$jmx_port" ] || [ "$jmx_port" = "9404" ]; then
        jmx_port="5556"
        print_status "Using JMX port 5556 (detected from your setup)"
    fi
    
    # Create targeted configuration
    create_targeted_config $jmx_port
    
    # Create monitoring namespace
    print_status "Creating monitoring namespace: $MONITORING_NAMESPACE"
    kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: $MONITORING_NAMESPACE
EOF

    # Check if Grafana Cloud credentials are provided
    if [ -z "$GRAFANA_CLOUD_USERNAME" ] || [ -z "$GRAFANA_CLOUD_API_KEY" ]; then
        print_warning "Grafana Cloud credentials not provided via environment variables."
        print_warning "Please set GRAFANA_CLOUD_USERNAME and GRAFANA_CLOUD_API_KEY"
        print_warning "Or create a secret manually:"
        echo "kubectl create secret generic grafana-cloud-credentials \\"
        echo "  --from-literal=username=your-grafana-username \\"
        echo "  --from-literal=api-key=your-grafana-api-key \\"
        echo "  --namespace=$MONITORING_NAMESPACE"
        echo ""
        read -p "Do you want to continue without credentials? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        # Create Grafana Cloud credentials secret
        print_status "Creating Grafana Cloud credentials secret..."
        kubectl create secret generic grafana-cloud-credentials \
            --from-literal=username="$GRAFANA_CLOUD_USERNAME" \
            --from-literal=api-key="$GRAFANA_CLOUD_API_KEY" \
            --namespace=$MONITORING_NAMESPACE \
            --dry-run=client -o yaml | kubectl apply -f -
    fi

    # Deploy Grafana Alloy
    print_status "Deploying Grafana Alloy targeting $APP_NAME with JMX port: $jmx_port..."
    kubectl apply -f k8s-manifests/grafana-alloy-specific-app.yaml

    # Wait for deployment to be ready
    print_status "Waiting for Grafana Alloy to be ready..."
    kubectl wait --for=condition=available --timeout=300s deployment/grafana-alloy -n $MONITORING_NAMESPACE

    # Check deployment status
    print_status "Checking deployment status..."
    kubectl get pods -n $MONITORING_NAMESPACE -l app=grafana-alloy

    # Get service information
    print_status "Grafana Alloy service information:"
    kubectl get svc -n $MONITORING_NAMESPACE grafana-alloy

    # Display useful commands
    print_status "Deployment completed successfully!"
    echo ""
    print_status "Targeted monitoring for: $APP_NAME in namespace: $APP_NAMESPACE"
    print_status "JMX port detected: $jmx_port"
    echo ""
    print_status "Useful commands:"
    echo "  View logs: kubectl logs -f deployment/grafana-alloy -n $MONITORING_NAMESPACE"
    echo "  Check targets: kubectl port-forward svc/grafana-alloy 12345:12345 -n $MONITORING_NAMESPACE"
    echo "  Then visit: http://localhost:12345/api/v1/targets"
    echo "  Check configuration: kubectl exec -it deployment/grafana-alloy -n $MONITORING_NAMESPACE -- /bin/alloy --config.file=/etc/alloy/alloy.river --dry-run"
    echo ""

    # Check if the specific app is being monitored
    print_status "Checking if $APP_NAME is being monitored..."
    local monitored_pods=$(kubectl get pods -n $APP_NAMESPACE -l app=$APP_LABEL --no-headers 2>/dev/null | wc -l)
    if [ "$monitored_pods" -gt 0 ]; then
        print_status "✅ Found $monitored_pods pod(s) for $APP_NAME with label app=$APP_LABEL. They should be automatically discovered."
        kubectl get pods -n $APP_NAMESPACE -l app=$APP_LABEL
    else
        print_warning "No pods found for $APP_NAME with label app=$APP_LABEL"
        print_status "Available pods in namespace $APP_NAMESPACE:"
        kubectl get pods -n $APP_NAMESPACE
    fi

    print_status "Setup complete! $APP_NAME should now be monitored by Grafana Alloy."
}

# Run deployment
deploy_for_specific_app
