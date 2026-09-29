import httpx
import pytest

from app.iam import api_client as api


@pytest.fixture
def transport(monkeypatch):
    attempts = []
    delays = []
    monkeypatch.setattr(api.time, "sleep", delays.append)

    def install(handler):
        def record(request):
            attempts.append(request)
            return handler(request)
        client = httpx.Client(transport=httpx.MockTransport(record), base_url="http://iam.test/")
        monkeypatch.setattr(api, "client", client)
        return attempts, delays

    return install


@pytest.mark.parametrize("function,arg", [(api.get_user, "missing"), (api.get_application, "missing")])
def test_404_is_none_without_retry(transport, function, arg):
    attempts, delays = transport(lambda r: httpx.Response(404))
    assert function(arg) is None
    assert len(attempts) == 1
    assert delays == []


@pytest.mark.parametrize("failure", ["timeout", "network", "500"])
def test_transient_failures_retry_three_times(transport, failure, caplog):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        if failure == "network":
            raise httpx.ConnectError("test network failure", request=request)
        return httpx.Response(503)

    attempts, delays = transport(handler)
    with pytest.raises(api.IAMAPIError, match="temporarily unavailable"):
        api.get_user("alice")
    assert len(attempts) == 3
    assert delays == [0.5, 1.0]
    assert "Retrying IAM request" in caplog.text


def test_recovers_after_transient_failure(transport):
    responses = iter([httpx.Response(502), httpx.Response(200, json={"status": "ok"})])
    attempts, delays = transport(lambda r: next(responses))
    assert api.api_health() is True
    assert len(attempts) == 2
    assert delays == [0.5]


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_permanent_failures_do_not_retry(transport, status):
    attempts, delays = transport(lambda r: httpx.Response(status))
    with pytest.raises(api.IAMAPIError):
        api.get_application("sap")
    assert len(attempts) == 1
    assert delays == []


@pytest.mark.parametrize("payload", [[], {"username": "alice"}, {"active": "true"}])
def test_malformed_payload_is_handled(transport, payload):
    attempts, delays = transport(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(api.IAMAPIError):
        api.get_user("alice")
    assert len(attempts) == 1
    assert delays == []


def test_invalid_json_is_handled(transport):
    attempts, _ = transport(lambda r: httpx.Response(200, content=b"not json"))
    assert api.api_health() is False
    assert len(attempts) == 1


def test_explicit_timeouts():
    assert api.client.timeout.connect == 3
    assert api.client.timeout.read == 5


def test_url_encodes_lookup_argument(transport):
    attempts, _ = transport(lambda r: httpx.Response(404))
    api.get_user("alice?admin=true")
    assert attempts[0].url.query == b""
    assert "%3F" in str(attempts[0].url)
