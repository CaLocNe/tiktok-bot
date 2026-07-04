import os
import time
import random
import logging
import asyncio
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Selenium imports
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
import undetected_chromedriver as uc
from selenium_stealth import stealth

# Hỗ trợ đọc file .env (nếu có)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ==================== CẤU HÌNH ====================
TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("8684641966:AAHErNpEEVFy3Q-FK5NfkoKNbcChJXTBLY8")
if not TOKEN:
    raise ValueError("Missing BOT_TOKEN or TELEGRAM_TOKEN environment variable")

ACCOUNTS_FILE = "accounts.txt"
LOGIN_URL = "https://www.tiktok.com/login/phone-or-email/email"

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== QUẢN LÝ TÀI KHOẢN ====================
def load_accounts():
    """Đọc danh sách tài khoản từ file"""
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE, "r") as f:
        lines = [line.strip() for line in f if line.strip() and ":" in line]
    accounts = []
    for line in lines:
        try:
            user, pwd = line.split(":", 1)
            accounts.append({"username": user.strip(), "password": pwd.strip()})
        except ValueError:
            continue
    return accounts

def save_accounts(accounts):
    """Ghi danh sách tài khoản vào file"""
    with open(ACCOUNTS_FILE, "w") as f:
        for acc in accounts:
            f.write(f"{acc['username']}:{acc['password']}\n")

def add_account(username, password):
    accounts = load_accounts()
    if any(acc["username"] == username for acc in accounts):
        return False, "Tài khoản đã tồn tại!"
    accounts.append({"username": username, "password": password})
    save_accounts(accounts)
    return True, "Thêm thành công!"

def remove_account(username):
    accounts = load_accounts()
    new_accounts = [acc for acc in accounts if acc["username"] != username]
    if len(new_accounts) == len(accounts):
        return False, "Không tìm thấy tài khoản!"
    save_accounts(new_accounts)
    return True, "Xóa thành công!"

def get_accounts_list():
    return [acc["username"] for acc in load_accounts()]

# ==================== SELENIUM DRIVER ====================
def init_driver():
    """Khởi tạo undetected Chrome driver với stealth (hỗ trợ Render)"""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # Nếu Render set biến CHROME_BIN, dùng binary đó
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    try:
        driver = uc.Chrome(options=options)
        stealth(
            driver,
            languages=["en-US", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
        )
        return driver
    except Exception as e:
        logger.error(f"Khởi tạo driver thất bại: {e}")
        raise

def tiktok_login(driver, username, password):
    """Đăng nhập TikTok, xử lý nhiều tình huống lỗi"""
    try:
        driver.get(LOGIN_URL)
        # Chờ form login xuất hiện
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[placeholder*="Email"]'))
        )
        email_input = driver.find_element(By.CSS_SELECTOR, 'input[placeholder*="Email"]')
        email_input.clear()
        email_input.send_keys(username)

        pwd_input = driver.find_element(By.CSS_SELECTOR, 'input[placeholder*="Password"]')
        pwd_input.clear()
        pwd_input.send_keys(password)

        login_btn = driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        login_btn.click()
        time.sleep(5)

        # Kiểm tra captcha
        if "captcha" in driver.page_source.lower() or "verify" in driver.page_source.lower():
            return False, "Yêu cầu xác thực (captcha) – cần đăng nhập thủ công"

        # Kiểm tra đăng nhập thành công: có avatar hoặc chuyển hướng về trang chủ
        try:
            # Thử tìm avatar
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-e2e="user-avatar"]'))
            )
            return True, "Đăng nhập thành công"
        except TimeoutException:
            # Nếu không có avatar, kiểm tra URL có phải trang chủ không
            if "tiktok.com/" in driver.current_url and "login" not in driver.current_url:
                return True, "Đăng nhập thành công (không có avatar)"
            else:
                # Có thể sai mật khẩu, tìm thông báo lỗi
                try:
                    error = driver.find_element(By.CSS_SELECTOR, '[role="alert"]').text
                    return False, f"Sai thông tin: {error}"
                except:
                    return False, "Đăng nhập thất bại, không rõ nguyên nhân"
    except Exception as e:
        logger.error(f"Login error: {e}")
        return False, str(e)

