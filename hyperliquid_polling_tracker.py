import os
import sys
import time
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
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {"monitored_addresses": []}
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
    corrupted = False
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
            corrupted = True
            try:
                import shutil
                shutil.copy(DB_FILE, DB_FILE + f".bak_{int(time.time())}")
            except:
                pass
            
    if not os.path.exists(DB_FILE) and not corrupted:
        return None, 0
    return seen_set, last_update_id

def save_seen_trades_and_offset(seen_set, last_update_id):
    import shutil
    seen_list = sorted(list(seen_set))
    db_data = {
        "seen_tids": seen_list,
        "last_update_id": last_update_id
    }
    try:
        temp_file = DB_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False, indent=2)
        shutil.move(temp_file, DB_FILE)
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
        return False
        
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

def process_telegram_commands(last_update_id, seen_set):
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
                    
                    fills, err = fetch_user_fills(addr)
                    history_count = 0
                    if not err and fills and seen_set is not None:
                        for f in fills:
                            tid = f.get("tid")
                            if tid:
                                seen_set.add(str(tid))
                                history_count += 1
                                
                    msg = f"✅ <b>添加成功</b>\n已开始监控：<b>{label}</b>\n<code>{addr}</code>\n"
                    if history_count > 0:
                        msg += f"<i>(已预先抓取并静默归档 {history_count} 笔历史交易)</i>"
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
    import time
    payload = {
        "type": "userFillsByTime",
        "user": address,
        "startTime": int((time.time() - 2 * 60 * 60) * 1000)
    }
    headers = {
        "Content-Type": "application/json"
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data, None
            else:
                err = f"API Error Payload: {data}"
                print(f"[{address}] Fetch failed: {err}")
                return [], err
        else:
            err = f"Status {resp.status_code}: {resp.text}"
            print(f"[{address}] Fetch failed: {err}")
            return [], err
    except Exception as e:
        err = str(e)
        print(f"[{address}] Exception when fetching fills: {err}")
        return [], err

def format_fill_message(wallet_label, address, fills):
    lines = []
    short_addr = f"{address[:6]}...{address[-4:]}"
    lines.append(f"👤 <b>{wallet_label}</b> (<code>{short_addr}</code>)")
    
    # Group fills by (coin, direction)
    groups = {}
    group_order = []
    
    sorted_fills = sorted(fills, key=lambda x: x.get("time", 0))
    
    for fill in sorted_fills:
        coin = fill.get("coin", "未知代币")
        side = fill.get("side", "")
        direction = fill.get("dir", "")
        if not direction:
            direction = "买入 (Buy)" if side == "B" else "卖出 (Sell)"
            
        group_key = (coin, direction)
        if group_key not in groups:
            groups[group_key] = []
            group_order.append(group_key)
        groups[group_key].append(fill)
        
    for group_key in group_order:
        coin, direction = group_key
        group_fills = groups[group_key]
        
        total_sz = 0.0
        total_value_usd = 0.0
        total_pnl = 0.0
        total_pct = 0.0
        has_pnl = False
        has_pct = False
        first_fill_is_first_build = False
        
        # Determine group emoji from first fill
        first_fill = group_fills[0]
        first_side = first_fill.get("side", "")
        group_emoji = "🟢" if "B" in first_side or "Buy" in direction or "Long" in direction else "🔴"
        
        for fill in group_fills:
            try:
                px = float(fill.get("px", 0) or 0)
                sz = float(fill.get("sz", 0) or 0)
            except (ValueError, TypeError):
                px = 0.0
                sz = 0.0
            
            total_sz += sz
            total_value_usd += px * sz
            
            closed_pnl = fill.get("closedPnl", "0")
            try:
                pnl_val = float(closed_pnl)
                if pnl_val != 0:
                    total_pnl += pnl_val
                    has_pnl = True
            except ValueError:
                pass
                
            start_pos_str = fill.get("startPosition", "0")
            try:
                start_pos = float(start_pos_str)
                if start_pos == 0:
                    first_fill_is_first_build = True
                    has_pct = True
                else:
                    pct = (sz / abs(start_pos)) * 100
                    total_pct += pct
                    has_pct = True
            except Exception:
                pass
                
        total_pct_str = ""
        if has_pct:
            if first_fill_is_first_build:
                total_pct_str = " | <b>总仓位占比: 首笔建仓</b>"
            elif total_pct > 0:
                if total_pct > 100:
                    total_pct_str = f" | <b>总仓位占比: {total_pct:.1f}% (反手/超额)</b>"
                else:
                    total_pct_str = f" | <b>总仓位占比: {total_pct:.1f}%</b>"
                    
        total_pnl_str = ""
        if has_pnl:
            total_pnl_emoji = "🟢" if total_pnl > 0 else "🔴"
            total_pnl_str = f" (总盈亏: {total_pnl_emoji}<code>${total_pnl:+.2f}</code>)"
            
        avg_px = total_value_usd / total_sz if total_sz > 0 else 0.0
        if avg_px < 0.001:
            avg_px_str = f"${avg_px:.8f}"
        elif avg_px < 1:
            avg_px_str = f"${avg_px:.4f}"
        else:
            avg_px_str = f"${avg_px:,.2f}"
            
        group_lines = []
        group_lines.append(f"  🔸 <b>{direction} | {coin} (汇总)</b>{total_pct_str}{total_pnl_str}")
        group_lines.append(f"    总量: <code>{total_sz:g}</code> | 均价: <code>{avg_px_str}</code> | 总额: <code>${total_value_usd:,.2f}</code>")
            
        lines.append("\n".join(group_lines))
        
    return "\n".join(lines)

def main():
    seen_set, last_update_id = load_seen_trades_and_offset()
    new_update_id = process_telegram_commands(last_update_id, seen_set)
    
    config = load_config()
    addresses = config.get("monitored_addresses", [])
    
    first_run = (seen_set is None)
    
    if first_run:
        print("首次运行检测：正在初始化已读交易库，本次不会发送具体交易提醒以防打扰。")
        seen_set = set()
        
    all_new_fills_by_wallet = {}
    total_new_count = 0
    errors = []
    
    for wallet in addresses:
        addr = wallet.get("address", "").lower().strip()
        label = wallet.get("label", "未命名")
        if not addr:
            continue
            
        print(f"正在获取 [{label}] ({addr}) 的成交历史...")
        fills, err = fetch_user_fills(addr)
        if err:
            errors.append(f"[{label}] API 错误: {err}")
            continue
            
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

    def send_long_msg(text):
        has_tg = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
        has_dc = bool(os.getenv("DISCORD_WEBHOOK_URL"))
        
        if not has_tg and not has_dc:
            print("Warning: Neither Telegram nor Discord is configured.")
            return False

        # Discord content limit is 2000, Telegram is 4096. 
        # Using 1900 to be safe for both platforms and prevent Discord from throwing 400 Bad Request.
        MAX_LEN = 1900
        all_success = True
        
        chunks = []
        while len(text) > MAX_LEN:
            idx = text.rfind('\n\n', 0, MAX_LEN)
            if idx == -1: idx = MAX_LEN
            chunks.append(text[:idx])
            text = text[idx:].lstrip()
        if text:
            chunks.append(text)
            
        for chunk in chunks:
            chunk_ok = True
            if has_tg:
                if not send_tg_notification(chunk):
                    chunk_ok = False
            if has_dc:
                if not send_discord_notification(chunk):
                    chunk_ok = False
            if not chunk_ok:
                all_success = False
            
            # Sleep slightly to prevent hitting Discord/Telegram rate limits on multiple chunks
            time.sleep(0.5)
                
        return all_success

    if total_new_count > 0:
        if total_new_count > 1000:
            print(f"发现大量交易 ({total_new_count} 笔)，判定为新钱包初始化，自动归档并跳过推送。")
            info_msg = (
                f"📊 <b>监控列表已更新 / 历史数据归档</b>\n"
                f"检测到共 <code>{total_new_count}</code> 笔历史交易，已自动归档以防打扰。\n"
                f"自此之后的交易变动将正常推送。"
            )
            success = send_long_msg(info_msg)
            if success:
                save_seen_trades_and_offset(seen_set, new_update_id)
                print("归档状态推送成功。")
            else:
                print("归档状态推送失败。")
        else:
            print(f"发现 {total_new_count} 笔新交易！正在发送汇总通知...")
            msg_blocks = ["🔔 <b>Hyperliquid 交易汇总提醒</b> (近30分钟)\n"]
            
            for addr, data in all_new_fills_by_wallet.items():
                try:
                    block_text = format_fill_message(data["label"], addr, data["fills"])
                    msg_blocks.append(block_text)
                except Exception as e:
                    errors.append(f"[{data['label']}] 格式化错误: {e}")
                
            full_msg = "\n\n".join(msg_blocks)
            if errors:
                full_msg += "\n\n⚠️ <b>获取警告</b>\n" + "\n".join(errors)
            
            success = send_long_msg(full_msg)
            
            if success:
                save_seen_trades_and_offset(seen_set, new_update_id)
                print("交易汇总提醒推送成功。")
            else:
                print("交易提醒推送失败，未更新已读交易库。")
    else:
        print("未发现新交易。正在发送空交易状态推送...")
        status_msg = "ℹ️ <b>无新交易</b> (近30分钟)"
        if errors:
            status_msg += "\n\n⚠️ <b>获取警告</b>\n" + "\n".join(errors)
            
        send_long_msg(status_msg)
        save_seen_trades_and_offset(seen_set, new_update_id)

if __name__ == "__main__":
    main()
