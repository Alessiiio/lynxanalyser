"""SHAB person parsing for Italian / French SOGC publications (TI, Romandie)."""

from app.hr_network.shab_parser import (
    build_person_timeline,
    detect_shab_warnings,
    parse_persons_from_message,
    _extract_roles,
    _parse_person_segment,
)


IDROPRO_IT_MSG = (
    "IdroPro Sagl, in Caslano, CHE-337.545.873, c/o Besnik Mahmuti, Via Martelli 16, "
    "6987 Caslano, società a garanzia limitata (Nuova iscrizione). "
    "Data dello statuto: 28.04.2026. Capitale sociale: CHF 20'000.00. "
    "La società rinuncia ad una revisione limitata a partire dal momento della costituzione. "
    "Persone iscritte: Mahmuti, Besnik, cittadino kosovaro, in Caslano, socio e gerente, "
    "con firma individuale, con 20 quote da CHF 1'000.00."
)


def test_extract_roles_italian_socio_gerente():
    roles = _extract_roles("socio e gerente, con firma individuale")
    assert "Gesellschafter" in roles
    assert "Geschäftsführer" in roles


def test_parse_italian_person_segment():
    p = _parse_person_segment(
        "Mahmuti, Besnik, cittadino kosovaro, in Caslano, socio e gerente, "
        "con firma individuale, con 20 quote da CHF 1'000.00"
    )
    assert p is not None
    assert p["name"] == "Mahmuti, Besnik"
    assert p["nationality"] == "cittadino kosovaro"
    assert p["residence"] == "Caslano"
    assert "Gesellschafter" in p["roles"]
    assert "Geschäftsführer" in p["roles"]


def test_parse_persons_from_italian_nuova_iscrizione():
    persons = parse_persons_from_message(IDROPRO_IT_MSG, sogc_date="2026-05-05")
    assert len(persons) == 1
    assert persons[0]["name"] == "Mahmuti, Besnik"
    assert persons[0]["section"] == "current"


def test_timeline_idropro_italian():
    pubs = [{"sogcDate": "2026-05-05", "message": IDROPRO_IT_MSG}]
    tl = build_person_timeline(pubs)
    assert len(tl) == 1
    assert tl[0]["status"] == "current"
    assert tl[0]["id"] == "mahmuti-besnik"
    assert "Gesellschafter" in (tl[0].get("roles") or [])


def test_warnings_detect_italian_persons_and_revision_waiver():
    pubs = [{"sogcDate": "2026-05-05", "message": IDROPRO_IT_MSG}]
    warnings = detect_shab_warnings(pubs)
    assert not any("Keine eingetragenen Personen" in w for w in warnings)
    assert any("Revisionsverzicht" in w for w in warnings)


def test_parse_french_personnes_inscrites():
    msg = (
        "Foo Sàrl, à Lausanne. Personnes inscrites: Dupont, Marie, de nationalité française, "
        "à Lausanne, gérante et associée, avec signature individuelle."
    )
    persons = parse_persons_from_message(msg)
    assert len(persons) == 1
    assert persons[0]["name"] == "Dupont, Marie"
    assert "Geschäftsführerin" in persons[0]["roles"]
    assert "Gesellschafterin" in persons[0]["roles"]
