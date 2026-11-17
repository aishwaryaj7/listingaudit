#!/usr/bin/env bash
# Copy the marketplace and the rulebook from the local database to the one in Azure, give
# the agent its read-only login there, and upload the listing photos to Blob Storage. Run
# it from the project folder after deploy.sh, with the local database running:
#
#   ./deploy/load_data.sh
#
# Running it again replaces the data. The answer key stays on this computer: the app in
# Azure never needs it.
set -euo pipefail
cd "$(dirname "$0")/.."

RESOURCE_GROUP=${RESOURCE_GROUP:-listingaudit}

output() {
    az deployment group show --resource-group "$RESOURCE_GROUP" --name listingaudit \
        --query "properties.outputs.$1.value" --output tsv
}

if ! docker exec listingaudit-db pg_isready --quiet; then
    echo "Start the local database first: docker compose up -d db" >&2
    exit 1
fi

host=$(output postgresHost)
user=$(output postgresUser)
database=$(output database)
vault=$(output vault)
storage=$(output storageAccount)
PGPASSWORD=$(az keyvault secret show --vault-name "$vault" --name postgres-password \
    --query value --output tsv)
AGENT_PASSWORD=$(az keyvault secret show --vault-name "$vault" --name agent-db-password \
    --query value --output tsv)
export PGPASSWORD AGENT_PASSWORD

# psql and pg_dump from the local database's container, so nothing else needs installing
azure() {
    docker exec -i -e PGPASSWORD -e AGENT_PASSWORD -e PGOPTIONS="-c client_min_messages=warning" \
        listingaudit-db psql --quiet --set ON_ERROR_STOP=1 \
        "host=$host user=$user dbname=$database sslmode=require" "$@"
}

echo "Copying the marketplace and the rulebook to $host"
azure --command "CREATE EXTENSION IF NOT EXISTS vector"
docker exec listingaudit-db sh -c 'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --schema market --schema rulebook --no-owner --no-privileges --clean --if-exists' \
    | azure >/dev/null

echo "Giving the agent its read-only login"
azure <<'SQL'
\getenv agent_password AGENT_PASSWORD
SELECT 'CREATE ROLE listingaudit_agent'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'listingaudit_agent') \gexec
ALTER ROLE listingaudit_agent LOGIN PASSWORD :'agent_password';
GRANT USAGE ON SCHEMA market, rulebook TO listingaudit_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA market, rulebook TO listingaudit_agent;
SQL

echo "Uploading the listing photos to $storage"
az storage blob upload-batch --account-name "$storage" --auth-mode login \
    --destination photos --destination-path images --source data/images --overwrite \
    --output none

echo "Done: the review screen can audit listings now."
