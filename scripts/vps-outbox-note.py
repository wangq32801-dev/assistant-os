#!/usr/bin/env python3
"""
vps-outbox-note.py — VPS 每日任务产出 → Obsidian 全端的统一写入入口（2026-09-05 建）

用法:
    python3 ~/.hermes/scripts/vps-outbox-note.py <vault内路径> <内容文件或->
    例: python3 ~/.hermes/scripts/vps-outbox-note.py "04-VPS端/日报/AI日报-2026-09-05.md" - < 日报.md
    内容含图片时自行按 LiveSync children 拆块（本脚本单块≤7MB 足够日更文本）

链路: obsidian-outbox（本脚本写）→ CouchDB 持续复制 → obsidian-livesync 主库 → Mac/iPhone
安全: outbox-writer 账号物理上只能写 outbox 库, 主库 forbidden; 无法删除/修改已有笔记
注意: 同路径重复投递 = 覆盖同名文档（rev 推进, 内容以最后一次为准）→ 文件名带日期可避免
"""
import sys, os, json, time, random, string, base64, urllib.request

CREDS = os.path.expanduser("~/.outbox-credentials")

def load_creds():
    c = {}
    for line in open(CREDS):
        k, _, v = line.strip().partition("=")
        c[k] = v
    return c

def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr); sys.exit(2)
    path = sys.argv[1].lstrip("/")
    content = sys.stdin.read() if sys.argv[2] == "-" else open(sys.argv[2]).read()
    if not content.strip():
        print("empty content, abort", file=sys.stderr); sys.exit(1)

    c = load_creds()
    auth = base64.b64encode(f"{c['COUCHDB_USER']}:{c['COUCHDB_PASS']}".encode()).decode()

    # 大内容拆块（每块 200KB, 模仿 LiveSync 多 children 结构）
    chunks = [content[i:i+204800] for i in range(0, len(content), 204800)] or [content]
    block_ids, docs = [], []
    for ch in chunks:
        bid = "h:" + "".join(random.choices(string.ascii_lowercase + string.digits, k=11))
        block_ids.append(bid)
        docs.append({"_id": bid, "type": "leaf", "data": ch})
    now_ms = int(time.time() * 1000)
    docs.append({
        "_id": path, "path": path, "ctime": now_ms, "mtime": now_ms,
        "size": len(content.encode("utf-8")), "type": "plain",
        "children": block_ids, "deleted": False, "eden": {},
    })

    req = urllib.request.Request(c["COUCHDB_URL"] + "/_bulk_docs",
        data=json.dumps({"docs": docs}).encode(),
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"},
        method="POST")
    resp = json.load(urllib.request.urlopen(req, timeout=20))
    ok = all(r.get("ok") for r in resp)
    for r in resp:
        print(("✅" if r.get("ok") else "❌"), r.get("id"), r.get("rev", r.get("reason","")))
    print(f"{'DONE' if ok else 'FAILED'}: {path} ({len(content)} bytes, {len(chunks)} chunk(s))")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
