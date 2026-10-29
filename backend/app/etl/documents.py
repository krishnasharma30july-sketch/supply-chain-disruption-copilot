"""Retrieval documents: generated text from typed fields plus derived tags (so "heavy/bulky" is searchable)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from app.services.formatting import pretty_category
from app.services.geo import title_city

STAGE_LABELS = {
    "pending_approval": "pending payment approval",
    "awaiting_pickup": "awaiting carrier pickup",
    "in_transit": "in transit",
    "delivered": "delivered",
}
PROFILE_TAGS = {
    "BULKY": "heavy bulky oversized large freight big item volumoso pesado grande",
    "MEDIUM": "medium-size package",
    "PARCEL": "small parcel lightweight package",
}
CLASS_TAGS = {
    "FURNITURE_HOME": "furniture home goods household appliances moveis casa",
    "PERISHABLE": "perishable food beverages fresh perecivel alimentos",
    "ESSENTIAL": "essential items baby hygiene health pet essenciais",
    "FRAGILE": "fragile delicate breakable fragil",
    "HIGH_VALUE_TECH": "electronics technology high value gadgets eletronicos",
    "GIFT_SEASONAL": "gifts toys seasonal party presentes",
    "STANDARD": "general merchandise",
}


def _d(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "n/a"


def order_document(order: dict, lines: list[dict], seller_by_id: dict, customer: dict, lane_p50: float | None) -> dict:
    counts = Counter(l["product_id"] for l in lines)
    seen: set[str] = set()
    item_parts: list[str] = []
    tags: set[str] = set()
    for l in lines:
        if l["product_id"] in seen:
            continue
        seen.add(l["product_id"])
        item_parts.append(
            f"{counts[l['product_id']]}x {pretty_category(l['category_en'])} ({l['category_pt'] or 'uncategorized'}): "
            f"{l['billable_weight_kg']:.1f} kg billable, {l['length_cm']:.0f}x{l['width_cm']:.0f}x{l['height_cm']:.0f} cm, "
            f"{l['handling_profile']}, {l['product_class']}"
        )
        tags.update(PROFILE_TAGS[l["handling_profile"]].split())
        tags.update(CLASS_TAGS.get(l["product_class"], "").split())
    sellers = []
    for sid in dict.fromkeys(l["seller_id"] for l in lines):
        s = seller_by_id[sid]
        sellers.append(f"{title_city(s['city_norm'])}, {s['state']} (seller {sid[:8]})")
    first_seller = seller_by_id[lines[0]["seller_id"]]
    value = sum(l["price"] + l["freight_value"] for l in lines)
    freight = sum(l["freight_value"] for l in lines)
    stage = STAGE_LABELS.get(order["stage"], order["stage"])
    lane = f"{first_seller['state']}->{customer['state']}"
    # Tags come right after the header: the embedding model only reads the first ~128 tokens, and the tags are what
    # make concepts such as "heavy/bulky" or "perishable" semantically retrievable.
    content = "\n".join([
        f"Order {order['order_id']} | stage: {stage} | purchased {_d(order['purchase_ts'])} | promised by {_d(order['estimated_delivery_ts'])}",
        "Tags: " + " ".join(sorted(tags)),
        "Items: " + "; ".join(item_parts),
        "Seller: " + "; ".join(sellers),
        f"Destination: {title_city(customer['city_norm'])}, {customer['state']} | lane {lane}"
        + (f" median transit {lane_p50:.1f} days" if lane_p50 is not None else ""),
        f"Value R$ {value:.2f} including freight R$ {freight:.2f}",
    ])
    title = (
        f"Order {order['order_id'][:8].upper()} | {stage} | {title_city(first_seller['city_norm'])}/{first_seller['state']}"
        f" -> {title_city(customer['city_norm'])}/{customer['state']}"
    )
    meta = {
        "stage": order["stage"],
        "origin_uf": first_seller["state"],
        "dest_uf": customer["state"],
        "category_pt": lines[0]["category_pt"] or "",
        "product_class": lines[0]["product_class"],
        "handling_profile": max((l["handling_profile"] for l in lines), key=lambda p: {"PARCEL": 0, "MEDIUM": 1, "BULKY": 2}[p]),
        "value_brl": round(value, 2),
        "purchase_date": _d(order["purchase_ts"]),
    }
    return {"doc_id": f"order:{order['order_id']}", "doc_type": "order", "ref_id": order["order_id"],
            "title": title[:200], "content": content, "meta": meta}


def product_document(p: dict, sellers_offering: int) -> dict:
    content = "\n".join([
        f"Product {p['product_id']} | category {pretty_category(p['category_en'])} ({p['category_pt'] or 'uncategorized'})",
        "Tags: " + PROFILE_TAGS[p["handling_profile"]] + " " + CLASS_TAGS.get(p["product_class"], ""),
        f"Class {p['product_class']} | handling {p['handling_profile']}",
        f"Weight {p['weight_g'] / 1000:.2f} kg, dimensions {p['length_cm']:.0f}x{p['width_cm']:.0f}x{p['height_cm']:.0f} cm, "
        f"billable {p['billable_weight_kg']:.1f} kg" + (" (dimensions imputed)" if p["imputed"] else ""),
        f"Offered by {sellers_offering} seller(s) recently",
    ])
    return {
        "doc_id": f"product:{p['product_id']}", "doc_type": "product", "ref_id": p["product_id"],
        "title": f"{pretty_category(p['category_en'])} | {p['billable_weight_kg']:.1f} kg | {p['handling_profile']}"[:200],
        "content": content,
        "meta": {"category_pt": p["category_pt"] or "", "product_class": p["product_class"],
                 "handling_profile": p["handling_profile"], "billable_weight_kg": round(p["billable_weight_kg"], 2)},
    }


def seller_document(s: dict, stats: dict | None, categories: list[str], classes: list[str], active_orders: int) -> dict:
    st = stats or {}
    rate = st.get("late_delivery_rate")
    handling = st.get("median_handling_days")
    content = "\n".join([
        f"Seller {s['seller_id']} located in {title_city(s['city_norm'])}, {s['state']}",
        "Tags: " + " ".join(sorted({t for c in classes for t in CLASS_TAGS.get(c, '').split()})),
        "Sells: " + (", ".join(pretty_category(c) for c in categories) or "n/a"),
        f"Active orders in the snapshot: {active_orders}",
        f"Historical late-delivery rate: {rate:.0%}" if rate is not None else "Historical late-delivery rate: n/a",
        f"Median handling time: {handling:.1f} days" if handling is not None else "Median handling time: n/a",
    ])
    return {
        "doc_id": f"seller:{s['seller_id']}", "doc_type": "seller", "ref_id": s["seller_id"],
        "title": f"Seller {s['seller_id'][:8]} | {title_city(s['city_norm'])}, {s['state']}",
        "content": content, "meta": {"state": s["state"], "city": s["city_norm"]},
    }


def category_document(name_pt: str, name_en: str, product_class: str) -> dict:
    content = (
        f"{pretty_category(name_en)} ({name_pt.replace('_', ' ')}). Product class: {product_class}. "
        f"Related terms: {CLASS_TAGS.get(product_class, '')}"
    )
    return {"doc_id": f"category:{name_pt}", "doc_type": "category", "ref_id": name_pt,
            "title": pretty_category(name_en), "content": content, "meta": {"product_class": product_class}}


def policy_document(chunk_id: str, doc_title: str, heading: str, text: str) -> dict:
    return {"doc_id": f"policy:{chunk_id}", "doc_type": "policy", "ref_id": chunk_id,
            "title": f"{doc_title}: {heading}"[:200], "content": f"{heading}\n{text}", "meta": {"chunk_id": chunk_id}}
