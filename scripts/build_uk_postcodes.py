"""UK: every live postcode with its coordinates, plus postcode sector and district centres, from the ONS Postcode
Directory (ONSPD).

Usage:  python scripts/build_uk_postcodes.py path/to/ONSPD_AUG_2026.zip

Input: the quarterly ONSPD from the ONS Open Geography Portal (search "ONS Postcode Directory"; Open Government Licence,
contains Royal Mail, OS and ONS data). The ZIP is read as a stream and never unpacked.

Output:
  data/uk_postcodes.csv.gz       postcode (as written, e.g. "SS2 6BH"), lat, lon – live postcodes (no termination date)
                                 that have coordinates; the database build turns it into a lookup table, so a full
                                 postcode typed in the search goes to its own point
  data/uk_postcode_areas.csv.gz  level (sector / district), code ("SS2 6" / "SS2"), lat, lon (mean of its live
                                 postcodes), postcodes (how many), small_users (how many are "small user" postcodes, i.e.
                                 ordinary addresses; a sector with none is PO boxes / large businesses only) – the database build adds the sectors as zip rows and
                                 uses the district centres for districts the workbook has as a single point
"""
import collections, csv, gzip, io, os, sys, zipfile

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUT = os.path.join(DATA, "uk_postcodes.csv.gz")
AREAS = os.path.join(DATA, "uk_postcode_areas.csv.gz")


def main(path):
    z = zipfile.ZipFile(path)
    name = next(n for n in z.namelist() if n.lower().startswith("data/onspd_") and n.lower().endswith("_uk.csv"))
    n = kept = 0
    acc = {"sector": collections.defaultdict(lambda: [0.0, 0.0, 0, 0]), "district": collections.defaultdict(lambda: [0.0, 0.0, 0, 0])}
    with z.open(name) as fh, io.TextIOWrapper(gzip.GzipFile(OUT, "wb", mtime=0), encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        w.writerow(["postcode", "lat", "lon"])
        for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")):
            n += 1
            if r["doterm"].strip(): continue  # terminated
            try: la, lo = float(r["lat"]), float(r["long"])
            except ValueError: continue
            if la > 90: continue  # 99.999999 = no grid reference
            pc = r["pcds"].strip()
            w.writerow([pc, round(la, 5), round(lo, 5)]); kept += 1
            if " " not in pc: continue
            for level, code in (("sector", pc[:-2]), ("district", pc.split(" ")[0])):
                a = acc[level][code]; a[0] += la; a[1] += lo; a[2] += 1; a[3] += r["usrtypind"].strip() == "0"
    with io.TextIOWrapper(gzip.GzipFile(AREAS, "wb", mtime=0), encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        w.writerow(["level", "code", "lat", "lon", "postcodes", "small_users"])
        for level in ("district", "sector"):
            for code, (sla, slo, k, small) in sorted(acc[level].items()):
                w.writerow([level, code, round(sla / k, 5), round(slo / k, 5), k, small])
    print(f"{name}: {n:,} postcodes, {kept:,} live with coordinates -> {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB)")
    print(f"  {len(acc['district']):,} districts, {len(acc['sector']):,} sectors -> {AREAS}")


if __name__ == "__main__":
    main(sys.argv[1])
