import os
import sys
import time
import json
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    seen_tids = {}
    last_update_id = 0
    position_state = {}
    corrupted = False
    now_ms = int(time.time() * 1000)
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    raw_tids = data.get("seen_tids", {})
                    if isinstance(raw_tids, dict):
                        # 新格式: {tid: 成交时间(ms)}
                        for k, v in raw_tids.items():
                            try:
                                seen_tids[str(k)] = int(v)
                            except (ValueError, TypeError):
                                seen_tids[str(k)] = now_ms
                    elif isinstance(raw_tids, list):
                        # 旧格式: [tid, ...]，没有时间信息，统一记为当前时间避免迁移时重播
                        seen_tids = {str(t): now_ms for t in raw_tids}
                    last_update_id = data.get("last_update_id", 0)
                    ps = data.get("position_state", {})
                    if isinstance(ps, dict):
                        position_state = ps
                elif isinstance(data, list):
                    # 更旧的格式: 纯 list
                    seen_tids = {str(t): now_ms for t in data}
        except Exception as e:
            print(f"Warning: Failed to load seen_trades.json: {e}")
            corrupted = True
            try:
                import shutil
                shutil.copy(DB_FILE, DB_FILE + f".bak_{int(time.time())}")
            except:
                pass

    if not os.path.exists(DB_FILE) and not corrupted:
        return None, 0, {}
    return seen_tids, last_update_id, position_state

def prune_old_tids(seen_tids, max_age_ms=4 * 60 * 60 * 1000, max_keep=200000):
    """
    防止 seen_trades.json 无限膨胀。
    tid 不是递增的（是无序唯一 ID），不能按 tid 数值大小判断新旧，
    必须按成交时间 time 清理：只保留最近 max_age_ms 内的成交，
    若仍超过 max_keep 则按时间倒序保留最新的 max_keep 条兜底。
    """
    if not seen_tids:
        return seen_tids
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - max_age_ms
    pruned = {tid: t for tid, t in seen_tids.items() if t >= cutoff}
    if len(pruned) > max_keep:
        sorted_items = sorted(pruned.items(), key=lambda kv: kv[1], reverse=True)
        pruned = dict(sorted_items[:max_keep])
    print(f"Pruned {len(seen_tids) - len(pruned)} old tids (kept {len(pruned)})")
    return pruned

def save_seen_trades_and_offset(seen_tids, last_update_id, position_state=None):
    import shutil
    # 按成交时间定期清理旧 tid，防止文件无限膨胀
    seen_tids = prune_old_tids(seen_tids)
    db_data = {
        "seen_tids": seen_tids,
        "last_update_id": last_update_id
    }
    if position_state is not None:
        db_data["position_state"] = position_state
    try:
        temp_file = DB_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(db_data, f, ensure_ascii=False, indent=2)
        shutil.move(temp_file, DB_FILE)
    except Exception as e:
        print(f"Error saving seen trades and offset: {e}")

def _fill_time_ms(fill):
    """安全获取成交时间（毫秒）。Hyperliquid 正常总会返回 time，这里做兜底。"""
    t = fill.get("time") or 0
    try:
        t = int(t)
    except (ValueError, TypeError):
        t = 0
    if t <= 0:
        t = int(time.time() * 1000)
    return t

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
        # NOTE: Telegram API ALWAYS returns HTTP 200 even on failure (wrong token,
        # bot kicked, invalid chat_id, etc.). The real success indicator is
        # resp.json()["ok"]. Checking only status_code == 200 is a critical bug
        # that causes missed alerts to be silently marked as "sent" in the DB.
        result = resp.json()
        if result.get("ok"):
            return True
        else:
            err_code = result.get("error_code", "?")
            err_desc = result.get("description", "unknown error")
            print(f"Telegram API error {err_code}: {err_desc}")
            return False
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

def process_telegram_commands(last_update_id, seen_tids):
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
                    if not err and fills and seen_tids is not None:
                        for f in fills:
                            tid = f.get("tid")
                            if tid:
                                seen_tids[str(tid)] = _fill_time_ms(f)
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

