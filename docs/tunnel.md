# Public URL for the WhatsApp webhook — 4 tareeqe, sab free

`03-replies` ko WhatsApp ke incoming messages chahiye. WAHA container se n8n container tak
reach theek hai, **magar WhatsApp ka server (WAHA ke through) bahar se aata hai** — isliye ek
public HTTPS URL chahiye. Chaar option:

## A) Cloudflare quick tunnel (default, 30 second)
```bash
bash scripts/tunnel.sh
```
- ✅ Account bhi nahi chahiye, $0, HTTPS ready.
- ❌ URL har restart pe **badal jaata hai** (`https://random-3-words.trycloudflare.com`).
  Aap laptop band karke kholein → purana URL mara → replies aana band.
- Hal: `make status` pakad leta hai (webhook `000`). `tunnel.sh` dobara + `docker compose restart waha`.
- **Salaah:** demo/testing ke liye behtareen. Paisa kamaate waqt is par mat jiyen.

## B) Cloudflare named tunnel + apna domain (15 min, $0 — mere hisaab se best)
Ek baar setup, phir URL **permanent** hai aur aapke brand ka lagta hai.
```bash
# domain Cloudflare DNS me add hona chahiye (free tier kaafi)
docker run --rm -it cloudflare/cloudflared:latest tunnel login
docker run --rm -it cloudflare/cloudflared:latest tunnel create demosites \
  --name demosites --config /dev/null
# credentials.json ko repo ke bahar sambhaal lein, phir:
docker run -d --name demosites_tunnel --restart unless-stopped \
  -v $HOME/.cloudflared:/etc/cloudflared cloudflare/cloudflared:latest \
  tunnel run --token <TUNNEL_TOKEN>
```
Cloudflare dashboard → Networks → Tunnels → Public hostname:
`hooks.aapkadomain.com` → `http://n8n:5678`.
```bash
WEBHOOK_URL=https://hooks.aapkadomain.com/
N8N_EDITOR_BASE_URL=https://hooks.aapkadomain.com/
docker compose up -d n8n waha
```
✅ URL fixed, HTTPS, aapka domain = thoda zyada trust, aur n8n UI bhi remote se khulta hai
(Cloudflare Access free tier se login lag jaayein — n8n UI par public login khatarnak hai).

⚠️ **Security nahi-baatne-wali baat:** agar aap n8n ko public URL par editor ke saath khol rahe
hain, **Access/2FA lagayein**. n8n UI = aapke saare API keys + customer phone numbers ka control panel.

## C) Local network / same LAN (agar phone aur server ek Wi-Fi par hon)
```bash
WEBHOOK_URL=http://192.168.1.20:5678/
```
WAHA + n8n ek machine pe hain aur WAHA ka hook **localhost** pe bheja ja sakta hai:
`WHATSAPP_HOOK_URL=http://n8n:5678/webhook/waha` — koi tunnel nahi, kuyke webhook server-side hai!
✅ **Yeh asli best option hai** agar WAHA aur n8n same Docker network pe hain (jaisa compose karta hai).
compose file me `WHATSAPP_HOOK_URL: ${WEBHOOK_URL%/}webhook/waha` isliye default se tunnel use karta hai —
aap `.env` me `WEBHOOK_URL=http://n8n:5678/` daal dein to webhook **poori tarah local** reh jaayega,
koi public URL ki zaroorat hi khatam. Test: `bash scripts/dryrun.sh --to <apna number>` ke baad
CRM me `last_inbound` bhara aana chahiye.

> Yehi default salaah hai: **public URL ki zaroorat nahi** — jab tak aap server ghar se bahar na rakh rahe.

## D) Tailscale/ZeroTier (agar VPS pe hai aur UI private rakhna hai)
VPS pe tailscale, laptop pe tailscale → `WEBHOOK_URL=http://100.x.y.z:5678/`? Kaam nahi karega
WAHA→n8n ke liye (dono same network pe hain to C) — lekin aap UI/CRM remotely dekhne ke liye
Tailscale **free** (3 users) use karein. Public URL = sirf webhook ke liye, baaki sab private.

## Kaunsa choose karein?
| Situation | Option |
|---|---|
| abhi test kar raha hoon | **C** (webhook local) + A (UI ko phone se dekhna ho to) |
| ghar ka 24/7 server, WAHA same box | **C** — bas. Zero exposure |
| VPS pe daala, brand chahiye | **B** + Access login |
| 5 minute me demo dikhana hai | **A** |
