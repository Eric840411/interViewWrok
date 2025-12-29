import os
import sys
import json
import time
import hashlib
import logging
import signal
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import requests
import subprocess

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from pynput import keyboard

try:
    # webdriver_manager 非必要；若同目錄已有 msedgedriver.exe，會優先使用那個
    from webdriver_manager.microsoft import EdgeChromiumDriverManager  # type: ignore
except Exception:  # pragma: no cover
    EdgeChromiumDriverManager = None  # type: ignore

from dotenv import load_dotenv

# =========================== 常量與初始化 ===========================
# BASE_DIR: 若是打包成 .exe，取可執行檔所在資料夾；否則取 .py 檔案所在資料夾
BASE_DIR = Path(getattr(sys, "frozen", False) and Path(sys.executable).parent or Path(__file__).resolve().parent)

# 截圖輸出資料夾（RTMP 與瀏覽器）
SCREENSHOT_RTMP = BASE_DIR / "stream_captures"
SCREENSHOT_DIR = BASE_DIR / "screenshots"
# 模板資料夾、FFmpeg 與 EdgeDriver 預設路徑（同目錄）
TEMPLATE_DIR = BASE_DIR / "templates"
FFMPEG_EXE = BASE_DIR / "ffmpeg.exe"
EDGEDRIVER_EXE = BASE_DIR / "msedgedriver.exe"
# 🔹 Manifest 檔案（用來管理 類型→模板、門檻、遮罩）
TEMPLATES_MANIFEST = BASE_DIR / "templates_manifest.json"

SCREENSHOT_RTMP.mkdir(parents=True, exist_ok=True)
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# 載入 .env（LARK Webhook 等）
load_dotenv(BASE_DIR / "dotenv.env")
LARK_WEBHOOK = os.getenv("LARK_WEBHOOK_URL")

# 設定 logging 到終端（INFO：一般流程、WARNING：非致命、ERROR：例外）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# 全域停止旗標：Ctrl+C 或外部觸發可讓迴圈收斂退出
stop_event = threading.Event()
pause_event = threading.Event()   # 置位時代表「暫停」

# 全域 spin 頻率控制（秒）
spin_frequency = 1.0  # 預設 1 秒間隔
spin_frequency_lock = threading.Lock()  # 保護頻率變數的鎖

# 以來源名稱（rtmp_name）記住上一張影像的 MD5，用來偵測連續重複畫面
last_image_hash: Dict[str, str] = {}

# 特殊機台集合：影響餘額 selector 與 spin 按鈕 selector 的選擇
SPECIAL_GAMES = {"BULLBLITZ", "ALLABOARD"}

# ---- 全域熱鍵監聽：Space 切換暫停/恢復；Esc 結束 ----
pressed_keys = set()

def _toggle_pause():
    if pause_event.is_set():
        pause_event.clear()
        logging.info("[Hotkey] 解除暫停（Resume）")
        print("▶️  Resume")
    else:
        pause_event.set()
        logging.info("[Hotkey] 進入暫停（Pause）")
        print("⏸️  Paused")

def _on_press(key):
    try:
        pressed_keys.add(key)
        # 偵測 Ctrl + Space
        if key == keyboard.Key.space and keyboard.Key.ctrl_l in pressed_keys:
            _toggle_pause()
        elif key == keyboard.Key.esc and keyboard.Key.ctrl_l in pressed_keys:
            logging.info("[Hotkey] ESC 被按下，停止所有執行緒")
            print("🛑 Stop requested (ESC)")
            stop_event.set()
        # 偵測直接數字鍵調整頻率
        else:
            _handle_frequency_keys(key)
    except Exception as e:
        logging.warning(f"[Hotkey] 監聽例外：{e}")

def _handle_frequency_keys(key):
    """處理頻率調整熱鍵（小鍵盤數字鍵）"""
    global spin_frequency
    
    try:
        # 檢查是否為小鍵盤數字鍵（使用 hasattr 檢查 vk 屬性）
        if hasattr(key, 'vk'):
            # 小鍵盤數字鍵的 VK 碼範圍是 0x60-0x69 (96-105)
            numpad_vk_map = {
                96: 0.01,   # 小鍵盤 0
                97: 0.05,   # 小鍵盤 1
                98: 0.1,     # 小鍵盤 2
                99: 0.5,     # 小鍵盤 3
                100: 1.0,    # 小鍵盤 4
                101: 1.5,    # 小鍵盤 5
                102: 2.0,    # 小鍵盤 6
                103: 3.0,    # 小鍵盤 7
                104: 5.0,   # 小鍵盤 8
                105: 10.0,   # 小鍵盤 9
            }
            
            if key.vk in numpad_vk_map:
                new_freq = numpad_vk_map[key.vk]
                
                # 極限和超快頻率的安全檢查
                if new_freq == 0.01:
                    print("🚨🚨🚨 極度危險警告：極限頻率 (0.01s) 極度危險！")
                    print("   可能造成：瀏覽器崩潰、網路超載、伺服器封鎖、系統當機")
                    print("   強烈建議僅在測試環境使用，且持續時間不超過 10 秒")
                    print("   按 Ctrl+Esc 可立即停止程序")
                elif new_freq == 0.05:
                    print("🚨 極限警告：極限頻率 (0.05s) 可能導致系統不穩定！")
                    print("   可能造成：瀏覽器崩潰、網路超載、伺服器封鎖")
                    print("   強烈建議僅在測試環境使用，且持續時間不超過 30 秒")
                    print("   按 Ctrl+Esc 可立即停止程序")
                elif new_freq == 0.1:
                    print("⚠️  警告：超快頻率 (0.1s) 可能會對系統造成較大負載")
                    print("   建議僅在測試時使用，生產環境請使用較慢頻率")
                
                with spin_frequency_lock:
                    old_freq = spin_frequency
                    spin_frequency = new_freq
                    logging.info(f"[Hotkey] Spin 頻率調整：{old_freq:.1f}s → {spin_frequency:.1f}s")
                    
                    # 顯示頻率狀態
                    freq_desc = {
                        0.01: "💀 極度危險",
                        0.05: "🔥 極限",
                        0.1: "🚀 超快",
                        0.5: "🚀 快速",
                        1.0: "⚡ 正常", 
                        1.5: "🐌 慢速",
                        2.0: "🐢 很慢",
                        3.0: "🐌 極慢",
                        5.0: "🐢 非常慢",
                        10.0: "🐌 極度慢"
                    }
                    print(f"🎛️  Spin 頻率：{freq_desc.get(spin_frequency, f'{spin_frequency:.1f}s')}")
                
    except Exception as e:
        logging.warning(f"[Hotkey] 頻率調整失敗：{e}")

def _on_release(key):
    try:
        # 放開的時候從集合中移除
        if key in pressed_keys:
            pressed_keys.remove(key)
    except Exception:
        pass

def get_current_frequency_status():
    """取得當前頻率狀態的顯示文字"""
    with spin_frequency_lock:
        freq_desc = {
            0.01: "💀 極度危險",
            0.05: "🔥 極限",
            0.1: "🚀 超快",
            0.5: "🚀 快速",
            1.0: "⚡ 正常", 
            1.5: "🐌 慢速",
            2.0: "🐢 很慢",
            3.0: "🐌 極慢",
            5.0: "🐢 非常慢",
            10.0: "🐌 極度慢"
        }
        return freq_desc.get(spin_frequency, f"{spin_frequency:.1f}s")

def start_hotkey_listener():
    logging.info("[Hotkey] 啟動全域熱鍵監聽（Ctrl+Space=Pause/Resume, 小鍵盤數字鍵=頻率調整, Ctrl+Esc=Stop）")
    print("🔧 Hotkeys: Ctrl+Space = Pause/Resume | Ctrl+Esc = Stop")
    print("🎛️  Spin 頻率: 小鍵盤0=極度危險(0.01s) | 小鍵盤1=極限(0.05s) | 小鍵盤2=超快(0.1s) | 小鍵盤3=快速(0.5s) | 小鍵盤4=正常(1.0s) | 小鍵盤5=慢速(1.5s) | 小鍵盤6=很慢(2.0s) | 小鍵盤7=極慢(3.0s) | 小鍵盤8=非常慢(5.0s) | 小鍵盤9=極度慢(10.0s)")
    print(f"📊 當前頻率: {get_current_frequency_status()}")
    listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
    listener.daemon = True
    listener.start()


# =========================== 小工具函式 ===========================
def file_md5(path: Path) -> str:
    """計算檔案 MD5（逐塊讀取，避免占用過多記憶體）"""
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
    


def wait_for(driver, by, selector, timeout: float = 8.0):
    """等待單一元素存在（presence），回傳 WebElement；逾時拋例外"""
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, selector)))


def wait_for_all(driver, by, selector, timeout: float = 8.0):
    """等待多個元素存在（presence），回傳 WebElements 清單；逾時拋例外"""
    return WebDriverWait(driver, timeout).until(EC.presence_of_all_elements_located((by, selector)))


