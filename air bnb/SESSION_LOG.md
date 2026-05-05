# Session Log — Airbnb Booking Platform Exercise

完整紀錄這個練習從零到完成的過程。日期:2026-05-04 ~ 2026-05-05。

---

## 目標

依照 [Design Airbnb Booking Platform PDF](Design%20Airbnb%20Booking%20Platform%202fd90cd3ccd88030aef2d0b94f6d0dc0.pdf),
參考既有的 [qr_code_generator](../qr_code_generator/) 練習結構,設計一個對應的程式設計練習題。

PDF 涵蓋:Functional/Non-functional requirements、API design、High-level design、4 個 Deep Dives
(concurrent booking、search latency、peak season、geo search)。

---

## 階段 1: 文件設計 (3 份核心文件)

仿照 qr_code_generator 的 README / PROMPT / NOTES 三件套,加碼一份 TEACHING.md 作為跨題總結教材。

| 文件 | 角色 |
|---|---|
| [README.md](README.md) | 入口、雙軌制 (Challenge / Guided)、scaffold 安裝步驟 |
| [PROMPT.md](PROMPT.md) | 規格、5 題 Design Questions、curl 驗證測試 |
| [NOTES.md](NOTES.md) | 5 題的延伸 Q&A 與答案模板 |
| [TEACHING.md](TEACHING.md) | 跨題串聯的總結教材,1 張圖 + 5 題心法 |

**5 題 Design Questions (從 PDF 提煉):**
1. **Inventory Schema** — Per-day rows vs Range rows
2. **Concurrent Booking** — Pessimistic Lock / Reserved+TTL+Cron / Logical Availability
3. **Search Latency / Index Strategy** — Compound / Partial / Covering + Elasticsearch / Materialized cache
4. **State Machine + Idempotency** — reserved → paid → booked + webhook replay
5. **Read-Heavy Caching** — Why home detail OK to cache, search results not

---

## 階段 2: Scaffold (FastAPI + SQLite + 互動 UI)

完整可跑的 playground,每個 Q 都能即時切換設計選項看行為差異。

### 目錄結構
```
scaffold/
├── requirements.txt          # FastAPI 0.115, SQLAlchemy 2.0, uvicorn, jinja2
├── .gitignore                # *.db, .venv/, __pycache__/
└── app/
    ├── __init__.py
    ├── main.py               # FastAPI bootstrap + 自動 seed small + 套用 index 策略
    ├── database.py           # SQLite + WAL mode + connection pool
    ├── models.py             # Home / Inventory / Booking / WebhookEvent
    ├── schemas.py            # Pydantic request/response
    ├── settings.py           # Runtime-toggleable: index_strategy / cache /
    │                         # concurrency_mode / idempotency_enforced / TTL
    ├── seed.py               # seed_small (50 房 × 30 天) / seed_large (10K × 365)
    ├── search.py             # Search query 實作 (per-day rows GROUP BY HAVING COUNT)
    ├── booking.py            # try_reserve (logical / naive) + confirm_payment +
    │                         # finalize_booking + cancel_booking
    ├── cache.py              # In-process HomeDetailCache (dict + Lock + TTL)
    ├── indexes.py            # apply_index_strategy: DROP/CREATE 各種 index +
    │                         # explain_search 包 EXPLAIN QUERY PLAN
    ├── bench.py              # Run search query N 次 across 5 個 strategy
    ├── routes.py             # 所有 HTTP route
    └── templates/
        └── index.html        # 6-tab UI (Q1..Q5 + Admin)
```

### 啟動
```powershell
cd "air bnb/scaffold"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8765
# 開 http://localhost:8765
```

選 8765 而非 8000 是因為 8000 跟 qr_code_generator 衝突。

---

## 階段 3: UI 重新設計 (從一頁式 → 6 tab)

最初做成單頁 + sidebar settings,使用者反映「混亂、不知道測什麼」。
重新設計成 6 個分頁,每題獨立,焦點明確。

