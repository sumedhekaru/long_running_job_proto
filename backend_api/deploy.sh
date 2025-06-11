#!/bin/bash
# Edit these values for your project
PROJECT_ID="your-gcp-project-id"
REGION="us-central1"
SERVICE_NAME="backend-api"

set -e

IMAGE=gcr.io/$PROJECT_ID/$SERVICE_NAME

# Build the Docker image and push to Google Container Registry
gcloud builds submit --tag $IMAGE

# Deploy to Cloud Run
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated

echo "Deployment complete!"
