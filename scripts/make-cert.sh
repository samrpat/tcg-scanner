#!/usr/bin/env bash
# Generate a self-signed TLS certificate so the capture page works on a phone.
#
# Browsers only expose getUserMedia in a "secure context": HTTPS, or localhost. A laptop at
# http://localhost gets the camera; a phone at http://192.168.x.x does not, and there is no
# flag or permission that changes that. So the phone needs HTTPS, and for a box on your own
# LAN a self-signed certificate is the only option that does not involve a public domain.
#
# The certificate covers localhost and every LAN address this machine currently has, so the
# same file works however you reach the server.
set -euo pipefail

OUT="${1:-data/certs}"
DAYS=825   # Safari refuses certificates valid for much longer than this.

mkdir -p "$OUT"

collect_ips() {
  if command -v ipconfig >/dev/null 2>&1; then
    for iface in $(ipconfig getiflist 2>/dev/null || echo "en0 en1"); do
      ipconfig getifaddr "$iface" 2>/dev/null || true
    done
  fi
  if command -v hostname >/dev/null 2>&1; then
    hostname -I 2>/dev/null | tr ' ' '\n' || true
  fi
  if command -v ip >/dev/null 2>&1; then
    ip -4 -o addr show scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]}' || true
  fi
}

IPS="$(collect_ips | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | sort -u || true)"

ALT="DNS:localhost,DNS:*.local,IP:127.0.0.1,IP:::1"
for ip in $IPS; do
  ALT="${ALT},IP:${ip}"
done

echo "Certificate will cover:"
printf '  localhost, 127.0.0.1\n'
for ip in $IPS; do printf '  %s\n' "$ip"; done
echo

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$OUT/server.key" \
  -out "$OUT/server.crt" \
  -days "$DAYS" \
  -subj "/CN=tcg-scanner.local/O=TCG Scanner (self-signed)" \
  -addext "subjectAltName=${ALT}" \
  -addext "basicConstraints=CA:FALSE" \
  -addext "keyUsage=digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" \
  2>/dev/null

chmod 600 "$OUT/server.key"
chmod 644 "$OUT/server.crt"

echo "Wrote $OUT/server.crt and $OUT/server.key"
echo
echo "Next:"
echo "  make up"
echo "  On the phone, open  https://<this machine's LAN IP>:\${WEB_TLS_PORT:-8443}"
echo "  Accept the certificate warning once. The camera then works exactly as it does on the laptop."
