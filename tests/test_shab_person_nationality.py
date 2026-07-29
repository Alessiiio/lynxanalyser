"""SHAB person segment: nationality / Heimatort / staatenlos."""

from app.hr_network.shab_parser import _parse_person_segment


def test_parse_foreign_nationality():
    p = _parse_person_segment(
        "Yücel, Onur, türkischer Staatsangehöriger, in Dietikon, Geschäftsführer und Gesellschafter"
    )
    assert p is not None
    assert p["nationality"] == "türkischer Staatsangehöriger"
    assert p["residence"] == "Dietikon"
    assert p["heimatort"] is None


def test_parse_swiss_heimatort():
    p = _parse_person_segment(
        "Müller, Anna, von Zürich, in Basel, Geschäftsführerin"
    )
    assert p is not None
    assert p["nationality"] is None
    assert p["heimatort"] == "Zürich"
    assert p["residence"] == "Basel"


def test_parse_staatenlos():
    p = _parse_person_segment(
        "Mirzal, Can, staatenlos, in Zürich, Geschäftsführer und Gesellschafter, mit Einzelunterschrift"
    )
    assert p is not None
    assert p["name"] == "Mirzal, Can"
    assert p["nationality"] == "staatenlos"
    assert p["residence"] == "Zürich"
    assert p["heimatort"] is None


def test_parse_staatenlos_mutation_note():
    p = _parse_person_segment(
        "Mirzal, Can, staatenlos, in Zürich, Mitglied des Vorstandes, mit Einzelunterschrift "
        "[bisher: syrischer Staatsangehöriger, Mitglied des Vorstandes, ohne Zeichnungsberechtigung]"
    )
    # [bisher: ...] is stripped by split; here still in segment — nationality must be staatenlos
    assert p is not None
    assert p["nationality"] == "staatenlos"
