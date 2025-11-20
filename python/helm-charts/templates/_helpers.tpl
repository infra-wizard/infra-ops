{{- define "data-analytics.name" -}}
{{ .Chart.Name }}
{{- end }}

{{- define "data-analytics.fullname" -}}
{{ .Release.Name }}-{{ .Chart.Name }}
{{- end }}
