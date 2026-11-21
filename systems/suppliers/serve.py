"""A stand-in supplier directory, served on localhost for the sourcing agent to scrape.

DEMO DATA. The companies, prices and lead times in catalog.json are invented. The point is
to give the Apify scraper realistic HTML to read, with the same shape a real directory has:
an index, a page per part, and a detail page per supplier. In production the same scraper
runs against real directories; nothing else about the agent changes.

    python systems/suppliers/serve.py            # http://127.0.0.1:3002
    python systems/suppliers/serve.py --port 4000

Pages, and what a scraper can select:

    GET /                           index: one card per part, linking to its listing
    GET /parts/<part_id>            listing: table.supplier-table, one tr.supplier per row
    GET /suppliers/<slug>           detail: dl.spec with dt/dd pairs
    GET /api/catalog.json           the whole catalog, for a scraper that would rather
                                    not parse HTML
"""
from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CATALOG = Path(__file__).with_name("catalog.json")
STYLE = """
:root{--ink:#1a1a1a;--ink2:#555;--line:#e3e3e3;--bg:#fbfbfa;--accent:#0b5}
*{box-sizing:border-box}
body{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     color:var(--ink);background:var(--bg)}
.wrap{max-width:900px;margin:0 auto;padding:28px 20px 60px}
.demo{background:#fff8e1;border:1px solid #f0d98c;border-radius:6px;padding:10px 13px;
      font-size:13px;color:#6b5600;margin-bottom:22px}
h1{font-size:23px;margin:0 0 4px}h2{font-size:17px;margin:30px 0 10px}
.sub{color:var(--ink2);font-size:13.5px;margin:0 0 22px}
a{color:#06c;text-decoration:none}a:hover{text-decoration:underline}
.card{background:#fff;border:1px solid var(--line);border-radius:7px;padding:15px 17px;
      margin-bottom:11px}
.card h3{margin:0 0 3px;font-size:15.5px}
.meta{color:var(--ink2);font-size:13px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);
      border-radius:7px;overflow:hidden;font-size:14px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}
th{background:#f4f4f3;font-size:12.5px;text-transform:uppercase;letter-spacing:.04em;
   color:var(--ink2);font-weight:600}
tr:last-child td{border-bottom:0}
td.price{font-variant-numeric:tabular-nums;font-weight:600}
dl.spec{background:#fff;border:1px solid var(--line);border-radius:7px;padding:4px 17px;
        margin:0}
dl.spec dt{color:var(--ink2);font-size:12.5px;text-transform:uppercase;
           letter-spacing:.04em;margin-top:13px}
dl.spec dd{margin:2px 0 13px;font-size:15px}
.flag{font-size:12.5px;color:var(--ink2)}
.warn{background:#fff1f0;border:1px solid #f3c2bd;border-radius:6px;padding:10px 13px;
      font-size:13.5px;color:#8a2a20;margin:12px 0 0}
footer{margin-top:40px;color:var(--ink2);font-size:12.5px;border-top:1px solid var(--line);
       padding-top:14px}
"""

COUNTRIES = {"CN": "China", "VN": "Vietnam", "TW": "Taiwan", "MX": "Mexico",
             "IN": "India", "PL": "Poland", "TR": "Turkey", "CH": "Switzerland",
             "IT": "Italy"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load() -> dict:
    return json.loads(CATALOG.read_text())


def page(title: str, body: str) -> bytes:
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title} - Global Parts Directory</title><style>{STYLE}</style>"
            f"</head><body><div class=wrap>{body}"
            f"<footer>Global Parts Directory (demo). Prices are FOB, in USD, and exclude "
            f"duty and freight.</footer></div></body></html>").encode()


def demo_banner(notice: str) -> str:
    return f"<div class=demo><strong>Demo directory.</strong> {notice}</div>"


def index(catalog: dict) -> bytes:
    cards = []
    for part in catalog["parts"]:
        cards.append(
            f"<div class=card><h3><a href='/parts/{part['part_id']}'>{part['name']}</a></h3>"
            f"<div class=meta>HTS {part['hts_code']} &middot; "
            f"{len(part['suppliers'])} suppliers listed &middot; "
            f"currently sourced from {COUNTRIES.get(part['incumbent']['country'], part['incumbent']['country'])}"
            f"</div></div>")
    return page("Index", demo_banner(catalog["_demo_notice"]) +
                "<h1>Global Parts Directory</h1>"
                "<p class=sub>Suppliers of machined and electronic components, by part.</p>"
                + "".join(cards))


