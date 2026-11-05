# -*- coding: utf-8 -*-
"""Creem 服务端接线测试（离线：本地 stub Creem 服务 + 内存存储 + 真实 HTTP Handler）。

覆盖：
1. webhook 验签（正/负例、篡改、缺 secret → 503、超 16KB 的 webhook 体仍被接受）
2. checkout.completed → 落单 + 激活码镜像入表 + 重复投递幂等
3. 事件里没有激活码时也能正确处理（购买者之后靠 Creem 现查激活）
4. refund.created → 订单置 refunded + 激活码置 revoked + 幂等
5. 激活双轨：本地码走原逻辑；Creem 码校验+激活+镜像+绑设备；状态异常给出对应文案
6. **回归**：未配置 Creem 时，行为与改动前完全一致（"激活码无效" / 503）
7. Handler 级：/webhook/creem 原始字节验签、/creem/checkout 需鉴权
"""

import hashlib
import hmac
import json
import sys
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from creem_client import CreemClient  # noqa: E402
import importlib  # noqa: E402

# 服务端代码在仓库里有两份近乎孪生的文件：
#   remote_trial_register_service.py —— 开发/测试用（本套件的原始目标）
#   trial_register_service.py        —— **线上 systemd 实际执行的入口**（部署时上传这份）
# 二者曾出现过认证链/校验逻辑漂移，故整个套件按模块参数化，Creem 改动必须对两份都成立。
SERVER_MODULES = ("remote_trial_register_service", "trial_register_service")

TrialRegisterService = None  # 由下方 server_module fixture 注入
Handler = None


@pytest.fixture(params=SERVER_MODULES, autouse=True)
def server_module(request, monkeypatch):
    module = importlib.import_module(request.param)
    monkeypatch.setattr(sys.modules[__name__], "TrialRegisterService", module.TrialRegisterService)
    monkeypatch.setattr(sys.modules[__name__], "Handler", module.Handler)
    return module
from trial_db_backends import BaseTrialStore, utc_now_iso  # noqa: E402

API_KEY = "creem_test_service_key"
WEBHOOK_SECRET = "whsec_service_test"
PRODUCT_ID = "prod_service_test"
DEVICE_ID = "device1234567890abcdef"
REQUEST_ID = "req1234567890abcdef"
HW_HASH = "a" * 32
CREEM_LICENSE_KEY = "CREEM-ABCD-1234-WXYZ"


# ------------------------------------------------------------------ 内存存储
class MemoryStore(BaseTrialStore):
    """与 MySqlTrialStore 语义一致的测试替身（幂等语义必须相同）。"""

    def __init__(self):
        self.trials, self.licenses, self.orders = {}, {}, {}

    def backend_name(self):
        return "memory"

    def is_configured(self):
        return True

    def get_device_trial(self, device_id):
        return self.trials.get(device_id)

    def register_device_trial(self, device_id, client_ip="", hw_hash=""):
        row = {"id": str(uuid.uuid4()), "device_id": device_id, "trial_start_date": utc_now_iso(),
               "client_ip": client_ip, "hw_hash": hw_hash, "created_at": utc_now_iso()}
        self.trials[device_id] = row
        return dict(row)

    def count_recent_registrations_by_ip(self, client_ip, hours=24):
        return 0

    def count_recent_registrations_by_hw_hash(self, hw_hash, hours=24):
        return 0

    def find_existing_by_hw_hash(self, hw_hash):
        return None

    def list_trials(self, limit=50):
        return list(self.trials.values())[:limit]

    def delete_trial(self, device_id):
        return self.trials.pop(device_id, None) is not None

    def get_license_by_key(self, license_key):
        row = self.licenses.get(license_key)
        return dict(row) if row else None

    def create_license(self, license_key, plan_type, status="active", expires_at=None):
        if license_key in self.licenses:
            return dict(self.licenses[license_key])
        row = {"id": str(uuid.uuid4()), "license_key": license_key, "plan_type": plan_type,
               "status": status, "device_id": None, "activated_at": None,
               "expires_at": expires_at, "created_at": utc_now_iso()}
        self.licenses[license_key] = row
        return dict(row)

    def bind_license_device(self, license_id, device_id):
        for row in self.licenses.values():
            if row["id"] == license_id and not row.get("device_id"):
                row["device_id"] = device_id
                row["activated_at"] = utc_now_iso()
                return True
        return False

    def list_licenses(self, limit=50):
        return list(self.licenses.values())[:limit]

    def delete_license(self, license_key):
        return self.licenses.pop(license_key, None) is not None

    # -------- Creem 订单 --------
    def insert_creem_order(self, record):
        dedupe = (record or {}).get("dedupe_key")
        if not dedupe:
            raise ValueError("dedupe_key required")
        if dedupe in self.orders:
            return False
        self.orders[dedupe] = dict(record)
        return True

    def get_creem_order_by_dedupe_key(self, dedupe_key):
        row = self.orders.get(dedupe_key)
        return dict(row) if row else None

    def get_creem_order_by_license_key(self, license_key):
        for row in self.orders.values():
            if row.get("license_key") == license_key:
                return dict(row)
        return None

    def list_creem_orders(self, limit=50):
        return list(self.orders.values())[:limit]

    def mark_creem_order_refunded(self, dedupe_key="", order_id="", license_key=""):
        hit = False
        for row in self.orders.values():
            if ((dedupe_key and row.get("dedupe_key") == dedupe_key)
                    or (order_id and row.get("order_id") == order_id)
                    or (license_key and row.get("license_key") == license_key)):
                if row.get("status") != "refunded":
                    row["status"] = "refunded"
                    row["refunded_at"] = utc_now_iso()
                    hit = True
        return hit

    def update_license_status(self, license_key, status):
        row = self.licenses.get(license_key)
        if not row or row.get("status") == status:
            return False
        row["status"] = status
        return True


