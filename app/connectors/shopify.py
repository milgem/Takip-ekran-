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
from .base import ItemRow, StockRow, ConnectorError, Field, FetchResult, OrderRow

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
  orders(first: 25, after: $cursor, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt cancelledAt sourceName tags
      app { name }
      displayFinancialStatus displayFulfillmentStatus
      currentTotalPriceSet { shopMoney { amount currencyCode } }
      lineItems(first: 30) { nodes {
        name variantTitle sku currentQuantity
        discountedTotalSet { shopMoney { amount } }
        variant { id barcode } product { id title }
      } }
      __CUSTOMER__
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


CUSTOMER_FIELDS = "customer { displayName } billingAddress { name }"


def _orders_page(cfg, cursor, q):
    """Müşteri adıyla dener; uygulamanın müşteri verisi izni yoksa isimsiz sorguya döner."""
    try:
        return _gql(cfg, ORDERS_QUERY.replace("__CUSTOMER__", CUSTOMER_FIELDS), {"cursor": cursor, "q": q})["orders"]
    except ConnectorError as e:
        if "yetki" in str(e).lower() and "token" in str(e).lower():
            raise
        return _gql(cfg, ORDERS_QUERY.replace("__CUSTOMER__", ""), {"cursor": cursor, "q": q})["orders"]


def _gid(v) -> str:
    return str(v or "").rsplit("/", 1)[-1]


def _line_items(n) -> list:
    out = []
    for li in ((n.get("lineItems") or {}).get("nodes") or []):
        qty = float(li.get("currentQuantity") or 0)      # iade/iptal edilen adetler düşülmüş
        if qty <= 0:
            continue
        var, prod = li.get("variant") or {}, li.get("product") or {}
        title = li.get("variantTitle") or ""
        out.append(ItemRow(
            product_id=_gid(prod.get("id")), variant_id=_gid(var.get("id")), sku=li.get("sku") or "",
            barcode=var.get("barcode") or "", name=prod.get("title") or li.get("name") or "",
            variant="" if title == "Default Title" else title, quantity=qty,
            amount=float(((li.get("discountedTotalSet") or {}).get("shopMoney") or {}).get("amount") or 0)))
    return out


VARIANTS_QUERY = """
query Variants($cursor: String) {
  productVariants(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title sku barcode inventoryQuantity price
      selectedOptions { name value }
      product { id title status }
    }
  }
}"""

SIZE_HINTS = ("beden", "numara", "size", "olcu", "ölçü", "boy")


def fetch_stock(cfg) -> list:
    rows, cursor = [], None
    while True:
        conn = _gql(cfg, VARIANTS_QUERY, {"cursor": cursor})["productVariants"]
        for v in conn["nodes"]:
            opts = [(o["name"], o["value"]) for o in v.get("selectedOptions") or [] if o.get("value") != "Default Title"]
            sizes = [val for nm, val in opts if any(h in nm.lower() for h in SIZE_HINTS)]
            others = [val for nm, val in opts if not any(h in nm.lower() for h in SIZE_HINTS)]
            p = v.get("product") or {}
            rows.append(StockRow(
                product_id=_gid(p.get("id")), variant_id=_gid(v["id"]), product_name=p.get("title") or "",
                variant=" / ".join(others + sizes), sku=v.get("sku") or "", barcode=v.get("barcode") or "",
                stock=float(v.get("inventoryQuantity") or 0), price=float(v.get("price") or 0),
                active=(p.get("status") == "ACTIVE")))
        if not conn["pageInfo"]["hasNextPage"]:
            return rows
        cursor = conn["pageInfo"]["endCursor"]


def _customer(n) -> str:
    c = (n.get("customer") or {}).get("displayName") or (n.get("billingAddress") or {}).get("name") or ""
    return c.strip()


def fetch(cfg, start: date, end: date) -> FetchResult:
    q = f"created_at:>={start.isoformat()} created_at:<{(end + timedelta(days=1)).isoformat()}"
    res, cursor = FetchResult(), None
    while True:
        conn = _orders_page(cfg, cursor, q)
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
                is_cancelled=cancelled, channel=channel, customer=_customer(n), items=_line_items(n),
                is_pending=not cancelled and ful in ("UNFULFILLED", "PARTIALLY_FULFILLED", "ON_HOLD", "IN_PROGRESS", "SCHEDULED"),
            ))
        if not conn["pageInfo"]["hasNextPage"]:
            return res
        cursor = conn["pageInfo"]["endCursor"]
