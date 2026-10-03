# Docker Configuration

This directory contains the Docker configuration files for local development and CI/CD.

## Why are these files here?

To keep the repository root clean, the `docker-compose.yml` and `docker-compose.ci.yml` files have been moved to this `docker/` subdirectory. 

The `Dockerfile` and `.dockerignore` remain in the root directory because Docker and CI tools expect them to be there by default, and moving them can cause issues with the build context.

## Usage

Because the files are in this subdirectory, you must specify the `-f` flag when running `docker compose` commands from the repository root:

```bash
# Start local infrastructure
docker compose -f docker/docker-compose.yml up -d postgres redis rabbitmq

# Stop local infrastructure
docker compose -f docker/docker-compose.yml down -v
```

In CI pipelines, the build context is configured to step back out to the root directory `context: ..` to correctly find the `Dockerfile` and build the application.
