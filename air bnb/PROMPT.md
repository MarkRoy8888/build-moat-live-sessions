# Airbnb Booking Platform Prototype

## System Requirements

Build a guest-facing Airbnb booking system where:

- Users can **search** listings by `city` + `start_date` + `end_date` (with pagination)
- Users can **view home details** (id, city, address, type, amenities — text only, no images / reviews)
- Users can **book** a home for a date range
- Booking flow has two steps: **reserve** (hold inventory for 10 minutes) → **confirm payment** (mark as booked)
- A reservation that is not paid within 10 minutes must be released so others can book
- **Two users booking overlapping dates on the same home — only one succeeds, no double booking**
- Search must complete in **< 500ms** even with many listings
- Search returns "available" homes; the system favours availability for reads but consistency for writes

## Functional Endpoints

```http
GET  /home/search?city={city}&startDate={YYYY-MM-DD}&endDate={YYYY-MM-DD}&pageSize={n}&page={n}
GET  /home/{home_id}
POST /home/book
     body: { "home_id": "...", "start_date": "...", "end_date": "...", "user_id": "..." }
POST /home/book/{booking_id}/confirm    # mock payment success
POST /home/book/{booking_id}/cancel     # user abandons / payment fails
```

## Design Questions

Answer these before you start coding:

1. **Inventory representation:** Why pre-generate one row per `(home_id, date)` for the next 6–12 months, instead of storing booked date *ranges* as `(home_id, start, end)`? What does each approach cost in storage / write amplification, and what does each one buy you in query latency?

2. **Concurrent booking control:** Three options were on the table: (a) `SELECT FOR UPDATE` pessimistic lock during the entire payment window, (b) Add a `reserved` status with `expires_at`, plus a cron job to flip expired rows back to `available`, (c) Treat availability as a **logical** state (`available OR (reserved AND expired)`) and keep cron as a janitor only. Which does the scaffold choose, and what does the cron-failure scenario look like under each?

3. **Search latency under growth — secondary index design:** Current query is `WHERE city = ? AND date BETWEEN ? AND ? AND status = 'available'`. As listings grow to 10M and date rows to billions, what slows down first?
   - **3a.** Should you build a single-column index on `city`, or a **compound (composite) index** on `(city, date, status)`? What's the difference in query plan?
   - **3b.** Given a compound index `(city, date, status)`, will `WHERE date = ?` (no `city` filter) use the index? Why or why not? (Hint: **leftmost prefix rule**.)
   - **3c.** What is a **covering index**? If you change the query to `SELECT home_id, status`, can you make the entire query satisfied from the index alone (no row fetch)?
   - **3d.** What's the **write cost** of having too many indexes? Why should you build indexes from query patterns rather than speculatively?
   - **3e.** When does indexing stop being enough? Compare with (a) Elasticsearch fed by CDC and (b) materialized "city → available date bitmap" cache. When is each the right next step?

4. **Booking state machine + payment failures:** A booking moves through `reserved → paid → booked`. What goes wrong if you only had `available / booked` (no intermediate `reserved`)? What goes wrong if `paid` and `booked` were a single step? What does your system do when the payment webhook arrives **twice** for the same booking (idempotency)?

5. **Read-heavy caching:** At 10M DAU ≈ 400K QPS, you must cache. **Home detail** (`GET /home/{id}`) is great to cache; **inventory availability** (search) is dangerous to cache. Why? What invalidation strategy do you use for each, and what stale window is acceptable for each?

## Verification

Your prototype should pass all of these:

