# -*- coding: utf-8 -*-
"""creem_client.py 单元测试（纯 mock，不联网、不需要 Creem 账号）。

覆盖重点：
1. 请求形状（URL / 方法 / x-api-key / body / metadata）
2. 错误映射（4xx/5xx/超时/网络不可达/非 JSON）与 retryable 判定
3. **凭据卫生**：API key 永不出现在异常文本或 repr 里
4. webhook HMAC-SHA256 验签（正例 + 各类反例）
5. 环境变量装载（缺 key → None，供服务端降级）
"""

import io
import json
import socket
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import creem_client  # noqa: E402
from creem_client import (  # noqa: E402
    CreemClient,
    CreemError,
    client_from_env,
    required_env_names,
    verify_webhook_signature,
)

API_KEY = "creem_test_FAKEKEY_do_not_use_1234567890"
PRODUCT = "prod_TEST123"
SECRET = "whsec_TEST_SECRET"


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._raw = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install(monkeypatch, payload=None, status=200, error=None):
    """替换 urlopen，返回捕获到的请求列表。"""
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "headers": {k.lower(): v for k, v in request.header_items()},
                "body": json.loads(request.data.decode("utf-8")) if request.data else None,
                "timeout": timeout,
            }
        )
        if error is not None:
            raise error
        return _FakeResponse(payload if payload is not None else {}, status)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def _http_error(code, body=None, url="https://api.creem.io/v1/checkouts"):
    raw = json.dumps(body if body is not None else {"message": "bad", "trace_id": "tr_123"}).encode()
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(raw))


def _client(**kwargs):
    params = {"api_key": API_KEY, "product_id": PRODUCT, "webhook_secret": SECRET}
    params.update(kwargs)
    return CreemClient(**params)


# --------------------------------------------------------------- 构造与卫生
class TestConstruction:
    def test_empty_api_key_rejected(self):
        with pytest.raises(ValueError):
            CreemClient("")

    def test_repr_hides_api_key(self):
        assert API_KEY not in repr(_client())

    def test_default_base_url_is_production(self):
        assert _client().base_url == creem_client.DEFAULT_API_BASE

    def test_base_url_trailing_slash_normalized(self):
        assert _client(base_url="https://test-api.creem.io/v1/").base_url == (
            "https://test-api.creem.io/v1"
        )


# ------------------------------------------------------------------ Checkout
class TestCreateCheckout:
    def test_request_shape(self, monkeypatch):
        payload = {"id": "ch_1", "checkout_url": "https://checkout.creem.io/ch_1", "status": "pending"}
        calls = _install(monkeypatch, payload)
        result = _client().create_checkout(
            request_id="order_123",
            success_url="https://hundunxiazaiqi.cn/download",
            metadata={"device_id": "dev_abc", "hw_hash": "hh_1"},
        )
        assert result["checkout_url"] == "https://checkout.creem.io/ch_1"
        call = calls[0]
        assert call["url"] == "https://api.creem.io/v1/checkouts"
        assert call["method"] == "POST"
        assert call["headers"]["x-api-key"] == API_KEY
        assert call["headers"]["content-type"] == "application/json"
        assert call["body"]["product_id"] == PRODUCT
        assert call["body"]["request_id"] == "order_123"
        assert call["body"]["success_url"] == "https://hundunxiazaiqi.cn/download"
        assert call["body"]["metadata"] == {"device_id": "dev_abc", "hw_hash": "hh_1"}

    def test_sends_default_user_agent(self, monkeypatch):
        """Cloudflare 会按 UA 拦机器人：默认 Python-urllib 会被挡成 403/1010（实测踩过）。"""
        calls = _install(monkeypatch, {"checkout_url": "x"})
        _client().create_checkout(request_id="o1")
        ua = calls[0]["headers"]["user-agent"]
        assert ua == creem_client.DEFAULT_USER_AGENT
        assert "Python-urllib" not in ua and ua.strip()

    def test_user_agent_env_override(self, monkeypatch):
        monkeypatch.setenv("CREEM_API_KEY", API_KEY)
        monkeypatch.setenv("CREEM_USER_AGENT", "CustomAgent/9.9")
        assert client_from_env().user_agent == "CustomAgent/9.9"
        monkeypatch.setenv("CREEM_USER_AGENT", "   ")
        assert client_from_env().user_agent == creem_client.DEFAULT_USER_AGENT

    def test_user_agent_sent_on_every_endpoint(self, monkeypatch):
        calls = _install(monkeypatch, {"status": "active"})
        _client().activate_license(license_key="K", instance_name="d")
        _client().validate_license(license_key="K")
        _client().list_license_instances(license_id="lic_1")
        assert all(c["headers"].get("user-agent") == creem_client.DEFAULT_USER_AGENT for c in calls)
        assert len(calls) == 3

    def test_metadata_strips_empty_values(self, monkeypatch):
        calls = _install(monkeypatch, {"checkout_url": "x"})
        _client().create_checkout(request_id="o1", metadata={"device_id": "d1", "hw_hash": ""})
        assert calls[0]["body"]["metadata"] == {"device_id": "d1"}

    def test_omits_success_url_when_blank(self, monkeypatch):
        calls = _install(monkeypatch, {"checkout_url": "x"})
        _client().create_checkout(request_id="o1")
        assert "success_url" not in calls[0]["body"]

    def test_requires_product_id(self, monkeypatch):
        _install(monkeypatch, {})
        with pytest.raises(CreemError):
            _client(product_id="").create_checkout(request_id="o1")
        # 未发出请求即失败
        assert True

    def test_requires_request_id(self, monkeypatch):
        _install(monkeypatch, {})
        with pytest.raises(CreemError):
            _client().create_checkout(request_id="  ")


