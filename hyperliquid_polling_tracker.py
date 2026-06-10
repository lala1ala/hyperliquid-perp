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
        # 如果文件不存在，初始化默认结构
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
    """
    返回 (seen_tids_set, last_update_id)
    兼容旧版本 seen_trades.json 只是一个 list 的情况
    """
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
            
    # 如果文件不存在且是第一次运行，我们返回 seen_set = None 标识初始化
    if not os.path.exists(DB_FILE):
        return None, 0
        
    return seen_set, last_update_id

def save_seen_trades_and_offset(seen_set, last_update_id):
    seen_list = list(seen_set)[-3000:]  # 限制大小在 3000 条内
    db_data = {
        "seen_tids": seen_list,
        "last_update_id": last_update_id
    }
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving seen trades and offset: {e}")

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

def process_telegram_commands(last_update_id):
    """
    通过 Telegram getUpdates 读取发给机器人的新增指令，并更新 hl_tracker_config.json
    """
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
            
            # 只处理来自授权 Chat ID 的指令以保证安全
            if sender_id != chat_id_str:
                continue
                
            text = message.get("text", "").strip()
            if not text:
                continue
                
            # 处理 /add 指令
            if text.startswith("/add"):
                parts = text.split(maxsplit=2)
                if len(parts) < 2:
                    send_tg_notification("⚠️ <b>格式错误</b>\n正确格式: <code>/add 钱包地址 备注标签</code>")
                    continue
                
                addr = parts[1].lower().strip()
                label = parts[2].strip() if len(parts) == 3 else "未命名"
                
                if not addr.startswith("0x") or len(addr) != 42:
                    send_tg_notification(f"⚠️ <b>格式错误</b>\n地址 <code>{addr}</code> 似乎不是合法的 EVM/Hyperliquid 地址。")
                    continue
                
                # 检查是否已存在
                existing = next((item for item in monitored if item.get("address").lower() == addr), None)
                if existing:
                    existing["label"] = label
                    send_tg_notification(f"✅ <b>修改成功</b>\n地址已存在，已更新标签为：<b>{label}</b>\n<code>{addr}</code>")
                else:
                    monitored.append({"address": addr, "label": label})
                    send_tg_notification(f"✅ <b>添加成功</b>\n已开始监控：<b>{label}</b>\n<code>{addr}</code>")
                config_changed = True
                
            # 处理 /remove 指令
            elif text.startswith("/remove"):
                parts = text.split(maxsplit=1)
                if len(parts) < 2:
                    send_tg_notification("⚠️ <b>格式错误</b>\n正确格式: <code>/remove 钱包地址</code>")
                    continue
                
                addr = parts[1].lower().strip()
                
                # 查找并删除
                initial_len = len(monitored)
                monitored = [item for item in monitored if item.get("address").lower() != addr]
                
                if len(monitored) < initial_len:
                    send_tg_notification(f"❌ <b>移除成功</b>\n已停止监控地址：\n<code>{addr}</code>")
                    config_changed = True
                else:
                    send_tg_notification(f"⚠️ <b>未找到该地址</b>\n监控列表中不包含地址：\n<code>{addr}</code>")
                    
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
        
        # 仓位变动占比计算 (% of Position)
        pct_str = ""
        start_pos_str = fill.get("startPosition", "0")
        try:
            start_pos = float(start_pos_str)
            if start_pos == 0:
                # 从零开仓，相当于占新仓位的100%
                pct_str = " | <b>首笔建仓 (100%)</b>"
            else:
                # 仓位占比 = 本次交易数量 / 变动前持仓绝对值
                pct = (sz / abs(start_pos)) * 100
                if pct > 100:
                    pct_str = f" | <b>仓位占比: {pct:.1f}% (反手/超额)</b>"
                else:
                    pct_str = f" | <b>仓位占比: {pct:.1f}%</b>"
        except Exception:
            pass
            
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
            f"  • {emoji} <b>{direction}</b> | <b>{coin}</b>{pct_str}{pnl_str}\n"
            f"    均价: {px_str} | 数量: {sz:g} (${value_usd:,.2f} USD)\n"
            f"    时间: {time_str}"
        )
    return "\n".join(lines)

def main():
    seen_set, last_update_id = load_seen_trades_and_offset()
    
    # 1. 优先读取并处理来自 Telegram 的 /add 和 /remove 指令
    new_update_id = process_telegram_commands(last_update_id)
    
    # 2. 读取最新的钱包地址配置
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
        print("初始化完成通知已发送至 Telegram。")
        return

    # 如果有新交易，生成汇总通知（全半小时合并为一条消息）
    if total_new_count > 0:
        print(f"发现 {total_new_count} 笔新交易！正在发送汇总通知...")
        msg_blocks = ["🔔 <b>Hyperliquid 交易汇总提醒</b> (近30分钟)\n"]
        
        for addr, data in all_new_fills_by_wallet.items():
            block_text = format_fill_message(data["label"], addr, data["fills"])
            msg_blocks.append(block_text)
            
        full_msg = "\n\n".join(msg_blocks)
        
        # 发送 Telegram 消息
        success = send_tg_notification(full_msg)
        if success:
            save_seen_trades_and_offset(seen_set, new_update_id)
            print("交易汇总提醒推送成功。")
        else:
            print("交易提醒推送失败，未更新已读交易库。")
    else:
        # 如果没有新交易，但也可能处理了指令，需要更新 last_update_id 并保存
        save_seen_trades_and_offset(seen_set, new_update_id)
        print("未发现新交易。")

if __name__ == "__main__":
    main()