def follow_target(driver, target_username):
    """Follow một tài khoản, xử lý nhiều kiểu nút Follow"""
    try:
        driver.get(f"https://www.tiktok.com/@{target_username}")
        # Chờ profile load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'h1[data-e2e="user-title"]'))
        )

        # Tìm nút Follow – ưu tiên data-e2e, sau đó class, sau đó text
        follow_btn = None
        # Cách 1: dùng data-e2e
        try:
            follow_btn = driver.find_element(By.CSS_SELECTOR, '[data-e2e="follow-button"]')
        except:
            pass

        # Cách 2: tìm button có class chứa "follow"
        if not follow_btn:
            buttons = driver.find_elements(By.XPATH, '//button[contains(@class, "follow")]')
            for btn in buttons:
                if "Follow" in btn.text or "Following" in btn.text:
                    follow_btn = btn
                    break

        # Cách 3: tìm button có text chính xác "Follow"
        if not follow_btn:
            try:
                follow_btn = driver.find_element(By.XPATH, '//button[text()="Follow"]')
            except:
                pass

        if not follow_btn:
            return False, "Không tìm thấy nút Follow"

        if "Following" in follow_btn.text:
            return False, "Đã follow từ trước"

        follow_btn.click()
        time.sleep(random.uniform(2, 4))
        return True, "Follow thành công"
    except TimeoutException:
        return False, "Không tìm thấy profile (tài khoản không tồn tại hoặc bị chặn)"
    except Exception as e:
        logger.error(f"Follow error: {e}")
        return False, str(e)

def follow_with_account(account, target):
    """Dùng một account để follow"""
    driver = None
    try:
        driver = init_driver()
        ok, msg = tiktok_login(driver, account["username"], account["password"])
        if not ok:
            return f"❌ {account['username']}: {msg}"
        ok, msg = follow_target(driver, target)
        return f"{'✅' if ok else '❌'} {account['username']}: {msg}"
    except Exception as e:
        return f"⚠️ {account['username']}: Lỗi - {str(e)}"
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

async def follow_all_accounts(target: str):
    """Dùng tất cả account để follow (bất đồng bộ)"""
    accounts = load_accounts()
    if not accounts:
        return ["⚠️ Chưa có tài khoản nào trong danh sách!"]
    results = []
    for acc in accounts:
        result = await asyncio.to_thread(follow_with_account, acc, target)
        results.append(result)
        # Nghỉ ngẫu nhiên để tránh bị phát hiện
        await asyncio.sleep(random.uniform(8, 20))
    return results

# ==================== BOT TELEGRAM ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **TikTok Follow Bot**\n\n"
        "📌 **Quản lý tài khoản:**\n"
        "/add_account <user> <pass> – Thêm tài khoản clone\n"
        "/remove_account <user> – Xóa tài khoản\n"
        "/list_accounts – Danh sách tài khoản\n"
        "/clear_accounts – Xóa toàn bộ\n\n"
        "🎯 **Lệnh follow:**\n"
        "/follow <username> – Follow mục tiêu bằng tất cả tài khoản\n\n"
        "📊 **Trạng thái:**\n"
        "/status – Kiểm tra bot"
    )

async def add_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ Cách dùng: /add_account <username> <password>")
        return
    username = context.args[0]
    password = " ".join(context.args[1:])
    ok, msg = add_account(username, password)
    await update.message.reply_text(f"{'✅' if ok else '❌'} {msg}")

async def remove_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Cách dùng: /remove_account <username>")
        return
    username = context.args[0]
    ok, msg = remove_account(username)
    await update.message.reply_text(f"{'✅' if ok else '❌'} {msg}")

async def list_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accounts = get_accounts_list()
    if not accounts:
        await update.message.reply_text("📭 Danh sách trống.")
        return
    text = "📋 **Danh sách tài khoản:**\n" + "\n".join(f"- {acc}" for acc in accounts)
    await update.message.reply_text(text)

async def clear_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_accounts([])
    await update.message.reply_text("🗑️ Đã xóa toàn bộ tài khoản.")

async def follow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Cách dùng: /follow <username_tiktok>")
        return
    target = context.args[0]
    await update.message.reply_text(f"⏳ Đang thực hiện follow @{target} bằng tất cả tài khoản...")
    results = await follow_all_accounts(target)
    reply = "📊 **Kết quả follow:**\n" + "\n".join(results)
    await update.message.reply_text(reply)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = len(load_accounts())
    await update.message.reply_text(f"🟢 Bot đang hoạt động\n📂 Số tài khoản: {count}")

# ==================== FLASK KEEP-ALIVE ====================
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "TikTok Follow Bot is running!"

@flask_app.route("/health")
def health():
    return "OK"

# ==================== MAIN ====================
def run_telegram_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_account", add_account))
    app.add_handler(CommandHandler("remove_account", remove_account))
    app.add_handler(CommandHandler("list_accounts", list_accounts))
    app.add_handler(CommandHandler("clear_accounts", clear_accounts))
    app.add_handler(CommandHandler("follow", follow))
    app.add_handler(CommandHandler("status", status))

    logger.info("🚀 Telegram bot đã khởi động...")
    app.run_polling()

if __name__ == "__main__":
    # Chạy Flask trong thread riêng (để phục vụ health check)
    flask_thread = threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000))), daemon=True)
    flask_thread.start()

    # Chạy bot polling ở thread chính (đảm bảo luôn chạy)
    run_telegram_bot()
