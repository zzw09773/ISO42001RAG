#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSL_DIR="${SSL_DIR:-${SCRIPT_DIR}/ssl}"
DOMAIN="${CERT_DNS:-aimla.ai.example.com}"

umask 077
mkdir -p "$SSL_DIR"

OPENSSL_CONF="$(mktemp)"
trap 'rm -f "$OPENSSL_CONF"' EXIT

cat > "$OPENSSL_CONF" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = req_ext

[dn]
C = TW
ST = Taiwan
L = Taipei
O = NCSIST
OU = AIMLA
CN = ${DOMAIN}

[req_ext]
subjectAltName = @alt_names

[alt_names]
DNS.1 = ${DOMAIN}
EOF

openssl genrsa -out "$SSL_DIR/cert.key" 2048
openssl req -new -key "$SSL_DIR/cert.key" -out "$SSL_DIR/cert.csr" -config "$OPENSSL_CONF"
openssl x509 -req -days 365 \
  -in "$SSL_DIR/cert.csr" \
  -signkey "$SSL_DIR/cert.key" \
  -out "$SSL_DIR/cert.crt" \
  -extensions req_ext \
  -extfile "$OPENSSL_CONF"

chmod 600 "$SSL_DIR/cert.key"
chmod 644 "$SSL_DIR/cert.crt" "$SSL_DIR/cert.csr"

echo "自簽憑證已建立於 $SSL_DIR，DNS SAN: $DOMAIN"
