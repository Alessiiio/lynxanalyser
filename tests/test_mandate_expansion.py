"""Person→company mandate expansion for firm-network L3+ (Zefix/SHAB primary)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.hr_network.moneyhouse_person import (
    firm_names_match,
    hit_lists_seed_firm,
    select_person_hit,
)
from app.hr_network.person_names import names_same_person
from app.hr_network.person_search import parse_person_query
from app.hr_network.zefix_resolve import _pick_search_hit


def test_pick_search_hit_prefers_exact_liquidated_name():
    results = [
        {"name": "Other GmbH", "status": "ACTIVE", "ehraid": 1},
        {"name": "BUGLO GmbH in Liquidation", "status": "BEING_CANCELLED", "ehraid": 2},
    ]
    hit = _pick_search_hit("BUGLO GmbH in Liquidation", results)
    assert hit["ehraid"] == 2


def test_pick_search_hit_active_when_no_exact():
    results = [
        {"name": "Lucio AG", "status": "CANCELLED", "ehraid": 1},
        {"name": "Lucio GmbH", "status": "ACTIVE", "ehraid": 2},
    ]
    hit = _pick_search_hit("Lucio", results)
    assert hit["ehraid"] == 2


def test_firm_names_match_liquidated_and_legal_form():
    assert firm_names_match("FBP Bau GmbH", "FBP Bau GmbH in Liquidation")
    assert firm_names_match("BUGLO GmbH", "BUGLO GmbH in Liquidation")
    assert not firm_names_match("FBP Bau GmbH", "FBO Bau GmbH")  # spelling drift


def _mh_person(first: str, last: str, companies: list[str], city: str = "Zug") -> dict:
    return {
        "id": f"{first}-{last}".lower(),
        "uri": f"/de/person/{first}-{last}".lower(),
        "currentName": {
            "firstName": first,
            "lastName": last,
            "name": f"{first} {last}",
        },
        "currentDomicile": {"city": city},
        "activeMandate": True,
        "relatedCompanies": [{"name": n, "from": "2024-01-01"} for n in companies],
    }


def test_select_prefers_seed_listed_among_homonyms():
    query = parse_person_query("Muster, Max")
    wrong = _mh_person("Max", "Muster", ["Other AG", "Fake GmbH"], city="Bern")
    right = _mh_person(
        "Max", "Muster", ["A GmbH", "Other Mandat AG"], city="Zürich"
    )
    # Wrong hit slightly better on residence if we preferred that alone
    wrong["currentDomicile"] = {"city": "St. Gallen"}
    sel = select_person_hit(
        [wrong, right],
        query=query,
        residence="St. Gallen",
        seed_name="A GmbH",
        seed_uid="CHE-123.456.789",
    )
    assert sel["seed_confirmed"] is True
    assert sel["identity_status"] == "confirmed"
    assert sel["matched_hit"]["id"] == "max-muster"
    assert hit_lists_seed_firm(right, seed_name="A GmbH", seed_uid=None)


def test_select_soft_accept_unique_strong_name_without_seed():
    query = parse_person_query("Barbul, Michael")
    only = _mh_person(
        "Michael", "Barbul", ["Lucio GmbH", "AB Abbruch GmbH"], city="St. Gallen"
    )
    sel = select_person_hit(
        [only],
        query=query,
        residence="St. Gallen",
        seed_name="FBP Bau GmbH",
        seed_uid="CHE-342.140.654",
        display_name="Barbul, Michael",
    )
    assert sel["identity_status"] == "soft"
    assert sel["seed_confirmed"] is False
    assert sel["matched_hit"] is not None
    note = sel.get("note") or ""
    assert "nur dem Namen nach" in note
    assert "FBP Bau GmbH" in note
    assert "Seed-Firma" not in note
    assert "Disambiguierung" not in note
    choices = sel.get("identity_choices") or []
    assert len(choices) == 1
    assert choices[0]["seed_listed"] is False
    assert "Lucio GmbH" in (choices[0].get("related_companies") or [])
    assert choices[0].get("profile_url") == (
        "https://www.moneyhouse.ch/de/person/michael-barbul"
    )


def test_select_rejects_ambiguous_without_seed():
    query = parse_person_query("Müller, Hans")
    a = _mh_person("Hans", "Müller", ["Firma Alpha AG"], city="Zürich")
    b = _mh_person("Hans", "Müller", ["Firma Beta AG"], city="Bern")
    # Distinct MH ids
    a["id"] = "hans-mueller-1"
    b["id"] = "hans-mueller-2"
    sel = select_person_hit(
        [a, b],
        query=query,
        residence=None,
        seed_name="Seed GmbH",
        seed_uid=None,
        display_name="Müller, Hans",
    )
    assert sel["matched_hit"] is None
    assert sel["identity_status"] == "none"
    assert sel["viable_count"] == 2
    note = sel.get("note") or ""
    assert "Mehrere Personen" in note
    assert "Seed-Firma" not in note
    assert "Disambiguierung" not in note
    choices = sel.get("identity_choices") or []
    assert len(choices) == 2
    assert {c["person_key"] for c in choices} == {"hans-mueller-1", "hans-mueller-2"}
    for c in choices:
        assert c.get("profile_url", "").startswith("https://www.moneyhouse.ch/de/person/")


def test_mh_profile_url_from_uri_patterns():
    from app.hr_network.moneyhouse_person import mh_profile_url

    assert (
        mh_profile_url("/de/person/max-muster")
        == "https://www.moneyhouse.ch/de/person/max-muster"
    )
    assert (
        mh_profile_url("https://www.moneyhouse.ch/de/person/max-muster")
        == "https://www.moneyhouse.ch/de/person/max-muster"
    )
    assert (
        mh_profile_url("https://moneyhouse.ch/de/person/max-muster")
        == "https://www.moneyhouse.ch/de/person/max-muster"
    )
    assert mh_profile_url("max-muster") is None
    assert mh_profile_url("") is None
    assert mh_profile_url(None) is None
    assert mh_profile_url("https://evil.example/de/person/x") is None


def test_select_force_mh_person_key():
    query = parse_person_query("Müller, Hans")
    a = _mh_person("Hans", "Müller", ["Firma Alpha AG"], city="Zürich")
    b = _mh_person("Hans", "Müller", ["Firma Beta AG"], city="Bern")
    a["id"] = "hans-mueller-1"
    b["id"] = "hans-mueller-2"
    sel = select_person_hit(
        [a, b],
        query=query,
        seed_name="Seed GmbH",
        force_mh_person_key="hans-mueller-2",
        display_name="Müller, Hans",
    )
    assert sel["identity_status"] == "forced"
    assert sel["matched_hit"]["id"] == "hans-mueller-2"
    assert sel["seed_confirmed"] is False


def test_l3_shab_runs_before_moneyhouse():
    """Product rule: Zefix/SHAB mandate expansion must precede Moneyhouse fill-in."""
    import asyncio

    from app.hr_network import fraud_network as fn

    async def _run() -> list[str]:
        order: list[str] = []
        seed_detail = {
            "name": "FBP Bau GmbH",
            "ehraid": 1546350,
            "uid": "CHE342140654",
            "status": "ACTIVE",
            "canton": "ZG",
            "registryOfCommerceId": 170,
            "legalSeat": "Zug",
            "address": {},
            "sogcPub": [{"sogcDate": "2026-04-20", "mutationTypes": [], "message": "x"}],
            "hasTakenOver": [],
            "wasTakenOverBy": [],
            "branchOffices": [],
            "headOffices": [],
            "furtherHeadOffices": [],
            "auditCompanies": [],
            "oldNames": [],
        }
        timeline = [
            {
                "id": "barbul-michael",
                "name": "Barbul, Michael",
                "roles": ["Gesellschafter"],
                "residence": "St. Gallen",
                "status": "current",
                "first_seen": "2026-04-20",
                "last_seen": "2026-04-20",
                "exited_date": None,
                "source": "shab",
            },
        ]

        def fake_mh(*_a, **_k):
            order.append("moneyhouse")
            return {
                "enabled": True,
                "matched_person": {
                    "name": "Michael Barbul",
                    "seed_confirmed": True,
                    "identity_status": "confirmed",
                },
                "seed_confirmed": True,
                "identity_status": "confirmed",
                "companies": [{"name": "Lucio GmbH", "from": "2026-03-09"}],
            }

        async def fake_shab(names, **_k):
            order.append("shab")
            return {
                "by_person": {},
                "match_count": 0,
                "scanned_months": 0,
                "total_months": 0,
                "search_complete": True,
                "elapsed_seconds": 0.01,
                "years_back": 12,
                "note": None,
            }

        async def fake_resolve(name, uid):
            if uid or (name and "FBP" in (name or "")):
                return seed_detail
            if name == "Lucio GmbH":
                return {
                    "name": "Lucio GmbH",
                    "ehraid": 1400108,
                    "uid": "CHE255996983",
                    "status": "ACTIVE",
                    "legalSeat": "Herisau",
                    "sogcPub": [],
                }
            raise LookupError(name)

        with patch.object(fn.config, "ZEFIX_USERNAME", "x"), patch.object(
            fn.config, "ZEFIX_PASSWORD", "y"
        ), patch.object(
            fn, "resolve_company_detail", side_effect=fake_resolve
        ), patch.object(
            fn, "build_person_timeline", return_value=timeline
        ), patch.object(
            fn, "search_person_mandates", side_effect=fake_mh
        ), patch.object(
            fn, "moneyhouse_person_search_enabled", return_value=True
        ), patch.object(
            fn, "search_persons_batch", new=AsyncMock(side_effect=fake_shab)
        ), patch(
            "app.checks.zefix_mutations.analyze_mutations",
            return_value={
                "warning_flags": [],
                "mutation_analysis": {},
                "publication_count": 1,
            },
        ), patch(
            "app.hr_network.case_flags.annotate_network_with_case_flags",
            new=AsyncMock(side_effect=lambda r: r),
        ):
            await fn.build_fraud_network(
                level=3,
                ad_hoc_company={"name": "FBP Bau GmbH", "uid": "CHE-342.140.654"},
                max_person_searches=4,
            )
        return order

    order = asyncio.run(_run())
    assert "shab" in order and "moneyhouse" in order
    assert order.index("shab") < order.index("moneyhouse"), order


def test_l3_includes_moneyhouse_mandates_for_seed_organ():
    """Seed organ's Moneyhouse relatedCompanies must appear as graph company nodes at L3."""
    import asyncio

    from app.hr_network import fraud_network as fn

    async def _run() -> dict:
        seed_detail = {
            "name": "FBP Bau GmbH",
            "ehraid": 1546350,
            "uid": "CHE342140654",
            "status": "ACTIVE",
            "canton": "ZG",
            "registryOfCommerceId": 170,
            "legalSeat": "Zug",
            "address": {},
            "sogcPub": [{"sogcDate": "2026-04-20", "mutationTypes": [], "message": "x"}],
            "hasTakenOver": [],
            "wasTakenOverBy": [],
            "branchOffices": [],
            "headOffices": [],
            "furtherHeadOffices": [],
            "auditCompanies": [],
            "oldNames": [],
        }

        barbru_person = {
            "id": "barbul-michael",
            "name": "Barbul, Michael",
            "roles": ["Gesellschafter"],
            "residence": "St. Gallen",
            "nationality": None,
            "heimatort": "Appenzell",
            "status": "current",
            "first_seen": "2026-04-20",
            "last_seen": "2026-04-20",
            "exited_date": None,
            "source": "shab",
        }

        related_details = {
            "Lucio GmbH": {
                "name": "Lucio GmbH",
                "ehraid": 1400108,
                "uid": "CHE255996983",
                "status": "ACTIVE",
                "legalSeat": "Herisau",
                "sogcPub": [],
            },
            "AB Abbruch GmbH": {
                "name": "AB Abbruch GmbH",
                "ehraid": 1639562,
                "uid": "CHE179377089",
                "status": "ACTIVE",
                "legalSeat": "St. Gallen",
                "sogcPub": [],
            },
            "BUGLO GmbH in Liquidation": {
                "name": "BUGLO GmbH in Liquidation",
                "ehraid": 1322419,
                "uid": "CHE201324707",
                "status": "BEING_CANCELLED",
                "legalSeat": "St. Gallen",
                "sogcPub": [],
            },
        }

        # Seed listed in MH → identity confirmed (realistic after disambiguation fix).
        mh_payload = {
            "enabled": True,
            "matched_person": {
                "name": "Michael Gabriel Barbul",
                "score": 14.25,
                "seed_confirmed": True,
                "identity_status": "confirmed",
            },
            "seed_confirmed": True,
            "identity_status": "confirmed",
            "companies": [
                {"name": "FBP Bau GmbH", "from": "2026-04-20", "source": "moneyhouse"},
                {"name": "Lucio GmbH", "from": "2026-03-09", "source": "moneyhouse"},
                {"name": "AB Abbruch GmbH", "from": "2026-03-25", "source": "moneyhouse"},
                {
                    "name": "BUGLO GmbH in Liquidation",
                    "from": "2024-03-11",
                    "source": "moneyhouse",
                },
            ],
        }

        async def fake_resolve(name, uid):
            if uid or (name and "FBP" in (name or "")):
                return seed_detail
            if name in related_details:
                return related_details[name]
            raise LookupError(name)

        async def fake_shab(*_a, **_k):
            return {
                "by_person": {},
                "match_count": 0,
                "scanned_months": 0,
                "total_months": 0,
                "search_complete": True,
                "elapsed_seconds": 0.1,
                "years_back": 12,
                "note": None,
            }

        with patch.object(fn.config, "ZEFIX_USERNAME", "x"), patch.object(
            fn.config, "ZEFIX_PASSWORD", "y"
        ), patch.object(
            fn, "resolve_company_detail", side_effect=fake_resolve
        ), patch.object(
            fn, "build_person_timeline", return_value=[barbru_person]
        ), patch.object(
            fn, "search_person_mandates", return_value=mh_payload
        ), patch.object(
            fn, "moneyhouse_person_search_enabled", return_value=True
        ), patch.object(
            fn, "search_persons_batch", new=AsyncMock(side_effect=fake_shab)
        ), patch(
            "app.checks.zefix_mutations.analyze_mutations",
            return_value={
                "warning_flags": [],
                "mutation_analysis": {},
                "publication_count": 1,
            },
        ), patch(
            "app.hr_network.case_flags.annotate_network_with_case_flags",
            new=AsyncMock(side_effect=lambda r: r),
        ):
            return await fn.build_fraud_network(
                level=3,
                ad_hoc_company={"name": "FBP Bau GmbH", "uid": "CHE-342.140.654"},
                max_person_searches=8,
            )

    result = asyncio.run(_run())

    labels = {n.get("label") for n in result["nodes"] if n.get("type") == "company"}
    assert "FBP Bau GmbH" in labels
    assert "Lucio GmbH" in labels
    assert "AB Abbruch GmbH" in labels
    assert "BUGLO GmbH in Liquidation" in labels

    # Liquidated firm must not be dropped
    buglo = next(n for n in result["nodes"] if n.get("label") == "BUGLO GmbH in Liquidation")
    assert buglo.get("status") in ("BEING_CANCELLED", "BEING_CANCELLED".lower()) or "CANCEL" in str(
        buglo.get("status") or ""
    ).upper() or buglo.get("status") == "BEING_CANCELLED"

    ps = result["stats"]["person_search"]
    assert ps["moneyhouse_matches"] >= 3
    assert ps["moneyhouse_seed_confirmed"] >= 1
    assert ps["method"] == "zefix+shab+moneyhouse"

    person = next(n for n in result["nodes"] if n.get("type") == "person")
    assert person.get("moneyhouse_seed_confirmed") is True


