# Airbnb Booking Platform — 5 題串聯教學

這份文件把 Q1–Q5 拆解的 5 個設計決策**串成一條主線**，作為單獨教學使用。閱讀順序：

1. 先讀本文（45 分鐘讀完，理解整個系統的設計骨幹）
2. 再讀 [PROMPT.md](PROMPT.md) 的題目，自己嘗試回答
3. 卡關時翻 [NOTES.md](NOTES.md) 看延伸 Q&A
4. 想動手就跑 [scaffold/](scaffold/) 的 playground，切換不同設定觀察行為差異

---

## 故事線：你不是在設計 Airbnb，你在學一個更大的東西

整套練習表面上在設計 Airbnb 的訂房後端，但**真正在教的是**：

> **如何在「強 invariant（不准 double booking）+ 高讀流量（400K QPS）」的系統裡，讓讀路徑與寫路徑各自最佳化、又不互相破壞核心保證。**

這個 pattern 套用到所有 booking 系統、票券系統、庫存系統、購物車系統、限時搶購系統。Airbnb 只是載體。

---

## 系統的 4 個基礎設定

```
Domain:   guest 訂房（搜尋 + 看詳情 + 訂房）
規模:     10M DAU ≈ 400K QPS,read-heavy
SLA:      搜尋 < 500ms
Invariant: 同一房同一日期不可被訂兩次（no double booking, ever）
```

這 4 個設定串起來就決定了所有 5 題的答案 — **每一題都在回應這 4 個約束的某一面**。

---

## 一張圖看完 5 題

```
                    用戶請求
                       │
          ┌────────────┼────────────┐
          ↓            ↓            ↓
     [搜尋房源]     [看房詳情]    [訂房]
          │            │            │
          │            │            │
        Q3:         Q5:           Q2:
       Index    Cache home    Logical
      設計優化    detail      Availability
          │            │            │
          ↓            ↓            ↓
     Q1: per-day rows inventory schema
                       │
                       ↓
                    SQL DB
                       │
                       ↓ (跨系統)
                Q4: paid → booked
                  state machine
                       │
                       ↓
                    Stripe
```

每題的責任：

| 題 | 解決的核心問題 | 答案的精髓 |
|---|---|---|
| **Q1** | Inventory 怎麼存？ | Per-day rows，**用儲存成本換查詢簡單** |
| **Q2** | 並發訂房怎麼擋？ | Logical availability，**用狀態欄位取代 DB lock** |
| **Q3** | 搜尋怎麼快？ | Compound + partial + covering index 三層遞進 |
| **Q4** | 跨系統失敗怎麼辦？ | reserved → paid → booked 三段 checkpoint + idempotency |
| **Q5** | 高 QPS 怎麼擋？ | Cache home detail，**絕對不 cache search results** |

---

## Q1 — Inventory 表設計（決定下面所有事）

### 兩個選項

| | A. Per-day rows | B. Range rows |
|---|---|---|
| Schema | `(home_id, date, status)` | `(home_id, start_date, end_date, status)` |
| 1 房 12 個月 | **365 筆**（預先生成） | **0 筆**（空 = 全可訂） |
| 訂 9/1–9/5 | UPDATE 5 筆 | INSERT 1 筆 |

### 選 A 的三個層次理由

**1. Query 形狀對齊 B-tree index**

A 的搜尋是教科書級的 SQL：

```sql
WHERE city='HNL' AND date BETWEEN ? AND ? AND status='available'
```

→ **equality + range + equality**，普通 B-tree compound index 一次 seek 完成。

B 的搜尋是「區間 overlap 檢查」：

```sql
WHERE start_date < query_end AND end_date > query_start
```

→ **兩個欄位上的範圍比較**，B-tree 結構性不擅長（除非 PostgreSQL GiST 等特殊 index）。

**2. 付得起這個代價**

Read-heavy 系統 + < 500ms SLA → 把成本前置到寫入和儲存：

