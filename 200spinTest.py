from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
import time
import os
import sys
import csv
import signal
import logging
import json
import random

# =========================
# 基本設定
# =========================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SPIN_MIN = 10
SPIN_MAX = 25                # 每個遊戲 SPIN 次數上限（達到就強制退出）
WINDOW_SIZE = "350,750"

keyword_actions = {}
machine_actions = {}

# =========================
# 共用工具
# =========================
def resource_path(rel_path: str) -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel_path)

def looks_like_url(s: str) -> bool:
    return isinstance(s, str) and s.strip().lower().startswith(("http://", "https://"))

def js_click(driver, elem):
    driver.execute_script("arguments[0].click();", elem)

def launch_driver(url: str):
    edge_options = webdriver.EdgeOptions()
    edge_options.add_argument(
        "--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.127 Mobile Safari/537.36"
    )
    edge_options.add_argument(f"--window-size={WINDOW_SIZE}")
    edge_options.add_argument("--incognito")

    # 優先使用本地 msedgedriver.exe，若失敗則使用 webdriver-manager 自動下載
    driver_path = resource_path("msedgedriver.exe")
    if not os.path.exists(driver_path):
        raise FileNotFoundError(f"找不到驅動程式：{driver_path}")

    service = Service(executable_path=driver_path)
    driver = webdriver.Edge(service=service, options=edge_options)
    driver.get(url)
    return driver

# =========================
# 讀取 accounts.csv
# =========================
def load_accounts(csv_path: str):
    """
    讀取三欄：account, game_title_code, url（允許有表頭）
    若無表頭，預設 A=account, C=game_title_code，且在整列中找第一個像 URL 的欄位。
    """
    rows_out = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return rows_out

    header = [h.strip().lower() for h in rows[0]]
    col_account = col_game = col_url = None
    for idx, name in enumerate(header):
        if col_account is None and "account" in name:
            col_account = idx
        if col_game is None and "game_title_code" in name.replace(" ", ""):
            col_game = idx
        if col_url is None and "url" in name:
            col_url = idx

    start_idx = 1 if (col_account is not None or col_game is not None or col_url is not None) else 0

    for i in range(start_idx, len(rows)):
        r = [c.strip() for c in rows[i]]
        if not r or all(not c for c in r):
            continue

        account = r[col_account] if col_account is not None and col_account < len(r) else (r[0] if len(r) >= 1 else f"row_{i}")
        game_title_code = r[col_game] if col_game is not None and col_game < len(r) else (r[2] if len(r) >= 3 else "")
        if col_url is not None and col_url < len(r) and looks_like_url(r[col_url]):
            url = r[col_url]
        else:
            url = next((c for c in r if looks_like_url(c)), None)

        if not url:
            continue

        rows_out.append({"account": account, "game_title_code": game_title_code, "url": url})

    return rows_out

# =========================
# actions.json 支援
# =========================
def click_multiple_positions(driver, positions, click_take=False):
    for label in positions or []:
        try:
            xpath = f"//span[normalize-space(text())='{label}']"
            elems = WebDriverWait(driver, 2).until(
                EC.presence_of_all_elements_located((By.XPATH, xpath))
            )
            js_click(driver, elems[0])
            logging.info(f"✅ 已點擊座標: {label}")
            time.sleep(0.2)
        except Exception as e:
            logging.warning(f"❌ 找不到座標 '{label}': {e}")

    if click_take:
        try:
            take_btn = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".my-button.btn_take"))
            )
            js_click(driver, take_btn)
            logging.info("✅ 已點擊 Take 按鈕")
        except TimeoutException:
            pass

