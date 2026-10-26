"""Meta (Facebook/Instagram) Marketing API — hesap ve kampanya seviyesi günlük insights."""
import json
from datetime import date

import httpx

from .. import config
from .base import AdRow, CampaignRow, ConnectorError, Field, FetchResult

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


STATUS_TR = {"ACTIVE": "Aktif", "PAUSED": "Duraklatıldı", "CAMPAIGN_PAUSED": "Duraklatıldı", "DELETED": "Kaldırıldı",
             "ARCHIVED": "Arşivlendi", "IN_PROCESS": "İnceleniyor", "WITH_ISSUES": "Sorunlu"}


def _pages(cfg, path, params):
    body = _get(cfg, path, params)
    while True:
        yield from body.get("data", [])
        nxt = body.get("paging", {}).get("next")
        if not nxt:
            return
        body = httpx.get(nxt, timeout=60).json()
        if "error" in body:
            raise ConnectorError(f"Meta hatası: {body['error'].get('message', '')[:250]}")


def _campaigns(cfg, start: date, end: date) -> list[CampaignRow]:
    status = {}
    try:
        for c in _pages(cfg, f"{_acct(cfg)}/campaigns", {"fields": "id,effective_status", "limit": 500}):
            status[c["id"]] = STATUS_TR.get(c.get("effective_status", ""), c.get("effective_status", ""))
    except ConnectorError:
        pass                                      # durum okunamazsa metrikler yine gelir
    rows = []
    params = {
        "level": "campaign", "time_increment": 1,
        "fields": "campaign_id,campaign_name,spend,impressions,clicks,actions,action_values",
        "time_range": json.dumps({"since": start.isoformat(), "until": end.isoformat()}),
        "limit": 500,
    }
    for d in _pages(cfg, f"{_acct(cfg)}/insights", params):
        rows.append(CampaignRow(
            day=date.fromisoformat(d["date_start"]), campaign_id=d.get("campaign_id", ""),
            name=d.get("campaign_name", ""), status=status.get(d.get("campaign_id", ""), ""),
            spend=float(d.get("spend", 0)), impressions=int(d.get("impressions", 0)), clicks=int(d.get("clicks", 0)),
            conversions=_pick(d.get("actions"), PURCHASE_TYPES),
            conversion_value=_pick(d.get("action_values"), PURCHASE_TYPES),
            leads=_pick(d.get("actions"), LEAD_TYPES),
        ))
    return rows


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
            break
        body = httpx.get(nxt, timeout=60).json()
    res.campaigns = _campaigns(cfg, start, end)
    return res
