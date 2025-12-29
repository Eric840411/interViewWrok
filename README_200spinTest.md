# 200spinTest.py 使用說明

## 概述

`200spinTest.py` 是一個批次測試工具，用於對多個帳號執行固定次數或隨機次數的 Spin 測試。適合快速驗證遊戲功能、測試多個帳號的遊戲流程，或進行批次測試。

---

## 📁 檔案結構

```
project/
├── 200spinTest.py              # 主程式
├── accounts.csv                # 帳號清單（必填）
├── actions.json                # 動作定義（選填，與 AutoSpin.py 共用）
└── msedgedriver.exe            # Edge WebDriver（必填）
```

---

## ⚙️ 配置檔案參數說明

### 1. `accounts.csv` - 帳號清單

CSV 格式的帳號清單，支援有表頭或無表頭的格式。

#### 格式說明

**有表頭格式（推薦）：**
```csv
account,game_title_code,url
osmel002,873-COINCOMBO-0115,https://example.com/game?token=xxx
osmel003,873-JJBX-0004,https://example.com/game?token=yyy
```

**無表頭格式：**
```csv
osmel002,873-COINCOMBO-0115,https://example.com/game?token=xxx
osmel003,873-JJBX-0004,https://example.com/game?token=yyy
```

#### 欄位說明

| 欄位 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `account` | string | ✅ | 帳號名稱（用於 log 識別） |
| `game_title_code` | string | ❌ | 遊戲標題代碼（用於從大廳進入遊戲） |
| `url` | string | ✅ | 遊戲頁面的完整 URL |

#### 自動識別規則

- **有表頭**：程式會自動識別欄位名稱（不區分大小寫）
  - 包含 `account` → 帳號欄位
  - 包含 `game_title_code` 或 `gametitlecode` → 遊戲標題欄位
  - 包含 `url` → URL 欄位
- **無表頭**：預設第一欄為帳號，第三欄為遊戲標題，自動尋找第一個像 URL 的欄位作為 URL

---

### 2. `actions.json` - 動作定義（選填）

與 `AutoSpin.py` 共用同一個 `actions.json` 檔案，定義遊戲進入後的點擊動作。

```json
{
  "keyword_actions": {
    "關鍵字": ["座標1", "座標2", ...]
  },
  "machine_actions": {
    "關鍵字": {
      "positions": ["座標1", "座標2", ...],
      "click_take": true
    }
  }
}
```

#### 參數詳細說明

| 參數 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `keyword_actions` | object | ❌ | 進入遊戲後立即執行的動作（Join 後執行） |
| `keyword_actions[關鍵字]` | array | ✅ | 座標清單（格式：`"X,Y"`），依序點擊 |
| `machine_actions` | object | ❌ | Spin 後執行的特殊流程動作 |
| `machine_actions[關鍵字].positions` | array | ✅ | 座標清單（格式：`"X,Y"`），依序點擊 |
| `machine_actions[關鍵字].click_take` | boolean | ❌ | 是否在點擊完座標後，額外點擊 Take 按鈕（預設：`false`） |

#### 座標格式

- 格式：`"X,Y"`（例如：`"5,32"`）
- 對應到遊戲畫面上的座標位置
- 程式會尋找頁面上文字內容為該座標的 `span` 元素並點擊

#### 執行時機

- **keyword_actions**：在點擊 Join 進入遊戲後立即執行
- **machine_actions**：在每次 Spin 後執行（如果匹配到關鍵字）

---

## 🎮 程式內建參數

### 基本設定

| 參數 | 類型 | 預設值 | 說明 |
|------|------|--------|------|
| `SPIN_MIN` | int | `10` | 隨機 Spin 次數的最小值 |
| `SPIN_MAX` | int | `25` | 隨機 Spin 次數的最大值（達到上限後強制退出） |
| `WINDOW_SIZE` | string | `"350,750"` | 瀏覽器視窗大小（寬,高） |

### 等待時間

