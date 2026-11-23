"""Estimated population around every zip point, from a global population grid, plus average household sizes.

Usage:  python scripts/estimate_population.py data/popgrid [ISO2 ...]

Each zip point gets the people living closer to it than to any other zip point of the same country (the country's
area split into "nearest point" cells, cut at the border). Where zip points are denser than the grid (city streets,
buildings), a grid cell holding several zip points splits its people equally between them instead. Zip rows that
share a point share its people equally.
UK: only the postcode sectors are used (the districts contain them), and not the sectors without any ordinary address
(PO boxes and large businesses only, small_users = 0 in data/uk_postcode_areas.csv.gz). Cells more than MAX_KM from every zip point of
their country stay unassigned (e.g. French overseas departments, which have no zip points in the workbook).

Needs data/natrep.db from scripts/build_db.py and, in the given folder (not in git):
  GHS_POP_E2025_GLOBE_R2023A_4326_30ss_V1_0.tif  GHSL population grid, epoch 2025, 30 arc-seconds (~1 km), EU JRC
      https://human-settlement.emergency.copernicus.eu/download.php?ds=pop  (CC BY 4.0)
  ne_10m_admin_0_countries.geojson               Natural Earth country borders (public domain)
  UNDESA_PD_2026_hh-size-composition.xlsx (or un_hh_size_2026.xlsx)
      UN DESA Database on Household Size and Composition 2026, https://population.un.org/Household/ (CC BY 3.0 IGO)
and the packages numpy, scipy, rasterio, pillow (only this script needs them, not the database build).

With ISO2 codes, only those countries are (re)computed and the others already in the output are kept.

Output:
  data/zip_population.csv.gz  iso2, zip, lat, lon, pop  (pop per zip row, rounded; lat/lon rounded to 5 decimals)
  data/household_size.csv     iso2, size, year, source  (latest UN average household size per country)
"""
import collections, csv, glob, gzip, io, json, math, os, sqlite3, sys, time
import numpy as np
import openpyxl
import rasterio
from rasterio.windows import Window
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DB = os.path.join(ROOT, "data", "natrep.db")
OUT = os.path.join(ROOT, "data", "zip_population.csv.gz")
HH_OUT = os.path.join(ROOT, "data", "household_size.csv")
MAX_KM = 250
TILE = 1200  # grid cells per tile side (10 degrees)
R = 6371.0088
# Natural Earth units that belong to a workbook country's zip data although Natural Earth gives them another code
EXTRA_UNITS = {"TR": {"CYN"}}  # Northern Cyprus: Turkish "Mersin 10" postcodes


def unit(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack((np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)))


def polygons(features):
    """[(exterior, [holes])] with rings as lists of (lon, lat)."""
    out = []
    for f in features:
        g = f["geometry"]
        for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
            out.append((poly[0], poly[1:]))
    return out


def household_sizes(folder):
    path = next(iter(glob.glob(os.path.join(folder, "*hh-size-composition*.xlsx")) + glob.glob(os.path.join(folder, "un_hh_size*.xlsx"))))
    ws = openpyxl.load_workbook(path, read_only=True)["HH size and composition 2026"]
    it = ws.iter_rows(values_only=True)
    hdr = None
    for r in it:
        if r and r[0] == "Country and area": hdr = list(r); break
    ci, cy, cs, cn = hdr.index("ISO2 Code"), hdr.index("Reference year"), hdr.index("Average household size (number of members)"), hdr.index("Data source detailed name")
    best = {}
    for r in it:
        try: iso, year, size = r[ci], int(str(r[cy])[:4]), float(r[cs])
        except (TypeError, ValueError): continue
        if iso and (iso not in best or year >= best[iso][1]): best[iso] = (size, year, r[cn])
    return best


