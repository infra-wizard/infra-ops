kubectl get pods -n argo-cd -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{range .spec.containers[*]}  {.image}{"\n"}{end}{range .spec.initContainers[*]}  [init] {.image}{"\n"}{end}{"\n"}{end}'