| 操作 | 等待時間 | 說明 |
|------|----------|------|
| 點擊遊戲卡片後 | `1.0s` | 等待頁面穩定 |
| 點擊 Join 後 | `1.0s` | 等待進入遊戲 |
| 點擊座標後 | `0.2s` | 等待點擊完成 |
| 執行 keyword_actions 後 | `0.5s` | 等待動作完成 |
| Spin 間隔 | `1.5s` | 每次 Spin 之間的等待時間 |
| 點擊 Cashout 後 | `0.5s` | 等待退出選單出現 |
| 點擊 Exit 後 | `0.5s` | 等待確認選單出現 |
| 點擊 Confirm 後 | `5.0s` | 等待回到大廳 |
| 回到大廳後 | `3.0s` | 確認大廳載入完成 |

### 超時設定

| 操作 | 超時時間 | 說明 |
|------|----------|------|
| 尋找遊戲卡片 | `10s` | 等待大廳載入 |
| 尋找 Join 按鈕 | `6s` | 等待 Join 按鈕出現 |
| 尋找 Spin 按鈕 | `5s` | 等待 Spin 按鈕出現 |
| 點擊座標 | `2s` | 等待座標元素出現 |
| 點擊 Take 按鈕 | `2s` | 等待 Take 按鈕出現 |
| 點擊 Exit 按鈕 | `2s` | 等待 Exit 按鈕出現 |
| 點擊 Confirm 按鈕 | `2s` | 等待 Confirm 按鈕出現 |
| 確認回到大廳 | `10s` | 等待大廳容器元素出現 |

---

## 🔧 功能說明

### 1. 執行流程

1. **讀取配置**：
   - 讀取 `accounts.csv` 取得所有測試任務
   - 讀取 `actions.json`（如果存在）載入動作定義

2. **逐一執行任務**：
   - 對每個帳號依序執行（不並行）
   - 每個帳號完成後才執行下一個

3. **單一任務流程**：
   - 開啟瀏覽器並載入 URL
   - 從大廳進入指定遊戲（依 `game_title_code`）
   - 執行固定次數或隨機次數的 Spin
   - 完成後退出到大廳
   - 關閉瀏覽器

### 2. 進入遊戲流程

1. **檢查是否已在遊戲內**：
   - 嘗試尋找 Spin 按鈕
   - 如果找到，跳過大廳流程

2. **從大廳進入**：
   - 在大廳尋找包含 `game_title_code` 的遊戲卡片
   - 滾動到卡片並點擊
   - 尋找並點擊 Join 按鈕
   - 執行 `keyword_actions`（如果匹配到關鍵字）

### 3. Spin 流程

1. **Spin 次數決定**：
   - 如果指定了 `n` 參數，使用固定次數
   - 如果未指定，使用隨機次數（`SPIN_MIN` 到 `SPIN_MAX` 之間）

2. **執行 Spin**：
   - 尋找 Spin 按鈕（支援兩種常見選擇器）
   - 點擊 Spin 按鈕
   - 執行 `machine_actions`（如果匹配到關鍵字）
   - 等待 1.5 秒後繼續下一次

3. **容錯機制**：
   - 如果找不到 Spin 按鈕，嘗試重新進入遊戲
   - 如果點擊被遮擋，滾動到視窗中心再點擊

### 4. 退出流程

1. **強制退出**：
   - 點擊 Cashout 按鈕
   - 點擊 Exit To Lobby 按鈕
   - 點擊 Confirm 按鈕
   - 等待回到大廳（確認 `grid_gm_item` 元素出現）

2. **重試機制**：
   - 如果退出後未回到大廳，會再執行一輪 Spin 後重新嘗試退出
   - 直到成功回到大廳為止

---

## ⌨️ 中斷控制

### Ctrl+C 中斷

- 按下 `Ctrl+C` 時，程式會等待當前任務完成後才停止
- 不會立即中斷正在執行的任務，確保資料完整性

---

## 📊 日誌輸出

### 日誌級別

- **INFO**：一般流程資訊
- **WARNING**：非致命警告（找不到元素、未回到大廳等）
- **ERROR**：例外錯誤

### 關鍵日誌標記

- `➡️`：任務開始
- `✔️`：操作成功
- `❌`：操作失敗
- `⚠️`：警告訊息
- `🎮`：進入遊戲
- `🎲`：開始新一輪 Spin
- `✅`：Spin 完成
- `🚪`：退出流程
- `🏠`：回到大廳
- `🔹`：執行特殊流程

