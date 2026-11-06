#!/usr/bin/env python3
"""VPS 健康监控 v2.3 — 每5分钟检查一次，异常时微信告警（CPU改用/proc/stat采样+连续2次防抖）"""
import subprocess, json, urllib.request, os, time, sys, re

# cron环境PATH不含venv，补上hermes命令路径
os.environ["PATH"] = "/home/ubuntu/.hermes/hermes-agent/venv/bin:" + os.environ.get("PATH", "/usr/bin:/bin")
CPU_WARN, MEM_WARN, DISK_WARN = 90, 90, 90
HOME = os.path.expanduser("~")
WD = os.path.join(HOME, ".hermes/watchdog")
HL = os.path.join(WD, "health.log")
AH = os.path.join(WD, "alerted.json")
os.makedirs(WD, exist_ok=True)

def log(m):
    with open(HL, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}\n")

def alert(title, detail):
    try:
        m = f"⚠️ {title}\n{detail}".replace("'", "'\\''")
        subprocess.run(f"hermes send --to weixin '{m}'", shell=True,
                       capture_output=True, text=True, timeout=15)
        log(f" 告警已推送: {title}")
    except: log(" 告警推送失败")

def alerted():
    n = time.time()
    if os.path.exists(AH):
        with open(AH) as f:
            d = json.load(f)
        return {k: v for k, v in d.items() if n - v < 300}
    return {}
def mark(k):
    d = alerted(); d[k] = time.time()
    with open(AH, "w") as f: json.dump(d, f)
def should(k): return k not in alerted()

def get_mcp_configs():
    """Extract mcp_servers via Python yaml subprocess (bypasses truncation)."""
    try:
        r = subprocess.run([sys.executable, "-c", """
import yaml, json
with open("/home/ubuntu/.hermes/config.yaml") as f:
    cfg = yaml.safe_load(f)
mcps = cfg.get("mcp_servers", {})
out = {}
for name, srv in mcps.items():
    if not isinstance(srv, dict): continue
    out[name] = {
        "url": srv.get("url", ""),
        "auth": (srv.get("headers", {}) or {}).get("Authorization", "").replace("Bearer ", ""),
        "cmd": srv.get("command", ""),
        "enabled": srv.get("enabled", True)
    }
print(json.dumps(out))
"""], capture_output=True, text=True, timeout=10)
        return json.loads(r.stdout.strip())
    except Exception as e:
        log(f" 配置解析失败: {e}")
        return {}

