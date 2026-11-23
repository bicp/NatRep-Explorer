"""India: coordinates for the India ZIP tab, from the GeoNames postal-code file.

Usage:  python scripts/build_in_pincodes.py path/to/IN.zip      (https://download.geonames.org/export/zip/IN.zip, CC BY 4.0)

The workbook's India tab is the same GeoNames list with the latitude/longitude columns dropped, so every row is matched
back by pincode, place and district. GeoNames' India points vary in quality:
  - most are the post office's own village or locality ("accuracy" 3–4);
  - about half the pincodes share a point with 10 or more other pincodes: a district or town centre that GeoNames used
    when it had nothing better (123 Bengaluru pincodes sit on one point). These are kept but marked approximate;
  - a few hundred rows are more than 150 km from their own district (a same-named village elsewhere). These are moved to
    the centre of their pincode's other points in that district, or to the district centre, and marked as moved.

Output: data/in_pincodes.csv.gz  zip, place, admin2, lat, lon, point (empty = GeoNames place point; otherwise why it is
approximate). The database build fills the India rows from it.
"""
import collections, csv, gzip, io, math, os, statistics as st, sys, zipfile

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "in_pincodes.csv.gz")
SHARED = 10        # a point shared by this many pincodes is a placeholder centre
FAR_KM = 150       # further than this from its district's centre = misplaced


def km(a, b):
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def median(ps):
    return (round(st.median(p[0] for p in ps), 4), round(st.median(p[1] for p in ps), 4))


def read(path):
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z, z.open("IN.txt") as fh:
            return list(csv.reader(io.TextIOWrapper(fh, encoding="utf-8"), delimiter="\t"))
    with open(path, encoding="utf-8") as fh:
        return list(csv.reader(fh, delimiter="\t"))


def main(path):
    rows = [r for r in read(path) if len(r) >= 12 and r[0] == "IN" and r[9] and r[10]]
    for r in rows: r[9], r[10] = float(r[9]), float(r[10])
    pins = collections.defaultdict(set)
    for r in rows: pins[(r[9], r[10])].add(r[1])
    shared = {p for p, z in pins.items() if len(z) >= SHARED}
    good = lambda r: r[11] != "1" and (r[9], r[10]) not in shared
    by_dist = collections.defaultdict(list)
    for r in rows:
        if good(r): by_dist[(r[3], r[5])].append((r[9], r[10]))
    dist_c = {k: median(v) for k, v in by_dist.items()}
    pin_points = collections.defaultdict(list)  # (pincode, district) -> its good points inside the district
    for r in rows:
        d = (r[3], r[5])
        if good(r) and d in dist_c and km((r[9], r[10]), dist_c[d]) <= FAR_KM: pin_points[r[1], d].append((r[9], r[10]))
    out, n = [], collections.Counter()
    for r in rows:
        p, d = (r[9], r[10]), (r[3], r[5])
        note = ""
        if d in dist_c and km(p, dist_c[d]) > FAR_KM:
            near = pin_points.get((r[1], d))
            p, note = (median(near), "moved: was far outside its district; pincode centre") if near else \
                      (dist_c[d], "moved: was far outside its district; district centre")
            n["moved"] += 1
        elif p in shared or r[11] == "1":
            note = f"approximate: district or town centre shared by {len(pins[p])} pincodes" if p in shared else "approximate (GeoNames estimate)"
            n["approximate"] += 1
        else:
            n["place"] += 1
        out.append((r[1], r[2], r[5], p[0], p[1], note))
    with io.TextIOWrapper(gzip.GzipFile(OUT, "wb", mtime=0), encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["zip", "place", "admin2", "lat", "lon", "point"])
        w.writerows(out)
    print(f"{len(out):,} rows, {len({r[0] for r in out}):,} pincodes -> {os.path.normpath(OUT)}")
    print(f"  place points {n['place']:,} · approximate {n['approximate']:,} · moved {n['moved']:,}")


if __name__ == "__main__":
    if len(sys.argv) != 2: sys.exit(__doc__)
    main(sys.argv[1])
