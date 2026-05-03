# QR Code Generator — 設計討論延伸筆記

這份筆記整理 5 題 Design Questions 的**延伸 Q&A 重點**,作為寫 `PROMPT.md` 答案時的參考素材。原題目見 [PROMPT.md](PROMPT.md)。

---

## Q1: Static vs Dynamic QR Code

### 核心取捨

| 維度 | Static 贏 | Dynamic 贏 |
|---|---|---|
| 可變性 | 永遠不變 | 需要事後改目標 |
| 可追蹤性 | 不在乎掃描數 | 需要 analytics |
| 可控性 | 不需停用/過期 | 要能下架釣魚連結 |
| 可用性/成本 | 不依賴後台、零成本 | 願意維運 server |
| QR 密度 | 原網址短才行 | 短網址永遠密度低 |

決策口訣:**「印出去後想不想換目標 / 看誰掃了 / 關掉它?」** 任一個 yes → Dynamic;全部 no → Static。

### 反直覺洞察:其實有第三類「簽章 token」

很多人以為機票、演唱會票是「靜態 URL QR」,**錯**。它們是**第三類**:

| 類型 | 編碼內容 | 解碼方 |
|---|---|---|
| Static URL | 純網址 | 一般手機相機 |
| Dynamic URL | 短網址 → server 轉址 | 一般手機相機 |
| **Signed token** | 二進位簽章資料(IATA BCBP 等) | **專用掃描器**離線驗章 |

機場閘門、演唱會門口的掃描器**不會打開瀏覽器**。它讀的是密碼學簽章資料,本地驗章,不依賴網路。

這就是為什麼這類場景不能用 dynamic redirect:
- **必須離線可驗**(機場網路掛了不能停飛)
- **延遲不能忍**(每秒掃幾百張票)
- **server 是 SPOF**(redirect server 掛 = 全閘停擺)
- **被偽造代價過高**(假票進場 = 天文數字損失)

### 「destination-side analytics 替代 dynamic」這個直覺對不對?

**對一半**。如果 QR 目標是你自己的網站(可埋 GA),確實能補一部分:

| Dynamic 提供 | destination 自己埋 GA 補得到嗎? |
|---|---|
| 計算掃描次數、時間 | ✅ |
| **來源歸因**(同一張 QR 印在海報/傳單/包裝,誰看的?) | ❌ |
| Bot / 慢連線 / JS 被擋的 hit | ❌ |
| 改目標 / 軟刪除 / 過期 / 釣魚下架 | ❌ |

且本練習的場景是「使用者提交**任意 URL**」,不一定是自家域名 → 這個前提根本不成立。所以本練習的 dynamic 是**鎖死的選擇**。

### 答案模板

> 本系統需求包含「事後修改、軟刪除、過期、分析、釣魚阻擋」,這些都是 server 端中介才能做到的能力,所以選 dynamic。Static 適用於「目標永遠不變、不需追蹤、不依賴 server」的場景(個人名片、Wi-Fi 連線資訊、離線展場)。票券類(機票、演唱會)其實屬於第三類「簽章 token」,QR 編碼的不是 URL 而是簽章資料,由專用掃描器離線驗證。

---

## Q2: Token Generation

### 演進史 — 從最笨的做法慢慢加東西

```
V1: 使用者自取  ── 衝突太常見,放棄
   ↓
V2: 純亂數     ── 同 URL 不 deterministic,不去重
   ↓
V3: 純 hash    ── 撞了沒救
   ↓
V4: hash + nonce retry  ← 終點
```

### Scaffold 的選擇:`hash_with_nonce`

```python
for attempt in range(MAX_RETRIES):
    payload = url if attempt == 0 else f"{url}::{attempt}"
    digest = sha256(payload.encode()).digest()
    token = base62_encode(digest)[:7]
    if not token_exists_in_db(db, token):
        return token
raise RuntimeError(...)
```

關鍵:**第 0 次嘗試用純 URL**(token = `sha256(url)[:7]`,deterministic),撞了才加 `::N` retry。

