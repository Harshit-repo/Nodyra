// Acronyms preserved in uppercase when auto-titling snake_case param names.
const PARAM_LABEL_ACRONYMS = new Set([
  "http", "https", "url", "uri", "id", "aws", "api", "sms", "ip", "ips",
  "json", "xml", "os", "csv", "jwt", "oauth", "oauth2", "sql", "cors",
  "tls", "ssl", "ssh", "tcp", "udp", "dns", "ai", "ldap", "smtp", "ftp",
  "sftp", "gcs", "s3", "rss", "uuid", "md5", "sha", "html", "css", "rgb",
  "cli", "io", "cdn", "cpu", "ram", "gpu",
]);

export function formatParamLabel(name: string): string {
  if (!name) return "";
  return name
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => {
      const lower = word.toLowerCase();
      if (PARAM_LABEL_ACRONYMS.has(lower)) return lower.toUpperCase();
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(" ");
}

export function isSecretField(name: string): boolean {
  return /password|secret|key|token|private|uri|url|connection/i.test(name);
}
