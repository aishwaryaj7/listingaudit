using 'main.bicep'

// deploy.sh sets these from Azure, and the language model and Langfuse settings from .env
param deployerId = readEnvironmentVariable('DEPLOYER_ID')
param deployerIp = readEnvironmentVariable('DEPLOYER_IP')
param deployApps = bool(readEnvironmentVariable('DEPLOY_APPS', 'true'))
param image = readEnvironmentVariable('IMAGE', '')
param minReplicas = int(readEnvironmentVariable('MIN_REPLICAS', '0'))

param visionBaseUrl = readEnvironmentVariable('VISION_BASE_URL', '')
param visionModel = readEnvironmentVariable('VISION_MODEL', 'gpt-5-mini')
param agentModel = readEnvironmentVariable('AGENT_MODEL', 'gpt-5-mini')
param agentModelLarge = readEnvironmentVariable('AGENT_MODEL_LARGE', '')
param langfusePublicKey = readEnvironmentVariable('LANGFUSE_PUBLIC_KEY', '')
param langfuseBaseUrl = readEnvironmentVariable('LANGFUSE_BASE_URL', 'https://cloud.langfuse.com')

param postgresPassword = readEnvironmentVariable('AZURE_POSTGRES_PASSWORD')
param agentDbPassword = readEnvironmentVariable('AZURE_AGENT_DB_PASSWORD')
param screenPassword = readEnvironmentVariable('AZURE_SCREEN_PASSWORD')
param visionApiKey = readEnvironmentVariable('VISION_API_KEY')
param langfuseSecretKey = readEnvironmentVariable('LANGFUSE_SECRET_KEY', '')