# ------------------------------------------------------------- Creem stub 服务
class StubCreem:
    """最小 Creem API 替身：/checkouts、/licenses/validate、/licenses/activate。"""

    def __init__(self):
        self.checkouts = []
        self.activations = []
        self.license_status = "active"
        self.activation_error_status = 0  # 非 0 → activate 返回该 HTTP 状态
        self.ua = None
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self._srv.server_address[1]}/v1"

    def stop(self):
        self._srv.shutdown()

    def _handler(inner):
        stub = inner

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                stub.ua = self.headers.get("User-Agent")
                path = self.path
                if path == "/v1/checkouts":
                    stub.checkouts.append(body)
                    self._reply(200, {"id": "ch_stub", "status": "pending",
                                      "checkout_url": "https://creem.io/test/checkout/ch_stub"})
                elif path == "/v1/licenses/validate":
                    self._reply(200, {"status": stub.license_status, "expires_at": None})
                elif path == "/v1/licenses/activate":
                    stub.activations.append(body)
                    if stub.activation_error_status:
                        self._reply(stub.activation_error_status, {"message": "activation limit reached"})
                    else:
                        self._reply(200, {"status": "active", "activation": 1,
                                          "activation_limit": None, "expires_at": None})
                else:
                    self._reply(404, {"message": "not found"})

        return H


@pytest.fixture
def stub():
    s = StubCreem()
    yield s
    s.stop()


@pytest.fixture
def store():
    return MemoryStore()


def _make_service(store, stub, monkeypatch, *, with_creem=True):
    if with_creem:
        monkeypatch.setenv("CREEM_API_KEY", API_KEY)
        monkeypatch.setenv("CREEM_API_BASE", stub.base_url)
        monkeypatch.setenv("CREEM_PRODUCT_ID", PRODUCT_ID)
        monkeypatch.setenv("CREEM_WEBHOOK_SECRET", WEBHOOK_SECRET)
    else:
        for name in ("CREEM_API_KEY", "CREEM_API_BASE", "CREEM_PRODUCT_ID", "CREEM_WEBHOOK_SECRET"):
            monkeypatch.delenv(name, raising=False)
    return TrialRegisterService(db=store, api_key="client-key", admin_key="admin-key",
                                allowed_app="chaos_downloader", signing_key="", trial_days=15)


@pytest.fixture
def service(store, stub, monkeypatch):
    return _make_service(store, stub, monkeypatch)


# ------------------------------------------------------------------ 事件构造
def _license_event(event_type):
    return {
        "eventType": event_type,
        "object": {
            "id": "ch_1",
            "status": "completed" if event_type == "checkout.completed" else "refunded",
            "order": {"id": "ord_1", "amount": 1990, "currency": "USD", "checkout_id": "ch_1"},
            "product": {"id": PRODUCT_ID, "name": "SourceCraft"},
            "customer": {"id": "cust_1", "email": "buyer@example.com", "country": "US"},
            "metadata": {"device_id": DEVICE_ID, "hw_hash": HW_HASH, "client_request_id": REQUEST_ID},
            "license": {"key": CREEM_LICENSE_KEY, "status": "active"},
        },
    }


