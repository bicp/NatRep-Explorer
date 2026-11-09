"""Build data/natrep.db from the Global Nat Rep + Zip Codes workbook.

Usage:  python scripts/build_db.py path/to/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx [data/natrep.db]

Re-run whenever the workbook is updated; the database is rebuilt from scratch.
"""
import json, math, os, re, sqlite3, sys, time
import openpyxl

XLSX = sys.argv[1] if len(sys.argv) > 1 else "Global_Nat_Rep_and_Zip_Codes_Combined.xlsx"
DB = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "data", "natrep.db")

ISO2 = {
 "Argentina":"AR","Australia":"AU","Austria":"AT","Belgium":"BE","Brazil":"BR","Bulgaria":"BG","Canada":"CA","Chile":"CL",
 "China":"CN","Colombia":"CO","Costa Rica":"CR","Croatia":"HR","Cyprus":"CY","Czech Republic":"CZ","Denmark":"DK",
 "Dominican Republic":"DO","Ecuador":"EC","Estonia":"EE","Finland":"FI","France":"FR","Germany":"DE","Greece":"GR",
 "Guatemala":"GT","Hong Kong":"HK","Hungary":"HU","Iceland":"IS","India":"IN","Ireland":"IE","Italy":"IT","Japan":"JP",
 "Latvia":"LV","Lithuania":"LT","Luxembourg":"LU","Malta":"MT","Mexico":"MX","Monaco":"MC","Morocco":"MA",
 "Netherlands":"NL","New Zealand":"NZ","Norway":"NO","Panama":"PA","Peru":"PE","Philippines":"PH","Poland":"PL",
 "Portugal":"PT","Puerto Rico":"PR","Romania":"RO","Russia":"RU","Saudi Arabia":"SA","Serbia":"RS","Singapore":"SG",
 "Slovakia":"SK","Slovenia":"SI","South Africa":"ZA","South Korea":"KR","Spain":"ES","Sweden":"SE","Switzerland":"CH",
 "Taiwan":"TW","Thailand":"TH","Turkey":"TR","United Kingdom":"GB","United States":"US","Uruguay":"UY","Vietnam":"VN",
}
AGE_BANDS = ["18-24", "25-34", "35-44", "45-54", "55+"]


def s(v):
    if v is None: return ""
    if isinstance(v, float) and v.is_integer(): v = int(v)
    return str(v).strip()


def num(v):
    if v is None or isinstance(v, bool): return None
    if isinstance(v, (int, float)): return float(v)
    try: return float(str(v).replace(",", "").replace("%", "").strip())
    except ValueError: return None


def grid(ws, limit=None):
    out = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if limit and i >= limit: break
        out.append(list(r))
    w = max((len(r) for r in out), default=0)
    return [r + [None] * (w - len(r)) for r in out]


def find(g, pred, start=0):
    for i in range(start, len(g)):
        for j, v in enumerate(g[i]):
            if v is not None and pred(s(v)): return i, j
    return None


def blocks(g, r0, r1):
    """Split a rectangular region into tables separated by empty columns, then by empty rows."""
    sub = g[r0:r1]
    if not sub: return []
    w = len(sub[0])
    used = [any(s(r[j]) for r in sub) for j in range(w)]
    groups, cur = [], []
    for j in range(w):
        if used[j]: cur.append(j)
        elif cur: groups.append(cur); cur = []
    if cur: groups.append(cur)
    out = []
    for cols in groups:
        rows = []
        for r in sub:
            vals = [r[j] for j in cols]
            if any(s(v) for v in vals): rows.append(vals)
            elif rows: out.append(rows); rows = []
        if rows: out.append(rows)
    res = []
    for rows in out:
        # trim empty columns inside block
        keep = [k for k in range(len(rows[0])) if any(s(r[k]) for r in rows)]
        rows = [[(r[k] if not isinstance(r[k], str) else r[k].strip()) for k in keep] for r in rows]
        title = None
        if len(rows) > 1 and sum(1 for v in rows[0] if s(v)) == 1:
            title = s(next(v for v in rows[0] if s(v))); rows = rows[1:]
        res.append({"title": title, "rows": [[v if isinstance(v, (int, float)) else s(v) for v in r] for r in rows]})
    return res


