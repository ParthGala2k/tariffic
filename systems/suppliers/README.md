# Supplier directory (demo)

A stand-in supplier directory served on localhost, so the sourcing agent's Apify scraper has
realistic HTML to read. **The company names, prices and lead times are invented.** In
production the same scraper runs against real directories and marketplaces; nothing else
about the agent changes.

```bash
python systems/suppliers/serve.py              # http://127.0.0.1:3002
python systems/suppliers/serve.py --port 4000
```

## Pages, and what a scraper selects

| Route | Selector to use | What it gives |
|---|---|---|
| `/` | `.card h3 a` | one card per part, linking to its listing |
| `/parts/<part_id>` | `table.supplier-table tr.supplier` | one row per supplier. Each row also carries `data-country`, `data-unit-price` and `data-moq` |
| `/suppliers/<slug>` | `dl.spec dt` / `dd.<field>` | the supplier record: country, city, unit price, MOQ, lead time, certifications, years trading |
| `/api/catalog.json` | — | the whole catalogue, for a scraper that would rather not parse HTML |

Part ids are `PRT-BOARD`, `PRT-FRAME`, `PRT-BURR`. Prices are FOB, in USD, and exclude duty
and freight — the duty is the agent's job to add, from `data/reference/hts_programs.csv`.

## What is in it

13 suppliers across 7 countries, against the three parts Brewline actually buys. Annual
quantities come from the ERP stock layers, so the savings are against real volumes.

| Part | Bought today | Alternatives listed |
|---|---|---|
| Electronic controller board | Shenzhen Boardworks (CN) $200 | India, Vietnam, Taiwan, Mexico, Poland |
| Fabricated steel frame | Ningbo Steelform (CN) $300 | India, Turkey, Mexico, Vietnam |
| Grinder burr set | Milano Grind (IT) $80 | India, Vietnam, Taiwan, Switzerland |

## Three things the data is shaped to teach

**1. The big win is real.** Boards from China carry IEEPA China 20% **plus** Section 301
25% — 45% on top of the price. Those are China-specific, so they vanish entirely if the part
comes from India or Vietnam. Best case: **$34,500 a year** on boards alone.

**2. Steel is a trap.** Section 232 applies to steel goods from **any** country. Moving the
frame out of China removes the 20% China tariff but **not** the 50% steel tariff, so the
saving is far smaller — **$8,268 a year**, not the $30k an agent might assume by analogy
with the boards. An agent that reasons "move it out of China and the tariff goes away" will
overstate this by a factor of four. `tariff_rates_for_origin` exists so it doesn't have to
reason at all.

**3. Not every alternative is cheaper.** Jura Präzision (Switzerland) has no tariff on burrs
but costs $112 against Milano Grind's $92 landed — **$6,300 a year worse**. A recommendation
engine that only ever says "switch" is not giving advice.

## The tariff table this works against

`data/reference/hts_programs.csv` carries `applies_to_origin` **and** `applies_to_hts`, so a
programme can be scoped to a commodity as well as a country:

| Programme | Rate | Origins | Commodity |
|---|---|---|---|
| `IEEPA_CN` | 20% | CN | any |
| `IEEPA_EU` | 15% | IT, PL | any |
| `S232_STEEL` | 50% | **any** | HTS 7326 (steel) |
| `S301_CN` | 25% | CN | HTS 8537 |
| `S122` | 15% | any | any (not in force) |

Those five rows reproduce the duty lines on all ten of Brewline's real customs entries
exactly, which is the test that the table is right.