# ------------------------------------------------------------------ Licenses
class TestLicenseEndpoints:
    def test_activate_payload(self, monkeypatch):
        calls = _install(monkeypatch, {"status": "active", "key": "K"})
        _client().activate_license(license_key="ABC-123", instance_name="dev_abc")
        call = calls[0]
        assert call["url"] == "https://api.creem.io/v1/licenses/activate"
        assert call["body"] == {"key": "ABC-123", "instance_name": "dev_abc"}

    def test_validate_omits_empty_instance_id(self, monkeypatch):
        calls = _install(monkeypatch, {"status": "active"})
        _client().validate_license(license_key="ABC-123")
        assert calls[0]["body"] == {"key": "ABC-123"}

    def test_validate_includes_instance_id(self, monkeypatch):
        calls = _install(monkeypatch, {"status": "active"})
        _client().validate_license(license_key="ABC-123", instance_id="inst_9")
        assert calls[0]["body"]["instance_id"] == "inst_9"

    def test_deactivate_requires_instance_id(self, monkeypatch):
        _install(monkeypatch, {})
        with pytest.raises(CreemError):
            _client().deactivate_license(license_key="ABC-123", instance_id="")

    def test_activate_requires_key(self, monkeypatch):
        _install(monkeypatch, {})
        with pytest.raises(CreemError):
            _client().activate_license(license_key="", instance_name="dev")

    def test_customer_licenses_url_quotes_and_paginates(self, monkeypatch):
        calls = _install(monkeypatch, {"items": []})
        _client().list_customer_licenses(customer_id="cust 1/2", page_number=2, page_size=10)
        assert calls[0]["url"].startswith("https://api.creem.io/v1/customers/cust%201%2F2/licenses?")
        assert "page_number=2" in calls[0]["url"] and "page_size=10" in calls[0]["url"]
        assert calls[0]["method"] == "GET"

    def test_license_instances_url(self, monkeypatch):
        calls = _install(monkeypatch, [])
        _client().list_license_instances(license_id="lic_7")
        assert calls[0]["url"] == "https://api.creem.io/v1/licenses/lic_7/instances"


# --------------------------------------------------------------- 错误映射
class TestErrorMapping:
    def test_401_parses_trace_id_and_hides_key(self, monkeypatch):
        _install(monkeypatch, error=_http_error(401, {"message": "invalid key", "trace_id": "tr_9"}))
        with pytest.raises(CreemError) as info:
            _client().create_checkout(request_id="o1")
        err = info.value
        assert err.status == 401
        assert err.trace_id == "tr_9"
        assert err.retryable is False
        assert API_KEY not in str(err)
        assert "invalid key" in str(err)

    @pytest.mark.parametrize("code,retryable", [(400, False), (403, False), (404, False), (429, True), (500, True), (503, True)])
    def test_retryable_classification(self, monkeypatch, code, retryable):
        _install(monkeypatch, error=_http_error(code))
        with pytest.raises(CreemError) as info:
            _client().create_checkout(request_id="o1")
        assert info.value.retryable is retryable
        assert info.value.status == code

    def test_network_unreachable(self, monkeypatch):
        _install(monkeypatch, error=urllib.error.URLError("connection refused"))
        with pytest.raises(CreemError) as info:
            _client().create_checkout(request_id="o1")
        assert info.value.retryable is True
        assert API_KEY not in str(info.value)

    def test_timeout(self, monkeypatch):
        _install(monkeypatch, error=socket.timeout("timed out"))
        with pytest.raises(CreemError) as info:
            _client().create_checkout(request_id="o1")
        assert info.value.retryable is True
        assert "超时" in str(info.value)

    def test_non_json_body(self, monkeypatch):
        def fake_urlopen(request, timeout=None):
            return _FakeResponse.__new__(_FakeResponse)

        class _Bad(_FakeResponse):
            def __init__(self):
                self._raw = b"<html>gateway</html>"
                self.status = 200

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Bad())
        with pytest.raises(CreemError) as info:
            _client().create_checkout(request_id="o1")
        assert "非 JSON" in str(info.value)

    def test_public_dict_shape(self, monkeypatch):
        _install(monkeypatch, error=_http_error(500, {"trace_id": "tr_5"}))
        with pytest.raises(CreemError) as info:
            _client().create_checkout(request_id="o1")
        public = info.value.to_public_dict()
        assert public["ok"] is False and public["success"] is False
        assert public["upstream_status"] == 500
        assert public["upstream_trace_id"] == "tr_5"
        assert API_KEY not in json.dumps(public)