def test_l4_former_mandates_not_blocked_by_l3_shab_or_mh():
    """
    After L3 (SHAB then MH) for current organ, L4 formers still expand
    (SHAB primary; order inverted so L3 MH hits never skip formers).
    """
    import asyncio

    from app.hr_network import fraud_network as fn

    async def _run() -> dict:
        seed_detail = {
            "name": "FBP Bau GmbH",
            "ehraid": 1546350,
            "uid": "CHE342140654",
            "status": "ACTIVE",
            "canton": "ZG",
            "registryOfCommerceId": 170,
            "legalSeat": "Zug",
            "address": {},
            "sogcPub": [{"sogcDate": "2026-04-20", "mutationTypes": [], "message": "x"}],
            "hasTakenOver": [],
            "wasTakenOverBy": [],
            "branchOffices": [],
            "headOffices": [],
            "furtherHeadOffices": [],
            "auditCompanies": [],
            "oldNames": [],
        }

        timeline = [
            {
                "id": "barbul-michael",
                "name": "Barbul, Michael",
                "roles": ["Gesellschafter"],
                "residence": "St. Gallen",
                "nationality": None,
                "heimatort": "Appenzell",
                "status": "current",
                "first_seen": "2026-04-20",
                "last_seen": "2026-04-20",
                "exited_date": None,
                "source": "shab",
            },
            {
                "id": "mahmuti-besnik",
                "name": "Mahmuti, Besnik",
                "roles": ["Geschäftsführer", "Gesellschafter"],
                "residence": "Oberdorf (BL)",
                "nationality": None,
                "heimatort": None,
                "status": "former",
                "first_seen": "2022-07-21",
                "last_seen": "2026-02-03",
                "exited_date": "2026-02-03",
                "source": "shab",
            },
        ]

        related = {
            "Lucio GmbH": {
                "name": "Lucio GmbH",
                "ehraid": 1400108,
                "uid": "CHE255996983",
                "status": "ACTIVE",
                "legalSeat": "Herisau",
                "sogcPub": [],
            },
            "IdroPro Sagl": {
                "name": "IdroPro Sagl",
                "ehraid": 1748021,
                "uid": "CHE337545873",
                "status": "ACTIVE",
                "legalSeat": "Lugano",
                "sogcPub": [],
            },
            "B&J DI MAHMUTI": {
                "name": "B&J DI MAHMUTI",
                "ehraid": 1300262,
                "uid": "CHE123456789",
                "status": "ACTIVE",
                "legalSeat": "Lugano",
                "sogcPub": [],
            },
        }

        # Current organ: full MH; former: no MH identity (force SHAB path).
        def fake_mh(display_name, **_k):
            if "Barbul" in (display_name or ""):
                return {
                    "enabled": True,
                    "matched_person": {
                        "name": "Michael Gabriel Barbul",
                        "seed_confirmed": True,
                        "identity_status": "confirmed",
                    },
                    "seed_confirmed": True,
                    "identity_status": "confirmed",
                    "companies": [
                        {"name": "FBP Bau GmbH", "from": "2026-04-20"},
                        {"name": "Lucio GmbH", "from": "2026-03-09"},
                    ],
                }
            return {
                "enabled": True,
                "matched_person": None,
                "seed_confirmed": False,
                "identity_status": "none",
                "companies": [],
                "note": "Keine passende Person in Moneyhouse",
                "viable_count": 0,
            }

        async def fake_resolve(name, uid):
            if uid or (name and "FBP" in (name or "")):
                return seed_detail
            if name in related:
                return related[name]
            raise LookupError(name)

        shab_calls: list = []

        async def fake_shab(names, **_k):
            shab_calls.append(list(names or []))
            out = {
                "by_person": {},
                "match_count": 0,
                "scanned_months": 1,
                "total_months": 1,
                "search_complete": True,
                "elapsed_seconds": 0.05,
                "years_back": 12,
                "note": None,
            }
            for n in names or []:
                if "Besnik" in n:
                    out["by_person"][n] = {
                        "matches": [
                            {
                                "ehraid": 1748021,
                                "name": "IdroPro Sagl",
                                "uid": "CHE-337.545.873",
                                "status": "ACTIVE",
                                "legal_seat": "Lugano",
                                "sogc_date": "2024-01-15",
                                "role_hint": "SHAB-Treffer",
                            },
                            {
                                "ehraid": 1300262,
                                "name": "B&J DI MAHMUTI",
                                "uid": "CHE-123.456.789",
                                "status": "ACTIVE",
                                "legal_seat": "Lugano",
                                "sogc_date": "2023-06-01",
                                "role_hint": "SHAB-Treffer",
                            },
                        ]
                    }
                    out["match_count"] += 2
            return out

        with patch.object(fn.config, "ZEFIX_USERNAME", "x"), patch.object(
            fn.config, "ZEFIX_PASSWORD", "y"
        ), patch.object(
            fn, "resolve_company_detail", side_effect=fake_resolve
        ), patch.object(
            fn, "build_person_timeline", return_value=timeline
        ), patch.object(
            fn, "search_person_mandates", side_effect=fake_mh
        ), patch.object(
            fn, "moneyhouse_person_search_enabled", return_value=True
        ), patch.object(
            fn, "search_persons_batch", new=AsyncMock(side_effect=fake_shab)
        ), patch(
            "app.checks.zefix_mutations.analyze_mutations",
            return_value={
                "warning_flags": [],
                "mutation_analysis": {},
                "publication_count": 1,
            },
        ), patch(
            "app.hr_network.case_flags.annotate_network_with_case_flags",
            new=AsyncMock(side_effect=lambda r: r),
        ):
            result = await fn.build_fraud_network(
                level=4,
                ad_hoc_company={"name": "FBP Bau GmbH", "uid": "CHE-342.140.654"},
                max_person_searches=8,
            )
            return result, shab_calls

    result, shab_calls = asyncio.run(_run())

    # Former expanded via SHAB primary even when current organ had MH at L3
    assert any(any("Besnik" in n for n in batch) for batch in shab_calls), shab_calls

    persons = [n for n in result["nodes"] if n.get("type") == "person"]
    assert len(persons) == 2
    besnik = next(p for p in persons if "Besnik" in (p.get("label") or ""))
    barbul = next(p for p in persons if "Barbul" in (p.get("label") or ""))
    assert besnik.get("person_status") == "former"
    assert barbul.get("person_status") == "current"
    assert not names_same_person(besnik.get("label"), barbul.get("label"))

    companies = {n.get("label") for n in result["nodes"] if n.get("type") == "company"}
    assert "Lucio GmbH" in companies
    assert "IdroPro Sagl" in companies
    assert "B&J DI MAHMUTI" in companies

    edges = result["edges"]
    def _has_edge(person_id_substr: str, company_label: str) -> bool:
        company_ids = {
            n["id"]
            for n in result["nodes"]
            if n.get("type") == "company" and n.get("label") == company_label
        }
        person_ids = {
            n["id"]
            for n in result["nodes"]
            if n.get("type") == "person" and person_id_substr in (n.get("id") or "")
        }
        return any(
            e["from"] in person_ids and e["to"] in company_ids
            or e["to"] in person_ids and e["from"] in company_ids
            for e in edges
        )

    assert _has_edge("barbul", "Lucio GmbH")
    assert _has_edge("besnik", "IdroPro Sagl")
    assert _has_edge("besnik", "B&J DI MAHMUTI")
    # Never: Barbul → IdroPro or Besnik → Lucio
    assert not _has_edge("barbul", "IdroPro Sagl")
    assert not _has_edge("besnik", "Lucio GmbH")

    ps = result["stats"]["person_search"]
    assert ps["shab_matches"] >= 2
    assert ps["moneyhouse_matches"] >= 1


