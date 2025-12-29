# AutoSpin.py 使用說明

## 概述

`AutoSpin.py` 是一個自動化遊戲測試工具，支援多機台同時運行、自動 Spin、RTMP 串流檢測、模板比對、錯誤畫面偵測等功能。

---

## 📁 檔案結構

```
project/
├── AutoSpin.py                 # 主程式
├── game_config.json            # 遊戲機台配置檔
├── templates_manifest.json     # 模板清單與門檻設定
├── actions.json                # 動作定義（keyword_actions / machine_actions）
├── dotenv.env                  # 環境變數（LARK_WEBHOOK_URL）
├── templates/                  # 模板圖片資料夾
├── stream_captures/            # RTMP 截圖與錄影輸出資料夾
├── screenshots/                # 瀏覽器截圖輸出資料夾
├── ffmpeg.exe                  # FFmpeg 執行檔（RTMP 截圖/錄影）
└── msedgedriver.exe            # Edge WebDriver（若無則自動下載）
```

---

## ⚙️ 配置檔案參數說明

### 1. `game_config.json` - 遊戲機台配置

每筆配置代表一個遊戲機台，支援多機台同時運行。

```json
{
  "url": "遊戲 URL（必填）",
  "rtmp": "RTMP 名稱（選填，用於識別）",
  "rtmp_url": "RTMP 串流 URL（選填，用於截圖/錄影）",
  "game_title_code": "遊戲標題代碼（選填，用於從大廳進入遊戲）",
  "template_type": "模板類型（選填，覆蓋自動推斷）",
  "error_template_type": "錯誤畫面模板類型（選填，針對特定機台）",
  "enabled": true,              // 是否啟用此機台
  "enable_recording": true,     // 是否啟用錄影功能
  "enable_template_detection": true  // 是否啟用模板偵測
}
```

#### 參數詳細說明

| 參數 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `url` | string | ✅ | 遊戲頁面的完整 URL |
| `rtmp` | string | ❌ | RTMP 識別名稱，用於 log 和檔案命名 |
| `rtmp_url` | string | ❌ | RTMP 串流 URL（格式：`rtmp://...`），用於截圖和錄影 |
| `game_title_code` | string | ❌ | 遊戲標題代碼，用於從大廳自動進入遊戲（例如：`"873-COINCOMBO-0115"`） |
| `template_type` | string | ❌ | 直接指定模板類型，覆蓋自動推斷（例如：`"COINCOMBO"`） |
| `error_template_type` | string | ❌ | 錯誤畫面專用模板類型（例如：`"CC_error"`），高分觸發，只截圖不錄影 |
| `enabled` | boolean | ❌ | 是否啟用此機台（預設：`true`） |
| `enable_recording` | boolean | ❌ | 是否啟用錄影功能（預設：`true`） |
| `enable_template_detection` | boolean | ❌ | 是否啟用模板偵測（預設：`true`），高頻率時可關閉以提升性能 |

---

### 2. `templates_manifest.json` - 模板清單與門檻設定

管理所有模板圖片、比對門檻、條件過濾。

```json
{
  "default_threshold": 0.80,
  "types": {
    "類型名稱": {
      "threshold": 0.75,
      "templates": [
        {
          "file": "模板檔名.png",
          "threshold": 0.35,
          "mask": "遮罩檔名.png",
          "when": {
            "rtmp": "RTMP名稱",
            "title": "遊戲標題",
            "contains": {
              "rtmp": "包含字串",
              "title": "包含字串"
            }
          }
        }
      ]
    }
  }
}
```

#### 參數詳細說明

| 參數 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `default_threshold` | float | ❌ | 全域預設門檻（預設：`0.80`），低於此值觸發 |
| `types` | object | ✅ | 模板類型字典，key 為類型名稱 |
| `types[類型].threshold` | float | ❌ | 該類型的預設門檻，低於此值觸發 |
| `types[類型].templates` | array | ✅ | 該類型的模板清單 |
| `templates[].file` | string | ✅ | 模板圖片檔名（需放在 `templates/` 資料夾） |
| `templates[].threshold` | float | ❌ | 該模板的專屬門檻（優先於類型門檻） |
| `templates[].mask` | string | ❌ | 遮罩圖片檔名（用於部分比對） |
| `templates[].when` | object | ❌ | 條件過濾，只有符合條件時才使用此模板 |
| `when.rtmp` | string | ❌ | 精確比對 RTMP 名稱 |
| `when.title` | string | ❌ | 精確比對遊戲標題 |
| `when.contains` | object | ❌ | 包含判斷（`rtmp` 或 `title` 包含指定字串） |

#### 觸發邏輯

- **一般模板**：`score <= threshold` → 觸發（低分觸發）
- **錯誤模板**（`error_template_type`）：`score >= threshold` → 觸發（高分觸發，只截圖不錄影）

---

### 3. `actions.json` - 動作定義

