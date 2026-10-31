import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest
from aiohttp import web

from app.miniapp import MiniAppAPI


def signed_init_data(token: str, user_id: int, auth_date: int) -> str:
    values = {"auth_date": str(auth_date), "query_id": "AA-test", "user": json.dumps({"id": user_id})}
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_valid_telegram_webapp_signature():
    raw = signed_init_data("123:secret", 987654, 2_000_000_000)
    assert MiniAppAPI.validate_init_data(raw, "123:secret", now=2_000_000_010) == 987654


def test_bale_webapp_uses_the_same_documented_hmac_scheme():
    raw = signed_init_data("736:bale-secret", 1628853893, 2_000_000_000)
    assert MiniAppAPI.validate_init_data(raw, "736:bale-secret", now=2_000_000_010) == 1628853893


def test_tampered_or_expired_webapp_signature_is_rejected():
    raw = signed_init_data("123:secret", 987654, 2_000_000_000)
    with pytest.raises(web.HTTPUnauthorized):
        MiniAppAPI.validate_init_data(raw.replace("987654", "987655"), "123:secret", now=2_000_000_010)
    with pytest.raises(web.HTTPUnauthorized):
        MiniAppAPI.validate_init_data(raw, "123:secret", now=2_000_100_000)
