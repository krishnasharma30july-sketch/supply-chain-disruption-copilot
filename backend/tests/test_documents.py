"""Retrieval documents: the derived tags must sit inside the embedding model's input window."""

from datetime import datetime

from app.etl.documents import order_document, product_document, seller_document


def _line(**kw):
    base = dict(product_id="p1", seller_id="s1", order_item_id=1, price=100.0, freight_value=20.0,
                category_en="furniture_decor", category_pt="moveis_decoracao", billable_weight_kg=35.0,
                length_cm=105.0, width_cm=70.0, height_cm=30.0, handling_profile="BULKY", product_class="FURNITURE_HOME")
    base.update(kw)
    return base


def test_order_document_puts_tags_right_after_the_header():
    order = {"order_id": "o" * 32, "stage": "awaiting_pickup", "purchase_ts": datetime(2018, 7, 28),
             "estimated_delivery_ts": datetime(2018, 8, 14)}
    seller = {"s1": {"city_norm": "sao paulo", "state": "SP"}}
    doc = order_document(order, [_line()], seller, {"city_norm": "belo horizonte", "state": "MG"}, 4.0)
    lines = doc["content"].split("\n")
    assert lines[1].startswith("Tags:")
    assert "heavy" in lines[1] and "bulky" in lines[1] and "furniture" in lines[1]


def test_product_and_seller_documents_put_tags_second():
    product = dict(_line(), weight_g=30000.0, imputed=False)
    assert product_document(product, 2)["content"].split("\n")[1].startswith("Tags:")
    seller = {"seller_id": "s" * 32, "city_norm": "sao paulo", "state": "SP"}
    assert seller_document(seller, None, ["furniture_decor"], ["FURNITURE_HOME"], 3)["content"].split("\n")[1].startswith("Tags:")


def test_seller_document_says_where_the_parcels_go():
    from app.etl.documents import destinations_line

    seller = {"seller_id": "abcdef12", "city_norm": "campinas", "state": "SP"}
    doc = seller_document(seller, None, ["furniture_decor"], ["FURNITURE_HOME"], 3,
                          ship_active={"RJ": 3, "SP": 1}, ship_all={"RJ": 5, "SP": 4, "MG": 1})
    lines = doc["content"].split("\n")
    assert lines[1].startswith("Tags:")  # tags stay on line 2 (the retrieval ranking relies on it)
    assert "Parcels in the pipeline now go to: Rio de Janeiro (RJ) 75%, Sao Paulo (SP) 25%" in doc["content"]
    assert "Overall ships to: Rio de Janeiro (RJ) 50%, Sao Paulo (SP) 40%, Minas Gerais (MG) 10%" in doc["content"]
    # a seller with no destination history keeps the old layout (no empty lines)
    plain = seller_document(seller, None, [], [], 0)["content"]
    assert "ships to" not in plain and "" not in plain.split("\n")
    assert destinations_line("x", {}) is None
