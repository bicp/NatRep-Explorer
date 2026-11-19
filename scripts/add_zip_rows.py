"""Append missing postcodes (data/zip_additions.csv) to the zip tabs of the workbook.

Usage:  python scripts/add_zip_rows.py [additions.csv] [workbook.xlsx]

CSV columns: sheet, country_code, zip, place, admin1, admin1_code, admin2, admin2_code, admin3, admin3_code, lat, lon,
accuracy, source – the tab's own layout (columns A–L) plus the source. Rows whose zip + place are already in the tab
are not added again; if the CSV has new coordinates for them (e.g. after more sampling), only those are updated. Running it twice changes nothing the second time. Each changed tab's note (cell B2) gets one line with the
number of rows added per source.

Like apply_zip_fixes.py it patches the sheet XML inside the .xlsx and leaves every other part unchanged.
"""
import collections, csv, html, os, re, shutil, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ADD = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "zip_additions.csv")
XLSX = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "data", "Global_Nat_Rep_and_Zip_Codes_Combined.xlsx")
COLS = ["country_code", "zip", "place", "admin1", "admin1_code", "admin2", "admin2_code", "admin3", "admin3_code", "lat", "lon", "accuracy"]
TAG = "Nov 2026, added:"
CELL = re.compile(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)


def main():
    adds = collections.defaultdict(list)
    for r in csv.DictReader(open(ADD, encoding="utf-8")): adds[r["sheet"]].append(r)
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

    def val(attrs, body):
        t = re.search(r't="(\w+)"', attrs)
        if t and t.group(1) == "s": return sst[int(re.search(r"<v>(\d+)</v>", body).group(1))]
        if t and t.group(1) == "inlineStr": return html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", body or "", re.S)))
        v = re.search(r"<v>(.*?)</v>", body or "")
        return html.unescape(v.group(1)) if v else ""

    inl = lambda ref, text, s="": f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{html.escape(text, quote=False)}</t></is></c>'
    raw = {i.filename: d for i, d in infos}
    changed = {}
    for sheet, rows in adds.items():
        p = path_of(sheet)
        xml = raw[p].decode("utf-8")
        have, last = set(), 0
        for m in re.finditer(r'<row r="(\d+)"[^>]*?(?:/>|>(.*?)</row>)', xml, re.S):
            r = int(m.group(1)); last = max(last, r)
            cells = {c.group(1): c for c in CELL.finditer(m.group(2) or "")}
            if r >= 5 and "B" in cells and "C" in cells:
                have.add((val(cells["B"].group(3), cells["B"].group(4)), val(cells["C"].group(3), cells["C"].group(4))))
        new = [r for r in rows if (r["zip"], r["place"]) not in have]
        # rows added earlier: update their coordinates if the CSV now has better ones (e.g. after more sampling)
        want = {(r["zip"], r["place"]): (float(r["lat"]), float(r["lon"])) for r in rows}
        moved = 0

        def upd(m):
            nonlocal moved
            r, body = int(m.group(1)), m.group(3) or ""
            cells = {c.group(1): c for c in CELL.finditer(body)}
            if r < 5 or "B" not in cells or "C" not in cells or "J" not in cells or "K" not in cells: return m.group(0)
            k = (val(cells["B"].group(3), cells["B"].group(4)), val(cells["C"].group(3), cells["C"].group(4)))
            if k not in want: return m.group(0)
            la, lo = float(val(cells["J"].group(3), cells["J"].group(4))), float(val(cells["K"].group(3), cells["K"].group(4)))
            if abs(la - want[k][0]) < 1e-6 and abs(lo - want[k][1]) < 1e-6: return m.group(0)
            moved += 1
            body = body.replace(cells["J"].group(0), f'<c r="J{r}"><v>{want[k][0]!r}</v></c>').replace(cells["K"].group(0), f'<c r="K{r}"><v>{want[k][1]!r}</v></c>')
            return f'<row r="{r}"{m.group(2)}>{body}</row>'

        xml = re.sub(r'<row r="(\d+)"([^>]*?)(?:/>|>(.*?)</row>)', upd, xml, flags=re.S)
        if moved: print(f"  {sheet}: {moved:,} added rows got new coordinates")
        if not new and not moved: print(f"  {sheet}: nothing new"); continue
        if not new: changed[p] = xml.encode("utf-8"); continue
        out = []
        for k, r in enumerate(new):
            n = last + 1 + k
            cs = []
            for j, col in enumerate(COLS):
                ref, v = f"{chr(65 + j)}{n}", r.get(col, "")
                if v in ("", None): continue
                cs.append(f'<c r="{ref}"><v>{float(v)!r}</v></c>' if col in ("lat", "lon", "accuracy") else inl(ref, v))
            out.append(f'<row r="{n}">{"".join(cs)}</row>')
        end = last + len(new)
        xml = xml.replace("</sheetData>", "".join(out) + "</sheetData>")
        xml = re.sub(r'<dimension ref="A1:([A-Z]+)\d+"/>', rf'<dimension ref="A1:\g<1>{end}"/>', xml)
        xml = re.sub(r'<autoFilter ref="A4:([A-Z]+)\d+"', rf'<autoFilter ref="A4:\g<1>{end}"', xml)
        m = re.search(r'<c r="B2"([^>]*?)(?:/>|>(.*?)</c>)', xml, re.S)
        note = re.sub(rf" · {re.escape(TAG)} [^·]*", "", val(m.group(1), m.group(2) or ""))
        src = collections.Counter(r["source"] for r in rows)
        note += f" · {TAG} {len(rows):,} postcodes missing from the source ({'; '.join(f'{k}: {v:,}' for k, v in src.items())}); list in data/zip_additions.csv"
        st = re.search(r'\ss="\d+"', m.group(1))
        xml = xml[:m.start()] + inl("B2", note, st.group(0) if st else "") + xml[m.end():]
        changed[p] = xml.encode("utf-8")
        print(f"  {sheet}: {len(new):,} rows added (rows {last + 1}–{end})")
    if not changed: print("nothing to change"); return
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx", dir=os.path.dirname(os.path.abspath(XLSX))).name
    with zipfile.ZipFile(tmp, "w") as zout:
        for info, d in infos: zout.writestr(info, changed.get(info.filename, d), compress_type=info.compress_type)
    shutil.move(tmp, XLSX)
    print(f"{len(changed)} tabs written")


if __name__ == "__main__":
    main()
