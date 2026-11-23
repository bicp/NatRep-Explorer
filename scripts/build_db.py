"""Build data/natrep.db from the Global Nat Rep + Zip Codes workbook and publish it for the browser.

Usage:  python scripts/build_db.py path/to/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx [data/natrep.db] [app/static/db]

Re-run whenever the workbook is updated; the database is rebuilt from scratch and split into chunks in
app/static/db/ (plus config.json), which the browser reads with HTTP range requests.
"""
import collections, csv, gzip, json, math, os, re, sqlite3, sys, time
import openpyxl

XLSX = sys.argv[1] if len(sys.argv) > 1 else "Global_Nat_Rep_and_Zip_Codes_Combined.xlsx"
DB = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "data", "natrep.db")
WEB_DIR = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(__file__), "..", "app", "static", "db")

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
# Some Nat Rep tabs hold extra sections for parts of the country below the national one (the GB tab: England,
# Scotland, Wales, Northern Ireland). Each becomes its own entry, "<country> – <section>", with these codes.
SUB_ISO2 = {"England": "GB-ENG", "Scotland": "GB-SCT", "Wales": "GB-WLS", "Northern Ireland": "GB-NIR"}
AGE_BANDS = ["18-24", "25-34", "35-44", "45-54", "55+"]
# adults by single year of age and sex (UN World Population Prospects), written by scripts/fetch_un_ages.py
UN_AGES = os.path.join(os.path.dirname(__file__), "..", "data", "un_wpp_age_sex.csv")
# the same for the UK and its nations from the ONS mid-year estimates, written by scripts/fetch_ons_uk_ages.py;
# replaces the UN figures for the countries it covers
ONS_AGES = os.path.join(os.path.dirname(__file__), "..", "data", "ons_uk_age_sex.csv")
ONS_AGE_SOURCE = "ONS mid-year population estimates, mid-2024 (table MYE2)"
# Brazil: one point per CEP from IBGE census addresses, per state (data/br_ceps/<UF>.csv.gz, scripts/build_br_ceps.py);
# a state with a file gets those CEPs instead of the workbook's CEP ranges
BR_CEPS = os.path.join(os.path.dirname(__file__), "..", "data", "br_ceps")
BR_CEP_STATES = {f.split(".")[0] for f in os.listdir(BR_CEPS) if f.endswith(".csv.gz")} if os.path.isdir(BR_CEPS) else set()
BR_MUN = os.path.join(os.path.dirname(__file__), "..", "data", "br_municipios.csv")
UK_POSTCODES = os.path.join(os.path.dirname(__file__), "..", "data", "uk_postcodes.csv.gz")
UK_AREAS = os.path.join(os.path.dirname(__file__), "..", "data", "uk_postcode_areas.csv.gz")
# Estimated people around each zip point (GHSL 2025 population grid) and UN average household sizes, written by
# scripts/estimate_population.py. Only the countries in POP_RELEASED get them (released region by region).
ZIP_POP = os.path.join(os.path.dirname(__file__), "..", "data", "zip_population.csv.gz")
HH_SIZE = os.path.join(os.path.dirname(__file__), "..", "data", "household_size.csv")
BR_OCCUPIED = 72_400_000  # occupied private households, IBGE Census 2022
POP_RELEASED = {
    # Europe
    "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GB", "GR", "HR", "HU", "IE", "IS", "IT", "LT",
    "LU", "LV", "MC", "MT", "NL", "NO", "PL", "PT", "RO", "RS", "RU", "SE", "SI", "SK", "TR",
    # Americas
    "AR", "BR", "CA", "CL", "CO", "CR", "DO", "EC", "GT", "MX", "PA", "PE", "PR", "US", "UY",
    # Asia-Pacific\n    "AU", "CN", "JP", "KR", "NZ", "PH", "SG", "TH",
}


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


def sections(g):
    """[(title, first_row, end_row)] of a Nat Rep tab: a title in column B with "Internet penetration" and "Target"
    right below. The first section is the country itself; any further ones are parts of it (e.g. the UK nations)."""
    starts = [(s(g[i][1]), i) for i in range(len(g) - 2)
              if s(g[i][1]) and s(g[i + 1][1]).lower() == "internet penetration" and s(g[i + 2][1]).lower() == "target"]
    if not starts: return [(None, 0, len(g))]
    starts[0] = (None, 0)
    return [(t, i, starts[k + 1][1] if k + 1 < len(starts) else len(g)) for k, (t, i) in enumerate(starts)]


