"""Replace income tables in the workbook with the household income distributions in data/household_income.json.

Usage:  python scripts/write_income_tables.py [path/to/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx]

Each entry of the JSON names a Nat Rep tab ("sheet") and, for tabs with several sections such as GB, the section title
("section": "England"; null for the first, national one). For each entry the script
  1. finds the section's income table header row (the row with an "Income Definition" cell),
  2. deletes every income table on that row, plus helper cells whose formulas read from it (e.g. the band summary
     below the old Eurostat tables),
  3. writes the new table with the standard headers (Country, Year, Currency, Income Definition, Income Band Local,
     % Households, Household Base, Source, Confidence) and a Total row: in the old table's place if the rows are
     free, otherwise to the right of everything else on the tab.
Cell styles are copied from the table it replaces. Re-running with the same JSON gives the same workbook.

Only the XML of the affected sheets changes; everything else in the file stays byte for byte the same. (Saving with
openpyxl would drop the cached results of every formula in the workbook, which scripts/build_db.py reads.)

JSON entry: {"sheet": "Germany", "section": null, "country": "Germany", "year": "2024", "currency": "EUR",
  "definition": "Net monthly household income ...; share of households", "source": "...", "base": "41.3m households",
  "confidence": "High", "bands": [{"label": "Under €1,250", "share": 0.112}, ...]}
"""
import collections, html, json, os, re, shutil, sys, tempfile, zipfile

XLSX = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "Global_Nat_Rep_and_Zip_Codes_Combined.xlsx")
JSON = os.path.join(os.path.dirname(__file__), "..", "data", "household_income.json")
HEAD = ["Country", "Year", "Currency", "Income Definition", "Income Band Local", "% Households", "Household Base", "Source", "Confidence"]
MIN_RIGHT_COL = 17  # when the table can't go in the old place: column Q at the earliest


def col_num(s):
    n = 0
    for ch in s: n = n * 26 + ord(ch) - 64
    return n


def col_name(n):
    s = ""
    while n: n, r = divmod(n - 1, 26); s = chr(65 + r) + s
    return s


class Sheet:
    """The cells of one worksheet's XML as {(row, col): cell xml}, rebuilt in order on save."""
    CELL = re.compile(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)
    ROW = re.compile(r'<row r="(\d+)"([^>]*?)(?:/>|>(.*?)</row>)', re.S)

    def __init__(self, xml, sst):
        self.sst = sst
        m = re.search(r"<sheetData\s*/>|<sheetData>(.*?)</sheetData>", xml, re.S)
        self.head, self.tail = xml[:m.start()], xml[m.end():]
        self.rows, self.cells = {}, {}
        for rm in self.ROW.finditer(m.group(1) or ""):
            r = int(rm.group(1))
            self.rows[r] = re.sub(r'\s+spans="[^"]*"', "", rm.group(2))
            for cm in self.CELL.finditer(rm.group(3) or ""):
                self.cells[(r, col_num(cm.group(1)))] = cm.group(0)

    def attrs(self, rc):
        x = self.cells.get(rc)
        return self.CELL.match(x) if x else None

    def value(self, r, c):
        m = self.attrs((r, c))
        if not m: return None
        a, body = m.group(3), m.group(4) or ""
        t = re.search(r't="(\w+)"', a)
        if t and t.group(1) == "s": return self.sst[int(re.search(r"<v>(\d+)</v>", body).group(1))]
        if t and t.group(1) == "inlineStr": return html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", body, re.S)))
        v = re.search(r"<v>(.*?)</v>", body)
        return html.unescape(v.group(1)) if v else None

    def formula(self, r, c):
        m = self.attrs((r, c))
        f = m and re.search(r"<f[^>]*>(.*?)</f>", m.group(4) or "", re.S)
        return html.unescape(f.group(1)) if f else None

    def style(self, r, c):
        m = self.attrs((r, c))
        s = m and re.search(r's="(\d+)"', m.group(3))
        return s.group(1) if s else None

    def filled(self, r, c):
        v = self.value(r, c)
        return (v not in (None, "")) or bool(self.formula(r, c))

    def put(self, r, c, v, s):
        ref, st = f"{col_name(c)}{r}", f' s="{s}"' if s else ""
        if isinstance(v, tuple): x = f'<c r="{ref}"{st}><f>{html.escape(v[0], quote=False)}</f><v>{v[1]}</v></c>'
        elif isinstance(v, (int, float)): x = f'<c r="{ref}"{st}><v>{v}</v></c>'
        else: x = f'<c r="{ref}"{st} t="inlineStr"><is><t xml:space="preserve">{html.escape(str(v), quote=False)}</t></is></c>'
        self.cells[(r, c)] = x
        self.rows.setdefault(r, "")

    def xml(self):
        by_row = collections.defaultdict(list)
        for (r, c), x in self.cells.items(): by_row[r].append((c, x))
        out = []
        for r in sorted(set(self.rows) | set(by_row)):
            cs = "".join(x for _, x in sorted(by_row.get(r, [])))
            out.append(f'<row r="{r}"{self.rows.get(r, "")}>{cs}</row>' if cs else f'<row r="{r}"{self.rows.get(r, "")}/>')
        maxc = max((c for _, c in self.cells), default=1); maxr = max(self.rows, default=1)
        head = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="A1:{col_name(maxc)}{maxr}"/>', self.head)
        return head + "<sheetData>" + "".join(out) + "</sheetData>" + self.tail


