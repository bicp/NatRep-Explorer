"""Add latitude/longitude to the Brazil ZIP tab, so Brazil supports radius search.

Usage:  python scripts/add_brazil_coords.py [workbook.xlsx]

The tab lists CEP ranges per municipality (Correios). Each row gets the coordinates of its municipality's seat from
data/br_municipios.csv (IBGE code, name, state, latitude, longitude; from github.com/kelvins/municipios-brasileiros,
MIT licence) in columns K (Latitude), L (Longitude) and M (IBGE code). Rows are matched on state + municipality name
(accents, case and punctuation ignored); RENAMED covers municipalities whose name changed since the Correios list.
The script stops if a row can't be matched. Running it again rewrites the same values.

It patches the sheet XML inside the .xlsx and leaves every other part unchanged, like apply_zip_fixes.py.
"""
import csv, html, os, re, shutil, sys, tempfile, unicodedata, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "Global_Nat_Rep_and_Zip_Codes_Combined.xlsx")
MUN = os.path.join(HERE, "..", "data", "br_municipios.csv")
SHEET = "Brazil ZIP"
TAG = "Nov 2026:"
RENAMED = {  # (state, name in the Correios list): name in the IBGE list
    ("GO", "Bom Jesus"): "Bom Jesus de Goiás", ("PE", "Iguaraci"): "Iguaracy", ("RN", "Arez"): "Arês",
    ("RN", "Boa Saúde"): "Januário Cicco (Boa Saúde)", ("RN", "Campo Grande"): "Augusto Severo (Campo Grande)",
    ("SP", "Florínea"): "Florínia", ("TO", "São Valério da Natividade"): "São Valério", ("TO", "Tabocão"): "Fortaleza do Tabocão",
    ("MG", "Amparo da Serra"): "Amparo do Serra", ("MG", "Dona Euzébia"): "Dona Eusébia", ("MG", "Santa Rita do Ibitipoca"): "Santa Rita de Ibitipoca",
    ("MG", "Silverânia"): "Silveirânia", ("PA", "Eldorado dos Carajás"): "Eldorado do Carajás", ("PE", "Lagoa do Itaenga"): "Lagoa de Itaenga",
    ("PE", "São Caitano"): "São Caetano", ("SE", "Graccho Cardoso"): "Gracho Cardoso", ("TO", "Couto de Magalhães"): "Couto Magalhães",
}
HEAD = {"K": "Latitude", "L": "Longitude", "M": "IBGE municipality code"}
CELL = re.compile(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)


def norm(t):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower())


def main():
    mun = {(r["uf"], norm(r["nome"])): r for r in csv.DictReader(open(MUN, encoding="utf-8"))}
    with zipfile.ZipFile(XLSX) as z:
        wbx = z.read("xl/workbook.xml").decode("utf-8")
        rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        sst = [html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))
               for si in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8"), re.S)]
        infos = [(i, z.read(i.filename)) for i in z.infolist()]
    tag = next(t for t in re.findall(r"<sheet [^>]*>", wbx) if f'name="{SHEET}"' in t)
    rid = re.search(r'r:id="([^"]+)"', tag).group(1)
    rel = next(t for t in re.findall(r"<Relationship [^>]*>", rels) if f'Id="{rid}"' in t)
    path = "xl/" + re.search(r'Target="([^"]+)"', rel).group(1).lstrip("/").removeprefix("xl/")
    xml = dict((i.filename, d) for i, d in infos)[path].decode("utf-8")

    def val(attrs, body):
        t = re.search(r't="(\w+)"', attrs)
        if t and t.group(1) == "s": return sst[int(re.search(r"<v>(\d+)</v>", body).group(1))]
        if t and t.group(1) == "inlineStr": return html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", body, re.S)))
        v = re.search(r"<v>(.*?)</v>", body or "")
        return html.unescape(v.group(1)) if v else ""

    inl = lambda ref, s, text: f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{html.escape(text, quote=False)}</t></is></c>'
    missing, n = [], 0

    def row(m):
        nonlocal n
        r, body = int(m.group(1)), m.group(3) or ""
        cells = {c.group(1): c for c in CELL.finditer(body)}
        keep = "".join(c.group(0) for k, c in cells.items() if k not in HEAD)
        if r == 4:
            st = re.search(r'\ss="\d+"', cells["J"].group(3)) if "J" in cells else None
            st = st.group(0) if st else ""
            return f'<row r="4"{m.group(2)}>{keep}' + "".join(inl(f"{k}4", st, h) for k, h in HEAD.items()) + "</row>"
        if r < 5 or "A" not in cells or "C" not in cells: return m.group(0)
        uf, name = val(cells["A"].group(3), cells["A"].group(4)), val(cells["C"].group(3), cells["C"].group(4))
        if not uf.strip(): return m.group(0)
        hit = mun.get((uf, norm(RENAMED.get((uf, name), name))))
        if not hit: missing.append((r, uf, name)); return m.group(0)
        n += 1
        return (f'<row r="{r}"{m.group(2)}>{keep}<c r="K{r}"><v>{hit["latitude"]}</v></c><c r="L{r}"><v>{hit["longitude"]}</v></c>'
                + inl(f"M{r}", "", hit["codigo_ibge"]) + "</row>")

    xml = re.sub(r'<row r="(\d+)"([^>]*?)(?:/>|>(.*?)</row>)', row, xml, flags=re.S)
    if missing: sys.exit("no municipality for: " + "; ".join(f"row {r} {u} {nm}" for r, u, nm in missing))
    xml = re.sub(r'\sspans="[^"]*"', "", xml)
    xml = re.sub(r'<dimension ref="A1:[A-Z]+(\d+)"/>', r'<dimension ref="A1:M\1"/>', xml)
    xml = re.sub(r'<autoFilter ref="A4:[A-Z]+(\d+)"', r'<autoFilter ref="A4:M\1"', xml)
    if 'min="11"' not in xml: xml = xml.replace("</cols>", '<col min="11" max="12" width="11" customWidth="1"/><col min="13" max="13" width="14" customWidth="1"/></cols>')
    m = re.search(r'<c r="B2"([^>]*?)(?:/>|>(.*?)</c>)', xml, re.S)
    note = re.sub(rf" · {re.escape(TAG)} [^·]*", "", val(m.group(1), m.group(2) or ""))
    note += f" · {TAG} latitude/longitude of each municipality's seat added in columns K–L (IBGE code in M), from data/br_municipios.csv (kelvins/municipios-brasileiros)"
    st = re.search(r'\ss="\d+"', m.group(1))
    xml = xml[:m.start()] + inl("B2", st.group(0) if st else "", note) + xml[m.end():]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx", dir=os.path.dirname(os.path.abspath(XLSX))).name
    with zipfile.ZipFile(tmp, "w") as out:
        for info, d in infos: out.writestr(info, xml.encode("utf-8") if info.filename == path else d, compress_type=info.compress_type)
    shutil.move(tmp, XLSX)
    print(f"{SHEET}: coordinates for {n:,} rows")


if __name__ == "__main__":
    main()
