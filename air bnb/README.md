# Airbnb Booking Platform — Exercise

## How to Use

1. Read `PROMPT.md`
2. Answer the Design Questions (write your answers directly in `PROMPT.md`)
3. Build the prototype:
   - **Challenge Track:** Build from scratch using `PROMPT.md` as your spec
   - **Guided Track:** Go to `scaffold/`, fill in the TODOs
4. Verify with the curl tests at the bottom of `PROMPT.md`
5. Bring your Design Questions answers to live session for discussion

## Choose Your Track

**Challenge Track** — You decide the architecture, file structure, and implementation. Any language/framework is OK (Python + FastAPI + SQLite recommended). Read `PROMPT.md` to get started.

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
| `app/inventory.py` | `seed_inventory()` | How far ahead to pre-generate `(home_id, date)` rows |
| `app/search.py` | `search_homes()` | How to translate "available across N consecutive nights" into a query |
| `app/booking.py` | `try_reserve()` | Concurrency control — pessimistic lock vs reserved+TTL vs logical availability |
| `app/booking.py` | `confirm_payment()` | Transition `reserved → paid → booked` atomically; handle payment failure |
| `app/routes.py` | `book_home()` | Wire up reservation + payment flow + idempotency key handling |

### Run and Verify

```bash
uvicorn app.main:app --reload --port 8765
```

The default port `8000` often clashes with other dev servers (the QR exercise uses it too).
We use `8765` throughout — feel free to pick any free port, just match it in the curl examples.

Then run the verification tests from `PROMPT.md` and open <http://localhost:8765/> for the playground UI.

## Scope Reminder

This exercise focuses on the **guest** flow: searching, viewing, and booking. The following are **out of scope** (matching the original system design doc):

- Host admin (creating / editing listings)
- Real payment integration (we mock it as a coin-flip)
- Recommendations / ranking
- Reviews and images

## Bonus Challenges

- Add a cron job that flips expired `reserved` rows back to `available`
- Add an idempotency key to `POST /home/book` so the same payment webhook firing twice doesn't double-charge
- Replace the SQL `LIKE`/range scan with a fake "search index" (in-memory inverted index by city) and measure the latency difference
- Support geo search: `?lat=...&lng=...&radius_km=...` instead of just `city=`