---

## ⚠️ 注意事項

1. **執行順序**：
   - 程式會依序執行每個帳號，不會並行執行
   - 每個帳號完成後才會執行下一個

2. **Spin 次數限制**：
   - 每輪最多執行 `SPIN_MAX` 次（預設 25 次）
   - 達到上限後會強制退出

3. **退出確認**：
   - 程式會確認真的回到大廳（檢查 `grid_gm_item` 元素）
   - 如果未回到大廳，會重試一輪 Spin 後再次退出

4. **動作定義**：
   - `keyword_actions` 在 Join 後立即執行
   - `machine_actions` 在每次 Spin 後執行
   - 關鍵字匹配是基於 `game_title_code` 的包含判斷

5. **瀏覽器設定**：
   - 使用無痕模式（Incognito）
   - 偽裝 iPhone User-Agent
   - 視窗大小固定為 350x750

6. **檔案依賴**：
   - 必須有 `accounts.csv` 檔案
   - `actions.json` 為選填，不存在時會跳過動作執行
   - 必須有 `msedgedriver.exe` 在同目錄

---

## 📝 範例配置

### accounts.csv 範例

**有表頭格式：**
```csv
account,game_title_code,url
osmel002,873-COINCOMBO-0115,https://osmclient-c.bewen.me/?token=xxx&gameid=osmbwjl
osmel003,873-JJBX-0004,https://osmclient-c.bewen.me/?token=yyy&gameid=osmjjbx
```

**無表頭格式：**
```csv
osmel002,873-COINCOMBO-0115,https://osmclient-c.bewen.me/?token=xxx&gameid=osmbwjl
osmel003,873-JJBX-0004,https://osmclient-c.bewen.me/?token=yyy&gameid=osmjjbx
```

### actions.json 範例

```json
{
  "keyword_actions": {
    "COINCOMBO": ["19,38"],
    "JJBX": ["18,9"]
  },
  "machine_actions": {
    "COINCOMBO": {
      "positions": ["5,32"],
      "click_take": false
    },
    "JJBX": {
      "positions": ["3,2", "3,5", "3,7"],
      "click_take": true
    }
  }
}
```

---

## 🔄 與 AutoSpin.py 的差異

| 特性 | 200spinTest.py | AutoSpin.py |
|------|----------------|-------------|
| **執行模式** | 批次測試（固定次數） | 持續運行（無限循環） |
| **並行執行** | 否（逐一執行） | 是（多機台同時運行） |
| **RTMP 檢測** | 無 | 有（截圖/錄影） |
| **模板比對** | 無 | 有 |
| **餘額檢測** | 無 | 有（低餘額自動退出） |
| **404 檢測** | 無 | 有 |
| **熱鍵控制** | 無 | 有（暫停/頻率調整） |
| **適用場景** | 快速測試、批次驗證 | 長期監控、自動化運營 |

---

## 🚀 使用範例

### 基本使用

```bash
python 200spinTest.py
```

程式會：
1. 讀取 `accounts.csv` 中的所有帳號
2. 依序對每個帳號執行 Spin 測試
3. 每個帳號完成後關閉瀏覽器，繼續下一個

### 執行流程範例

```
➡️ [osmel002](873-COINCOMBO-0115) 啟動：https://...
✅ 已在遊戲內，跳過大廳找卡片流程
🎲 第 1 輪：本輪 SPIN 次數 = 15
✅ SPIN 1/15 (.my-button.btn_spin)
🔹 SPIN 後特殊流程: COINCOMBO -> ['5,32'], take=False
✅ SPIN 2/15 (.my-button.btn_spin)
...
✅ SPIN 15/15 (.my-button.btn_spin)
🛑 本輪 SPIN 完成，嘗試退出至大廳…
🚪 Exit To Lobby
✅ Confirm 離開
🏠 已回到大廳容器畫面
✔️ 確認已回到大廳，結束 SPIN 任務
✔️ [osmel002](873-COINCOMBO-0115) 完成並關閉
```

---

## 📞 支援

如有問題或建議，請檢查日誌輸出或聯繫開發團隊。

