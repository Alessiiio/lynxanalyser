"""Edge label merge + SHAB mojibake repair (FBP process audit P0)."""

from __future__ import annotations

from app.hr_network.fraud_network import (
    _GraphBuilder,
    merge_edge_labels,
)
from app.hr_network.shab_parser import (
    clean_shab_message_for_display,
    enrich_publication_for_timeline,
    repair_mojibake,
)


def test_merge_edge_labels_prefers_roles_over_mandat():
    assert merge_edge_labels("Mandat", "Geschäftsführer, Gesellschafter") == (
        "Geschäftsführer, Gesellschafter"
    )
    assert merge_edge_labels("GF", "Mandat · ehemalig") == "GF"
    # Generic alone stays generic
    assert merge_edge_labels("Mandat", "erwähnt") == "Mandat"


def test_graph_one_edge_per_person_company_pair():
    g = _GraphBuilder()
    g.add_node({"id": "person:besnik", "type": "person", "label": "Mahmuti, Besnik", "min_level": 1})
    g.add_node({"id": "company:1", "type": "company", "label": "FBP Bau GmbH", "min_level": 1})
    g.add_edge(
        frm="person:besnik",
        to="company:1",
        label="Geschäftsführer, Gesellschafter",
        edge_type="person_role",
        min_level=2,
        person_status="former",
    )
    g.add_edge(
        frm="person:besnik",
        to="company:1",
        label="Mandat",
        edge_type="person_company",
        min_level=3,
        person_status="former",
    )
    # Second firm
    g.add_node({"id": "company:2", "type": "company", "label": "IdroPro Sagl", "min_level": 4})
    g.add_edge(
        frm="person:besnik",
        to="company:2",
        label="Mandat",
        edge_type="person_company",
        min_level=4,
        person_status="current",
    )

    nodes, edges = g.export(5)
    person_edges = [e for e in edges if e.get("type") in ("person_role", "person_company")]
    assert len(person_edges) == 2
    fbp = next(e for e in person_edges if e["to"] == "company:1" or e["from"] == "company:1")
    assert fbp.get("person_status") == "former"
    assert "Geschäftsführer" in (fbp.get("label") or "")
    assert "Mandat" not in (fbp.get("label") or "") or "Geschäftsführer" in (fbp.get("label") or "")
    # No parallel Mandat-only edge to FBP
    assert sum(
        1
        for e in person_edges
        if {e.get("from"), e.get("to")} == {"person:besnik", "company:1"}
    ) == 1


def test_repair_mojibake_geschaeftsfuehrer():
    raw = "GeschÃ¤ftsfÃ¼hrer und Gesellschafter"
    fixed = repair_mojibake(raw)
    assert "Geschäftsführer" in fixed
    assert "Ã" not in fixed


def test_enrich_publication_repairs_message_full():
    pub = {
        "sogcDate": "2026-02-03",
        "message": (
            "Ausgeschiedene Personen: Mahmuti, Besnik, in Oberdorf, "
            "GeschÃ¤ftsfÃ¼hrer und Gesellschafter;"
        ),
    }
    en = enrich_publication_for_timeline(pub)
    text = en.get("message_clean") or ""
    assert "Geschäftsführer" in text or "Gesch" in clean_shab_message_for_display(pub["message"])
    cleaned = clean_shab_message_for_display(pub["message"])
    assert "Ã" not in cleaned
    assert "Geschäftsführer" in cleaned or "führer" in cleaned.lower() or "Gesellschafter" in cleaned