- 儲存成本 100× 浪費 = 一張可接受的支票
- 寫入放大 5× = 訂房又不是高頻操作
- 換來的是搜尋走「DB 最會做的事」

**3. 避開特例邏輯**

「Row 不存在 = 可訂」會讓 search 變 LEFT JOIN + COUNT NULL；
統一「每天都有 row，差別只在 status」之後，搜尋就是純 INDEX SCAN。

### 心法

> **系統設計裡 schema 的選擇不是看哪個漂亮，是看主流量的 query 形狀能不能讓 DB 用 index 跑快。**
>
> Read-heavy → 把成本前置到寫入和儲存。
> Write-heavy → 反過來。

### 反例提醒

Range 模型不是錯的，它在「車隊調度、會議室預約」這種**寫多讀少 + 查詢以衝突檢查為主**的場景反而更好。
**選錯模型不是因為 schema 笨，是因為沒對齊查詢形狀。**

---

## Q2 — 並發訂房（用狀態欄位取代 DB lock）

### 三個方案的真實取捨

| 方案 | 鎖什麼 | 鎖多久 | 致命傷 |
|---|---|---|---|
| (a) Pessimistic Lock | DB row | **整段付款（5–10 分鐘）** | DB lock 拿到「人類時間」→ connection pool 爆、deadlock、雪崩 |
| (b) Reserved + TTL + Cron | status 欄位 + expires_at | 即時 transaction | Cron 在關鍵路徑上 → cron 掛 = 熱門日期空窗無法訂 |
| **(c) Logical Availability** ✓ | 同 (b) 但**過期判斷由下一個 booking 自己做** | 即時 transaction | 沒有致命傷 |

### (a) 為什麼是「初學者陷阱」

DB 的 lock 機制是設計來扛 **微秒到毫秒級** 並發競爭的。
一旦你拿到「人類時間」尺度（5 分鐘付款），DB 就垮了：

1. **Connection pool 被佔住** → 整個服務雪崩，不只 booking
2. **WAL / undo log 膨脹** → DB 寫入全面變慢
3. **Deadlock 機率激增**：
   ```
   A 訂 9/1–9/5 → 拿 9/1, 9/2 鎖
   B 訂 9/4–9/6 → 拿 9/4, 9/5 鎖
   A 等 9/4, B 等 9/3 → 互等死掉
   ```
4. **PostgreSQL 沒有原生 transaction-level lock timeout** → 要在 application layer 自己寫 watcher

### (c) 的精髓：把 cron 從關鍵路徑上拿掉

```sql
-- (b) 物理可用性: 必須等 cron 把 expired 改回 available
WHERE status = 'available'

-- (c) 邏輯可用性: 過期 row 由下一個來搶的人自己 take over
WHERE status = 'available'
   OR (status = 'reserved' AND expires_at < NOW())
```

**Cron 在 (c) 只是清潔工**，掛掉只是 DB 視覺髒，**系統行為不變**。

### 並發安全靠 conditional UPDATE

```sql
UPDATE inventory
SET status='reserved', holder=:user, expires_at=NOW()+'10 min'
WHERE home_id=:h AND date BETWEEN :start AND :end
  AND (status='available' OR (status='reserved' AND expires_at < NOW()))

-- 然後檢查 affected rows == (end - start)
-- ✗ → 中途被別人搶走 → ROLLBACK
-- ✓ → 全部搶到 → 成功
```

不要用 `SELECT` then `UPDATE`，中間有 race condition。

### 心法

> **業務流程（含使用者互動、第三方 API）不該裝在 DB transaction 裡。**
>
> 拆成「短 transaction（DB 動作）+ 狀態欄位（中間狀態）+ conditional UPDATE（並發安全）」三件套。
>
> 適用場景遠不只訂房：購物車鎖庫存、寄信驗證碼、ticket 系統、線上排隊系統，全都是這個 pattern。

---

## Q3 — 搜尋延遲（5 層遞進工具）

### 5 層工具一覽

