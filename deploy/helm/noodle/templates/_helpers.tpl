{{- define "noodle.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "noodle.secretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- printf "%s-runtime" .Release.Name -}}
{{- end -}}
{{- end -}}

{{- define "noodle.env" -}}
- name: DATABASE_URL
  value: {{ .Values.postgres.url | quote }}
- name: REDIS_URL
  value: {{ .Values.redis.url | quote }}
- name: QUEUE_BACKEND
  value: "redis"
- name: RUNTIME_MODE
  value: "production"
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "noodle.secretName" . }}
      key: secret-key
- name: INTERNAL_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "noodle.secretName" . }}
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
      name: {{ include "noodle.secretName" . }}
      key: mcp-oauth-client-secret
      optional: true
- name: TRUSTED_PROXY_COUNT
  value: {{ .Values.trustedProxyCount | default 1 | quote }}
{{- end -}}