```bash
# --- Setup: seed a few homes and inventory (the scaffold does this on boot) ---

# Search homes in a city for a date range
curl "http://localhost:8000/home/search?city=Honolulu&startDate=2026-09-01&endDate=2026-09-05&pageSize=10&page=1"
# → 200, returns Home[]

# Get home details
curl http://localhost:8000/home/{home_id}
# → 200, returns {"id": "...", "city": "...", "address": "...", "type": "...", "amenities": [...]}

# Reserve a home for a date range
curl -X POST http://localhost:8000/home/book \
  -H "Content-Type: application/json" \
  -d '{"home_id":"H1","start_date":"2026-09-01","end_date":"2026-09-05","user_id":"U1"}'
# → 200, returns {"booking_id": "...", "status": "reserved", "expires_at": "..."}

# Confirm payment within 10 minutes
curl -X POST http://localhost:8000/home/book/{booking_id}/confirm
# → 200, returns {"booking_id": "...", "status": "paid"}
# Inventory rows for those dates now have status = "booked"

# Search now excludes those dates
curl "http://localhost:8000/home/search?city=Honolulu&startDate=2026-09-01&endDate=2026-09-05&pageSize=10&page=1"
# → 200, H1 NOT in results

# --- Concurrency: two users race for the same dates ---

# User A reserves
curl -X POST http://localhost:8000/home/book \
  -H "Content-Type: application/json" \
  -d '{"home_id":"H2","start_date":"2026-10-01","end_date":"2026-10-03","user_id":"U_A"}'
# → 200 reserved

# User B tries the same dates immediately
curl -X POST http://localhost:8000/home/book \
  -H "Content-Type: application/json" \
  -d '{"home_id":"H2","start_date":"2026-10-01","end_date":"2026-10-03","user_id":"U_B"}'
# → 409 Conflict, body: {"detail": "Dates not available"}

# User A abandons (or 10 minutes pass, then any new booking attempt re-checks logical availability)
curl -X POST http://localhost:8000/home/book/{A_booking_id}/cancel
# → 200, status: "cancelled"

# User B retries — now succeeds
curl -X POST http://localhost:8000/home/book \
  -H "Content-Type: application/json" \
  -d '{"home_id":"H2","start_date":"2026-10-01","end_date":"2026-10-03","user_id":"U_B"}'
# → 200 reserved

# --- Idempotency: payment webhook fires twice ---

curl -X POST http://localhost:8000/home/book/{booking_id}/confirm \
  -H "Idempotency-Key: webhook-evt-12345"
# → 200, status: "paid"

curl -X POST http://localhost:8000/home/book/{booking_id}/confirm \
  -H "Idempotency-Key: webhook-evt-12345"
# → 200, same response (NOT a second charge / NOT a 409)

# --- Non-existent home ---
curl -o /dev/null -w "%{http_code}" http://localhost:8000/home/DOES_NOT_EXIST
# → 404
```

### Verification: Secondary Index Behavior

These tests use SQLite's `EXPLAIN QUERY PLAN` to **prove** the index is (or isn't) being used. At small data volumes the wall-clock difference is invisible — that's why we read the query plan instead of timing it.

```bash
# (Boot the scaffold once so it seeds the DB, then stop it.)

# --- Step 1: query plan WITHOUT an index ---
sqlite3 airbnb.db "EXPLAIN QUERY PLAN
  SELECT home_id FROM inventory
  WHERE city = 'Honolulu' AND date BETWEEN '2026-09-01' AND '2026-09-05'
    AND status = 'available'"
# Expected: SCAN inventory          ← full table scan

# --- Step 2: add the compound index ---
sqlite3 airbnb.db "CREATE INDEX idx_city_date_status ON inventory(city, date, status)"

# --- Step 3: same query — index now used ---
sqlite3 airbnb.db "EXPLAIN QUERY PLAN
  SELECT home_id FROM inventory
  WHERE city = 'Honolulu' AND date BETWEEN '2026-09-01' AND '2026-09-05'
    AND status = 'available'"
# Expected: SEARCH inventory USING INDEX idx_city_date_status

# --- Step 4: leftmost-prefix rule — query without leading column ---
sqlite3 airbnb.db "EXPLAIN QUERY PLAN
  SELECT home_id FROM inventory
  WHERE date = '2026-09-01' AND status = 'available'"
# Expected: SCAN inventory          ← index NOT used (no leading 'city' filter)
# Lesson: column order in a compound index is part of the contract,
#         not just a stylistic choice.

# --- Step 5: covering index — add home_id to the index payload ---
sqlite3 airbnb.db "DROP INDEX idx_city_date_status"
sqlite3 airbnb.db "CREATE INDEX idx_covering ON inventory(city, date, status, home_id)"
sqlite3 airbnb.db "EXPLAIN QUERY PLAN
  SELECT home_id FROM inventory
  WHERE city = 'Honolulu' AND date BETWEEN '2026-09-01' AND '2026-09-05'
    AND status = 'available'"
# Expected: SEARCH inventory USING COVERING INDEX idx_covering
# Lesson: 'COVERING' = entire query answered from the index pages,
#         no need to visit the base table at all.
```

**PowerShell variant** (Windows): wrap each `sqlite3 ... "<sql>"` in PowerShell — the syntax is the same, but use double quotes carefully or pipe the SQL via `Get-Content`.

**Bonus benchmark:** seed 10K homes × 365 days = 3.65M rows, then run the search query 1000× with and without the index:

```bash
# scaffold/scripts/bench_index.py is provided — run it like:
python scripts/bench_index.py --rows 3650000
# Expected output (rough order of magnitude):
#   Without index:  ~800ms / query
#   With index:     ~2ms   / query
#   Speedup:        ~400×
```

## Suggested Tech Stack

Python + FastAPI + SQLite + SQLAlchemy recommended, but you may use any language/framework. The scaffold uses these.
