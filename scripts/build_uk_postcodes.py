"""UK: every live postcode with its coordinates, from the ONS Postcode Directory (ONSPD).

Usage:  python scripts/build_uk_postcodes.py path/to/ONSPD_AUG_2026.zip

Input: the quarterly ONSPD from the ONS Open Geography Portal (search "ONS Postcode Directory"; Open Government Licence,
contains Royal Mail, OS and ONS data). The ZIP is read as a stream and never unpacked.
Output: data/uk_postcodes.csv.gz with postcode (as written, e.g. "SS2 6BH"), lat, lon, for live postcodes (no
termination date) that have coordinates. The database build turns it into a lookup table, so a full postcode typed in
the search goes to its own point; the zip tab itself keeps the postcode districts (SS2).
"""
import csv, gzip, io, os, sys, zipfile

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "uk_postcodes.csv.gz")


def main(path):
    z = zipfile.ZipFile(path)
    name = next(n for n in z.namelist() if n.lower().startswith("data/onspd_") and n.lower().endswith("_uk.csv"))
    n = kept = 0
    with z.open(name) as fh, gzip.open(OUT, "wt", encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        w.writerow(["postcode", "lat", "lon"])
        for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig", newline="")):
            n += 1
            if r["doterm"].strip(): continue  # terminated
            try: la, lo = float(r["lat"]), float(r["long"])
            except ValueError: continue
            if la > 90: continue  # 99.999999 = no grid reference
            w.writerow([r["pcds"].strip(), round(la, 5), round(lo, 5)]); kept += 1
    print(f"{name}: {n:,} postcodes, {kept:,} live with coordinates -> {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1])