# =========================
# 大廳找遊戲 → Join（依 game_title_code）
# =========================
def scroll_and_click_game(driver, game_title_code: str) -> bool:
    """
    在大廳依 game_title_code 找卡片 -> 點卡片 -> 找 Join -> 點 Join
    並在 Join 後執行 keyword_actions（若匹配）
    """
    # 先檢查是否已經在遊戲內（能找到 SPIN 按鈕）
    btn, _ = find_spin_button(driver)
    if btn:
        logging.info("✅ 已在遊戲內，跳過大廳找卡片流程")
        return True
    
    try:
        items = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.ID, "grid_gm_item"))
        )

        target = None
        for item in items:
            try:
                title = item.get_attribute("title") or ""
                if game_title_code and game_title_code in title:
                    target = item
                    break
            except Exception:
                continue

        if not target:
            logging.warning(f"❌ 無法在大廳中找到遊戲: {game_title_code}")
            return False

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", target)
        time.sleep(0.2)
        js_click(driver, target)
        logging.info(f"✅ 成功點擊遊戲卡片: {game_title_code}")
        time.sleep(1.0)

        # 全頁找 Join（新 DOM 不一定掛在卡片下面）
        try:
            join_btns = WebDriverWait(driver, 6).until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, "//div[contains(@class, 'gm-info-box')]//span[normalize-space(text())='Join']")
                )
            )
            for join in join_btns:
                if join.is_displayed():
                    js_click(driver, join)
                    logging.info("🎮 成功點擊 Join 進入遊戲")
                    time.sleep(1.0)

                    # Join 後執行 keyword_actions（比對 game_title_code）
                    if game_title_code and keyword_actions:
                        for kw, positions in keyword_actions.items():
                            if kw and kw in game_title_code:
                                logging.info(f"🔹 Join 後特殊流程: {kw} -> {positions}")
                                click_multiple_positions(driver, positions)
                                time.sleep(0.5)
                    return True

            logging.warning("⚠️ 找到 gm-info-box，但沒有可見的 Join 按鈕")
            return False

        except TimeoutException:
            logging.warning("⚠️ 找不到 Join 按鈕")
            return False

    except Exception as e:
        logging.error(f"❌ 執行滑動並點擊遊戲時失敗: {e}", exc_info=True)
        return False

# =========================
# SPIN 與退出
# =========================
def find_spin_button(driver):
    """兼容兩種常見 SPIN 選擇器"""
    selectors = [".my-button.btn_spin", ".btn_spin .my-button"]
    for sel in selectors:
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
            )
            return btn, sel
        except TimeoutException:
            continue
    return None, None

def force_exit(driver) -> bool:
    """強制離開機器（不判斷餘額）"""
    try:
        try:
            quit_btn = driver.find_element(By.CSS_SELECTOR, ".my-button.btn_cashout")
            js_click(driver, quit_btn)
            time.sleep(0.5)
        except NoSuchElementException:
            pass

        try:
            exit_btn = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".function-btn .reserve-btn-gray"))
            )
            js_click(driver, exit_btn)
            logging.info("🚪 Exit To Lobby")
            time.sleep(0.5)
        except TimeoutException:
            pass

        try:
            confirm_btn = WebDriverWait(driver, 2).until(
                EC.element_to_be_clickable((By.XPATH, "//button[.//div[normalize-space(text())='Confirm']]"))
            )
            js_click(driver, confirm_btn)
            logging.info("✅ Confirm 離開")
            time.sleep(5)
        except TimeoutException:
            pass
        
        # ⏳ 關鍵：等待大廳容器元素（container）出現，確認真的回到大廳
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "grid_gm_item"))
            )
            logging.info("🏠 已回到大廳容器畫面")
            time.sleep(3)
            return True
        
        except TimeoutException:
            logging.warning("⚠️ 沒有偵測到大廳容器，可能仍在遊戲頁")
            # 可以視需求決定要不要再 retry 一次退出
            return False

    except Exception as e:
        logging.warning(f"離開流程錯誤: {e}")