def parse_natrep(name, ws):
    g = grid(ws)
    d = {"internet": None, "internet_src": None, "target": None, "age_gender": [], "education": [], "income": [],
         "notes": [], "extras": [], "warnings": []}
    p = find(g, lambda v: v.lower() == "internet penetration")
    if p:
        i, j = p; row = g[i]
        nums = [(k, v) for k, v in enumerate(row) if k > j and num(v) is not None]
        if nums:
            d["internet"] = num(nums[0][1])
            d["internet_src"] = next((s(v) for k, v in enumerate(row) if k > nums[0][0] and s(v)), None)
    p = find(g, lambda v: v.lower() == "target")
    if p:
        d["target"] = next((num(v) for v in g[p[0]][p[1] + 1:] if num(v) is not None), None)
    for gender in ("MALE", "FEMALE"):
        p = find(g, lambda v, gg=gender: v == gg)
        if not p: d["warnings"].append(f"{gender} block not found"); continue
        i, j = p
        for k in range(1, 8):
            lab = s(g[i + k][j]).replace("–", "-")
            if lab in AGE_BANDS:
                d["age_gender"].append({"gender": gender.title(), "age": lab, "share": num(g[i + k][j + 1])})
    # education blocks
    i = 0
    while True:
        p = find(g, lambda v: v.upper().startswith("EDUCATION -"), i)
        if not p: break
        i, j = p
        cls = s(g[i][j]).split("-", 1)[1].strip()
        k = i + 1
        while k < len(g) and s(g[k][j]) and s(g[k][j]).lower() != "total":
            lab, val = s(g[k][j]), num(g[k][j + 1])
            if not lab.lower().startswith(("not available", "source")):
                d["education"].append({"classification": cls, "level": lab, "share": val})
            elif lab.lower().startswith("source"):
                d["notes"].append(lab)
            k += 1
        i += 1
    # income
    p = find(g, lambda v: v == "Income Definition")
    if p:
        hi = p[0]; hdr = {s(v): k for k, v in enumerate(g[hi]) if s(v)}
        bcol = hdr.get("Income Band Local", hdr.get("Band"))
        pcol = hdr.get("% Households", hdr.get("% Population"))
        country = year = cur = defin = None
        k = hi + 1
        while k < len(g) and bcol is not None and s(g[k][bcol]) and s(g[k][bcol]).lower() != "total":
            r = g[k]; get = lambda h: r[hdr[h]] if h in hdr else None
            year = s(get("Year")) or year; cur = s(get("Currency")) or cur; defin = s(get("Income Definition")) or defin
            lo, up = num(get("Lower bound")), num(get("Upper bound"))
            band = s(r[bcol])
            if "Lower bound" in hdr:
                rng = (f"{lo:,.0f}+" if up is None else f"{lo:,.0f}–{up:,.0f}") if lo is not None else ""
                band = f"{band} ({rng} {cur})" if rng else band
            d["income"].append({"band": band, "lower": lo, "upper": up, "share": num(r[pcol]) if pcol is not None else None,
                                "currency": cur, "year": year, "definition": defin, "source": s(get("Source")),
                                "confidence": s(get("Confidence")), "base": s(get("Household Base"))})
            k += 1
    else:
        d["warnings"].append("income table not found")
    # notes + extras
    pn = find(g, lambda v: v.lower().startswith("notes & sources"))
    pa = find(g, lambda v: v.upper().startswith("ADDITIONAL COUNTRY-SPECIFIC"))
    if pn:
        end = pa[0] if pa else len(g)
        for k in range(pn[0] + 1, end):
            t = " ".join(s(v) for v in g[k] if s(v))
            if t: d["notes"].append(t)
    if pa:
        d["extras"] = blocks(g, pa[0] + 1, len(g))
    # checks
    tot = sum(x["share"] or 0 for x in d["age_gender"])
    if d["age_gender"] and abs(tot - 1) > 0.01: d["warnings"].append(f"gender×age sums to {tot:.3f}")
    if len(d["age_gender"]) != 10: d["warnings"].append(f"{len(d['age_gender'])} gender×age cells")
    return d