定義遊戲進入後的點擊動作。

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
| `keyword_actions` | object | ❌ | 進入遊戲後立即執行的動作（依 `game_title_code` 關鍵字匹配） |
| `keyword_actions[關鍵字]` | array | ✅ | 座標清單（格式：`"X,Y"`），依序點擊 |
| `machine_actions` | object | ❌ | 特殊流程動作（當餘額連續 10 次無變化時觸發） |
| `machine_actions[關鍵字].positions` | array | ✅ | 座標清單（格式：`"X,Y"`），依序點擊 |
| `machine_actions[關鍵字].click_take` | boolean | ❌ | 是否在點擊完座標後，額外點擊 Take 按鈕（預設：`false`） |

#### 座標格式

- 格式：`"X,Y"`（例如：`"5,32"`）
- 對應到遊戲畫面上的座標位置
- 程式會尋找頁面上文字內容為該座標的 `span` 元素並點擊

---

### 4. `dotenv.env` - 環境變數

```env
LARK_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx
```

| 參數 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `LARK_WEBHOOK_URL` | string | ❌ | Lark 機器人 Webhook URL，用於推播通知 |

---

## 🎮 程式內建參數

### 全域變數

| 變數 | 類型 | 預設值 | 說明 |
|------|------|--------|------|
| `spin_frequency` | float | `1.0` | Spin 間隔時間（秒），可透過熱鍵動態調整 |
| `stop_event` | Event | - | 全域停止旗標（Ctrl+C 或 Ctrl+Esc 觸發） |
| `pause_event` | Event | - | 全域暫停旗標（Ctrl+Space 切換） |
| `SPECIAL_GAMES` | set | `{"BULLBLITZ", "ALLABOARD"}` | 特殊機台集合，影響餘額 selector 和 Spin 按鈕 selector |

### 餘額檢測參數

| 參數 | 類型 | 預設值 | 說明 |
|------|------|--------|------|
| `_check_interval` | int | `10` | 連續無變化次數門檻，達到此值觸發特殊流程 |
| `_no_change_count` | int | `0` | 當前連續無變化計數器 |
| 低餘額門檻 | int | `20000` | 餘額低於此值時執行退出流程 |

### RTMP 檢測參數

| 參數 | 類型 | 預設值 | 說明 |
|------|------|--------|------|
| `threshold` | float | `0.80` | 模板比對門檻（低於此值觸發，一般模板） |
| `max_dup` | int | `3` | 連續重複畫面次數門檻 |
| 錄影時長 | int | `120` | 觸發錄影時的錄製時長（秒） |
| 錄影啟動等待 | float | `3.0` | 等待錄影程序啟動的最大時間（秒） |
| 錄影開始後等待 | float | `10.0` | 錄影開始後暫停 Spin 的時間（秒） |

### 404 檢測參數

| 參數 | 類型 | 預設值 | 說明 |
|------|------|--------|------|
| `_404_check_interval` | float | `30.0` | 404 檢測間隔（秒） |

### 頻率相關參數

| 頻率範圍 | 等待時間 | 隨機抖動 | 說明 |
|----------|----------|----------|------|
| `<= 0.1s` | `0.05s` | ±5% | 超快頻率，使用快速餘額檢查 |
| `<= 0.5s` | `0.2s` | ±10% | 快速頻率 |
| `> 0.5s` | `0.5s` | ±20% | 正常頻率以上 |

### 超快頻率 RTMP 檢測參數

| 參數 | 類型 | 預設值 | 說明 |
|------|------|--------|------|
| 截圖超時 | float | `2.0` | FFmpeg 截圖超時時間（秒） |
| 間隔檢測 | int | `5` | 每隔 N 次 Spin 才檢測一次 RTMP |
| `max_templates` | int | `2` | 限制比對的模板數量 |

---

## ⌨️ 熱鍵功能

| 熱鍵 | 功能 | 說明 |
|------|------|------|
| `Ctrl + Space` | 暫停/恢復 | 切換全域暫停狀態 |
| `Ctrl + Esc` | 停止程式 | 優雅退出所有執行緒 |
| `小鍵盤 0` | 頻率：0.01s | 💀 極度危險（僅測試環境） |
| `小鍵盤 1` | 頻率：0.05s | 🔥 極限（僅測試環境） |
| `小鍵盤 2` | 頻率：0.1s | 🚀 超快 |
| `小鍵盤 3` | 頻率：0.5s | 🚀 快速 |
| `小鍵盤 4` | 頻率：1.0s | ⚡ 正常（預設） |
| `小鍵盤 5` | 頻率：1.5s | 🐌 慢速 |
| `小鍵盤 6` | 頻率：2.0s | 🐢 很慢 |
| `小鍵盤 7` | 頻率：3.0s | 🐌 極慢 |
| `小鍵盤 8` | 頻率：5.0s | 🐢 非常慢 |
| `小鍵盤 9` | 頻率：10.0s | 🐌 極度慢 |

---

## 🔧 功能說明

### 1. 自動 Spin 流程