def fetch_user_fills(address, lookback_seconds=2 * 60 * 60):
    """
    分页拉取指定地址最近 lookback_seconds 内的全部成交。

    Hyperliquid 的 userFillsByTime 接口单次最多返回 2000 笔且按时间升序，
    若一个钱包在窗口内成交超过 2000 笔，直接单次调用会漏掉最新（最关键）的
    那批成交。这里通过不断前移 startTime 游标分页拉取，保证巨量平仓也不会漏。
    返回 (成交列表[按时间倒序], 错误信息)。
    """
    url = "https://api.hyperliquid.xyz/info"
    start_time_ms = int((time.time() - lookback_seconds) * 1000)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    all_fills = []
    local_seen = set()
    cursor_start = start_time_ms
    max_pages = 50  # 安全上限：50 * 2000 = 10 万笔，足够覆盖任何极端情况

    for _ in range(max_pages):
        payload = {
            "type": "userFillsByTime",
            "user": address,
            "startTime": cursor_start
        }

        data = None
        last_err = ""
        for attempt in range(3):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    if not isinstance(data, list):
                        err = f"API Error Payload: {data}"
                        print(f"[{address}] Fetch failed: {err}")
                        return [], err
                    break
                else:
                    last_err = f"Status {resp.status_code}: {resp.text}"
                    print(f"[{address}] Fetch failed attempt {attempt+1}: {last_err}")
                    time.sleep(2)
            except Exception as e:
                last_err = str(e)
                print(f"[{address}] Exception when fetching fills attempt {attempt+1}: {last_err}")
                time.sleep(2)

        if data is None:
            return [], f"Failed after 3 attempts. Last error: {last_err}"

        if not data:
            break

        for fill in data:
            tid = str(fill.get("tid", ""))
            if not tid:
                continue
            if tid not in local_seen:
                local_seen.add(tid)
                all_fills.append(fill)

        # 不足 2000 说明已经拉到窗口内全部成交
        if len(data) < 2000:
            break

        # 游标前移；用最后一条成交时间（含重叠，靠 tid 去重），确保不漏同毫秒边界
        last_time = data[-1].get("time", 0)
        if last_time <= cursor_start:
            cursor_start = last_time + 1
        else:
            cursor_start = last_time

    # 按时间倒序返回，方便后续优先看到最新成交
    all_fills.sort(key=lambda x: x.get("time", 0), reverse=True)
    return all_fills, None

def fetch_position_state(address):
    """获取地址当前持仓快照，用于检测「全部平仓/大幅减仓」等关键事件。"""
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "clearinghouseState", "user": address}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return None, str(e)

    position_count = 0
    total_notional = 0.0
    for pos in data.get("assetPositions", []):
        if pos.get("type") != "oneWay":
            continue
        p = pos.get("position", {})
        try:
            szi = float(p.get("szi", 0) or 0)
        except (ValueError, TypeError):
            szi = 0.0
        if abs(szi) < 1e-9:
            continue
        position_count += 1
        try:
            total_notional += abs(float(p.get("positionValue", 0) or 0))
        except (ValueError, TypeError):
            pass

    margin_summary = data.get("marginSummary", {})
    try:
        account_value = float(margin_summary.get("accountValue", 0) or 0)
    except (ValueError, TypeError):
        account_value = 0.0

    return {
        "position_count": position_count,
        "total_notional": total_notional,
        "account_value": account_value,
    }, None