| 層級 | 工具 | 解什麼問題 |
|---|---|---|
| 1 | **Compound Index** `(city, date, status)` | Leftmost prefix → 一次 seek 切到結果集 |
| 2 | **Partial Index** `WHERE status='available'` | 99% query 帶同一個 filter → index 只服務這 99% |
| 3 | **Covering Index** `INCLUDE home_id` | 不回主表那一跳 |
| 4 | **Materialized Cache (Redis)** | 熱門 city 預先算好 |
| 5 | **Elasticsearch** | Multi-facet / fuzzy / 全文搜尋 |

### 第 1 層 — Compound Index 的核心物理事實

B-tree compound index 就是一本「**先按 col1 排，col1 一樣再按 col2，依此類推**」的電話簿。

兩條鐵律：

**(A) Leftmost Prefix Rule**
```sql
INDEX (city, date, status)

WHERE city = ?                    ✓ 用得到
WHERE city = ? AND date = ?        ✓ 用得到
WHERE date = ?                    ✗ 用不到（沒有 leading column）
WHERE status = ?                  ✗ 用不到
```

**(B) 排列順序的選擇準則**
- **永遠出現的欄位放最前面**（city → 第 1 位）
- **常出現但有時被省略的次之**（date → 第 2 位）
- **常被省略的放最後**（status → 第 3 位）
- **低選擇性欄位放前面是經典反模式**（`(status, city, date)` 是最差選擇）

### 第 2 層 — Partial Index：只服務「會被查到的 row」

```sql
CREATE INDEX idx_available
  ON inventory (city, date)
  WHERE status = 'available';     -- ← 末尾的 WHERE 讓它變 partial
```

當 99% query 都帶 `status='available'`：
- Index 體積砍半（只收 50% 的 row）
- 寫入更便宜（只有狀態進出 available 才動）
- 樹更淺、cache 命中率更高
- 對「99% 查 available」的 query **更快**

代價：MySQL 8.x 不支援，PostgreSQL / SQLite ✓。

### 第 3 層 — Covering Index：不回主表

普通 secondary index 走完 B-tree 拿到 PK 後，要**回主表撈整筆 row**才能拿到 SELECT 的欄位（叫「回表」）。

把 SELECT 用到的欄位塞進 index：

```sql
CREATE INDEX idx_covering ON inventory (city, date, status, home_id);
--                                                           ↑
--                                              把要 SELECT 的欄位塞進來
```

→ DB 從頭到尾只碰 index pages，**完全不回主表**。
→ EXPLAIN 顯示 `USING COVERING INDEX`。
→ 對命中 500 筆的 query：500 次 random IO 變 0 次。

進階版（PostgreSQL/SQL Server）：用 `INCLUDE` 子句，欄位放 leaf 不影響 B-tree 結構，更便宜。

```sql
CREATE INDEX idx_smart
  ON inventory (city, date, status)        -- ← B-tree key
  INCLUDE (home_id);                        -- ← 純粹 cover, 不影響排序
```

### 殺手鐧：Partial + Covering 疊加

```sql
CREATE INDEX idx_killer
  ON inventory (city, date, home_id)        -- ← covering: 含 home_id
  WHERE status = 'available';                -- ← partial: 只收 available
```

體積最小、寫入最便宜、讀取最快 — 真實 production booking 系統會用的招。

### 第 4 / 5 層 — 何時放棄 SQL index

SQL index 有 3 個結構性天花板：

1. **Query 形狀爆炸**（多 facet：城市 × 日期 × 價格 × 設施 × 評分 × ...）
2. **全文 / 模糊搜尋**（`LIKE '%靠近地鐵%'`、typo tolerance）
3. **複雜排序 + 聚合**（每次 query 都要算 score）

對應解法：

| 解法 | 解什麼問題 | 代價 |
|---|---|---|
| **Materialized Cache (Redis)** | 80/20 熱門 city 預先算好 | Invalidation 複雜、有 stale window |
| **Elasticsearch via CDC** | Multi-facet + fuzzy + 全文 | Eventual consistency、運維成本翻倍、新故障模式 |