def spin_n_times_then_exit(driver, game_title_code: str, n: int = None):
    """跑一輪 SPIN（可隨機次數），嘗試退出；若未回到大廳，照你需求繼續再跑一輪 SPIN，直到成功偵測到大廳。"""
    # 先確保有進到該遊戲（容錯）
    try:
        scroll_and_click_game(driver, game_title_code)
    except Exception:
        pass

    round_idx = 0
    while True:
        round_idx += 1
        # 若 n 未指定 -> 本輪隨機
        spins_target = n if n is not None else random.randint(SPIN_MIN, SPIN_MAX)
        logging.info(f"🎲 第 {round_idx} 輪：本輪 SPIN 次數 = {spins_target}")

        spins = 0
        while spins < spins_target:
            btn, sel = find_spin_button(driver)
            if not btn:
                # 可能還在大廳或 UI 尚未渲染；再嘗試一次進場
                scroll_and_click_game(driver, game_title_code)
                time.sleep(0.8)
                continue

            try:
                js_click(driver, btn)
                spins += 1
                logging.info(f"✅ SPIN {spins}/{spins_target} ({sel})")

                # SPIN 後執行 machine_actions（以 game_title_code 匹配）
                for kw, spec in (machine_actions or {}).items():
                    if isinstance(spec, dict):
                        positions = spec.get("positions", [])
                        do_take = bool(spec.get("click_take", False))
                    else:
                        positions, do_take = spec
                    if kw and kw in (game_title_code or ""):
                        logging.info(f"🔹 SPIN 後特殊流程: {kw} -> {positions}, take={do_take}")
                        click_multiple_positions(driver, positions, click_take=do_take)

            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                js_click(driver, btn)
                spins += 1
            except Exception as e:
                logging.warning(f"點擊 SPIN 失敗：{e}")

            time.sleep(1.5)

        # 一輪 SPIN 結束 → 嘗試退出
        logging.info("🛑 本輪 SPIN 完成，嘗試退出至大廳…")
        success = force_exit(driver)
        if success:
            # ✅ 成功回到大廳，結束 while True
            logging.info("✔️ 確認已回到大廳，結束 SPIN 任務")
            break
        else:
            # ❌ 未回到大廳，依需求再跑一輪
            logging.warning("↻ 未回到大廳，準備再執行一輪 SPIN")
            time.sleep(1.0)

# =========================
# 單一 URL 任務（逐一執行）
# =========================
def run_one(account: str, game_title_code: str, url: str):
    logging.info(f"➡️ [{account}]({game_title_code}) 啟動：{url}")
    driver = launch_driver(url)
    try:
        # 進入指定遊戲並跑固定次數 SPIN
        scroll_and_click_game(driver, game_title_code)
        spin_n_times_then_exit(driver, game_title_code=game_title_code)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    logging.info(f"✔️ [{account}]({game_title_code}) 完成並關閉")

# =========================
# 主程式
# =========================
def main():
    # Ctrl+C：當前 URL 完成後停止
    interrupted = {"flag": False}
    def handle_interrupt(sig, frame):
        interrupted["flag"] = True
        logging.info("⚠️ 收到中斷，當前 URL 完成後停止")
    signal.signal(signal.SIGINT, handle_interrupt)

    # 讀 actions.json（可選）
    if os.path.exists("actions.json"):
        with open("actions.json", "r", encoding="utf-8") as f:
            actions = json.load(f)
        global keyword_actions, machine_actions
        keyword_actions = actions.get("keyword_actions", {}) or {}
        raw_ma = actions.get("machine_actions", {}) or {}
        # 支援兩種結構：dict/tuple
        machine_actions = {
            kw: (info.get("positions", []), bool(info.get("click_take", False)))
            if isinstance(info, dict) else info
            for kw, info in raw_ma.items()
        }
        logging.info("已載入 actions.json")

    # 讀 accounts.csv
    csv_path = "accounts.csv"
    if not os.path.exists(csv_path):
        logging.error("找不到 accounts.csv，請確認檔案位置")
        return

    tasks = load_accounts(csv_path)
    if not tasks:
        logging.error("accounts.csv 讀不到任何有效資料")
        return

    # 逐一執行（不要同時全部跑）
    for row in tasks:
        if interrupted["flag"]:
            break
        run_one(row["account"], row["game_title_code"], row["url"])

    logging.info("全部任務完成")

if __name__ == "__main__":
    main()
