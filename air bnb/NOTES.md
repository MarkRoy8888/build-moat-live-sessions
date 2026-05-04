# Airbnb Booking Platform — 設計討論延伸筆記

這份筆記整理 5 題 Design Questions 的**延伸 Q&A 重點**,作為寫 `PROMPT.md` 答案時的參考素材。原題目見 [PROMPT.md](PROMPT.md)。

---

## Q1: Inventory Representation —「每天一筆」 vs「區間一筆」

### 兩種模型一次看清

| 維度 | Per-day rows<br>`(home_id, date, status)` | Range rows<br>`(home_id, start, end)` |
|---|---|---|
| 1 個房 12 個月 | **365 筆** | 0 筆(空 = 全可訂) |
| 預訂 9/1–9/5 | UPDATE 5 筆 | INSERT 1 筆 |
| 「9/3 是否可訂?」查詢 | **單筆 PK lookup** | 掃 ranges 找 overlap |
| 「9/1–9/5 全可訂?」查詢 | **range scan + COUNT = 5** | 掃 ranges 找 overlap,複雜 |
| 「未來一年 Honolulu 任一天可訂的房」 | **複合 index 直接掃** | 對每個 home 都要展開 ranges,慢 |
| 寫入放大 | 高(1 訂房 = N 筆 update) | 低(1 訂房 = 1 筆 insert) |
| 儲存成本 | 高(N × homes × days) | 低 |

### 核心取捨:**用寫入成本換查詢簡單**

短租平台的特徵:
- **讀寫比極度傾斜**(搜尋 / 看房遠多於訂房)
- 搜尋語意是「整段日期都要可訂」 — range overlap 的 SQL 很難寫快
- 旺季要扛 400K QPS,搜尋必須是「**簡單 range scan + filter**」才能 < 500ms

→ 把成本前置到「每天一筆 + 預先生成」,讓搜尋變成 **`WHERE city=? AND date BETWEEN ? AND ? AND status='available'` + COUNT** 這種 DB 最擅長的查詢。

### 「為什麼要預先生成?生成到何時?」

**預先生成**是因為:如果 row 不存在就視為 available,搜尋邏輯會變成「LEFT JOIN + COUNT NULL」這種痛苦寫法;統一「每天都有 row,差別只在 status」之後,搜尋就是純 INDEX SCAN。

**生成到 6–12 個月**:
- 太短:使用者想訂明年春節找不到 → UX 差
- 太長:儲存成本爆炸,且大部分日期永遠不會被查

實務做法:nightly cron 把「滾動視窗」往前推一天。每天為每個 home 多生 1 筆,刪掉今天之前的。

### 反直覺:**Range 模型適合什麼?**

不要把「per-day rows」當作絕對正解。Range 模型在以下場景更好:
- **資源池**(車隊、會議室)寫入頻繁、查詢以「特定時段是否衝突」為主
- **排班系統**(醫生班表)以區間為自然單位
- 沒有「未來 N 天空房列表」的搜尋需求

短租 / 飯店訂房選 per-day,因為**搜尋面向才是業務核心**,訂房只是搜尋之後的轉換步驟。

### 答案模板

> 採 per-day rows (`home_id, date, status`)。代價是寫入放大(1 訂房 = N 筆 UPDATE)與儲存成本(N homes × 365 days),換來的是搜尋查詢可以退化成「複合索引 range scan + COUNT」這種 DB 最擅長的形式 — 在 read-heavy + < 500ms 延遲要求下,這是值得的取捨。
>
> 預先生成 6–12 個月的 inventory,nightly cron 滾動推進,避免「row 不存在」與 LEFT JOIN 的特例邏輯。Range 模型適合寫多讀少、且查詢以「衝突檢查」為主的場景(車隊、會議室),不適合本系統。

---

## Q2: Concurrent Booking — 三種策略的真實取捨

### 三種方案一次比較

| 方案 | 鎖什麼 | 鎖多久 | Cron 角色 | Cron 掛掉的後果 |
|---|---|---|---|---|
| (a) Pessimistic Lock | DB row | **整個付款流程**(可能 5 分鐘) | 無 | N/A |
| (b) Reserved + TTL + Cron | 無(用 status 欄位) | 即時 transaction | **權威** — 過期不掃就一直被佔 | **booking 系統卡住,熱門日期無法被訂** |
| (c) Logical Availability + Cron 當清潔工 | 無 | 即時 transaction | **僅整理** — 過期 row 邏輯上已可被搶 | **行為不變**,只是 DB 看起來髒 |