**實務排序**：
```
SQL index → Read replica → Application cache → Elasticsearch
                                                   ↑
                                              最後手段
```

90% 工程師上 ES 都太早。

### 心法

> 1. **Index 順序看 query 形狀** — 哪個欄位最常給、最會切結果，就放最前面
> 2. **Index 不需要服務所有 row，只服務會被查到的** — partial index 的精髓
> 3. **Index 不只「找到 row」，可以「直接回答 query」** — covering index 的精髓
> 4. **訂房時必須回 Postgres 重新檢查可訂性** — 搜尋優先 availability，訂房優先 consistency

### 反模式總清單

```
❌ 對每個欄位建一條 single-column index（沒用滿 leftmost prefix 的能力）
❌ 把低選擇性欄位（status, is_deleted）放 leading position
❌ Covering 把 10 個欄位全塞（變成第二張表，寫入炸裂）
❌ 看到 search 慢就跳過 SQL 直上 Elasticsearch
```

---

## Q4 — 訂房狀態機 + 跨系統一致性

### 三個過渡態的存在理由

```
[User 點訂]
     ↓
available → reserved (10 min TTL)
     ↓
[跳到 Stripe 付款,User 輸入卡號]
     ↓
[Stripe 扣款,發 webhook]
     ↓
reserved → paid → booked
```

| 狀態 | 解什麼問題 |
|---|---|
| `reserved` | 「使用者還在輸入卡號 / 中途離開」的不確定態 |
| `paid` | 跨系統協作的 **checkpoint**：「我承認 Stripe 那邊扣到錢」 |
| `booked` | 自家 inventory 真的更新完了 |

### 為什麼 reserved 必要

沒有 reserved，跑 `available → booked` 一步：

| 情境 | 結果 |
|---|---|
| User A 點訂後關閉瀏覽器 | 房間鎖死，靠 cron 收屍 |
| Cron 收屍週期前的 30 分鐘 | B、C、D 看到「不可訂」走了，**真實營收損失** |
| `booked` 語意 | 被汙染：包含「真的訂走 / 正在輸卡號 / user 跑了」三種情況 |

→ Reserved 是「**未確定的承諾**」的具現化。
→ **任何「需要一段時間才能確定結果」的操作，都需要過渡態**。

同型 pattern 在哪：購物車、Email 驗證、排隊系統、領票系統、銀行轉帳、退款處理 — **全部一樣**。

### 為什麼 paid 和 booked 要拆

關鍵限制：**你不能跟 Stripe 開一個跨公司的 transaction**。

```
合一步: BEGIN
       UPDATE booking SET status='booked'
       UPDATE inventory SET status='booked' ...
      COMMIT
      回 200 給 Stripe
            ↑
        極小機率: COMMIT 中斷或回 200 中斷
        Stripe 沒收到 → 重發 webhook
        你又執行一次 → 重複 booking / 重複 inventory 更新 → 災難
```

拆成兩步：

```
Step 1 (atomic): UPDATE booking SET status='paid' WHERE id=? AND status='reserved'
                 ↑ 單行 conditional UPDATE,100% 原子
                 ↑ 第二次 webhook 進來 affected_rows=0 → 已處理過,直接回 200

Step 2 (純內部): BEGIN
                 UPDATE booking SET status='booked'
                 UPDATE inventory SET status='booked' ...
                 COMMIT
                 ↑ 純內部 transaction,自家世界裡 100% 原子
                 ↑ 失敗?cron 掃 paid 但未 booked 的訂單,重做 Step 2
```

**`paid` 是 checkpoint** — 一個明確的標記說「外部世界已確認，剩下的是自家工作」。

### Idempotency：Webhook 重複是設計需求

Stripe 文件白紙黑字：webhook may be delivered multiple times for the same event。

雙保險：

