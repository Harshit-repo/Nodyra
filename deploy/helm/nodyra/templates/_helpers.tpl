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