### 為什麼 (a) 在實務上幾乎都是壞主意

長時間持有 transaction 的代價,**不只是慢**:

```
1. DB connection pool 被佔住 → 其他無關 query 也排隊
2. WAL / undo log 膨脹 → DB 整體效能下滑
3. Lock contention 隨並發量平方成長
4. Deadlock 機率激增
```

#### Deadlock 例子(來自原 PDF)

```
User A 想訂 09/01–09/05 → 拿到 09/01, 09/02 的 lock
User B 想訂 09/04–09/06 → 拿到 09/04, 09/05 的 lock
User A 等 09/04 → 卡住
User B 等 09/03 → 卡住
                    ↑
                deadlock
```

**且 PostgreSQL 沒有原生 transaction-level 的 lock timeout**。要做 timeout 必須在 application layer 加 watcher(額外複雜度)。

### (b) → (c) 是同一條路上的進化

兩者都用 `status` 欄位 + `expires_at` 欄位。差別在「**過期 row 的處理時機**」:

**(b) 是「物理可用性」:**
```python
# Booking 嘗試
if row.status != 'available':
    raise Conflict
```
→ 一定要等 cron 把 expired 的 row 改回 available,後續 booking 才能搶。Cron 延遲 5 分鐘 = 熱門日期空窗 5 分鐘無法被訂。

**(c) 是「邏輯可用性」:**
```python
# Booking 嘗試
BEGIN
  if row.status == 'available' OR (row.status == 'reserved' AND row.expires_at < NOW()):
      UPDATE row SET status='reserved', expires_at=NOW()+10min, holder=current_user
      COMMIT
  else:
      ROLLBACK; raise Conflict
```
→ Cron 還沒來?沒關係,下一個來搶的人**自己**就會把過期 reservation 翻掉。Cron 只是減少 DB 上「視覺髒污」(看到很多過期 reserved row)的清潔工,**不在關鍵路徑上**。

### 「Cron 從關鍵路徑上拿掉」是這題的精髓

**這是一個非常常見的系統設計手法**:

> 任何時候你寫了「靠 cron 把狀態翻回去才能正常運作」的設計,都該停下來想:**能不能把這件事推到下一個進入 critical section 的請求順手做掉?**

例子不只 booking:
- 過期的 session token → 下次認證時順手清掉,不靠 cron 掃
- 過期的 cache entry → lookup 時檢查 timestamp,不只靠 TTL 過期事件
- 過期的 lock → 持有者過期就視為釋放,不靠 watchdog

**邏輯狀態 > 物理狀態,是一種防禦性設計** — 你的系統行為不依賴另一個元件按時運作。

### 並發安全靠什麼?

(c) 還需要 DB 層的並發保證,不能只靠 SELECT 後 UPDATE(中間有 race condition):

```sql
-- 方案 1: 用 conditional UPDATE,讓 DB 來保證 atomic
UPDATE inventory
SET status = 'reserved', expires_at = NOW() + INTERVAL '10 min', holder = :user
WHERE home_id = :h AND date BETWEEN :start AND :end
  AND (status = 'available' OR (status = 'reserved' AND expires_at < NOW()))

-- 然後檢查 affected rows 是否等於 (end - start)
-- 不等於 → 有人搶先了 → ROLLBACK
```

```sql
-- 方案 2: SERIALIZABLE isolation level + retry on serialization failure
```

兩者都不需要長時間持有 lock。

### 答案模板

> 採 (c) **logical availability + cron as janitor**。
>
> 拒絕 (a) pessimistic lock:長時間持有 transaction 會佔 DB connection、放大 WAL、造成 lock contention 與 deadlock(典型例:A 訂 9/1–9/5 拿到 9/1–9/2 鎖,B 訂 9/4–9/6 拿到 9/4–9/5 鎖,互等對方);PostgreSQL 又沒有原生 transaction-level lock timeout。
>
> 拒絕單純 (b):cron 在關鍵路徑上,cron 掛 = 熱門日期空窗無法被訂。改用 (c) 後,booking 在進 critical section 時自己判斷「row 是否邏輯上可用」(`available` 或 `reserved AND expired`),用 conditional UPDATE 一次到位;cron 只是清潔工,延遲 / 故障**不影響系統行為**。
>
> 並發安全靠 DB 的 conditional UPDATE 加 affected rows 檢查(或 SERIALIZABLE + retry),不靠 SELECT-then-UPDATE。

