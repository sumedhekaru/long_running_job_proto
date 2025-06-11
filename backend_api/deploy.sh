#!/bin/bash
# Edit these values for your project
PROJECT_ID="long-running-forecast"
REGION="us-east4"
REPOSITORY="cloud-run-source-deploy"
IMAGE_NAME="backend-api"
SERVICE_NAME="backend-api"

set -e

# Step 1: Authenticate Docker to Google Cloud (first time only)
# gcloud config set project long-running-forecast

# Create artifact registry repository
# gcloud services enable artifactregistry.googleapis.com run.googleapis.com

# Create artifact registry repository
# gcloud artifacts repositories create cloud-run-source-deploy \
#   --repository-format=docker \
#   --location=us-east4 \
#   --description="Docker repository for Cloud Run"

# gcloud auth configure-docker "$REGION-docker.pkg.dev"

# Step 2: Build Docker image
docker build -t "$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/$IMAGE_NAME" .

# Step 3: Tag the Docker image (to ensure latest version)
docker tag "$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/$IMAGE_NAME" "$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/$IMAGE_NAME:latest"

# Step 4: Push the Docker image to Artifact Registry
docker push "$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/$IMAGE_NAME:latest"

# Step 5: Deploy the image to Cloud Run
gcloud run deploy "$SERVICE_NAME" \
  --image="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/$IMAGE_NAME:latest" \
  --platform=managed \
  --region="$REGION" \
  --allow-unauthenticated

echo "Deployment complete!"
