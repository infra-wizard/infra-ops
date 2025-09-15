# 1. Create Grafana Cloud credentials secret
kubectl create secret generic grafana-cloud-credentials \
  --from-literal=username=your-grafana-username \
  --from-literal=api-key=your-grafana-api-key \
  --namespace=monitoring

# 2. Deploy the monitoring stack
kubectl apply -f k8s-manifests/grafana-alloy-specific-app.yaml

# 3. Verify deployment
kubectl get pods -n monitoring
kubectl logs -f deployment/grafana-alloy -n monitoring