### 「同 URL 不同 token」的 7 個 Cost(誠實版本)

7 個 cost **絕大多數在小規模 / 個人練習都不痛**,規模放大才浮現:

| Cost | 真實觸發條件 | 個人練習會痛嗎? |
|---|---|---|
| 1. UX 期待錯位 | 「同人對同 URL 重複提交」 | 不會 |
| 2. 分析碎片化 | 「同 URL 真有多 token,且要做總和分析」 | 不會 |
| 3. 攻擊放大 | 「公開服務 + 沒 rate limit」 | 不會 |
| 4. 下架成本 | 「黑名單某 URL 對應多 token」 | 不會 |
| 5. 快取/儲存浪費 | 「DB 真的有大量 dup」 | 不會 |
| 6. **Token 預測洩漏 namespace** | 「攻擊者能算 hash + probe」 | **架構級,規模再小都成立** |
| 7. 運維除錯 | 「多人運維且要 trace」 | 不會 |

→ Scaffold 故意**不去重**,因為設計意圖是「**token = campaign-level 獨立資源**」,每筆有自己的過期、刪除、custom alias。如果 dedup,A 的 1 天過期會把 B 的 30 天 QR 一起殺掉。

### Cost 6 的真實攻擊路徑(這個值得單獨講)

**問題不在「T1 ≠ T2 洩漏」**(那其實沒洩漏),**問題在「URL → token 是公開可算的函數」**:

```
Hash 預測攻擊 = oracle attack
─────────────────────────────────

攻擊者離線:
  expected_token = sha256("https://target.com")[:7]

攻擊者線上:
  GET /r/<expected_token> → 302/410?
  
  302 → URL 被縮過(可能洩漏)
  404 → 沒人縮過
```

**3 個真實攻擊場景:**

#### 場景 A:Apple 抓內部洩密

```
1. Alex(Apple 員工)把內部 URL 用你的 service 縮成短連結傳給記者
2. Apple 安全團隊有完整內部 URL 清單
3. 對每個內部 URL 算 sha256[:7],probe 你 service 的 /r/<token>
4. 命中 → 知道洩密發生
5. 法律強制要求你 service 提供 createIP / createTime
6. 比對 VPN log → 抓到 Alex
```

你 service **不是被攻擊的對象**,而是「被借刀殺人」的工具。

#### 場景 B:Phishing 目標清單建構

攻擊者想找「對銀行短網址有信任條件反射」的人:

```python
for bank_url in [...所有銀行頁面...]:
    token = sha256(bank_url)[:7]
    if requests.head(f"shortener/r/{token}").status_code == 302:
        hits.append(bank_url)
# hits = 「在你 service 縮過銀行 URL 的人」
# 對這群人發假冒「短網址通知」phishing 信,命中率比亂槍打鳥高 100 倍
```

#### 場景 C:政治異議追蹤(真實案例)

威權政府監測「禁書 URL 是否被縮短分享」:

```python
while True:
    for url in banned_books:
        token = sha256(url)[:7]
        if check_redirect(token):
            request_subpoena_to_get_creator_ip()
            track_dissident()
    sleep(60)
```

### 防禦:HMAC + secret salt

```python
SECRET_SALT = "server private key, never published"
token = sha256(url + SECRET_SALT + nonce)[:7]
```

攻擊者**不知道 salt**,無法離線算 hash → 必須對 server 試,degrade 成 random brute-force(不可行)。或直接改 `random` 策略,徹底斷絕預測。

### 答案模板

> 採 **Base62 + 7 碼 + SHA-256(url + nonce_attempt) + DB UNIQUE retry**,nonce 從 0 開始(第 0 次純 URL),撞到遞增最多 10 次。同 URL 兩次提交在第 2 次踩到 token_exists,自動 retry 拿到不同 token — 這是有意的:每張 QR 是獨立資源(各有過期、刪除狀態)。
>
> 空間 62⁷ ≈ 3.5 兆,1 億筆下單筆碰撞機率 ≈ 3×10⁻⁵,10 次重試全撞機率 < 10⁻⁴⁵。生日悖論在 187 萬筆達 50%「DB 內存在某對撞」,但這不是該關心的指標 — 真正影響服務的是「下一筆撞」,該機率隨 N/空間 線性成長。
>
> 並發安全靠 DB UNIQUE constraint + IntegrityError catch retry,不能僅依賴 check-then-insert(race condition)。
>
> **生產化補強**:本策略讓 URL → token 對應**可離線預測**,在公開服務上會被當作「URL 是否被提交過」的 oracle 攻擊。應改用純 random,或在 hash payload 加 server-side secret salt(HMAC),讓預測必須持有 server 私鑰才行。

