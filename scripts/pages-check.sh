#!/usr/bin/env bash
# pages-check.sh — GitHub Pages ka status + live link probe (deploy ke baad 404 aana common bug hai)
set -uo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && { set -a; . ./.env; set +a; }
[[ -n "${GITHUB_TOKEN:-}" && -n "${GITHUB_REPO:-}" ]] || { echo "GITHUB_TOKEN / GITHUB_REPO .env me daalein"; exit 1; }
echo "── Pages config ──"
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$GITHUB_REPO/pages" | \
python3 -c 'import sys,json
d=json.load(sys.stdin)
if not d: print("❌ Pages enable nahi (Settings → Pages → Deploy from a branch → main / (root))"); raise SystemExit(1)
print("status:", d.get("status"), "| url:", d.get("html_url"), "| build_type:", d.get("build_type"))
print("source:", (d.get("source") or {}).get("branch"), (d.get("source") or {}).get("path"))'
echo "── .nojekyll (jekyll skip = fast builds) ──"
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/repos/$GITHUB_REPO/contents/.nojekyll"
echo "── build status ──"
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/$GITHUB_REPO/pages/builds/latest" | \
python3 -c 'import sys,json
d=json.load(sys.stdin); print(d.get("status"), "|", d.get("created_at"), "|", (d.get("error") or {}).get("message",""))'
echo "── live probe ──"
BASE="${PAGES_BASE_URL:-}"
[[ -n "$BASE" ]] || { echo "PAGES_BASE_URL .env me daalein (e.g. https://user.github.io/demo-sites)"; exit 1; }
for u in "$BASE/" "$BASE/smoke-test/" "$BASE/README.md"; do
  printf '  %-58s → %s\n' "$u" "$(curl -s -o /dev/null -w '%{http_code} %{size_download}B' -L "$u")"
done
echo
echo "Agar 404: 30-60 sec Pages build. Manual check: docker compose logs ai-gateway | grep -i pages"
