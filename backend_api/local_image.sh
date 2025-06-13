#!/bin/bash

# Exit on error
set -e

IMAGE_NAME="my-backend-api"
CONTAINER_NAME="my-backend-api-container"
PORT=8080

# Build the Docker image
echo "Building Docker image..."
docker build -t $IMAGE_NAME .

echo "Stopping and removing any existing container..."
# Stop and remove previous container if running
if [ $(docker ps -aq -f name=$CONTAINER_NAME) ]; then
    docker stop $CONTAINER_NAME || true
    docker rm $CONTAINER_NAME || true
fi

# Run the Docker container
echo "Running Docker container..."
docker run --name $CONTAINER_NAME -p $PORT:8080 --env-file .env $IMAGE_NAME
