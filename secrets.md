# External Secrets Operator (ESO) with Azure Key Vault - Complete Guide

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Azure Setup](#azure-setup)
5. [ESO Configuration](#eso-configuration)
6. [Helm Chart Integration](#helm-chart-integration)
7. [ArgoCD Setup](#argocd-setup)
8. [Testing & Troubleshooting](#testing--troubleshooting)
9. [Best Practices](#best-practices)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Azure Key Vault                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ db-password  │  │   api-key    │  │  redis-pwd   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└────────────────────────┬────────────────────────────────┘
                         │ (1) ESO polls Key Vault
                         │     every X minutes
                         ▼
┌─────────────────────────────────────────────────────────┐
│              External Secrets Operator                   │
│  - Watches ExternalSecret resources                      │
│  - Authenticates via Workload Identity/SP                │
│  - Fetches secrets from Key Vault                        │
└────────────────────────┬────────────────────────────────┘
                         │ (2) Creates/Updates
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Kubernetes Secret                           │
│  apiVersion: v1                                          │
│  kind: Secret                                            │
│  data:                                                   │
│    DATABASE_PASSWORD: <base64>                           │
│    API_KEY: <base64>                                     │
└────────────────────────┬────────────────────────────────┘
                         │ (3) Mounted as env/volume
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Your Application Pod                        │
│  - Reads secrets as environment variables                │
│  - Or mounts as files                                    │
└─────────────────────────────────────────────────────────┘
```

**Key Components:**
- **SecretStore**: Defines connection to Azure Key Vault
- **ExternalSecret**: Defines which secrets to sync and how
- **Kubernetes Secret**: Created automatically by ESO

---

## Prerequisites

### Required
- AKS cluster (1.19+)
- Azure CLI installed
- `kubectl` configured
- Helm 3.x
- ArgoCD installed
- Azure Key Vault created

### Check Versions
```bash
az --version              # Azure CLI 2.40+
kubectl version --short   # 1.19+
helm version              # 3.0+
```

---

## Installation

### Step 1: Install External Secrets Operator

```bash
# Add Helm repository
helm repo add external-secrets https://charts.external-secrets.io
helm repo update

# Create namespace
kubectl create namespace external-secrets-system

# Install ESO
helm install external-secrets \
  external-secrets/external-secrets \
  -n external-secrets-system \
  --set installCRDs=true \
  --set webhook.port=9443

# Verify installation
kubectl get pods -n external-secrets-system
```

**Expected Output:**
```
NAME                                                READY   STATUS    RESTARTS   AGE
external-secrets-8d8c8c7d4-xxxxx                   1/1     Running   0          30s
external-secrets-cert-controller-69d8c7d4-xxxxx    1/1     Running   0          30s
external-secrets-webhook-7d8c8c7d4-xxxxx           1/1     Running   0          30s
```

### Step 2: Verify CRDs Installation

```bash
kubectl get crds | grep external-secrets
```

**Expected Output:**
```
clusterexternalsecrets.external-secrets.io
clustersecretstores.external-secrets.io
externalsecrets.external-secrets.io
secretstores.external-secrets.io
```

---

## Azure Setup

### Method A: Workload Identity (Recommended) ⭐

This is the **most secure** method - no secrets stored in Kubernetes!

#### 1. Enable Workload Identity on AKS

```bash
# Set variables
RESOURCE_GROUP="myResourceGroup"
CLUSTER_NAME="myAKSCluster"
LOCATION="eastus"
IDENTITY_NAME="eso-identity"
KEYVAULT_NAME="mykeyvault"

# Enable OIDC Issuer and Workload Identity
az aks update \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --enable-oidc-issuer \
  --enable-workload-identity

# Get OIDC Issuer URL
OIDC_ISSUER=$(az aks show \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --query "oidcIssuerProfile.issuerUrl" \
  -o tsv)

echo "OIDC Issuer: $OIDC_ISSUER"
```

#### 2. Create User-Assigned Managed Identity

```bash
# Create managed identity
az identity create \
  --name $IDENTITY_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION

# Get identity details
IDENTITY_CLIENT_ID=$(az identity show \
  --name $IDENTITY_NAME \
  --resource-group $RESOURCE_GROUP \
  --query clientId -o tsv)

IDENTITY_OBJECT_ID=$(az identity show \
  --name $IDENTITY_NAME \
  --resource-group $RESOURCE_GROUP \
  --query principalId -o tsv)

echo "Client ID: $IDENTITY_CLIENT_ID"
echo "Object ID: $IDENTITY_OBJECT_ID"
```

#### 3. Grant Key Vault Access

```bash
# Give identity permission to read secrets from Key Vault
az keyvault set-policy \
  --name $KEYVAULT_NAME \
  --object-id $IDENTITY_OBJECT_ID \
  --secret-permissions get list

# Verify access
az keyvault show \
  --name $KEYVAULT_NAME \
  --query "properties.accessPolicies[?objectId=='$IDENTITY_OBJECT_ID']"
```

#### 4. Create Federated Identity Credential

```bash
# For each namespace where you'll use ESO, create a federated credential
NAMESPACE="myapp-dev"
SERVICE_ACCOUNT="external-secrets-sa"

az identity federated-credential create \
  --name "${NAMESPACE}-federated-credential" \
  --identity-name $IDENTITY_NAME \
  --resource-group $RESOURCE_GROUP \
  --issuer $OIDC_ISSUER \
  --subject "system:serviceaccount:${NAMESPACE}:${SERVICE_ACCOUNT}" \
  --audience api://AzureADTokenExchange

# Repeat for each environment
# For prod:
az identity federated-credential create \
  --name "myapp-prod-federated-credential" \
  --identity-name $IDENTITY_NAME \
  --resource-group $RESOURCE_GROUP \
  --issuer $OIDC_ISSUER \
  --subject "system:serviceaccount:myapp-prod:external-secrets-sa" \
  --audience api://AzureADTokenExchange
```

#### 5. Create Service Account in Each Namespace

```bash
# This will be done via Helm chart (shown later)
# But here's the manual way for reference:

cat <<EOF | kubectl apply -f -
apiVersion: v1
kind: ServiceAccount
metadata:
  name: external-secrets-sa
  namespace: myapp-dev
  annotations:
    azure.workload.identity/client-id: $IDENTITY_CLIENT_ID
  labels:
    azure.workload.identity/use: "true"
EOF
```

---

### Method B: Service Principal (Alternative)

Use this if you can't use Workload Identity.

#### 1. Create Service Principal

```bash
# Create service principal
SP_NAME="eso-service-principal"
SP_OUTPUT=$(az ad sp create-for-rbac --name $SP_NAME)

# Extract values
CLIENT_ID=$(echo $SP_OUTPUT | jq -r '.appId')
CLIENT_SECRET=$(echo $SP_OUTPUT | jq -r '.password')
TENANT_ID=$(echo $SP_OUTPUT | jq -r '.tenant')

# Save these securely!
echo "Client ID: $CLIENT_ID"
echo "Client Secret: $CLIENT_SECRET"
echo "Tenant ID: $TENANT_ID"
```

#### 2. Grant Key Vault Access

```bash
# Give service principal access to Key Vault
az keyvault set-policy \
  --name $KEYVAULT_NAME \
  --spn $CLIENT_ID \
  --secret-permissions get list
```

#### 3. Create Kubernetes Secret

```bash
# Store credentials in Kubernetes (in each namespace)
kubectl create secret generic azure-secret-sp \
  --from-literal=clientid=$CLIENT_ID \
  --from-literal=clientsecret=$CLIENT_SECRET \
  -n myapp-dev

# For prod
kubectl create secret generic azure-secret-sp \
  --from-literal=clientid=$CLIENT_ID \
  --from-literal=clientsecret=$CLIENT_SECRET \
  -n myapp-prod
```

---

## ESO Configuration

### Helm Chart Structure

```
helm-charts/
└── myapp/
    ├── Chart.yaml
    ├── templates/
    │   ├── serviceaccount.yaml          # NEW
    │   ├── secretstore.yaml             # NEW
    │   ├── externalsecret.yaml          # NEW
    │   ├── deployment.yaml
    │   ├── service.yaml
    │   └── _helpers.tpl
    └── values/
        ├── values-dev.yaml
        ├── values-test.yaml
        ├── values-staging.yaml
        ├── values-preprod.yaml
        └── values-prod.yaml
```

---

### Chart.yaml

```yaml
apiVersion: v2
name: myapp
description: My Application with External Secrets
type: application
version: 1.0.0
appVersion: "1.0.0"
```

---

### templates/serviceaccount.yaml

```yaml
{{- if .Values.externalSecrets.enabled }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .Values.externalSecrets.serviceAccount.name }}
  namespace: {{ .Values.namespace }}
  {{- if eq .Values.externalSecrets.authType "WorkloadIdentity" }}
  annotations:
    azure.workload.identity/client-id: {{ .Values.azure.identityClientId }}
  labels:
    azure.workload.identity/use: "true"
  {{- end }}
{{- end }}
```

---

### templates/secretstore.yaml

```yaml
{{- if .Values.externalSecrets.enabled }}
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: {{ .Values.externalSecrets.secretStore.name }}
  namespace: {{ .Values.namespace }}
spec:
  provider:
    azurekv:
      authType: {{ .Values.externalSecrets.authType }}
      vaultUrl: {{ .Values.azure.keyVault.vaultUrl }}
      
      {{- if eq .Values.externalSecrets.authType "WorkloadIdentity" }}
      # Workload Identity authentication
      serviceAccountRef:
        name: {{ .Values.externalSecrets.serviceAccount.name }}
      {{- else if eq .Values.externalSecrets.authType "ServicePrincipal" }}
      # Service Principal authentication
      tenantId: {{ .Values.azure.tenantId }}
      authSecretRef:
        clientId:
          name: {{ .Values.externalSecrets.authSecretName }}
          key: clientid
        clientSecret:
          name: {{ .Values.externalSecrets.authSecretName }}
          key: clientsecret
      {{- end }}
{{- end }}
```

---

### templates/externalsecret.yaml

```yaml
{{- if .Values.externalSecrets.enabled }}
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: {{ include "myapp.fullname" . }}-secrets
  namespace: {{ .Values.namespace }}
  labels:
    {{- include "myapp.labels" . | nindent 4 }}
spec:
  refreshInterval: {{ .Values.externalSecrets.refreshInterval }}
  
  secretStoreRef:
    name: {{ .Values.externalSecrets.secretStore.name }}
    kind: SecretStore
  
  target:
    name: {{ include "myapp.fullname" . }}-secrets
    creationPolicy: Owner
    template:
      type: Opaque
      metadata:
        labels:
          {{- include "myapp.labels" . | nindent 10 }}
  
  {{- if .Values.externalSecrets.dataFrom }}
  # Option 1: Import all secrets from a Key Vault secret (JSON format)
  dataFrom:
    {{- range .Values.externalSecrets.dataFrom }}
    - extract:
        key: {{ .key }}
    {{- end }}
  {{- end }}
  
  {{- if .Values.externalSecrets.data }}
  # Option 2: Map individual secrets
  data:
    {{- range .Values.externalSecrets.data }}
    - secretKey: {{ .secretKey }}
      remoteRef:
        key: {{ .remoteKey }}
        {{- if .property }}
        property: {{ .property }}
        {{- end }}
    {{- end }}
  {{- end }}
{{- end }}
```

---

### templates/deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "myapp.fullname" . }}
  namespace: {{ .Values.namespace }}
  labels:
    {{- include "myapp.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      {{- include "myapp.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "myapp.selectorLabels" . | nindent 8 }}
        {{- if eq .Values.externalSecrets.authType "WorkloadIdentity" }}
        azure.workload.identity/use: "true"
        {{- end }}
    spec:
      {{- if .Values.externalSecrets.enabled }}
      serviceAccountName: {{ .Values.externalSecrets.serviceAccount.name }}
      {{- end }}
      
      containers:
      - name: {{ .Chart.Name }}
        image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
        imagePullPolicy: {{ .Values.image.pullPolicy }}
        
        ports:
        - name: http
          containerPort: {{ .Values.service.targetPort }}
          protocol: TCP
        
        {{- if .Values.externalSecrets.enabled }}
        # Option 1: Load all secrets as environment variables
        envFrom:
        - secretRef:
            name: {{ include "myapp.fullname" . }}-secrets
        {{- end }}
        
        # Option 2: Load specific secrets as env vars
        # env:
        # - name: DATABASE_PASSWORD
        #   valueFrom:
        #     secretKeyRef:
        #       name: {{ include "myapp.fullname" . }}-secrets
        #       key: DATABASE_PASSWORD
        # - name: API_KEY
        #   valueFrom:
        #     secretKeyRef:
        #       name: {{ include "myapp.fullname" . }}-secrets
        #       key: API_KEY
        
        resources:
          {{- toYaml .Values.resources | nindent 10 }}
```

---

### templates/_helpers.tpl

```yaml
{{/*
Expand the name of the chart.
*/}}
{{- define "myapp.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "myapp.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "myapp.labels" -}}
helm.sh/chart: {{ include "myapp.chart" . }}
{{ include "myapp.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "myapp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "myapp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "myapp.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}
```

---

## Helm Chart Integration

### values/values-dev.yaml

```yaml
# Application settings
namespace: myapp-dev
replicaCount: 2

image:
  repository: myregistry.azurecr.io/myapp
  tag: "1.0.0"
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 80
  targetPort: 8080

resources:
  limits:
    cpu: 500m
    memory: 512Mi
  requests:
    cpu: 250m
    memory: 256Mi

# External Secrets Configuration
externalSecrets:
  enabled: true
  authType: WorkloadIdentity  # or ServicePrincipal
  refreshInterval: 1h
  
  serviceAccount:
    name: external-secrets-sa
  
  secretStore:
    name: azure-keyvault-store
  
  # Azure Configuration
  azure:
    keyVault:
      name: mykeyvault-dev
      vaultUrl: https://mykeyvault-dev.vault.azure.net
    identityClientId: "your-managed-identity-client-id"
    # tenantId: "your-tenant-id"  # Only for Service Principal
  
  # For Service Principal auth
  # authSecretName: azure-secret-sp
  
  # Option 1: Import entire JSON secret from Key Vault
  dataFrom:
    - key: myapp-dev-secrets  # Name of secret in Key Vault containing JSON
  
  # Option 2: Map individual secrets
  # data:
  #   - secretKey: DATABASE_PASSWORD
  #     remoteKey: myapp-dev-db-password
  #   - secretKey: API_KEY
  #     remoteKey: myapp-dev-api-key
  #   - secretKey: REDIS_PASSWORD
  #     remoteKey: myapp-dev-redis-pwd
  #   - secretKey: JWT_SECRET
  #     remoteKey: myapp-dev-jwt-secret
```

### values/values-prod.yaml

```yaml
namespace: myapp-prod
replicaCount: 5

image:
  repository: myregistry.azurecr.io/myapp
  tag: "1.0.0"
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 80
  targetPort: 8080

resources:
  limits:
    cpu: 2000m
    memory: 2Gi
  requests:
    cpu: 1000m
    memory: 1Gi

externalSecrets:
  enabled: true
  authType: WorkloadIdentity
  refreshInterval: 15m  # More frequent refresh in prod
  
  serviceAccount:
    name: external-secrets-sa
  
  secretStore:
    name: azure-keyvault-store
  
  azure:
    keyVault:
      name: mykeyvault-prod
      vaultUrl: https://mykeyvault-prod.vault.azure.net
    identityClientId: "your-managed-identity-client-id"
  
  dataFrom:
    - key: myapp-prod-secrets
```

---

## Azure Key Vault Secret Format

### Option 1: Single JSON Secret (Recommended)

Store all secrets as a JSON object in one Key Vault secret:

```bash
# Create JSON secret in Key Vault
az keyvault secret set \
  --vault-name mykeyvault-dev \
  --name myapp-dev-secrets \
  --value '{
    "DATABASE_PASSWORD": "MySecurePassword123!",
    "API_KEY": "sk-1234567890abcdef",
    "REDIS_PASSWORD": "RedisPass456!",
    "JWT_SECRET": "super-secret-jwt-key-xyz"
  }'
```

### Option 2: Individual Secrets

```bash
# Create individual secrets
az keyvault secret set \
  --vault-name mykeyvault-dev \
  --name myapp-dev-db-password \
  --value "MySecurePassword123!"

az keyvault secret set \
  --vault-name mykeyvault-dev \
  --name myapp-dev-api-key \
  --value "sk-1234567890abcdef"

az keyvault secret set \
  --vault-name mykeyvault-dev \
  --name myapp-dev-redis-pwd \
  --value "RedisPass456!"
```

---

## ArgoCD Setup

### ArgoCD Application - Dev Environment

```yaml
# argocd/myapp-dev.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: myapp-dev
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  
  source:
    repoURL: https://github.com/myorg/helm-charts.git
    targetRevision: main
    path: charts/myapp
    helm:
      valueFiles:
        - values/values-dev.yaml
  
  destination:
    server: https://kubernetes.default.svc
    namespace: myapp-dev
  
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
      allowEmpty: false
    syncOptions:
      - CreateNamespace=true
    retry:
      limit: 5
      backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m
```

### ArgoCD Application - Prod Environment

```yaml
# argocd/myapp-prod.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: myapp-prod
  namespace: argocd
spec:
  project: default
  
  source:
    repoURL: https://github.com/myorg/helm-charts.git
    targetRevision: main
    path: charts/myapp
    helm:
      valueFiles:
        - values/values-prod.yaml
  
  destination:
    server: https://kubernetes.default.svc
    namespace: myapp-prod
  
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

### Deploy ArgoCD Applications

```bash
# Apply all environments
kubectl apply -f argocd/myapp-dev.yaml
kubectl apply -f argocd/myapp-test.yaml
kubectl apply -f argocd/myapp-staging.yaml
kubectl apply -f argocd/myapp-preprod.yaml
kubectl apply -f argocd/myapp-prod.yaml

# Check status
argocd app list
argocd app get myapp-dev
```

---

## Testing & Troubleshooting

### 1. Verify ESO Installation

```bash
# Check ESO pods
kubectl get pods -n external-secrets-system

# Check ESO logs
kubectl logs -n external-secrets-system -l app.kubernetes.io/name=external-secrets
```

### 2. Verify SecretStore

```bash
# Check SecretStore status
kubectl get secretstore -n myapp-dev
kubectl describe secretstore azure-keyvault-store -n myapp-dev

# Look for "Valid" status
# Status should show: 
#   Conditions:
#     Ready: True
```

### 3. Verify ExternalSecret

```bash
# Check ExternalSecret
kubectl get externalsecret -n myapp-dev
kubectl describe externalsecret myapp-secrets -n myapp-dev

# Check status
kubectl get externalsecret myapp-secrets -n myapp-dev -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}'
# Should output: True
```

### 4. Verify Kubernetes Secret Created

```bash
# Check if K8s secret was created
kubectl get secret myapp-secrets -n myapp-dev

# View secret keys (not values)
kubectl get secret myapp-secrets -n myapp-dev -o jsonpath='{.data}' | jq 'keys'

# Decode a secret value (for testing)
kubectl get secret myapp-secrets -n myapp-dev -o jsonpath='{.data.DATABASE_PASSWORD}' | base64 -d
```

### 5. Test in Pod

```bash
# Create test pod
kubectl run test-pod \
  --image=busybox \
  --restart=Never \
  -n myapp-dev \
  --command -- sleep 3600

# Mount secret as env vars
kubectl set env pod/test-pod \
  --from=secret/myapp-secrets \
  -n myapp-dev

# Check env vars
kubectl exec -it test-pod -n myapp-dev -- env | grep -E 'DATABASE|API|REDIS'

# Cleanup
kubectl delete pod test-pod -n myapp-dev
```

### 6. Check Application Logs

```bash
# Check if your app can read secrets
kubectl logs -f deployment/myapp -n myapp-dev

# Check for authentication errors
kubectl logs -n external-secrets-system -l app.kubernetes.io/name=external-secrets | grep -i error
```

---

## Common Issues & Solutions

### Issue 1: SecretStore Not Ready

```bash
# Check SecretStore
kubectl describe secretstore azure-keyvault-store -n myapp-dev
```

**Error:** `could not get Azure authorizer`

**Solution:**
```bash
# Verify Workload Identity is configured
kubectl get sa external-secrets-sa -n myapp-dev -o yaml | grep azure.workload.identity

# Check federated credential exists
az identity federated-credential list \
  --identity-name eso-identity \
  --resource-group myResourceGroup
```

---

### Issue 2: ExternalSecret Not Syncing

**Error:** `secret not found in Azure Key Vault`

**Solution:**
```bash
# List secrets in Key Vault
az keyvault secret list --vault-name mykeyvault-dev

# Check if secret exists
az keyvault secret show \
  --vault-name mykeyvault-dev \
  --name myapp-dev-secrets

# Verify Key Vault URL is correct
kubectl get secretstore azure-keyvault-store -n myapp-dev -o yaml | grep vaultUrl
```

---

### Issue 3: Permission Denied

**Error:** `Caller is not authorized to perform action`

**Solution:**
```bash
# Verify managed identity has Key Vault access
IDENTITY_OBJECT_ID=$(az identity show \
  --name eso-identity \
  --resource-group myResourceGroup \
  --query principalId -o tsv)

# Check access policies
az keyvault show \
  --name mykeyvault-dev \
  --query "properties.accessPolicies[?objectId=='$IDENTITY_OBJECT_ID']"

# If empty, grant access again
az keyvault set-policy \
  --name mykeyvault-dev \
  --object-id $IDENTITY_OBJECT_ID \
  --secret-permissions get list
```

---

### Issue 4: Secrets Not Updating

```bash
# Force refresh
kubectl annotate externalsecret myapp-secrets \
  -n myapp-dev \
  force-sync=$(date +%s) \
  --overwrite

# Check refresh interval
kubectl get externalsecret myapp-secrets -n myapp-dev -o jsonpath='{.spec.refreshInterval}'

# Watch for updates
kubectl get secret myapp-secrets -n myapp-dev -w
```

---

## Best Practices

### 1. Secret Organization

```bash
# Use environment prefixes
myapp-dev-secrets
myapp-test-secrets
myapp-staging-secrets
myapp-prod-secrets

# Or use separate Key Vaults per environment (Recommended)
mykeyvault-dev
mykeyvault-test
mykeyvault-staging
mykeyvault-prod
```

### 2. Refresh Intervals

```yaml
# Development: Longer intervals (less cost)
refreshInterval: 1h

# Production: Shorter intervals (faster rotation)
refreshInterval: 15m

# For critical secrets: Very short
refreshInterval: 5m
```

### 3. RBAC for Key Vault

```bash
# Principle of least privilege
# Only grant 'get' and 'list' permissions, not 'set' or 'delete'

az keyvault set-policy \
  --name mykeyvault-prod \
  --object-id $IDENTITY_OBJECT_ID \
  --secret-permissions get list
  # NOT: --secret-permissions all
```

### 4. Monitoring

```yaml
# Add monitoring for ExternalSecret status
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-rules
data:
  external-secrets.yaml: |
    groups:
    - name: external-secrets
      rules:
      - alert: ExternalSecretNotReady
        expr: externalsecret_status_condition{condition="Ready",status="False"} == 1
        for: 5m
        annotations:
          summary: "ExternalSecret {{ $labels.name }} not ready"
```

### 5. Secret Rotation

```bash
# Update secret in Key Vault
az keyvault secret set \
  --vault-name mykeyvault-prod \
  --name myapp-prod-db-password \
  --value "NewPassword123!"

# ESO will automatically sync within refreshInterval
# No pod restart needed unless app caches the secret

# For immediate update:
kubectl rollout restart deployment/myapp -n myapp-prod
```

### 6. Multi-Environment Setup Script

```bash
#!/bin/bash
# setup-eso-environments.sh

ENVIRONMENTS=("dev" "test" "staging" "preprod" "prod")
RESOURCE_GROUP="myResourceGroup"
IDENTITY_NAME="eso-identity"
CLUSTER_NAME="myAKSCluster"

OIDC_ISSUER=$(az aks show \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --query "oidcIssuerProfile.issuerUrl" -o tsv)

for ENV in "${ENVIRONMENTS[@]}"; do
  NAMESPACE="myapp-${ENV}"
  
  echo "Setting up ${ENV} environment..."
  
  # Create federated credential
  az identity federated-credential create \
    --name "${NAMESPACE}-federated-credential" \
    --identity-name $IDENTITY_NAME \
    --resource-group $RESOURCE_GROUP \
    --issuer $OIDC_ISSUER \
    --subject "system:serviceaccount:${NAMESPACE}:external-secrets-sa" \
    --audience api://AzureADTokenExchange
  
  echo "✓ ${ENV} environment configured"
done

echo "All environments configured!"
```

---

## Complete Example Flow

### 1. Developer Updates Code

```bash
git commit -m "Update app to v1.1.0"
git push origin main

# CI/CD builds and pushes image
docker build -t myregistry.azurecr.io/myapp:1.1.0 .
docker push myregistry.azurecr.io/myapp:1.1.0
```

### 2. Update Helm Values

```bash
# Update values/values-dev.yaml
image:
  tag: "1.1.0"

git commit -m "Deploy v1.1.0 to dev"
git push origin main
```

### 3. ArgoCD Syncs

```
ArgoCD detects change → Syncs Helm chart → Deploys:
  ✓ ServiceAccount (with Workload Identity)
  ✓ SecretStore (connects to Key Vault)
  ✓ ExternalSecret (defines what to sync)
  ✓ Deployment (uses synced secrets)
```

### 4. ESO Syncs Secrets

```
ESO sees ExternalSecret → Authenticates to Key Vault →
Fetches secrets → Creates Kubernetes Secret →
Pod starts with environment variables
```

### 5. Secret Rotation

```bash
# Update secret in Key Vault
az keyvault secret set \
  --vault-name mykeyvault-dev \
  --name myapp-dev-secrets \
  --value '{"DATABASE_PASSWORD": "NewPassword456!"}'

# Within refreshInterval (e.g., 1h), ESO automatically:
# 1. Fetches new secret from Key Vault
# 2. Updates Kubernetes Secret
# 3. App reads new value on next restart or if watching secret
```

---

## Summary

You now have:
- ✅ External Secrets Operator installed
- ✅ Workload Identity configured for secure authentication
- ✅ SecretStore connecting to Azure Key Vault
- ✅ ExternalSecrets syncing secrets automatically
- ✅ Helm charts managing everything
- ✅ ArgoCD deploying across all 5 environments
- ✅ **Zero secrets in Git!**

**Benefits:**
- 🔒 Secrets stored securely in Azure Key Vault
- 🔄 Automatic secret rotation
- 🌍 Multi-environment support
- 📝 GitOps workflow with ArgoCD
- 🚫 No secrets in source code

---

## Quick Reference Commands

```bash
# Check ESO status
kubectl get pods -n external-secrets-system
kubectl get secretstore --all-namespaces
kubectl get externalsecret --all-namespaces

# Debug SecretStore
kubectl describe secretstore <name> -n <namespace>

# Debug ExternalSecret
kubectl describe externalsecret <name> -n <namespace>

# Check generated secrets
kubectl get secret <name> -n <namespace>
kubectl get secret <name> -n <namespace> -o yaml

# View ESO logs
kubectl logs -n external-secrets-system -l app.kubernetes.io/name=external-secrets --tail=100 -f

# Force secret refresh
kubectl annotate externalsecret <name> -n <namespace> force-sync=$(date +%s) --overwrite

# Test Key Vault connectivity
az keyvault secret list --vault-name <vault-name>
az keyvault secret show --vault-name <vault-name> --name <secret-name>
```

---

## Additional Resources

- [External Secrets Operator Documentation](https://external-secrets.io/)
- [Azure Key Vault Documentation](https://learn.microsoft.com/en-us/azure/key-vault/)
- [AKS Workload Identity Documentation](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview)
- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)

---

**Created:** 2025
**Last Updated:** 2025
**Version:** 1.0