def test_fbp_seed_edge_besnik_person_status_former():
    """API FBP: Besnik→FBP edge carries person_status=former (not only node status)."""
    import asyncio

    from app.hr_network import fraud_network as fn

    async def _run() -> dict:
        seed_detail = {
            "name": "FBP Bau GmbH",
            "ehraid": 1546350,
            "uid": "CHE342140654",
            "status": "ACTIVE",
            "canton": "ZG",
            "registryOfCommerceId": 170,
            "legalSeat": "Zug",
            "address": {},
            "sogcPub": [{"sogcDate": "2026-04-20", "mutationTypes": [], "message": "x"}],
            "hasTakenOver": [],
            "wasTakenOverBy": [],
            "branchOffices": [],
            "headOffices": [],
            "furtherHeadOffices": [],
            "auditCompanies": [],
            "oldNames": [],
        }
        timeline = [
            {
                "id": "mahmuti-besnik",
                "name": "Mahmuti, Besnik",
                "roles": ["Geschäftsführer", "Gesellschafter"],
                "residence": "Oberdorf (BL)",
                "nationality": None,
                "heimatort": None,
                "status": "former",
                "first_seen": "2022-07-21",
                "last_seen": "2026-02-03",
                "exited_date": "2026-02-03",
                "source": "shab",
            },
        ]

        with patch.object(fn.config, "ZEFIX_USERNAME", "x"), patch.object(
            fn.config, "ZEFIX_PASSWORD", "y"
        ), patch.object(
            fn, "resolve_company_detail", new=AsyncMock(return_value=seed_detail)
        ), patch.object(
            fn, "build_person_timeline", return_value=timeline
        ), patch.object(
            fn, "moneyhouse_person_search_enabled", return_value=False
        ), patch(
            "app.checks.zefix_mutations.analyze_mutations",
            return_value={
                "warning_flags": [],
                "mutation_analysis": {},
                "publication_count": 1,
            },
        ), patch(
            "app.hr_network.case_flags.annotate_network_with_case_flags",
            new=AsyncMock(side_effect=lambda r: r),
        ):
            return await fn.build_fraud_network(
                level=2,
                ad_hoc_company={"name": "FBP Bau GmbH", "uid": "CHE-342.140.654"},
                max_person_searches=2,
            )

    result = asyncio.run(_run())
    besnik = next(
        n for n in result["nodes"]
        if n.get("type") == "person" and "Besnik" in (n.get("label") or "")
    )
    assert besnik.get("person_status") == "former"
    fbp_ids = {
        n["id"] for n in result["nodes"]
        if n.get("type") == "company" and "FBP" in (n.get("label") or "")
    }
    edges = [
        e for e in result["edges"]
        if e["from"] == besnik["id"] and e["to"] in fbp_ids
        or e["to"] == besnik["id"] and e["from"] in fbp_ids
    ]
    assert edges, result["edges"]
    assert all(e.get("person_status") == "former" for e in edges)
    mandates = besnik.get("mandates") or []
    assert any(
        m.get("status") == "former" and "FBP" in (m.get("company") or "")
        for m in mandates
    )


