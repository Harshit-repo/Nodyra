# ADR-0004: Multi-Tenant Sandbox Defaults Fail Closed

Status: Accepted

Date: 2026-07-13

## Context

Single-tenant self-hosted deployments can run trusted workflow authors with the
standard subprocess runner. Multi-tenant deployments have a different threat
model: tenant Python must not share the worker process or an unrestricted
network namespace, and unsafe node deployment should require an explicit
operator or user approval.

## Decision

When multi-tenancy is enabled and the operator has not set explicit values,
Nodyra raises unsafe-node policy from `warn` to `require_approval`, sets
`execution_sandbox=required`, and enables non-zero warm sandbox pool defaults.
The startup sandbox policy then validates that the effective configuration can
honour the requested isolation boundary.

## Consequences

Operators who enable multi-tenancy get conservative defaults instead of a
trusted-author runtime by accident. Existing single-tenant installs keep their
current defaults. Operators can still make explicit choices, but strict startup
policy rejects combinations that claim to sandbox while routing execution
through an in-process path.

## Alternatives Rejected

Leaving multi-tenant defaults identical to single-tenant defaults was rejected
because it creates an unsafe silent deployment mode. Forcing every deployment
into mandatory sandboxing was rejected because trusted single-tenant automation
still needs a low-friction self-hosted path.
