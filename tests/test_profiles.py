"""A user's profile comes from token claims, and access is denied unless a scope grants it."""

import pytest

from retail_agent.safety import UserProfile


def profile(**claims):
    return UserProfile.from_claims({"sub": "alice", **claims})


def test_brand_scopes_become_the_users_brands():
    p = profile(name="Alice", scopes=["brand:Allegra K", "brand:Levi's"])
    assert (p.user_id, p.name) == ("alice", "Alice")
    assert p.brands == ("Allegra K", "Levi's") and not p.all_brands
    assert p.describe_scope() == "brands: Allegra K, Levi's"


def test_seeing_every_brand_takes_an_explicit_grant():
    p = profile(scopes=["brand:*"])
    assert p.all_brands and p.describe_scope() == "all brands"


@pytest.mark.parametrize(
    "claims", [{}, {"scopes": []}, {"scopes": ["brand:"]}, {"scopes": ["brand: "]}]
)
def test_no_brand_scope_means_no_access(claims):
    p = profile(**claims)
    assert p.brands == () and not p.all_brands
    assert p.describe_scope() == "no brands"


def test_scopes_of_other_kinds_grant_nothing():
    p = profile(scopes=["reports:read", "admin", "*", "brands:*", "Brand:Roxy", "brand:Roxy"])
    assert p.brands == ("Roxy",) and not p.all_brands


def test_duplicates_are_removed_and_order_is_kept():
    assert profile(scopes=["brand:B", "brand:A", "brand:B"]).brands == ("B", "A")


def test_a_brand_name_may_contain_a_colon():
    assert profile(scopes=["brand:Dolce: Vita"]).brands == ("Dolce: Vita",)


def test_the_name_defaults_to_the_subject():
    assert profile().name == "alice"


@pytest.mark.parametrize("claims", [{}, {"sub": ""}, {"sub": "  "}, {"sub": 42}])
def test_a_token_without_a_subject_is_rejected(claims):
    with pytest.raises(ValueError, match="subject"):
        UserProfile.from_claims({"scopes": ["brand:*"], **claims})


@pytest.mark.parametrize("scopes", ["brand:*", {"brand": "*"}, ["brand:Roxy", 7], None])
def test_malformed_scopes_are_rejected(scopes):
    with pytest.raises(ValueError, match="scopes"):
        profile(scopes=scopes)


def test_sample_token_payloads_load_for_both_backends(profiles):
    from pathlib import Path

    from retail_agent.safety import load_profiles

    assert set(profiles) == {"alice", "bob", "carol"}
    assert profiles["carol"].all_brands and not profiles["alice"].all_brands
    real = load_profiles(Path(__file__).parents[1] / "config" / "users.bigquery.json")
    assert set(real) == {"alice", "bob", "carol"}
    assert real["alice"].brands == ("Allegra K", "Levi's", "Roxy")
    assert set(real["alice"].brands).isdisjoint(real["bob"].brands)