---

## Q3: Search Latency —「索引 / Elasticsearch / 物化快取」三選一?

### 先問:**先慢在哪?**

```sql
SELECT home_id FROM Inventory
WHERE city = 'Honolulu'
  AND date BETWEEN '2026-09-01' AND '2026-09-05'
  AND status = 'available'
GROUP BY home_id
HAVING COUNT(*) = 5
```

10M listings × 365 days = **36 億筆 inventory rows**。沒有 index → full table scan,絕對死。

### 三個方案,適用情境完全不同

| 方案 | 解什麼問題 | 不解什麼問題 | 何時導入 |
|---|---|---|---|
| (a) 複合 index `(city, date, status)` | DB 內部存取路徑 | 全文搜尋、模糊比對、複雜 facet | **第一個該做的事** |
| (b) Elasticsearch via CDC | 全文搜尋、facet aggregation、複雜布林查詢 | DB 一致性(eventual)、運維成本 | 當需求超出 SQL,例如「面海 + 寵物友善 + 包早餐」這種 multi-facet | 
| (c) 物化「city → 可用日期 bitmap」cache | 把熱門 city 的 read 完全擋在 DB 之前 | 冷門 city / long tail / 寫入放大 | 讀寫比極端傾斜、且 query shape 高度集中 |

### (a) 索引的隱藏代價

> 索引不是免費的午餐 — **每筆 INSERT / UPDATE 都要同步更新所有相關索引**。

→ 訂房時 update 5 筆 inventory row × 3 個索引 = 15 次索引寫入。
→ 但對 read-heavy 系統,這是值得的。
→ **守則**:索引服務的是查詢,先有 query plan 再加 index;不要先建一堆 index 等查詢過來。

### Secondary Index 的核心知識(這題的重點)

#### Primary vs Secondary Index

| | Primary Index | Secondary Index |
|---|---|---|
| 數量 | 每張表 1 個 | 每張表 N 個 |
| 結構 | 直接決定 row 在磁碟上的順序(InnoDB clustered) | 額外的 B-tree,leaf 存「索引欄位 + PK」 |
| Lookup 路徑 | 一次 B-tree 走訪就到 row | 一次 B-tree 找到 PK → 再回 primary 表撈 row(**回表 / bookmark lookup**) |
| 為什麼這重要 | 預設用 PK lookup 最快 | Secondary index 隱含「**多走一跳**」,covering index 可以避免 |

#### 複合 Index 的 leftmost prefix 規則(必懂)

對 `INDEX (city, date, status)`:

```sql
-- 用得到 index:
WHERE city = ?                           -- ✓ 用 city
WHERE city = ? AND date = ?              -- ✓ 用 (city, date)
WHERE city = ? AND date = ? AND status = ?  -- ✓ 用 (city, date, status)
WHERE city = ? AND status = ?            -- ✓ 用 city,然後 filter status(部分用到)

-- 用不到 index:
WHERE date = ?                           -- ✗ 沒有 leading column
WHERE status = ?                         -- ✗ 沒有 leading column
WHERE date = ? AND status = ?            -- ✗ 沒有 leading column
```

**物理直覺**:複合 index 在 B-tree 裡的排序是「先 city,city 相同再比 date,date 相同再比 status」。這像電話簿先按姓排再按名 — 你查「所有姓陳的」很快,查「所有名字叫志明的」就要翻整本。

**設計守則**:
- 把**選擇性最高(最區分性)**的欄位放最前面
- 把**最常作為 equality filter** 的欄位放最前面
- range filter(`BETWEEN`、`>` 等)後面的欄位**用不到 index**(只能拿 prefix 走 index 找到範圍,後面的欄位就只能 filter)

→ 對 booking 系統,`city` (equality, 選擇性中等) → `date` (range) → `status` (equality, 選擇性低) 順序合理。

#### Covering Index — 把回表那一跳省掉

