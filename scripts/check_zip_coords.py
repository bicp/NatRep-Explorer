"""Check the zip coordinates in the built database (data/natrep.db) and list suspicious rows.

Usage:  python scripts/check_zip_coords.py [natrep.db] [report.csv]

Three checks, each per country:
  1. outside the country: the point is outside the country's bounding boxes (BOX, generous, with islands/territories),
  2. far from its region: more than max(120 km, 6 × the region's median spread) from the median of its admin1/admin2
     group; flagged "decimal point slipped" when ×10 or ÷10 of the latitude or longitude puts it back in the group,
  3. landmarks: well-known postcodes (LANDMARKS) must be within 15 km of the known location.
Prints a summary and writes every flagged row to the CSV (default: zip_coordinate_check.csv in the current folder).
Remote islands, huge regions (Western Australia, Siberian districts) and business-only codes show up as "far" too, so
read the CSV before changing anything; the fixes themselves go through data/zip_coord_fixes.csv and apply_zip_fixes.py.
"""
import collections, csv, math, os, statistics as st, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "data", "natrep.db")
OUT = sys.argv[2] if len(sys.argv) > 2 else "zip_coordinate_check.csv"

BOX = {"AR": [(-55.2, -21.7, -73.7, -53.5)], "AU": [(-44, -9, 112.5, 154), (-12.3, -10.3, 96.7, 105.8), (-29.2, -28.9, 167.8, 168.1), (-31.7, -31.4, 159, 159.2)],
       "AT": [(46.3, 49.1, 9.5, 17.2)], "BE": [(49.4, 51.6, 2.5, 6.5)], "BG": [(41.2, 44.3, 22.3, 28.7)], "BR": [(-34, 5.4, -74.1, -28.8)],
       "CA": [(41.6, 83.2, -141.1, -52.5)], "CL": [(-56, -17.4, -76, -66.3), (-27.3, -27, -109.5, -109.2), (-33.8, -33.5, -80.9, -78.7), (-90, -60, -90, -50)],
       "CN": [(15.5, 53.6, 73.5, 135.1)], "CO": [(-4.3, 12.6, -79.1, -66.8), (12.4, 13.5, -81.8, -81.3)], "CR": [(8, 11.3, -86, -82.5), (5.4, 5.6, -87.2, -86.9)],
       "HR": [(42.3, 46.6, 13.4, 19.5)], "CZ": [(48.5, 51.1, 12, 18.9)], "DK": [(54.5, 57.8, 8, 15.2)], "DO": [(17.4, 20, -72.1, -68.3)],
       "EC": [(-5.1, 1.5, -81.1, -75.1), (-1.5, 1.7, -92.1, -89.2)], "EE": [(57.5, 59.7, 21.7, 28.3)], "FR": [(41.3, 51.1, -5.2, 9.6), (10.2, 10.4, -109.3, -109.1)],
       "DE": [(47.2, 55.1, 5.8, 15.1)], "GT": [(13.7, 17.9, -92.3, -88.2)], "HU": [(45.7, 48.6, 16.1, 22.9)], "IS": [(63.2, 66.6, -24.6, -13.4)],
       "IE": [(51.4, 55.4, -10.7, -5.9)], "IT": [(35.4, 47.1, 6.6, 18.6)], "JP": [(20.4, 45.6, 122.9, 154)], "LV": [(55.6, 58.1, 20.9, 28.3)],
       "LT": [(53.8, 56.5, 20.9, 26.9)], "LU": [(49.4, 50.2, 5.7, 6.6)], "MT": [(35.8, 36.1, 14.1, 14.6)], "MX": [(14.5, 32.8, -118.5, -86.7)],
       "MC": [(43.7, 43.77, 7.39, 7.45)], "MA": [(20.7, 35.95, -17.1, -1)], "NL": [(50.7, 53.6, 3.3, 7.3)],
       "NZ": [(-47.4, -34.3, 166.3, 178.6), (-44.4, -43.6, -177, -176.1)], "NO": [(57.9, 71.3, 4.4, 31.2)], "PA": [(7.1, 9.7, -83.1, -77.1)],
       "PE": [(-18.4, -0.03, -81.4, -68.6)], "PH": [(4.5, 21.2, 116.9, 126.7)], "PL": [(49, 54.9, 14.1, 24.2)],
       "PT": [(36.9, 42.2, -9.6, -6.1), (32.3, 33.2, -17.3, -16.2), (36.9, 39.8, -31.3, -24.9)], "PR": [(17.8, 18.6, -67.4, -65.2)],
       "RO": [(43.6, 48.3, 20.2, 29.8)], "RU": [(41.1, 82, 19.6, 180), (64, 72, -180, -168)], "RS": [(42.2, 46.2, 18.8, 23.1)],
       "SG": [(1.15, 1.48, 103.6, 104.1)], "SK": [(47.7, 49.7, 16.8, 22.6)], "SI": [(45.4, 46.9, 13.3, 16.6)], "ZA": [(-34.9, -22.1, 16.4, 33)],
       "KR": [(33.1, 38.7, 124.5, 131.9)], "ES": [(35.9, 43.8, -9.4, 4.4), (27.6, 29.5, -18.2, -13.3), (35.2, 35.95, -5.4, -2.9)],
       "SE": [(55.3, 69.1, 10.9, 24.2)], "CH": [(45.8, 47.9, 5.9, 10.5)], "TH": [(5.6, 20.5, 97.3, 105.7)], "TR": [(35.8, 42.2, 25.6, 44.9), (34.9, 35.75, 32.2, 34.7)],
       "GB": [(49.8, 60.9, -8.7, 1.8), (49.1, 49.8, -2.7, -2)], "UY": [(-35.1, -30, -58.5, -53)],
       "US": [(24.4, 49.4, -125, -66.9), (51, 71.5, -180, -129.9), (51, 53, 172, 180), (18.9, 22.3, -160.3, -154.7), (17.6, 18.6, -67.4, -64.5),
              (13.2, 20.6, 144.6, 146.1), (-14.4, -14.1, -170.9, -169.4)]}
