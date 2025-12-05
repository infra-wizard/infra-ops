az keyvault secret show \
  --vault-name $KEYVAULT_NAME \
  --name $CERT_SECRET_NAME \
  --query value -o tsv > tls.crt

# Get private key from Key Vault
az keyvault secret show \
  --vault-name $KEYVAULT_NAME \
  --name $KEY_SECRET_NAME \
  --query value -o tsv > tls.key
