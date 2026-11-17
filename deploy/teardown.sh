#!/usr/bin/env bash
# Delete everything deploy.sh created, so that nothing costs money any more:
#
#   ./deploy/teardown.sh
#
# Key Vault keeps a deleted vault for 7 days and its name with it, so the vault is purged
# too, and a new deployment can use the name again.
set -euo pipefail

RESOURCE_GROUP=${RESOURCE_GROUP:-listingaudit}

vault=$(az keyvault list --resource-group "$RESOURCE_GROUP" --query "[0].name" --output tsv)
echo "Deleting the resource group $RESOURCE_GROUP (this takes a few minutes)"
az group delete --name "$RESOURCE_GROUP" --yes
if [ -n "$vault" ]; then
    echo "Purging the Key Vault $vault"
    az keyvault purge --name "$vault"
fi
