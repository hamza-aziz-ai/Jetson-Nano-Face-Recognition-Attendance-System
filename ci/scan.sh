#!/bin/sh
# Fail if the repository contains private IP addresses, secret files, biometric data, models or credentials.
# Usage: sh ci/scan.sh   (from the repo root; scans tracked files)
set -u
fail=0
files=$(git ls-files)

# 1. files that must never be committed
bad=$(printf '%s\n' "$files" | grep -E '(^|/)(web_auth|rtsp_auth|facepipe\.env)$|^(data|gallery)/|\.(onnx|plan|engine|jpg|jpeg|png|bmp|mp4|tsv|jsonl)$' || true)
if [ -n "$bad" ]; then echo "FORBIDDEN FILES:"; echo "$bad"; fail=1; fi

# 2. private IPv4 addresses (127.0.0.1 is allowed: the local RTP hop)
ips=$(printf '%s\n' "$files" | xargs grep -nIE '\b(192\.168|10\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]{1,3}\.[0-9]{1,3}\b' -- 2>/dev/null || true)
if [ -n "$ips" ]; then echo "PRIVATE IP ADDRESSES:"; echo "$ips"; fail=1; fi

# 3. credentials
creds=$(printf '%s\n' "$files" | xargs grep -nIE 'BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}' -- 2>/dev/null || true)
if [ -n "$creds" ]; then echo "CREDENTIALS:"; echo "$creds"; fail=1; fi

[ $fail -eq 0 ] && echo "scan ok: $(printf '%s\n' "$files" | wc -l) tracked files, no secrets, IPs or biometric data"
exit $fail
