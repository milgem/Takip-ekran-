"""Google Ads API (REST) — müşteri hesabı günlük metrikler.

Tek MCC (yönetici hesap) altında tüm müşteriler: developer token, OAuth client
ve refresh token bir kez alınır; her firma için sadece müşteri ID'si değişir.
"""
import time
from datetime import date

import httpx

from .. import config
from .base import AdRow, ConnectorError, Field, FetchResult

KIND = "google_ads"
TITLE = "Google Ads"
FIELDS = [
    Field("customer_id", "Müşteri hesap ID", placeholder="123-456-7890"),
    Field("login_customer_id", "MCC (yönetici) hesap ID", required=False, placeholder="111-222-3333",
          help="Hesaba MCC üzerinden erişiyorsanız doldurun."),
    Field("developer_token", "Developer token", secret=True, help="MCC > Araçlar > API Merkezi."),
    Field("client_id", "OAuth Client ID", help="Google Cloud Console > Kimlik bilgileri."),
    Field("client_secret", "OAuth Client Secret", secret=True),
    Field("refresh_token", "Refresh token", secret=True, help="Rehberdeki adımla bir kez alınır."),
]

_tok: dict[str, tuple[str, float]] = {}


def _digits(v: str) -> str:
    return "".join(c for c in (v or "") if c.isdigit())


def _access_token(cfg) -> str:
    key = cfg["client_id"] + cfg["refresh_token"][-8:]
    if key in _tok and _tok[key][1] > time.time() + 120:
        return _tok[key][0]
    r = httpx.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": cfg["client_id"].strip(), "client_secret": cfg["client_secret"].strip(),
        "refresh_token": cfg["refresh_token"].strip(), "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise ConnectorError(f"Google OAuth hatası: {r.text[:250]}")
    b = r.json()
    _tok[key] = (b["access_token"], time.time() + b.get("expires_in", 3600))
    return b["access_token"]


def _search(cfg, gaql: str) -> list[dict]:
    cid = _digits(cfg["customer_id"])
    headers = {"Authorization": f"Bearer {_access_token(cfg)}",
               "developer-token": cfg["developer_token"].strip()}
    if _digits(cfg.get("login_customer_id", "")):
        headers["login-customer-id"] = _digits(cfg["login_customer_id"])
    r = httpx.post(f"https://googleads.googleapis.com/{config.GOOGLE_ADS_API_VERSION}/customers/{cid}/googleAds:searchStream",
                   json={"query": gaql}, headers=headers, timeout=90)
    if r.status_code != 200:
        try:
            msg = r.json()[0]["error"]["message"] if isinstance(r.json(), list) else r.json()["error"]["message"]
        except Exception:  # noqa: BLE001
            msg = r.text[:300]
        raise ConnectorError(f"Google Ads hatası ({r.status_code}): {msg[:300]}")
    rows = []
    for chunk in r.json():
        rows.extend(chunk.get("results", []))
    return rows


def test(cfg) -> str:
    rows = _search(cfg, "SELECT customer.descriptive_name, customer.currency_code FROM customer LIMIT 1")
    c = rows[0]["customer"] if rows else {}
    return f"Bağlantı başarılı: {c.get('descriptiveName', '?')} ({c.get('currencyCode', '?')})"


def fetch(cfg, start: date, end: date) -> FetchResult:
    rows = _search(cfg, f"""
        SELECT segments.date, metrics.cost_micros, metrics.impressions, metrics.clicks,
               metrics.conversions, metrics.conversions_value
        FROM customer
        WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'""")
    res = FetchResult()
    for r in rows:
        m = r.get("metrics", {})
        res.ads.append(AdRow(
            day=date.fromisoformat(r["segments"]["date"]),
            spend=int(m.get("costMicros", 0)) / 1_000_000,
            impressions=int(m.get("impressions", 0)), clicks=int(m.get("clicks", 0)),
            conversions=float(m.get("conversions", 0)), conversion_value=float(m.get("conversionsValue", 0)),
        ))
    return res
