"""Middle-name subset identity matching for person nodes."""

from __future__ import annotations

from app.hr_network.fraud_network import _GraphBuilder, _person_id
from app.hr_network.person_names import (
    names_same_person,
    person_identity_key,
    prefer_display_name,
)
from app.hr_network.shab_parser import build_person_timeline, _normalize_person_id


def test_middle_name_subset_is_same_person():
    assert names_same_person("Barbul, Michael", "Barbul, Michael Gabriel")
    assert names_same_person("Barbul, Michael Gabriel", "Barbul, Michael")
    assert names_same_person("Michael Barbul", "Barbul, Michael Gabriel")


def test_different_first_names_not_merged():
    assert not names_same_person("Barbul, Michael", "Barbul, Max")
    assert not names_same_person("Müller, Hans", "Müller, Anna")


def test_besnik_never_merged_with_michael_barbul():
    """Regression: FBP screenshot — unrelated organs must never collapse."""
    pairs = [
        ("Mahmuti, Besnik", "Barbul, Michael"),
        ("Mahmuti, Besnik", "Barbul, Michael Gabriel"),
        ("Besnik Mahmuti", "Michael Gabriel Barbul"),
        ("Mahmuti, Besnik", "Michael Barbul"),
        ("Besnik Mahmuti", "Barbul, Michael Gabriel"),
    ]
    for a, b in pairs:
        assert not names_same_person(a, b), (a, b)
        assert not names_same_person(b, a), (b, a)
        assert person_identity_key(a) != person_identity_key(b)


def test_middle_name_requires_same_surname_and_first_given():
    # Space form without shared surname → no
    assert not names_same_person("Michael Gabriel", "Michael Other")
    # Same last, different first given → no
    assert not names_same_person("Barbul, Michael", "Barbul, Gabriel")
    # Subset only when first token matches
    assert names_same_person("Barbul, Michael", "Barbul, Michael G.")


def test_conflicting_middle_names_not_merged():
    """Both have a second given name that is not a prefix of the other."""
    assert not names_same_person("Weber, Hans Paul", "Weber, Hans Peter")


def test_identity_key_ignores_middle():
    assert person_identity_key("Barbul, Michael") == "barbul-michael"
    assert person_identity_key("Barbul, Michael Gabriel") == "barbul-michael"
    assert person_identity_key("Michael Gabriel Barbul") == "barbul-michael"


def test_prefer_more_complete_name():
    assert prefer_display_name("Barbul, Michael", "Barbul, Michael Gabriel") == (
        "Barbul, Michael Gabriel"
    )
    assert prefer_display_name("Barbul, Michael Gabriel", "Barbul, Michael") == (
        "Barbul, Michael Gabriel"
    )
    # Must not invent hybrids from unrelated people
    assert prefer_display_name("Mahmuti, Besnik", "Barbul, Michael Gabriel") == (
        "Mahmuti, Besnik"
    )


def test_normalize_person_id_still_full_slug():
    """Raw SHAB ids stay distinctive; merge happens via names_same_person."""
    assert _normalize_person_id("Barbul, Michael") == "barbul-michael"
    assert _normalize_person_id("Barbul, Michael Gabriel") == "barbul-michael-gabriel"


def test_graph_collapses_middle_name_variants():
    g = _GraphBuilder()
    g.add_node({
        "id": _person_id("barbul-michael"),
        "type": "person",
        "label": "Barbul, Michael",
        "roles": ["Gesellschafter"],
        "person_status": "current",
        "min_level": 1,
    })
    g.add_node({
        "id": _person_id("barbul-michael-gabriel"),
        "type": "person",
        "label": "Barbul, Michael Gabriel",
        "roles": ["Geschäftsführer", "Gesellschafter"],
        "person_status": "current",
        "min_level": 5,
    })
    g.add_edge(
        frm=_person_id("barbul-michael"),
        to="company:seed",
        label="Ges.",
        edge_type="person_role",
        min_level=1,
    )
    g.add_edge(
        frm=_person_id("barbul-michael-gabriel"),
        to="company:related",
        label="GF",
        edge_type="person_role",
        min_level=5,
    )
    g.add_node({
        "id": "company:seed",
        "type": "company",
        "label": "FBP Bau GmbH",
        "min_level": 1,
    })
    g.add_node({
        "id": "company:related",
        "type": "company",
        "label": "BUGLO GmbH",
        "min_level": 3,
    })

    nodes, edges = g.export(5)
    persons = [n for n in nodes if n.get("type") == "person"]
    assert len(persons) == 1
    p = persons[0]
    assert p["id"] == "person:barbul-michael"
    assert p["label"] == "Barbul, Michael Gabriel"
    assert "Gesellschafter" in p["roles"]
    assert "Geschäftsführer" in p["roles"]
    assert p["min_level"] == 1

    person_ends = {
        e["from"] if e["from"].startswith("person:") else e["to"]
        for e in edges
        if e["from"].startswith("person:") or e["to"].startswith("person:")
    }
    assert person_ends == {"person:barbul-michael"}


