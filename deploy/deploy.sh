#!/usr/bin/env bash
# Deploy ListingAudit to Azure: main.bicep without the apps, then the image into the
# registry, then main.bicep with the apps. Run it from the project folder after the quick
# start, with Docker running and signed in with az login:
#
#   ./deploy/deploy.sh
#
# The first run takes 15 to 30 minutes; ./deploy/load_data.sh then copies the data. Run it
# again to deploy new code: the passwords stay the ones kept in Key Vault. RESOURCE_GROUP
# and LOCATION choose where it goes.
set -euo pipefail
cd "$(dirname "$0")/.."

RESOURCE_GROUP=${RESOURCE_GROUP:-listingaudit}
LOCATION=${LOCATION:-germanywestcentral}

if [ ! -f .env ] || [ ! -d data/models ]; then
    echo "Run the quick start first (see README): .env or data/models is missing." >&2
    exit 1
fi
# The language model and Langfuse settings, the same as on this computer
set -a
. ./.env
set +a
: "${VISION_API_KEY:?is missing from .env: the agent in Azure needs a language model}"

output() {
    az deployment group show --resource-group "$RESOURCE_GROUP" --name listingaudit \
        --query "properties.outputs.$1.value" --output tsv
}

deploy() {
    DEPLOY_APPS=$1 IMAGE=${2:-} az deployment group create --resource-group "$RESOURCE_GROUP" \
        --name listingaudit --template-file deploy/main.bicep \
        --parameters deploy/main.bicepparam --output none
}

echo "Resource group $RESOURCE_GROUP in $LOCATION"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --tags project=listingaudit \
    --output none

# The passwords kept in Key Vault, or new random ones on the first run. Azure wants three
# kinds of characters in the database's password; hex has two, so a capital leads.
vault=$(az keyvault list --resource-group "$RESOURCE_GROUP" --query "[0].name" --output tsv)
password() {
    if [ -n "$vault" ]; then
        az keyvault secret show --vault-name "$vault" --name "$1" --query value --output tsv
    else
        echo "L$(openssl rand -hex "$2")"
    fi
}
AZURE_POSTGRES_PASSWORD=$(password postgres-password 24)
AZURE_AGENT_DB_PASSWORD=$(password agent-db-password 24)
AZURE_SCREEN_PASSWORD=$(password screen-password 12)
# The person deploying may upload photos and read the secrets, and their address may
# reach the database to load the data
DEPLOYER_ID=$(az ad signed-in-user show --query id --output tsv)
DEPLOYER_IP=$(curl -fsS https://api.ipify.org)
export AZURE_POSTGRES_PASSWORD AZURE_AGENT_DB_PASSWORD AZURE_SCREEN_PASSWORD DEPLOYER_ID DEPLOYER_IP

echo "1/3 Database, registry, Key Vault, storage and the apps' environment"
deploy false

registry=$(output registry)
image="$registry/listingaudit:$(date -u +%Y%m%d-%H%M%S)"
echo "2/3 The image $image"
az acr login --name "${registry%%.*}"
docker buildx build --platform linux/amd64 --tag "$image" --push .

echo "3/3 The review API and the review screen"
deploy true "$image"

vault=$(output vault)
echo
echo "The review screen: $(output screenUrl)"
echo "Its password:      az keyvault secret show --vault-name $vault --name screen-password --query value --output tsv"
echo "After the first deployment, copy the data: ./deploy/load_data.sh"