def test_idropro_seed_discovers_fbp_as_former_via_shab_despite_mh():
    """
    Bidirectional: seed IdroPro + MH only lists current firms → SHAB still finds
    former FBP and links Besnik→FBP with person_status=former.
    """
    import asyncio
    import re

    from app.hr_network import fraud_network as fn
    from app.hr_network.shab_parser import build_person_timeline as real_timeline

    async def _run() -> dict:
        seed_detail = {
            "name": "IdroPro Sagl",
            "ehraid": 1748021,
            "uid": "CHE337545873",
            "status": "ACTIVE",
            "canton": "TI",
            "registryOfCommerceId": 200,
            "legalSeat": "Lugano",
            "address": {},
            "sogcPub": [{"sogcDate": "2026-05-05", "mutationTypes": [], "message": "x"}],
            "hasTakenOver": [],
            "wasTakenOverBy": [],
            "branchOffices": [],
            "headOffices": [],
            "furtherHeadOffices": [],
            "auditCompanies": [],
            "oldNames": [],
        }
        fbp_detail = {
            "name": "FBP Bau GmbH",
            "ehraid": 1546350,
            "uid": "CHE342140654",
            "status": "ACTIVE",
            "legalSeat": "Zug",
            "sogcPub": [
                {
                    "sogcDate": "2022-07-21",
                    "message": (
                        "Eingetragene Personen: Mahmuti, Besnik, in Oberdorf (BL), "
                        "Geschäftsführer und Gesellschafter;"
                    ),
                },
                {
                    "sogcDate": "2026-02-03",
                    "message": (
                        "Ausgeschiedene Personen: Mahmuti, Besnik, in Oberdorf (BL), "
                        "Geschäftsführer und Gesellschafter;"
                    ),
                },
            ],
        }
        bj_detail = {
            "name": "B&J DI MAHMUTI",
            "ehraid": 1300262,
            "uid": "CHE123456789",
            "status": "ACTIVE",
            "legalSeat": "Lugano",
            "sogcPub": [],
        }
        seed_timeline = [
            {
                "id": "mahmuti-besnik",
                "name": "Mahmuti, Besnik",
                "roles": ["Geschäftsführer"],
                "residence": "Caslano",
                "nationality": None,
                "heimatort": None,
                "status": "current",
                "first_seen": "2026-05-05",
                "last_seen": "2026-05-05",
                "exited_date": None,
                "source": "shab",
            },
        ]

        def timeline_for(pubs):
            # Seed stub: return current organ; FBP pubs → real former status
            if pubs is seed_detail.get("sogcPub") or (
                isinstance(pubs, list)
                and len(pubs) == 1
                and (pubs[0] or {}).get("sogcDate") == "2026-05-05"
                and (pubs[0] or {}).get("message") == "x"
            ):
                return seed_timeline
            return real_timeline(pubs)

        def fake_mh(display_name, **_k):
            return {
                "enabled": True,
                "matched_person": {
                    "name": "Besnik Mahmuti",
                    "seed_confirmed": True,
                    "identity_status": "confirmed",
                },
                "seed_confirmed": True,
                "identity_status": "confirmed",
                # Real MH profile: current only — FBP already gone
                "companies": [
                    {"name": "IdroPro Sagl", "from": "2026-05-05"},
                    {"name": "B&J DI MAHMUTI", "from": "2017-04-03"},
                ],
            }

        async def fake_resolve(name, uid):
            n = (name or "").lower()
            digits = re.sub(r"\D", "", uid or "")
            if "337545873" in digits or "idropro" in n:
                return seed_detail
            if "342140654" in digits or "fbp" in n:
                return fbp_detail
            if "123456789" in digits or "b&j" in n or "mahmuti" in n:
                return bj_detail
            if not name and not uid:
                return seed_detail
            raise LookupError((name, uid))

        shab_calls: list = []

        async def fake_shab(names, **_k):
            shab_calls.append(list(names or []))
            out = {
                "by_person": {},
                "match_count": 0,
                "scanned_months": 1,
                "total_months": 1,
                "search_complete": True,
                "elapsed_seconds": 0.05,
                "years_back": 12,
                "note": None,
            }
            for n in names or []:
                if "Besnik" in n or "Mahmuti" in n:
                    out["by_person"][n] = {
                        "matches": [
                            {
                                "ehraid": 1546350,
                                "name": "FBP Bau GmbH",
                                "uid": "CHE-342.140.654",
                                "status": "ACTIVE",
                                "legal_seat": "Zug",
                                "sogc_date": "2026-02-03",
                                "role_hint": "SHAB-Treffer",
                            },
                        ]
                    }
                    out["match_count"] += 1
            return out

        def fake_zefix_get(path: str):
            if "1546350" in path:
                return fbp_detail
            if "1748021" in path:
                return seed_detail
            if "1300262" in path:
                return bj_detail
            raise LookupError(path)

        with patch.object(fn.config, "ZEFIX_USERNAME", "x"), patch.object(
            fn.config, "ZEFIX_PASSWORD", "y"
        ), patch.object(
            fn, "resolve_company_detail", side_effect=fake_resolve
        ), patch.object(
            fn, "build_person_timeline", side_effect=timeline_for
        ), patch.object(
            fn, "search_person_mandates", side_effect=fake_mh
        ), patch.object(
            fn, "moneyhouse_person_search_enabled", return_value=True
        ), patch.object(
            fn, "search_persons_batch", new=AsyncMock(side_effect=fake_shab)
        ), patch.object(
            fn, "_zefix_get", side_effect=fake_zefix_get
        ), patch(
            "app.checks.zefix_mutations.analyze_mutations",
            return_value={
                "warning_flags": [],
                "mutation_analysis": {},
                "publication_count": 1,
            },
        ), patch(
            "app.hr_network.case_flags.annotate_network_with_case_flags",
            new=AsyncMock(side_effect=lambda r: r),
        ):
            result = await fn.build_fraud_network(
                level=3,
                ad_hoc_company={"name": "IdroPro Sagl", "uid": "CHE-337.545.873"},
                max_person_searches=4,
            )
            return result, shab_calls

    result, shab_calls = asyncio.run(_run())

    # SHAB runs first (Zefix primary); MH confirmed never replaces/skips SHAB
    assert any(
        any("Besnik" in n or "Mahmuti" in n for n in batch)
        for batch in shab_calls
    ), shab_calls

    companies = {n.get("label") for n in result["nodes"] if n.get("type") == "company"}
    assert "FBP Bau GmbH" in companies, companies
    assert "B&J DI MAHMUTI" in companies or any("B&J" in (c or "") for c in companies)

    besnik = next(
        n for n in result["nodes"]
        if n.get("type") == "person" and "Besnik" in (n.get("label") or "")
    )
    # Seed-centric node status remains current at IdroPro
    assert besnik.get("person_status") == "current"

    fbp_ids = {
        n["id"] for n in result["nodes"]
        if n.get("type") == "company" and "FBP" in (n.get("label") or "")
    }
    edges = [
        e for e in result["edges"]
        if (e["from"] == besnik["id"] and e["to"] in fbp_ids)
        or (e["to"] == besnik["id"] and e["from"] in fbp_ids)
    ]
    assert edges, "missing Besnik↔FBP edge"
    assert any(e.get("person_status") == "former" for e in edges), edges

    mandates = besnik.get("mandates") or []
    assert any(
        m.get("status") == "former" and "FBP" in (m.get("company") or "")
        for m in mandates
    ), mandates