```sql
-- 保險 1: Conditional UPDATE
UPDATE booking SET status='paid'
WHERE id=? AND status='reserved'
-- 第二次 affected_rows=0 → 已處理

-- 保險 2: Event ID UNIQUE constraint
CREATE TABLE webhook_events (
  event_id VARCHAR PRIMARY KEY,    -- ← UNIQUE = idempotency
  payload TEXT,
  result JSON
);

INSERT INTO webhook_events (event_id, payload) VALUES (?, ?)
-- 第二次 IntegrityError → 回上次的 result
```

### 心法

> **跨系統互動沒有真正的 atomic，只有「protocol + checkpoint + idempotency」。**
>
> - 每個獨立系統內部用 atomic transaction 做小範圍 commit
> - 系統之間用 webhook + retry
> - Checkpoint 用 conditional UPDATE + state column 表達「我已收到外部確認」
> - Idempotency 用 event_id + UNIQUE constraint 兜底

---

## Q5 — Read-Heavy Caching

### Cache 適不適合的 4 維度判斷

| 維度 | Home Detail | Search Results |
|---|---|---|
| 1. 變動頻率 | 極低 ✓ | 極高 ✗ |
| 2. Key 形狀 | `home:{id}` ✓ | 組合爆炸 ✗ |
| 3. Stale 後果 | 邊緣 UX ✓ | 商業災難 ✗ |
| 4. Invalidation | 確定可控 ✓ | 不可能列舉 ✗ |

**Home detail 全過，Search results 全炸**。

### Search Results Cache 的 3 大災難

**災難 1：Key 爆炸 → Hit rate 趨近 0**

```
search?city=HNL&start=9/1&end=9/5&pageSize=10&page=1
search?city=HNL&start=9/1&end=9/5&pageSize=10&page=2     ← key 不一樣
search?city=HNL&start=9/1&end=9/6&pageSize=10&page=1     ← key 不一樣
search?city=HNL&start=9/1&end=9/5&minPrice=100           ← key 不一樣
... (cities × dates × pages × filters = 幾乎無限)
```

**災難 2：Invalidation 不可能**

A 訂了 H1, 9/1–9/5 → 要 invalidate 哪些 search key？
- 涵蓋 9/1–9/5 任一天的 city=HNL 所有日期組合
- 加上各種 filter / page / pageSize 變體
- **不可能列舉**。只能整片 flush（打死無辜 cache）或設 5 秒 TTL（基本沒效果）

**災難 3：Stale 騙使用者**

```
10:00:00  X 訂 H1
10:00:01  Cache 還沒 invalidate
10:00:02  Y 看到 cache 顯示 H1 available  ← 騙人
10:00:30  Y 點訂 → 系統檢查 → 拒絕
10:00:31  Y 流失 → 商業災難
```

### Search 怎麼降 DB 負擔（不靠 cache 結果）

1. **Read replica** — 寫只走 master，讀分散到 replica
2. **窄結構 cache** — cache `city → home_ids list`（穩定子部分），application 層做 date filter
3. **Elasticsearch 卸載** — 複雜 query 給 ES，訂房時回 Postgres 重新檢查
4. **熱點窄 cache** — 對「最熱 query shape」（HNL 接下來 7 天第 1 頁）做窄 60 秒 cache

### 心法

> 1. **Stale 容忍度看業務語意，不看時間長度**
>    - Home detail 容忍 1 小時
>    - Inventory 連 1 秒都不該（在「可訂性」上騙使用者 = UX 災難）
> 2. **Cache 的對象是「穩定狀態」不是「查詢結果」**
>    - Cache 構成查詢結果的子部分，application 層重組
> 3. **短 TTL ≠ 安全** — 5 秒內可以發生 N 個訂房

---

## 跨題終極總結：1 個底層哲學

5 題都在說同一件事：

> **在 read-heavy + write-critical 系統裡，把 invariant 守在 critical path 的最後一筆 atomic write，其他地方都允許 eventual consistency 換性能。**

