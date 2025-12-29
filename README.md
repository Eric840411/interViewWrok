# 自動化遊戲測試工具

本專案包含兩個主要的自動化測試工具，用於遊戲測試和監控。

## 📦 專案結構

```
project/
├── AutoSpin.py                 # 持續運行模式（多機台同時運行、RTMP 檢測、模板比對）
├── 200spinTest.py              # 批次測試模式（固定次數 Spin、多帳號測試）
├── README_AutoSpin.md          # AutoSpin.py 詳細說明
├── README_200spinTest.md       # 200spinTest.py 詳細說明
├── actions.json                # 動作定義（兩個工具共用）
├── templates_manifest.json     # 模板清單與門檻設定
├── game_config.example.json    # 遊戲配置範例（請複製為 game_config.json 並填入真實資料）
├── dotenv.example.env          # 環境變數範例（請複製為 dotenv.env 並填入真實資料）
├── accounts.example.csv        # 帳號清單範例（請複製為 accounts.csv 並填入真實資料）
└── templates/                  # 模板圖片資料夾
```

## 🚀 快速開始

### 1. 安裝依賴

```bash
pip install selenium opencv-python numpy requests python-dotenv pynput
```

### 2. 配置檔案

1. **複製範例檔案**：
   ```bash
   cp game_config.example.json game_config.json
   cp dotenv.example.env dotenv.env
   cp accounts.example.csv accounts.csv
   ```

2. **填入真實資料**：
   - `game_config.json`：填入遊戲 URL、RTMP 資訊等
   - `dotenv.env`：填入 Lark Webhook URL（選填）
   - `accounts.csv`：填入帳號資訊（僅 200spinTest.py 需要）

### 3. 準備必要檔案

- `ffmpeg.exe`：放在專案根目錄（AutoSpin.py 的 RTMP 功能需要）
- `msedgedriver.exe`：放在專案根目錄（兩個工具都需要）

### 4. 執行

**AutoSpin.py（持續運行模式）：**
```bash
python AutoSpin.py
```

**200spinTest.py（批次測試模式）：**
```bash
python 200spinTest.py
```

## 📚 詳細說明

- **AutoSpin.py**：請參考 [README_AutoSpin.md](README_AutoSpin.md)
  - 多機台同時運行
  - RTMP 串流檢測與錄影
  - 模板比對與錯誤畫面偵測
  - 餘額檢測與自動退出
  - 404 頁面檢測
  - 熱鍵控制（暫停/頻率調整）

- **200spinTest.py**：請參考 [README_200spinTest.md](README_200spinTest.md)
  - 批次測試多個帳號
  - 固定次數或隨機次數 Spin
  - 自動進入遊戲與退出
  - 支援 actions.json 動作定義

## 🔧 主要功能

### AutoSpin.py
- ✅ 多機台同時運行（每個機台獨立執行緒）
- ✅ RTMP 串流截圖與錄影
- ✅ 模板比對（支援條件過濾、遮罩比對）
- ✅ 錯誤畫面偵測（高分觸發，只截圖不錄影）
- ✅ 餘額變化檢測（連續無變化觸發特殊流程）
- ✅ 低餘額自動退出並重新進入
- ✅ 404 頁面自動檢測與刷新
- ✅ 熱鍵控制（Ctrl+Space 暫停、小鍵盤調整頻率）
- ✅ Lark 推播通知

### 200spinTest.py
- ✅ 批次測試多個帳號
- ✅ 固定次數或隨機次數 Spin
- ✅ 自動從大廳進入遊戲
- ✅ 自動退出到大廳
- ✅ 支援 actions.json 動作定義
- ✅ 容錯機制（退出失敗自動重試）

## 🛠️ 技術棧

- **Python 3.x**
- **Selenium**：瀏覽器自動化
- **OpenCV**：圖像處理與模板比對
- **FFmpeg**：RTMP 串流截圖與錄影
- **pynput**：熱鍵監聽

## 📝 注意事項

1. **敏感資訊**：
   - 請勿將 `game_config.json`、`dotenv.env`、`accounts.csv` 上傳到公開倉庫
   - 使用 `.gitignore` 排除這些檔案
   - 使用範例檔案（`*.example.*`）作為模板

2. **執行檔**：
   - `ffmpeg.exe` 和 `msedgedriver.exe` 需要手動下載並放在專案根目錄
   - 這些檔案已加入 `.gitignore`，不會被上傳

3. **輸出檔案**：
   - 截圖和錄影檔案會保存在 `stream_captures/` 資料夾
   - 這些資料夾已加入 `.gitignore`，不會被上傳

## 📄 授權

本專案僅供學習和測試使用。

