import os
import sys
import requests
from datetime import datetime

# 强制 stdout 使用 UTF-8 编码避免 Windows 控制台 GBK 崩溃
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def load_env_fallback():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    load_env_fallback()

def send_test_message():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not found in environment!")
        return False
        
    print(f"Connecting to Telegram Bot (token ending in {token[-8:]}) for chat: {chat_id}")
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    html_text = (
        "🤖 <b>Hyperliquid 监控机器人连接测试</b>\n\n"
        f"⏰ <b>测试时间</b>: {now}\n"
        "📈 <b>通知状态</b>: 准备中\n\n"
        "测试消息发送。"
    )
    
    data = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        resp = requests.post(url, json=data, timeout=15)
        print(f"HTTP Status: {resp.status_code}")
        print(f"Response Body: {resp.text}")
        if resp.status_code == 200:
            print("Message sent successfully!")
            return True
        else:
            print("Failed to send message.")
            return False
    except Exception as e:
        print(f"Exception occurred: {e}")
        return False

if __name__ == "__main__":
    send_test_message()
