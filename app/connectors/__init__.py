from . import google_ads, meta, shopify, ticimax

REGISTRY = {m.KIND: m for m in (shopify, ticimax, meta, google_ads)}
STORE_KINDS = {"shopify", "ticimax"}
AD_KINDS = {"meta", "google_ads"}


def validate(kind: str, cfg: dict):
    from .base import ConnectorError
    missing = [f.label for f in REGISTRY[kind].FIELDS if f.required and not str(cfg.get(f.name, "")).strip()]
    if missing:
        raise ConnectorError("Eksik bilgi: " + ", ".join(missing))
