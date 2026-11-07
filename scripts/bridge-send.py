#!/usr/bin/env python3
"""Bridge JSON-RPC 助手：从运行中的 bridge 进程环境读 token，不硬编码。
用法:
  bridge-send.py status                          # 查桥接状态
  bridge-send.py send <to> <type> <content>      # to=server|mac, type=message|task
  bridge-send.py receive <target>                # target=server|mac, 不 ack
  bridge-send.py raw <method> <params_json>      # 原始 JSON-RPC

通道与安全说明:
  通道1: https://127.0.0.1:9090 — mTLS（客户端证书 + 服务端证书校验）。
         服务端 CA 证书从 ~/.hermes/bridge-certs/ 下自动探测（ca.pem / server-ca.pem / root-ca.pem）。
         仅限本机回环地址 + Bearer token 双重验证。
  通道2: http://127.0.0.1:80/bridge-api/ — nginx 反代回退通道，仅本机回环 + Bearer token。
         ⚠️ 默认禁用（明文回退是安全下坡）；确需开启时设 BRIDGE_ALLOW_PLAINTEXT=1。
  安全取舍：两条通道都绑定 127.0.0.1 回环，不出本机；证书全部自签。
"""
import ssl, json, sys, os, http.client
from pathlib import Path
HERMES_DIR = Path.home() / ".hermes"
CERT_DIR = HERMES_DIR / "bridge-certs"
CA_CANDIDATES = ["ca.pem", "server-ca.pem", "root-ca.pem"]

def get_token():
    import glob
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

def build_context():
    """mTLS context：客户端证书 + 服务端证书校验。找不到 CA 时显式报错而非静默降级。"""
    ctx = ssl.create_default_context(cafile=None)
    ca = None
    for name in CA_CANDIDATES:
        cand = CERT_DIR / name
        if cand.is_file():
            ca = cand
            break
    if ca is None:
        raise SystemExit(
            f"ERROR: no server CA certificate found in {CERT_DIR} "
            f"(looked for: {', '.join(CA_CANDIDATES)}). "
            "Refusing to connect without server verification. "
            "Set BRIDGE_INSECURE=1 only if you understand the risk.")
    ctx.load_verify_locations(cafile=str(ca))
    cert = CERT_DIR / "vps-client.pem"
    key = CERT_DIR / "vps-client-key.pem"
    ctx.load_cert_chain(str(cert), str(key))
    return ctx

def call(payload):
    token = get_token()
    # 通道1: mTLS HTTPS 直连（服务端证书校验开启）
    try:
        if os.environ.get("BRIDGE_INSECURE") == "1":
            # 显式 opt-out：仅本机回环 + token，跳过服务端校验
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.load_cert_chain(str(CERT_DIR / "vps-client.pem"), str(CERT_DIR / "vps-client-key.pem"))
        else:
            ctx = build_context()
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
    except SystemExit:
        raise
    except Exception as e:
        sys.stderr.write(f"[https-9090 error: {e}] trying nginx fallback\n")
    # 通道2: nginx 反代回退（本机回环 + Bearer token）。默认禁用：安全开关默认方向必须关。
    if os.environ.get("BRIDGE_ALLOW_PLAINTEXT") != "1":
        raise SystemExit(
            "ERROR: HTTPS channel failed. Plaintext fallback is disabled by default; "
            "set BRIDGE_ALLOW_PLAINTEXT=1 to allow the nginx HTTP fallback.")
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