def check_http(url, token, timeout=8):
    try:
        body = json.dumps({"jsonrpc":"2.0","id":1,
                           "method":"tools/list","params":{}}).encode()
        hdrs = {"Content-Type":"application/json","Accept":"application/json, text/event-stream"}
        if token: hdrs["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}" + ("(token)" if e.code==401 else "")
    except Exception as ex:
        return False, str(ex)[:50]

# ── Main ──
alerts = []

# 1. Gateway（防抖：连续2次确认才告警，避免重启瞬间误报）
try:
    r = subprocess.run(["systemctl","--user","is-active","hermes-gateway-chat"],
                       capture_output=True, text=True, timeout=5)
    ok = r.stdout.strip() == "active"
    log(f" {'✅' if ok else '❌'} 网关: {'正常' if ok else r.stdout.strip()}")
    # 防抖
    GW_ST = os.path.join(WD, "gateway_state.json")
    prev_down = False
    if os.path.exists(GW_ST):
        try:
            with open(GW_ST) as f: prev_down = json.load(f).get("down", False)
        except Exception: prev_down = False
    cur_down = not ok
    with open(GW_ST, "w") as f: json.dump({"down": cur_down}, f)
    sustained = cur_down and prev_down
    if sustained: alerts.append(("VPS网关离线", f"状态: {r.stdout.strip()}（连续2次确认）"))
except: alerts.append(("VPS网关检查失败", "systemctl异常"))

# 2. Bridge
try:
    p = subprocess.run(["pgrep","-f","hermes-bridge-server.py"],
                       capture_output=True, text=True, timeout=5)
    # 端口检测（mTLS 需要客户端证书，curl 握手过不了）
    c = subprocess.run(["ss","-tlnp"], capture_output=True, text=True, timeout=5)
    port_ok = ":9090" in c.stdout
    ok = bool(p.stdout.strip()) and port_ok
    log(f" {'✅' if ok else '❌'} Bridge: {'在线' if ok else '离线'}")
    if not ok: alerts.append(("Bridge服务离线", "进程或端口9090不可用"))
except: pass

# 3. CPU/Mem/Load
try:
    # CPU: /proc/stat 采样1.5s算真实瞬时占用，避免 top -bn1 首帧瞬时误报
    def _cpu_snapshot():
        with open("/proc/stat") as f:
            vals = f.readline().split()
        nums = [float(v) for v in vals[1:]]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        return sum(nums), idle
    t0, i0 = _cpu_snapshot()
    time.sleep(1.5)
    t1, i1 = _cpu_snapshot()
    dt = t1 - t0
    cpu = 100.0 * (dt - (i1 - i0)) / dt if dt > 0 else 0
    # 防抖：连续2次(间隔5分钟=累计10分钟)超阈值才告警，杜绝瞬时尖峰瞎触发
    CPU_ST = os.path.join(WD, "cpu_state.json")
    prev_high = False
    if os.path.exists(CPU_ST):
        try:
            with open(CPU_ST) as f: prev_high = json.load(f).get("high", False)
        except Exception: prev_high = False
    cur_high = cpu > CPU_WARN
    with open(CPU_ST, "w") as f: json.dump({"high": cur_high}, f)
    sustained = cur_high and prev_high
    r2 = subprocess.run(["free","-m"], capture_output=True, text=True, timeout=5)
    mem = 0
    lines = r2.stdout.strip().split("\n")
    if len(lines) >= 2:
        p = lines[1].split()
        mem = (float(p[1])-float(p[6] if len(p)>6 else p[3]))/float(p[1])*100
    load = os.getloadavg()[0]
    log(f" ✅ CPU {cpu:.0f}% | 内存 {mem:.0f}% | 负载 {load:.1f}")
    if sustained: alerts.append(("CPU过高", f"{cpu:.0f}% (连续2次确认)"))
    if mem > MEM_WARN: alerts.append(("内存过高", f"{mem:.0f}%"))
except Exception as e:
    log(f" CPU检测异常: {e}")

# 4. Disk
try:
    r = subprocess.run(["df","-h","/"], capture_output=True, text=True, timeout=5)
    p = r.stdout.strip().split("\n")[1].split()
    disk = int(p[4].replace("%",""))
    log(f" {'⚠️' if disk>90 else '✅'} 磁盘 {disk}%")
    if disk > DISK_WARN: alerts.append(("磁盘将满", f"{disk}%"))
except: pass

# 5. MCP Connectivity
log(" 🔌 MCP连通性检测...")
mcps = get_mcp_configs()
known_http = {"flomo", "ticktick"}

for name, info in mcps.items():
    if name == "bridge": continue
    if not info.get("enabled", True):
        log(f" ⏭️  {name}: 已禁用"); continue
    url, auth, cmd = info.get("url",""), info.get("auth",""), info.get("cmd","")

    if url and name in known_http:
        ok, detail = check_http(url, auth)
    elif cmd and ("bash " in cmd or "python3 " in cmd):
        sp = cmd.strip().split()[1] if len(cmd.strip().split())>1 else ""
        sp = sp.replace("~", HOME)
        ok = os.path.exists(sp)
        detail = "文件存在" if ok else f"文件缺失"
    elif cmd and "npx" in cmd:
        rx = subprocess.run(["which","npx"], capture_output=True, text=True, timeout=3)
        ok = bool(rx.stdout.strip())
        detail = "npx可用" if ok else "npx不存在"
    else:
        log(f" ⏭️  {name}: 跳过(非HTTP/无url)"); continue

    log(f" {'✅' if ok else '❌'} {name}: {detail}")
    if not ok:
        # ── 自愈：Flomo 401 自动重载 token ──
        if name == "flomo" and "401" in detail:
            log(f" 🔧 尝试自愈 flomo 401...")
            try:
                env_file = os.path.join(HOME, ".hermes/.env")
                new_token = None
                if os.path.exists(env_file):
                    with open(env_file) as ef:
                        for line in ef:
                            if "MCP_FLOMO_API_KEY" in line and "=" in line:
                                new_token = line.split("=", 1)[1].strip()
                                break
                if new_token:
                    subprocess.run([
                        "hermes", "config", "set",
                        "mcp_servers.flomo.headers.Authorization",
                        f"Bearer {new_token}"
                    ], capture_output=True, text=True, timeout=10)
                    log(f" 🔧 已更新 flomo token")
                    # 重试
                    ok2, detail2 = check_http(url, new_token)
                    if ok2:
                        ok = True
                        detail = f"自愈成功: {detail2}"
                        log(f" ✅ flomo 自愈成功")
                    else:
                        detail = f"401+自愈失败: {detail2}"
                        log(f" ❌ flomo 自愈失败: {detail2}")
                else:
                    log(f" ❌ .env中未找到MCP_FLOMO_API_KEY")
            except Exception as he:
                log(f" ❌ 自愈异常: {he}")
        k = f"mcp_{name}"
        if not ok:
            if should(k): alerts.append((f"{name} MCP异常", detail)); mark(k)

for title, detail in alerts:
    k = title[:20]
    if should(k): alert(title, detail); mark(k)

# 6. 守望狗完整性校验（交叉验证）
HASH_FILE = "/etc/hermes_watchdog_hashes"
try:
    if not os.path.exists(HASH_FILE):
        log(" ⚠️ 哈希基准缺失: /root/.hermes_watchdog_hashes")
    else:
        with open(HASH_FILE) as f:
            expected = {}
            for line in f:
                line = line.strip()
                if ":" in line:
                    name, hash_val = line.split(":", 1)
                    expected[name] = hash_val
        tampered = []
        for name, expected_hash in expected.items():
            fpath = f"/home/ubuntu/.hermes/scripts/{name}" if name.endswith(".py") else f"/home/ubuntu/.hermes/state/{name}"
            try:
                result = subprocess.run(["sha256sum", fpath], capture_output=True, text=True, timeout=5)
                actual_hash = result.stdout.split()[0] if result.stdout else "MISSING"
                if actual_hash != expected_hash:
                    tampered.append(f"{name}: 被篡改")
                    log(f" ❌ {name} 哈希不匹配!")
                else:
                    log(f" ✅ {name}: 完整性正常")
            except Exception:
                tampered.append(f"{name}: 文件丢失")
                log(f" ❌ {name}: 文件不存在")
        if tampered:
            k = "watchdog_integrity"
            if should(k):
                alert("守望狗完整性异常", "、".join(tampered))
                mark(k)
        # ── 自愈：全绿时自动刷新哈希基线 ──
        elif not alerts and expected:  # 有基线但无不匹配 → 正常变更
            now_ts = time.time()
            all_old = True
            for name in expected:
                fpath = f"/home/ubuntu/.hermes/scripts/{name}" if name.endswith(".py") else f"/home/ubuntu/.hermes/state/{name}"
                try:
                    mtime = os.path.getmtime(fpath)
                    if now_ts - mtime < 600:  # 10分钟内改过的不纳入
                        all_old = False
                        break
                except OSError:
                    pass
            if all_old:
                try:
                    files = []
                    for name in expected:
                        fpath = f"/home/ubuntu/.hermes/scripts/{name}" if name.endswith(".py") else f"/home/ubuntu/.hermes/state/{name}"
                        r = subprocess.run(["sha256sum", fpath], capture_output=True, text=True, timeout=5)
                        if r.stdout:
                            files.append(f"{r.stdout.split()[0]}  {fpath}")
                    if files:
                        with open(HASH_FILE, "w") as hf:
                            hf.write("\n".join(files) + "\n")
                        log(" 🔄 哈希基线已自动刷新")
                except Exception:
                    pass
except Exception as e:
    log(f" ⚠️ 完整性校验异常: {e}")

log(" ✅ 全部正常" if not alerts else f" ⚠️ 共{len(alerts)}个告警")
sys.exit(0)
