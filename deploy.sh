#!/bin/bash

ENVIRONMENT=${1:-dev}

# Validate environment
if [[ ! "$ENVIRONMENT" =~ ^(dev|staging|prod)$ ]]; then
    echo "Error: Environment must be dev, staging, or prod"
    echo "Usage: $0 <environment>"
    echo "Example: $0 prod"
    exit 1
fi

# Set ACR registry and image pull secret based on environment
case $ENVIRONMENT in
    dev)
        ACR_REGISTRY="devacr.azurecr.io"
        IMAGE_PULL_SECRET="dev-acr-secret"
        ;;
    staging)
        ACR_REGISTRY="stagingacr.azurecr.io"
        IMAGE_PULL_SECRET="staging-acr-secret"
        ;;
    prod)
        ACR_REGISTRY="prodacr.azurecr.io"
        IMAGE_PULL_SECRET="prod-acr-secret"
        ;;
esac

OUTPUT_FILE="values-${ENVIRONMENT}.yaml"

echo "Generating values file for ${ENVIRONMENT} environment..."
echo "ACR Registry: ${ACR_REGISTRY}"
echo "Image Pull Secret: ${IMAGE_PULL_SECRET}"
echo "Output file: ${OUTPUT_FILE}"

# Generate the values file
cat > ${OUTPUT_FILE} << EOF
# Generated values file for ${ENVIRONMENT} environment
# ACR Registry: ${ACR_REGISTRY}
# Generated on: $(date)

# Global settings
global:
  imageRegistry: "${ACR_REGISTRY}"
  imagePullSecrets:
    - name: "${IMAGE_PULL_SECRET}"

# Prometheus Operator
prometheusOperator:
  enabled: true
  image:
    registry: "${ACR_REGISTRY}"
    repository: "prometheus-operator/prometheus-operator"
    tag: "v0.68.0"
  imagePullSecrets:
    - name: "${IMAGE_PULL_SECRET}"
  prometheusConfigReloader:
    image:
      registry: "${ACR_REGISTRY}"
      repository: "prometheus-operator/prometheus-config-reloader"
      tag: "v0.68.0"
  thanosImage:
    registry: "${ACR_REGISTRY}"
    repository: "thanos/thanos"
    tag: "v0.32.5"

# Prometheus
prometheus:
  enabled: true
  prometheusSpec:
    image:
      registry: "${ACR_REGISTRY}"
      repository: "prometheus/prometheus"
      tag: "v2.47.1"
    imagePullSecrets:
      - name: "${IMAGE_PULL_SECRET}"
    retention: 30d
    retentionSize: 45GB
    replicas: 1
    serviceMonitorSelectorNilUsesHelmValues: false
    serviceMonitorSelector: {}
    podMonitorSelectorNilUsesHelmValues: false
    podMonitorSelector: {}
    ruleSelectorNilUsesHelmValues: false
    ruleSelector: {}
    storageSpec:
      volumeClaimTemplate:
        spec:
          storageClassName: "default"
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 50Gi
    resources:
      requests:
        memory: 1Gi
        cpu: 500m
      limits:
        memory: 4Gi
        cpu: 2000m

# Alertmanager
alertmanager:
  enabled: true
  alertmanagerSpec:
    image:
      registry: "${ACR_REGISTRY}"
      repository: "prometheus/alertmanager"
      tag: "v0.26.0"
    imagePullSecrets:
      - name: "${IMAGE_PULL_SECRET}"
    replicas: 1
    retention: 720h
    storage:
      volumeClaimTemplate:
        spec:
          storageClassName: "default"
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 5Gi
    resources:
      requests:
        memory: 128Mi
        cpu: 50m
      limits:
        memory: 256Mi
        cpu: 200m

# Grafana
grafana:
  enabled: true
  image:
    registry: "${ACR_REGISTRY}"
    repository: "grafana/grafana"
    tag: "10.1.4"
  imagePullSecrets:
    - name: "${IMAGE_PULL_SECRET}"
  adminPassword: "admin123"
  persistence:
    enabled: true
    storageClassName: "default"
    size: 10Gi
  resources:
    requests:
      memory: 256Mi
      cpu: 200m
    limits:
      memory: 512Mi
      cpu: 500m
  sidecar:
    image:
      registry: "${ACR_REGISTRY}"
      repository: "kiwigrid/k8s-sidecar"
      tag: "1.25.2"
    dashboards:
      enabled: true
      label: grafana_dashboard
    datasources:
      enabled: true
      defaultDatasourceEnabled: true
  defaultDashboardsEnabled: true
  defaultDashboardsTimezone: utc
  adminUser: admin
  ingress:
    enabled: false

# Kube State Metrics
kubeStateMetrics:
  enabled: true
  image:
    registry: "${ACR_REGISTRY}"
    repository: "registry.k8s.io/kube-state-metrics/kube-state-metrics"
    tag: "v2.10.0"

# Node Exporter
nodeExporter:
  enabled: true
  image:
    registry: "${ACR_REGISTRY}"
    repository: "prometheus/node-exporter"
    tag: "v1.6.1"

# Kubernetes component monitoring
kubeApiServer:
  enabled: true

kubelet:
  enabled: true

kubeControllerManager:
  enabled: true

coreDns:
  enabled: true

kubeEtcd:
  enabled: true

kubeScheduler:
  enabled: true

kubeProxy:
  enabled: true
EOF

echo ""
echo "✅ Successfully generated ${OUTPUT_FILE}"
echo ""
echo "File contents preview:"
echo "======================"
head -20 ${OUTPUT_FILE}
echo "... (truncated)"
echo ""
echo "You can now use this file for deployment:"
echo "helm upgrade --install kube-prometheus-stack-${ENVIRONMENT} prometheus-community/kube-prometheus-stack \\"
echo "  --namespace monitoring-${ENVIRONMENT} \\"
echo "  --values ${OUTPUT_FILE}"