---

## Q3: Redirect Strategy (302 vs 301)

### 物理類比

| | 301 | 302 |
|---|---|---|
| 寄信 | 「我永久搬家了,通訊錄改新地址」 | 「我今天去朋友家,先寄那邊,下次還是寄我家」 |
| 餐廳 | 「本店永久遷至新址」(Google Maps 改) | 「今日於新址臨時營業」 |
| 電話 | 「她已調離,新分機 1234」(改通訊錄) | 「她在 1234 開會,我幫您轉」(下次還是打總機) |

**核心**:301 命令瀏覽器**寫永久快取規則**,302 命令瀏覽器**每次重新問**。

### 本練習為什麼必選 302

5 個需求**全部需要「每次都過 server」**:

| Spec 要求 | 301 會壞在哪 |
|---|---|
| Modify target URL after creation | 用戶端卡舊快取,改了沒用 |
| Soft delete | 瀏覽器繼續直連,沒下架 |
| Expiration | 客戶端不問 server,過期失效 |
| Analytics | 第二次後跳過 server,沒紀錄 |
| Malicious URL blocking | 釣魚連結檢舉後也救不回來 |

### 反直覺洞察:**「動態 + 301」對單瀏覽器 ≈ 靜態 QR**

```
動態 + 301 的生命週期:
  第 1 次掃 → 經 server → 收到 301 → 連 destination
                          ↑
                         寫入永久快取
  第 2 次以後 → 看快取直連 destination(跳過 server)
                  ↑
                  跟靜態 QR 完全一樣!
```

只有「**換不同瀏覽器**」才看得到「動態」這個身份還在 server。否則對單一瀏覽器來說,302 的彈性全部消失。

→ **301 是「兩邊都拿不到的最差解」**:既付了 server 維運成本,又失去動態應有的「所有人立刻看到改動」。

### 「301 是單向門」

```
你今天用 302 → 哪天改 301 → 安全 ✓
你今天用 301 → 哪天改回 302 → 危險 ✗
              已發出去的 301 已被 1000 個瀏覽器永久快取,改回 302 救不了
```

「能回頭的決定先做、不能回頭的決定要慎重」 — 不確定要不要支援改目標時,永遠先 302。

### Cache-Control 才是真實主宰

現代瀏覽器**先看 Cache-Control,再看 status**:

```http
HTTP/1.1 302 Found
Location: https://example.com
Cache-Control: no-store, max-age=0
```

可做到「302 但更強硬不准快取」。`status code` 是 hint,`Cache-Control` 是法律。

### NBA 比分 app 的「記得歷史比分」不是 301

很多人以為 NBA app 重開後還顯示舊比分是「301 快取」,**其實是另一層**:

| Layer | 範圍 | 機制 |
|---|---|---|
| Layer 1: Redirect 快取 | 「URL A → URL B 的路由規則」 | 301/302 在管 |
| **Layer 2: 資料快取** | **「這份 JSON 內容」本身** | **Service Worker / IndexedDB / `Cache-Control` on data response** |

NBA API 回的是 200 OK + JSON,**根本不是 redirect**。「立刻顯示舊比分 + 背景更新」叫 **stale-while-revalidate**,跟 301/302 沒關係。

### 答案模板

