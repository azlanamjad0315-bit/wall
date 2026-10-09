"""deploy.py — publish the single-file site, then prove it is live.

Targets (all free):
  github-pages : PUT via Contents API → poll until 200 (Pages build takes 20-90 s, so we wait)
  cf-pages     : direct upload via the Pages manifest API (no git, instant)
  local        : write to /data/public and serve from the gateway — zero accounts, perfect for
                 a dry run and for the `make test` path

Why "prove it is live" matters: the outreach message contains the demo URL. If Pages is still
building, the prospect taps a 404 and the whole funnel dies. So we never return a URL we have
not fetched successfully.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("gateway.deploy")


class DeployError(RuntimeError):
    pass


class Deployer:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.target = (cfg.get("DEPLOY_TARGET") or "local").strip().lower()
        self.data = Path(cfg.get("DATA_DIR", "/data"))
        (self.data / "public").mkdir(parents=True, exist_ok=True)
        self._transport = None          # tests inject httpx.MockTransport

    def _client(self, **kw):
        return httpx.AsyncClient(transport=self._transport, **kw)

    # ------------------------------------------------------------------ github
    @property
    def gh(self) -> tuple[str, str, str, str]:
        repo = self.cfg.get("GITHUB_REPO", "")          # "user/demo-sites"
        tok = self.cfg.get("GITHUB_TOKEN", "")
        branch = self.cfg.get("GITHUB_BRANCH", "main")
        base = (self.cfg.get("PAGES_BASE_URL") or "").rstrip("/")
        if "/" not in repo:
            raise DeployError("GITHUB_REPO must look like 'username/demo-sites'")
        if not tok:
            raise DeployError("GITHUB_TOKEN missing — fine-grained PAT with Contents:read/write")
        if not base:
            owner, name = repo.split("/", 1)
            root_like = {owner.lower(), f"{owner}.github.io", f"{owner}.github.io".lower()}
            base = f"https://{owner.lower()}.github.io" if name.lower() in root_like \
                else f"https://{owner.lower()}.github.io/{name}"
        return repo, tok, branch, base

    async def _gh_put(self, client: httpx.AsyncClient, repo: str, tok: str, branch: str,
                      path: str, content: str, message: str) -> dict:
        h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"}
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        sha = None
        r = await client.get(f"{url}?ref={branch}", headers=h)
        if r.status_code == 200:
            sha = (r.json() or {}).get("sha")
        elif r.status_code not in (404, 403, 401):
            log.info("gh stat %s -> %s (continuing as new file)", path, r.status_code)
        b = {"message": message[:70], "branch": branch,
             "content": base64.b64encode(content.encode()).decode()}
        if sha:
            b["sha"] = sha
        r = await client.put(url, headers=h, json=b)
        if r.status_code not in (200, 201):
            raise DeployError(f"github commit failed for {path}: {r.status_code} {r.text[:220]}")
        return r.json()

    async def deploy_github(self, slug: str, html: str, extra: dict | None = None) -> dict:
        repo, tok, branch, base = self.gh
        async with self._client(timeout=60) as c:
            guard = await c.get(f"https://api.github.com/repos/{repo}/contents/.nojekyll?ref={branch}",
                                headers={"Authorization": f"Bearer {tok}"})
            if guard.status_code == 404:      # skip Jekyll: faster builds, no underscore stripping
                await self._gh_put(c, repo, tok, branch, ".nojekyll", "", "chore: disable jekyll")
            await self._gh_put(c, repo, tok, branch, f"{slug}/index.html", html,
                               f"feat(demo): {slug}")
            if extra:
                for path, body in extra.items():
                    await self._gh_put(c, repo, tok, branch, path, body, f"chore: {path}")
            url = f"{base}/{slug}/"
            live = await self._wait_live(c, url)
        return {"target": "github-pages", "url": url, "live": live, "repo": repo,
                "note": "" if live else "committed, Pages still building — link will work in ~1-2 min"}

    # ----------------------------------------------------------- cloudflare pages
    async def deploy_cf(self, slug: str, html: str) -> dict:
        acct, proj, tok = self.cfg.get("CF_ACCOUNT_ID"), self.cfg.get("CF_PAGES_PROJECT"), \
            self.cfg.get("CF_API_TOKEN")
        if not (acct and proj and tok):
            raise DeployError("CF_ACCOUNT_ID / CF_PAGES_PROJECT / CF_API_TOKEN incomplete")
        body = html.encode()
        digest = hashlib.sha256(body).hexdigest()
        h = {"Authorization": f"Bearer {tok}"}
        async with self._client(timeout=90) as c:
            manifest = {f"/{slug}/index.html": digest}
            files = {"_manifest": (None, json.dumps(manifest), "text/plain"),
                     digest: (f"{slug}/index.html", body, "text/html")}
            r = await c.post(f"https://api.cloudflare.com/client/v4/accounts/{acct}/pages/projects/"
                             f"{proj}/deployments", headers=h, files=files)
            if r.status_code not in (200, 201):
                raise DeployError(f"cloudflare deploy failed: {r.status_code} {r.text[:240]}")
            d = (r.json().get("result") or {})
            url = d.get("url") or f"https://{proj}.pages.dev/{slug}/"
            alias = d.get("deployment_trigger", {}).get("metadata", {}).get("branch")  # noqa
            live = await self._wait_live(c, url)
        return {"target": "cf-pages", "url": url, "live": live, "preview": d.get("url"),
                "note": "aliases production domain after approval"}

    # -------------------------------------------------------------------- local
    def deploy_local(self, slug: str, html: str) -> dict:
        p = self.data / "public" / slug
        p.mkdir(parents=True, exist_ok=True)
        (p / "index.html").write_text(html, encoding="utf-8")
        pub = (self.cfg.get("PUBLIC_PREVIEW_URL") or "").rstrip("/")
        url = f"{pub}/preview/{slug}/" if pub else f"/preview/{slug}/"
        return {"target": "local", "url": url, "live": True,
                "path": str(p / "index.html"),
                "note": "gateway-local preview (no account needed)"}

    # ------------------------------------------------------------------ helpers
    async def _wait_live(self, client: httpx.AsyncClient, url: str, tries: int = 12) -> bool:
        for i in range(tries):
            try:
                r = await client.get(url, headers={"Cache-Control": "no-cache"})
                if r.status_code == 200 and len(r.text) > 800:
                    return True
            except Exception as e:
                log.debug("liveness probe %s: %s", url, e)
            await asyncio.sleep(8 if i < 3 else 12)
        return False

    async def deploy(self, slug: str, html: str, meta: dict | None = None) -> dict:
        slug = re.sub(r"[^a-z0-9.-]", "-", (slug or "demo").lower()).strip("-.") or "demo"
        t = self.target
        if t in ("github", "github-pages", "gh"):
            out = await self.deploy_github(slug, html, {f"{slug}/meta.json": json.dumps(meta or {},
                                                                                          ensure_ascii=False)})
        elif t in ("cf", "cloudflare", "cf-pages"):
            out = await self.deploy_cf(slug, html)
        else:
            out = self.deploy_local(slug, html)
        out["slug"] = slug
        out["bytes"] = len(html)
        return out

    async def deploy_portfolio(self, html: str) -> dict:
        if self.target in ("github", "github-pages", "gh"):
            repo, tok, branch, base = self.gh
            async with self._client(timeout=60) as c:
                await self._gh_put(c, repo, tok, branch, "index.html", html, "chore: refresh portfolio")
                url = base + "/"
                return {"target": self.target, "url": url, "live": await self._wait_live(c, url, 4)}
        p = self.data / "public" / "index.html"
        p.write_text(html, encoding="utf-8")
        pub = (self.cfg.get("PUBLIC_PREVIEW_URL") or "").rstrip("/")
        return {"target": "local", "url": (pub + "/preview/") if pub else "/preview/", "live": True}