如果 query 要的所有欄位**都在 index 裡**,DB 可以直接從 index 拿結果,不用回 primary 表。

```sql
-- 普通 secondary index
CREATE INDEX idx_a ON inventory(city, date, status);
SELECT home_id FROM inventory WHERE city = 'HNL' AND date = '2026-09-01';
-- 走 idx_a 找到 PK → 回 inventory 表撈 home_id  ← 多一跳

-- Covering index
CREATE INDEX idx_b ON inventory(city, date, status, home_id);
SELECT home_id FROM inventory WHERE city = 'HNL' AND date = '2026-09-01';
-- 走 idx_b 找到後直接拿 home_id  ← 不用回表
-- EXPLAIN: USING COVERING INDEX
```

**代價**:index 變大(多存 home_id),寫入更慢一點。
**收益**:讀取省一個 random IO,大表上差很多。

對 booking 搜尋,`(city, date, status, home_id)` 這個 covering index 是教科書級的設計,因為:
- 搜尋只要 home_id list,然後再用 PK 撈 home detail(可 batch)
- Inventory 表寫入頻率還算低(預先生成 + 訂房時 update),多一個欄位的寫入成本可接受

#### Selectivity / Cardinality — 為什麼有 index 還是慢?

```sql
-- 100 萬筆 inventory,status 只有 2 種值(available / booked)
CREATE INDEX idx_status ON inventory(status);
SELECT * FROM inventory WHERE status = 'available';
-- DB 可能直接 full scan,不走 index!
```

**原因**:如果 `status='available'` 命中 60% 的 row,走 index 找到 60% 的 PK 然後回表 60 萬次,**比直接 sequential scan 100 萬筆還慢**(random IO 比 sequential IO 慢 100 倍)。

DB 的 query optimizer 會看 statistics 自己決定要不要走 index。**選擇性低的欄位單獨建 index 通常無效**。

→ 對 booking,**單獨對 `status` 建 index 是浪費**;但放在複合 index 的最後一位(`(city, date, status)`)是值得的(用來在已找到的小範圍內再 filter)。

#### EXPLAIN QUERY PLAN — 唯一可信的真相來源

不要靠**直覺**判斷 index 有沒有被用。每次都實際跑:

```bash
sqlite3 airbnb.db "EXPLAIN QUERY PLAN <your query>"
```

關鍵字解讀:
- `SCAN <table>` → 全表掃描 ❌
- `SEARCH <table> USING INDEX <idx>` → 走 index ✓
- `SEARCH <table> USING COVERING INDEX <idx>` → 走 index 且不用回表 ✓✓
- `USING INDEX FOR ORDER BY` → ORDER BY 也省了排序

PostgreSQL / MySQL 也都有對應的 EXPLAIN(語法不同),概念一致。**所有效能優化的第一步永遠是 EXPLAIN**。

### 本練習的測試怎麼設計

scaffold 提供 `scripts/bench_index.py`:
1. 種 3.65M 筆 inventory(10K homes × 365 days)
2. DROP index → 跑 1000 次搜尋查詢 → 計時
3. CREATE 複合 index → 再跑 1000 次 → 計時
4. 對比 EXPLAIN QUERY PLAN 輸出

預期**約 400× 加速**(1ms vs 400ms 級別)。
小資料(< 1K rows)看不出差別 — 這是 secondary index 的特性,**問題規模夠大才浮現**,所以練習要刻意種大量資料。

### (b) Elasticsearch:CDC 同步是真痛點

PDF 點到了但沒展開。實務上 ES + Postgres 的同步永遠是 **eventual consistency**:

```
Postgres: UPDATE inventory SET status='booked'
            ↓ Debezium / Kafka Connect 抓 WAL
            ↓ (50ms ~ 數秒延遲)
            ↓
Elasticsearch: 收到 update event,refresh index
            ↓ (refresh interval 預設 1s)
            ↓
搜尋才看得到「不可訂」
```

**結果**:剛被訂走的房,可能在 ES 上還顯示為 available,使用者點進去訂房才被擋下。
→ 體驗差,但比「double booking 真的成立」好。
→ 解法:搜尋走 ES(快、有彈性),**訂房 critical path 永遠回 Postgres 重新檢查**(consistency-first)。這就是「**搜尋優先 availability,訂房優先 consistency**」的具體落地。

