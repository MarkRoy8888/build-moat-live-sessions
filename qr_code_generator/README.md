# QR Code Generator — Exercise

## How to Use

1. Read `PROMPT.md`
2. Answer the Design Questions (write your answers directly in `PROMPT.md`)
3. Build the prototype:
   - **Challenge Track:** Build from scratch using `PROMPT.md` as your spec
   - **Guided Track:** Go to `scaffold/`, fill in the TODOs
4. Verify with the curl tests at the bottom of `PROMPT.md`
5. Bring your Design Questions answers to live session for discussion

## Choose Your Track

**Challenge Track** — You decide the architecture, file structure, and implementation. Any language/framework is OK (Python + FastAPI recommended). Read `PROMPT.md` to get started.

**Guided Track** — File structure and boilerplate are provided. Fill in the core logic marked with `TODO`. Go to `scaffold/` and follow the instructions below.

## Guided Track Setup

**Prerequisite:** Python 3.10 or higher

```bash
cd scaffold
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Files to Fill In

| File | TODO | Design Decision |
|------|------|-----------------|
| `app/token_gen.py` | `generate_token()` | How to generate unique, URL-safe short tokens |
| `app/url_validator.py` | `validate_url()` | URL normalization and malicious URL blocking |
| `app/routes.py` | `redirect()` | Cache → DB lookup → 410/404 fallback flow |

### Run and Verify

```bash
uvicorn app.main:app --reload
```

Then run the verification tests from `PROMPT.md`.

## Interactive Playground UI

The scaffold includes a web UI that lets you toggle Q1–Q5 design choices live and observe behaviour differences side-by-side.

### Quick start

```bash
cd scaffold
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open <http://localhost:8000/> in your browser.

### What's in the UI

- **Create panel** — input URL, see live URL normalization preview, set expiration / custom alias, generate QR
- **Settings panel** (top-right ⚙️) — toggle 5 design choices and watch system behavior change immediately:
  - **Q2** — Token strategy (`random` / `hash_only` / `hash_with_nonce`) + length (5–8)
  - **Q4** — Normalization mode (`conservative` / `aggressive`)
  - **Q3** — Redirect status (`302 Found` / `301 Moved Permanently`)
  - **Q5** — Gone status for deleted/expired (`410 Gone` / `404 Not Found`)
- **Manage table** — list all QRs, edit URL, set TTL, soft-delete, view scan analytics, test scan in new tab

### How to verify each design choice

| Q | Try this in UI |
|---|---|
| **Q3** (302 vs 301) | Generate a QR → open in new tab (sees example.com) → Edit URL to a different site → open same short URL in **new tab** → instantly hits new URL. Now toggle to 301, repeat in **incognito window** to see how cached redirects break the change-target feature. |
| **Q4** (Normalization) | Type `http://Example.com:80/About/#section1` → toggle conservative ↔ aggressive in settings → see the live preview transform differently. Conservative shows 2 changes, aggressive shows 6. |
| **Q5** (404 vs 410) | Soft-delete a QR → open its short URL → see 410 page. Toggle gone_status to 404 in settings → soft-delete another QR → open its short URL → see 404 page. Then visit `/r/INVALID` → always 404 (never existed). |
| **Q2** (Token gen) | Set strategy=`random`, length=`5` → submit same URL twice → get two different 5-char tokens. Set strategy=`hash_only` → submit same URL twice → second submit returns HTTP 422 (deterministic collision, no retry). Set strategy=`hash_with_nonce` (default) → submit twice → first attempt is `sha256(url)[:7]`, second attempt retries with nonce and succeeds with different token. |

### Settings persistence

Settings are stored in-memory on the server (single shared instance). They reset to defaults on server restart, which is intentional for a teaching playground.

## Bonus Challenges

- Build a simple frontend (input URL → display QR code image)
- Add rate limiting to the create endpoint
- Add expiration support with automatic 410 responses