def _sign(raw: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _webhook(service, event, *, secret=WEBHOOK_SECRET, sig=None, event_override=None):
    raw = json.dumps(event_override if event_override is not None else event).encode()
    return service.creem_webhook(raw_body=raw, signature=sig if sig is not None else _sign(raw, secret),
                                 client_ip="1.2.3.4")


def _activate_payload(license_key, device=DEVICE_ID):
    return {"device_id": device, "app": "chaos_downloader", "request_id": REQUEST_ID,
            "hw_hash": HW_HASH, "license_key": license_key}


# =========================================================== 1. webhook 验签
class TestWebhookSignature:
    def test_valid_signature_accepted(self, service):
        status, data = _webhook(service, _license_event("checkout.completed"))
        assert status == 200 and data["ok"] is True

    def test_tampered_body_rejected(self, service):
        raw = json.dumps(_license_event("checkout.completed")).encode()
        status, data = _webhook(service, None, sig=_sign(raw), event_override=json.loads(raw))
        assert status == 200  # 原样发送应通过（对照组）
        status, data = service.creem_webhook(raw_body=raw + b" ", signature=_sign(raw), client_ip="1.2.3.4")
        assert status == 401 and "signature" in data["message"]

    def test_wrong_secret_rejected(self, service):
        status, _ = _webhook(service, _license_event("checkout.completed"), secret="wrong-secret")
        assert status == 401

    def test_missing_signature_rejected(self, service):
        raw = json.dumps(_license_event("checkout.completed")).encode()
        status, _ = service.creem_webhook(raw_body=raw, signature="", client_ip="1.2.3.4")
        assert status == 401

    def test_unconfigured_creem_returns_503(self, store, stub, monkeypatch):
        svc = _make_service(store, stub, monkeypatch, with_creem=False)
        status, data = _webhook(svc, _license_event("checkout.completed"))
        assert status == 503 and svc.creem is None

    def test_unknown_event_ignored(self, service):
        status, data = _webhook(service, {"eventType": "subscription.active", "object": {}})
        assert status == 200 and "ignored" in data["message"]

    def test_oversized_webhook_body_ok_reason(self, service):
        """webhook 上限 256KB，不能被客户端 16KB 上限误杀（_read_raw_body 重构的回归点）。"""
        event = _license_event("checkout.completed")
        event["object"]["note"] = "x" * 40000  # > 16KB 但 < 256KB
        status, data = _webhook(service, event)
        assert status == 200 and data["ok"] is True


# ================================================= 2/3. checkout.completed
class TestCheckoutCompleted:
    def test_records_order_and_mirrors_license(self, service, store):
        status, data = _webhook(service, _license_event("checkout.completed"))
        assert status == 200 and data["ok"] is True
        order = store.get_creem_order_by_dedupe_key("checkout.completed:ord_1")
        assert order is not None
        assert order["license_key"] == CREEM_LICENSE_KEY
        assert order["customer_email"] == "buyer@example.com"
        assert order["amount"] == 1990 and order["currency"] == "USD"
        assert order["device_id"] == DEVICE_ID
        row = store.get_license_by_key(CREEM_LICENSE_KEY)
        assert row is not None and row["status"] == "active" and row["plan_type"] == "lifetime"

    def test_duplicate_delivery_is_idempotent(self, service, store):
        _webhook(service, _license_event("checkout.completed"))
        status, data = _webhook(service, _license_event("checkout.completed"))
        assert status == 200 and data.get("duplicate") is True
        assert len(store.list_creem_orders()) == 1
        assert len(store.list_licenses()) == 1

    def test_event_without_license_key_still_records_order(self, service, store):
        event = _license_event("checkout.completed")
        event["object"].pop("license")
        status, data = _webhook(service, event)
        assert status == 200 and data["ok"] is True
        assert store.get_creem_order_by_license_key(CREEM_LICENSE_KEY) is None
        assert len(store.list_creem_orders()) == 1
        assert store.list_licenses() == []  # 不建空码

    def test_existing_license_not_overwritten(self, service, store):
        store.create_license(CREEM_LICENSE_KEY, plan_type="lifetime", status="active")
        before = store.get_license_by_key(CREEM_LICENSE_KEY)["id"]
        _webhook(service, _license_event("checkout.completed"))
        assert store.get_license_by_key(CREEM_LICENSE_KEY)["id"] == before


# ======================================================== 4. refund.created
class TestRefund:
    def test_refund_revokes_license(self, service, store):
        _webhook(service, _license_event("checkout.completed"))
        status, data = _webhook(service, _license_event("refund.created"))
        assert status == 200 and data["license_revoked"] is True
        assert store.get_license_by_key(CREEM_LICENSE_KEY)["status"] == "revoked"
        assert store.get_creem_order_by_dedupe_key("checkout.completed:ord_1")["status"] == "refunded"

    def test_refund_before_mirror_blocks_later_activation(self, service, store, stub):
        """退款先于发码/镜像到达时，该码之后也不能激活成功（不依赖 Creem 是否同步禁用）。"""
        assert stub.license_status == "active"  # Creem 侧仍报有效
        _webhook(service, _license_event("refund.created"))
        status, data = service.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                                client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and data["success"] is False and "撤销" in data["message"]
        assert store.get_license_by_key(CREEM_LICENSE_KEY) is None
        assert stub.activations == []  # 连 Creem 的激活都不该调

    def test_refund_is_idempotent(self, service, store):
        _webhook(service, _license_event("refund.created"))
        status, data = _webhook(service, _license_event("refund.created"))
        assert status == 200 and data.get("duplicate") is True
        assert len(store.list_creem_orders()) == 1

    def test_revoked_license_cannot_activate(self, service, store):
        _webhook(service, _license_event("refund.created"))
        status, data = service.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                                client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and data["success"] is False
        assert "禁用" in data["message"] or "撤销" in data["message"]


# ==================================================== 5. 激活双轨（Creem 码）
class TestActivateCreemLicense:
    def test_creem_key_validated_mirrored_and_bound(self, service, store, stub):
        status, data = service.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                                client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and data["success"] is True
        assert data["activated_now"] is True
        assert [a["instance_name"] for a in stub.activations] == [DEVICE_ID]
        row = store.get_license_by_key(CREEM_LICENSE_KEY)
        assert row["status"] == "active" and row["device_id"] == DEVICE_ID
        assert stub.ua and "Python-urllib" not in stub.ua  # CF 会挡默认 UA

    def test_creem_key_second_device_blocked(self, service, store):
        service.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                 client_ip="1.2.3.4", api_key="client-key")
        status, data = service.activate_license(
            payload=_activate_payload(CREEM_LICENSE_KEY, device="device9999999999999999"),
            client_ip="1.2.3.5", api_key="client-key")
        assert status == 200 and data["success"] is False and "其他设备" in data["message"]

    def test_second_activation_hits_local_table_not_creem(self, service, store, stub):
        service.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                 client_ip="1.2.3.4", api_key="client-key")
        calls_after_first = len(stub.activations)
        status, data = service.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                                client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and data["success"] is True and data["activated_now"] is False
        assert len(stub.activations) == calls_after_first  # 第二次不再打 Creem

    def test_inactive_creem_key_rejected(self, service, store, stub):
        stub.license_status = "disabled"
        status, data = service.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                                client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and data["success"] is False and "禁用" in data["message"]
        assert store.get_license_by_key(CREEM_LICENSE_KEY) is None

    def test_expired_creem_key_message(self, service, store, stub):
        stub.license_status = "expired"
        status, data = service.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                                client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and "过期" in data["message"]

    def test_creem_activation_conflict_maps_to_device_message(self, service, stub):
        stub.license_status = "active"
        stub.activation_error_status = 409
        status, data = service.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                                client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and data["success"] is False and "其他设备" in data["message"]

    def test_local_license_still_works(self, service, store, stub):
        store.create_license("LTAA-BBBB-CCCC-DDEE", plan_type="lifetime", status="active")
        status, data = service.activate_license(payload=_activate_payload("LTAA-BBBB-CCCC-DDEE"),
                                                client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and data["success"] is True
        assert stub.activations == []  # 本地码不打扰 Creem


# ============================================ 6. 回归：未配置 Creem 时零变化
class TestWithoutCreem:
    def test_unknown_key_still_invalid(self, store, stub, monkeypatch):
        svc = _make_service(store, stub, monkeypatch, with_creem=False)
        status, data = svc.activate_license(payload=_activate_payload(CREEM_LICENSE_KEY),
                                            client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and data["success"] is False and data["message"] == "激活码无效"

    def test_checkout_endpoint_returns_503(self, store, stub, monkeypatch):
        svc = _make_service(store, stub, monkeypatch, with_creem=False)
        status, data = svc.creem_create_checkout(
            payload={"device_id": DEVICE_ID, "app": "chaos_downloader", "request_id": REQUEST_ID,
                     "hw_hash": HW_HASH},
            client_ip="1.2.3.4", api_key="client-key")
        assert status == 503

    def test_local_license_flow_unaffected(self, store, stub, monkeypatch):
        svc = _make_service(store, stub, monkeypatch, with_creem=False)
        store.create_license("YRAA-BBBB-CCCC-DDEE", plan_type="yearly", status="active")
        status, data = svc.activate_license(payload=_activate_payload("YRAA-BBBB-CCCC-DDEE"),
                                            client_ip="1.2.3.4", api_key="client-key")
        assert status == 200 and data["success"] is True


# ================================================ 7. Handler 级（真实 HTTP）
@pytest.fixture
def http_service(store, stub, monkeypatch):
    svc = _make_service(store, stub, monkeypatch)

    class _H(Handler):
        pass

    _H.service = svc
    _H.backend_name = "memory"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", svc, store
    srv.shutdown()


def _post(url, payload, headers=None):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class TestHandlerRouting:
    def test_webhook_via_http_signed(self, http_service):
        base, svc, store = http_service
        raw = json.dumps(_license_event("checkout.completed")).encode()
        status, data = _post(f"{base}/webhook/creem", json.loads(raw),
                             {"Creem-Signature": _sign(raw)})
        assert status == 200 and data["ok"] is True
        assert store.get_license_by_key(CREEM_LICENSE_KEY) is not None

    def test_webhook_via_http_bad_signature(self, http_service):
        base, svc, store = http_service
        status, data = _post(f"{base}/webhook/creem", _license_event("checkout.completed"),
                             {"Creem-Signature": "deadbeef"})
        assert status == 401 and store.list_creem_orders() == []

    def test_webhook_without_signature_header(self, http_service):
        base, svc, store = http_service
        status, _ = _post(f"{base}/webhook/creem", _license_event("checkout.completed"))
        assert status == 401 and store.list_creem_orders() == []

    def test_checkout_endpoint_requires_api_key(self, http_service):
        base, svc, store = http_service
        payload = {"device_id": DEVICE_ID, "app": "chaos_downloader", "request_id": REQUEST_ID,
                   "hw_hash": HW_HASH}
        status, _ = _post(f"{base}/creem/checkout", payload)
        assert status == 401

    def test_checkout_endpoint_creates_checkout(self, http_service):
        base, svc, store = http_service
        payload = {"device_id": DEVICE_ID, "app": "chaos_downloader", "request_id": REQUEST_ID,
                   "hw_hash": HW_HASH}
        status, data = _post(f"{base}/creem/checkout", payload, {"X-Api-Key": "client-key"})
        assert status == 200 and data["success"] is True
        assert data["checkout_url"].startswith("https://creem.io/")
        assert svc.creem.product_id == PRODUCT_ID

    def test_existing_routes_unaffected(self, http_service):
        base, svc, store = http_service
        status, data = _post(f"{base}/register-trial",
                             {"device_id": DEVICE_ID, "app": "chaos_downloader",
                              "request_id": REQUEST_ID, "hw_hash": HW_HASH},
                             {"X-Api-Key": "client-key"})
        assert status == 200 and data.get("ok") is True

    def test_unknown_route_404(self, http_service):
        base, svc, store = http_service
        status, _ = _post(f"{base}/nope", {"x": 1}, {"X-Api-Key": "client-key"})
        assert status == 404

    def test_service_starts_without_creem_client(self, monkeypatch):
        """creem_client 缺失/未配置时服务仍可构造（不能因可选依赖崩掉）。"""
        for name in ("CREEM_API_KEY", "CREEM_API_BASE", "CREEM_PRODUCT_ID", "CREEM_WEBHOOK_SECRET"):
            monkeypatch.delenv(name, raising=False)
        svc = TrialRegisterService(db=MemoryStore(), api_key="k", admin_key="a", signing_key="")
        assert svc.creem is None and svc._creem_webhook_secret == ""
