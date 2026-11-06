#!/usr/bin/env python3
"""Bridge JSON-RPC 助手：从运行中的 bridge 进程环境读 token，不硬编码。
用法:
  bridge-send.py status                          # 查桥接状态
  bridge-send.py send <to> <type> <content>      # to=server|mac, type=message|task
  bridge-send.py receive <target>                # target=server|mac, 不 ack
  bridge-send.py raw <method> <params_json>      # 原始 JSON-RPC
通道: https://127.0.0.1:9090 (mTLS 客户端证书) -> 失败自动回退 http://localhost:80/bridge-api/
"""
import ssl, json, sys, http.client
from pathlib import Path
HERMES_DIR = Path.home() / ".hermes"

def get_token():
    import glob, os
    for environ in glob.glob("/proc/[0-9]*/environ"):
        try:
            with open(environ, "rb") as f:
                env = f.read().decode("utf-8", "ignore").split("\0")
            for kv in env:
                if kv.startswith("BRIDGE_TOKEN=") and kv != "BRIDGE_TOKEN=hermes-bridge-default-token":
                    return kv.split("=", 1)[1]
        except (IOError, OSError):
            continue
    raise SystemExit("ERROR: no BRIDGE_TOKEN found in any process env")

def call(payload):
    token = get_token()
    # 通道1: mTLS HTTPS 直连
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        cert = HERMES_DIR / "bridge-certs" / "vps-client.pem"
        key = HERMES_DIR / "bridge-certs" / "vps-client-key.pem"
        ctx.load_cert_chain(str(cert), str(key))
        conn = http.client.HTTPSConnection("127.0.0.1", 9090, context=ctx, timeout=10)
        conn.request("POST", "/", body=json.dumps(payload), headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        resp = conn.getresponse()
        out = resp.read().decode()
        conn.close()
        if resp.status == 200:
            print(out)
            return
        sys.stderr.write(f"[https-9090 failed: {resp.status}] trying nginx fallback\n")
    except Exception as e:
        sys.stderr.write(f"[https-9090 error: {e}] trying nginx fallback\n")
    # 通道2: nginx 反代
    conn = http.client.HTTPConnection("127.0.0.1", 80, timeout=10)
    conn.request("POST", "/bridge-api/", body=json.dumps(payload), headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    resp = conn.getresponse()
    print(resp.read().decode())
    conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "status":
        call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "bridge_status", "arguments": {}}})
    elif cmd == "send" and len(sys.argv) >= 5:
        to, typ, content = sys.argv[2], sys.argv[3], sys.argv[4]
        prio = int(sys.argv[5]) if len(sys.argv) > 5 else 1
        call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "bridge_send", "arguments":
                         {"to": to, "type": typ, "content": content, "priority": prio}}})
    elif cmd == "receive" and len(sys.argv) >= 3:
        call({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "bridge_receive", "arguments": {"target": sys.argv[2], "ack": False}}})
    elif cmd == "raw" and len(sys.argv) >= 4:
        call({"jsonrpc": "2.0", "id": 1, "method": sys.argv[2], "params": json.loads(sys.argv[3])})
    else:
        print(__doc__); sys.exit(1)
