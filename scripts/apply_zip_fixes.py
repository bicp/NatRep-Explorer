"""Write corrected zip coordinates (data/zip_coord_fixes.csv) into the zip tabs of the workbook.

Usage:  python scripts/apply_zip_fixes.py [fixes.csv] [workbook.xlsx]

Each CSV row names a tab ("sheet"), a worksheet row ("row"), the coordinates it should have now (old_lat/old_lon) and
the new ones (lat/lon). The script sets columns J (latitude) and K (longitude) of that row, but only where the cell
still holds the old value (or a value from an earlier fix of the same row: rows fixed twice end at the last one); a row that already has the new value is skipped, any other value stops the script. Running
it twice therefore changes nothing the second time. Each changed tab's note (cell B2) gets one line saying how many
coordinates were replaced and from which source.

Like write_income_tables.py it patches the sheet XML inside the .xlsx and leaves every other part byte for byte the
same (saving with openpyxl would drop the cached formula results that build_db.py reads).
"""
import collections, csv, html, os, re, shutil, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIXES = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "zip_coord_fixes.csv")
XLSX = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "data", "Global_Nat_Rep_and_Zip_Codes_Combined.xlsx")
CELL = re.compile(r'<c r="([JK])(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)
NOTE = re.compile(r'<c r="B2"([^>]*?)(?:/>|>(.*?)</c>)', re.S)
TAG = "Nov 2026:"


def fmt(v):
    return repr(round(float(v), 6))


def main():
    fixes = collections.defaultdict(lambda: collections.defaultdict(list))  # sheet -> row -> fixes in file order
    sources = collections.defaultdict(collections.Counter)
    for r in csv.DictReader(open(FIXES, encoding="utf-8")):
        fixes[r["sheet"]][int(r["row"])].append(r)
        sources[r["sheet"]][r["source"]] += 1
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
    for sheet, rows in fixes.items():
        p = path_of(sheet)
        xml = raw[p].decode("utf-8")
        n = {"set": 0, "already": 0}; touched = set()

        def sub(m):
            col, r = m.group(1), int(m.group(2))
            chain = rows.get(r)
            if not chain: return m.group(0)
            cur = re.search(r"<v>(.*?)</v>", m.group(4) or "")
            cur = float(cur.group(1)) if cur else None
            k = ("old_lat", "lat") if col == "J" else ("old_lon", "lon")
            new = chain[-1][k[1]]  # a row fixed twice (e.g. decimal point, then moved) ends at the last value
            if cur is not None and abs(cur - float(new)) < 1e-6:
                n["already"] += 1; return m.group(0)
            if cur is None or all(abs(cur - float(f[x])) > 1e-6 for f in chain for x in k):
                sys.exit(f"{sheet} {col}{r}: expected {chain[0][k[0]]}, found {cur} – workbook changed since the fixes were made")
            n["set"] += 1; touched.add(r)
            attrs = re.sub(r'\s*t="[^"]*"', "", m.group(3))
            return f'<c r="{col}{r}"{attrs}><v>{fmt(new)}</v></c>'

        xml = CELL.sub(sub, xml)
        if n["set"] + n["already"] != 2 * len(rows): sys.exit(f"{sheet}: found {n['set'] + n['already']} of {2 * len(rows)} coordinate cells")
        if n["set"]:
            m = NOTE.search(xml)
            a, body = m.group(1), m.group(2) or ""
            t = re.search(r't="(\w+)"', a)
            note = sst[int(re.search(r"<v>(\d+)</v>", body).group(1))] if t and t.group(1) == "s" else html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", body, re.S)))
            note = re.sub(rf" · {re.escape(TAG)} [^·]*", "", note)  # replace this script's earlier line, if any
            src = "; ".join(f"{k}: {v:,}" for k, v in sources[sheet].items())
            note += f" · {TAG} {len(rows):,} coordinates corrected (wrongly restored decimal points, scrambled rows, misplaced places) – {src}; list in data/zip_coord_fixes.csv"
            a = re.sub(r'\s*t="[^"]*"', "", a)
            xml = xml[:m.start()] + f'<c r="B2"{a} t="inlineStr"><is><t xml:space="preserve">{html.escape(note, quote=False)}</t></is></c>' + xml[m.end():]
            changed[p] = xml.encode("utf-8")
        print(f"  {sheet}: {len(touched):,} rows corrected, {len(rows) - len(touched):,} already correct")
    if not changed: print("nothing to change"); return
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx", dir=os.path.dirname(os.path.abspath(XLSX))).name
    with zipfile.ZipFile(tmp, "w") as out:
        for info, d in infos: out.writestr(info, changed.get(info.filename, d), compress_type=info.compress_type)
    shutil.move(tmp, XLSX)
    print(f"{len(changed)} tabs written")


if __name__ == "__main__":
    main()
