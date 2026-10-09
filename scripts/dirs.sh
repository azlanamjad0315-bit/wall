#!/usr/bin/env bash
# dirs.sh — docker compose ke bind-mount folders pehle se banao.
#
# Kyun zaroori hai: agar host folder mojood na ho to Docker use **root:root** banata hai, aur
# n8n (uid 1000 = node user) usme likh nahi pata → "EACCES: permission denied" ke saath container
# restart loop me chala jaata hai. Yeh #1 "maine sirf up chalaya tha" bug hai. Hum folder pehle
# bana kar 777 kar dete hain, taake har container likh sake.
#
# Kaise chalayein:  bash scripts/dirs.sh      (bootstrap.sh aur `make up` ise khud chalate hain)
set -uo pipefail
cd "$(dirname "$0")/.."

DIRS=$(grep -oE '^[[:space:]]*- \./[A-Za-z0-9._/-]+' docker-compose.yml \
        | sed -E 's|^[[:space:]]*- \./||' | sort -u)

if [[ -z "${DIRS//[[:space:]]/}" ]]; then
  echo "! docker-compose.yml se bind mounts nahi pare — check karein"
  exit 0
fi

made=0
for d in $DIRS; do
  if [[ ! -d "$d" ]]; then
    mkdir -p "$d" && made=$((made+1))
  fi
  chmod 0777 "$d" 2>/dev/null || true
  printf '  • %s\n' "$d"
done
# config/prompts/sites read-only mounts hain — bas maujood hone chahiye
for d in config prompts sites n8n/workflows; do [[ -d "$d" ]] || mkdir -p "$d"; done

echo "✓ data dirs ready ($made naye banaye)"