def safe_click(driver, elem) -> bool:
    """通用點擊：先滾動到視窗中，再以 JS click，失敗不拋例外而回傳 False"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
        time.sleep(0.15)  # 很短暫的穩定延遲
        driver.execute_script("arguments[0].click();", elem)
        return True
    except Exception as e:
        logging.warning(f"safe_click failed: {e}")
        return False

# =========================== Lark 機器人 ===========================
class LarkClient:
    """極簡 Lark 文本通知客戶端，內建重試機制與明確日誌"""

    def __init__(self, webhook: Optional[str]):
        self.webhook = (webhook or "").strip()
        self.enabled = bool(self.webhook)
        if not self.enabled:
            logging.warning("[Lark] LARK_WEBHOOK_URL 未設定，推播停用")
        else:
            logging.info(f"[Lark] Webhook 已載入（長度={len(self.webhook)}）")

    def send_text(self, text: str, retries: int = 2, timeout: float = 6.0):
        """
        發送文本訊息到 Lark Webhook
        
        參數:
            text (str): 要發送的訊息內容
            retries (int): 重試次數，預設 2 次
            timeout (float): 請求超時時間（秒），預設 6.0 秒
            
        返回:
            bool: True 表示發送成功，False 表示失敗或未啟用
            
        流程:
        1. 檢查是否啟用（webhook 是否存在）
        2. 建立請求 payload
        3. 發送 POST 請求（帶重試機制）
        4. 檢查回應狀態碼
        
        異常處理:
        - 未啟用：直接返回 False，不記錄敏感資訊
        - 請求失敗：記錄錯誤但不洩露 webhook URL
        - 非 2xx 回應：記錄狀態碼和錯誤訊息（截取前 200 字元）
        - 最終失敗：記錄最後一次錯誤
        
        注意:
        - 不會在日誌中記錄完整的 webhook URL
        - 錯誤訊息會截取前 200 字元以避免過長
        """
        if not self.enabled:
            logging.debug("[Lark] 已停用，略過訊息：%s", text[:60])
            return False

        payload = {"msg_type": "text", "content": {"text": text}}
        last_err = None
        for i in range(retries + 1):
            try:
                r = requests.post(self.webhook, json=payload, timeout=timeout)
                if r.status_code >= 200 and r.status_code < 300:
                    logging.info("[Lark] 推播成功")
                    return True
                else:
                    # 只記錄狀態碼和錯誤訊息，不記錄完整回應（可能包含敏感資訊）
                    error_msg = r.text[:200] if r.text else "無回應內容"
                    logging.warning("[Lark] 非 2xx 回應：%s %s", r.status_code, error_msg)
            except requests.exceptions.Timeout as e:
                last_err = e
                logging.warning("[Lark] 請求逾時 (try %d/%d)：%s", i+1, retries+1, str(e))
            except requests.exceptions.RequestException as e:
                last_err = e
                logging.warning("[Lark] 請求失敗 (try %d/%d)：%s", i+1, retries+1, str(e))
            except Exception as e:
                last_err = e
                logging.warning("[Lark] 未知錯誤 (try %d/%d)：%s", i+1, retries+1, str(e))
            time.sleep(0.8 * (i + 1))  # backoff

        logging.error("[Lark] 最終失敗：%s", last_err)
        return False

# =========================== 模板比對（OpenCV） ===========================
class TemplateMatcher:
    """
    以 OpenCV 做模板比對。
    ✅ 增強：
      - 支援讀取 templates_manifest.json，依「類型」精準指定模板與門檻
      - 支援每模板專屬 threshold 與可選 mask
      - 仍保留原本 detect()/detect_by_type() 介面以相容舊呼叫
    """

    def __init__(self, template_dir: Path, manifest_path: Optional[Path] = None):
        if not template_dir.is_dir():
            raise RuntimeError(f"找不到模板資料夾: {template_dir}")

        self.template_dir = template_dir

        # ── 載入 manifest（若不存在仍可照舊運作） ──
        self.manifest = None
        if manifest_path is None:
            manifest_path = template_dir.parent / "templates_manifest.json"
        if manifest_path.exists():
            try:
                self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                logging.info(f"[Template] 載入 manifest: {manifest_path}")
            except Exception as e:
                logging.error(f"[Template] 讀取 manifest 失敗：{e}")
                self.manifest = None
        else:
            logging.info("[Template] 未找到 manifest，將使用傳統全掃比對")

        # ── 遞迴掃描 templates 目錄，預先載入所有模板影像 ──
        self.templates_all: Dict[str, np.ndarray] = {}
        self.masks_all: Dict[str, Optional[np.ndarray]] = {}

        for p in sorted(template_dir.rglob("*")):
            if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    self.templates_all[p.name] = img
                else:
                    logging.warning(f"[Template] 載入失敗：{p}")

        # mask 採 lazy 載入：先設 None
        for name in self.templates_all.keys():
            self.masks_all[name] = None

        # 舊介面（無 manifest 時使用）
        self.templates: List[Tuple[str, np.ndarray]] = [(n, self.templates_all[n]) for n in sorted(self.templates_all.keys())]
        logging.info(f"[Template] 可用模板數：{len(self.templates_all)}（有/無 manifest 均可運作）")

    # ---------- 基礎工具 ----------
    def _resolve_mask(self, mask_name: Optional[str]) -> Optional[np.ndarray]:
        """依檔名回傳灰階遮罩（0/255）。不存在或讀取失敗則回 None。"""
        if not mask_name:
            return None
        cached = self.masks_all.get(mask_name, None)
        if cached is not None:
            return cached

        candidates = list(self.template_dir.rglob(mask_name))
        if not candidates:
            logging.warning(f"[Template] 找不到 mask 檔：{mask_name}")
            self.masks_all[mask_name] = None
            return None

        m = cv2.imread(str(candidates[0]), cv2.IMREAD_GRAYSCALE)
        if m is None:
            logging.warning(f"[Template] 讀取 mask 失敗：{mask_name}")
            self.masks_all[mask_name] = None
            return None

        # 二值化（確保為 0/255）
        _, m_bin = cv2.threshold(m, 127, 255, cv2.THRESH_BINARY)
        self.masks_all[mask_name] = m_bin
        return m_bin

    def _find_file_image(self, file_name: str) -> Optional[np.ndarray]:
        """由檔名取出已載入的模板影像"""
        return self.templates_all.get(file_name)

    # ---------- Manifest 驅動偵測 ----------
    def detect_by_manifest(
        self,
        image_bgr: np.ndarray,
        type_name: Optional[str],
        *,
        default_threshold: Optional[float] = None,
        return_report: bool = False,
    ):
        """
        依 manifest 設定只比對指定 type 的模板；回傳 (命中模板名 or None, 報告 or None)
        - 命中邏輯：低於門檻觸發（分數 <= threshold）
        - 命中邏輯：優先用模板 threshold；無則用類型 threshold；再無則用 default_threshold / manifest.default_threshold
        - report=True 會回傳一個 JSON-like dict，包含每模板分數與命中判斷
        - 建議的 templates_manifest.json 例：
          {
            "default_threshold": 0.80,
            "types": {
              "MOREPUFF": {
                "threshold": 0.80,
                "templates": [
                  { "file": "MOREPUFF.png", "threshold": 0.85 },
                  { "file": "MOREPUFF_freeze.png", "mask": "MOREPUFF_mask.png" }
                ]
              }
            }
          }
        """
        if image_bgr is None or image_bgr.size == 0:
            logging.warning("[Template] 輸入影像為空，略過比對")
            return None, None

        # 用來回傳詳細分數資訊（僅在 return_report=True 時有意義）
        report = {"type": type_name, "templates": []}

        if self.manifest is None:
            # 無 manifest：退回舊邏輯（全模板掃描，以 default_threshold 當高分門檻，這裡直接反轉成「低於門檻觸發」也可）
            thr = default_threshold if default_threshold is not None else 0.8
            # 取得最高分模板
            gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
            best_name, best_score = None, float("-inf")
            for name, tpl in self.templates:
                if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
                    continue
                res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)
                if max_val > best_score:
                    best_name, best_score = name, float(max_val)
            # 低於門檻才觸發
            if best_name is not None and best_score <= thr:
                logging.warning(f"[Template] 低分觸發（無 manifest）：{best_name} score={best_score:.3f} <= thr {thr:.2f}")
                if return_report:
                    report["templates"].append(
                        {"file": best_name, "score": float(best_score), "thr": float(thr), "hit": True}
                    )
                    return best_name, report
                return best_name
            logging.info(f"[Template] 未觸發（無 manifest）：best={best_name} {best_score:.3f} > thr {thr:.2f}")
            if return_report:
                if best_name is not None:
                    report["templates"].append(
                        {"file": best_name, "score": float(best_score), "thr": float(thr), "hit": False}
                    )
                return None, report
            return None

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

        types = self.manifest.get("types", {})
        type_cfg = types.get(type_name or "", {})
        type_threshold = type_cfg.get("threshold", None)
        eff_default_thr = default_threshold if default_threshold is not None else self.manifest.get("default_threshold", 0.8)
        tpl_specs = type_cfg.get("templates", [])

        # ===== 依 when 條件過濾可用模板（方案 B 核心）=====
        # 讓 matcher 能讀到當前 Runner 的設定（由呼叫端注入 self.matcher.cfg）
        rtmp  = getattr(getattr(self, "cfg", None), "rtmp", "") or ""
        title = getattr(getattr(self, "cfg", None), "game_title_code", "") or ""

        def _match_when(cond: Optional[dict]) -> bool:
            if not cond:
                return True
            # 精確比對
            if "rtmp" in cond and cond["rtmp"] != rtmp:
                return False
            if "title" in cond and cond["title"] != title:
                return False
            # 包含判斷（可選）
            contains = cond.get("contains", {})
            if isinstance(contains, dict):
                for k, v in contains.items():
                    src = ""
                    if k == "rtmp":
                        src = rtmp
                    elif k == "title":
                        src = title
                    else:
                        continue
                    if v not in src:
                        return False
            return True
        
        filtered_specs = [s for s in tpl_specs if _match_when(s.get("when"))]
        if not filtered_specs:
            logging.info(f"[Template] 類型 {type_name} 在當前條件下無可用模板（rtmp='{rtmp}', title='{title}'）")
            return None
        
        tpl_specs = filtered_specs
        logging.info(f"[Template] 類型 {type_name}：符合條件模板 {len(tpl_specs)} 張（rtmp='{rtmp}', title='{title}'）")
        # ===== 過濾結束 =====

        if not tpl_specs:
            logging.warning(f"[Template] manifest 中類型 '{type_name}' 沒有模板清單，略過")
            return None
       
        # 逐一比對，任何一張「分數 <= 自己門檻」即觸發
        for spec in tpl_specs:
            file = spec.get("file")
            if not file:
                continue

            tpl_img = self._find_file_image(file)
            if tpl_img is None:
                logging.warning(f"[Template] 找不到模板影像：{file}")
                continue

            # 尺寸檢查
            if gray.shape[0] < tpl_img.shape[0] or gray.shape[1] < tpl_img.shape[1]:
                logging.info(f"[Template] 跳過（畫面比模板小）：{file}")
                continue

            # 取得遮罩（若有）
            mask = self._resolve_mask(spec.get("mask"))

            # 以 TM_CCOEFF_NORMED 比對（OpenCV 4.2+ 支援 mask）
            res = cv2.matchTemplate(gray, tpl_img, cv2.TM_CCOEFF_NORMED, mask=mask)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)

            # 此模板有效門檻（模板 > 類型 > 預設）
            tpl_thr = float(spec.get("threshold", type_threshold if type_threshold is not None else eff_default_thr))
            hit = (max_val <= tpl_thr)  # ★ 低於門檻觸發
            logging.info(f"[Template][{type_name}][{getattr(self, 'current_game', 'NA')}] {file} → score={max_val:.5f} thr={tpl_thr:.2f} hit={hit}")

            if return_report:
                report["templates"].append(
                    {"file": file, "score": float(max_val), "thr": float(tpl_thr), "hit": bool(hit)}
                )

            if hit:
                logging.warning(f"[Template][{type_name}][{getattr(self, 'current_game', 'NA')}] 低分觸發：{file} (score={max_val:.3f} <= thr {tpl_thr:.2f})")
                if return_report:
                    return file, report
                return file
        
        logging.info(f"[Template][{type_name}][{getattr(self, 'current_game', 'NA')}] 未觸發（已比對 {len(tpl_specs)} 張模板）")
        if return_report:
            return None, report
        return None

    def detect_by_manifest_fast(
        self,
        image_bgr: np.ndarray,
        type_name: Optional[str],
        *,
        default_threshold: Optional[float] = None,
        max_templates: int = 2,
    ) -> Optional[str]:
        """
        快速模板比對版本：
        - 限制比對的模板數量
        - 跳過複雜的條件過濾
        - 優化性能，適合超快頻率使用
        """
        if image_bgr is None or image_bgr.size == 0:
            return None

        if self.manifest is None:
            # 無 manifest：使用快速全掃描
            thr = default_threshold if default_threshold is not None else 0.8
            gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
            best_name, best_score = None, float("-inf")
            
            # 限制比對數量
            templates_to_check = list(self.templates.items())[:max_templates]
            for name, tpl in templates_to_check:
                if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
                    continue
                res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)
                if max_val > best_score:
                    best_name, best_score = name, float(max_val)
            
            if best_name is not None and best_score <= thr:
                return best_name
            return None

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        types = self.manifest.get("types", {})
        type_cfg = types.get(type_name or "", {})
        type_threshold = type_cfg.get("threshold", None)
        eff_default_thr = default_threshold if default_threshold is not None else self.manifest.get("default_threshold", 0.8)
        tpl_specs = type_cfg.get("templates", [])

        if not tpl_specs:
            return None
        
        # 限制比對數量
        tpl_specs = tpl_specs[:max_templates]
        
        # 快速比對（跳過複雜的條件過濾）
        for spec in tpl_specs:
            file = spec.get("file")
            if not file:
                continue

            tpl_img = self._find_file_image(file)
            if tpl_img is None:
                continue

            # 尺寸檢查
            if gray.shape[0] < tpl_img.shape[0] or gray.shape[1] < tpl_img.shape[1]:
                continue

            # 快速比對（不使用 mask）
            res = cv2.matchTemplate(gray, tpl_img, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)

            # 此模板有效門檻
            tpl_thr = float(spec.get("threshold", type_threshold if type_threshold is not None else eff_default_thr))
            hit = (max_val <= tpl_thr)
            
            if hit:
                return file
        
        return None
            
        logging.info(f"[Template] 未觸發（類型 {type_name} 的所有模板皆高於各自門檻）")
        return None

    # ---------- 原本 detect_by_type / detect（保留相容） ----------
    def detect_by_type(
        self,
        image_bgr: np.ndarray,
        type_name: Optional[str],
        threshold: float = 0.40,
        log_top_n: int = 0,
        debug: bool = False,
        debug_dir: Optional[Path] = None,
        top_k_boxes: int = 0,
        nms_iou: float = 0.3,
        save_topk_heatmaps: bool = False,
    ) -> Optional[str]:
        """備用舊行為：依類型名稱（以資料夾/前綴推斷）做比對；建議改用 manifest"""
        if image_bgr is None or image_bgr.size == 0:
            return None
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

        # 若未建立 type 索引，退回全掃
        # 這裡簡化：直接用 self.templates（全掃）
        scores = []
        for name, tpl in self.templates:
            if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
                continue
            res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            scores.append((name, float(max_val), max_loc))
            if log_top_n == 0:
                logging.info(f"[Template][{type_name or 'ALL'}] {name} → {max_val:.5f}")

        if not scores:
            return None
        best_name, best_score, _ = max(scores, key=lambda x: x[1])
        return best_name if best_score >= threshold else None

    def detect(self, image_bgr: np.ndarray, threshold: float = 0.40, log_top_n: int = 0, debug: bool = False, debug_dir: Optional[Path] = None,) -> Optional[str]:
        """備用舊行為：全模板掃描"""
        if image_bgr is None or image_bgr.size == 0:
            return None
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        scores = []
        for name, tpl in self.templates:
            if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
                continue
            res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            scores.append((name, float(max_val)))
        if not scores:
            return None
        best_name, best_score = max(scores, key=lambda x: x[1])
        return best_name if best_score >= threshold else None


# =========================== FFmpeg 截圖 ===========================
class FFmpegRunner:
    """以 FFmpeg 針對 RTMP 取單張快照；若失敗或逾時回傳 False"""

    def __init__(self, ffmpeg_path: Path):
        self.ffmpeg = ffmpeg_path

    def snapshot(self, rtmp_url: str, output: Path, timeout: float = 5.0) -> bool:
        """
        從 RTMP 串流截取單張畫面
        
        參數:
            rtmp_url (str): RTMP 串流 URL（不記錄到日誌以避免洩露）
            output (Path): 輸出圖片路徑
            timeout (float): 執行超時時間（秒），預設 5.0 秒
            
        返回:
            bool: True 表示截圖成功，False 表示失敗或超時
            
        流程:
        1. 建立 FFmpeg 命令（-frames:v 1 只取單張，-q:v 2 提高品質）
        2. 執行 FFmpeg 子程序
        3. 檢查輸出檔案是否存在
        
        異常處理:
        - 超時：記錄警告並返回 False
        - FFmpeg 執行失敗：記錄警告並返回 False
        - 檔案不存在：返回 False
        
        注意:
        - 不會在日誌中記錄完整的 RTMP URL
        - 使用 subprocess.DEVNULL 隱藏 FFmpeg 輸出
        """
        cmd = [str(self.ffmpeg), "-y", "-i", rtmp_url, "-frames:v", "1", "-q:v", "2", str(output)]
        try:
            import subprocess
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
            return output.exists()
        except subprocess.TimeoutExpired:
            logging.warning(f"FFmpeg 截圖超時（{timeout}s）")
            return False
        except FileNotFoundError:
            logging.error("找不到 FFmpeg 執行檔")
            return False
        except Exception as e:
            logging.warning(f"FFmpeg 截圖失敗: {e}")
            return False


# =========================== 404 頁面檢測 ===========================
def is_404_page(driver):
    """
    檢測當前頁面是否為 404 錯誤頁面
    
    參數:
        driver: Selenium WebDriver 實例
        
    返回:
        bool: True 表示是 404 頁面，False 表示不是
        
    檢測方法:
        1. 檢查頁面標題（包含 "404" 或 "not found"）
        2. 檢查頁面內容（包含 "404 not found" 或 "nginx/1.20.1"）
        3. 檢查 URL（包含 "404"）
        
    異常處理:
        - 檢測過程中的例外：記錄 debug 日誌並返回 False（保守策略）
        
    注意:
        - 使用保守策略：無法確定時返回 False
        - 避免誤判導致不必要的刷新
    """
    try:
        # 檢查頁面標題
        page_title = driver.title.lower()
        if "404" in page_title or "not found" in page_title:
            logging.warning("🚨 檢測到 404 頁面（通過標題）")
            return True
        
        # 檢查頁面內容
        page_source = driver.page_source.lower()
        if "404 not found" in page_source or "nginx/1.20.1" in page_source:
            logging.warning("🚨 檢測到 404 頁面（通過內容）")
            return True
        
        # 檢查 URL
        current_url = driver.current_url.lower()
        if "404" in current_url:
            logging.warning("🚨 檢測到 404 頁面（通過 URL）")
            return True
        
        return False
        
    except Exception as e:
        logging.debug(f"檢測 404 頁面時發生錯誤: {e}")
        return False


# =========================== 域模型（設定） ===========================
@dataclass
class GameConfig:
    """單一機台／測試目標的設定模型（來自 game_config.json 的一筆）"""
    url: str
    rtmp: Optional[str] = None
    rtmp_url: Optional[str] = None
    game_title_code: Optional[str] = None
    template_type: Optional[str] = None  # ✅ 新增：可直接指定類型（覆蓋推斷）
    # ✅ 只針對特定機器啟用的「錯誤畫面」模板類型（例如 RTMP error 畫面）
    # 未設定時保持舊行為，不會多做任何比對
    error_template_type: Optional[str] = None
    enabled: bool = True
    enable_recording: bool = True  # ✅ 新增：是否啟用錄製功能
    enable_template_detection: bool = True  # ✅ 新增：是否啟用模板偵測（高頻率時可關閉）


# =========================== 遊戲執行器 ===========================
def infer_template_type(game_title_code: Optional[str], keyword_actions: Dict[str, List[str]], machine_actions: Dict[str, Tuple[List[str], bool]]) -> Optional[str]:
    """
    從 game_title_code 內含的關鍵字，推斷模板 type。
    先看 machine_actions 的 key，再看 keyword_actions 的 key；第一個命中的就回傳。
    """
    if not game_title_code:
        return None
    for kw in machine_actions.keys():
        if kw and kw in game_title_code:
            return kw
    for kw in keyword_actions.keys():
        if kw and kw in game_title_code:
            return kw
    return None


class GameRunner:
    """
    掌管單一機台的整個流程：
    - 啟動 Edge，進入 URL
    - 在 Lobby 找遊戲卡片 -> Join
    - 迴圈地：檢查餘額 -> 點擊 Spin -> 特殊流程 -> RTMP 偵測
    """

    def __init__(
        self,
        config: GameConfig,
        matcher: TemplateMatcher,
        ffmpeg: FFmpegRunner,
        lark: LarkClient,
        keyword_actions: Dict[str, List[str]],
        machine_actions: Dict[str, Tuple[List[str], bool]],
    ):
        self.cfg = config
        self.matcher = matcher
        self.ffmpeg = ffmpeg
        self.lark = lark
        self.keyword_actions = keyword_actions          # ex: {"BULL": ["X1","X2"]}
        self.machine_actions = machine_actions          # ex: {"BULL": (["X1","X2"], True)}
        self.driver = None
        self._rec_proc = None          # type: Optional[subprocess.Popen]
        self._rec_end_at = 0.0         # 錄影結束時間（epoch 秒）
        self._rec_name = None          # 正在錄的檔名前綴（rtmp 名稱）
        self._auto_pause = False   # 只暫停本 GameRunner，不影響別台
        self._last_balance = None      # 記錄上次的餘額，用於檢測變化
        self._no_change_count = 0      # 記錄連續無變化的次數
        self._check_interval = 10      # 每 10 次檢查一次
        self._spin_count = 0          # 用於間隔檢測的計數器
        self._last_404_check_time = 0.0  # 上次 404 檢測的時間戳
        self._404_check_interval = 30.0  # 404 檢測間隔（秒）

        # ✅ 依 game_config 指定或 game_title_code 推斷模板類型，供比對時只用該類型模板
        self.template_type: Optional[str] = (
            config.template_type or infer_template_type(config.game_title_code, keyword_actions, machine_actions)
        )
        logging.info(f"[Template] 類型設定：game='{config.game_title_code}' → type='{self.template_type}'")

        # ✅ 針對個別機器額外指定「錯誤畫面」專用模板類型
        # 若未設定，則維持原本只用 self.template_type 的流程
        self.error_template_type: Optional[str] = getattr(config, "error_template_type", None)
        if self.error_template_type:
            logging.info(
                f"[Template] 錯誤畫面類型設定：game='{config.game_title_code}' → error_type='{self.error_template_type}'"
            )

    # ----------------- 404 頁面檢測與刷新 -----------------
    def _check_and_refresh_if_404(self):
        """
        定時檢測 404 頁面並自動刷新
        
        流程:
        1. 檢查是否到達檢測間隔（預設 30 秒）
        2. 檢測當前頁面是否為 404（檢查標題、內容、URL）
        3. 若為 404，執行刷新流程：
           - 先嘗試 refresh()
           - 若仍為 404，重新載入原始 URL
           - 驗證是否成功恢復
        
        返回:
            bool: True 表示執行過刷新，False 表示未到達檢測時間或無需刷新
            
        異常處理:
        - 檢測過程中的例外：記錄錯誤並返回 False
        - 刷新過程中的例外：記錄錯誤並返回 False
        
        注意:
        - 不會在日誌中記錄完整的 URL
        - 只記錄 RTMP 名稱（如果有的話）
        """
        try:
            current_time = time.time()
            
            # 檢查是否到達檢測間隔
            if current_time - self._last_404_check_time < self._404_check_interval:
                return False  # 尚未到達檢測時間
            
            # 更新檢測時間
            self._last_404_check_time = current_time
            
            # 檢測 404 頁面
            if is_404_page(self.driver):
                logging.warning(f"🚨 [{self.cfg.rtmp or 'Unknown'}] 檢測到 404 頁面，準備刷新...")
                
                # 刷新頁面
                try:
                    self.driver.refresh()
                    logging.info(f"✅ [{self.cfg.rtmp or 'Unknown'}] 頁面已刷新")
                    time.sleep(3.0)  # 等待頁面加載
                    
                    # 再次檢測是否還是 404
                    if is_404_page(self.driver):
                        logging.error(f"❌ [{self.cfg.rtmp or 'Unknown'}] 刷新後仍然是 404 頁面")
                        
                        # 嘗試重新加載原始 URL
                        logging.info(f"🔄 [{self.cfg.rtmp or 'Unknown'}] 嘗試重新加載原始 URL...")
                        self.driver.get(self.cfg.url)
                        time.sleep(3.0)  # 等待頁面加載
                        
                        if is_404_page(self.driver):
                            logging.error(f"❌ [{self.cfg.rtmp or 'Unknown'}] 重新加載後仍然是 404 頁面")
                        else:
                            logging.info(f"✅ [{self.cfg.rtmp or 'Unknown'}] 重新加載成功")
                    else:
                        logging.info(f"✅ [{self.cfg.rtmp or 'Unknown'}] 刷新成功，頁面正常")
                    
                    return True
                    
                except Exception as e:
                    logging.error(f"❌ [{self.cfg.rtmp or 'Unknown'}] 刷新頁面時發生錯誤: {e}")
                    return False
            else:
                logging.debug(f"✅ [{self.cfg.rtmp or 'Unknown'}] 頁面正常，無需刷新")
                return False
                
        except Exception as e:
            logging.error(f"❌ [{self.cfg.rtmp or 'Unknown'}] 檢測 404 頁面時發生錯誤: {e}")
            return False

    # ----------------- 瀏覽器建立 -----------------
    def _build_driver(self):
        """
        建立與回傳 Edge WebDriver
        
        流程：
        1. 設定 Edge 選項（User-Agent、視窗大小、無痕模式）
        2. 優先使用同目錄的 msedgedriver.exe
        3. 若不存在，嘗試使用 webdriver_manager 自動下載
        4. 建立 WebDriver 並載入遊戲 URL
        
        返回:
            webdriver.Edge: 已載入遊戲 URL 的 WebDriver 實例
            
        異常:
            RuntimeError: 找不到 msedgedriver.exe 且未安裝 webdriver_manager
            Exception: 瀏覽器啟動或載入 URL 失敗
        """
        edge_options = webdriver.EdgeOptions()
        # 偽裝 iPhone UA（頁面走行動版流程）
        edge_options.add_argument(
            "--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.127 Mobile Safari/537.36"
        )
        edge_options.add_argument("--window-size=432,859")
        edge_options.add_argument("--incognito")

        try:
            if EDGEDRIVER_EXE.exists():
                service = Service(executable_path=str(EDGEDRIVER_EXE))
            else:
                if EdgeChromiumDriverManager is None:
                    raise RuntimeError("找不到 msedgedriver.exe，且未安裝 webdriver_manager")
                path = EdgeChromiumDriverManager().install()
                service = Service(executable_path=path)

            drv = webdriver.Edge(service=service, options=edge_options)
            # 載入 URL（不記錄完整 URL 以避免洩露敏感資訊）
            drv.get(self.cfg.url)
            logging.info(f"瀏覽器已載入遊戲 URL（rtmp={self.cfg.rtmp or 'N/A'}）")
            return drv
        except RuntimeError:
            raise
        except Exception as e:
            logging.error(f"建立或載入瀏覽器時發生錯誤: {e}")
            raise
    
    def _is_recording_active(self) -> bool:
        """
        檢查目前是否有錄影進行中
        
        返回:
            bool: True 表示錄影進行中，False 表示未錄影或已結束
            
        流程:
        1. 檢查錄影程序是否存在
        2. 檢查程序是否仍在運行（poll() 返回 None 表示運行中）
        3. 若程序已結束，清理內部狀態
        
        注意:
        - 程序結束後會自動清理狀態，無需手動調用清理函數
        """
        if self._rec_proc is None:
            return False
        try:
            if self._rec_proc.poll() is None:
                return True
        except Exception as e:
            logging.debug(f"檢查錄影程序狀態時發生錯誤: {e}")
            # 程序可能已異常終止，清理狀態
            self._rec_proc = None
            self._rec_end_at = 0.0
            self._rec_name = None
            return False
        # 程序已結束，清掉狀態
        self._rec_proc = None
        self._rec_end_at = 0.0
        self._rec_name = None
        return False
    
    def _start_recording(self, name: str, url: str, duration_sec: int = 120, ts: Optional[str] = None) -> None:
        """
        使用 FFmpeg 錄製 RTMP 串流
        
        參數:
            name (str): 錄影檔名前綴（通常是 RTMP 名稱）
            url (str): RTMP 串流 URL
            duration_sec (int): 錄影時長（秒），預設 120 秒
            ts (Optional[str]): 時間戳，用於檔案命名。若為 None，自動生成
            
        流程:
        1. 檢查是否啟用錄製功能
        2. 生成輸出檔案路徑
        3. 建立 FFmpeg 命令（H.264 + AAC 編碼）
        4. 啟動 FFmpeg 子程序
        5. 記錄錄影狀態（程序、結束時間、檔名）
        6. 推播 Lark 通知（可選）
        
        異常處理:
        - 錄製功能停用：直接返回，不執行錄影
        - FFmpeg 啟動失敗：記錄錯誤，不拋出例外（避免中斷主流程）
        """
        # 檢查是否啟用錄製功能
        if not self.cfg.enable_recording:
            logging.info(f"[{name}] 錄製功能已停用，跳過錄影")
            return
            
        if ts is None:
            ts = time.strftime("%Y%m%d_%H%M%S")
        out_mp4 = SCREENSHOT_RTMP / f"{name}_{ts}.mp4"
        cmd = [
            str(FFMPEG_EXE), "-y",
             
            # —— Input 調優 —— 
            "-fflags", "nobuffer",
            "-rtmp_live", "live",
            "-i", url,
        
            # —— 目標時長 —— 
            "-t", str(duration_sec),
            
            # —— 重新編碼（低延遲、關鍵幀密度）——
            "-c:v", "libx264",
            "-preset", "veryfast",           # 或 ultrafast（更省 CPU / 畫質稍差）
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-g", "25",                      # 25fps ≈ 每 1 秒一個 I 幀（依來源 fps 調整）
            "-keyint_min", "25",
            "-sc_threshold", "0",            # 固定 GOP，避免 scene-cut 打破 keyframe 間距
            
            "-c:a", "aac",
            "-b:a", "128k",
            
            # —— MP4 容器 —— 
            "-movflags", "+faststart",
            "-f", "mp4",
            
            str(out_mp4),
            ]

        try:
            self._rec_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._rec_end_at = time.time() + duration_sec
            self._rec_name = name
            logging.warning(f"[Record] 開始錄影 {duration_sec}s → {out_mp4.name}")

            # 記錄錄影開始時間，後面 spin_forever 會用
            self._rec_started_at = time.time()
            # 可選：推播開始錄影（不包含完整路徑，避免洩露系統路徑）
            try:
                self.lark.send_text(f"📹 [{name}] 開始錄影 {duration_sec}s：{out_mp4.name}")
            except Exception as e:
                logging.debug(f"推播錄影通知失敗: {e}")
        except FileNotFoundError as e:
            logging.error(f"[Record] 找不到 FFmpeg 執行檔: {e}")
        except subprocess.SubprocessError as e:
            logging.error(f"[Record] FFmpeg 子程序啟動失敗: {e}")
        except Exception as e:
            logging.error(f"[Record] 無法啟動 FFmpeg 錄影: {e}\n{traceback.format_exc()}")

    def _maybe_cleanup_finished_recording(self):
        """如果錄影已結束，清理內部狀態（非必要，但讓狀態即時）"""
        if self._rec_proc is not None and self._rec_proc.poll() is not None:
            logging.info("[Record] 錄影結束")
            self._rec_proc = None
            self._rec_end_at = 0.0
            self._rec_name = None


    # ----------------- Lobby / Join 流程 -----------------
    def scroll_and_click_game(self, game_title_code: str) -> bool:
        """
        從大廳進入指定遊戲
        
        參數:
            game_title_code (str): 遊戲標題代碼，用於匹配遊戲卡片
            
        返回:
            bool: True 表示成功進入遊戲（或已在遊戲中），False 表示失敗
            
        流程:
        1. 檢查是否已在遊戲中（尋找 Spin 按鈕）
        2. 在大廳尋找包含 game_title_code 的遊戲卡片
        3. 滾動到卡片並點擊
        4. 尋找並點擊 Join 按鈕（如果存在）
        5. 執行 keyword_actions（如果匹配到關鍵字）
        
        異常處理:
        - 找不到遊戲卡片：記錄警告並返回 False
        - Join 按鈕不存在：視為正常情況，繼續流程
        - 點擊失敗：記錄錯誤但不拋出例外
        - keyword_actions 執行失敗：記錄警告但不中斷流程
        
        注意:
        - Join 按鈕可能不會每次出現，這是正常情況
        - 即使 Join 失敗，也會嘗試執行 keyword_actions
        """
        try:
            items = wait_for_all(self.driver, By.ID, "grid_gm_item", timeout=10)
            for item in items:
                title = item.get_attribute("title")
                if title and game_title_code in title:
                    if not safe_click(self.driver, item):
                        continue
                    logging.info(f"點擊遊戲卡片: {title}")
                    time.sleep(1.2)

                    # Join 按鈕不一定是卡片內部 DOM；改抓全局 gm-info-box
                    # 注意：Join 按鈕可能不會每次出現，這是正常的
                    try:
                        join_btns = wait_for_all(
                            self.driver,
                            By.XPATH,
                            "//div[contains(@class, 'gm-info-box')]//span[normalize-space(text())='Join']",
                            timeout=3,  # 縮短超時時間，快速判斷是否存在
                        )
                        for btn in join_btns:
                            try:
                                if btn.is_displayed() and safe_click(self.driver, btn):
                                    logging.info("點擊 Join 進入遊戲")
                                    time.sleep(3.0)
                                    break
                            except Exception as e:
                                # 處理 stale element reference 或其他錯誤，直接跳過
                                logging.debug(f"點擊 Join 時發生錯誤（已跳過）: {e}")
                    except TimeoutException:
                        # Join 按鈕不存在是正常的，直接跳過
                        logging.info("Join 按鈕未出現（這是正常的），跳過 Join 步驟")
                    except Exception as e:
                        # 其他錯誤也直接跳過，不重試
                        logging.info(f"Join 按鈕查找失敗（已跳過）: {e}")
                    
                    # ✅ 無論 Join 是否成功，都嘗試執行 keyword_actions
                    # 因為可能已經通過其他方式進入遊戲（例如直接點擊卡片就進入）
                    if game_title_code:
                        for kw, positions in self.keyword_actions.items():
                            if kw in game_title_code:
                                logging.info(f"嘗試執行 keyword_actions: {kw} -> {positions}")
                                try:
                                    # 等待一下確保頁面穩定
                                    time.sleep(1.0)
                                    self.click_multiple_positions(positions)
                                    logging.info(f"✅ keyword_actions 執行成功: {kw} -> {positions}")
                                    time.sleep(1.0)
                                except Exception as kw_err:
                                    logging.warning(f"執行 keyword_actions 時發生錯誤: {kw_err}")
                                break  # 只執行第一個匹配的關鍵字
                    
                    # 無論 Join 是否成功，都返回 True 讓流程繼續
                    return True
                        
            logging.warning(f"大廳找不到遊戲: {game_title_code}")
        except Exception as e:
            logging.error(f"scroll_and_click_game 失敗: {e}")
            import traceback
            logging.error(traceback.format_exc())
        return False

    def click_multiple_positions(self, positions: List[str], click_take: bool = False):
        """
        依序點擊多個座標位置
        
        參數:
            positions (List[str]): 座標清單，格式為 ["X,Y", "X,Y", ...]
            click_take (bool): 是否在點擊完所有座標後，額外點擊 Take 按鈕，預設 False
            
        流程:
        1. 依序遍歷 positions 清單
        2. 對每個座標，尋找頁面上文字內容為該座標的 span 元素
        3. 點擊找到的元素
        4. 若 click_take=True，額外點擊 Take 按鈕
        
        異常處理:
        - 找不到座標元素：記錄警告但繼續下一個座標
        - 點擊失敗：記錄警告但繼續下一個座標
        - Take 按鈕不存在：靜默失敗（不記錄錯誤）
        
        注意:
        - 座標格式為 "X,Y"（例如："5,32"）
        - 每個座標點擊後等待 0.2 秒
        - 即使部分座標失敗，也會繼續執行剩餘座標
        """
        for pos in positions:
            try:
                elems = wait_for_all(self.driver, By.XPATH, f"//span[normalize-space(text())='{pos}']", timeout=2.5)
                if elems:
                    safe_click(self.driver, elems[0])
                    logging.info(f"已點擊座標位: {pos}")
                    time.sleep(0.4)
            except TimeoutException:
                logging.warning(f"找不到座標位 {pos}（超時 2.5 秒）")
            except Exception as e:
                logging.warning(f"點擊座標位 {pos} 時發生錯誤: {e}")

        if click_take:
            try:
                take_btn = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, ".my-button.btn_take"))
                )
                safe_click(self.driver, take_btn)
                logging.info("已點擊 Take 按鈕")
            except TimeoutException:
                logging.debug("找不到 Take 按鈕（超時 3 秒）")
            except Exception as e:
                logging.warning(f"點擊 Take 按鈕時發生錯誤: {e}")

    # ----------------- Spin 迴圈（核心） -----------------
    def _is_in_game(self) -> bool:
        """
        檢查當前頁面是否在遊戲中（而非大廳）
        
        返回:
            bool: True 表示在遊戲中，False 表示在大廳
            
        檢測邏輯:
        1. 檢查遊戲中的指標元素（Spin 按鈕、餘額顯示）
        2. 檢查大廳特有的元素（遊戲卡片網格）
        3. 如果都找不到，預設認為在遊戲中（保守策略）
        
        異常處理:
        - 元素查找失敗：視為在遊戲中（保守策略）
        - 其他例外：記錄警告並視為在遊戲中
        
        注意:
        - 使用保守策略：無法確定時預設認為在遊戲中
        - 避免誤判導致流程中斷
        """
        try:
            # 檢查遊戲中的指標元素
            game_indicators = [
                ".my-button.btn_spin",      # Spin 按鈕
                ".balance-bg.hand_balance", # 餘額顯示
                ".h-balance.hand_balance",  # 特殊機台餘額顯示
            ]
            
            for indicator in game_indicators:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, indicator)
                    if elements and any(elem.is_displayed() for elem in elements):
                        return True
                except Exception:
                    continue
            
            # 檢查大廳特有的元素（相反的指標）
            lobby_indicators = [
                (By.ID, "grid_gm_item"),  # 遊戲卡片網格
            ]
            
            for by, selector in lobby_indicators:
                try:
                    elements = self.driver.find_elements(by, selector)
                    if elements and any(elem.is_displayed() for elem in elements):
                        logging.info("檢測到大廳元素，當前在大廳")
                        return False
                except Exception:
                    continue
            
            # 如果都找不到，預設認為在遊戲中（保守策略）
            logging.debug("無法確定頁面狀態，預設認為在遊戲中")
            return True
            
        except Exception as e:
            logging.warning(f"檢查遊戲狀態時發生錯誤: {e}")
            # 發生錯誤時，預設認為在遊戲中（保守策略）
            return True

    def _parse_balance(self, is_special: bool) -> Optional[int]:
        """
        擷取當前遊戲餘額並轉換為整數
        
        參數:
            is_special (bool): 是否為特殊機台（影響 selector 選擇）
            
        返回:
            Optional[int]: 餘額數值，若無法取得則返回 None
            
        流程:
        1. 根據機台類型選擇對應的 CSS selector
        2. 尋找餘額元素並取得文字
        3. 移除逗號和空白
        4. 只保留數字字元
        5. 轉換為整數
        
        異常處理:
        - 元素不存在：返回 None
        - 文字格式異常：返回 None
        - 轉換失敗：返回 None
        
        注意:
        - 特殊機台（BULLBLITZ、ALLABOARD）使用不同的 selector
        - 容錯處理：只保留數字字元，忽略其他字元
        """
        sel = ".h-balance.hand_balance .text2" if is_special else ".balance-bg.hand_balance .text2"
        try:
            el = self.driver.find_element(By.CSS_SELECTOR, sel)
            txt = (el.text or "").replace(",", "").strip()
            # 容錯：只保留數字
            nums = "".join(ch for ch in txt if ch.isdigit())
            return int(nums) if nums else None
        except NoSuchElementException:
            logging.debug("找不到餘額元素（selector: %s）", sel)
            return None
        except ValueError as e:
            logging.debug(f"餘額轉換失敗: {e}")
            return None
        except Exception as e:
            logging.debug(f"解析餘額時發生錯誤: {e}")
            return None

    def _click_spin(self, is_special: bool) -> bool:
        """
        點擊 Spin 按鈕
        
        參數:
            is_special (bool): 是否為特殊機台（影響 selector 選擇）
            
        返回:
            bool: True 表示成功點擊，False 表示失敗
            
        流程:
        1. 根據機台類型選擇對應的 CSS selector
        2. 等待 Spin 按鈕出現（超時 8 秒）
        3. 使用 safe_click 安全點擊
        
        異常處理:
        - 按鈕不存在或超時：記錄警告並返回 False
        - 點擊失敗：記錄警告並返回 False
        
        注意:
        - 特殊機台使用 ".btn_spin .my-button"
        - 一般機台使用 ".my-button.btn_spin"
        """
        spin_selector = ".btn_spin .my-button" if is_special else ".my-button.btn_spin"
        try:
            btn = wait_for(self.driver, By.CSS_SELECTOR, spin_selector, timeout=8)
            return safe_click(self.driver, btn)
        except TimeoutException:
            logging.warning(f"找不到 Spin 按鈕（selector: {spin_selector}，超時 8 秒）")
            return False
        except Exception as e:
            logging.warning(f"點擊 Spin 時發生錯誤: {e}")
            return False

    def _find_cashout_button(self):
        """
        尋找 Cashout 按鈕，直接定位到 handle-main 底層的按鈕
        避免被 select-main 遮罩層阻擋
        """
        # 優先使用 handle-main 底層的選擇器
        handle_main_selectors = [
            ".handle-main .my-button.btn_cashout",                    # handle-main 內的 cashout 按鈕
            ".handle-main .my-button--normal.btn_cashout",             # handle-main 內的 normal cashout 按鈕
            ".handle-main .my-button.my-button--normal.btn_cashout",  # handle-main 內的完整類別 cashout 按鈕
            ".handle-main .btn_cashout",                               # handle-main 內的簡化 cashout 按鈕
            ".handle-main div[class*='btn_cashout']",                 # handle-main 內包含 cashout 的 div
            ".handle-main button[class*='cashout']",                   # handle-main 內包含 cashout 的 button
        ]
        
        # 嘗試 handle-main 底層的選擇器
        for selector in handle_main_selectors:
            try:
                logging.debug(f"🔍 嘗試 handle-main 選擇器: {selector}")
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                
                for elem in elements:
                    try:
                        # 詳細檢查元素狀態
                        is_displayed = elem.is_displayed()
                        is_enabled = elem.is_enabled()
                        
                        # 檢查元素位置和大小
                        try:
                            location = elem.location
                            size = elem.size
                            has_size = size['width'] > 0 and size['height'] > 0
                        except Exception:
                            has_size = True
                        
                        # 檢查元素是否在 handle-main 內
                        try:
                            handle_main_parent = elem.find_element(By.XPATH, "./ancestor::div[contains(@class, 'handle-main')]")
                            in_handle_main = handle_main_parent is not None
                        except Exception:
                            in_handle_main = False
                        
                        logging.debug(f"🔍 handle-main 元素狀態: displayed={is_displayed}, enabled={is_enabled}, has_size={has_size}, in_handle_main={in_handle_main}")
                        
                        if is_displayed and is_enabled and has_size and in_handle_main:
                            logging.info(f"✅ 找到 handle-main 底層 Cashout 按鈕，使用選擇器: {selector}")
                            logging.debug(f"📍 元素位置: {location}, 大小: {size}")
                            return elem
                        else:
                            logging.debug(f"⚠️ handle-main 元素狀態不符合要求")
                            
                    except Exception as e:
                        logging.debug(f"檢查 handle-main 元素狀態時發生錯誤: {e}")
                        continue
                        
            except Exception as e:
                logging.debug(f"handle-main 選擇器 {selector} 失敗: {e}")
                continue
        
        # 如果 handle-main 選擇器都失敗，嘗試其他備用選擇器
        logging.info("⚠️ handle-main 選擇器都失敗，嘗試備用選擇器...")
        
        # 多個可能的備用選擇器
        backup_selectors = [
            ".my-button.btn_cashout",                    # 原始選擇器
            ".btn_cashout",                               # 簡化版本
            ".my-button--normal.btn_cashout",             # 包含 my-button--normal 的版本
            "div.my-button.btn_cashout",                  # 明確指定 div 元素
            "div[class*='btn_cashout']",                  # div 包含 btn_cashout 類別
            ".my-button.my-button--normal.btn_cashout",  # 修正：正確的 CSS 選擇器格式
            ".my-button .my-button--normal .btn_cashout", # 新增：空格分隔的類別選擇器
            "div.my-button.my-button--normal.btn_cashout", # 新增：明確指定 div 元素
            "button[class*='cashout']",                   # 包含 cashout 的按鈕
            "button[class*='cash']",                      # 包含 cash 的按鈕
            ".my-button[class*='cashout']",               # my-button 類別包含 cashout
            "//div[contains(@class, 'btn_cashout')]",     # XPath 版本 - div 包含 btn_cashout
            "//div[contains(@class, 'my-button') and contains(@class, 'btn_cashout')]", # XPath 組合版本
            "//div[contains(@class, 'my-button--normal') and contains(@class, 'btn_cashout')]", # XPath 新增：包含 my-button--normal
            "//button[contains(@class, 'cashout')]",      # XPath 版本
            "//button[contains(text(), 'Cashout')]",      # 文字內容版本
            "//button[contains(text(), 'Cash')]",         # 簡化文字版本
            "//span[contains(text(), 'Cashout')]",        # span 元素版本
            "//div[contains(@class, 'cashout')]//button", # div 包含 cashout 類別
            "//img[@alt='Button Image']/..",              # 通過 img 的 alt 屬性找到父 div
            "//div[contains(@class, 'my-button') and contains(@class, 'my-button--normal') and contains(@class, 'btn_cashout')]", # XPath 完整版本
        ]
        
        for selector in backup_selectors:
            try:
                if selector.startswith("//"):
                    # XPath 選擇器
                    elements = self.driver.find_elements(By.XPATH, selector)
                else:
                    # CSS 選擇器
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                
                for elem in elements:
                    try:
                        # 詳細檢查元素狀態
                        is_displayed = elem.is_displayed()
                        is_enabled = elem.is_enabled()
                        
                        # 檢查元素位置和大小
                        try:
                            location = elem.location
                            size = elem.size
                            has_size = size['width'] > 0 and size['height'] > 0
                        except Exception:
                            has_size = True
                        
                        logging.debug(f"🔍 元素狀態檢查: displayed={is_displayed}, enabled={is_enabled}, has_size={has_size}")
                        
                        if is_displayed and is_enabled and has_size:
                            logging.info(f"✅ 找到 Cashout 按鈕，使用選擇器: {selector}")
                            logging.debug(f"📍 元素位置: {location}, 大小: {size}")
                            return elem
                        else:
                            logging.debug(f"⚠️ 元素狀態不符合要求: displayed={is_displayed}, enabled={is_enabled}, has_size={has_size}")
                            
                    except Exception as e:
                        logging.debug(f"檢查元素狀態時發生錯誤: {e}")
                        continue
                        
            except Exception as e:
                logging.debug(f"選擇器 {selector} 失敗: {e}")
                continue
        
        logging.warning("⚠️ 所有 Cashout 按鈕選擇器都失敗")
        
        # 增強診斷：檢查遮罩層問題
        try:
            logging.info("🔍 檢查遮罩層問題...")
            
            # 檢查 select-main 遮罩層
            select_main_elements = self.driver.find_elements(By.CSS_SELECTOR, ".select-main")
            if select_main_elements:
                logging.info(f"🎭 找到 {len(select_main_elements)} 個 select-main 遮罩層")
                for i, mask in enumerate(select_main_elements):
                    try:
                        is_displayed = mask.is_displayed()
                        location = mask.location
                        size = mask.size
                        logging.info(f"  遮罩層 {i+1}: displayed={is_displayed}, location={location}, size={size}")
                    except Exception:
                        pass
            
            # 檢查 handle-main 元素
            handle_main_elements = self.driver.find_elements(By.CSS_SELECTOR, ".handle-main")
            if handle_main_elements:
                logging.info(f"🎮 找到 {len(handle_main_elements)} 個 handle-main 元素")
                for i, handle in enumerate(handle_main_elements):
                    try:
                        is_displayed = handle.is_displayed()
                        location = handle.location
                        size = handle.size
                        logging.info(f"  handle-main {i+1}: displayed={is_displayed}, location={location}, size={size}")
                        
                        # 檢查 handle-main 內的按鈕
                        buttons_in_handle = handle.find_elements(By.CSS_SELECTOR, ".my-button")
                        logging.info(f"    handle-main {i+1} 內有 {len(buttons_in_handle)} 個按鈕")
                        
                        for j, btn in enumerate(buttons_in_handle):
                            try:
                                class_name = btn.get_attribute("class") or ""
                                btn_location = btn.location
                                btn_size = btn.size
                                logging.info(f"      按鈕 {j+1}: class='{class_name}', location={btn_location}, size={btn_size}")
                            except Exception:
                                pass
                                
                    except Exception:
                        pass
                    
        except Exception as e:
            logging.debug(f"診斷遮罩層時發生錯誤: {e}")
        
        return None

    def _low_balance_exit_and_reenter(self, bal: int, game_title_code: Optional[str]):
        """
        低餘額退出流程：退出遊戲並重新進入
        
        參數:
            bal (int): 當前餘額（用於日誌）
            game_title_code (Optional[str]): 遊戲標題代碼，用於重新進入遊戲
            
        流程:
        1. 點擊 Cashout 按鈕
        2. 點擊 Exit To Lobby 按鈕
        3. 點擊 Confirm 按鈕
        4. 驗證是否成功回到大廳
        5. 重新進入遊戲（如果提供 game_title_code）
        6. 驗證是否成功進入遊戲
        
        異常處理:
        - 找不到 Cashout 按鈕：記錄錯誤並返回 False
        - 找不到 Exit 按鈕：視為正常，直接嘗試 Confirm
        - 退出失敗：記錄錯誤但不拋出例外
        - 重新進入失敗：記錄警告但不拋出例外
        
        返回:
            bool: True 表示退出成功，False 表示失敗
            
        注意:
        - 退出後會等待並驗證是否真的回到大廳
        - 重新進入後會驗證是否真的進入遊戲
        """
        logging.warning(f"BAL 過低（{bal:,}），執行退出流程")
        try:
            quit_btn = self._find_cashout_button()
            if quit_btn:
                safe_click(self.driver, quit_btn)
                time.sleep(1.0)
            else:
                logging.error("❌ 找不到 Cashout 按鈕，無法執行退出流程")
                return False

            try:
                exit_btn = WebDriverWait(self.driver, 2).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, ".function-btn .reserve-btn-gray"))
                    )
                safe_click(self.driver, exit_btn)
                logging.info("[ExitFlow] 已點擊 Exit / Exit To Lobby")
                time.sleep(1.0)
            except TimeoutException:
                logging.info("[ExitFlow] 找不到 Exit，直接嘗試 Confirm")

            confirm_btn = WebDriverWait(self.driver, 2).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//div[normalize-space(text())='Confirm']]"))
            )
            safe_click(self.driver, confirm_btn)
            time.sleep(3.0)
            
            # ✅ 驗證是否成功回到大廳
            if not self._is_in_game():
                logging.info("[ExitFlow] 已成功回到大廳")
            else:
                logging.warning("[ExitFlow] 退出後仍在遊戲中，可能需要額外等待")
                time.sleep(2.0)
        except Exception as e:
            logging.error(f"退出流程失敗: {e}\n{traceback.format_exc()}")
            return False

        # ✅ 重新進入遊戲，並驗證是否成功進入
        if game_title_code:
            logging.info(f"[ExitFlow] 準備重新進入遊戲: {game_title_code}")
            if self.scroll_and_click_game(game_title_code):
                # 等待遊戲加載並驗證是否成功進入
                time.sleep(3.0)
                if self._is_in_game():
                    logging.info("[ExitFlow] 成功重新進入遊戲")
                else:
                    logging.warning("[ExitFlow] 重新進入遊戲後仍在大廳，可能需要額外等待")
                    time.sleep(2.0)
            else:
                logging.warning("[ExitFlow] 重新進入遊戲失敗")

    def _fast_low_balance_exit_and_reenter(self, bal: int, game_title_code: Optional[str]):
        """
        超快頻率的快速退出流程：
        Cashout -> Exit To Lobby -> Confirm
        減少等待時間以保持高速
        """
        logging.warning(f"BAL 過低（{bal}），執行快速退出流程")
        try:
            quit_btn = self._find_cashout_button()
            if quit_btn:
                safe_click(self.driver, quit_btn)
                time.sleep(0.5)  # 減少等待時間
            else:
                logging.error("❌ 找不到 Cashout 按鈕，無法執行快速退出流程")
                return False

            try:
                exit_btn = WebDriverWait(self.driver, 1).until(  # 減少等待時間
                    EC.element_to_be_clickable((By.CSS_SELECTOR, ".function-btn .reserve-btn-gray"))
                    )
                safe_click(self.driver, exit_btn)
                logging.info("[FastExitFlow] 已點擊 Exit / Exit To Lobby")
                time.sleep(0.5)  # 減少等待時間
            except TimeoutException:
                logging.info("[FastExitFlow] 找不到 Exit，直接嘗試 Confirm")

            confirm_btn = WebDriverWait(self.driver, 1).until(  # 減少等待時間
                EC.element_to_be_clickable((By.XPATH, "//button[.//div[normalize-space(text())='Confirm']]"))
            )
            safe_click(self.driver, confirm_btn)
            time.sleep(1.5)  # 減少等待時間
            
            # ✅ 驗證是否成功回到大廳
            if not self._is_in_game():
                logging.info("[FastExitFlow] 已成功回到大廳")
            else:
                logging.warning("[FastExitFlow] 退出後仍在遊戲中，可能需要額外等待")
                time.sleep(1.0)
        except Exception as e:
            logging.error(f"快速退出流程失敗: {e}")

        # ✅ 重新進入遊戲，並驗證是否成功進入
        if game_title_code:
            logging.info(f"[FastExitFlow] 準備重新進入遊戲: {game_title_code}")
            if self.scroll_and_click_game(game_title_code):
                # 等待遊戲加載並驗證是否成功進入
                time.sleep(2.0)  # 快速流程使用較短等待時間
                if self._is_in_game():
                    logging.info("[FastExitFlow] 成功重新進入遊戲")
                else:
                    logging.warning("[FastExitFlow] 重新進入遊戲後仍在大廳，可能需要額外等待")
                    time.sleep(1.0)
            else:
                logging.warning("[FastExitFlow] 重新進入遊戲失敗")

    def _fast_rtmp_check(self, name: str, url: str, threshold: float = 0.80) -> bool:
        """
        超快頻率專用的快速 RTMP 檢測
        
        參數:
            name (str): RTMP 識別名稱（用於日誌和檔案命名）
            url (str): RTMP 串流 URL
            threshold (float): 模板比對門檻，預設 0.80
            
        返回:
            bool: True 表示觸發錄影（一般模板低分觸發），False 表示未觸發或錯誤模板觸發
            
        流程:
        1. 使用較短超時時間（2秒）截圖
        2. 讀取圖片並驗證
        3. 先用原本的模板類型比對（低分觸發）
        4. 若未觸發，檢查錯誤模板類型（高分觸發，只截圖不錄影）
        5. 立即清理截圖（錯誤模板除外）
        
        優化:
        - 跳過重複畫面檢測（節省時間）
        - 限制比對模板數量（max_templates=2）
        - 錯誤模板觸發時保留截圖但不觸發錄影
        
        異常處理:
        - FFmpeg 截圖失敗：返回 False
        - 圖片讀取失敗：清理截圖後返回 False
        - 模板比對例外：清理截圖後返回 False
        """
        logging.info(f"[{name}] 超快頻率快速 RTMP 檢測")
        
        # 使用較短的截圖超時 (2秒)
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = SCREENSHOT_RTMP / f"{name}_{ts}.jpg"
        if not self.ffmpeg.snapshot(url, out, timeout=2.0):
            logging.warning(f"[{name}] 快速檢測 - FFmpeg 擷取失敗或逾時")
            return False

        # 讀取圖片
        img = cv2.imread(str(out))
        if img is None or img.size == 0:
            logging.warning(f"[{name}] 快速檢測 - 讀圖失敗，刪除後跳過")
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
            return False
        
        # 快速模板比對（限制模板數量）
        try:
            self.matcher.current_game = self.cfg.game_title_code or "UnknownGame"
            self.matcher.cfg = self.cfg

            hit = None

            # 1) 先用原本的模板類型比對（維持舊流程，低分觸發）
            if self.template_type:
                hit = self.matcher.detect_by_manifest_fast(
                    img,
                    type_name=self.template_type,
                    default_threshold=threshold,
                    max_templates=2,  # 限制比對數量
                )

            # 2) 若原本類型未觸發，且有為此機台額外指定 error_template_type，
            #    則改用「高分觸發」邏輯再比一次（比分數大則觸發）
            error_hit_file_fast = None
            if hit is None and self.error_template_type and self.error_template_type != self.template_type:
                logging.info(
                    f"[{name}] 快速檢測 - 進行錯誤畫面模板比對（高分觸發），type='{self.error_template_type}'"
                )
                # 為了取得分數細節，error 類型改用完整版 detect_by_manifest
                _, report = self.matcher.detect_by_manifest(
                    img,
                    type_name=self.error_template_type,
                    default_threshold=threshold,
                    return_report=True,
                )
                best_file = None
                best_score = float("-inf")
                error_hit = False
                for item in report.get("templates", []):
                    score = item["score"]
                    thr = item["thr"]
                    hit_high = (score >= thr)
                    logging.info(
                        f"[{name}] ErrorTemplateScore(fast) file={item['file']} "
                        f"score={score:.5f} thr={thr:.2f} hit_high={hit_high} (高分觸發: score>=thr)"
                    )
                    if hit_high:
                        error_hit = True
                        if score > best_score:
                            best_score = score
                            best_file = item["file"]

                if error_hit:
                    error_hit_file_fast = best_file
                    hit = best_file
                    logging.warning(
                        f"[{name}] 🎯 錯誤模板高分觸發（快速檢測）：{best_file} "
                        f"(score={best_score:.5f} >= thr={thr:.2f})"
                    )
                else:
                    logging.info(
                        f"[{name}] 錯誤模板未觸發（快速檢測，所有模板分數皆 < 門檻）"
                    )

        except Exception as e:
            logging.error(f"[{name}] 快速檢測 - 模板比對發生例外：{e}\n{traceback.format_exc()}")
            try:
                out.unlink(missing_ok=True)
            except Exception as cleanup_err:
                logging.debug(f"清理截圖失敗: {cleanup_err}")
            return False
        
        # 針對 error 模板：只截圖、不錄影 → 不刪除截圖並直接返回 False
        if 'error_hit_file_fast' in locals() and error_hit_file_fast:
            logging.info(f"[{name}] 快速檢測：錯誤模板高分觸發，已保留截圖，不觸發錄影")
            return False

        # 其他情況：維持原本流程，立即清理截圖
        try:
            out.unlink(missing_ok=True)
        except Exception:
            pass
        
        if hit is not None:
            logging.warning(f"[{name}] 快速檢測 - 低分觸發：{hit}")
            return True
        
        return False

    def _rtmp_once_check(self, name: str, url: str, threshold: float = 0.80, max_dup: int = 3) -> None:
        """
        針對 RTMP 執行一次截圖 + 模板偵測
        
        參數:
            name (str): RTMP 識別名稱（用於日誌和檔案命名）
            url (str): RTMP 串流 URL（不記錄到日誌以避免洩露）
            threshold (float): 模板比對門檻，預設 0.80
            max_dup (int): 連續重複畫面次數門檻，預設 3
            
        流程:
        1. 檢查是否正在錄影（錄影中跳過檢測，只清理截圖）
        2. 使用 FFmpeg 截圖（超時 5 秒）
        3. 重複畫面檢測（MD5 比對，連續 max_dup 次推播通知）
        4. 模板比對：
           - 先用原本的模板類型（低分觸發 → 錄影）
           - 若未觸發，檢查錯誤模板類型（高分觸發 → 只截圖）
        5. 觸發時保留截圖，未觸發時清理截圖
        
        觸發邏輯:
        - 一般模板：score <= threshold → 啟動錄影 120 秒
        - 錯誤模板：score >= threshold → 只保留截圖，不錄影
        
        異常處理:
        - FFmpeg 截圖失敗：記錄警告並返回
        - 圖片讀取失敗：清理截圖並返回
        - 模板比對例外：保留截圖協助診斷
        """
        # 若已有錄影在進行，先維護一次狀態；錄影中則直接略過「偵測」（但還是清掉截圖）
        if self._is_recording_active():
            ts = time.strftime("%Y%m%d_%H%M%S")
            out = SCREENSHOT_RTMP / f"{name}_{ts}.jpg"
            try:
                if self.ffmpeg.snapshot(url, out, timeout=5.0):
                    try:
                        out.unlink(missing_ok=True)  # 錄影中，任何截圖直接清掉
                    except Exception as cleanup_err:
                        logging.debug(f"錄影中清理截圖失敗: {cleanup_err}")
            except Exception as snapshot_err:
                logging.debug(f"錄影中截圖失敗: {snapshot_err}")
            return

        # 取得一張快照供偵測
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = SCREENSHOT_RTMP / f"{name}_{ts}.jpg"
        try:
            if not self.ffmpeg.snapshot(url, out, timeout=5.0):
                logging.warning(f"[{name}] FFmpeg 擷取失敗或逾時")
                return
        except Exception as e:
            logging.error(f"[{name}] FFmpeg 截圖發生例外: {e}")
            return

        # 重複畫面偵測（以 MD5 比對）
        curr = file_md5(out)
        prev = last_image_hash.get(name)
        if prev == curr:
            cnt = int(last_image_hash.get(f"{name}_dup", "0")) + 1
            last_image_hash[f"{name}_dup"] = str(cnt)
            logging.warning(f"[{name}] 重複圖片 {cnt}/{max_dup}")
            # 重複的這張，立刻刪掉
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
            # 達門檻推播一次後把 counter 歸零
            if cnt >= max_dup:
                try:
                    self.lark.send_text(f"🔄 [{name}] RTMP 畫面連續重複 {cnt} 次，請檢查串流")
                except Exception:
                    pass
                last_image_hash[f"{name}_dup"] = "0"
            return
        else:
            last_image_hash[name] = curr
            last_image_hash[f"{name}_dup"] = "0"

        # 模板偵測（低於門檻觸發錄影）
        img = cv2.imread(str(out))
        if img is None or img.size == 0:
            logging.warning(f"[{name}] 讀圖失敗或為空影像：{out.name}，刪除後跳過")
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
            return
        
        error_hit_file = None  # 標記是否由 error 模板高分觸發
        try:
            self.matcher.current_game = self.cfg.game_title_code or "UnknownGame"
            self.matcher.cfg = self.cfg

            hit = None

            # 1) 先用原本的模板類型比對（維持舊流程，低分觸發）
            if self.template_type:
                hit = self.matcher.detect_by_manifest(
                    img,
                    type_name=self.template_type,   # 僅比對該遊戲類型
                    default_threshold=threshold     # fallback 門檻
                )

            # 2) 若原本類型未觸發，且有為此機台額外指定 error_template_type，
            #    則改用「高分觸發」邏輯再比一次（比分數大則觸發）
            if hit is None and self.error_template_type and self.error_template_type != self.template_type:
                logging.info(
                    f"[{name}] RTMP 檢測 - 進行錯誤畫面模板比對（高分觸發），type='{self.error_template_type}'"
                )
                _, report = self.matcher.detect_by_manifest(
                    img,
                    type_name=self.error_template_type,
                    default_threshold=threshold,
                    return_report=True,
                )
                # 額外輸出 error 模板的分數細節，並改用「score >= thr」作為觸發條件
                best_file = None
                best_score = float("-inf")
                error_hit = False
                for item in report.get("templates", []):
                    score = item["score"]
                    thr = item["thr"]
                    hit_high = (score >= thr)
                    logging.info(
                        f"[{name}] ErrorTemplateScore file={item['file']} "
                        f"score={score:.5f} thr={thr:.2f} hit_high={hit_high} (高分觸發: score>=thr)"
                    )
                    if hit_high:
                        error_hit = True
                        if score > best_score:
                            best_score = score
                            best_file = item["file"]

                if error_hit:
                    error_hit_file = best_file
                    hit = best_file
                    logging.warning(
                        f"[{name}] 🎯 錯誤模板高分觸發：{best_file} "
                        f"(score={best_score:.5f} >= thr={thr:.2f})"
                    )
                else:
                    logging.info(
                        f"[{name}] 錯誤模板未觸發（所有模板分數皆 < 門檻）"
                    )

        except Exception as e:
            logging.error(f"[{name}] 模板比對發生例外：{e}\n{traceback.format_exc()}")
            # 保留截圖協助診斷（不清理）
            return
            
        if hit is not None:
            # 判斷觸發來源：error_template_type（高分觸發，只截圖不錄影），template_type（低分觸發 + 錄影）
            if error_hit_file:
                # ✅ 錯誤模板：只截圖、不錄影（out 已是本次 error 畫面的截圖）
                logging.warning(f"[{name}] 錯誤模板高分觸發：{hit}，僅截圖、不啟動錄影")
                try:
                    self.lark.send_text(f"⚠️ [{name}] 錯誤畫面偵測到（{hit}），已保留截圖，不自動錄影")
                except Exception:
                    pass
                # 不要刪除 out；直接結束
                return
            else:
                # 一般模板：維持原本「低分觸發 + 錄影」流程
                logging.warning(f"[{name}] 低分觸發：{hit}")
                
                if self.cfg.enable_recording:
                    logging.warning(f"[{name}] 開始錄影 120s")
                    try:
                        self.lark.send_text(f"🎯 [{name}] 低分觸發：{hit}\n即刻開始錄影 2 分鐘")
                    except Exception:
                        pass
                    # ★ 自動暫停本機台（不影響其他台）
                    self._auto_pause = True
                    logging.info(f"[{name}]已暫停spin")

                    # ★ 用同一個 ts（與上面快照 out 同名）
                    self._start_recording(name, url, duration_sec=120, ts=ts)     

                    # 等待錄影程序真的起來（最多 3 秒）
                    t0 = time.time()   
                    while time.time() - t0 < 3.0:
                        if self._is_recording_active():
                            break
                        time.sleep(0.1)
                    # ★ 錄影啟動後，恢復本機台 SPIN
                    self._auto_pause = False
                    logging.info(f"[{name}]已重新啟動spin")
                else:
                    # 錄製功能停用，只推播通知
                    logging.info(f"[{name}] 錄製功能已停用，僅推播觸發通知")
                    try:
                        self.lark.send_text(f"🎯 [{name}] 低分觸發：{hit}\n（錄製功能已停用）")
                    except Exception:
                        pass
            
            # 不刪這張截圖（當作觸發證據）
            return
        else:
            # 未觸發 → 清理截圖
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
    
        # 錄影可能剛好在這輪結束（極少數），做個狀態維護
        self._maybe_cleanup_finished_recording()

    def spin_forever(self):
        """
        主要工作迴圈（無限循環直到收到停止訊號）
        
        每輪循環流程:
        1. 檢查暫停狀態（pause_event 或 _auto_pause）
        2. 定時檢測 404 頁面（每 30 秒一次）
        3. 檢查錄影狀態（錄影開始未滿 10 秒時暫停 Spin）
        4. 餘額檢查（Spin 前，低於 20000 執行退出流程）
        5. 檢查是否在遊戲中（退出流程後可能還在大廳）
        6. 點擊 Spin 按鈕
        7. 餘額變化檢測（超快頻率用上次比較，正常頻率用前後比較）
        8. 特殊流程（連續 10 次無變化觸發 machine_actions）
        9. RTMP 檢測（根據頻率和設定執行模板比對）
        10. 動態等待（根據頻率加上隨機抖動）
        
        頻率調整:
        - 超快頻率（≤0.1s）：使用快速餘額檢查、間隔 RTMP 檢測
        - 正常頻率（>0.1s）：使用標準流程
        
        異常處理:
        - 任意例外：記錄錯誤、嘗試 RTMP 截圖、等待 1 秒後繼續
        - KeyboardInterrupt：由外層 run() 處理
        
        停止條件:
        - stop_event 被設置（Ctrl+C 或 Ctrl+Esc）
        """
        game_code = self.cfg.game_title_code or ""
        is_special_game = any(k in game_code for k in SPECIAL_GAMES)

        while not stop_event.is_set():
            while pause_event.is_set() and not stop_event.is_set():
                logging.info("[Loop] 已暫停，等待恢復（Space 解除暫停）")
                time.sleep(0.3)
            try:
                loop_start_time = time.time()  # 記錄循環開始時間
                
                # 獲取當前頻率設定
                with spin_frequency_lock:
                    current_freq = spin_frequency
                
                # ✅ 定時檢測 404 頁面（每 30 秒一次）
                self._check_and_refresh_if_404()
                
                # ✅ 如果正在錄影，並且錄影開始未滿 10 秒，就暫停 spin
                if hasattr(self, "_rec_started_at"):
                    delta = time.time() - self._rec_started_at
                    if delta < 10:
                        logging.info(f"[{game_code}] 錄影開始 {delta:.1f}s，等待到 10 秒才開始 Spin")
                        time.sleep(1.0)
                        continue  # 跳過這輪 loop，不執行 Spin
                # 1) Balance 檢查（Spin 前）
                bal_before = self._parse_balance(is_special=is_special_game)
                if bal_before is not None:
                    if bal_before < 20000:
                        # 所有頻率都執行退出流程，但超快頻率使用快速退出
                        if current_freq <= 0.1:  # 超快頻率使用快速退出流程
                            logging.warning(f"超快頻率({current_freq}s) - 餘額過低({bal_before})，執行快速退出流程")
                            self._fast_low_balance_exit_and_reenter(bal_before, self.cfg.game_title_code)
                            time.sleep(1.0)  # 減少等待時間
                            continue
                        else:  # 正常頻率使用標準退出流程
                            self._low_balance_exit_and_reenter(bal_before, self.cfg.game_title_code)
                            time.sleep(2.0)
                            continue
                else:
                    logging.info("無法取得 BAL，略過本輪餘額檢查")

                # ✅ 檢查是否在遊戲中（退出流程後可能還在大廳）
                if not self._is_in_game():
                    logging.warning(f"{game_code} 檢測到在大廳，先嘗試進入遊戲")
                    if game_code:
                        if self.scroll_and_click_game(game_code):
                            logging.info(f"{game_code} 成功進入遊戲，等待頁面穩定")
                            time.sleep(3.0)  # 等待遊戲加載
                        else:
                            logging.warning(f"{game_code} 無法進入遊戲，跳過本輪")
                            time.sleep(2.0)
                            continue
                    else:
                        logging.warning(f"{game_code} 沒有 game_title_code，無法進入遊戲")
                        time.sleep(2.0)
                        continue

                # 2) 點擊 Spin
                if not self._click_spin(is_special=is_special_game):
                    logging.warning(f"{game_code} 點擊 Spin 失敗，嘗試回廳重進")
                    if game_code:
                        self.scroll_and_click_game(game_code)
                    time.sleep(1.0)
                    continue

                logging.info(f"已點擊 {'特殊' if is_special_game else '一般'} Spin (頻率: {get_current_frequency_status()})")

                # 3) 餘額變化檢測（超快頻率使用快速檢查）
                balance_changed = False
                
                # 根據頻率調整等待時間
                if current_freq <= 0.1:  # 超快頻率
                    time.sleep(0.05)  # 極短等待時間
                    logging.info(f"超快頻率({current_freq}s) - 快速餘額檢查")
                elif current_freq <= 0.5:  # 快速頻率
                    time.sleep(0.2)  # 較短等待時間
                else:  # 正常頻率以上
                    time.sleep(0.5)  # 標準等待時間
                
                bal_after = self._parse_balance(is_special=is_special_game)
                
                # 檢測餘額變化（累積統計模式）
                balance_changed = False
                should_trigger_special = False
                
                if current_freq <= 0.1:  # 超快頻率使用與上次餘額比較
                    if self._last_balance is not None and bal_after is not None:
                        balance_changed = (bal_after != self._last_balance)
                        if balance_changed:
                            logging.info(f"超快頻率餘額變化 (與上次比較): {self._last_balance:,} → {bal_after:,} (變化: {bal_after - self._last_balance:+,})")
                            self._no_change_count = 0  # 重置計數器
                        else:
                            self._no_change_count += 1
                            logging.info(f"超快頻率餘額無變化 (與上次比較): {bal_after:,} (連續無變化: {self._no_change_count}/{self._check_interval})")
                    else:
                        self._no_change_count += 1
                        logging.info(f"超快頻率 - 無法與上次餘額比較，計入無變化: {self._no_change_count}/{self._check_interval}")
                else:  # 正常頻率使用 Spin 前後比較
                    if bal_before is not None and bal_after is not None:
                        balance_changed = (bal_after != bal_before)
                        if balance_changed:
                            logging.info(f"餘額變化: {bal_before:,} → {bal_after:,} (變化: {bal_after - bal_before:+,})")
                            self._no_change_count = 0  # 重置計數器
                        else:
                            self._no_change_count += 1
                            logging.info(f"餘額無變化: {bal_after:,} (連續無變化: {self._no_change_count}/{self._check_interval})")
                    elif self._last_balance is not None and bal_after is not None:
                        # 如果這輪無法取得 Spin 前餘額，但能取得 Spin 後餘額，與上次比較
                        balance_changed = (bal_after != self._last_balance)
                        if balance_changed:
                            logging.info(f"餘額變化 (與上次比較): {self._last_balance:,} → {bal_after:,} (變化: {bal_after - self._last_balance:+,})")
                            self._no_change_count = 0  # 重置計數器
                        else:
                            self._no_change_count += 1
                            logging.info(f"餘額無變化 (與上次比較): {bal_after:,} (連續無變化: {self._no_change_count}/{self._check_interval})")
                    else:
                        self._no_change_count += 1
                        logging.info(f"無法檢測餘額變化，計入無變化: {self._no_change_count}/{self._check_interval}")
                
                # 檢查是否達到觸發特殊流程的條件
                if self._no_change_count >= self._check_interval:
                    should_trigger_special = True
                    logging.info(f"🎯 連續 {self._check_interval} 次無變化，觸發特殊流程！")
                    self._no_change_count = 0  # 重置計數器
                
                # 更新上次餘額記錄
                if bal_after is not None:
                    self._last_balance = bal_after

                # 4) 特殊機台 Spin 後流程（依 actions.json 的 machine_actions）
                # 只有累積 10 次無變化時才執行特殊流程
                if should_trigger_special:
                    for kw, (positions, do_take) in self.machine_actions.items():
                        if game_code and kw in game_code:
                            if current_freq <= 0.1:  # 超快頻率
                                logging.info(f"超快頻率({current_freq}s) - 連續{self._check_interval}次無變化觸發特殊流程: {kw} -> {positions}, take={do_take}")
                            else:
                                logging.info(f"連續{self._check_interval}次無變化觸發特殊流程: {kw} -> {positions}, take={do_take}")
                            self.click_multiple_positions(positions, click_take=do_take)
                            break
                elif balance_changed:
                    logging.info("餘額有變化，重置計數器，繼續 Spin")
                else:
                    logging.info(f"餘額無變化，累積計數: {self._no_change_count}/{self._check_interval}，繼續 Spin")

                # 5) RTMP 單次偵測（可選）
                if self.cfg.rtmp and self.cfg.rtmp_url:
                    # 檢查是否啟用模板偵測（高頻率時可關閉以提升性能）
                    if current_freq <= 0.1:  # 超快頻率使用間隔檢測
                        if not self.cfg.enable_template_detection:
                            logging.info(f"超快頻率({current_freq}s) - 模板偵測已關閉，跳過 RTMP 檢測")
                        else:
                            self._spin_count += 1
                            # 每隔 5 次 Spin 才檢測一次 RTMP
                            if self._spin_count % 5 == 0:
                                logging.info(f"超快頻率({current_freq}s) - 間隔檢測 RTMP (第 {self._spin_count} 次)")
                                if self._fast_rtmp_check(self.cfg.rtmp, self.cfg.rtmp_url, threshold=0.80):
                                    # 快速檢測觸發，執行錄影流程
                                    logging.warning(f"[{self.cfg.rtmp}] 快速檢測觸發，開始錄影 120s")
                                    try:
                                        self.lark.send_text(f"🎯 [{self.cfg.rtmp}] 快速檢測觸發\n即刻開始錄影 2 分鐘")
                                    except Exception:
                                        pass
                                    # 自動暫停本機台
                                    self._auto_pause = True
                                    logging.info(f"[{self.cfg.rtmp}]已暫停spin")
                                    
                                    # 開始錄影
                                    ts = time.strftime("%Y%m%d_%H%M%S")
                                    self._start_recording(self.cfg.rtmp, self.cfg.rtmp_url, duration_sec=120, ts=ts)
                                    
                                    # 等待錄影程序啟動
                                    t0 = time.time()   
                                    while time.time() - t0 < 3.0:
                                        if self._is_recording_active():
                                            break
                                        time.sleep(0.1)
                                    # 恢復本機台 SPIN
                                    self._auto_pause = False
                                    logging.info(f"[{self.cfg.rtmp}]已重新啟動spin")
                    else:  # 正常頻率使用標準檢測
                        if not self.cfg.enable_template_detection:
                            logging.info(f"正常頻率({current_freq}s) - 模板偵測已關閉，跳過 RTMP 檢測")
                        else:
                            self._rtmp_once_check(self.cfg.rtmp, self.cfg.rtmp_url, threshold=0.80)

                # 6) 動態 sleep：使用全域頻率設定，加上小幅隨機抖動避免同步問題
                with spin_frequency_lock:
                    base_sleep = spin_frequency
                
                # 根據頻率調整隨機抖動範圍
                if base_sleep <= 0.1:  # 極限頻率使用最小抖動
                    random_factor = 0.95 + np.random.random() * 0.1  # 0.95 到 1.05 (±5%)
                elif base_sleep <= 0.2:  # 超快頻率使用較小抖動
                    random_factor = 0.9 + np.random.random() * 0.2  # 0.9 到 1.1 (±10%)
                else:  # 其他頻率使用標準抖動
                    random_factor = 0.8 + np.random.random() * 0.4  # 0.8 到 1.2 (±20%)
                
                actual_sleep = base_sleep * random_factor
                
                # 計算並顯示實際循環時間
                loop_elapsed = time.time() - loop_start_time
                logging.info(f"循環耗時: {loop_elapsed:.3f}s | 設定頻率: {base_sleep:.3f}s | 實際等待: {actual_sleep:.3f}s")
                
                time.sleep(actual_sleep)

            except KeyboardInterrupt:
                # 手動中斷：向上拋出，由 run() 處理
                raise
            except Exception as e:
                # 任意例外：記錄並嘗試拍一次 RTMP 便於診斷
                logging.error(f"spin_forever 例外: {e}\n{traceback.format_exc()}")
                try:
                    if self.cfg.rtmp and self.cfg.rtmp_url:
                        self._rtmp_once_check(self.cfg.rtmp + "_Exception", self.cfg.rtmp_url, threshold=0.80)
                except Exception as rtmp_err:
                    logging.debug(f"例外時 RTMP 截圖失敗: {rtmp_err}")
                time.sleep(1.0)  # 避免例外循環過快

        while (pause_event.is_set() or self._auto_pause) and not stop_event.is_set():
            logging.info("[Loop] 已暫停（%s）", "Global" if pause_event.is_set() else "Auto")
            time.sleep(0.2)

    # ----------------- 對外啟動 -----------------
    def run(self):
        """
        建立瀏覽器、必要時先嘗試從 Lobby 進入遊戲，接著進入 spin_forever 迴圈
        
        流程：
        1. 建立 Edge WebDriver 並載入遊戲 URL
        2. 若提供 game_title_code，從大廳進入指定遊戲
        3. 進入 spin_forever 無限循環（直到收到停止訊號）
        
        異常處理：
        - KeyboardInterrupt：優雅退出，關閉瀏覽器
        - 其他例外：記錄錯誤並關閉瀏覽器
        """
        # 安全日誌輸出（不洩露 URL 和 token）
        safe_info = f"rtmp={self.cfg.rtmp or 'N/A'}, game={self.cfg.game_title_code or 'N/A'}, template_type={self.template_type or 'N/A'}"
        logging.info(f"初始化遊戲測試: {safe_info}")
        try:
            self.driver = self._build_driver()
        except Exception as e:
            logging.error(f"建立瀏覽器失敗: {e}")
            raise
        try:
            # 若提供 game_title_code，開啟後先嘗試從 Lobby 進入
            if self.cfg.game_title_code:
                self.scroll_and_click_game(self.cfg.game_title_code)
            self.spin_forever()
        except KeyboardInterrupt:
            logging.info("手動中止")
        finally:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass


# =========================== 主程式與訊號處理 ===========================
def handle_interrupt(sig, frame):
    """Ctrl+C 時將 stop_event 設為 True，讓各執行緒優雅退出"""
    print("\n🛑 收到 Ctrl+C，中止中…")
    stop_event.set()

signal.signal(signal.SIGINT, handle_interrupt)


def main():
    """
    入口函式：
    - 讀取 game_config.json -> 過濾 enabled 機台 -> 轉成 GameConfig
    - 讀取 actions.json（keyword_actions / machine_actions）
    - 建立共享元件：TemplateMatcher / FFmpegRunner / LarkClient
    - 針對每一台機台啟動一個執行緒跑 GameRunner.run()
    """
    start_hotkey_listener()
    logging.info("[Main] 啟動主程式，開始讀取設定檔")
    # 讀取遊戲清單
    try:
        with (BASE_DIR / "game_config.json").open("r", encoding="utf-8") as f:
            cfg_list = json.load(f)
        logging.info(f"[Main] 讀取 game_config.json 成功，筆數={len(cfg_list)}")
    except Exception as e:
        logging.error(f"[Main] 讀取 game_config.json 失敗: {e}")
        raise

    games: List[GameConfig] = []
    for raw in cfg_list:
        if raw.get("enabled", True):
            games.append(
                GameConfig(
                    url=raw.get("url"),
                    rtmp=raw.get("rtmp"),
                    rtmp_url=raw.get("rtmp_url"),
                    game_title_code=raw.get("game_title_code"),
                    template_type=raw.get("template_type"),  # ✅ 支援直接指定
                    error_template_type=raw.get("error_template_type"),  # ✅ 針對特定機器的錯誤畫面模板類型
                    enabled=True,
                    enable_recording=raw.get("enable_recording", True),  # ✅ 支援錄製功能開關
                    enable_template_detection=raw.get("enable_template_detection", True),  # ✅ 支援模板偵測開關
                )
            )

    # 讀取動作定義
    with (BASE_DIR / "actions.json").open("r", encoding="utf-8") as f:
        actions = json.load(f)
    keyword_actions: Dict[str, List[str]] = actions.get("keyword_actions", {})
    # 將 {"kw": {"positions":[...], "click_take":true}} 轉成 {"kw": ([...], True)}
    machine_actions: Dict[str, Tuple[List[str], bool]] = {
        kw: (info.get("positions", []), bool(info.get("click_take", False)))
        for kw, info in actions.get("machine_actions", {}).items()
    }

    # 共用元件（✅ 帶入 manifest）
    matcher = TemplateMatcher(TEMPLATE_DIR, manifest_path=TEMPLATES_MANIFEST)
    ff = FFmpegRunner(FFMPEG_EXE)
    lark = LarkClient(LARK_WEBHOOK)

    # 每台機台一個執行緒
    threads: List[threading.Thread] = []
    recording_enabled_count = sum(1 for conf in games if conf.enable_recording)
    logging.info(f"[Main] 準備啟動 {len(games)} 個執行緒，其中 {recording_enabled_count} 個啟用錄製功能")
    
    for idx, conf in enumerate(games):
        runner = GameRunner(conf, matcher, ff, lark, keyword_actions, machine_actions)
        recording_status = "啟用錄製" if conf.enable_recording else "停用錄製"
        logging.info(f"[Main] 啟動執行緒 {idx+1}/{len(games)}: {conf.rtmp or conf.game_title_code or 'NA'} ({recording_status})")
        
        t = threading.Thread(
            target=runner.run,
            name=f"GameThread-{conf.rtmp or conf.game_title_code or 'NA'}",
            daemon=True,  # 設為守護緒，主程式結束時可隨之關閉
        )
        t.start()
        threads.append(t)
        # 錯開啟動時間，避免同時連接 RTMP 造成資源競爭（每個間隔 1-2 秒）
        if idx < len(games) - 1:
            delay = 1.0 + np.random.random()
            logging.info(f"[Main] 等待 {delay:.2f} 秒後啟動下一個執行緒")
            time.sleep(delay)

    # 等待所有執行緒完成（一般情況下會長時運行）
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