def table_rows(ws, header_row, limit=None):
    g = grid(ws, limit)
    hdr = [s(v) for v in g[header_row]]
    out = []
    for r in g[header_row + 1:]:
        if not any(s(v) for v in r): continue
        out.append({h: (r[k] if isinstance(r[k], (int, float)) else s(r[k])) for k, h in enumerate(hdr) if h})
    return hdr, out


def main():
    t0 = time.time()
    if os.path.exists(DB): os.remove(DB)
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript("""
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
    CREATE TABLE countries(name TEXT PRIMARY KEY, iso2 TEXT, natrep_sheet TEXT, zip_sheet TEXT, zip_records INT,
        layout TEXT, region_tab TEXT, notes TEXT, internet REAL, internet_src TEXT, target REAL, has_coords INT DEFAULT 0,
        warnings TEXT);
    CREATE TABLE age_gender(country TEXT, gender TEXT, age TEXT, share REAL);
    CREATE TABLE education(country TEXT, classification TEXT, level TEXT, share REAL, ord INT);
    CREATE TABLE income(country TEXT, ord INT, band TEXT, lower REAL, upper REAL, share REAL, currency TEXT, year TEXT,
        definition TEXT, source TEXT, confidence TEXT, base TEXT);
    CREATE TABLE notes(country TEXT, ord INT, text TEXT);
    CREATE TABLE extras(country TEXT, ord INT, source_sheet TEXT, title TEXT, rows_json TEXT);
    CREATE TABLE refs(kind TEXT, country TEXT, data_json TEXT);
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE zips(id INTEGER PRIMARY KEY, country TEXT, iso2 TEXT, zip TEXT, place TEXT, admin1 TEXT, admin2 TEXT,
        admin3 TEXT, lat REAL, lon REAL, extra TEXT);
    """)
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    print("workbook opened", round(time.time() - t0, 1), "s")

    home = grid(wb["Home"])
    con.execute("INSERT INTO meta VALUES('subtitle',?)", (s(home[1][0]),))
    con.execute("INSERT INTO meta VALUES('source_file',?)", (os.path.basename(XLSX),))
    con.execute("INSERT INTO meta VALUES('built',?)", (time.strftime("%Y-%m-%d %H:%M"),))
    countries = []
    hi = next(i for i, r in enumerate(home) if s(r[0]) == "Country")
    for r in home[hi + 1:]:
        if not s(r[0]) or s(r[0]) == "Total": continue
        countries.append(dict(name=s(r[0]), sheet=s(r[1]), zip_sheet=s(r[2]) if s(r[2]) in wb.sheetnames else None,
                              zip_records=num(r[3]), layout=s(r[4]), region_tab=s(r[6]), notes=s(r[7])))

    for c in countries:
        d = parse_natrep(c["name"], wb[c["sheet"]])
        iso = ISO2.get(c["name"])
        con.execute("INSERT INTO countries VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?)",
                    (c["name"], iso, c["sheet"], c["zip_sheet"], c["zip_records"], c["layout"], c["region_tab"], c["notes"],
                     d["internet"], d["internet_src"], d["target"], json.dumps(d["warnings"])))
        con.executemany("INSERT INTO age_gender VALUES(?,?,?,?)", [(c["name"], x["gender"], x["age"], x["share"]) for x in d["age_gender"]])
        con.executemany("INSERT INTO education VALUES(?,?,?,?,?)", [(c["name"], x["classification"], x["level"], x["share"], i) for i, x in enumerate(d["education"])])
        con.executemany("INSERT INTO income VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [(c["name"], i, x["band"], x["lower"], x["upper"], x["share"], x["currency"], x["year"], x["definition"], x["source"], x["confidence"], x["base"]) for i, x in enumerate(d["income"])])
        con.executemany("INSERT INTO notes VALUES(?,?,?)", [(c["name"], i, t) for i, t in enumerate(d["notes"])])
        ex = [(c["name"], i, c["sheet"], b["title"], json.dumps(b["rows"], default=str)) for i, b in enumerate(d["extras"])]
        if c["region_tab"] and c["region_tab"] in wb.sheetnames:
            for b in blocks(grid(wb[c["region_tab"]], 3000), 0, 3000):
                ex.append((c["name"], len(ex), c["region_tab"], b["title"], json.dumps(b["rows"], default=str)))
        con.executemany("INSERT INTO extras VALUES(?,?,?,?,?)", ex)
        if d["warnings"]: print("  !", c["name"], d["warnings"])

    # reference tables
    hdr, rows = table_rows(wb["HHI Comparable"], 1)
    con.executemany("INSERT INTO refs VALUES('hhi',?,?)", [(r.get("Country"), json.dumps(r)) for r in rows if r.get("Country")])
    con.execute("INSERT INTO meta VALUES('hhi_columns',?)", (json.dumps([h for h in hdr if h]),))
    sg = grid(wb["Sources"])
    top, sub = sg[2], sg[3]
    cols, grp = [], ""
    for k in range(len(sub)):
        grp = s(top[k]) or grp
        cols.append(s(sub[k]) or s(top[k]))
    for r in sg[4:]:
        if s(r[0]):
            con.execute("INSERT INTO refs VALUES('sources',?,?)", (s(r[0]), json.dumps({cols[k]: s(v) for k, v in enumerate(r) if cols[k] and s(v)})))

    # zip codes
    total = 0
    by_sheet = {c["zip_sheet"]: c for c in countries if c["zip_sheet"]}
    for sheet, c in by_sheet.items():
        ws = wb[sheet]; iso = ISO2.get(c["name"]); batch = []
        it = ws.iter_rows(min_row=4, values_only=True)
        hdr = [s(v) for v in next(it)]
        for r in it:
            if sheet == "US ZIP":
                if not s(r[0]): continue
                lat = lon = None
                if s(r[15]) and "," in s(r[15]):
                    a, b = s(r[15]).split(",", 1); lat, lon = num(a), num(b)
                extra = {"Population": r[6], "Density": r[7], "Timezone": s(r[14]), "Counties": s(r[10])}
                batch.append((c["name"], iso, s(r[0]), s(r[1]), s(r[3]), s(r[9]), "", lat, lon, json.dumps(extra)))
            elif sheet == "Brazil ZIP":
                if not s(r[3]): continue
                extra = {"Range type": s(r[5]), "Situation": s(r[4])}
                batch.append((c["name"], iso, s(r[3]), s(r[2]), s(r[1]), "", "", None, None, json.dumps(extra, ensure_ascii=False)))
            else:
                if len(r) < 11 or not s(r[1]): continue
                batch.append((c["name"], iso, s(r[1]), s(r[2]), s(r[3]), s(r[5]), s(r[7]), num(r[9]), num(r[10]), None))
            if len(batch) >= 50000:
                con.executemany("INSERT INTO zips(country,iso2,zip,place,admin1,admin2,admin3,lat,lon,extra) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
                total += len(batch); batch = []
        con.executemany("INSERT INTO zips(country,iso2,zip,place,admin1,admin2,admin3,lat,lon,extra) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
        total += len(batch)
        print(f"  {sheet}: done ({total:,} total, {round(time.time() - t0)}s)")

    print("indexing…")
    con.executescript("""
    UPDATE zips SET lat=NULL, lon=NULL WHERE lat IS NULL OR lon IS NULL OR abs(lat)>90 OR abs(lon)>180 OR (lat=0 AND lon=0);
    UPDATE countries SET has_coords=(SELECT count(*)>0 FROM zips z WHERE z.country=countries.name AND z.lat IS NOT NULL);
    CREATE INDEX zips_country ON zips(country);
    CREATE INDEX zips_zip ON zips(zip COLLATE NOCASE);
    CREATE VIRTUAL TABLE zips_geo USING rtree(id, minlat, maxlat, minlon, maxlon);
    INSERT INTO zips_geo SELECT id, lat, lat, lon, lon FROM zips WHERE lat IS NOT NULL;
    CREATE VIRTUAL TABLE zips_fts USING fts5(zip, place, admin1, admin2, admin3, country, content='zips', content_rowid='id', tokenize="unicode61 remove_diacritics 2", prefix='2 3 4');
    INSERT INTO zips_fts(rowid, zip, place, admin1, admin2, admin3, country) SELECT id, zip, place, admin1, admin2, admin3, country FROM zips;
    """)
    con.commit(); con.execute("VACUUM"); con.close()
    print(f"done: {total:,} zip rows, {len(countries)} countries, {round(time.time() - t0)}s, {os.path.getsize(DB) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