### (c) 物化快取:什麼時候真的值得?

「city → 可用日期 bitmap」是極端優化,代價很高:
- 任何訂房都要 invalidate / 更新對應 city 的 bitmap
- Bitmap 表達「日期可訂」很省,但要支援「N 個連續可訂」需要做 bitmap AND
- 通常只在「**前 100 個熱門 city 佔了 80% 流量**」時導入

實務上 90% 的 booking 系統靠 (a) 就夠;另外 9% 加 (b);1% 才需要 (c)。

### 反直覺:**Elasticsearch 不一定比 SQL+index 快**

對「精確 city + 精確 date range」這種**結構化、選擇性高**的查詢,Postgres B-tree index 的 range scan 在 < 100ms 內完成是輕鬆的事。
ES 真正的優勢在:
- 全文(「靠近地鐵的雙人房」)
- 模糊(typo tolerance)
- Facet aggregation(「prices by city」「amenities counts」)
- 複雜布林(`should: [...] minimum_should_match: 2`)

→ **不要因為「規模大」就直覺上 ES**,先看 query shape。

### 答案模板

> 先做 (a) **複合 index `(city, date, status)`**,這是免費午餐裡最便宜的一份。索引代價是每次 inventory update 多寫幾筆 index entry,但本系統是 read-heavy,值得。
>
> 等需求出現 multi-facet 搜尋(amenities、價格區間、模糊比對)再導 (b) **Elasticsearch via CDC**,並接受 eventual consistency 帶來的「搜尋顯示可訂、實際下訂被擋」UX(訂房 critical path 永遠回 Postgres 重新檢查 — 這就是搜尋優先 availability、訂房優先 consistency 的落地)。
>
> (c) **物化 city → 可用日期 bitmap** 是最後手段,只在「熱門 city 佔絕大多數流量、寫入相對稀少」時才值得 invalidation 的複雜度。
>
> **不要因為規模大就直覺上 ES** — 對「精確 city + 精確 date range」這類選擇性高的結構化查詢,Postgres + 索引在 < 100ms 內完成綽綽有餘。

---

## Q4: Booking State Machine + 付款失敗 + Idempotency

### 三個狀態的存在理由

```
        ┌─────────────────────────────────────────┐
        │  available  ←──────┐                    │
        └────┬────────────────┤                   │
             │ POST /book     │ cancel            │ release on expire
             ↓                │ (logical)         │
        ┌─────────────┐       │                   │
        │  reserved   │───────┘                   │
        │ (10 min TTL)│                           │
        └────┬────────┘                           │
             │ payment success                    │
             ↓                                    │
        ┌─────────────┐                           │
        │   paid /    │───────────────────────────┘
        │   booked    │
        └─────────────┘
```

### 「為什麼不能跳過 reserved 直接 booked?」

如果只有 `available / booked`,那 booking 流程變成:

```
1. 使用者按「下訂」
2. server: UPDATE status='booked'
3. 跳轉付款頁
4. 使用者付款成功 → 沒事
5. 使用者付款失敗 / 中途離開 → 房間被永遠佔住
```

→ 第 5 點要靠**事後** rollback `booked → available`,但這時:
- 已經有 N 個其他使用者看到「不可訂」走了
- 你要怎麼知道是「離開」還是「正在輸入信用卡」?
- 取消後要不要通知那些走掉的使用者?

→ 加 `reserved` 這個**過渡態**是用一個 10 分鐘的視窗換「使用者還在猶豫 / 輸入卡號」的時間。**過渡態的本質是『未確定的承諾』**。

### 「為什麼不能跳過 paid 直接 booked?」

```
reserved → booked  (single step on payment success)
```

問題:**收款方(Stripe)和你(Booking Service)是兩個系統**,你的 `booked` 不等於 Stripe 的 `paid`。如果:

```
1. Stripe 扣款成功
2. Stripe 發 webhook 通知你
3. 你收到 webhook,要 UPDATE status='booked'
4. ❌ 你的 DB 在這一秒掛了
5. Stripe 重發 webhook
6. 你收到第二次 → 又執行一次 UPDATE
```

如果 `paid` 和 `booked` 是同一步,你會發現:
- 沒有「我已經承認收到付款,但還沒完成自家入帳」這個中間狀態
- 重複的 webhook 沒辦法區分「第一次處理失敗的 retry」vs「真的是新事件」

