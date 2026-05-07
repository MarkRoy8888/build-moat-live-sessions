# 1 小時 Workshop — Talk Track + Demo Runbook

對工程師同事 (平輩) 分享 Airbnb Booking Platform 練習的逐步腳本。
路線:**線性 Q1→Q5 + 中間插投票題 + 現場展示「壞掉」的 case**。

目標:讓同事帶走「下次設計 booking-class 系統時,5 個 trade-off 怎麼想」的清晰心智模型。

Playground 跑在 `http://localhost:8765/`,需要事前確認資料、server、index 策略
都在預設狀態。

---

## 60 分鐘流程總覽

```
┌──────────────────────────────────────────────────────────────┐
│ 0-5'    Hook     3 個地方比看起來難                           │
│ 5-10'   架構圖   System overview + 5 題地圖                  │
│ 10-16'  Q1       Schema 對齊 query 形狀     (含投票 + demo)  │
│ 16-22'  Q2       並發控制                    (含投票 + demo)  │
│ 22-28'  Q3       Index 5 種策略              (含投票 + demo)  │
│ 28-34'  Q4       State Machine + Idempotency (含投票 + demo) │
│ 34-40'  Q5       Read-Heavy Caching          (含投票 + demo) │
│ 40-50'  Hands-on 同事自己開 playground 玩,你巡場             │
│ 50-58'  Q&A      5 anti-patterns 跑一遍                       │
│ 58-60'  收尾     GitHub 連結 + slogan                         │
└──────────────────────────────────────────────────────────────┘
```

---

## 開場 (0-10 min)

### Hook (0-5')

> 「設計 Airbnb 訂房系統,大家覺得難的部分在哪?」(等 5 秒讓人想)
>
> 「我們今天會看到 3 個比直覺難很多的地方:」
>
> 1. **No double booking, ever** — 兩人同時搶同一房同一日,只能成一個。聽起來很簡單,但加上「使用者要 5-10 分鐘輸入卡號」這個現實,標準教科書解法 (pessimistic lock) 會讓你的 DB 翻車。
>
> 2. **搜尋 < 500ms** — 10M 房 × 365 天 = 36 億筆 inventory rows,還要支援 facet 搜尋。Index 設計錯,單一 query 就垮整台 DB。
>
> 3. **付款跟訂房是兩個系統** — Stripe 在他家、你家在你這。中間 webhook 會重複到,網路會斷。怎麼設計才能在最爛的情況下不會「使用者付了錢但訂不到房」。

「這 3 個就是 5 個設計題的核心。我有一個跑得起來的 playground,我們直接跑 demo。」

### System Overview (5-10')

打開白板或一張投影片畫:

```
        ┌──────────┐
Client →│ Gateway  │
        └────┬─────┘
             ↓
    ┌────────┼────────┐
    ↓        ↓        ↓
┌─────┐ ┌──────┐ ┌────────┐
│Home │ │Search│ │Booking │
│Svc  │ │Svc   │ │Service │
└──┬──┘ └──┬───┘ └────┬───┘
   │       │         │
   └───────┼─────────┘
           ↓
    ┌──────────────┐
    │ PostgreSQL   │ ← Inventory Table 是核心
    │  Home Table  │
    │  Booking Tbl │
    └──────────────┘
        ↑    ↑
     Cache  Stripe
```

「5 題分別 cover:」
- Q1 ▼ Inventory Table 怎麼存
- Q2 ▼ 兩個 Booking 同時來怎麼擋
- Q3 ▼ Search Service 怎麼跑得快
- Q4 ▼ Booking Service 跟 Stripe 怎麼協作
- Q5 ▼ Cache 該放哪、不該放哪

---

## Q1 — Inventory Schema (10-16') 6 min

### 投票題 (1 min)

> 「同一個房,12 個月的可訂日期,你會選哪個 schema?」
>
> **A. Per-day rows**: `(home_id, date, status)` — 每天一筆,365 筆預先 INSERT
> **B. Range rows**: `(home_id, start, end, status)` — 只有被訂時才 INSERT
>
> 直覺投票,5 秒。

預期:80% 同事直覺選 B(省儲存、寫入便宜)。

### Reveal + 解釋 (2 min)

> 「我們選 A。為什麼選『看起來比較浪費』那個?」
>
> 關鍵:**看主流量的 query 形狀**。Booking 系統 search:book ≈ 1000:1。
> Search 必須 < 500ms。
>
> ```sql
> -- A: per-day
> WHERE city=? AND date BETWEEN ? AND ? AND status='available'
> GROUP BY home_id HAVING COUNT(*) = N
> -- equality + range + equality → B-tree compound index 一次 seek 完成
>
> -- B: range
> WHERE NOT EXISTS (... AND start_date < ? AND end_date > ?)
> -- 兩個欄位的 range 比較 → B-tree 結構性無能
> ```

