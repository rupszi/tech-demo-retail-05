import pandas as pd
import pytest

from retail_agent.safety import scrub_frame, scrub_text


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Contact maria.silva.12@example.com for details", "email"),
        ("Call (415) 555-0132 tomorrow", "phone"),
        ("Call 415-555-0132 or +1 415.555.0132", "phone"),
        ("Ships to 4821 Garcia Street", "street_address"),
        ("Lives at 12 Old Mill Road.", "street_address"),
        ("Located at 37.774929, -122.419416", "coordinates"),
        ("Card 4111 1111 1111 1111 on file", "card_number"),
        ("SSN 123-45-6789", "ssn"),
    ],
)
def test_personal_data_is_masked(text, kind):
    masked, hits = scrub_text(text)
    assert hits.get(kind, 0) >= 1
    assert "removed]" in masked
    assert scrub_text(masked) == (masked, {})  # nothing left to mask


@pytest.mark.parametrize(
    "text",
    [
        "Revenue grew 12.5% to $1,234,567.89 in Q1 2025.",
        "Top customer is ID 4821 with 37 orders between 2024-01-15 and 2025-03-02.",
        "## 3 Key Factors Drive Growth",
        "Texas averages $49.25 per item versus $60.14 in Illinois.",
        "Driftline returns: 35% (120 of 343 items).",
        "Customers from the Email channel spend 8% more.",
        "Order 1234567890123 shipped in 3 days.",
    ],
)
def test_business_text_is_left_alone(text):
    assert scrub_text(text) == (text, {})


def test_counts_every_hit():
    _, hits = scrub_text("a@example.com, b@example.com and 415-555-0132")
    assert hits == {"email": 2, "phone": 1}


def test_frame_text_cells_are_masked():
    frame = pd.DataFrame({"id": [1, 2], "note": ["mail a@example.com", "fine"], "n": [1.5, 2.5]})
    cleaned, hits = scrub_frame(frame)
    assert cleaned["note"].tolist() == ["mail [email removed]", "fine"]
    assert hits == {"email": 1}
    assert frame["note"][0] == "mail a@example.com"  # the input is not modified
    assert cleaned["id"].tolist() == [1, 2] and cleaned["n"].tolist() == [1.5, 2.5]


def test_frame_without_text_is_returned_as_is():
    frame = pd.DataFrame({"n": [1, 2]})
    cleaned, hits = scrub_frame(frame)
    assert cleaned is frame and hits == {}


def test_every_string_in_a_nested_value_is_masked():
    from retail_agent.safety import scrub_value

    value = {
        "sql": "WHERE email = 'a.b@example.com'",
        "steps": [{"error": "call 555-123-4567"}, ("c.d@example.com", 3)],
        "ms": 12,
        "ok": True,
    }
    assert scrub_value(value) == {
        "sql": "WHERE email = '[email removed]'",
        "steps": [{"error": "call [phone removed]"}, ["[email removed]", 3]],
        "ms": 12,
        "ok": True,
    }