1. **餘額檢查**：Spin 前檢查餘額，低於 20000 執行退出流程
2. **點擊 Spin**：依機台類型選擇對應的 Spin 按鈕 selector
3. **餘額變化檢測**：
   - 超快頻率（≤0.1s）：與上次餘額比較
   - 正常頻率（>0.1s）：Spin 前後餘額比較
   - 連續 10 次無變化 → 觸發特殊流程（`machine_actions`）
4. **特殊流程**：依 `actions.json` 的 `machine_actions` 執行點擊動作
5. **RTMP 檢測**：根據頻率和設定執行模板比對
6. **動態等待**：根據頻率設定加上隨機抖動

### 2. RTMP 檢測與錄影

#### 一般模板（低分觸發）

- **觸發條件**：`score <= threshold`
- **行為**：啟動 FFmpeg 錄影 120 秒，保留截圖
- **錄影期間**：暫停本機台 Spin，錄影啟動後恢復

#### 錯誤模板（高分觸發）

- **觸發條件**：`score >= threshold`
- **行為**：只保留截圖，不啟動錄影
- **設定方式**：在 `game_config.json` 中設定 `error_template_type`

#### 重複畫面檢測

- 連續 3 次畫面相同 → 推播 Lark 通知
- 錄影中會跳過所有檢測，直接清理截圖

### 3. 404 頁面檢測

- 每 30 秒檢測一次
- 檢測到 404 → 自動刷新頁面
- 刷新後仍為 404 → 重新加載原始 URL

### 4. 低餘額退出流程

- **觸發條件**：餘額 < 20000
- **流程**：Cashout → Exit To Lobby → Confirm → 重新進入遊戲
- **超快頻率**：使用快速退出流程（減少等待時間）

---

## 📊 輸出檔案

### 截圖檔案

- **位置**：`stream_captures/`
- **命名格式**：`{rtmp名稱}_{時間戳}.jpg`
- **保留條件**：
  - 模板觸發時保留（一般模板或錯誤模板）
  - 未觸發時自動刪除

### 錄影檔案

- **位置**：`stream_captures/`
- **命名格式**：`{rtmp名稱}_{時間戳}.mp4`
- **時長**：120 秒
- **格式**：MP4（H.264 + AAC）

---

## 🐛 除錯與日誌

### 日誌級別

- **INFO**：一般流程資訊
- **WARNING**：非致命警告（觸發、重複畫面等）
- **ERROR**：例外錯誤

### 關鍵日誌標記

- `[Template]`：模板比對相關
- `[Record]`：錄影相關
- `[Lark]`：推播通知相關
- `[Hotkey]`：熱鍵操作相關
- `ErrorTemplateScore`：錯誤模板分數詳情

---

## ⚠️ 注意事項

1. **極限頻率警告**：
   - 0.01s 和 0.05s 頻率極度危險，可能導致系統不穩定
   - 僅建議在測試環境使用，且持續時間不超過 10-30 秒

2. **錄影功能**：
   - 需要 `ffmpeg.exe` 在同目錄
   - RTMP URL 必須可訪問
   - 錄影期間會暫停本機台 Spin

3. **模板比對**：
   - 模板圖片需放在 `templates/` 資料夾
   - 錯誤模板使用「高分觸發」邏輯（`score >= threshold`）
   - 一般模板使用「低分觸發」邏輯（`score <= threshold`）

4. **特殊機台**：
   - `BULLBLITZ` 和 `ALLABOARD` 使用不同的餘額和 Spin 按鈕 selector

5. **多機台運行**：
   - 每個機台獨立執行緒
   - 錯開啟動時間（間隔 1-2 秒）避免資源競爭

---

## 📝 範例配置

### game_config.json 範例

```json
[
  {
    "url": "https://example.com/game?token=xxx",
    "rtmp": "COINCOMBO115",
    "rtmp_url": "rtmp://example.com/live/COINCOMBO115_MAIN",
    "game_title_code": "873-COINCOMBO-0115",
    "template_type": "COINCOMBO",
    "error_template_type": "CC_error",
    "enabled": true,
    "enable_recording": true,
    "enable_template_detection": true
  }
]
```

### templates_manifest.json 範例

```json
{
  "default_threshold": 0.80,
  "types": {
    "COINCOMBO": {
      "threshold": 0.75,
      "templates": [
        {
          "file": "CC115.png",
          "threshold": 0.35,
          "when": { "rtmp": "COINCOMBO115" }
        }
      ]
    },
    "CC_error": {
      "threshold": 0.75,
      "templates": [
        {
          "file": "CC_error.png",
          "threshold": 0.80,
          "when": { "rtmp": "COINCOMBO115" }
        }
      ]
    }
  }
}
```

---

## 🔄 更新記錄

- **錯誤模板功能**：支援針對特定機台的錯誤畫面檢測（高分觸發，只截圖不錄影）
- **404 檢測**：定時檢測並自動刷新
- **餘額變化檢測**：支援超快頻率的快速檢查模式
- **熱鍵頻率調整**：支援小鍵盤數字鍵動態調整頻率
- **模板比對**：支援 manifest 驅動的條件過濾和遮罩比對