> 採 302。本系統 5 個核心功能(改目標、軟刪除、過期、分析、下架釣魚)全部依賴「每次掃描都要回 server」。301 一旦被瀏覽器快取就**繞過 server**,以上功能全部失效;即使後續切回 302 也救不回已快取狀態。301 唯一優勢是後續請求省一個 RTT,但 QR 場景多為「不同人各掃一次」,該優勢無法兌現,且即便兌現也低於上述功能的價值。
>
> 延遲若仍是顧慮,可在 302 之上加 `Cache-Control: no-store` 顯式禁快取 + 把 redirect 路徑前置 Redis 快取(降 server 內部延遲),scaffold 已用 `redirect_cache: dict[str, str]` 模擬這層。

---

## Q4: URL Normalization

### 「`http://Example.com/` 與 `https://example.com` 為什麼可能相同?」

| 差異 | RFC 嚴格說 | 實務上 |
|---|---|---|
| `Example` vs `example` | hostname **case-insensitive**(明文規定) | 完全相同 |
| `/` vs 空字串 | 空 path 等於 `/`(RFC 3986) | server 收到一樣 |
| `http` vs `https` | 不同 scheme = 不同 URI | 現代網站幾乎都強制 http→https,意圖一致 |

→ 「**potentially**(可能)」就是這個分界:RFC 嚴格說可能不同,實務上幾乎一定相同。短網址服務取**實務語意**而非 RFC 嚴格語意。

### 三層規則(風險光譜)

#### 安全區(永遠該做)

- scheme 小寫
- hostname 小寫
- 移除預設 port(`:80` for http,`:443` for https)
- 空 path 補 `/`
- percent-encoding 大寫(`%2a` → `%2A`)
- path 中 `./` `../` 解析

#### 灰色區(看產品決定)

- `http` 升級為 `https` — 對方 server 可能不支援
- 移除 fragment `#xxx` — 失去 deep linking
- 移除 trailing `/`(非根目錄)— 對某些 server 是不同資源

#### 危險區(別碰)

- path 全部小寫 — Linux server 上 `/About` 與 `/about` 是不同檔案
- 去掉 `www.` — DNS 上是不同子網域
- query param 重排 — 部分 server 依賴順序
- 去除 `utm_*` — 毀掉行銷追蹤

### Scaffold 的選擇

`aggressive` 模式(預設)+ `conservative` 模式(可切換)。`aggressive` 較激進(整 URL 小寫、剝 fragment、升 https、剝 trailing /),`conservative` 只動 RFC 安全規則。

### 額外的安全考量

- **IDN 同形異字攻擊**:`аpple.com`(西里爾 а)≠ `apple.com`(拉丁 a)。需 punycode 比對黑名單
- **Trailing dot**:`evil.com.` 在 DNS 等同 `evil.com`,但字串不同 → 漏掉黑名單
- **Userinfo 偽裝**:`http://google.com@evil.com/` 肉眼像 google.com,實際導 evil.com

### 答案模板

> URL 正規化目的有三:**讓 blocklist 命中、讓 analytics 一致、避免 DB 出現肉眼相同但字串不同的紀錄**。
>
> 必做(安全):scheme 小寫、hostname 小寫、移除預設 port、空 path 補 `/`、percent-encoding 大寫。
> 看產品:http→https 升級、移除 fragment、移除 trailing `/`(非根目錄要小心)。
> 不做:path 小寫、`www.` 去除、query param 重排、`utm_*` 移除。
>
> 至於「`http://Example.com/` 與 `https://example.com`」:hostname 大小寫不敏感(RFC)、空 path = `/`(RFC),這兩個差異規範上判定相同;`http` vs `https` 嚴格說不同,但實務上現代網站幾乎強制轉 https,**短網址服務取實務語意**,把三個差異一併消除。
>
> 安全層面額外處理 IDN 同形異字、trailing dot、`userinfo@host` 偽裝,blocklist 才能真正有效。

---

## Q5: Error Semantics (404 vs 410)

### 物理類比

| | 404 | 410 |
|---|---|---|
| 寄信退回 | 「查無此地址」(不知道為什麼) | 「住戶已永久搬離」(明確) |
| 餐廳 | 「找不到這家店」(可能搬走?還是名字錯?) | 鐵門大字「永久結束營業」 |
| 電話分機 | 「您撥的號碼是空號」 | 「該分機已退休,新窗口是 ___」 |

