# Case Manager: Suspect-Namen für Data Science

**Zweck:** DS erhält nur **Firmen- bzw. Personennamen** (schlanke CSVs). Lynx filtert False Positives **bevor** Namen auf die Watchlist und in den Export gelangen. Namensvarianten und Zahlungstreffer baut der DS.

## Pipeline

1. Fraudfirma (z. B. AB GmbH) → Akte eröffnen / bestätigen oder «In Abklärung»
2. Organe und geprüfte umliegende Firmen → Watchlist
3. Monitoring / L5 / Bulk entdeckt weitere Firmen (z. B. XYZ GmbH) → CM markiert bewusst
4. Admin → **Firmennamen für DS** / **Personennamen für DS**
5. DS variiert Namen → Hits bei Zahlungen an «warme» Firmen

## Betrugsarten (Broschüre)

Beim Bestätigen Pflichtfeld. Labels deutsch; in der DB stabile Codes (`investment_scam`, `phone_scam`, …).

Legacy: `fake_bank_employee` wird zu `phone_scam` (Telefonbetrug) migriert.

## Gates gegen False Positives

| Schritt | Regel |
|--------|--------|
| Bestätigung | Betrugsart wählen; L5-Gate beachten |
| L5-Netz | Nur **aktive Organe** und **klar verdächtige Firmen** markieren — Unbeteiligte nicht übernehmen |
| In Abklärung | Bewusst «warm halten» → Firma+Organe auf WL → Firmenname in DS-Liste |
| Kein Betrug / Akte löschen (ohne Bestätigung) | Auto-`case_open`-Watchlist wird bereinigt |
| Bulk-Scan | Review mit Netz vor «zur Watchlist» |
| Personen-DS-Export | Nur starke Quellen (`fraud_list_officer`, `under_investigation`) |

## Was nicht in die DS-CSV gehört

Keine Betrugsart, UID, Betrag oder Fall-ID — nur der Name. Ausführliche Fraudfirmen-CSV bleibt separat (Admin, intern).
