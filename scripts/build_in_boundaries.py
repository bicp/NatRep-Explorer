"""India: points and people per pincode from India Post's official pincode boundaries.

Usage:  python scripts/build_in_boundaries.py data/in_boundaries/Datagov_Pincode_Boundaries.parquet data/popgrid

Inputs (not in git):
  Datagov_Pincode_Boundaries.parquet  "All India Pincode Boundary geo-json" (Department of Posts, data.gov.in,
      Government Open Data Licence – India), 19,312 pincode areas. data.gov.in was unavailable in 2026; the same file is
      mirrored at https://github.com/ramSeraph/indian_admin_boundaries/releases/tag/postal (GeoParquet).
  data/popgrid/  the GHSL population grid, as for scripts/estimate_population.py
and data/in_pincodes.csv.gz (GeoNames points per row, scripts/build_in_pincodes.py). Needs pyarrow, shapely, rasterio.

Points: a row whose GeoNames point lies inside its own pincode's area keeps it (usually the post office's village);
any other row moves to a point inside its pincode's area. Pincodes without a boundary keep their GeoNames points.
People: the GHSL 2025 grid cells whose centre lies inside the pincode's area; an area too small to hold a cell centre
gets its share of the cell it sits in (by area).

Output:
  data/in_pincode_points.csv.gz  zip, place, admin2, lat, lon, point  (replaces in_pincodes.csv.gz in the build)
  data/in_pincode_areas.csv.gz   pincode, lat, lon (a point inside the area), people
"""
import collections, csv, glob, gzip, io, math, os, sys
import numpy as np
import pyarrow.parquet as pq
import rasterio
import shapely
from rasterio import features
from rasterio.windows import from_bounds

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
SRC = os.path.join(DATA, "in_pincodes.csv.gz")
POINTS = os.path.join(DATA, "in_pincode_points.csv.gz")
AREAS = os.path.join(DATA, "in_pincode_areas.csv.gz")


def write(path, header, rows):
    with io.TextIOWrapper(gzip.GzipFile(path, "wb", mtime=0), encoding="utf-8", newline="") as fh:
        w = csv.writer(fh); w.writerow(header); w.writerows(rows)


def main(parquet, popgrid):
    t = pq.read_table(parquet, columns=["Pincode", "geometry"]).to_pydict()
    geoms = shapely.make_valid(shapely.from_wkb(t["geometry"]))
    by = collections.defaultdict(list)
    for p, g in zip(t["Pincode"], geoms): by[str(p).strip()].append(g)
    poly = {p: (shapely.union_all(gs) if len(gs) > 1 else gs[0]) for p, gs in by.items()}
    for g in poly.values(): shapely.prepare(g)
    inner = {p: shapely.point_on_surface(g) for p, g in poly.items()}

    # people per area
    grid = rasterio.open(glob.glob(os.path.join(popgrid, "GHS_POP_*_30ss_*.tif"))[0])
    keys = sorted(poly)
    win = from_bounds(*shapely.total_bounds([poly[k] for k in keys]), transform=grid.transform).round_offsets().round_lengths()
    pop = grid.read(1, window=win); pop[~(pop > 0)] = 0
    tr = grid.window_transform(win)
    ids = features.rasterize(((poly[k], i + 1) for i, k in enumerate(keys)), out_shape=pop.shape, transform=tr, fill=0, dtype="int32")
    people = np.bincount(ids.ravel(), weights=pop.ravel(), minlength=len(keys) + 1)[1:]
    cells = np.bincount(ids.ravel(), minlength=len(keys) + 1)[1:]
    cell_area = abs(tr.a * tr.e)
    tiny = 0
    for i, k in enumerate(keys):
        if cells[i]: continue
        r, c = rasterio.transform.rowcol(tr, inner[k].x, inner[k].y)
        if 0 <= r < pop.shape[0] and 0 <= c < pop.shape[1]:
            people[i] = pop[r, c] * min(poly[k].area / cell_area, 1.0); tiny += 1
    write(AREAS, ["pincode", "lat", "lon", "people"],
          [[k, round(inner[k].y, 5), round(inner[k].x, 5), int(round(people[i]))] for i, k in enumerate(keys)])

    # points per row
    out, n = [], collections.Counter()
    with gzip.open(SRC, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            g = poly.get(r["zip"])
            if g is None: n["no boundary"] += 1
            elif g.contains(shapely.Point(float(r["lon"]), float(r["lat"]))): n["inside"] += 1
            else:
                r["lat"], r["lon"] = round(inner[r["zip"]].y, 5), round(inner[r["zip"]].x, 5)
                r["point"] = "inside its pincode area (India Post boundary)"; n["moved"] += 1
            out.append([r["zip"], r["place"], r["admin2"], r["lat"], r["lon"], r["point"]])
    write(POINTS, ["zip", "place", "admin2", "lat", "lon", "point"], out)
    print(f"{len(poly):,} pincode areas, {people.sum() / 1e6:,.0f} M people ({tiny} areas smaller than a grid cell) -> {AREAS}")
    print(f"{len(out):,} rows: {n['inside']:,} kept (inside their area), {n['moved']:,} moved inside it, "
          f"{n['no boundary']:,} without a boundary -> {POINTS}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