### 每個 tab 的設計
| Tab | 主題 | 主要互動 | 即時切換 |
|---|---|---|---|
| **Q1** | Inventory Schema | Per-day vs Range 對比按鈕 + ms 結果並排 | bookings 目標筆數 (0–200K) |
| **Q2** | Concurrent Booking | 🏁 Race A vs B 按鈕 + 雙欄結果 | logical / naive concurrency mode |
| **Q3** | Index Strategy | 單次 search / EXPLAIN / Bench 三按鈕 | none / simple / compound / covering / partial |
| **Q4** | State Machine | 1️⃣ Reserve → 2️⃣ Confirm Pay → 3️⃣ Finalize 三段 + Replay + Cancel | idempotency on/off |
| **Q5** | Caching | GET 1 次 / GET × 10 / 🏁 Compare on/off (各 100 次) | cache on/off |
| **Admin** | 資料管理 | Seed Small/Large / Reset / Reset bookings only / TTL | (read-only settings 顯示) |

---

## 階段 4: 關鍵 Bug 修復與功能升級

按時間順序記錄遇到的問題跟修法。

### Bug 1: Index 策略切換後 EXPLAIN 仍顯示舊 index
- **症狀**:PATCH index_strategy=none 後,EXPLAIN QUERY PLAN 仍顯示之前的 index
- **根因**:SQLAlchemy 連線池快取了 prepared statement,沒看到 schema 變化
- **修法**:`apply_index_strategy` 完成後 `engine.dispose()` 讓所有連線重建
- 檔案:[scaffold/app/indexes.py](scaffold/app/indexes.py)

### Bug 2: Naive 模式並發訂房沒有真正示範 double booking
- **症狀**:naive 模式下按 🏁 Race,結果跟 logical 一樣 (一個成功 + 一個 reject),
  看不到 race 的破綻
- **根因**:SQLite 有 database-level write lock,加上每個 SQL < 1ms,race window
  在實務上幾乎不會對撞
- **修法**:
  1. `try_reserve()` 已經有 `artificial_delay_ms` 參數,但 `routes.py` 沒傳
  2. `/home/book` 加 `?delay_ms=` query param (0–2000)
  3. UI 在 naive 模式自動帶 `?delay_ms=200`,在 SELECT 跟 UPDATE 中間 sleep
  4. UI 對撞發生時自動偵測 + 顯示 "DOUBLE BOOKING DETECTED" 警告
- 檔案:[scaffold/app/booking.py](scaffold/app/booking.py),
  [scaffold/app/routes.py](scaffold/app/routes.py),
  [scaffold/app/templates/index.html](scaffold/app/templates/index.html)

### Bug 3: Q4 三段流程被合併成一步,看不到 paid 中間狀態
- **症狀**:按 Confirm Pay 後 booking 直接從 reserved 跳到 booked,看不到 paid
- **根因**:原本 `confirm_payment` 一步做完 reserved → paid → 內部 finalize
- **修法**:拆成兩個 endpoint
  - `/confirm` 只做 reserved → paid (Stripe ack 的 single-row UPDATE)
  - `/finalize` 做 paid → booked (純內部 atomic transaction 更新 inventory)
- UI 改成 3 個獨立按鈕 + 4-step pipeline 視覺化
- 檔案:[scaffold/app/booking.py](scaffold/app/booking.py),routes.py,index.html

### Bug 4: Q4 pipeline 顯示在反覆操作後狀態不一致
- **症狀**:已 booked 後再按 Confirm Pay,pipeline 出現「booked 亮黃 + paid 沒亮綠」
- **根因**:每個按鈕 hard-code `_setStep('paid', ['reserved'])`,沒看實際 status
- **修法**:`_renderPipeline(status)` 改成根據 booking.status 自動推導 done/active 集合
- 加 🔄 Reset Q4 按鈕讓使用者隨時清乾淨重來
- 檔案:[scaffold/app/templates/index.html](scaffold/app/templates/index.html)

### Bug 5: Q4 房源下拉沒載入
- **症狀**:Q4 step 2 房源 select 變空
- **根因**:`loadHomesForCity` 用 `/home/search`,被 availability 過濾;跑過幾次 demo
  booking 後對應日期就沒可訂房,下拉就空了
- **修法**:新增 `/api/homes?city=` 列出該 city 所有房 (不管 availability),
  Q1/Q4/Q5 共用