def format_position_change_alert(address, label, prev, curr):
    """对比前后两次持仓快照，生成关键仓位异动提醒。"""
    prev_count = int(prev.get("position_count", 0) or 0)
    curr_count = int(curr.get("position_count", 0) or 0)
    prev_av = float(prev.get("account_value", 0) or 0)
    curr_av = float(curr.get("account_value", 0) or 0)
    prev_notional = float(prev.get("total_notional", 0) or 0)
    short = f"{address[:6]}...{address[-4:]}"

    # 只有之前确实持有仓位时才报警，避免首轮/空仓地址产生噪音
    if prev_count <= 0:
        return None

    all_closed = curr_count == 0
    big_reduce = (not all_closed) and curr_count < prev_count * 0.5
    funds_cleared = prev_av > 0 and curr_av <= 1e-9

    if not (all_closed or big_reduce or funds_cleared):
        return None

    if all_closed or funds_cleared:
        emoji, title = "🚨", "全部平仓 / 资金清空"
    else:
        emoji, title = "⚠️", "大幅减仓"

    lines = [f"{emoji} <b>【{title}】{label}</b> (<code>{short}</code>)"]
    lines.append(f"   持仓数量: <code>{prev_count}</code> → <code>{curr_count}</code>")
    if prev_notional > 0:
        lines.append(f"   平仓前名义价值: <code>${prev_notional:,.2f}</code>")
    if prev_av > 0 or curr_av > 0:
        lines.append(f"   账户价值: <code>${prev_av:,.2f}</code> → <code>${curr_av:,.2f}</code>")

    return "\n".join(lines)

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
        first_side = str(first_fill.get("side") or "")
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
                pnl_val = float(closed_pnl if closed_pnl is not None else 0)
                if pnl_val != 0:
                    total_pnl += pnl_val
                    has_pnl = True
            except (ValueError, TypeError):
                pass
                
            start_pos_str = fill.get("startPosition", "0")
            try:
                start_pos = float(start_pos_str if start_pos_str is not None else 0)
                if start_pos == 0:
                    first_fill_is_first_build = True
                    has_pct = True
                else:
                    pct = (sz / abs(start_pos)) * 100
                    total_pct += pct
                    has_pct = True
            except (ValueError, TypeError):
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
    # 全局超时保护：如果脚本运行超过 20 分钟则强制退出
    # GitHub Actions 默认超时 6 小时，但我们不应该让脚本挂起那么久
    import signal

    def timeout_handler(signum, frame):
        print("FATAL: Script timed out after 20 minutes. Exiting with error.")
        sys.exit(1)

    # 仅在非 Windows 系统上设置 signal alarm（GitHub Actions 使用 Linux）
    if hasattr(signal, 'SIGALRM'):
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(1200)  # 20 分钟

    try:
        _main_impl()
    except Exception as e:
        print(f"FATAL: Unhandled exception in main: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if hasattr(signal, 'SIGALRM'):
            signal.alarm(0)  # 取消超时

def _main_impl():
    seen_tids, last_update_id, position_state = load_seen_trades_and_offset()
    new_update_id = process_telegram_commands(last_update_id, seen_tids)

    config = load_config()
    addresses = config.get("monitored_addresses", [])

    first_run = (seen_tids is None)

    if first_run:
        print("首次运行检测：正在初始化已读交易库，本次不会发送具体交易提醒以防打扰。")
        seen_tids = {}

    position_state = position_state or {}
    updated_position_state = dict(position_state)

    all_new_fills_by_wallet = {}
    position_alerts = []
    total_new_count = 0
    errors = []

    # 使用线程池并发获取所有钱包的成交历史 + 持仓快照，大幅减少运行时间
    def fetch_single_wallet(wallet):
        addr = wallet.get("address", "").lower().strip()
        label = wallet.get("label", "未命名")
        if not addr:
            return None
        print(f"正在获取 [{label}] ({addr}) 的成交历史...")
        fills, ferr = fetch_user_fills(addr)
        pstate, perr = fetch_position_state(addr)
        return {"addr": addr, "label": label, "fills": fills, "err": ferr,
                "pstate": pstate, "perr": perr}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_single_wallet, w): w for w in addresses}
        for future in as_completed(futures):
            result = future.result()
            if result is None:
                continue
            addr = result["addr"]
            label = result["label"]
            fills = result["fills"]
            err = result["err"]
            pstate = result["pstate"]
            perr = result["perr"]

            # 1) 持仓异动检测（不依赖成交笔数，专门兜底巨量平仓）
            if perr:
                errors.append(f"[{label}] 持仓快照错误: {perr}")
            elif pstate is not None:
                updated_position_state[addr] = pstate
                if not first_run:
                    prev = position_state.get(addr)
                    if prev:
                        alert = format_position_change_alert(addr, label, prev, pstate)
                        if alert:
                            position_alerts.append(alert)

            # 2) 成交检测
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
                    seen_tids[tid_str] = _fill_time_ms(fill)
                    continue

                if tid_str not in seen_tids:
                    new_fills.append(fill)
                    seen_tids[tid_str] = _fill_time_ms(fill)
                    total_new_count += 1

            if new_fills:
                all_new_fills_by_wallet[addr] = {
                    "label": label,
                    "fills": new_fills
                }
    if first_run:
        save_seen_trades_and_offset(seen_tids, new_update_id, updated_position_state)
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

    if total_new_count > 0 or position_alerts:
        msg_blocks = []

        if position_alerts:
            msg_blocks.append("🚨 <b>Hyperliquid 仓位异动提醒</b> (近30分钟)\n")
            msg_blocks.extend(position_alerts)

        if all_new_fills_by_wallet:
            if position_alerts:
                msg_blocks.append("🔔 <b>成交明细</b>\n")
            else:
                msg_blocks.append("🔔 <b>Hyperliquid 交易汇总提醒</b> (近30分钟)\n")

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
            save_seen_trades_and_offset(seen_tids, new_update_id, updated_position_state)
            print("提醒推送成功。")
        else:
            print("提醒推送失败，未更新已读交易库与持仓快照。")
    else:
        print("未发现新交易或仓位异动。正在发送空状态推送...")
        status_msg = "ℹ️ <b>无新交易 / 无仓位异动</b> (近30分钟)"
        if errors:
            status_msg += "\n\n⚠️ <b>获取警告</b>\n" + "\n".join(errors)

        send_long_msg(status_msg)
        save_seen_trades_and_offset(seen_tids, new_update_id, updated_position_state)

if __name__ == "__main__":
    main()
