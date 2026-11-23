"""UK: census usual residents and households per postcode sector, for the four nations.

Usage:  python scripts/build_uk_census.py data/uk_census

Input folder (not in git), files as downloaded:
  ew_p003_sectors.csv     England & Wales, Census 2021: usual residents per postcode sector (Nomis, table P003)
                          https://www.nomisweb.co.uk/output/census/2021/pcds_p003.csv
  ew_p002_households.csv  England & Wales, Census 2021: households per postcode (Nomis, table P002), summed per sector
                          https://www.nomisweb.co.uk/output/census/2021/pcd_p002.csv
  sc_census_2022_index.zip  Scotland's Census 2022 Index (NRS): Postcode_To_OA.csv has households and population
                          per postcode, summed per sector
                          https://www.nrscotland.gov.uk/publications/2022-census-geography-products
  ni_postcodes.xlsx       Northern Ireland, Census 2021 (NISRA): "Postcode sector" sheet
                          https://www.nisra.gov.uk/publications/census-2021-person-and-household-estimates-for-postcodes-in-northern-ireland
All Open Government Licence. Small counts are suppressed or perturbed by the census offices, so sector sums differ
slightly from the national totals.

Output: data/uk_census_sectors.csv  sector, people, households, census (year and office)
"""
import collections, csv, io, os, sys, zipfile
import openpyxl

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "uk_census_sectors.csv")


def sector(pc):
    p = pc.split()
    return f"{p[0]} {p[1][0]}" if len(p) == 2 and p[1] else None


def count(v):
    try: return int(v)
    except (TypeError, ValueError): return None  # "*" = suppressed


def main(folder):
    out = {}
    # England & Wales: people per sector (P003), households per postcode (P002)
    hh = collections.Counter()
    with open(os.path.join(folder, "ew_p002_households.csv"), encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            s = sector(r["Postcode"]); c = count(r["Count"])
            if s and c: hh[s] += c
    with open(os.path.join(folder, "ew_p003_sectors.csv"), encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            s = " ".join(r["Postcode Sectors"].split()); c = count(r["Count"])
            if c is not None: out[s] = [c, hh.get(s, 0), "Census 2021 (ONS)"]
    ew = len(out)
    # Scotland: households and population per postcode
    sc = collections.defaultdict(lambda: [0, 0])
    with zipfile.ZipFile(os.path.join(folder, "sc_census_2022_index.zip")) as z:
        name = next(n for n in z.namelist() if n.endswith("Postcode_To_OA.csv"))
        with z.open(name) as fh:
            for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")):
                s = sector(r["Postcode"])
                if s: sc[s][0] += count(r["PopulationCount"]) or 0; sc[s][1] += count(r["HouseholdCount"]) or 0
    for s, (p, h) in sc.items():
        if p or h: out[s] = [p, h, "Census 2022 (NRS)"]
    # Northern Ireland: sector table
    ws = openpyxl.load_workbook(os.path.join(folder, "ni_postcodes.xlsx"), read_only=True)["Postcode sector"]
    started = False; ni = 0
    for r in ws.iter_rows(values_only=True):
        if r and r[0] == "Postcode sector": started = True; continue
        if started and r and r[0]:
            p, h = count(r[1]), count(r[4])
            if p is not None: out[" ".join(str(r[0]).split())] = [p, h or 0, "Census 2021 (NISRA)"]; ni += 1
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh); w.writerow(["sector", "people", "households", "census"])
        for s in sorted(out): w.writerow([s, *out[s]])
    tot = collections.defaultdict(lambda: [0, 0, 0])
    for p, h, c in out.values(): t = tot[c]; t[0] += 1; t[1] += p; t[2] += h
    for c, (n, p, h) in tot.items(): print(f"  {c}: {n:,} sectors, {p:,} people, {h:,} households")
    print(f"{len(out):,} sectors -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