# (iso2, zip or SQL LIKE pattern, lat, lon) of well-known places
LANDMARKS = [("AT", "1010", 48.21, 16.37), ("AT", "8010", 47.07, 15.44), ("BE", "1000", 50.85, 4.35), ("BG", "1000", 42.70, 23.32), ("BR", "01000-001%", -23.55, -46.63),
             ("BR", "20000-001%", -22.91, -43.2), ("CA", "M5V", 43.64, -79.39), ("CL", "8320000", -33.44, -70.65), ("CN", "100000", 39.9, 116.4),
             ("CR", "10101", 9.93, -84.08), ("HR", "10000", 45.81, 15.98), ("CZ", "110 00", 50.08, 14.42), ("DK", "1050", 55.68, 12.58), ("EE", "10111", 59.44, 24.75),
             ("FR", "75001", 48.86, 2.34), ("FR", "13001", 43.30, 5.38), ("DE", "10115", 52.53, 13.38), ("DE", "80331", 48.14, 11.57), ("GT", "01001", 14.63, -90.51),
             ("HU", "1011", 47.5, 19.04), ("IS", "101", 64.15, -21.94), ("IE", "D02", 53.34, -6.25), ("IT", "00184", 41.89, 12.49), ("IT", "20121", 45.47, 9.19),
             ("JP", "100-0001", 35.68, 139.75), ("LV", "LV-1050", 56.95, 24.11), ("LT", "01100", 54.68, 25.28), ("LU", "L-1111", 49.61, 6.13), ("MT", "VLT", 35.9, 14.51),
             ("MX", "06000", 19.43, -99.13), ("MC", "98000", 43.73, 7.42), ("MA", "10000", 34.02, -6.83), ("MA", "20000", 33.59, -7.6), ("NL", "1012", 52.37, 4.89),
             ("NZ", "1010", -36.85, 174.76), ("NO", "0150", 59.91, 10.75), ("PE", "15001", -12.05, -77.04), ("PH", "1000", 14.59, 120.98),
             ("PL", "00-002", 52.23, 21.01), ("PT", "1100-148", 38.71, -9.14), ("PR", "00901", 18.46, -66.1), ("RO", "010011", 44.44, 26.1),
             ("RU", "101000", 55.76, 37.63), ("RU", "140410", 55.08, 38.78), ("RS", "11000", 44.82, 20.46), ("SG", "018989", 1.28, 103.85),
             ("SK", "811 01", 48.14, 17.11), ("SI", "1000", 46.05, 14.51), ("ZA", "2001", -26.2, 28.04), ("ES", "28013", 40.42, -3.71), ("ES", "08001", 41.38, 2.17),
             ("SE", "111 20", 59.33, 18.06), ("CH", "8001", 47.37, 8.54), ("CH", "1201", 46.21, 6.14), ("CH", "3011", 46.95, 7.45), ("CH", "4051", 47.55, 7.59),
             ("TH", "10200", 13.75, 100.49), ("TR", "34110", 41.01, 28.95), ("GB", "SW1A", 51.50, -0.14), ("GB", "M1", 53.48, -2.24), ("GB", "EH1", 55.95, -3.19),
             ("US", "10001", 40.75, -74.0), ("US", "90012", 34.06, -118.24), ("UY", "11000", -34.9, -56.2), ("AR", "1425", -34.58, -58.42),
             ("AR", "5000", -31.42, -64.18), ("AU", "2000", -33.87, 151.21), ("AU", "3000", -37.81, 144.96)]


