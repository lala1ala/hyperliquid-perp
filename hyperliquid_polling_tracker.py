import os
import sys
import json
import requests
from datetime import datetime

# 强制 stdout 使用 UTF-8 编码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 尝试载入环境变量
def load_env_fallback():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" in line:
                        key, val = line.split("=", 1)
                        os.environ[key.strip()] = val.strip()

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    load_env_fallback()

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "hl_tracker_config.json")
DB_FILE = os.path.join(os.path.dirname(__file__), "seen_trades.json")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: Config file {CONFIG_FILE} not found!")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def load_seen_trades():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"Warning: Failed to load seen_trades.json: {e}")
            return set()
    return None  # 返回 None 代表是第一次运行，用于做初始化免打扰

def save_seen_trades(seen_set):
    # 限制保存的交易ID历史数量，防止文件过大（保留最新的 3000 个）
    seen_list = list(seen_set)[-3000:]
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(seen_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving seen trades: {e}")

def fetch_user_fills(address):
    url = "https://api.hyperliquid.xyz/info"
    payload = {
        "type": "userFills",
        "user": address
    }
    headers = {
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"[{address}] Fetch failed with status {resp.status_code}: {resp.text}")
            return []
    except Exception as e:
        print(f"[{address}] Exception when fetching fills: {e}")
        return []

def send_tg_notification(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set!")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=data, timeout=15)
        if resp.status_code == 200:
            return True
        else:
            print(f"Telegram send failed: {resp.text}")
            return False
    except Exception as e:
        print(f"Telegram send exception: {e}")
        return False

def format_fill_message(wallet_label, address, fills):
    lines = []
    short_addr = f"{address[:6]}...{address[-4:]}"
    lines.append(f"👤 <b>{wallet_label}</b> (<code>{short_addr}</code>)")
    
    # 按照时间从早到晚排序交易
    sorted_fills = sorted(fills, key=lambda x: x.get("time", 0))
    
    for fill in sorted_fills:
        coin = fill.get("coin", "未知代币")
        px = float(fill.get("px", 0))
        sz = float(fill.get("sz", 0))
        value_usd = px * sz
        
        # 交易方向与 Emoji
        side = fill.get("side", "")  # B=Buy, A=Sell
        direction = fill.get("dir", "") # Open Long, Close Short etc.
        
        # 如果 dir 字段不存在，做基础 fallback
        if not direction:
            direction = "买入 (Buy)" if side == "B" else "卖出 (Sell)"
            
        emoji = "🟢" if "B" in side or "Buy" in direction or "Long" in direction else "🔴"
        
        # 盈亏计算
        pnl_str = ""
        closed_pnl = fill.get("closedPnl", "0")
        try:
            pnl_val = float(closed_pnl)
            if pnl_val != 0:
                pnl_emoji = "🟢" if pnl_val > 0 else "🔴"
                pnl_str = f" (盈亏: {pnl_emoji}<code>${pnl_val:+.2f}</code>)"
        except ValueError:
            pass
            
        # 格式化时间
        fill_time_ms = fill.get("time", 0)
        time_str = datetime.fromtimestamp(fill_time_ms / 1000.0).strftime("%H:%M:%S")
        
        # 格式化价格，对于微型代币保留更多小数位
        if px < 0.001:
            px_str = f"${px:.8f}"
        elif px < 1:
            px_str = f"${px:.4f}"
        else:
            px_str = f"${px:,.2f}"
            
        lines.append(
            f"  • {emoji} <b>{direction}</b> | <b>{coin}</b>{pnl_str}\n"
            f"    均价: {px_str} | 数量: {sz:g} (${value_usd:,.2f} USD)\n"
            f"    时间: {time_str}"
        )
    return "\n".join(lines)

def main():
    config = load_config()
    addresses = config.get("monitored_addresses", [])
    if not addresses:
        print("No addresses configured in hl_tracker_config.json")
        return
        
    seen_set = load_seen_trades()
    first_run = (seen_set is None)
    
    if first_run:
        print("首次运行检测：正在初始化已读交易库，本次不会发送具体交易提醒以防打扰。")
        seen_set = set()
        
    all_new_fills_by_wallet = {}
    total_new_count = 0
    
    for wallet in addresses:
        addr = wallet.get("address", "").lower().strip()
        label = wallet.get("label", "未命名")
        if not addr:
            continue
            
        print(f"正在获取 [{label}] ({addr}) 的成交历史...")
        fills = fetch_user_fills(addr)
        
        new_fills = []
        for fill in fills:
            tid = fill.get("tid")
            if not tid:
                continue
            tid_str = str(tid)
            
            # 如果是首次运行，直接归入已读，不视为新交易
            if first_run:
                seen_set.add(tid_str)
                continue
                
            if tid_str not in seen_set:
                new_fills.append(fill)
                seen_set.add(tid_str)
                total_new_count += 1
                
        if new_fills:
            all_new_fills_by_wallet[addr] = {
                "label": label,
                "fills": new_fills
            }
            
    # 如果是首次运行，保存初始状态并发送启动成功通知
    if first_run:
        save_seen_trades(seen_set)
        startup_msg = (
            "🤖 <b>Hyperliquid 监控机器人初始化成功！</b>\n\n"
            "系统已将当前历史交易归档，从现在起将实时监听最新仓位变动。\n"
            f"📊 <b>监控钱包数</b>: {len(addresses)} 个"
        )
        send_tg_notification(startup_msg)
        print("初始化完成通知已发送至 Telegram。")
        return

    # 如果有新交易，生成汇总通知
    if total_new_count > 0:
        print(f"发现 {total_new_count} 笔新交易！正在发送通知...")
        msg_blocks = ["🔔 <b>Hyperliquid 交易汇总提醒</b> (近30分钟)\n"]
        
        for addr, data in all_new_fills_by_wallet.items():
            block_text = format_fill_message(data["label"], addr, data["fills"])
            msg_blocks.append(block_text)
            
        full_msg = "\n\n".join(msg_blocks)
        
        # 发送 Telegram 消息
        success = send_tg_notification(full_msg)
        if success:
            save_seen_trades(seen_set)
            print("交易提醒推送成功。")
        else:
            print("交易提醒推送失败，未更新已读交易库。")
    else:
        print("未发现新交易。")

if __name__ == "__main__":
    main()
