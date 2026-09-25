{{- define "nodyra.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "nodyra.secretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- printf "%s-runtime" .Release.Name -}}
{{- end -}}
{{- end -}}

{{- define "nodyra.env" -}}
{{- if le (int .Values.runtime.heartbeatTimeoutSeconds) (int .Values.runtime.heartbeatIntervalSeconds) -}}
{{- fail "runtime.heartbeatTimeoutSeconds must be greater than runtime.heartbeatIntervalSeconds" -}}
{{- end -}}

- name: DATABASE_URL
  value: {{ .Values.postgres.url | quote }}
- name: REDIS_URL
  value: {{ .Values.redis.url | quote }}
- name: QUEUE_BACKEND
  value: "redis"
- name: ENVS_DIR
  value: "/app/envs"
- name: ARTIFACTS_DIR
  value: "/app/artifacts"
- name: RUNTIME_MODE
  value: "production"
- name: API_REPLICA_COUNT
  value: {{ .Values.api.replicas | quote }}
- name: WORKER_REPLICA_COUNT
  value: {{ .Values.worker.replicas | quote }}
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "nodyra.secretName" . }}
      key: secret-key
- name: INTERNAL_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "nodyra.secretName" . }}
      key: internal-api-token
- name: AUTH_REQUIRED
  value: {{ .Values.api.authRequired | quote }}
- name: AUTH_ALLOW_REGISTRATION
  value: {{ .Values.api.allowRegistration | quote }}
- name: CORS_ORIGINS
  value: {{ required "api.corsOrigins is required" .Values.api.corsOrigins | quote }}
- name: PUBLIC_API_URL
  value: {{ .Values.api.publicApiUrl | quote }}
- name: MCP_AUTHORIZATION_SERVER_URL
  value: {{ .Values.api.mcpAuthorizationServerUrl | quote }}
- name: MCP_OAUTH_INTROSPECTION_URL
  value: {{ .Values.api.mcpOauthIntrospectionUrl | quote }}
- name: MCP_OAUTH_CLIENT_ID
  value: {{ .Values.api.mcpOauthClientId | quote }}
- name: MCP_OAUTH_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "nodyra.secretName" . }}
      key: mcp-oauth-client-secret
      optional: true
- name: TRUSTED_PROXY_COUNT
  value: {{ .Values.trustedProxyCount | default 1 | quote }}
- name: RUNTIME_HEARTBEAT_INTERVAL_SECONDS
  value: {{ .Values.runtime.heartbeatIntervalSeconds | quote }}
- name: RUNTIME_HEARTBEAT_TIMEOUT_SECONDS
  value: {{ .Values.runtime.heartbeatTimeoutSeconds | quote }}
- name: RUNTIME_NO_PROGRESS_TIMEOUT_SECONDS
  value: {{ .Values.runtime.noProgressTimeoutSeconds | quote }}
{{- end -}}

{{- define "nodyra.apiImage" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{- define "nodyra.webImage" -}}
{{- $repository := .Values.web.imageRepository | default (printf "%s-web" .Values.image.repository) -}}
{{- if .Values.web.imageDigest -}}
{{- printf "%s@%s" $repository .Values.web.imageDigest -}}
{{- else -}}
{{- printf "%s:%s" $repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{/*
Pod-level security context.

The API image drops to uid 10001 via gosu in its entrypoint, but a pod with no
securityContext still *starts* as root and is rejected outright by a namespace
running the `restricted` Pod Security Standard. Declaring it here makes the
non-root identity the pod's contract rather than an entrypoint detail, and lets
the chart install unchanged into a hardened namespace.

fsGroup matters because the container no longer runs the root branch of
python-entrypoint.sh (the chown), so the kubelet must set volume ownership.
*/}}
{{- define "nodyra.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ .Values.securityContext.runAsUser }}
runAsGroup: {{ .Values.securityContext.runAsGroup }}
fsGroup: {{ .Values.securityContext.fsGroup }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "nodyra.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: {{ .Values.securityContext.readOnlyRootFilesystem }}
capabilities:
  drop:
    - ALL
{{- end -}}

{{/*
API and workers must see the same environments and local artifacts. Only
temporary files belong on emptyDir in a persistent deployment.
*/}}
{{- define "nodyra.scratchVolumes" -}}
- name: envs
  {{- if .Values.persistence.enabled }}
  persistentVolumeClaim:
    claimName: {{ .Values.persistence.envs.existingClaim | default (printf "%s-envs" .Release.Name) }}
  {{- else }}
  emptyDir: {}
  {{- end }}
- name: artifacts
  {{- if .Values.persistence.enabled }}
  persistentVolumeClaim:
    claimName: {{ .Values.persistence.artifacts.existingClaim | default (printf "%s-artifacts" .Release.Name) }}
  {{- else }}
  emptyDir: {}
  {{- end }}
- name: tmp
  emptyDir: {}
{{- end -}}

{{- define "nodyra.scratchVolumeMounts" -}}
- name: envs
  mountPath: /app/envs
- name: artifacts
  mountPath: /app/artifacts
- name: tmp
  mountPath: /tmp
{{- end -}}

{{- define "nodyra.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- printf "%s-nodyra" .Release.Name -}}
{{- else -}}
{{- .Values.serviceAccount.name | default "default" -}}
{{- end -}}
{{- end -}}