def sections(sh):
    """[(title or None, first_row, end_row)] like build_db.sections: a title in column B with "Internet penetration"
    and "Target" right below it."""
    last = max(sh.rows, default=1)
    starts = [(sh.value(r, 2), r) for r in range(1, last - 1)
              if sh.value(r, 2) and str(sh.value(r + 1, 2)).lower() == "internet penetration" and str(sh.value(r + 2, 2)).lower() == "target"]
    if not starts: return [(None, 1, last + 1)]
    starts[0] = (None, 1)
    return [(t, r, starts[k + 1][1] if k + 1 < len(starts) else last + 1) for k, (t, r) in enumerate(starts)]


def replace_table(sh, e):
    secs = {t: (a, b) for t, a, b in sections(sh)}
    if e.get("section") not in secs: sys.exit(f"{e['sheet']}: no section {e.get('section')!r}")
    a, b = secs[e.get("section")]
    in_row = lambda r: sorted(c for rr, c in sh.cells if rr == r)
    hr = next((r for r in range(a, b) if any(sh.value(r, c) == "Income Definition" for c in in_row(r))), None)
    if hr is None: sys.exit(f"{e['sheet']} {e.get('section') or ''}: no income table")
    heads = [c for c in in_row(hr) if sh.value(hr, c) == "Income Definition"]
    blocks, styles = [], None
    for j in heads:
        c0 = c1 = j
        while c0 > 1 and sh.filled(hr, c0 - 1): c0 -= 1
        while sh.filled(hr, c1 + 1): c1 += 1
        r1 = hr + 1
        while any(sh.filled(r1, c) for c in range(c0, c1 + 1)) and r1 < b: r1 += 1
        hdr = {sh.value(hr, c): c for c in range(c0, c1 + 1)}
        if styles is None:
            band = hdr.get("Income Band Local", hdr.get("Band", c0 + 4)); share = hdr.get("% Households", hdr.get("% Population", c0 + 5))
            styles = dict(head=sh.style(hr, c0), text=sh.style(hr + 1, c0 + 1), band=sh.style(hr + 1, band), share=sh.style(hr + 1, share),
                          total=sh.style(r1 - 1, band), total_share=sh.style(r1 - 1, share))
        blocks.append((c0, c1, hr, r1 - 1))
    dead = {(r, c) for c0, c1, r0, r1 in blocks for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}
    # helper cells elsewhere whose formulas read from the old table, and the header row right above them
    def reads_dead(f):
        return any((int(r), col_num(c)) in dead for c, r in re.findall(r"\$?([A-Z]{1,3})\$?(\d+)", f or ""))
    helpers = [(r, c) for (r, c) in list(sh.cells) if (r, c) not in dead and reads_dead(sh.formula(r, c))]
    if helpers:
        hr0, hr1 = min(r for r, _ in helpers), max(r for r, _ in helpers)
        hc0, hc1 = min(c for _, c in helpers), max(c for _, c in helpers)
        top = hr0 - 1 if any(sh.filled(hr0 - 1, c) for c in range(hc0, hc1 + 1)) else hr0  # its header row
        dead |= {(r, c) for r in range(top, hr1 + 1) for c in range(hc0, hc1 + 1)}
    for rc in dead: sh.cells.pop(rc, None)
    # where to write: the old place if every cell the new table needs there is free, else right of everything in
    # the rows it needs (column Q at the earliest)
    n = len(e["bands"]) + 2
    c0 = blocks[0][0]
    if any(sh.filled(r, c) for r in range(hr, hr + n) for c in range(c0, c0 + len(HEAD))):
        c0 = max([MIN_RIGHT_COL] + [c + 2 for (r, c) in sh.cells if hr <= r < hr + n and sh.filled(r, c)])
    for k, h in enumerate(HEAD): sh.put(hr, c0 + k, h, styles["head"])
    for i, bd in enumerate(e["bands"]):
        r = hr + 1 + i
        vals = [e["country"] if i == 0 else None, e["year"], e["currency"], e["definition"], bd["label"], round(bd["share"], 4),
                e.get("base") if i == 0 else None, e["source"], e.get("confidence", "High")]
        for k, v in enumerate(vals):
            if v not in (None, ""): sh.put(r, c0 + k, v, styles["band"] if k == 4 else styles["share"] if k == 5 else styles["text"])
    t = hr + 1 + len(e["bands"])
    pc = col_name(c0 + 5)
    sh.put(t, c0 + 4, "Total", styles["total"] or styles["band"])
    sh.put(t, c0 + 5, (f"SUM({pc}{hr + 1}:{pc}{t - 1})", round(sum(round(x["share"], 4) for x in e["bands"]), 4)), styles["total_share"] or styles["share"])
    return f"{e['sheet']}{' – ' + e['section'] if e.get('section') else ''}: {len(e['bands'])} bands at {col_name(c0)}{hr}" + (f", removed helper cells {len(helpers)}" if helpers else "")