def test_graph_does_not_merge_besnik_and_barbul():
    g = _GraphBuilder()
    g.add_node({
        "id": _person_id("mahmuti-besnik"),
        "type": "person",
        "label": "Mahmuti, Besnik",
        "roles": ["Geschäftsführer"],
        "person_status": "former",
        "min_level": 2,
    })
    g.add_node({
        "id": _person_id("barbul-michael"),
        "type": "person",
        "label": "Barbul, Michael Gabriel",
        "roles": ["Gesellschafter"],
        "person_status": "current",
        "min_level": 1,
    })
    # L5 full-name id must still only collapse onto Barbul, never Besnik
    g.add_node({
        "id": _person_id("barbul-michael-gabriel"),
        "type": "person",
        "label": "Barbul, Michael Gabriel",
        "roles": ["Geschäftsführer"],
        "person_status": "current",
        "min_level": 5,
    })
    persons = [n for n in g.export(5)[0] if n.get("type") == "person"]
    assert len(persons) == 2
    labels = {p["label"] for p in persons}
    assert "Mahmuti, Besnik" in labels
    assert any("Barbul" in (p.get("label") or "") for p in persons)
    besnik = next(p for p in persons if "Besnik" in (p.get("label") or ""))
    assert besnik["person_status"] == "former"


def test_graph_keeps_former_status_when_l5_adds_current_at_other_firm():
    g = _GraphBuilder()
    g.add_node({
        "id": _person_id("mahmuti-besnik"),
        "type": "person",
        "label": "Mahmuti, Besnik",
        "roles": ["Geschäftsführer"],
        "person_status": "former",
        "min_level": 2,
    })
    g.add_node({
        "id": _person_id("mahmuti-besnik"),
        "type": "person",
        "label": "Mahmuti, Besnik",
        "roles": ["Geschäftsführer", "Gesellschafter"],
        "person_status": "current",  # at related firm
        "min_level": 5,
    })
    p = next(n for n in g.export(5)[0] if n.get("type") == "person")
    assert p["person_status"] == "former"
    assert p["min_level"] == 2


def test_graph_does_not_merge_different_first_names():
    g = _GraphBuilder()
    g.add_node({
        "id": _person_id("barbul-michael"),
        "type": "person",
        "label": "Barbul, Michael",
        "roles": [],
        "min_level": 1,
    })
    g.add_node({
        "id": _person_id("barbul-max"),
        "type": "person",
        "label": "Barbul, Max",
        "roles": [],
        "min_level": 1,
    })
    persons = [n for n in g.export(1)[0] if n.get("type") == "person"]
    assert len(persons) == 2


def test_timeline_merges_middle_name_variant():
    pubs = [
        {
            "sogcDate": "2024-01-01",
            "message": (
                "Eingetragene Personen: Barbul, Michael, von Appenzell, in St. Gallen, "
                "Gesellschafter;"
            ),
        },
        {
            "sogcDate": "2025-06-01",
            "message": (
                "Eingetragene Personen: Barbul, Michael Gabriel, von Appenzell, "
                "in St. Gallen, Geschäftsführer und Gesellschafter;"
            ),
        },
    ]
    timeline = build_person_timeline(pubs)
    current = [p for p in timeline if p.get("status") == "current"]
    assert len(current) == 1
    assert current[0]["name"] == "Barbul, Michael Gabriel"
    roles_lower = " ".join(r.lower() for r in (current[0].get("roles") or []))
    assert "gesellschafter" in roles_lower
    assert "geschäftsführer" in roles_lower


def test_timeline_does_not_merge_besnik_and_barbul():
    pubs = [
        {
            "sogcDate": "2022-01-01",
            "message": (
                "Eingetragene Personen: Mahmuti, Besnik, in Oberdorf (BL), "
                "Geschäftsführer und Gesellschafter; Barbul, Michael, in St. Gallen, "
                "Gesellschafter;"
            ),
        },
        {
            "sogcDate": "2026-02-01",
            "message": (
                "Ausgeschiedene Personen: Mahmuti, Besnik, in Oberdorf (BL), "
                "Geschäftsführer und Gesellschafter;"
            ),
        },
        {
            "sogcDate": "2026-04-01",
            "message": (
                "Eingetragene Personen: Barbul, Michael Gabriel, in St. Gallen, "
                "Gesellschafter;"
            ),
        },
    ]
    timeline = build_person_timeline(pubs)
    assert any(
        p.get("status") == "former" and "Besnik" in (p.get("name") or "")
        for p in timeline
    )
    assert any(
        p.get("status") == "current" and "Barbul" in (p.get("name") or "")
        for p in timeline
    )
    assert not any(
        "Besnik" in (p.get("name") or "") and "Barbul" in (p.get("name") or "")
        for p in timeline
    )


def test_case_flag_identity_keys_align():
    """Watchlist full-slug and short node id share the same identity fingerprint."""
    assert person_identity_key("Barbul, Michael Gabriel") == person_identity_key(
        "Barbul, Michael"
    )
    assert person_identity_key("Barbul, Michael Gabriel") == "barbul-michael"