def main(folder, only):
    t0 = time.time()
    con = sqlite3.connect(DB)
    isos = [r[0] for r in con.execute("SELECT DISTINCT iso2 FROM zips WHERE lat IS NOT NULL AND iso2 IS NOT NULL ORDER BY iso2")]
    if only: isos = [i for i in isos if i in only]
    ne = json.load(open(os.path.join(folder, "ne_10m_admin_0_countries.geojson"), encoding="utf-8"))["features"]
    grid = rasterio.open(glob.glob(os.path.join(folder, "GHS_POP_*_30ss_*.tif"))[0])
    tr = grid.transform
    x0, dx, y0, dy = tr.c, tr.a, tr.f, tr.e

    kept = []
    if only and os.path.exists(OUT):
        with gzip.open(OUT, "rt", encoding="utf-8", newline="") as fh:
            kept = [r for r in csv.reader(fh)][1:]
        kept = [r for r in kept if r[0] not in only]
    uk_areas = os.path.join(ROOT, "data", "uk_postcode_areas.csv.gz")
    uk_business = set()
    if os.path.exists(uk_areas):
        with gzip.open(uk_areas, "rt", encoding="utf-8") as fh:
            uk_business = {r["code"] for r in csv.DictReader(fh) if r["level"] == "sector" and r.get("small_users") == "0"}
    rows_out = []
    for iso in isos:
        t1 = time.time()
        q = "SELECT zip, lat, lon, extra FROM zips WHERE iso2 = ? AND lat IS NOT NULL"
        rows = [(z, round(la, 5), round(lo, 5)) for z, la, lo, ex in con.execute(q, (iso,))
                if iso != "GB" or (ex and "postcode sector" in ex and z not in uk_business)]
        pts = collections.defaultdict(list)
        for z, la, lo in rows: pts[(la, lo)].append(z)
        keys = list(pts)
        klat, klon = np.array([k[0] for k in keys]), np.array([k[1] for k in keys])
        tree = cKDTree(unit(klat, klon))
        # grid cell of each point, to split a cell between the points inside it
        site_cell = np.floor((klat - y0) / dy).astype(np.int64) * grid.width + np.floor((klon - x0) / dx).astype(np.int64)
        order = np.argsort(site_cell, kind="stable"); sc = site_cell[order]
        feats = [f for f in ne if f["properties"].get("ISO_A2_EH") == iso or f["properties"].get("ADM0_A3") in EXTRA_UNITS.get(iso, ())]
        if not feats: print(f"{iso}: no Natural Earth border, skipped"); continue
        polys = polygons(feats)
        lons = [p[0] for ext, _ in polys for p in ext]; lats = [p[1] for ext, _ in polys for p in ext]
        c0 = max(int((min(lons) - x0) / dx), 0); c1 = min(int((max(lons) - x0) / dx) + 1, grid.width)
        r0 = max(int((max(lats) - y0) / dy), 0); r1 = min(int((min(lats) - y0) / dy) + 1, grid.height)
        acc = np.zeros(len(keys)); total = unassigned = 0.0
        chord = 2 * math.sin(MAX_KM / R / 2)
        for rr in range(r0, r1, TILE):
            for cc in range(c0, c1, TILE):
                h, w = min(TILE, r1 - rr), min(TILE, c1 - cc)
                pop = grid.read(1, window=Window(cc, rr, w, h))
                pop[~(pop > 0)] = 0
                if not pop.any(): continue
                img = Image.new("L", (w, h), 0); dr = ImageDraw.Draw(img)
                px = lambda ring: [((lo - x0) / dx - cc - 0.5, (la - y0) / dy - rr - 0.5) for lo, la in ring]
                for ext, holes in polys:
                    dr.polygon(px(ext), fill=1)
                    for hole in holes: dr.polygon(px(hole), fill=0)
                m = (np.asarray(img, dtype=bool)) & (pop > 0)
                if not m.any(): continue
                ri, cj = np.nonzero(m); v = pop[ri, cj]
                lat = y0 + (rr + ri + 0.5) * dy; lon = x0 + (cc + cj + 0.5) * dx
                gid = (rr + ri).astype(np.int64) * grid.width + (cc + cj)
                lo_, hi_ = np.searchsorted(sc, gid, "left"), np.searchsorted(sc, gid, "right")
                n = hi_ - lo_; has = n > 0
                if has.any():  # cells with zip points inside: split equally between them
                    cnt = n[has]; offs = np.repeat(lo_[has] - np.cumsum(cnt) + cnt, cnt) + np.arange(cnt.sum())
                    acc += np.bincount(order[offs], weights=np.repeat(v[has] / cnt, cnt), minlength=len(keys))
                total += v.sum()
                lat, lon, v = lat[~has], lon[~has], v[~has]
                if not len(v): continue
                d, idx = tree.query(unit(lat, lon), distance_upper_bound=chord)
                ok = np.isfinite(d)
                acc += np.bincount(idx[ok], weights=v[ok], minlength=len(keys))
                unassigned += v[~ok].sum()
        for k, (la, lo) in enumerate(keys):
            share = acc[k] / len(pts[(la, lo)])
            for z in pts[(la, lo)]: rows_out.append([iso, z, la, lo, int(round(share))])
        print(f"{iso}: {len(rows):,} zip rows at {len(keys):,} points, {total / 1e6:,.2f} M people in its border, "
              f"{unassigned / max(total, 1):.1%} further than {MAX_KM} km from any zip point, {time.time() - t1:.0f}s", flush=True)

    rows_out = kept + rows_out
    rows_out.sort(key=lambda r: (r[0], r[1], float(r[2]), float(r[3])))
    with io.TextIOWrapper(gzip.GzipFile(OUT, "wb", mtime=0), encoding="utf-8", newline="") as fh:
        w = csv.writer(fh); w.writerow(["iso2", "zip", "lat", "lon", "pop"]); w.writerows(rows_out)
    hh = household_sizes(folder)
    with open(HH_OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh); w.writerow(["iso2", "size", "year", "source"])
        for iso in sorted(hh): w.writerow([iso, hh[iso][0], hh[iso][1], hh[iso][2]])
    print(f"done in {time.time() - t0:.0f}s: {len(rows_out):,} rows -> {OUT}; household sizes for {len(hh)} countries -> {HH_OUT}")


if __name__ == "__main__":
    main(sys.argv[1], set(sys.argv[2:]))
