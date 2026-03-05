#!/bin/bash

# 產生測試用自簽憑證
mkdir -p ssl

# 產生私鑰
openssl genrsa -out ssl/cert.key 2048

# 產生憑證簽署請求 (CSR)
openssl req -new -key ssl/cert.key -out ssl/cert.csr -subj "/C=TW/ST=Taiwan/L=Taipei/O=MyCompany/OU=IT/CN=judge.local"

# 產生自簽憑證
openssl x509 -req -days 365 -in ssl/cert.csr -signkey ssl/cert.key -out ssl/cert.crt

echo "測試憑證已建立於 ssl/ 目錄下。請注意這只是用於測試，正式環境請替換為合法憑證。"