→ `paid` (我承認 Stripe 那邊扣到錢了) 和 `booked` (我自家 inventory 已經更新) 拆開,讓**自家 transaction** 變成 `paid → booked` 一個原子操作。萬一掛在中間,recovery 流程可以看到「paid 但還沒 booked」的訂單,重做最後一步。

### Idempotency:webhook 是註定會重複的

**原則**:Stripe 文件白紙黑字寫「webhook may be delivered multiple times for the same event」。任何處理 webhook 的系統,**必須**把「同一個事件來兩次」視為設計需求,不是 bug。

#### 三種做法

| 方案 | 怎麼做 | 適用 |
|---|---|---|
| 1. **Idempotency key 表** | 維護 `processed_keys (key, result, processed_at)` 表;每次 webhook 進來先 lookup,有就回上次 result | 通用,推薦 |
| 2. **State transition 防呆** | UPDATE 寫成 `UPDATE booking SET status='paid' WHERE id=? AND status='reserved'`,affected rows = 0 表示已經處理過 | 簡單,但要小心並發兩個 webhook 同時來 |
| 3. **Event sourcing** | 把 webhook 當 event 寫進 event log,排除 dup event,projection 端再算 | overkill,除非整個系統都用 ES |

**Scaffold 用方案 2(條件 UPDATE)+ 方案 1(Idempotency-Key header,簡單表)的混合**。

#### Idempotency Key 在哪一層做?

```
HTTP 層:  client 帶 Idempotency-Key header
DB 層:    table 加 UNIQUE constraint on idempotency_key
```

第二次同 key 進來 → INSERT IntegrityError → 直接回上次的 response。**雙保險**。

### 答案模板

> 三狀態(`available / reserved / paid+booked`)的存在分別解三個不同問題:
> - `reserved` 解「使用者還在輸入卡號」的中間態,沒有它你就只能事後 rollback,UX 災難
> - `paid` 解「Stripe 確認收款 vs 自家 inventory 更新」的兩階段問題,讓最後一步可以是自家 atomic transaction
>
> 付款失敗:`reserved → cancel`(立即釋放給其他人搶)。10 分鐘沒付:logical availability 模式下,後續來搶的人自己會 take over;cron 只負責整理 DB 視覺髒污。
>
> Idempotency:webhook 註定會重複,設計時當成需求而非例外。Scaffold 用「`Idempotency-Key` header + 條件 UPDATE(`WHERE status='reserved'`)」雙保險 — 第二次同 key 進來直接回第一次的 response,不會二次扣款也不會 409。

---

## Q5: Read-Heavy Caching — Home Detail vs Inventory

### 為什麼 home detail 好 cache,inventory 危險?

| 維度 | Home Detail | Inventory(可訂日期) |
|---|---|---|
| 變動頻率 | **極低**(房東一週改一次?) | **極高**(每筆訂房都改) |
| Hit rate 預期 | > 99% | 視 query 而定,可能很低 |
| Stale 後果 | 「地址舊了 1 分鐘」UX 影響小 | **「以為可訂、訂下去被擋」嚴重 UX + 商業風險** |
| Invalidation 信號 | 房東 PUT /home/{id} 時 invalidate | 每筆訂房都要 invalidate **多個 city × date 組合** |
| Key shape 簡單 | `home:{id}` | `search:{city}:{start}:{end}:...`(combinatorial 爆炸) |

### Home Detail 的 cache 策略 = Cache-aside + DB trigger invalidate

```python
def get_home(home_id):
    cached = redis.get(f"home:{home_id}")
    if cached:
        return cached
    home = db.query(Home).get(home_id)
    redis.set(f"home:{home_id}", home, ex=3600)  # 1 hr TTL 兜底
    return home

# 房東更新房源
def update_home(home_id, ...):
    db.commit()
    redis.delete(f"home:{home_id}")  # 主動 invalidate
```

**TTL 是 fallback,主動 invalidate 是主力**。光靠 TTL 會「房東改了地址,使用者 1 小時內看到舊的」。

### Inventory 為什麼 cache 不下去?

#### Combinatorial 爆炸

