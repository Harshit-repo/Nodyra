{{- define "noodle.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "noodle.env" -}}
- name: DATABASE_URL
  value: {{ .Values.postgres.url | quote }}
- name: REDIS_URL
  value: {{ .Values.redis.url | quote }}
- name: QUEUE_BACKEND
  value: "redis"
- name: SECRET_KEY
  value: {{ .Values.secret.key | quote }}
{{- end -}}
