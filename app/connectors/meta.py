"""Meta (Facebook/Instagram) Marketing API — hesap seviyesi günlük insights."""
import json
from datetime import date

import httpx

from .. import config
from .base import AdRow, ConnectorError, Field, FetchResult

KIND = "meta"
TITLE = "Meta Ads"
FIELDS = [
    Field("ad_account_id", "Reklam hesabı ID", placeholder="act_1234567890 veya 1234567890"),
    Field("access_token", "Sistem kullanıcısı erişim token'ı", secret=True,
          help="Business Manager > Sistem kullanıcıları > token oluştur (ads_read izni). Süresiz token önerilir."),
]

PURCHASE_TYPES = ["omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase"]
LEAD_TYPES = ["lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"]


def _acct(cfg) -> str:
    a = cfg.get("ad_account_id", "").strip()
    return a if a.startswith("act_") else f"act_{a}"


def _get(cfg, path, params):
    params = {**params, "access_token": cfg["access_token"].strip()}
    r = httpx.get(f"https://graph.facebook.com/{config.META_API_VERSION}/{path}", params=params, timeout=60)
    body = r.json()
    if "error" in body:
        err = body["error"]
        raise ConnectorError(f"Meta hatası ({err.get('code')}): {err.get('message', '')[:250]}")
    return body


def _pick(items, types) -> float:
    found = {i["action_type"]: float(i["value"]) for i in items or []}
    for t in types:
        if t in found:
            return found[t]
    return 0.0


def test(cfg) -> str:
    b = _get(cfg, _acct(cfg), {"fields": "name,currency,account_status"})
    return f"Bağlantı başarılı: {b.get('name')} ({b.get('currency')})"


def fetch(cfg, start: date, end: date) -> FetchResult:
    params = {
        "level": "account", "time_increment": 1,
        "fields": "spend,impressions,clicks,actions,action_values",
        "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
        "limit": 500,
    }
    res = FetchResult()
    body = _get(cfg, f"{_acct(cfg)}/insights", params)
    while True:
        for d in body.get("data", []):
            res.ads.append(AdRow(
                day=date.fromisoformat(d["date_start"]), spend=float(d.get("spend", 0)),
                impressions=int(d.get("impressions", 0)), clicks=int(d.get("clicks", 0)),
                conversions=_pick(d.get("actions"), PURCHASE_TYPES),
                conversion_value=_pick(d.get("action_values"), PURCHASE_TYPES),
                leads=_pick(d.get("actions"), LEAD_TYPES),
            ))
        nxt = body.get("paging", {}).get("next")
        if not nxt:
            return res
        body = httpx.get(nxt, timeout=60).json()
