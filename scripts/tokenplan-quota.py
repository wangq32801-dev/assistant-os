#!/usr/bin/env python3
"""千问 Token Plan 个人版官方额度（纯标准库，2026-11-24 实测通）
链路：RAM 子账号 AK/SK(ACS3-HMAC-SHA256) → GenerateCLIAccessToken → 控制台网关 usage
用法：tokenplan_quota.py [--json]
凭证：环境变量 ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET
     （未设则读 ENV_FILE，默认 ~/.hermes/profiles/chat/.env）
返回字段 per1MonthPercentage = 本期【已用】比例（不是剩余）。"""
import os, sys, json, hashlib, hmac, datetime, urllib.request, urllib.parse

ENV = os.environ.get("ENV_FILE") or os.path.expanduser("~/.hermes/profiles/chat/.env")
if os.path.exists(ENV):
    for ln in open(ENV):
        ln = ln.strip()
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            os.environ.setdefault(k, v)
AK = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID")
SK = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
if not AK or not SK:
    print("缺少 ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET", file=sys.stderr)
    sys.exit(2)


def acs3(method, host, path, action, version):
    """阿里云 OpenAPI ACS3-HMAC-SHA256 签名（空 body）→ 请求头 dict。
    坑：规范串里「请求头段」与「SignedHeaders」之间要多一个空行，官方文档示例没写，少了必报 signature mismatch。"""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = now.replace("-", "").replace(":", "") + hmac.new(SK.encode(), now.encode(), hashlib.sha256).hexdigest()[:16]
    sha = hashlib.sha256(b"").hexdigest()
    hdr = {"host": host, "x-acs-action": action, "x-acs-version": version,
           "x-acs-date": now, "x-acs-signature-nonce": nonce, "x-acs-content-sha256": sha}
    signed = sorted(hdr)
    canon = (method + "\n" + path + "\n\n" + "".join(k + ":" + hdr[k] + "\n" for k in signed)
             + "\n" + ";".join(signed) + "\n" + sha)
    sts = "ACS3-HMAC-SHA256\n" + hashlib.sha256(canon.encode()).hexdigest()
    sig = hmac.new(SK.encode(), sts.encode(), hashlib.sha256).hexdigest()
    hdr["Authorization"] = "ACS3-HMAC-SHA256 Credential=%s,SignedHeaders=%s,Signature=%s" % (
        AK, ";".join(signed), sig)
    return hdr


def _post(url, headers, body):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=20).read().decode()


def fetch():
    hdr = acs3("POST", "modelstudio.cn-beijing.aliyuncs.com", "/modelstudio/cli/generateAccessToken",
               "GenerateCLIAccessToken", "2026-02-10")
    hdr["content-type"] = "application/json"
    tok = json.loads(_post("https://modelstudio.cn-beijing.aliyuncs.com/modelstudio/cli/generateAccessToken",
                           hdr, b""))["cliAccessToken"]
    params = {"Api": "zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage", "V": "1.0",
              "Data": {"cornerstoneParam": {"protocol": "V2", "console": "ONE_CONSOLE", "productCode": "p_efm",
                                            "switchUserType": 3, "consoleSite": "BAILIAN_ALIYUN"}}}
    url = "https://bailian-cs.console.aliyun.com/cli/api.json?" + urllib.parse.urlencode({
        "action": "BroadScopeAspnGateway", "product": "sfm_bailian",
        "api": "zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage"})
    body = urllib.parse.urlencode({"params": json.dumps(params), "region": "cn-beijing"}).encode()
    d = json.loads(_post(url, {"Authorization": "Bea" + "rer " + tok,
                               "Content-Type": "application/x-www-form-urlencoded"}, body))["data"]
    if not d.get("success"):
        raise RuntimeError("%s %s" % (d.get("errorCode"), d.get("errorMsg")))
    return d["DataV2"]["data"]["data"]


if __name__ == "__main__":
    try:
        x = fetch()
    except Exception as e:
        print("ERR %s: %s" % (type(e).__name__, str(e)[:300]), file=sys.stderr)
        sys.exit(1)
    if "--json" in sys.argv:
        print(json.dumps(x, ensure_ascii=False))
        sys.exit(0)
    used_pct = x.get("per1MonthPercentage")
    if used_pct is None:
        print(json.dumps(x, ensure_ascii=False))
        sys.exit(0)
    total = float(os.environ.get("TOKENPLAN_MONTHLY_CREDITS", "80000"))
    used = used_pct * total
    reset = datetime.datetime.fromtimestamp(x["per1MonthResetTime"] / 1000).strftime("%m-%d %H:%M")
    print("本期已用 {:.1f}%  ≈ {:,.0f} / {:,.0f} Credits（档位按 {:,.0f} 折算）".format(
        used_pct * 100, used, total, total))
    print("剩余 ≈ {:,.0f} Credits | 重置 {}".format(total - used, reset))