def test_soft_mh_still_runs_shab_for_formers():
    """SHAB runs before soft MH identity; soft MH waits for user confirm (no auto-import)."""
    import asyncio

    from app.hr_network import fraud_network as fn

    async def _run() -> dict:
        seed_detail = {
            "name": "FBP Bau GmbH",
            "ehraid": 1546350,
            "uid": "CHE342140654",
            "status": "ACTIVE",
            "canton": "ZG",
            "registryOfCommerceId": 170,
            "legalSeat": "Zug",
            "address": {},
            "sogcPub": [{"sogcDate": "2026-04-20", "mutationTypes": [], "message": "x"}],
            "hasTakenOver": [],
            "wasTakenOverBy": [],
            "branchOffices": [],
            "headOffices": [],
            "furtherHeadOffices": [],
            "auditCompanies": [],
            "oldNames": [],
        }
        timeline = [
            {
                "id": "mahmuti-besnik",
                "name": "Mahmuti, Besnik",
                "roles": ["Geschäftsführer"],
                "residence": "Oberdorf (BL)",
                "status": "former",
                "first_seen": "2022-07-21",
                "last_seen": "2026-02-03",
                "exited_date": "2026-02-03",
                "source": "shab",
            },
        ]
        related = {
            "IdroPro Sagl": {
                "name": "IdroPro Sagl",
                "ehraid": 1748021,
                "uid": "CHE337545873",
                "status": "ACTIVE",
                "legalSeat": "Lugano",
                "sogcPub": [],
            },
        }
        shab_calls: list = []

        def fake_mh(*_a, **_k):
            return {
                "enabled": True,
                "matched_person": {
                    "name": "Besnik Mahmuti",
                    "person_key": "besnik-mahmuti",
                    "seed_confirmed": False,
                    "identity_status": "soft",
                },
                "seed_confirmed": False,
                "identity_status": "soft",
                "companies": [
                    {"name": "IdroPro Sagl", "from": "2026-05-05"},
                ],
                "note": (
                    "Moneyhouse-Profil passt nur dem Namen nach; Firma «FBP Bau GmbH» "
                    "steht dort nicht. Übernahmen sind unsicher."
                ),
                "viable_count": 1,
                "identity_choices": [
                    {
                        "person_key": "besnik-mahmuti",
                        "name": "Besnik Mahmuti",
                        "related_companies": ["IdroPro Sagl"],
                        "seed_listed": False,
                    }
                ],
            }

        async def fake_resolve(name, uid):
            if uid or (name and "FBP" in (name or "")):
                return seed_detail
            if name in related:
                return related[name]
            raise LookupError(name)

        async def fake_shab(names, **_k):
            shab_calls.append(list(names or []))
            return {
                "by_person": {},
                "match_count": 0,
                "scanned_months": 1,
                "total_months": 1,
                "search_complete": True,
                "elapsed_seconds": 0.02,
                "years_back": 12,
                "note": None,
            }

        with patch.object(fn.config, "ZEFIX_USERNAME", "x"), patch.object(
            fn.config, "ZEFIX_PASSWORD", "y"
        ), patch.object(
            fn, "resolve_company_detail", side_effect=fake_resolve
        ), patch.object(
            fn, "build_person_timeline", return_value=timeline
        ), patch.object(
            fn, "search_person_mandates", side_effect=fake_mh
        ), patch.object(
            fn, "moneyhouse_person_search_enabled", return_value=True
        ), patch.object(
            fn, "search_persons_batch", new=AsyncMock(side_effect=fake_shab)
        ), patch(
            "app.checks.zefix_mutations.analyze_mutations",
            return_value={
                "warning_flags": [],
                "mutation_analysis": {},
                "publication_count": 1,
            },
        ), patch(
            "app.hr_network.case_flags.annotate_network_with_case_flags",
            new=AsyncMock(side_effect=lambda r: r),
        ):
            result = await fn.build_fraud_network(
                level=4,
                ad_hoc_company={"name": "FBP Bau GmbH", "uid": "CHE-342.140.654"},
                max_person_searches=4,
            )
            return result, shab_calls

    result, shab_calls = asyncio.run(_run())
    assert shab_calls, "SHAB must run (primary path) even with soft MH"
    companies = {n.get("label") for n in result["nodes"] if n.get("type") == "company"}
    # Soft match waits for user confirmation — no auto-import of MH firms
    assert "IdroPro Sagl" not in companies
    ps = result.get("stats", {}).get("person_search") or {}
    choices = ps.get("identity_choices") or []
    assert choices, "soft identity must surface identity_choices"
    assert choices[0]["status"] == "soft"
    assert choices[0].get("can_accept_soft") is True
    assert choices[0].get("soft_person_key") == "besnik-mahmuti"
    warns = " ".join(ps.get("identity_warnings") or [])
    assert "nur dem Namen nach" in warns