def km(a, b, c, d):
    p = math.pi / 180
    x = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(x)))


def main():
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT iso2, zip, place, admin1, admin2, lat, lon FROM zips WHERE lat IS NOT NULL").fetchall()
    tot, flags = collections.Counter(r[0] for r in rows), []
    for r in rows:
        if r[0] in BOX and not any(a <= r[5] <= b and c <= r[6] <= d for a, b, c, d in BOX[r[0]]):
            flags.append((*r, "outside the country", ""))
    groups = collections.defaultdict(list)
    for r in rows:
        if r[3] or r[4]: groups[(r[0], r[3] or "", r[4] or "")].append(r)
    for g in groups.values():
        if len(g) < 4: continue
        mla, mlo = st.median(r[5] for r in g), st.median(r[6] for r in g)
        d = [km(mla, mlo, r[5], r[6]) for r in g]
        lim = max(120, 6 * st.median(d))
        for r, dd in zip(g, d):
            if dd <= lim: continue
            alt = [(r[5] * f, r[6]) for f in (10, .1)] + [(r[5], r[6] * f) for f in (10, .1)]
            kind = "decimal point slipped" if any(km(mla, mlo, a, b) <= lim for a, b in alt) else "far from its region"
            flags.append((*r, kind, f"{dd:.0f} km from region median {mla:.4f},{mlo:.4f}"))
    by = collections.defaultdict(collections.Counter)
    for f in flags: by[f[0]][f[7]] += 1
    print("country  rows  flagged")
    for iso in sorted(tot):
        if by[iso]: print(f"  {iso}  {tot[iso]:>7,}  " + ", ".join(f"{k}: {v}" for k, v in by[iso].items()))
    print("landmarks (median km off):")
    bad = 0
    for iso, z, la, lo in LANDMARKS:
        op = "LIKE" if "%" in z else "="
        rs = db.execute(f"SELECT lat, lon FROM zips WHERE iso2=? AND zip {op} ? AND lat IS NOT NULL LIMIT 200", (iso, z)).fetchall()
        if not rs: print(f"  {iso} {z}: not found"); bad += 1; continue
        m = st.median(km(la, lo, a, b) for a, b in rs)
        if m > 15: print(f"  {iso} {z}: {m:.0f} km off"); bad += 1
    print(f"  {len(LANDMARKS) - bad} of {len(LANDMARKS)} within 15 km")
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(["iso2", "zip", "place", "admin1", "admin2", "lat", "lon", "issue", "detail"]); w.writerows(sorted(flags))
    print(f"{len(flags):,} flagged rows in {OUT}")


if __name__ == "__main__":
    main()
