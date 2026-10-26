"""Shopify Admin GraphQL bağlayıcısı.

İki yetkilendirme yolu desteklenir:
  1) Eski (legacy) özel uygulama -> sabit 'shpat_...' erişim token'ı
  2) 2026 sonrası Dev Dashboard uygulaması -> Client ID + Client Secret
     (client credentials grant; token 24 saatte bir otomatik yenilenir)
"""
import time
from datetime import date, datetime, timedelta

import httpx

from .. import config
from .. import channels
from .base import ConnectorError, Field, FetchResult, OrderRow

KIND = "shopify"
TITLE = "Shopify"
FIELDS = [
    Field("shop", "Mağaza adresi", placeholder="magaza.myshopify.com",
          help="Admin panelindeki .myshopify.com adresi (alan adınız değil)."),
    Field("access_token", "Admin API erişim token'ı (shpat_…)", secret=True, required=False,
          help="Eski tip özel uygulamanız varsa bunu girin; yoksa boş bırakıp aşağıdaki ikisini doldurun."),
    Field("client_id", "Client ID", required=False, help="Dev Dashboard uygulaması için."),
    Field("client_secret", "Client Secret", secret=True, required=False),
]

_token_cache: dict[str, tuple[str, float]] = {}

ORDERS_QUERY = """
query Orders($cursor: String, $q: String!) {
  orders(first: 250, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt cancelledAt sourceName tags
      app { name }
      displayFinancialStatus displayFulfillmentStatus
      currentTotalPriceSet { shopMoney { amount currencyCode } }
    }
  }
}"""


def _shop(cfg) -> str:
    shop = cfg.get("shop", "").strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if not shop.endswith(".myshopify.com"):
        raise ConnectorError("Mağaza adresi '...myshopify.com' ile bitmeli.")
    return shop


def _token(cfg) -> str:
    if cfg.get("access_token"):
        return cfg["access_token"].strip()
    cid, secret = cfg.get("client_id", "").strip(), cfg.get("client_secret", "").strip()
    if not (cid and secret):
        raise ConnectorError("Erişim token'ı ya da Client ID + Client Secret girilmeli.")
    shop = _shop(cfg)
    cached = _token_cache.get(shop)
    if cached and cached[1] > time.time() + 300:
        return cached[0]
    r = httpx.post(f"https://{shop}/admin/oauth/access_token", timeout=30, data={
        "grant_type": "client_credentials", "client_id": cid, "client_secret": secret})
    if r.status_code != 200:
        raise ConnectorError(f"Shopify token alınamadı ({r.status_code}): {r.text[:200]}")
    body = r.json()
    _token_cache[shop] = (body["access_token"], time.time() + int(body.get("expires_in", 86399)))
    return body["access_token"]


def _gql(cfg, query, variables=None) -> dict:
    r = httpx.post(
        f"https://{_shop(cfg)}/admin/api/{config.SHOPIFY_API_VERSION}/graphql.json",
        json={"query": query, "variables": variables or {}},
        headers={"X-Shopify-Access-Token": _token(cfg)}, timeout=60)
    if r.status_code == 401:
        raise ConnectorError("Shopify yetkilendirme hatası — token ya da client bilgileri hatalı.")
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise ConnectorError(f"Shopify hatası: {str(body['errors'])[:300]}")
    return body["data"]


def _local(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(config.TIMEZONE).replace(tzinfo=None)


def test(cfg) -> str:
    d = _gql(cfg, "{ shop { name currencyCode } }")
    msg = f"Bağlantı başarılı: {d['shop']['name']} ({d['shop']['currencyCode']})"
    today = datetime.now(config.TIMEZONE).date()
    orders = fetch(cfg, today - timedelta(days=7), today).orders
    if orders:
        from collections import Counter
        dist = Counter(o.channel for o in orders)
        msg += f". Son 7 gün {len(orders)} sipariş — kanallar: " + ", ".join(f"{k} {v}" for k, v in dist.most_common())
    return msg


def fetch(cfg, start: date, end: date) -> FetchResult:
    q = f"created_at:>={start.isoformat()} created_at:<{(end + timedelta(days=1)).isoformat()}"
    res, cursor = FetchResult(), None
    while True:
        conn = _gql(cfg, ORDERS_QUERY, {"cursor": cursor, "q": q})["orders"]
        for n in conn["nodes"]:
            money = n["currentTotalPriceSet"]["shopMoney"]
            fin, ful = n["displayFinancialStatus"] or "", n["displayFulfillmentStatus"] or ""
            cancelled = bool(n["cancelledAt"]) or fin in ("REFUNDED", "VOIDED")
            app_name = (n.get("app") or {}).get("name", "")
            src = n.get("sourceName") or ""
            # Pazaryeri uygulamaları (Trendyol, Hepsiburada, Amazon…) siparişi kendi adlarıyla
            # ya da etiketle oluşturur; hiçbiri yoksa kaynak adına göre Web / POS.
            channel = channels.detect(app_name, src, " ".join(n.get("tags") or []),
                                      default=channels.classify_source(src))
            res.orders.append(OrderRow(
                external_id=n["id"].rsplit("/", 1)[-1], number=n["name"],
                created_at=_local(n["createdAt"]), total=float(money["amount"]),
                currency=money["currencyCode"], status=f"{fin} / {ful}",
                is_cancelled=cancelled, channel=channel,
                is_pending=not cancelled and ful in ("UNFULFILLED", "PARTIALLY_FULFILLED", "ON_HOLD", "IN_PROGRESS", "SCHEDULED"),
            ))
        if not conn["pageInfo"]["hasNextPage"]:
            return res
        cursor = conn["pageInfo"]["endCursor"]
