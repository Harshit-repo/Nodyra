# ADR-0002: Socket Proxy For Docker Sandbox Workers

Status: Accepted

Date: 2026-07-13

## Context

Sandboxed workflow runs need Docker access to build environment images, create
the sandbox network, and create, start, stop, log, and remove run containers.
Mounting `/var/run/docker.sock` directly into an application worker gives that
worker broad daemon control, which is too much authority for a production
sandbox boundary.

## Decision

The compose sandbox overlay routes worker Docker API traffic through
`docker-socket-proxy`. The proxy is the only service that mounts the host Docker
socket, and it exposes only the Docker API groups required by Nodyra sandbox
execution. The worker points `SANDBOX_DOCKER_HOST` at the proxy instead of the
raw socket.

## Consequences

Self-hosted deployments get container-per-run isolation without giving the
worker unconstrained Docker daemon access. The proxy remains same-host daemon
access, so hard multi-tenant or regulated deployments should use a remote
runner pool or Kubernetes boundary rather than relying on this overlay as the
only isolation layer.

## Alternatives Rejected

Directly mounting the Docker socket into the worker was rejected because it
collapses the sandbox control boundary. Requiring Kubernetes for every
deployment was rejected because the self-hosted compose path still needs a
production-grade isolation option.
