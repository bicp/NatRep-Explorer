"""Brazil: one point per CEP from IBGE's 2022 census address file (CNEFE).

Usage:  python scripts/build_br_ceps.py path/to/23_CE.zip [path/to/35_SP.zip ...]

Input: the per-state files from
https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/
(one row per address, with its CEP, locality, street, latitude, longitude and geocoding level). The ZIP is read as a
stream and never unpacked. If CEP Aberto downloads for the state are in data/cepaberto/ (e.g. ce.cepaberto_parte_1.zip),
their street names are used to pick each CEP's addresses, and their street and neighbourhood names are kept.

Each CEP's point is the median of its addresses on its own street: the street CEP Aberto gives for it when a census
street matches it, otherwise the CEP's most common street when that holds at least 40% of its addresses, otherwise all
its addresses. That drops addresses filed under a mistyped CEP. A CEP ending in -000 with no dominant street is a small
town's generic CEP and uses all its addresses. Addresses placed only at census-sector level (geocoding level 6) count only when nothing better exists.

Output: data/br_ceps/<UF>.csv.gz, one row per CEP: cep (00000-000), lat, lon, spread_m (median distance of the
addresses used from the point), municipality IBGE code, locality, street, addresses, households (private dwellings),
basis (how the point was chosen).
"""
import array, collections, csv, difflib, glob, gzip, io, math, os, re, statistics as st, sys, unicodedata, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
csv.field_size_limit(10 ** 7)
PREFIX = re.compile(r"^(rua|r|avenida|av|travessa|tv|praca|rodovia|estrada|vila|alameda|beco|via|largo|conjunto|sitio|fazenda|quadra|viela|passagem)\s+")


def key(t):
    t = re.sub(r"[^a-z0-9 ]", "", unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()).strip()
    return PREFIX.sub("", t)


def dist_m(a, b, c, d):
    return math.hypot((c - a) * 111320, (d - b) * 111320 * math.cos(math.radians(a)))


def cepaberto_streets(uf):
    out = {}
    for f in glob.glob(os.path.join(DATA, "cepaberto", f"{uf.lower()}.cepaberto_parte_*.zip")):
        z = zipfile.ZipFile(f)
        for n in z.namelist():
            if n.endswith(".csv"):
                for r in csv.reader(io.TextIOWrapper(z.open(n), encoding="utf-8", errors="replace")):
                    if len(r) > 3 and (r[1] or r[3]): out[r[0]] = (r[1], r[3])
    return out


def build(path):
    uf = os.path.basename(path).split("_")[1].split(".")[0]
    ca = cepaberto_streets(uf)
    print(f"{uf}: {len(ca):,} CEP Aberto streets", flush=True)
    pts = {}   # cep -> {street: (lats, lons, weak_lats, weak_lons)}
    info = {}  # cep -> [municipality counter, locality counter, addresses, households]
    n = 0
    with zipfile.ZipFile(path) as z, z.open(next(x for x in z.namelist() if x.lower().endswith(".csv"))) as fh:
        rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline=""), delimiter=";")
        h = {k: i for i, k in enumerate(next(rd))}
        iC, iLa, iLo, iL, iM, iLoc, iT, iTi, iN, iE = (h[k] for k in ("CEP", "LATITUDE", "LONGITUDE", "NV_GEO_COORD", "COD_MUNICIPIO",
                                                                   "DSC_LOCALIDADE", "NOM_TIPO_SEGLOGR", "NOM_TITULO_SEGLOGR", "NOM_SEGLOGR", "COD_ESPECIE"))
        for r in rd:
            n += 1
            if n % 2_000_000 == 0: print(f"  {uf}: {n:,} addresses, {len(info):,} CEPs", flush=True)
            cep = r[iC].strip()
            if len(cep) != 8 or not cep.isdigit(): continue
            try: la, lo, lev = float(r[iLa]), float(r[iLo]), int(r[iL] or 9)
            except ValueError: continue
            street = " ".join(x for x in (r[iT], r[iTi], r[iN]) if x).strip()
            s = pts.setdefault(cep, {})
            a = s.get(street)
            if a is None: a = s[street] = (array.array("f"), array.array("f"), array.array("f"), array.array("f"))
            if lev <= 5: a[0].append(la); a[1].append(lo)
            else: a[2].append(la); a[3].append(lo)
            i = info.get(cep)
            if i is None: i = info[cep] = [collections.Counter(), collections.Counter(), 0, 0]
            i[0][r[iM]] += 1
            if len(i[1]) < 30: i[1][r[iLoc].strip()] += 1
            i[2] += 1; i[3] += r[iE] == "1"
    os.makedirs(os.path.join(DATA, "br_ceps"), exist_ok=True)
    out = os.path.join(DATA, "br_ceps", f"{uf}.csv.gz")
    basis = collections.Counter()
    with io.TextIOWrapper(gzip.GzipFile(out, "wb", mtime=0), encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cep", "lat", "lon", "spread_m", "municipio", "localidade", "logradouro", "addresses", "households", "basis"])
        for cep in sorted(info):
            s, i = pts[cep], info[cep]
            size = {k: len(v[0]) + len(v[2]) for k, v in s.items()}
            main = max(size, key=size.get)
            use, how = list(s), "whole town (generic CEP)" if cep.endswith("000") else "all addresses"
            if True:
                want = key(ca.get(cep, ("", ""))[0])
                hit = max(s, key=lambda k: difflib.SequenceMatcher(None, want, key(k)).ratio()) if want else None
                if hit is not None and difflib.SequenceMatcher(None, want, key(hit)).ratio() > 0.7: use, how = [hit], "its street (CEP Aberto)"
                elif size[main] >= 0.4 * i[2]: use, how = [main], "its main street"
            la_ = [x for k in use for x in s[k][0]]; lo_ = [x for k in use for x in s[k][1]]
            if not la_: la_ = [x for k in use for x in s[k][2]]; lo_ = [x for k in use for x in s[k][3]]; how += ", census-sector level"
            la, lo = st.median(la_), st.median(lo_)
            spread = st.median(dist_m(la, lo, a, b) for a, b in zip(la_[:2000], lo_[:2000]))
            basis[how.split(",")[0]] += 1
            w.writerow([f"{cep[:5]}-{cep[5:]}", round(la, 6), round(lo, 6), round(spread), i[0].most_common(1)[0][0],
                        (ca[cep][1] if ca.get(cep) and ca[cep][1] else i[1].most_common(1)[0][0].title()),
                        (ca[cep][0] if ca.get(cep) and ca[cep][0] else (use[0] if len(use) == 1 else main).title()),
                        i[2], i[3], how])
    print(f"{uf}: {n:,} addresses -> {len(info):,} CEPs -> {out}\n   point from: {dict(basis)}")


if __name__ == "__main__":
    for p in sys.argv[1:]: build(p)