- 檔案:[scaffold/app/routes.py](scaffold/app/routes.py),index.html

### Bug 6: Q1 對比差距不明顯
- **症狀**:per-day vs range 對比結果只差 2 倍,看不出設計差別
- **根因**:auto-seed 的 50 筆 booking 不夠 + 散布在 query 範圍內,
  range query 的 NOT EXISTS 一找到 overlap 就 short-circuit
- **修法**:
  1. 加 `target_bookings` 參數 (0–200K),分批 INSERT (500/batch)
  2. Seed booking 故意放到 query window **之後** (offset > nights),
     強迫 NOT EXISTS 掃完該 home 全部 booking 才能斷定「沒 overlap」
  3. UI 加「bookings 目標筆數」輸入欄 + Reset bookings (only) 按鈕
- 結果:從 2× 拉到 50K bookings 時 **461× 差距**
- 檔案:[scaffold/app/routes.py](scaffold/app/routes.py),index.html

### 升級 1: Q5 加累積時間 + on/off 一鍵對比
- **觸發**:單次 GET 的 ms 差距太小 (5ms vs 0.05ms),看不出 cache 真實效益
- **新增**:
  1. 累積 stats panel (持續累加每次 GET 的 ms,顯示 hit/miss 各別平均 + 省了多少)
  2. 🏁 Compare cache on vs off (各跑 100 次同房 GET) 按鈕
- 結果:100 次 GET 對比 — Cache ON 3.11ms / Cache OFF 70.3ms = **22.6× 差距**
- 檔案:[scaffold/app/templates/index.html](scaffold/app/templates/index.html)

---

## 階段 5: Socratic 複習 (Q1–Q5 全部跑過)

跟使用者用對話方式把 5 題的核心概念串過一遍,確認理解 + 補足模糊地方。

### Q1 — Per-day vs Range 為什麼前者贏
- 使用者一開始直覺選 B (range),理由是「省儲存」
- 帶他看搜尋 query 的形狀:per-day 是 `equality + range + equality` → B-tree 完美對齊;
  range 是 `start < end AND end > start` 兩欄位 range 比較 → B-tree 結構性無能
- 心法:**schema 選哪個看「主流量的 query 形狀能不能讓 DB 用 index 跑快」**

### Q2 — Logical vs Naive 並發控制
- 使用者抓到關鍵 insight:「過期判斷由下一個來搶的人自己做,不靠 cron」
- 補足深層原則:**系統的正確性不該依賴另一個元件按時運作** (cron 從關鍵路徑拿掉)
- Conditional UPDATE 為什麼 atomic:DB 把 WHERE+SET 放在同一個 lock 內評估,
  第二個 UPDATE 拿到 lock 時 WHERE 重新評估自然 false → 自動 reject
- 銀行櫃台類比:logical 是「同時查+操作的單一指令」,naive 是「兩張單子中間有縫」

### Q3 — 5 種 Index 策略
- 5 種策略一覽 + 兩個核心概念 (leftmost prefix + selectivity)
- 兩個進階技巧 (partial = 末尾 WHERE,covering = 欄位塞 SELECT 用的)
- 殺手鐧:partial + covering 疊加
- **「不回表」**展開講清楚:secondary index 只存 (索引欄位 + PK),要 SELECT 其他欄位
  必須走階段 2 回主表撈 → random IO;covering 把 SELECT 欄位塞進 index → 完全跳過階段 2
- 何時超出 SQL index → Elasticsearch (inverted index 解 multi-facet) / 物化 cache

### Q4 — State Machine + Idempotency (使用者要求做完先停)
- 對 Q4 的複習進行到「為什麼三段?」就被使用者切到 Q5 的 UI 改善請求,
  Q4 完整複習未完成
- 但 Q4 的程式碼 + UI pipeline 都已經實作正確 (拆成 reserve / confirm-pay / finalize 三按鈕)