def main():
    entries = json.load(open(JSON, encoding="utf-8"))
    for e in entries:
        s = sum(x["share"] for x in e["bands"])
        if abs(s - 1) > 0.015: sys.exit(f"{e['country']}: shares sum to {s:.4f}")
    with zipfile.ZipFile(XLSX) as z:
        wbx = z.read("xl/workbook.xml").decode("utf-8")
        rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        sst = [html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
               for si in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8"), re.S)]
        infos = [(i, z.read(i.filename)) for i in z.infolist()]
    def path_of(name):
        tag = next((t for t in re.findall(r"<sheet [^>]*>", wbx) if f'name="{html.escape(name)}"' in t), None)
        if not tag: sys.exit(f"no sheet {name!r}")
        rid = re.search(r'r:id="([^"]+)"', tag).group(1)
        rel = next(t for t in re.findall(r"<Relationship [^>]*>", rels) if f'Id="{rid}"' in t)
        return "xl/" + re.search(r'Target="([^"]+)"', rel).group(1).lstrip("/").removeprefix("xl/")
    raw = {i.filename: d for i, d in infos}
    changed = {}
    for name in dict.fromkeys(e["sheet"] for e in entries):
        p = path_of(name)
        sh = Sheet(raw[p].decode("utf-8"), sst)
        for e in [e for e in entries if e["sheet"] == name]: print(" ", replace_table(sh, e))
        changed[p] = sh.xml().encode("utf-8")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx", dir=os.path.dirname(os.path.abspath(XLSX))).name
    with zipfile.ZipFile(tmp, "w") as out:
        for info, d in infos: out.writestr(info, changed.get(info.filename, d), compress_type=info.compress_type)
    shutil.move(tmp, XLSX)
    print(f"{len(entries)} income tables written on {len(changed)} tabs")


if __name__ == "__main__":
    main()