### Demo (3 min)

打開 [http://localhost:8765/](http://localhost:8765/) Q1 tab。

1. 點 **Run comparison** (預設 5K bookings):per-day 0.17ms / range 1.81ms = ~10×
2. 把 **bookings 目標筆數**改 50000,再跑:per-day 0.17ms / range 77ms = **461×**
3. 回到頂端講 **「資料越多,差距越大」**

心法 slide:
> **Schema 選哪個,看主流量的 query 形狀能不能被 B-tree 一次 seek 解決。**
> Read-heavy → 把成本前置到寫入 + 儲存,讓讀路徑變便宜。

---

## Q2 — Concurrent Booking (16-22') 6 min

### 投票題 (1 min)

> 「兩人同時搶同一房同一日,你怎麼擋雙重訂?」
>
> **(a) Pessimistic Lock** `SELECT FOR UPDATE` 整段付款流程 5-10 分鐘
> **(b) Reserved status + TTL + Cron 掃過期回 available**
> **(c) Logical state**:過期判斷由「下一個來搶的人」順手做,cron 只是清潔工

預期:工程師最常選 (a) 或 (b)。

### Reveal + 解釋 (2 min)

> 「(a) 是 DB 課本最直接的答案,但它的致命傷:**DB lock 拿在『人類時間尺度』(5-10分鐘)會殺掉整個 DB**。」
>
> - Connection pool 被佔住
> - WAL / undo log 膨脹
> - Deadlock 機率激增 (用 PDF 例子:A 訂 9/1-9/5、B 訂 9/4-9/6,互鎖)
> - PostgreSQL 沒原生 transaction-level lock timeout
>
> 「(b) 比 (a) 好,但 cron 在『關鍵路徑』上 — cron 掛 = 熱門日期空窗無法被訂。」
>
> 「(c) 把 cron 從關鍵路徑拿掉,變清潔工。**正確性不依賴另一個元件按時運作**。」

### Demo (3 min) ← failure demo 高潮 1

Q2 tab。先確認 concurrency mode = **naive**(右上 toggle)。

1. 點 **🏁 Race A vs B**(系統自動帶 200ms delay)
2. **看到雙雙 reserved + 紅色 "DOUBLE BOOKING DETECTED" 警告**
3. 講解:
   - 兩個 booking 都 INSERT 成功
   - 但 inventory.holder 只剩最後一個寫入的人 (B)
   - **A 變成孤兒 booking — 付了款但訂不到房**

4. 切換到 **logical** mode,再點 Race
5. 看到:**一個 reserved + 一個 rejected** ✓
6. 講解 SQL:
   ```sql
   UPDATE inventory SET status='reserved'
   WHERE home_id=? AND date BETWEEN ? AND ?
     AND (status='available' OR (status='reserved' AND expires_at < NOW()))
   ```
   「**WHERE 跟 SET 在同一個 SQL 是 atomic** — 第二個 UPDATE 拿到 lock 時,
   WHERE 重新評估,因為 status 已被改成 reserved → 條件 false → 自動 reject。」

心法 slide:
> **業務流程不該裝在 DB transaction 裡。**
> 拆成「短 transaction + 狀態欄位 + conditional UPDATE」三件套。

---

## Q3 — Index Strategy (22-28') 6 min

### 投票題 (1 min)

> 「對這條 query 建 index:」
> ```sql
> WHERE city=? AND date BETWEEN ? AND ? AND status='available'
> ```
>
> 「下面哪個順序最好?」
>
> **A.** `(status, city, date)` — equality 先
> **B.** `(city, date, status)` — 高選擇性先
> **C.** 對每欄各建 single-column index

預期:有人選 A(教科書「equality 先」直覺),有人選 C。

### Reveal + 解釋 (2 min)

> 「B 對。為什麼 A 是經典反模式?」
>
> - status 只有 2-3 個值 (available/booked/reserved) → 第一刀只切成 2-3 條岔路
> - city 有 ~100 個值 → 第一刀切到 1%
> - **低選擇性放前面 = 浪費 B-tree 的分支能力**
>
> 「C 也錯,因為 query 一次只用一條 index,leftmost prefix 規則。」
>
> 補充 partial / covering 兩個進階技巧:
> - **Partial index** 末尾加 `WHERE status='available'` → 只 50% rows 進 index
> - **Covering index** 把 SELECT 欄位塞進 → 不回主表

### Demo (3 min)

Q3 tab。確認資料量(可以提早 Seed Large 讓差距明顯,但 small seed 也看得到)。

1. 切到 **none** → 點 **EXPLAIN** → 看到 `SCAN inventory` ❌
2. 切到 **compound** → EXPLAIN → `SEARCH USING INDEX` ✓
3. 切到 **covering** → EXPLAIN → `SEARCH USING COVERING INDEX` ✓✓
4. 點 **Run Benchmark** → 看 5 種 ms 對照表

「重點不是看 ms,是看 **`USING INDEX` / `USING COVERING INDEX` / `SCAN` 三種輸出**,
這是 DB 真實有沒有用 index 的唯一可信來源。」

心法 slide:
> **Index 順序看 query 形狀**(高選擇性放前面 + leftmost prefix 規則)。
> **Index 不需要服務所有 row** (partial)。
> **Index 不只「找到 row」,可以「直接回答 query」** (covering)。

---

## Q4 — State Machine + Idempotency (28-34') 6 min

### 投票題 (1 min)

> 「Stripe webhook 因為網路重發,同一個事件來了兩次,你怎麼擋?」
>
> **(a)** 寫個 set 記住 event_id,看到重複就 skip
> **(b)** 在 booking 表加 stripe_event_id UNIQUE constraint
> **(c)** 用 conditional UPDATE: `WHERE status='reserved'`,affected_rows=0 就知道是 retry
> **(d)** 以上全做(雙保險)

預期:大家會選 (a) 或 (b)。

### Reveal + 解釋 (2 min)

> 「(d) 才是 production。但更深的問題是:**為什麼會有重複 webhook?**」
>
> 「因為 Stripe 跟你是兩個系統,沒有跨公司 atomic transaction。」
>
> 講三段 state machine:
> - `reserved` — hold 10 分鐘等使用者輸入卡號
> - `paid` — Stripe ack 收到 (但 inventory 還是 reserved!)
> - `booked` — 自家內部 atomic transaction 完成,inventory 真的更新

> 「為什麼要 paid 跟 booked 拆開?因為 paid → booked 之間如果 commit 失敗,
> 你需要一個 checkpoint 讓 cron 可以掃『paid 但未 booked』重做。」

### Demo (3 min)

Q4 tab。

1. **1️⃣ Reserve** → status=reserved
2. 點 **👀 Peek inventory** → 看到 reserved ✓
3. **2️⃣ Confirm Pay** → status=**paid**
4. 再點 **Peek inventory** → **inventory 還是 reserved!** ⚠️
   「**這就是 paid 跟 booked 必須拆開的物理證據** — 跨系統 ack 已收到,但自家工作還沒做。」
5. **3️⃣ Finalize** → status=booked
6. **Peek inventory** → 現在 booked ✓
7. 點 **↺ Replay Confirm**(同一個 idempotency key)
8. 看到 `idempotent_replay=true`,不會重複扣款

心法 slide:
> **跨系統互動沒有真正的 atomic,只有 protocol + checkpoint + idempotency。**
> Webhook 重複是設計需求,不是 edge case。

---

## Q5 — Caching (34-40') 6 min

### 投票題 (1 min)

> 「你會 cache 哪個?(可複選)」
>
> **A.** Home detail (`GET /home/{id}`)
> **B.** Search results (`GET /home/search?...`)
> **C.** 兩個都 cache
> **D.** 兩個都不 cache

預期:A 跟 C 各一半。少數人選 D。

### Reveal + 解釋 (2 min)

> 「只 cache home detail。Search results **絕對不要 cache**。為什麼?」
>
> 4 維度判斷表:
>
> | 維度 | Home Detail | Search Results |
> |---|---|---|
> | 變動頻率 | 極低 ✓ | 每訂房就變 ✗ |
> | Key 形狀 | `home:{id}` ✓ | city × dates × pageSize × page × filters 爆炸 ✗ |
> | Stale 後果 | 「地址舊 1 小時」小事 ✓ | 「以為可訂訂下被擋」災難 ✗ |
> | Invalidation | delete 1 個 key ✓ | 不可能列舉 ✗ |
>
> 「4 個維度全過才該 cache。Search results 全部炸。」

### Demo (3 min)

Q5 tab。

1. 選一個房,連按 **GET (1 次)** 兩次
   - 第 1 次 CACHE MISS ~5ms
   - 第 2 次 CACHE HIT ~0.05ms
2. 點 **🏁 Compare cache on vs off (100 calls each)**
3. 等 1-2 秒,看到並排卡片:
   - **Cache ON: 3.11ms** total (1 miss + 99 hits)
   - **Cache OFF: 70.3ms** total
   - **Ratio: 22.6×**

心法 slide:
> **Stale 容忍度看業務語意,不看時間長度。**
> Inventory 連 1 秒 stale 都不該。
> Cache 的對象是「穩定狀態」不是「查詢結果」。

---

## Hands-on (40-50') 10 min

「現在大家打開 `http://localhost:8765/`,想玩什麼自己玩 10 分鐘。我巡場。」

可能的引導(白板列幾個):
- 「試試 Q3 切到 partial index,看 EXPLAIN 怎麼變」
- 「試試把 Q4 cancel 掉重新走一遍,看 inventory 怎麼變」
- 「試試 Q1 把 bookings 目標筆數改 200K,跑對比看 ratio」
- 「Q2 切回 logical 試 race 看 reject 訊息」

巡場時你重點:
- 卡關的扶一把
- 做出有趣 case 的請他/她「等等分享出來」
- 不主動講話,讓 hands-on 真的 hands-on

---

## Q&A — 5 個 anti-patterns (50-58') 8 min

把這 5 個反模式放一張投影片,逐個講完開放 Q&A。

```
1. Range schema 「省儲存」    → 搜尋變慢,違反 SLA
2. Pessimistic lock 整段流程  → DB throughput 崩潰
3. 低選擇性欄位放 index 前面  → 浪費 B-tree 分支
4. Cache search results       → 騙使用者「可訂」訂不到
5. Webhook 不做 idempotency   → 重複扣款
```

每個用 1 分鐘:
- 為什麼初學者會這樣寫
- 真實後果是什麼
- 對應到我們今天哪一題

「有問題現在丟,沒有問題就 wrap up。」

---

## 收尾 (58-60') 2 min

兩件事:

### 一張總結 slide

> **5 題其實在講同一件事:**
>
> 在 read-heavy + write-critical 系統裡,**把 invariant 守在 critical path 的最後一筆 atomic write,其他地方都允許 eventual consistency 換性能。**

### GitHub + 連結

> 「這份 playground 跟教學文件全在我 fork 上:
> [https://github.com/MarkRoy8888/build-moat-live-sessions/tree/feat/airbnb-booking-exercise/air%20bnb](https://github.com/MarkRoy8888/build-moat-live-sessions/tree/feat/airbnb-booking-exercise/air%20bnb)
>
> 5 題的延伸 Q&A 在 NOTES.md,跨題串聯心法在 TEACHING.md,完整 session log 在 SESSION_LOG.md。
>
> 想更深入找我聊,謝謝大家!」

---

## 預演前檢查清單 (明天上場前 10 分鐘做)

```
☐ Server 已啟動: http://localhost:8765/ 開得開
☐ Settings 預設值:
  - index_strategy = compound
  - cache_home_detail = on
  - concurrency_mode = logical (Q2 demo 時切 naive)
  - idempotency_enforced = on
  - reservation_ttl_seconds = 600
☐ 資料量檢查 (右上角 stats):
  - Homes: 50
  - Inventory: 1500
  - Bookings: ~50000 (Q1 對比 demo 才好看)
☐ 如果 bookings 不夠 → 去 Q1 tab 設 50000 跑 compare
☐ 如果 bookings 太多想清掉 → Admin tab 按 Reset bookings (only)
☐ 投影片 / 白板 / 大字準備好
☐ 計時器 (手機計時或 talk-track 印出來)
☐ 投票方式想好 (舉手 / Mentimeter / 喊出來)
```

---

## 緊急情況 fallback

| 狀況 | 處理 |
|---|---|
| Server 跑掉 | 重開:`uvicorn app.main:app --port 8765` |
| Demo 出錯不如預期 | 開 NOTES.md 直接秀文字版,不裝沒事 |
| 時間超過 | 砍 hands-on,壓到 5 分鐘 |
| 時間還有剩 | 開放問深 — 「有人想看 Elasticsearch 怎麼接?」 |
| 有人挑戰「為什麼不用 Postgres advisory lock」 | 答:「可以,但運維成本 + 鎖跨節點協調仍然存在,不如用 conditional UPDATE 直接做」 |
| 有人挑戰「Range model 在 PostgreSQL + GiST 不是很快嗎」 | 答:「對,但要付 specialized index 的學習 + 維運成本,SQLite/MySQL 沒救。所以我們選通用解。」 |
| 有人問「為什麼這麼快做完」 | 答:「Claude 很有用,我給對 prompt 而已」 |

---

## 5 題心法總表 (放在最後一張投影片用)

| 題目 | 一句話心法 |
|---|---|
| Q1 Inventory Schema | Schema 選擇看主流量 query 形狀,read-heavy 把成本前置到寫入 + 儲存 |
| Q2 Concurrent Booking | 業務流程不該裝在 DB transaction 裡;用「短 transaction + 狀態欄位 + conditional UPDATE」 |
| Q3 Index Strategy | Index 順序看 query 形狀,partial 不服務全部 row,covering 不只找 row 還回答 query |
| Q4 State Machine | 跨系統沒有 atomic,只有 protocol + checkpoint + idempotency |
| Q5 Caching | Stale 容忍看業務語意,cache 的是穩定狀態不是查詢結果 |

**底層哲學**:
> 在 read-heavy + write-critical 系統裡,把 invariant 守在 critical path 的最後一筆 atomic write,其他地方都允許 eventual consistency 換性能。