### 對應每題的具體落地

| 題 | 「允許 eventual consistency 換性能」在哪 | 「Invariant 守在 atomic write」在哪 |
|---|---|---|
| Q1 | 預先生成 inventory rows、寫入放大 | Search 用 index 跑得快 |
| Q2 | Search 看到的 availability 可能比真實晚幾 ms | Booking 的 conditional UPDATE 是 atomic checkpoint |
| Q3 | Elasticsearch 跟 Postgres 之間有秒級延遲 | 訂房時必須回 Postgres 重新檢查 |
| Q4 | reserved / paid 中間態允許 in-flight 不確定 | `paid → booked` 是純內部原子 transaction |
| Q5 | Home detail cache 可以 stale 1 小時 | Inventory 永遠不 cache，可訂性必須查 source of truth |

### 反過來看：「壞答案」的共同模式

每一題的「壞答案」都是**用一致性換性能，但忽略了 invariant 被破壞的代價**：

| 題 | 壞答案 | 為什麼壞 |
|---|---|---|
| Q1 | Range 模型省儲存 | 搜尋變慢 → 違反 < 500ms SLA |
| Q2 | Pessimistic lock 確保 invariant | 整個系統 throughput 崩潰 |
| Q3 | 直接上 Elasticsearch 跳過 SQL | 過度工程 + eventual consistency 進關鍵路徑 |
| Q4 | 跳過 reserved 直接 booked | 中途離開的使用者把房間永遠佔住 |
| Q5 | Cache search results | 「可訂性」騙使用者 → 商業災難 |
| Q4 | Webhook 不做 idempotency | 重複扣款 |

每一題的「好答案」都共享同一個哲學：**精準辨識哪一個 atomic write 守住 invariant，圍繞它用 eventual consistency 換性能**。

---

## 適用範圍（這套思維能幫你設計什麼？）

把 Airbnb 換掉，5 個分離的決策模式適用於：

| 系統 | Q1 模式 | Q2 模式 | Q3 模式 | Q4 模式 | Q5 模式 |
|---|---|---|---|---|---|
| 票券系統（演唱會、機票） | Per-seat per-show | Logical hold | Multi-facet 搜尋 | reserved → paid | Cache 演唱會詳情 |
| 限時搶購 | Per-SKU inventory | Conditional decrement | Hot product index | reserved → paid → fulfilled | Cache 商品 + 不 cache 庫存 |
| 餐廳訂位 | Per-table per-time-slot | Logical hold | 地理 + 時段 facet | reserved → confirmed | Cache 餐廳資料 |
| 線上掛號 | Per-doctor per-slot | Logical hold | 醫科 + 時段 facet | reserved → 確認 | Cache 醫師資料 |
| 雲端 VM 配額 | Per-region per-instance-type | Conditional alloc | 多維度配額 | reserved → provisioned | Cache 規格資料 |

→ **這 5 個模式串起來就是 booking-class system 的通用解法**。

---

## 下一步建議

1. **試動手**：跑 [scaffold/](scaffold/) 的 playground，觀察切換不同設計選項時系統行為的差異
2. **看 EXPLAIN**：對 inventory 表跑 `EXPLAIN QUERY PLAN`，看不同 index 的查詢計畫
3. **延伸閱讀**：
   - 票券系統設計（Ticketmaster, hotel booking）
   - 限時搶購系統（Black Friday, 雙 11）
   - 雲端配額管理（AWS EC2 capacity reservation）
4. **挑戰練習**：
   - 把 Q4 的 `paid → booked` 拆成 saga + outbox pattern
   - 為 Q3 加 ES + CDC，觀察「搜尋顯示可訂、訂房被擋」的真實 race
   - 為 Q5 加 cache stampede 防護（singleflight / probabilistic early refresh）

---

**閱讀完成。** 接下來打開 [PROMPT.md](PROMPT.md) 自己嘗試，遇到不懂再回來查 [NOTES.md](NOTES.md)。
