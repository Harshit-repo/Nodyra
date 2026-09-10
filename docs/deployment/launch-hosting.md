# Suggested initial hosting for Nodyra

Recommendation reviewed 2026-09-10. No cloud resources have been purchased or
deployed as part of the local launch verification.

## Initial shape and cost

For a controlled first launch, use an Ubuntu Linux VM in **AWS Lightsail,
Sydney**, with **4 vCPUs and 16 GB RAM**. The public-IPv4 general-purpose bundle
is currently **US$84/month**, before tax, backups, object storage, domain, and
monitoring charges. Verify the selected region and checkout total before
purchase. [AWS bundle specification](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html).

This is an initial sizing recommendation, not a measured capacity guarantee.
The machine would run Caddy, the web/API services, PostgreSQL, Redis, and a
separate worker process. Start with low workflow concurrency, measure memory
and CPU during the actual workload, and move workers to separate compute when
heavy Python environments compete with the database or web service.

Use a dedicated VM/installation per trusted organization for the first
self-hosted release. For a service accepting untrusted workflow authors,
require the hardened sandbox and qualify gVisor or Kata on the actual Linux
host. Multi-tenant service operation also requires the licensed features,
tenant-isolation checks, and a production topology review. The single-VM
shape has a single-machine failure domain; high availability requires a
separate design with managed database/Redis and independent worker capacity.

## Host and ingress

1. Install Docker Engine and its Compose plugin (2.24.4 or newer) using the
   [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
2. Assign a static IP and point the chosen domain's DNS record at it. Open
   public TCP 80/443; restrict SSH to the operator's IP. Leave database, Redis,
   MinIO, API, and web internal ports on loopback.
3. Install Caddy as a host service. For the default Compose web port, the
   site configuration is:

   ```caddyfile
   app.your-domain.example {
       encode zstd gzip
       reverse_proxy 127.0.0.1:5173
   }
   ```

   Substitute the real owned domain. Caddy manages certificates and redirects
   HTTP to HTTPS once DNS and reachability are correct.
   [Automatic HTTPS](https://caddyserver.com/docs/automatic-https),
   [reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

4. Copy `deploy/.env.example` to a private `deploy/.env`, generate independent
   random hexadecimal secrets, and set at least:

   ```dotenv
   # Substitute independent random secrets; do not use these placeholders.
   NODYRA_SECRET_KEY=<64 random hexadecimal characters>
   INTERNAL_API_TOKEN=<different 64 random hexadecimal characters>
   POSTGRES_PASSWORD=<different random hexadecimal secret>
   MINIO_ROOT_PASSWORD=<different random hexadecimal secret>
   AUTH_REQUIRED=true
   AUTH_ALLOW_REGISTRATION=false
   NODYRA_BIND_HOST=127.0.0.1
   CORS_ORIGINS=https://app.your-domain.example
   PUBLIC_API_URL=https://app.your-domain.example/api
   TRUSTED_PROXY_COUNT=2
   SESSION_COOKIE_SECURE=true
   NODYRA_ALLOW_PRIVATE_EGRESS=0
   EXECUTION_SANDBOX=required
   SANDBOX_RUNTIME=runsc
   ARTIFACT_S3_BUCKET=<private external bucket>
   ARTIFACT_S3_REGION=ap-southeast-2
   ARTIFACT_S3_ENDPOINT=https://s3.ap-southeast-2.amazonaws.com
   ARTIFACT_S3_PUBLIC_ENDPOINT=https://s3.ap-southeast-2.amazonaws.com
   AWS_ACCESS_KEY_ID=<scoped bucket access key>
   AWS_SECRET_ACCESS_KEY=<scoped bucket secret>
   ```

   Two trusted proxies means Caddy → web nginx → API. Recalculate the value if
   a CDN/load balancer is added. Install and verify `runsc` first; required
   sandbox admission must fail rather than fall back when it is unavailable.
   Follow the [sandbox setup](../deployment.md#sandboxed-execution), including
   the socket-proxy overlay. Never expose the Docker proxy publicly.

5. Use the approved release source and verified image digests. A source build
   for qualification can be started with:

   ```bash
   docker compose --env-file deploy/.env -p nodyra \
     -f deploy/docker-compose.yml -f deploy/docker-compose.sandbox.yml \
     -f deploy/docker-compose.external-s3.yml \
     up -d --build
   ```

   The API runs migrations before serving. Check migration logs and service
   health before opening the application to users. Complete first-owner setup
   over HTTPS and keep public registration disabled until its intended access
   model has been reviewed.

## Durable storage and recovery

Use an external private S3 bucket in the same region, with versioning,
encryption, scoped credentials, and a documented lifecycle policy. The external
S3 overlay excludes both bundled MinIO services and removes their startup
dependencies. Do not enable the `local-artifacts` profile on this production
stack. The base Compose file still validates its MinIO password variable during
interpolation; retain the independent generated value even though this overlay
does not start MinIO.

The bundled MinIO image is retained only for disposable local acceptance.
Its community repository is archived, and the bundled image has unresolved
security findings; it is not a qualified production storage option.
[Upstream repository status](https://github.com/minio/minio),
[upstream security advisories](https://github.com/minio/minio/security).

For real AWS S3, configure `ARTIFACT_S3_BUCKET`, region, and the appropriate
endpoint rather than the Compose MinIO defaults. Set the public signing
endpoint to the browser-reachable S3 endpoint. Do not expose the MinIO console
or publish the bucket. Confirm both authenticated streaming downloads and
time-limited download links through the public application.

Back up PostgreSQL, object data, organization encryption keys, the application
signing secret, and deployment configuration into encrypted storage outside
the VM. Retain copies under a separate access policy. A versioned bucket is
not by itself a complete application backup: database rows and encryption keys
must also be restorable together.

Use the [restore runbook](../backup-restore.md) and recovery fixture to verify a
fresh restore. Record the actual RPO/RTO for representative production data.
Schedule backup execution and restore drills in the operator's infrastructure;
the local test did not configure a cloud backup schedule.

## Before public traffic

- Exercise HTTPS login/sign-out, workflow editing/publication, a scheduled run,
  authenticated webhook, cancellation/deadline, upload, and artifact download.
- Run `scripts/live_workflow_acceptance.py` on disposable data against the
  public `/api` URL and repeat the browser journeys on desktop and mobile.
- Check `/api/ops/production-attestation` as owner. Resolve every failure and
  attach external evidence for warnings; a configured URL alone is not proof
  of working TLS, backups, KMS, or retained tracing.
- Verify that untrusted execution is actually sandboxed on the selected
  runtime, including subworkflows and file transfers. Test failure when the
  sandbox daemon/runtime is unavailable.
- Set alerts for dependency health, queue age, run failures, storage capacity,
  failed backups, and certificate expiry. Assign the on-call/support and
  security-response owner.
- Keep a verified backup and previous signed images for rollback. Complete the
  [release evidence contract](../operations/release-evidence.md) and the
  [staged rollout](../operations/staged-rollout.md) before GA.

The remaining operator decisions are the owned domain, cloud account, accepted
monthly budget, production data location, and who operates the service. Public
deployment should be approved against these concrete settings after the final
local certification is green.