# --------------------------------------------------------------- webhook 验签
class TestWebhookSignature:
    BODY = b'{"eventType":"checkout.completed","object":{"id":"ch_1"}}'

    def _sig(self, body=None, secret=SECRET):
        import hashlib
        import hmac as _hmac

        return _hmac.new(secret.encode(), body if body is not None else self.BODY, hashlib.sha256).hexdigest()

    def test_valid(self):
        assert verify_webhook_signature(self.BODY, self._sig(), SECRET) is True

    def test_tampered_body_rejected(self):
        assert verify_webhook_signature(self.BODY + b" ", self._sig(), SECRET) is False

    def test_wrong_secret_rejected(self):
        assert verify_webhook_signature(self.BODY, self._sig(), "other-secret") is False

    def test_reserialized_json_rejected(self):
        # 重新序列化（键序/空格变化）会算错签名 —— 这正是必须用原始 body 的原因
        reserialized = json.dumps(json.loads(self.BODY.decode()), indent=2).encode()
        assert verify_webhook_signature(reserialized, self._sig(), SECRET) is False

    @pytest.mark.parametrize(
        "body,sig,secret",
        [
            (b"", "abc", SECRET),
            (BODY, "", SECRET),
            (BODY, "abc", ""),
            (BODY, None, SECRET),
        ],
    )
    def test_missing_pieces_rejected(self, body, sig, secret):
        assert verify_webhook_signature(body, sig, secret) is False

    def test_non_ascii_signature_does_not_raise(self):
        assert verify_webhook_signature(self.BODY, "签名-不是ASCII", SECRET) is False

    def test_comma_separated_header_accepts_valid_token(self):
        assert verify_webhook_signature(self.BODY, f"junk, {self._sig()}", SECRET) is True

    def test_case_sensitive(self):
        assert verify_webhook_signature(self.BODY, self._sig().upper(), SECRET) is False


# --------------------------------------------------------------- 环境变量
class TestEnvLoading:
    def test_none_without_key(self, monkeypatch):
        monkeypatch.delenv("CREEM_API_KEY", raising=False)
        assert client_from_env() is None

    def test_reads_all_values(self, monkeypatch):
        monkeypatch.setenv("CREEM_API_KEY", API_KEY)
        monkeypatch.setenv("CREEM_API_BASE", creem_client.TEST_API_BASE)
        monkeypatch.setenv("CREEM_PRODUCT_ID", PRODUCT)
        monkeypatch.setenv("CREEM_WEBHOOK_SECRET", SECRET)
        monkeypatch.setenv("CREEM_TIMEOUT", "5")
        client = client_from_env()
        assert client is not None
        assert client.base_url == creem_client.TEST_API_BASE
        assert client.product_id == PRODUCT
        assert client.webhook_secret == SECRET
        assert client.timeout == 5.0

    def test_bad_timeout_falls_back(self, monkeypatch):
        monkeypatch.setenv("CREEM_API_KEY", API_KEY)
        monkeypatch.setenv("CREEM_TIMEOUT", "not-a-number")
        assert client_from_env().timeout == creem_client.DEFAULT_TIMEOUT

    def test_required_env_names_lists_missing(self, monkeypatch):
        for name in ("CREEM_API_KEY", "CREEM_PRODUCT_ID", "CREEM_WEBHOOK_SECRET"):
            monkeypatch.delenv(name, raising=False)
        assert required_env_names() == ["CREEM_API_KEY", "CREEM_PRODUCT_ID", "CREEM_WEBHOOK_SECRET"]
        monkeypatch.setenv("CREEM_API_KEY", API_KEY)
        assert required_env_names() == ["CREEM_PRODUCT_ID", "CREEM_WEBHOOK_SECRET"]