def part_page(catalog: dict, part_id: str) -> bytes | None:
    part = next((p for p in catalog["parts"] if p["part_id"] == part_id), None)
    if part is None:
        return None
    rows = []
    for s in sorted(part["suppliers"], key=lambda s: s["unit_price"]):
        rows.append(
            f"<tr class=supplier data-country='{s['country']}' "
            f"data-unit-price='{s['unit_price']:.2f}' data-moq='{s['moq']}'>"
            f"<td class=name><a href='/suppliers/{slug(s['name'])}'>{s['name']}</a></td>"
            f"<td class=country>{COUNTRIES.get(s['country'], s['country'])} "
            f"<span class=flag>({s['country']})</span></td>"
            f"<td class=city>{s['city']}</td>"
            f"<td class=price>{s['unit_price']:.2f}</td>"
            f"<td class=moq>{s['moq']}</td>"
            f"<td class=lead>{s['lead_time_days']} days</td></tr>")
    warn = (f"<div class=warn>{part['note_for_the_agent']}</div>"
            if part.get("note_for_the_agent") else "")
    inc = part["incumbent"]
    return page(part["name"], demo_banner(catalog["_demo_notice"]) +
        f"<h1>{part['name']}</h1>"
        f"<p class=sub>HTS {part['hts_code']} &middot; annual requirement "
        f"{part['annual_quantity']} units &middot; currently bought from "
        f"{inc['supplier']} ({COUNTRIES.get(inc['country'], inc['country'])}) at "
        f"{inc['unit_price']:.2f} per unit</p>"
        f"<table class=supplier-table><thead><tr><th>Supplier</th><th>Country</th>"
        f"<th>City</th><th>Unit price (USD)</th><th>MOQ</th><th>Lead time</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>{warn}"
        f"<p class=sub style='margin-top:18px'><a href='/'>&larr; all parts</a></p>")


def supplier_page(catalog: dict, name_slug: str) -> bytes | None:
    for part in catalog["parts"]:
        for s in part["suppliers"]:
            if slug(s["name"]) != name_slug:
                continue
            spec = {
                "Supplier": s["name"],
                "Country": f"{COUNTRIES.get(s['country'], s['country'])} ({s['country']})",
                "City": s["city"],
                "Part": f"{part['name']} (HTS {part['hts_code']})",
                "Unit price": f"USD {s['unit_price']:.2f} FOB",
                "Minimum order": f"{s['moq']} units",
                "Lead time": f"{s['lead_time_days']} days",
                "Certifications": ", ".join(s["certifications"]),
                "Years trading": str(s["years_trading"]),
            }
            items = "".join(f"<dt>{k}</dt><dd class='{slug(k)}'>{v}</dd>"
                            for k, v in spec.items())
            return page(s["name"], demo_banner(catalog["_demo_notice"]) +
                        f"<h1>{s['name']}</h1>"
                        f"<p class=sub>Supplier record</p><dl class=spec>{items}</dl>"
                        f"<p class=sub style='margin-top:18px'>"
                        f"<a href='/parts/{part['part_id']}'>&larr; other suppliers of "
                        f"{part['name']}</a></p>")
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "GlobalPartsDirectory/1.0"

    def _send(self, body: bytes, status: int = 200, ctype: str = "text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        catalog = load()
        if path == "/":
            return self._send(index(catalog))
        if path == "/api/catalog.json":
            return self._send(json.dumps(catalog, indent=2).encode(), ctype="application/json")
        if path.startswith("/parts/"):
            body = part_page(catalog, path.removeprefix("/parts/"))
            return self._send(body) if body else self._not_found()
        if path.startswith("/suppliers/"):
            body = supplier_page(catalog, path.removeprefix("/suppliers/"))
            return self._send(body) if body else self._not_found()
        return self._not_found()

    def _not_found(self):
        self._send(page("Not found", "<h1>Not found</h1>"
                        "<p class=sub><a href='/'>Back to the index</a></p>"), 404)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3002)
    args = parser.parse_args()

    catalog = load()
    parts = len(catalog["parts"])
    suppliers = sum(len(p["suppliers"]) for p in catalog["parts"])
    print(f"Global Parts Directory (demo data) on http://{args.host}:{args.port}")
    print(f"  {parts} parts, {suppliers} suppliers")
    for part in catalog["parts"]:
        print(f"    http://{args.host}:{args.port}/parts/{part['part_id']}")
    print(f"  JSON: http://{args.host}:{args.port}/api/catalog.json")
    print("  Ctrl-C to stop")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
