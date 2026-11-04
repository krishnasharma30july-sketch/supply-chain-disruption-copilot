"""Customer-facing item wording: "1 Baby item" instead of "1 x Baby"."""

from app.services.comms import item_phrase, parse_item


def test_english_phrases_say_what_the_item_is():
    assert item_phrase(1, "Baby", "en") == "1 Baby item"
    assert item_phrase(2, "Bed Bath Table", "en") == "2 Bed Bath Table items"
    assert item_phrase(1, "Item", "en") == "1 item" and item_phrase(3, "Item", "en") == "3 items"


def test_portuguese_phrases():
    assert item_phrase(1, "Bebês", "pt-BR") == "1 item de Bebês"
    assert item_phrase(2, "Cama Mesa Banho", "pt-BR") == "2 itens de Cama Mesa Banho"
    assert item_phrase(2, "Item", "pt-BR") == "2 itens"


def test_every_format_parses_back_to_count_and_category():
    assert parse_item("1 Baby item") == (1, "Baby")
    assert parse_item("2 Bed Bath Table items") == (2, "Bed Bath Table")
    assert parse_item("2 itens de Cama Mesa Banho") == (2, "Cama Mesa Banho")
    assert parse_item("1 x Baby") == (1, "Baby")  # fact sheets stored by older runs
    assert parse_item("3 items") == (3, "")
    assert parse_item("something odd") == (1, "")