**404 = 「找不到,意圖不明」 / 410 = 「曾存在,擁有者刻意永久移除」**

### 5 個影響面向

| 影響 | 404 | 410 |
|---|---|---|
| UX 訊息 | 「找不到」(模糊) | 「已被停用」(明確) |
| 搜尋引擎 | 繼續爬幾週才放棄 | **立刻**從索引移除 |
| CDN 快取 | 短期或不快取 | 大膽快取 |
| 自動化客戶端 | 可能 retry | 不該 retry,清掉引用 |
| 自家監控 | 不知是 typo 還是下架 | 明確「下架」訊號 |

### 過期該歸 410 還是 404?

| 看法 | 對應 status |
|---|---|
| 過期是擁有者**刻意**設的 TTL | 410 ✓(本練習) |
| 過期可能想「等等再試」 | 404? |
| 503 是**暫時掛了**,完全不同語意 | 別用 503 |

scaffold 把 expired 跟 deleted 都歸 410,理由:兩者都是「擁有者意圖讓它失效」。如要更精細,可在 response body 加 reason 欄位:

```json
{ "detail": "Gone", "reason": "deleted" }
{ "detail": "Gone", "reason": "expired", "expired_at": "..." }
```

### 反方:有些服務全部回 404

**理由:資訊洩漏**。返回 410 等於告訴攻擊者「這 token 曾經有效」:

```
GET /r/aaaaaaa → 404 (沒這個)
GET /r/abc123  → 410 (有過,被刪了 ← 洩漏 namespace)
```

對隱私敏感的服務(政府文件、醫療、企業內部)寧可全部回 404 模糊化。本練習是公開服務,語意清晰價值大於 namespace 隱藏,選 410 是對的。

### 答案模板

> 該不一樣。**404 vs 410** 對應 RFC 9110 的兩種不同語意:
> - **404 Not Found**:server 不知道這資源,意圖模糊
> - **410 Gone**:資源**曾經存在,擁有者刻意永久移除**,意圖明確
>
> 區分有 5 個實際好處:UX 訊息可分(請檢查 vs 已停用)、爬蟲行為不同(410 立刻清索引)、CDN 快取策略不同、自家監控可分流、API 契約清晰。
>
> 「過期」跟「刪除」都歸 410,因為兩者都是擁有者意圖讓它失效;如要區分,在 body 加 reason 欄位即可。
>
> 反方:對隱私敏感的服務,返回 410 等於告訴攻擊者「這 token 曾經有效」,洩漏 namespace。這類服務寧可全部回 404 模糊化。本練習是公開短網址服務,語意清晰的價值大於 namespace 隱藏,選 410 + 404 是對的。
>
> 不要把過期當成「暫時失敗」回 503 — 503 是「server 掛了等等再試」,過期是**永久失效**,語意完全不同。

---

## 跨題串聯總結

5 題其實都在講同一個底層問題:**「state-changing 能力的歸屬」**。

```
Q1 (dynamic vs static):  state 在 server 還是在 QR 圖本身?
Q2 (token 生成):         如何讓 server 給的 state ID 唯一且難猜?
Q3 (302 vs 301):         如何讓客戶端持續詢問 server 的 state?
Q4 (normalization):      如何讓進來的輸入對應到一致的 state key?
Q5 (404 vs 410):         如何讓客戶端理解 state 的失效類型?
```

把這 5 條主軸串起來,你才在「設計一個有 state 的 web service」。Static QR、純 hash、純 301、不正規化、混淆 404/410,**每一個都是「state 控制力的削弱」**。dynamic + hash retry + 302 + aggressive normalize + 410/404 區分,則是**最大化 state 控制力**的工程決策。

至於要「最大化」到什麼程度,看你的業務需求 — 個人練習、行銷活動、銀行 API、政治異議匿名服務,每個取捨點都不同。本筆記提供的不是「正確答案」,是「**哪些變數可以調 + 各個方向的代價**」。