def test_identity_force_imports_mh_companies():
    """User confirm (force_mh_person) imports related firms via Zefix."""
    import asyncio

    from app.hr_network import fraud_network as fn

    async def _run() -> dict:
        seed_detail = {
            "name": "BKurti.ch GmbH",
            "ehraid": 1001,
            "uid": "CHE111111111",
            "status": "ACTIVE",
            "canton": "ZG",
            "registryOfCommerceId": 170,
            "legalSeat": "Zug",
            "address": {},
            "sogcPub": [{"sogcDate": "2025-01-01", "mutationTypes": [], "message": "x"}],
            "hasTakenOver": [],
            "wasTakenOverBy": [],
            "branchOffices": [],
            "headOffices": [],
            "furtherHeadOffices": [],
            "auditCompanies": [],
            "oldNames": [],
        }
        timeline = [
            {
                "id": "kurti-besnik",
                "name": "Kurti, Besnik",
                "roles": ["Gesellschafter"],
                "residence": "Zug",
                "status": "current",
                "first_seen": "2025-01-01",
                "last_seen": "2025-01-01",
                "exited_date": None,
                "source": "shab",
            },
        ]
        related = {
            "Alpha Holding AG": {
                "name": "Alpha Holding AG",
                "ehraid": 2002,
                "uid": "CHE222222222",
                "status": "ACTIVE",
                "legalSeat": "Zürich",
                "sogcPub": [],
            },
        }
        mh_calls: list[dict] = []

        def fake_mh(display_name, **kwargs):
            mh_calls.append({"name": display_name, **kwargs})
            force = kwargs.get("force_mh_person_key")
            if force == "besnik-kurti-2":
                return {
                    "enabled": True,
                    "matched_person": {
                        "name": "Besnik Kurti",
                        "person_key": "besnik-kurti-2",
                        "identity_status": "forced",
                        "seed_confirmed": False,
                    },
                    "identity_status": "forced",
                    "seed_confirmed": False,
                    "companies": [{"name": "Alpha Holding AG", "from": "2024-01-01"}],
                    "identity_choices": [],
                    "viable_count": 2,
                }
            return {
                "enabled": True,
                "matched_person": None,
                "identity_status": "none",
                "seed_confirmed": False,
                "companies": [],
                "note": (
                    "Mehrere Personen mit dem Namen Kurti, Besnik auf Moneyhouse — "
                    "keiner führt die analysierte Firma «BKurti.ch GmbH». "
                    "Bitte selbst zuordnen oder ignorieren."
                ),
                "viable_count": 2,
                "identity_choices": [
                    {
                        "person_key": "besnik-kurti-1",
                        "name": "Besnik Kurti",
                        "related_companies": ["Other AG"],
                        "seed_listed": False,
                    },
                    {
                        "person_key": "besnik-kurti-2",
                        "name": "Besnik Kurti",
                        "related_companies": ["Alpha Holding AG"],
                        "seed_listed": False,
                    },
                ],
            }

        async def fake_resolve(name, uid):
            if uid or (name and "BKurti" in (name or "")):
                return seed_detail
            if name in related:
                return related[name]
            raise LookupError(name)

        async def fake_shab(*_a, **_k):
            return {
                "by_person": {},
                "match_count": 0,
                "scanned_months": 1,
                "total_months": 1,
                "search_complete": True,
                "elapsed_seconds": 0.01,
                "years_back": 12,
                "note": None,
            }

        with patch.object(fn.config, "ZEFIX_USERNAME", "x"), patch.object(
            fn.config, "ZEFIX_PASSWORD", "y"
        ), patch.object(
            fn, "resolve_company_detail", side_effect=fake_resolve
        ), patch.object(
            fn, "build_person_timeline", return_value=timeline
        ), patch.object(
            fn, "search_person_mandates", side_effect=fake_mh
        ), patch.object(
            fn, "moneyhouse_person_search_enabled", return_value=True
        ), patch.object(
            fn, "search_persons_batch", new=AsyncMock(side_effect=fake_shab)
        ), patch(
            "app.checks.zefix_mutations.analyze_mutations",
            return_value={
                "warning_flags": [],
                "mutation_analysis": {},
                "publication_count": 1,
            },
        ), patch(
            "app.hr_network.case_flags.annotate_network_with_case_flags",
            new=AsyncMock(side_effect=lambda r: r),
        ):
            # First pass: ambiguous → choices, no new firm
            ambiguous = await fn.build_fraud_network(
                level=3,
                ad_hoc_company={"name": "BKurti.ch GmbH", "uid": "CHE-111.111.111"},
                max_person_searches=4,
            )
            # Confirm candidate 2
            confirmed = await fn.build_fraud_network(
                level=3,
                ad_hoc_company={"name": "BKurti.ch GmbH", "uid": "CHE-111.111.111"},
                max_person_searches=4,
                identity_overrides=[
                    {
                        "action": "accept",
                        "person_name": "Kurti, Besnik",
                        "moneyhouse_person_key": "besnik-kurti-2",
                    }
                ],
            )
            return ambiguous, confirmed, mh_calls

    ambiguous, confirmed, mh_calls = asyncio.run(_run())
    amb_ps = ambiguous.get("stats", {}).get("person_search") or {}
    choices = amb_ps.get("identity_choices") or []
    assert len(choices) == 1
    assert choices[0]["status"] == "ambiguous"
    assert len(choices[0].get("candidates") or []) == 2
    companies_amb = {
        n.get("label") for n in ambiguous["nodes"] if n.get("type") == "company"
    }
    assert "Alpha Holding AG" not in companies_amb

    assert any(c.get("force_mh_person_key") == "besnik-kurti-2" for c in mh_calls)
    companies_ok = {
        n.get("label") for n in confirmed["nodes"] if n.get("type") == "company"
    }
    assert "Alpha Holding AG" in companies_ok
    conf_ps = confirmed.get("stats", {}).get("person_search") or {}
    assert int(conf_ps.get("moneyhouse_matches") or 0) >= 1


