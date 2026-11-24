#!/usr/bin/env python3
"""千问 Token Plan 个人版官方额度: AK/SK→GenerateCLIAccessToken→控制台网关 usage (2026-11-24 实测通)"""
import os,sys,json,urllib.request,urllib.parse,datetime
env_file=os.environ.get('ENV_FILE') or os.path.expanduser('~/.hermes/profiles/chat/.env')  # 或直接 export 两个 ALIBABA_CLOUD_* 变量
for ln in (open(env_file) if os.path.exists(env_file) else []):
    ln=ln.strip()
    if '=' in ln and not ln.startswith('#'):
        k,v=ln.split('=',1); os.environ.setdefault(k,v)
from alibabacloud_tea_openapi import models as m
from alibabacloud_tea_openapi.client import Client
from alibabacloud_tea_util import models as u
cfg=m.Config(access_key_id=os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],access_key_secret=os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'],endpoint='modelstudio.cn-beijing.aliyuncs.com')
tok=Client(cfg).call_api(m.Params(action='GenerateCLIAccessToken',version='2026-02-10',protocol='HTTPS',method='POST',pathname='/modelstudio/cli/generateAccessToken',style='ROA',req_body_type='json',body_type='json'),m.OpenApiRequest(query={}),u.RuntimeOptions())['body']['cliAccessToken']
params={"Api":"zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage","V":"1.0","Data":{"cornerstoneParam":{"protocol":"V2","console":"ONE_CONSOLE","productCode":"p_efm","switchUserType":3,"consoleSite":"BAILIAN_ALIYUN"}}}
url="https://bailian-cs.console.aliyun.com/cli/api.json?"+urllib.parse.urlencode({"action":"BroadScopeAspnGateway","product":"sfm_bailian","api":"zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage"})
req=urllib.request.Request(url,data=urllib.parse.urlencode({"params":json.dumps(params),"region":"cn-beijing"}).encode(),headers={"Authorization":"Bea"+"rer "+tok,"Content-Type":"application/x-www-form-urlencoded"})
d=json.loads(urllib.request.urlopen(req,timeout=20).read())['data']
if not d.get('success'): print("ERR",d.get('errorCode'),d.get('errorMsg')); sys.exit(1)
x=d['DataV2']['data']['data']
total=float(os.environ.get('TOKENPLAN_MONTHLY_CREDITS','80000'))
used_pct=x.get('per1MonthPercentage'); rem_pct=1-used_pct
if '--json' in sys.argv: print(json.dumps(x,ensure_ascii=False)); sys.exit(0)
if rem_pct is None: print(json.dumps(x,ensure_ascii=False)); sys.exit(0)
reset=datetime.datetime.fromtimestamp(x['per1MonthResetTime']/1000).strftime('%m-%d %H:%M')
print(f"本期剩余 {rem_pct*100:.1f}%  ≈ {rem_pct*total:,.0f} / {total:,.0f} Credits (档位按 {total:,.0f} 折算)")
print(f"已用 ≈ {(1-rem_pct)*total:,.0f} Credits | 重置 {reset}")
