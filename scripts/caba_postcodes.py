"""Buenos Aires city (CABA) postcodes: sample them from the city's address service, then add them to the workbook.

The GeoNames file behind the Argentina ZIP tab has no codes for the city itself (4-digit codes 1000–1499). The city
government's USIG services give, for a point, the nearest street address (reversegeocoding) and, for an address, its
postcode (datos_utiles). Sampling points on a grid and averaging the points per postcode gives each code's position.

Usage:
  python scripts/caba_postcodes.py sample          # whole city, a point every ~350 m (~3,000 points)
  python scripts/caba_postcodes.py sample dense    # centre, Palermo, Recoleta: every ~120 m (~2,750 points)
  python scripts/caba_postcodes.py build           # data/caba_samples.jsonl -> Argentina rows in data/zip_additions.csv
then  python scripts/add_zip_rows.py               # writes them into the workbook

Sampling appends to data/caba_samples.jsonl and skips points already there, so it can be stopped and resumed at any
time (Ctrl+C). It waits 0.15 s between points; the service answers one address at a time (~1 s per point).
"""
import collections, csv, json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "..", "data", "caba_samples.jsonl")
ADD = os.path.join(HERE, "..", "data", "zip_additions.csv")
GRIDS = {  # name: (lat0, lat1, lon0, lon1, step_lat, step_lon)
    "city": (-34.706, -34.526, -58.532, -58.334, 0.00315, 0.00383),
    "dense": (-34.632, -34.575, -58.425, -58.357, 0.00108, 0.00131),
}
FIELDS = ["sheet", "country_code", "zip", "place", "admin1", "admin1_code", "admin2", "admin2_code", "admin3", "admin3_code", "lat", "lon", "accuracy", "source"]
SOURCE = "Buenos Aires city address service (USIG): centre of the addresses sampled in each postcode"


def get(url):
    for k in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r: return r.read().decode("utf-8")
        except Exception: time.sleep(2 + 3 * k)
    return None


def sample(grid):
    lat0, lat1, lon0, lon1, dla, dlo = GRIDS[grid]
    done = set()
    if os.path.exists(SAMPLES):
        for l in open(SAMPLES, encoding="utf-8"): done.add(tuple(json.loads(l)["pt"]))
    pts, la = [], lat0
    while la <= lat1:
        lo = lon0
        while lo <= lon1: pts.append((round(la, 5), round(lo, 5))); lo += dlo
        la += dla
    todo = [p for p in pts if p not in done]
    print(f"{grid}: {len(pts)} points, {len(todo)} to do", flush=True)
    with open(SAMPLES, "a", encoding="utf-8") as fh:
        for n, (la, lo) in enumerate(todo):
            rec = {"pt": [la, lo]}
            t = get(f"https://ws.usig.buenosaires.gob.ar/geocoder/2.2/reversegeocoding?x={lo}&y={la}")
            m = t and re.search(r"\((.*)\)", t, re.S)
            if m:
                try:
                    g = json.loads(m.group(1)); rec["puerta"] = g.get("puerta")
                    pm = re.match(r"(.+?)\s+(\d+)$", g.get("puerta") or "")
                    if pm and float(g.get("metros_a_esquina") or 0) < 400:
                        u = get("https://ws.usig.buenosaires.gob.ar/datos_utiles?" + urllib.parse.urlencode({"calle": pm.group(1), "altura": pm.group(2)}))
                        if u:
                            d = json.loads(u)
                            rec.update(cp=d.get("codigo_postal"), cpa=d.get("codigo_postal_argentino"), barrio=d.get("barrio"), comuna=d.get("comuna"))
                except Exception as e: rec["err"] = str(e)[:100]
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n"); fh.flush()
            if n % 200 == 0: print(f"  {n}/{len(todo)}", flush=True)
            time.sleep(0.15)
    print("done")


def build():
    recs = [json.loads(l) for l in open(SAMPLES, encoding="utf-8")]
    groups = collections.defaultdict(list)
    for r in recs:
        if r.get("cp") and re.fullmatch(r"1[0-4]\d\d", r["cp"]) and r.get("barrio"): groups[(r["cp"], r["barrio"], r.get("comuna") or "")].append(r["pt"])
    rows = []
    for (cp, barrio, comuna), pts in sorted(groups.items()):
        rows.append(dict(sheet="Argentina ZIP", country_code="AR", zip=cp, place=barrio.upper(), admin1="Ciudad Autonoma De Buenos Aires", admin1_code="C",
                         admin2=comuna.upper(), admin2_code="", admin3="", admin3_code="",
                         lat=round(sum(p[0] for p in pts) / len(pts), 5), lon=round(sum(p[1] for p in pts) / len(pts), 5),
                         accuracy=4 if len(pts) > 1 else 3, source=SOURCE))
    old = [r for r in csv.DictReader(open(ADD, encoding="utf-8"))] if os.path.exists(ADD) else []
    old = [r for r in old if r["sheet"] != "Argentina ZIP"]
    with open(ADD, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(old + rows)
    print(f"{len({r['zip'] for r in rows})} postcodes, {len(rows)} postcode/barrio rows from {sum(map(len, groups.values()))} sampled addresses -> {ADD}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "sample": sample(sys.argv[2] if len(sys.argv) > 2 else "city")
    elif len(sys.argv) > 1 and sys.argv[1] == "build": build()
    else: print(__doc__)
