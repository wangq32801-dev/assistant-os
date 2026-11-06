#!/usr/bin/env python3
"""守望狗 — 端口/用户/SSH 异常即时告警"""

import subprocess, json, os, sys

STATE_DIR = os.path.expanduser("~/.hermes/state")
BASELINE_PORTS = f"{STATE_DIR}/baseline-ports.txt"
BASELINE_USERS = f"{STATE_DIR}/baseline-users.txt"
KNOWN_IPS = f"{STATE_DIR}/known-ips.txt"
SCAN_WINDOW = "10 minutes ago"  # 匹配cron间隔

def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
    return r.stdout.strip(), r.stderr.strip()

def check_ports():
    now, _ = run("ss -tlnp | awk '/LISTEN/ {print $4}' | sort")
    if not os.path.exists(BASELINE_PORTS):
        return None
    old = open(BASELINE_PORTS).read().strip()
    new_ports = set(now.split("\n")) - set(old.split("\n"))
    gone_ports = set(old.split("\n")) - set(now.split("\n"))
    msgs = []
    if new_ports:
        msgs.append(f"🆕 新增监听端口：{', '.join(sorted(new_ports))}")
    if gone_ports:
        msgs.append(f"❌ 消失的端口：{', '.join(sorted(gone_ports))}")
    return "\n".join(msgs) if msgs else None

def check_users():
    now, _ = run("awk -F: '$3>=1000 && $3<65534 {print $1}' /etc/passwd | sort")
    if not os.path.exists(BASELINE_USERS):
        return None
    old = open(BASELINE_USERS).read().strip()
    new_users = set(now.split("\n")) - set(old.split("\n"))
    if new_users:
        return f"🆕 新增系统用户：{', '.join(sorted(new_users))}"
    return None

def check_ssh():
    if not os.path.exists(KNOWN_IPS):
        return None
    known = set(open(KNOWN_IPS).read().strip().split("\n"))
    out, _ = run(f'sudo journalctl -u ssh --since "{SCAN_WINDOW}" --no-pager | grep "Accepted "')
    if not out:
        return None
    new_ips = []
    for line in out.split("\n"):
        parts = line.split()
        # format: Mon DD HH:MM:SS host sshd[pid]: Accepted publickey for user from IP port NNNNN ...
        try:
            idx = parts.index("from")
            ip = parts[idx + 1]
            if ip not in known:
                user = parts[idx - 1] if idx > 0 else "?"
                auth = [p for p in parts if p == "publickey" or p == "password"]
                auth_str = auth[0] if auth else "?"
                new_ips.append(f"{ip} ({user}, {auth_str})")
        except (ValueError, IndexError):
            continue
    if new_ips:
        return f"⚠️ 陌生SSH登录：{', '.join(new_ips)}"
    return None

def notify_via_bridge(msg):
    """严重告警通过Bridge发送，最终推到微信"""
    import json
    try:
        payload = json.dumps({
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": "bridge_send", "arguments": {
                "to": "server", "content": msg, "priority": 0
            }}, "id": 1
        })
        subprocess.run([
            "curl", "-s", "-X", "POST", "http://127.0.0.1:9090/",
            "-H", "Content-Type: application/json",
            "-H", f"Authorization: Bearer {os.environ.get('BRIDGE_TOKEN', '')}",
            "-d", payload
        ], timeout=10, capture_output=True)
    except Exception:
        pass

def check_cron_delivery():
    """检查需要推送给用户的cron任务是否有推送失败"""
    import json
    try:
        out, _ = run("hermes cron list --json 2>/dev/null")
        if not out:
            # 备选：直接读cron db
            out, _ = run("find ~/.hermes -name 'cron.db' -exec sqlite3 {} 'select id,name,last_delivery_error from jobs where last_delivery_error is not null and last_delivery_error != \"\" and deliver != \"local\"' \\; 2>/dev/null")
            if not out:
                return None
            msgs = []
            for line in out.strip().split("\n"):
                if not line: continue
                parts = line.split("|")
                if len(parts) >= 3:
                    name, err = parts[1], parts[2]
                    msgs.append(f"📤 {name}: 推送失败 - {err[:60]}")
            return "\n".join(msgs) if msgs else None
        # 有JSON输出就解析
        data = json.loads(out)
        jobs = data.get("jobs", data.get("data", []))
        failed = []
        for j in jobs:
            if j.get("last_delivery_error") and j.get("deliver") != "local":
                failed.append(f"📤 {j.get('name','?')}: 推送失败 - {str(j['last_delivery_error'])[:60]}")
        return "\n".join(failed) if failed else None
    except Exception as e:
        return f"❌ cron推送检查失败: {e}"

def main():
    alerts = []
    critical = []  # 严重告警单独推送
    for check, label, is_critical in [
        (check_ports, "端口", False),
        (check_users, "账户", True),
        (check_ssh, "SSH", True),
        (check_cron_delivery, "Cron推送", True),
    ]:
        try:
            r = check()
            if r:
                alerts.append(r)
                if is_critical:
                    critical.append(r)
        except Exception as e:
            alerts.append(f"❌ {label}检查失败: {e}")
    if critical:
        notify_via_bridge("⚠️ 守望狗严重告警：\n" + "\n".join(critical))
    if alerts:
        print("\n".join(alerts))

if __name__ == "__main__":
    main()
