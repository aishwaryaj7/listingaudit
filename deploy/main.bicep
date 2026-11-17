// ListingAudit on Azure: the review API and the review screen on Container Apps, with
// PostgreSQL for the data, Blob Storage for the photos, Key Vault for the secrets and a
// registry for the image. deploy.sh runs it twice: first without the apps, then with
// them once the image is in the registry.

@description('Azure region for every resource.')
param location string = resourceGroup().location

@description('Object id of the person deploying, who may upload photos and read the secrets.')
param deployerId string

@description('Public IP address of the person deploying, let through the database firewall.')
param deployerIp string

@description('Deploy the container apps too; false on the first run, before the image exists.')
param deployApps bool = true

@description('The container image, such as <registry>.azurecr.io/listingaudit:<tag>.')
param image string = ''

@description('Replicas kept running; 0 lets the apps sleep when nobody uses them.')
@minValue(0)
@maxValue(1)
param minReplicas int = 0

@description('The PostgreSQL version of the local database, so that its dump loads as it is.')
@allowed(['16', '17'])
param postgresVersion string = '17'

@description('Azure OpenAI endpoint, https://<resource>.openai.azure.com/openai/v1/, or empty for the OpenAI API.')
param visionBaseUrl string = ''
param visionModel string = 'gpt-5-mini'
param agentModel string = 'gpt-5-mini'
param agentModelLarge string = ''

@description('Langfuse public key; without it and the secret key, nothing is traced.')
param langfusePublicKey string = ''
param langfuseBaseUrl string = 'https://cloud.langfuse.com'

@secure()
param postgresPassword string
@secure()
param agentDbPassword string
@secure()
param screenPassword string
@secure()
param visionApiKey string
@secure()
param langfuseSecretKey string = ''

var suffix = uniqueString(resourceGroup().id)
var tags = { project: 'listingaudit' }
var database = 'listingaudit'
var databaseAdmin = 'listingaudit'
var apiName = 'listingaudit-api'
var screenName = 'listingaudit-screen'
var tracing = !empty(langfusePublicKey) && !empty(langfuseSecretKey)

// Built-in roles
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var keyVaultSecretsOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
var storageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

// --- Shared pieces -----------------------------------------------------------------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'listingaudit-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// The apps' identity: it pulls the image, reads the secrets and reads the photos
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'listingaudit-app'
  location: location
  tags: tags
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'listingaudit${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource identityPullsImages 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// --- Secrets -----------------------------------------------------------------------

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'listingaudit-${take(suffix, 11)}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    accessPolicies: []
    softDeleteRetentionInDays: 7
  }
}

var secrets = [
  { name: 'postgres-password', value: postgresPassword }
  { name: 'agent-db-password', value: agentDbPassword }
  { name: 'screen-password', value: screenPassword }
  { name: 'vision-api-key', value: visionApiKey }
  { name: 'langfuse-secret-key', value: langfuseSecretKey }
]

resource vaultSecrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for secret in secrets: if (!empty(secret.value)) {
  parent: vault
  name: secret.name
  properties: { value: secret.value }
}]

resource identityReadsSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, keyVaultSecretsUser)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerManagesSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, deployerId, keyVaultSecretsOfficer)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficer)
    principalId: deployerId
    principalType: 'User'
  }
}

// --- Photos ------------------------------------------------------------------------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'listingaudit${take(suffix, 12)}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource photos 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'photos'
  properties: { publicAccess: 'None' }
}

resource identityReadsPhotos 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(photos.id, identity.id, storageBlobDataReader)
  scope: photos
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReader)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource deployerUploadsPhotos 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(photos.id, deployerId, storageBlobDataContributor)
  scope: photos
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    principalId: deployerId
    principalType: 'User'
  }
}

// --- Database ----------------------------------------------------------------------

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2025-08-01' = {
  name: 'listingaudit-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: postgresVersion
    administratorLogin: databaseAdmin
    administratorLoginPassword: postgresPassword
    storage: { storageSizeGB: 32 }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
  }
}

// The server takes one change at a time, so each step waits for the one before
resource pgvector 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2025-08-01' = {
  parent: postgres
  name: 'azure.extensions'
  properties: { value: 'VECTOR', source: 'user-override' }
}

resource listingauditDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2025-08-01' = {
  parent: postgres
  name: database
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
  dependsOn: [pgvector]
}

// Container Apps have no fixed outbound address, so the firewall lets in Azure's own
// addresses; the strong password and TLS keep others out
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2025-08-01' = {
  parent: postgres
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
  dependsOn: [listingauditDatabase]
}