def test_apply_identity_confirmation_merges_without_shab_rebuild():
    """Confirm identity: MH→Zefix for one person only; no full person-search walk."""
    import asyncio

    import app.hr_network.fraud_network as fn

    base = {
        "level": 4,
        "nodes": [
            {
                "id": "company:100",
                "type": "company",
                "label": "Seed GmbH",
                "ehraid": 100,
                "uid": "CHE-100.100.100",
                "is_seed": True,
                "min_level": 1,
            },
            {
                "id": "person:besnik",
                "type": "person",
                "label": "Besnik Mahmuti",
                "min_level": 1,
                "moneyhouse_identity_status": "soft",
                "identity_warning": "unsicher",
                "mandates": [
                    {
                        "company": "Seed GmbH",
                        "uid": "CHE-100.100.100",
                        "ehraid": 100,
                        "status": "current",
                    }
                ],
            },
        ],
        "edges": [
            {
                "from": "person:besnik",
                "to": "company:100",
                "label": "Geschäftsführer",
                "type": "person_role",
                "min_level": 1,
                "person_status": "current",
            }
        ],
        "seed_companies": [
            {
                "name": "Seed GmbH",
                "uid": "CHE-100.100.100",
                "ehraid": 100,
            }
        ],
        "persons_table": [
            {
                "person_id": "besnik",
                "name": "Besnik Mahmuti",
                "status": "current",
                "seed_company": "Seed GmbH",
                "seed_uid": "CHE-100.100.100",
                "mandates": [
                    {
                        "company": "Seed GmbH",
                        "uid": "CHE-100.100.100",
                        "ehraid": 100,
                    }
                ],
            }
        ],
        "stats": {
            "person_search": {
                "identity_choices": [
                    {
                        "person_name": "Besnik Mahmuti",
                        "person_id": "besnik",
                        "status": "soft",
                        "message": "soft match",
                        "soft_person_key": "besnik-key",
                        "candidates": [
                            {
                                "person_key": "besnik-key",
                                "name": "Besnik Mahmuti",
                                "related_companies": ["IdroPro Sagl"],
                            }
                        ],
                    }
                ],
                "identity_warnings": ["Besnik Mahmuti: soft match"],
                "moneyhouse_matches": 0,
                "matches": 0,
            }
        },
    }

    shab_called = {"n": 0}

    def fake_mh(display_name, **kwargs):
        assert kwargs.get("force_mh_person_key") == "besnik-key"
        return {
            "enabled": True,
            "matched_person": {
                "name": "Besnik Mahmuti",
                "person_key": "besnik-key",
                "identity_status": "forced",
            },
            "companies": [{"name": "IdroPro Sagl", "from": "2026-05-05"}],
            "identity_status": "forced",
            "seed_confirmed": False,
        }

    async def fake_resolve(name, uid):
        if name and "IdroPro" in name:
            return {
                "name": "IdroPro Sagl",
                "ehraid": 999,
                "uid": "CHE999999999",
                "status": "ACTIVE",
                "legalSeat": "Lugano",
            }
        raise LookupError(name)

    async def fail_shab(*_a, **_k):
        shab_called["n"] += 1
        raise AssertionError("SHAB must not run on incremental confirm")

    async def _run():
        with patch.object(fn, "search_person_mandates", side_effect=fake_mh), patch.object(
            fn, "resolve_company_detail", side_effect=fake_resolve
        ), patch.object(
            fn, "moneyhouse_person_search_enabled", return_value=True
        ), patch.object(
            fn, "search_persons_batch", new=AsyncMock(side_effect=fail_shab)
        ), patch(
            "app.hr_network.case_flags.annotate_network_with_case_flags",
            new=AsyncMock(side_effect=lambda r: r),
        ):
            accepted = await fn.apply_identity_confirmation(
                base=base,
                level=4,
                person_name="Besnik Mahmuti",
                person_id="besnik",
                moneyhouse_person_key="besnik-key",
                action="accept",
            )
            ignored = await fn.apply_identity_confirmation(
                base=base,
                level=4,
                person_name="Besnik Mahmuti",
                person_id="besnik",
                moneyhouse_person_key=None,
                action="ignore",
            )
            return accepted, ignored

    accepted, ignored = asyncio.run(_run())
    assert shab_called["n"] == 0
    assert accepted.get("incremental_identity") is True
    companies = {
        n.get("label") for n in accepted["nodes"] if n.get("type") == "company"
    }
    assert "IdroPro Sagl" in companies
    ps = accepted["stats"]["person_search"]
    assert not ps.get("identity_choices")
    assert int(ps.get("moneyhouse_matches") or 0) >= 1
    person = next(
        n for n in accepted["nodes"] if n.get("type") == "person"
    )
    assert person.get("moneyhouse_identity_status") == "forced"
    assert ignored.get("identity_action") == "ignore"
    assert not (ignored["stats"]["person_search"].get("identity_choices") or [])
    ignore_companies = {
        n.get("label") for n in ignored["nodes"] if n.get("type") == "company"
    }
    assert "IdroPro Sagl" not in ignore_companies