```
search?city=Honolulu&startDate=2026-09-01&endDate=2026-09-05
search?city=Honolulu&startDate=2026-09-01&endDate=2026-09-06
search?city=Honolulu&startDate=2026-09-02&endDate=2026-09-05
... (天數 × city × pageSize × page 組合無限)
```

→ Hit rate 注定很低(每個使用者的搜尋 key 幾乎不同)。

#### Invalidate 風暴

User 訂了 H1 9/1–9/5 → 要 invalidate 所有「city=Honolulu,date 範圍涵蓋 9/1–9/5 任一天」的 search key。
→ 不可能列舉。
→ 只能整片 flush(代價極高)或設極短 TTL(基本上沒 cache 效果)。

### 那 inventory 怎麼擋 read?

**正解不是 cache search 結果,是降低 search 的 DB 負擔本身**:
1. Read replica(scale out)
2. 對熱門 city 做「部分結果」cache(例如「Honolulu 未來 30 天可訂房 ID list」),搜尋時拿 list + filter dates 後再 LIMIT — hit rate 高很多
3. Elasticsearch(query 卸載)
4. 對「first page + 預設排序」這種**最熱的 query shape** 做窄 cache(80/20)

### 反直覺:**短 TTL ≠ 安全**

「我 cache 5 秒,反正 stale window 短」聽起來合理。但對訂房系統:
- 5 秒內可以發生 N 個訂房
- 5 秒間使用者看到「可訂」→ 點下去 → 真實 DB 拒絕 → UX 災難
- **不如不 cache**,寧可 search 慢一點,也不要在「可訂性」上騙使用者

→ Stale 容忍度看**業務語意**,不看時間長短。Home detail 容忍 1 小時,inventory 連 1 秒都不該。

### 答案模板

> Home detail 是 **「低變動 + Stale 影響小 + key shape 簡單」** 的完美 cache 對象 → cache-aside + DB trigger 主動 invalidate + 1 小時 TTL 兜底。預期 hit rate > 99%。
>
> Inventory(search 結果)**不該直接 cache**:(a) query key 是 city × dates × pageSize × page 的組合爆炸,hit rate 注定低;(b) 任何訂房都會讓無數 search key 同時 stale,invalidation 不可能;(c) 在「可訂性」上給使用者過期資訊,UX + 商業風險都極大。
>
> 要降 inventory 的 DB 負擔,正解是 read replica + 對熱門 city 做窄結構的「房 ID list」cache(再到 DB filter 精確 dates)+ Elasticsearch 卸載複雜 query。
>
> **Stale 容忍度看業務語意**:home detail 容忍 1 小時,inventory 連 1 秒都不該;短 TTL 不等於安全。

---

## 跨題串聯總結

5 題其實都在講同一個底層問題:**「在 read-heavy + write-critical 的系統裡,如何讓讀路徑與寫路徑各自最佳化、又不互相破壞 invariant?」**

```
Q1 (inventory 表設計):    為了讓「搜尋」是簡單的 range scan,把成本前置到「寫入」與「儲存」
Q2 (concurrent booking):  為了讓「訂房」是 atomic 而不阻塞他人,用 logical state + conditional UPDATE
Q3 (search latency):      讀路徑可以走 ES (eventual consistency),寫路徑(訂房)必須回 source of truth
Q4 (state machine):       reserved/paid/booked 是把「不可逆操作」拆成「可回復的中間態 + 不可逆的最後一筆 atomic write」
Q5 (caching):             讀路徑可以容忍 stale,寫路徑(可訂性判斷)絕對不能;cache 只放低變動的 home detail
```

把這 5 條主軸串起來,你才在「設計一個有強 invariant(no double booking)+ 高讀流量(400K QPS)的有 state web service」。

每一題的「壞答案」都是**用一致性換性能,但忽略了 invariant 被破壞的代價**:
- Range 模型省儲存 → 搜尋變慢
- Pessimistic lock 確保 invariant → 整個系統 throughput 崩潰
- Cache search 結果 → 「可訂性」騙使用者
- 跳過 reserved 直接 booked → 中途離開的使用者把房間永遠佔住
- webhook 不做 idempotency → 重複扣款

每一題的「好答案」都共享同一個哲學:**把 invariant 守在 critical path 的最後一筆 atomic write,其他地方都允許 eventual consistency 換性能**。
