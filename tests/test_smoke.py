#!/usr/bin/env python3
"""基础冒烟测试 — 不依赖真实账号/服务，python3 tests/test_smoke.py 即可运行。
覆盖外审指出的四类问题：启动错误（HOME 未定义）、告警退出状态检查、
证书校验拒绝静默降级、明文回退通道默认禁用。
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_health_monitor_imports_and_defines_home():
    """启动错误回归：模块 import 时不得 NameError（HOME 必须先于使用定义）。"""
    hm = load("vps-health-monitor")
    assert isinstance(hm.HOME, str) and hm.HOME
    assert ".hermes/hermes-agent/venv/bin" in os.environ["PATH"]
    print("PASS: vps-health-monitor imports cleanly, HOME defined before use")

def test_alert_failure_is_logged_not_silent():
    """告警退出状态检查：hermes send 失败时日志必须记录失败，不得误报成功。"""
    hm = load("vps-health-monitor")
    hm.WD = Path(tempfile.mkdtemp())
    hm.HL = hm.WD / "health.log"
    hm.AH = hm.WD / "alerted.json"
    fake = Path(tempfile.mkdtemp()) / "hermes"
    fake.write_text("#!/bin/sh\nexit 3\n")  # 模拟发送失败
    fake.chmod(0o755)
    with mock.patch.dict(os.environ, {"PATH": f"{fake.parent}:{os.environ['PATH']}"}):
        hm.alert("测试标题", "测试详情")
    log = (hm.WD / "health.log").read_text()
    assert "推送失败" in log, f"失败未记录进日志: {log}"
    assert "已推送" not in log, "失败被误报为成功！"
    print("PASS: alert() logs failure with exit code, no false success")

def test_alert_success_logged():
    hm = load("vps-health-monitor")
    hm.WD = Path(tempfile.mkdtemp())
    hm.HL = hm.WD / "health.log"
    hm.AH = hm.WD / "alerted.json"
    fake = Path(tempfile.mkdtemp()) / "hermes"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    with mock.patch.dict(os.environ, {"PATH": f"{fake.parent}:{os.environ['PATH']}"}):
        hm.alert("测试标题", "测试详情")
    log = (hm.WD / "health.log").read_text()
    assert "已推送" in log
    print("PASS: alert() logs success on exit 0")

def test_bridge_send_refuses_unverified_tls_by_default():
    """桥接安全：找不到服务端 CA 时必须显式报错，不得静默关闭校验。"""
    bs = load("bridge-send")
    tmp = tempfile.mkdtemp()
    bs.CERT_DIR = Path(tmp)  # 空目录，无 CA
    try:
        bs.build_context()
        raise AssertionError("build_context should have refused without CA")
    except SystemExit as e:
        assert "CA certificate" in str(e)
    print("PASS: bridge-send refuses to connect without server CA (no silent CERT_NONE)")


def test_bridge_send_builds_verified_context_with_ca():
    import ssl
    bs = load("bridge-send")
    tmp = Path(tempfile.mkdtemp())
    bs.CERT_DIR = tmp
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(tmp / "vps-client-key.pem"),
                    "-out", str(tmp / "ca.pem"),
                    "-days", "1", "-subj", "/CN=test-ca"],
                   check=True, capture_output=True)
    (tmp / "vps-client.pem").write_bytes((tmp / "ca.pem").read_bytes())
    ctx = bs.build_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    print("PASS: bridge-send builds mTLS context with server verification enabled")


def test_bridge_send_plaintext_fallback_disabled_by_default():
    """明文回退默认禁用（安全开关默认方向必须关），显式 opt-in 才放行。"""
    import http.client as hc
    bs = load("bridge-send")
    # 场景1：有 CA 但 HTTPS 连接失败 → 默认禁止明文回退，门禁拦截
    ca_dir = Path(tempfile.mkdtemp())
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(ca_dir / "vps-client-key.pem"),
                    "-out", str(ca_dir / "ca.pem"),
                    "-days", "1", "-subj", "/CN=test-ca"],
                   check=True, capture_output=True)
    (ca_dir / "vps-client.pem").write_bytes((ca_dir / "ca.pem").read_bytes())
    bs.CERT_DIR = ca_dir
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("BRIDGE_ALLOW_PLAINTEXT", None)
        with mock.patch.object(hc, "HTTPSConnection", side_effect=OSError("no network in test")):
            try:
                bs.call({"jsonrpc": "2.0", "id": 1, "method": "x", "params": {}})
                raise AssertionError("should have refused without BRIDGE_ALLOW_PLAINTEXT=1")
            except SystemExit as e:
                msg = str(e)
                assert ("BRIDGE_ALLOW_PLAINTEXT" in msg) or ("CA certificate" in msg), f"unexpected exit: {msg}"
    print("PASS: plaintext fallback disabled by default, explicit opt-in required")

def test_bridge_send_plaintext_fallback_opt_in_allowed():
    """显式 opt-in 后，门禁放行（后续真实连接失败与本测试无关）。"""
    import http.client as hc
    bs = load("bridge-send")
    ca_dir = Path(tempfile.mkdtemp())
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(ca_dir / "vps-client-key.pem"),
                    "-out", str(ca_dir / "ca.pem"),
                    "-days", "1", "-subj", "/CN=test-ca"],
                   check=True, capture_output=True)
    (ca_dir / "vps-client.pem").write_bytes((ca_dir / "ca.pem").read_bytes())
    bs.CERT_DIR = ca_dir
    with mock.patch.dict(os.environ, {"BRIDGE_ALLOW_PLAINTEXT": "1"}, clear=False):
        with mock.patch.object(hc, "HTTPSConnection", side_effect=OSError("no network in test")):
            try:
                bs.call({"jsonrpc": "2.0", "id": 1, "method": "x", "params": {}})
            except SystemExit as e:
                raise AssertionError(f"opt-in should have passed the gate: {e}")
            except Exception:
                pass  # 真实连接失败（无服务监听）是预期，门禁已放行
    print("PASS: plaintext fallback disabled by default, explicit opt-in required")

if __name__ == "__main__":
    fails = 0
    for fn in [test_health_monitor_imports_and_defines_home,
               test_alert_failure_is_logged_not_silent,
               test_alert_success_logged,
               test_bridge_send_refuses_unverified_tls_by_default,
               test_bridge_send_builds_verified_context_with_ca,
               test_bridge_send_plaintext_fallback_disabled_by_default]:
        try:
            fn()
        except Exception as e:
            fails += 1
            print(f"FAIL: {fn.__name__}: {e}")
    print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILED'}")
    sys.exit(1 if fails else 0)