def parse_natrep(name, g):
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
    # income: every table with an "Income Definition" header (a tab can hold several side by side, e.g. the GB tab's
    # DWP HBAI quintiles and FRS household gross income bands); each becomes its own table number (tbl)
    heads = [(i, j) for i in range(len(g)) for j, v in enumerate(g[i]) if s(v) == "Income Definition"]
    if not heads: d["warnings"].append("income table not found")
    for tbl, (hi, j) in enumerate(heads):
        c0 = j
        while c0 > 0 and s(g[hi][c0 - 1]): c0 -= 1
        c1 = j
        while c1 + 1 < len(g[hi]) and s(g[hi][c1 + 1]): c1 += 1
        hdr = {s(g[hi][k]): k for k in range(c0, c1 + 1)}  # only this table's own header cells
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
                                "confidence": s(get("Confidence")), "base": s(get("Household Base")), "tbl": tbl})
            k += 1
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
        warnings TEXT, age_source TEXT);
    CREATE TABLE age_gender(country TEXT, gender TEXT, age TEXT, share REAL);
    CREATE TABLE age_single(country TEXT, age INT, male REAL, female REAL);  -- thousands; age 100 = 100+
    CREATE TABLE education(country TEXT, classification TEXT, level TEXT, share REAL, ord INT);
    CREATE TABLE income(country TEXT, ord INT, band TEXT, lower REAL, upper REAL, share REAL, currency TEXT, year TEXT,
        definition TEXT, source TEXT, confidence TEXT, base TEXT, tbl INT);  -- tbl: which income table of the tab
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

    entries = []  # countries plus their sub-national sections
    for c in countries:
        g = grid(wb[c["sheet"]])
        for title, r0, r1 in sections(g):
            if title is None:
                entries.append((dict(c, iso=ISO2.get(c["name"])), g[r0:r1])); continue
            print(f"  {c['name']}: section {title}")
            entries.append((dict(c, name=f"{c['name']} – {title}", iso=SUB_ISO2.get(title), zip_sheet=None, zip_records=None,
                                 region_tab=None, notes=f"Part of {c['name']}; its zip codes are listed under {c['name']}."),
                            g[r0:r1]))
    for c, g in entries:
        d = parse_natrep(c["name"], g)
        con.execute("INSERT INTO countries VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,NULL)",
                    (c["name"], c["iso"], c["sheet"], c["zip_sheet"], c["zip_records"], c["layout"], c["region_tab"], c["notes"],
                     d["internet"], d["internet_src"], d["target"], json.dumps(d["warnings"])))
        con.executemany("INSERT INTO age_gender VALUES(?,?,?,?)", [(c["name"], x["gender"], x["age"], x["share"]) for x in d["age_gender"]])
        con.executemany("INSERT INTO education VALUES(?,?,?,?,?)", [(c["name"], x["classification"], x["level"], x["share"], i) for i, x in enumerate(d["education"])])
        con.executemany("INSERT INTO income VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [(c["name"], i, x["band"], x["lower"], x["upper"], x["share"], x["currency"], x["year"], x["definition"], x["source"], x["confidence"], x["base"], x["tbl"]) for i, x in enumerate(d["income"])])
        con.executemany("INSERT INTO notes VALUES(?,?,?)", [(c["name"], i, t) for i, t in enumerate(d["notes"])])
        ex = [(c["name"], i, c["sheet"], b["title"], json.dumps(b["rows"], default=str)) for i, b in enumerate(d["extras"])]
        if c["region_tab"] and c["region_tab"] in wb.sheetnames:
            for b in blocks(grid(wb[c["region_tab"]], 3000), 0, 3000):
                ex.append((c["name"], len(ex), c["region_tab"], b["title"], json.dumps(b["rows"], default=str)))
        con.executemany("INSERT INTO extras VALUES(?,?,?,?,?)", ex)
        if d["warnings"]: print("  !", c["name"], d["warnings"])

    # single-year ages for the age splits
    if os.path.exists(UN_AGES):
        with open(UN_AGES, encoding="utf-8") as fh:
            ua = [(r["country"], int(r["age"]), float(r["male_thousands"]), float(r["female_thousands"])) for r in csv.DictReader(fh)]
        con.executemany("INSERT INTO age_single VALUES(?,?,?,?)", ua)
        con.execute("CREATE INDEX age_single_country ON age_single(country, age)")
        con.execute("INSERT INTO meta VALUES('age_source', 'UN World Population Prospects 2024, medium variant, population on 1 July 2025')")
        print(f"  UN single-year ages: {len({r[0] for r in ua})} countries")
    else:
        print("  ! no", UN_AGES, "– run scripts/fetch_un_ages.py; age splits will be empty")
    if os.path.exists(ONS_AGES):
        with open(ONS_AGES, encoding="utf-8") as fh:
            oa = [(r["country"], int(r["age"]), float(r["male_thousands"]), float(r["female_thousands"])) for r in csv.DictReader(fh)]
        names = sorted({r[0] for r in oa})
        unknown = [n for n in names if n not in {c["name"] for c, _ in entries}]
        if unknown: sys.exit(f"{ONS_AGES}: not in the workbook: {', '.join(unknown)}")
        con.executemany("DELETE FROM age_single WHERE country=?", [(n,) for n in names])
        con.executemany("INSERT INTO age_single VALUES(?,?,?,?)", oa)
        con.executemany("UPDATE countries SET age_source=? WHERE name=?", [(ONS_AGE_SOURCE, n) for n in names])
        print(f"  ONS single-year ages: {', '.join(names)}")

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
    br_states = {}
    br_mun = {r["codigo_ibge"]: r["nome"] for r in csv.DictReader(open(BR_MUN, encoding="utf-8"))} if os.path.exists(BR_MUN) else {}
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
                br_states[s(r[0])] = s(r[1])
                if s(r[0]) in BR_CEP_STATES: continue  # this state has one row per CEP instead (added below)
                extra = {"Range type": s(r[5]), "Situation": s(r[4])}
                lat, lon = (num(r[10]), num(r[11])) if len(r) > 11 else (None, None)  # municipal seat (scripts/add_brazil_coords.py)
                batch.append((c["name"], iso, s(r[3]), s(r[2]), s(r[1]), "", "", lat, lon, json.dumps(extra, ensure_ascii=False)))
            else:
                if len(r) < 11 or not s(r[1]): continue
                batch.append((c["name"], iso, s(r[1]), s(r[2]), s(r[3]), s(r[5]), s(r[7]), num(r[9]), num(r[10]), None))
            if len(batch) >= 50000:
                con.executemany("INSERT INTO zips(country,iso2,zip,place,admin1,admin2,admin3,lat,lon,extra) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
                total += len(batch); batch = []
        if sheet == "Brazil ZIP":
            n0 = len(batch)
            for uf in sorted(BR_CEP_STATES):
                with gzip.open(os.path.join(BR_CEPS, f"{uf}.csv.gz"), "rt", encoding="utf-8") as fh:
                    for r in csv.DictReader(fh):
                        town = br_mun.get(r["municipio"], "")
                        whole = r["basis"].startswith("whole town")
                        extra = {"Addresses (Census 2022)": int(r["addresses"]), "Households": int(r["households"]), "Point": r["basis"]}
                        batch.append((c["name"], iso, r["cep"], town if whole else r["localidade"] or town, br_states.get(uf, uf), town,
                                      "" if whole else r["logradouro"], float(r["lat"]), float(r["lon"]), json.dumps(extra, ensure_ascii=False)))
            print(f"  Brazil: {len(batch) - n0:,} CEPs from IBGE census addresses ({', '.join(sorted(BR_CEP_STATES))}), CEP ranges for the other states")
        con.executemany("INSERT INTO zips(country,iso2,zip,place,admin1,admin2,admin3,lat,lon,extra) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
        total += len(batch)
        print(f"  {sheet}: done ({total:,} total, {round(time.time() - t0)}s)")

    # UK: every live postcode -> its point (data/uk_postcodes.csv.gz, scripts/build_uk_postcodes.py), for exact lookups;
    # the zips table keeps the postcode districts
    con.execute("CREATE TABLE uk_postcodes(pc TEXT PRIMARY KEY, postcode TEXT, lat REAL, lon REAL) WITHOUT ROWID")
    if os.path.exists(UK_POSTCODES):
        with gzip.open(UK_POSTCODES, "rt", encoding="utf-8") as fh:
            con.executemany("INSERT OR IGNORE INTO uk_postcodes VALUES(?,?,?,?)",
                            ((r["postcode"].replace(" ", "").upper(), r["postcode"], float(r["lat"]), float(r["lon"])) for r in csv.DictReader(fh)))
        print(f"  UK postcodes: {con.execute('SELECT count(*) FROM uk_postcodes').fetchone()[0]:,} (ONS Postcode Directory)")
    # UK postcode sectors (SS2 6) as zip rows, and district centres (SS2), both the mean of their live postcodes
    # (data/uk_postcode_areas.csv.gz). The workbook's districts list places (villages) with their own points; a district
    # with a single point gets the ONS centre. All district centres also go into uk_postcodes ("SS2"), for the search.
    if os.path.exists(UK_AREAS):
        with gzip.open(UK_AREAS, "rt", encoding="utf-8") as fh: areas = list(csv.DictReader(fh))
        uk_name = next((c["name"] for c in countries if ISO2.get(c["name"]) == "GB" and c["zip_sheet"]), "United Kingdom")
        drows = {}
        for r in con.execute("SELECT id, zip, place, admin1, admin2, lat, lon FROM zips WHERE iso2 = 'GB'"): drows.setdefault(r[1], []).append(r)
        dist = {a["code"]: a for a in areas if a["level"] == "district"}
        moved = [(float(dist[z]["lat"]), float(dist[z]["lon"]), json.dumps({"Point": "district centre (ONS Postcode Directory)"}), rs[0][0])
                 for z, rs in drows.items() if len(rs) == 1 and z in dist]
        con.executemany("UPDATE zips SET lat = ?, lon = ?, extra = ? WHERE id = ?", moved)
        sec = []
        for a in areas:
            if a["level"] != "sector": continue
            la, lo = float(a["lat"]), float(a["lon"])
            rs = [r for r in drows.get(a["code"].split(" ")[0], []) if r[5] is not None]
            near = min(rs, key=lambda r: (r[5] - la) ** 2 + ((r[6] - lo) * math.cos(math.radians(la))) ** 2) if rs else (None, None, "", "", "")
            sec.append((uk_name, "GB", a["code"], near[2], near[3], near[4], "", la, lo,
                        json.dumps({"Level": "postcode sector", "Live postcodes": int(a["postcodes"]), "Place": "nearest place in its district"})))
        con.executemany("INSERT INTO zips(country,iso2,zip,place,admin1,admin2,admin3,lat,lon,extra) VALUES(?,?,?,?,?,?,?,?,?,?)", sec)
        con.executemany("INSERT OR IGNORE INTO uk_postcodes VALUES(?,?,?,?)",
                        [(c, f"{c} (district centre)", float(a["lat"]), float(a["lon"])) for c, a in dist.items()])
        total += len(sec)
        print(f"  UK: {len(sec):,} postcode sectors added, {len(moved):,} single-point districts moved to their ONS centre")

    add_population(con)
    print("indexing…")
    finalize(con)
    con.close()
    export_for_web(DB, WEB_DIR)
    print(f"done: {total:,} zip rows, {len(countries)} countries + {len(entries) - len(countries)} sections, {round(time.time() - t0)}s, {os.path.getsize(DB) / 1e6:.0f} MB")


# The browser reads the database over HTTP, a few pages at a time, so every query must hit an index or a small
# precomputed table. A full table scan would download most of the file.
CELLS_PER_DEG = 10          # radius-search grid: 0.1° cells, cell = lat_row * 3600 + lon_col
# Every page the browser reads costs one HTTP round trip, so pages are as big as SQLite allows (64 KB), and each page
# is its own file. The browser then always asks for whole files, which any static host serves from its cache (Cloudflare
# Pages ignores Range requests). ~9,600 files today; Pages allows 20,000 per deploy, i.e. a database up to ~1.3 GB.
PAGE_SIZE = 65536
CHUNK_BYTES = PAGE_SIZE


def add_population(con):
    """zips.pop / zips.hh: estimated people and households per zip row. Real figures win where the data has them:
    US ZIP population (workbook), Brazil per CEP from its census dwellings (CEPs are far denser than the 1 km population
    grid): households = dwellings scaled to the 72.4 M occupied households of the 2022 census (the address file also
    counts empty and occasional-use dwellings), people = households x the census average household size. UK districts get none (their postcode
    sectors carry the people), so totals over a radius or region never count anyone twice."""
    con.execute("ALTER TABLE zips ADD COLUMN pop INT")
    con.execute("ALTER TABLE zips ADD COLUMN hh INT")
    if not os.path.exists(ZIP_POP): return
    size = {r["iso2"]: float(r["size"]) for r in csv.DictReader(open(HH_SIZE, encoding="utf-8"))} if os.path.exists(HH_SIZE) else {}
    est = {}
    with gzip.open(ZIP_POP, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["iso2"] in POP_RELEASED: est[(r["iso2"], r["zip"], float(r["lat"]), float(r["lon"]))] = int(r["pop"])
    upd, miss = [], collections.Counter()
    br_dwellings = sum(json.loads(ex).get("Households", 0) for (ex,) in con.execute("SELECT extra FROM zips WHERE iso2 = 'BR' AND extra LIKE '%Households%'")) or 1
    for i, iso, z, la, lo, ex in con.execute("SELECT id, iso2, zip, lat, lon, extra FROM zips WHERE lat IS NOT NULL"):
        if iso not in POP_RELEASED: continue
        e = json.loads(ex) if ex else {}
        p = est.get((iso, z, round(la, 5), round(lo, 5)))
        if iso == "US" and num(e.get("Population")) is not None: p = int(num(e["Population"]))
        if p is None:
            if iso != "GB" or e.get("Level") == "postcode sector": miss[iso] += 1
            continue
        if iso == "BR" and "Households" in e:
            h = round(e["Households"] * BR_OCCUPIED / br_dwellings); p = round(h * size["BR"])
        else:
            h = round(p / size[iso]) if iso in size else None
        upd.append((p, h, i))
    con.executemany("UPDATE zips SET pop = ?, hh = ? WHERE id = ?", upd)
    con.execute("INSERT OR REPLACE INTO meta VALUES('pop_countries', ?)", (json.dumps(sorted(POP_RELEASED)),))
    print(f"  population: {len(upd):,} zip rows ({len(POP_RELEASED)} countries)" + (f", unmatched: {dict(miss)}" if miss else ""))


def finalize(con):
    """Clean coordinates, store zips in grid-cell order and build the lookup tables the browser queries."""
    rows, cols = 180 * CELLS_PER_DEG, 360 * CELLS_PER_DEG
    con.executescript(f"""
    UPDATE zips SET lat=NULL, lon=NULL WHERE lat IS NULL OR lon IS NULL OR abs(lat)>90 OR abs(lon)>180 OR (lat=0 AND lon=0);
    UPDATE countries SET has_coords=(SELECT count(*)>0 FROM zips z WHERE z.country=countries.name AND z.lat IS NOT NULL);

    -- nearby zips sit next to each other in the file, so a radius search reads a few contiguous ranges
    CREATE TABLE zips_sorted(id INTEGER PRIMARY KEY, country TEXT, iso2 TEXT, zip TEXT, place TEXT, admin1 TEXT,
        admin2 TEXT, admin3 TEXT, lat REAL, lon REAL, extra TEXT, cell INT, zip_norm TEXT COLLATE NOCASE, zip_len INT, pop INT, hh INT);
    INSERT INTO zips_sorted(country, iso2, zip, place, admin1, admin2, admin3, lat, lon, extra, cell, zip_norm, zip_len, pop, hh)
      SELECT country, iso2, zip, place, admin1, admin2, admin3, lat, lon, extra,
             CASE WHEN lat IS NULL THEN NULL ELSE
               min(CAST((lat + 90) * {CELLS_PER_DEG} AS INT), {rows - 1}) * {cols} + min(CAST((lon + 180) * {CELLS_PER_DEG} AS INT), {cols - 1})
             END AS cell,
             replace(replace(zip, ' ', ''), '-', ''), length(zip),  -- search ignores spaces and hyphens
             pop, hh
      FROM zips ORDER BY cell IS NULL, cell, country, id;
    DROP TABLE zips;
    ALTER TABLE zips_sorted RENAME TO zips;
    CREATE INDEX zips_cell ON zips(cell);
    -- covering index: the zip search reads matches straight from the index instead of one row page per hit
    CREATE INDEX zips_zip ON zips(zip_len, zip_norm, country, iso2, zip, place, admin1, admin2, admin3, lat, lon);
    CREATE INDEX zips_admin1 ON zips(country, admin1);
    CREATE INDEX zips_admin2 ON zips(country, admin2);
    CREATE INDEX zips_place ON zips(country, place);

    -- one row per city / place (country, place, admin1), most zips first so FTS can stop after the first hits
    CREATE TABLE places(id INTEGER PRIMARY KEY, country TEXT, iso2 TEXT, place TEXT COLLATE NOCASE, admin1 TEXT,
        admin2 TEXT, zips INT, lat REAL, lon REAL, first_zip TEXT);
    INSERT INTO places(country, iso2, place, admin1, admin2, zips, lat, lon, first_zip)
      SELECT country, iso2, place, admin1, admin2, count(DISTINCT zip) n, avg(lat), avg(lon), min(zip)
      FROM zips WHERE place <> '' GROUP BY country, place, admin1 ORDER BY n DESC, country, place;
    CREATE INDEX places_place ON places(place, country, id, iso2, admin1, admin2, zips, lat, lon, first_zip);
    CREATE VIRTUAL TABLE places_fts USING fts5(place, country, content='places', content_rowid='id',
        tokenize="unicode61 remove_diacritics 2", prefix='2 3 4');
    INSERT INTO places_fts(rowid, place, country) SELECT id, place, country FROM places;
    INSERT INTO places_fts(places_fts) VALUES('optimize');

    -- state / region and province / county names with record counts
    CREATE TABLE regions(country TEXT, level TEXT, value TEXT COLLATE NOCASE, n INT);
    INSERT INTO regions SELECT country, 'admin1', admin1, count(*) FROM zips WHERE admin1 <> '' GROUP BY country, admin1;
    INSERT INTO regions SELECT country, 'admin2', admin2, count(*) FROM zips WHERE admin2 <> '' GROUP BY country, admin2;
    CREATE INDEX regions_value ON regions(level, value, country, n);
    CREATE INDEX regions_country ON regions(country, level);

    INSERT OR REPLACE INTO meta VALUES('zip_rows', (SELECT count(*) FROM zips));
    INSERT OR REPLACE INTO meta VALUES('zip_lens', (SELECT json_group_array(zip_len) FROM (SELECT DISTINCT zip_len FROM zips ORDER BY 1)));
    INSERT OR REPLACE INTO meta VALUES('cells_per_deg', {CELLS_PER_DEG});
    """)
    # longest run of non-digits a zip starts with (e.g. 3 for Malta's "ATD"): longer word queries skip the zip lookup
    alpha = max((len(re.match(r"\D*", z).group(0)) for (z,) in con.execute("SELECT zip_norm FROM zips")), default=0)
    con.execute("INSERT OR REPLACE INTO meta VALUES('zip_alpha_max', ?)", (alpha,))
    con.commit()
    con.executescript(f"PRAGMA journal_mode=DELETE; PRAGMA page_size={PAGE_SIZE}; VACUUM; ANALYZE;")


def export_for_web(db_path, out_dir):
    """Split the database into one file per page for the browser (sql.js-httpvfs "chunked" mode), plus config.json."""
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("natrep.db.") or f == "config.json":
            os.remove(os.path.join(out_dir, f))
    size = os.path.getsize(db_path)
    digits = max(4, len(str((size - 1) // CHUNK_BYTES)))
    with open(db_path, "rb") as fh:
        k = 0
        while chunk := fh.read(CHUNK_BYTES):
            with open(os.path.join(out_dir, f"natrep.db.{k:0{digits}d}"), "wb") as out:
                out.write(chunk)
            k += 1
    config = {"serverMode": "chunked", "requestChunkSize": PAGE_SIZE, "databaseLengthBytes": size,
              "serverChunkSize": CHUNK_BYTES, "urlPrefix": "natrep.db.", "suffixLength": digits,
              # one page per file, so read-ahead (several pages per request) can't apply; see vendor/sql-httpvfs/PATCHES.md
              "maxReadSpeed": PAGE_SIZE,
              "cacheBust": time.strftime("%Y%m%d%H%M%S")}
    with open(os.path.join(out_dir, "config.json"), "w") as fh:
        json.dump(config, fh)
    print(f"web files: {k} chunks in {out_dir}")

if __name__ == "__main__":
    main()