### Q5 — Cache 設計 (順便延伸成「cache 架構教學」)
- 解釋 hit rate / miss rate 是什麼、為什麼是 cache 的 KPI
- 4 層 cache 架構 (in-process / Redis / Redis Cluster / CDN)
- Cache-aside pattern + 各種進階 (read-through / write-through / write-back / refresh-ahead)
- 「Python df 在記憶體還是硬碟?」延伸題 — eager (pandas) vs lazy (Polars LazyFrame / Dask)
  + 怎麼用 `df.memory_usage(deep=True)` 跟 `psutil` 量
- 「怎麼實作 cache 把所有資料放快取?」:從 4 行 dict 到 cachetools TTLCache 到 Redis 的升級路徑

---

## 階段 6: Git History

所有變更在 fork `MarkRoy8888/build-moat-live-sessions` 的 `feat/airbnb-booking-exercise` 分支。

```
4706c68  feat(airbnb): naive race now reliably double-books + fix Q4 home dropdown
441dda7  feat(airbnb): Q1 compare scales to 200K bookings, range gap is now dramatic
250fef1  feat(airbnb): Q1 per-day vs range live ms comparison + Q4 pipeline state fixes
eab1601  feat(airbnb): redesign UI into 5 question-tabs and split confirm into pay+finalize
bf62668  docs(airbnb): use port 8765 instead of 8000 to avoid clash with QR exercise
bd78b9f  feat(airbnb): add Airbnb booking platform exercise with playground
52f471e  feat(airbnb): Q5 cache demo gains accumulator + on/off comparison button
```

PR 連結 (尚未開):
[https://github.com/MarkRoy8888/build-moat-live-sessions/pull/new/feat/airbnb-booking-exercise](https://github.com/MarkRoy8888/build-moat-live-sessions/pull/new/feat/airbnb-booking-exercise)

---

## 最終實測數字 (給你回看時對照)

### Q1 — Per-day vs Range
| Bookings | Per-day | Range | 倍數 |
|---|---|---|---|
| 2,000 | 0.17 ms | 1.81 ms | **10.9×** |
| 20,000 | 0.22 ms | 31.17 ms | **142.94×** |
| 50,000 | 0.17 ms | 77.16 ms | **461.47×** |

### Q3 — Index Strategy (small seed 1.5K rows)
| Strategy | Avg ms |
|---|---|
| none | 6.65 |
| simple | 2.01 |
| compound | 2.00 |
| covering | 2.56 |
| partial | 2.60 |
(資料小所以差距小;Seed Large 後 covering/partial 通常贏 5-10×)

### Q5 — Cache on vs off (100 calls)
| Mode | Total ms |
|---|---|
| Cache ON (1 miss + 99 hits) | 3.11 |
| Cache OFF (100 misses) | 70.30 |
**差距:22.6×**

### 程式碼總量
- TEACHING.md / NOTES.md / PROMPT.md / README.md 約 4500 行 (含中文教學)
- scaffold/ 約 2200 行 Python + 1100 行 HTML/JS
- 共 21 個檔案

---

## 操作偏好筆記 (使用者反饋)

- **不喜歡 AskUserQuestion 的選單**:測試教學中問「下一步要走哪個方向?」
  用 AskUserQuestion 跳出 2-4 選項時被使用者拒絕兩次。
  → 偏好用純文字寫問題讓使用者自由回答,維持對話節奏
  → 這條已存到 [memory/feedback_askuserquestion.md](../../../../.claude/projects/c--Users-LG-Documents-----build-moat-live-sessions-air-bnb/memory/feedback_askuserquestion.md)

- **修了直接重啟 server**:每次 code 變動後我都會自動 stop → restart server,使用者不需要每次手動操作

- **port 慣用 8765**:8000 跟 qr_code_generator 衝突,session 全程都用 8765

---

## 後續可以延伸的方向

如果未來想擴充這個練習:

1. **Q4 完整複習** (這次 session 沒跑完)
2. **加 Redis 取代 in-process cache** (示範 Layer 2 升級)
3. **加 Read replica** (主從複製,讀寫分離)
4. **加 真正的 cron 過期清理** (示範 logical availability 中 cron 作為清潔工的角色)
5. **加 Elasticsearch + CDC** (示範 Q3 的最後手段)
6. **支援 geo search** (PostGIS + 半徑搜尋)
7. **分散式 trace + Prometheus metrics** (production 觀測性)
