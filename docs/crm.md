# CRM: Baserow (default) ya Google Sheets

CRM is system ka **source of truth** hai: kaunsi lead mili, demo live hai ya nahi, message gaya,
jawaab aaya, intent kya tha, paisa aaya ya nahi. 4 workflows isi par chalate hain.

## Data model (44 fields, `tools/baserow_setup.py` banata hai)

| Group | Fields |
|---|---|
| identity | `lead_key` (unique: `node/123`), `business_name`, `category`, `category_label`, `emoji` |
| location | `city`, `area`, `country`, `address` |
| contact | `phone`, `whatsapp`, `email`, `website` |
| signal | `rating`, `reviews`, `hours`, `hotness` (0-100) |
| demo | `demo_slug`, `demo_url`, `design`, `site_bytes`, `ai_provider`, `deploy_target`, `deploy_note` |
| outreach | `status`, `intent`, `language`, `price_label`, `last_outbound`, `last_ai_reply`, `sent_at`, `outreach_channel` |
| thread | `last_inbound`, `outbound_count`, `reply_count`, `ai_reply_count`, `followup_count`, `next_action_at` |
| money | `payment_info_sent`, `paid`, `amount` |
| misc | `source`, `notes`, `ai_notes` |

### `status` ki poori zindagi
```
scanned → demo_ready → contacted → replied ─┬→ interested → payment_claimed → paid
   │            (01)        (02)     (03)   ├→ negotiating → interested
   └→ demo_failed (retry)                  ├→ revision_requested → contacted
                                            ├→ not_interested (end)
                                            └→ closed_noreply (04, 2 follow-ups ke baad)
```
`stalled`, `deploy_pending`, `send_failed`, `inbound_unmatched`, `handoff`, `auto_reply`, `spam` bhi
hain — sab `03`/`04` se set hote hain. `next_action_at` (YYYY-MM-DD text) workflow 02/04 ka gate hai.

Dates **text** fields hain (YYYY-MM-DD): Baserow ka date type ISO coercion karta hai jo n8n se
aate hue kabhi-kabhi toot-ta hai. Text = boring = reliable.

## Baserow (recommended, self-hosted, no account)
```bash
docker compose --profile baserow up -d         # ~40 s
# http://localhost:8100 → Sign up (local) → profile → Settings → API Token
bash scripts/crm.sh --token <TOKEN>
docker compose up -d n8n
```
`crm.sh` database + `leads` table + saare fields banata hai aur `.env` me `BASEROW_TABLE_ID`
likh deta hai. Dobara chalane par existing table reuse hota hai (duplicate fields nahi bante).

**Views jo aapko banane chahiye (1 min, UI me):**
1. `🔥 Aaj ke hot` — filter: status ∈ interested, negotiating, payment_claimed; sort hotness desc.
2. `📤 Pending outreach` — status = demo_ready AND demo_url not empty.
3. `💰 Paid` — paid = yes; `amount` ka sum = aapki revenue (Baserow inline aggregation).
4. `🧹 Cleanup` — status = demo_failed OR send_failed (daily 1 min, API key/tunnel theek karein).

## Google Sheets (agar aapko Sheet chahiye)
`CRM_BACKEND=gsheets` workflow JSON me **koi automatic switch nahi** hai (soch samajh kar:
do backend ka ek hybrid code rakhna = dobara-dobara tootne wala system). Sheets ke liye
Baserow nodes ko Sheets nodes se replace karein — 3 patterns, har workflow me same:

| Baserow node | n8n Google Sheets node | settings |
|---|---|---|
| `Read CRM · raw` | **Get Rows** | Document = `$env.GSHEET_ID`, Sheet = `$env.GSHEET_TAB` |
| `CRM: X · create` | **Append Row** | mapping: row object = `={{ $json.body_json }}` → use "Map Manual" once, columns same names |
| `CRM: X · update` + `· exists?` | **Update Row** | Column to match = `lead_key`, cell values = same object |
| `· body` Code node | **rehne dein** | wahi JSON banata hai — Sheets ko same field names chahiye |

Zaroori: Sheet ke pehle row me **exact column names** hone chahiye (upar wali table se copy karein —
44 columns, ek baar). Row 2 se data. `GSHEET_ID` = URL me `/d/<ID>/edit` wala hissa.

Credential: n8n → Credentials → Google Sheets → OAuth (ya Service Account). Sheet ko **Anyone with
the link → Viewer** mat karein (aapke lead data me phone numbers hain) — private rakhein.

Sheets-only mode ka practical farq: Sheets API slow hai (rate limit 60 reads/min/user) — `03-replies`
har inbound message par `Read CRM` karta hai, isliye 20+ replies/minute wale scale par Sheets
thak jaayega. Baserow local hai, isliye nahi thakta. Isliye default: Baserow. Aap 200 leads/month
tak kahin bhi theek hain.

## Reset / backup
```bash
make reset                 # saare rows delete (table/fields safe)
cp baserow/data /backups/baserow-$(date +%F).dump   # DB file (compose volume)
tar czf crm-$(date +%F).tgz baserow gateway/data n8n config prompts .env   # sab kuch
```
Weekly `tar` ek Google Drive folder me daal dein — yehi aapka business data hai (leads = paisa).

## Import/export
Baserow → table → ⋯ → Export CSV. Monthly khud karein: 40 rows dekh kar aapko pata chal jaayega
ke kaunsa area/niche/wording chal raha hai. Yeh 5 minute ka kaam poore mahine se zyada qeemti hai.