resource allowDeployer 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2025-08-01' = {
  parent: postgres
  name: 'deployer'
  properties: { startIpAddress: deployerIp, endIpAddress: deployerIp }
  dependsOn: [allowAzure]
}

// --- Apps --------------------------------------------------------------------------

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'listingaudit-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

var vaultSecret = '${vault.properties.vaultUri}secrets/'
var registries = [{ server: registry.properties.loginServer, identity: identity.id }]

var baseSecrets = [
  { name: 'postgres-password', keyVaultUrl: '${vaultSecret}postgres-password', identity: identity.id }
  { name: 'agent-db-password', keyVaultUrl: '${vaultSecret}agent-db-password', identity: identity.id }
  { name: 'vision-api-key', keyVaultUrl: '${vaultSecret}vision-api-key', identity: identity.id }
]
var tracingSecrets = [
  { name: 'langfuse-secret-key', keyVaultUrl: '${vaultSecret}langfuse-secret-key', identity: identity.id }
]
var apiSecrets = concat(baseSecrets, tracing ? tracingSecrets : [])

var baseSettings = [
  { name: 'POSTGRES_HOST', value: postgres.properties.fullyQualifiedDomainName }
  { name: 'POSTGRES_USER', value: databaseAdmin }
  { name: 'POSTGRES_DB', value: database }
  { name: 'POSTGRES_PASSWORD', secretRef: 'postgres-password' }
  { name: 'AGENT_DB_PASSWORD', secretRef: 'agent-db-password' }
  { name: 'PGSSLMODE', value: 'require' }
  { name: 'VISION_API_KEY', secretRef: 'vision-api-key' }
  { name: 'VISION_MODEL', value: visionModel }
  { name: 'AGENT_MODEL', value: agentModel }
  { name: 'PHOTOS_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
  { name: 'PHOTOS_CONTAINER', value: photos.name }
  // The identity that DefaultAzureCredential signs in with
  { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
  { name: 'LANGFUSE_ENVIRONMENT', value: 'azure' }
]
var tracingSettings = [
  { name: 'LANGFUSE_PUBLIC_KEY', value: langfusePublicKey }
  { name: 'LANGFUSE_SECRET_KEY', secretRef: 'langfuse-secret-key' }
  { name: 'LANGFUSE_BASE_URL', value: langfuseBaseUrl }
]
var baseUrlSetting = empty(visionBaseUrl) ? [] : [{ name: 'VISION_BASE_URL', value: visionBaseUrl }]
var largeModelSetting = empty(agentModelLarge) ? [] : [{ name: 'AGENT_MODEL_LARGE', value: agentModelLarge }]
var apiSettings = concat(baseSettings, baseUrlSetting, largeModelSetting, tracing ? tracingSettings : [])

// The API is reachable only from inside the environment, where the screen calls it
resource api 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: apiName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 8000
        allowInsecure: true
      }
      registries: registries
      secrets: apiSecrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: { cpu: json('2.0'), memory: '4Gi' }
          env: apiSettings
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 10
              failureThreshold: 10
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: 1 }
    }
  }
  dependsOn: [identityPullsImages, identityReadsSecrets, identityReadsPhotos, vaultSecrets]
}

// The screen is the only public address, and it asks for the screen password
resource screen 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: screenName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8501
      }
      registries: registries
      secrets: [{ name: 'screen-password', keyVaultUrl: '${vaultSecret}screen-password', identity: identity.id }]
    }
    template: {
      containers: [
        {
          name: 'screen'
          image: image
          command: ['streamlit']
          args: ['run', 'src/listingaudit/review/screen.py', '--server.address', '0.0.0.0']
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'API_URL', value: 'http://${apiName}' }
            { name: 'SCREEN_PASSWORD', secretRef: 'screen-password' }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/_stcore/health', port: 8501 }
              initialDelaySeconds: 5
              periodSeconds: 5
              failureThreshold: 10
            }
          ]
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: 1 }
    }
  }
  dependsOn: [api, identityPullsImages, identityReadsSecrets, vaultSecrets]
}

output registry string = registry.properties.loginServer
output vault string = vault.name
output storageAccount string = storage.name
output postgresHost string = postgres.properties.fullyQualifiedDomainName
output postgresUser string = databaseAdmin
output database string = database
output screenUrl string = deployApps ? 'https://${screen.properties.configuration.ingress.fqdn}' : ''
