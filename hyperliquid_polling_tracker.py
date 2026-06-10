import os
import sys
import json
import requests
from datetime import datetime

# 强制 stdout 使用 UTF-8 编码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 载入环境变量
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
        return {"monitored_addresses": []}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return {"monitored_addresses": []}

def save_config(config_data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        print("Config file saved successfully.")
    except Exception as e:
        print(f"Error saving config: {e}")

def load_seen_trades_and_offset():
    seen_set = set()
    last_update_id = 0
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    seen_set = set(data.get("seen_tids", []))
                    last_update_id = data.get("last_update_id", 0)
                elif isinstance(data, list):
                    seen_set = set(data)
        except Exception as e:
            print(f"Warning: Failed to load seen_trades.json: {e}")
            
    if not os.path.exists(DB_FILE):
        return None, 0
    return seen_set, last_update_id

def save_seen_trades_and_offset(seen_set, last_update_id):
    seen_list = sorted(list(seen_set))
    db_data = {
        "seen_tids": seen_list,
        "last_update_id": last_update_id
    }
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving seen trades and offset: {e}")

def html_to_discord_markdown(html_text):
    """
    将 Telegram 的 HTML 格式标签转换为 Discord 支持的 Markdown 格式
    """
    md = html_text
    md = md.replace("<b>", "**").replace("</b>", "**")
    md = md.replace("<i>", "*").replace("</i>", "*")
    md = md.replace("<code>", "`").replace("</code>", "`")
    return md

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
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram send exception: {e}")
        return False

def send_discord_notification(text):
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        # 如果没有配置 Discord Webhook，则静默跳过
        return True
        
    discord_text = html_to_discord_markdown(text)
    payload = {
        "content": discord_text
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.status_code in [200, 204]:
            return True
        else:
            print(f"Discord send failed with status {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"Discord send exception: {e}")
        return False

def process_telegram_commands(last_update_id):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id_str = str(os.getenv("TELEGRAM_CHAT_ID", ""))
    
    if not token or not chat_id_str:
        return last_update_id
        
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 5}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return last_update_id
            
        updates = resp.json().get("result", [])
        if not updates:
            return last_update_id
            
        config_data = load_config()
        monitored = config_data.get("monitored_addresses", [])
        config_changed = False
        new_last_id = last_update_id
        
        for update in updates:
            new_last_id = max(new_last_id, update.get("update_id", 0))
            message = update.get("message")
            if not message:
                continue
                
            sender_chat = message.get("chat", {})
            sender_id = str(sender_chat.get("id", ""))
            
            if sender_id != chat_id_str:
                continue
                
            text = message.get("text", "").strip()
            if not text:
                continue
                
            if text.startswith("/add"):
                parts = text.split(maxsplit=2)
                if len(parts) < 2:
                    msg = "⚠️ <b>格式错误</b>\n正确格式: <code>/add 钱包地址 备注标签</code>"
                    send_tg_notification(msg)
                    send_discord_notification(msg)
                    continue
                
                addr = parts[1].lower().strip()
                label = parts[2].strip() if len(parts) == 3 else "未命名"
                
                if not addr.startswith("0x") or len(addr) != 42:
                    msg = f"⚠️ <b>格式错误</b>\n地址 <code>{addr}</code> 似乎不是合法的 EVM/Hyperliquid 地址。"
                    send_tg_notification(msg)
                    send_discord_notification(msg)
                    continue
                
                existing = next((item for item in monitored if item.get("address").lower() == addr), None)
                if existing:
                    existing["label"] = label
                    msg = f"✅ <b>修改成功</b>\n地址已存在，已更新标签为：<b>{label}</b>\n<code>{addr}</code>"
                    send_tg_notification(msg)
                    send_discord_notification(msg)
                else:
                    monitored.append({"address": addr, "label": label})
                    msg = f"✅ <b>添加成功</b>\n已开始监控：<b>{label}</b>\n<code>{addr}</code>"
                    send_tg_notification(msg)
                    send_discord_notification(msg)
                config_changed = True
                
            elif text.startswith("/remove"):
                parts = text.split(maxsplit=1)
                if len(parts) < 2:
                    msg = "⚠️ <b>格式错误</b>\n正确格式: <code>/remove 钱包地址</code>"
                    send_tg_notification(msg)
                    send_discord_notification(msg)
                    continue
                
                addr = parts[1].lower().strip()
                initial_len = len(monitored)
                monitored = [item for item in monitored if item.get("address").lower() != addr]
                
                if len(monitored) < initial_len:
                    msg = f"❌ <b>移除成功</b>\n已停止监控地址：\n<code>{addr}</code>"
                    send_tg_notification(msg)
                    send_discord_notification(msg)
                    config_changed = True
                else:
                    msg = f"⚠️ <b>未找到该地址</b>\n监控列表中不包含地址：\n<code>{addr}</code>"
                    send_tg_notification(msg)
                    send_discord_notification(msg)
                    
        if config_changed:
            config_data["monitored_addresses"] = monitored
            save_config(config_data)
            
        return new_last_id
    except Exception as e:
        print(f"Error processing Telegram commands: {e}")
        return last_update_id

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

def format_fill_message(wallet_label, address, fills):
    lines = []
    short_addr = f"{address[:6]}...{address[-4:]}"
    lines.append(f"👤 <b>{wallet_label}</b> (<code>{short_addr}</code>)")
    
    sorted_fills = sorted(fills, key=lambda x: x.get("time", 0))
    
    for fill in sorted_fills:
        coin = fill.get("coin", "未知代币")
        px = float(fill.get("px", 0))
        sz = float(fill.get("sz", 0))
        value_usd = px * sz
        
        side = fill.get("side", "")
        direction = fill.get("dir", "")
        
        if not direction:
            direction = "买入 (Buy)" if side == "B" else "卖出 (Sell)"
            
        emoji = "🟢" if "B" in side or "Buy" in direction or "Long" in direction else "🔴"
        
        pct_str = ""
        start_pos_str = fill.get("startPosition", "0")
        try:
            start_pos = float(start_pos_str)
            if start_pos == 0:
                pct_str = " | <b>首笔建仓 (100%)</b>"
            else:
                pct = (sz / abs(start_pos)) * 100
                if pct > 100:
                    pct_str = f" | <b>仓位占比: {pct:.1f}% (反手/超额)</b>"
                else:
                    pct_str = f" | <b>仓位占比: {pct:.1f}%</b>"
        except Exception:
            pass
            
        pnl_str = ""
        closed_pnl = fill.get("closedPnl", "0")
        try:
            pnl_val = float(closed_pnl)
            if pnl_val != 0:
                pnl_emoji = "🟢" if pnl_val > 0 else "🔴"
                pnl_str = f" (盈亏: {pnl_emoji}<code>${pnl_val:+.2f}</code>)"
        except ValueError:
            pass
            
        fill_time_ms = fill.get("time", 0)
        time_str = datetime.fromtimestamp(fill_time_ms / 1000.0).strftime("%H:%M:%S")
        
        if px < 0.001:
            px_str = f"${px:.8f}"
        elif px < 1:
            px_str = f"${px:.4f}"
        else:
            px_str = f"${px:,.2f}"
            
        lines.append(
            f"  • {emoji} <b>{direction}</b> | <b>{coin}</b>{pct_str}{pnl_str}\n"
            f"    均价: {px_str} | 数量: {sz:g} (${value_usd:,.2f} USD)\n"
            f"    时间: {time_str}"
        )
    return "\n".join(lines)

def main():
    seen_set, last_update_id = load_seen_trades_and_offset()
    new_update_id = process_telegram_commands(last_update_id)
    
    config = load_config()
    addresses = config.get("monitored_addresses", [])
    
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
            
    if first_run:
        save_seen_trades_and_offset(seen_set, new_update_id)
        startup_msg = (
            "🤖 <b>Hyperliquid 监控机器人初始化成功！</b>\n\n"
            "系统已将当前历史交易归档，从现在起将实时监听最新仓位变动。\n"
            f"📊 <b>当前监控数</b>: {len(addresses)} 个钱包\n\n"
            "💡 <b>提示</b>：您现在可以通过与我对话发送指令来增减监控地址：\n"
            "• <code>/add 钱包地址 备注标签</code>\n"
            "• <code>/remove 钱包地址</code>"
        )
        send_tg_notification(startup_msg)
        send_discord_notification(startup_msg)
        print("初始化完成通知已发送。")
        return

    if total_new_count > 0:
        if total_new_count > 30:
            print(f"发现大量交易 ({total_new_count} 笔)，判定为新钱包初始化，自动归档并跳过推送。")
            info_msg = (
                f"📊 <b>监控列表已更新 / 历史数据归档</b>\n"
                f"检测到共 <code>{total_new_count}</code> 笔历史交易，已自动归档以防打扰。\n"
                f"自此之后的交易变动将正常推送。"
            )
            tg_success = send_tg_notification(info_msg)
            dc_success = send_discord_notification(info_msg)
            if tg_success or dc_success:
                save_seen_trades_and_offset(seen_set, new_update_id)
                print("归档状态推送成功。")
            else:
                print("归档状态推送失败。")
        else:
            print(f"发现 {total_new_count} 笔新交易！正在发送汇总通知...")
            msg_blocks = ["🔔 <b>Hyperliquid 交易汇总提醒</b> (近30分钟)\n"]
            
            for addr, data in all_new_fills_by_wallet.items():
                block_text = format_fill_message(data["label"], addr, data["fills"])
                msg_blocks.append(block_text)
                
            full_msg = "\n\n".join(msg_blocks)
            
            # 同时发送 Telegram 和 Discord 推送
            tg_success = send_tg_notification(full_msg)
            dc_success = send_discord_notification(full_msg)
            
            # 只要任意一个渠道发送成功，就更新已读库，避免在重复运行时重发
            if tg_success or dc_success:
                save_seen_trades_and_offset(seen_set, new_update_id)
                print("交易汇总提醒推送成功。")
            else:
                print("交易提醒推送失败，未更新已读交易库。")
    else:
        print("未发现新交易。正在发送空交易状态推送...")
        status_msg = "ℹ️ <b>无新交易</b> (近30分钟)"
        send_tg_notification(status_msg)
        send_discord_notification(status_msg)
        save_seen_trades_and_offset(seen_set, new_update_id)

if __name__ == "__main__":
    main()
