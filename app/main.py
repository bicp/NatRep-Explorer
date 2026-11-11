"""NatRep Explorer – FastAPI backend serving Nat Rep figures and zip code search from data/natrep.db."""
import csv
import io
import json
import math
import os
import re
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("NATREP_DB", os.path.join(BASE, "..", "data", "natrep.db"))
MAX_RADIUS_KM = 500
MAX_ROWS = 50000

app = FastAPI(title="NatRep Explorer", version="1.0")


def db():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def rows(sql, args=()):
    with db() as con:
        return [dict(r) for r in con.execute(sql, args)]


def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- Nat Rep
@app.get("/api/meta")
def meta():
    m = {r["key"]: r["value"] for r in rows("SELECT key, value FROM meta")}
    m["hhi_columns"] = json.loads(m.get("hhi_columns", "[]"))
    m["zip_rows"] = rows("SELECT count(*) n FROM zips")[0]["n"]
    m["countries"] = rows("SELECT count(*) n FROM countries")[0]["n"]
    return m


@app.get("/api/countries")
def countries():
    out = rows("SELECT name, iso2, zip_records, internet, has_coords, notes, layout FROM countries ORDER BY name")
    ag = rows("SELECT country, gender, age, share FROM age_gender")
    ed = rows("SELECT country, level, share FROM education WHERE classification LIKE 'ISCED%' ORDER BY ord")
    hhi = {r["country"]: json.loads(r["data_json"]) for r in rows("SELECT country, data_json FROM refs WHERE kind='hhi'")}
    for c in out:
        mine = [a for a in ag if a["country"] == c["name"]]
        c["male"] = sum(a["share"] or 0 for a in mine if a["gender"] == "Male")
        c["age"] = {}
        for a in mine:
            c["age"][a["age"]] = c["age"].get(a["age"], 0) + (a["share"] or 0)
        e = [x for x in ed if x["country"] == c["name"]]
        c["tertiary"] = next((x["share"] for x in e if x["level"].lower().startswith("tertiary")), None)
        h = hhi.get(c["name"], {})
        c["hhi_median_eur"] = h.get("Median EUR Est")
        c["hhi_confidence"] = h.get("Confidence")
    return out


@app.get("/api/countries/{name}")
def country(name: str):
    c = rows("SELECT * FROM countries WHERE name = ?", (name,))
    if not c:
        raise HTTPException(404, "Unknown country")
    c = c[0]
    c["warnings"] = json.loads(c["warnings"] or "[]")
    c["home_note"] = c.pop("notes")
    c["age_gender"] = rows("SELECT gender, age, share FROM age_gender WHERE country=? ORDER BY gender DESC, age", (name,))
    c["education"] = rows("SELECT classification, level, share FROM education WHERE country=? ORDER BY ord", (name,))
    c["income"] = rows("SELECT band, lower, upper, share, currency, year, definition, source, confidence, base FROM income WHERE country=? ORDER BY ord", (name,))
    c["notes"] = [r["text"] for r in rows("SELECT text FROM notes WHERE country=? ORDER BY ord", (name,))]
    c["extras"] = [dict(title=r["title"], sheet=r["source_sheet"], rows=json.loads(r["rows_json"]))
                   for r in rows("SELECT title, source_sheet, rows_json FROM extras WHERE country=? ORDER BY ord", (name,))]
    ref = {r["kind"]: json.loads(r["data_json"]) for r in rows("SELECT kind, data_json FROM refs WHERE country=?", (name,))}
    c["hhi"] = ref.get("hhi")
    c["sources"] = ref.get("sources")
    c["regions"] = rows("SELECT admin1, count(*) n FROM zips WHERE country=? AND admin1<>'' GROUP BY admin1 ORDER BY admin1", (name,))
    return c


# ---------------------------------------------------------------- Locations
def fts_query(q: str) -> str:
    toks = re.findall(r"\w+", q, flags=re.UNICODE)
    return " ".join(f'"{t}"*' for t in toks)


@app.get("/api/search")
def search(q: str = Query(..., min_length=1), country: Optional[str] = None, limit: int = 60):
    q = q.strip()
    args, where = [], []
    if country:
        where.append("z.country = ?")
        args.append(country)
    w = (" AND " + " AND ".join(where)) if where else ""
    # 1) exact / prefix zip match first
    zq = re.sub(r"\s+", "", q)
    res = rows(f"""SELECT z.id, z.country, z.iso2, z.zip, z.place, z.admin1, z.admin2, z.admin3, z.lat, z.lon FROM zips z
                   WHERE (replace(z.zip,' ','') = ? COLLATE NOCASE OR replace(z.zip,' ','') LIKE ? COLLATE NOCASE){w}
                   ORDER BY length(z.zip), z.zip LIMIT ?""", [zq, zq + "%", *args, limit])
    # 2) full-text over place / regions
    ft = fts_query(q)
    if ft and len(res) < limit:
        seen = {r["id"] for r in res}
        more = rows(f"""SELECT z.id, z.country, z.iso2, z.zip, z.place, z.admin1, z.admin2, z.admin3, z.lat, z.lon
                        FROM zips_fts f JOIN zips z ON z.id = f.rowid
                        WHERE zips_fts MATCH ?{w} ORDER BY (lower(z.place) = lower(?)) DESC, f.rank LIMIT ?""",
                    [ft, *args, q, limit * 3])
        for r in more:
            if r["id"] not in seen and len(res) < limit:
                res.append(r)
                seen.add(r["id"])
    # cities / places grouped (one entry per place, with zip count and centre point)
    places = []
    if ft:
        places = rows(f"""SELECT z.country, z.iso2, z.place, z.admin1, z.admin2, count(DISTINCT z.zip) zips,
                                 avg(z.lat) lat, avg(z.lon) lon, min(z.zip) first_zip
                          FROM zips_fts f JOIN zips z ON z.id = f.rowid
                          WHERE zips_fts MATCH ?{w}
                          GROUP BY z.country, z.place, z.admin1
                          ORDER BY (lower(z.place) = lower(?)) DESC, zips DESC LIMIT 15""",
                      [f"place : ({ft})", *args, q])
    # region suggestions (admin1/admin2 names that match)
    regions = []
    if len(q) >= 2:
        for level in ("admin1", "admin2"):
            regions += rows(f"""SELECT country, '{level}' level, {level} value, count(*) n FROM zips z
                                WHERE {level} LIKE ? COLLATE NOCASE{w} GROUP BY country, {level} ORDER BY n DESC LIMIT 8""",
                            [q + "%", *args])
    return {"results": res, "places": places, "regions": regions[:12]}


def radius_rows(lat, lon, km, country=None):
    if km <= 0 or km > MAX_RADIUS_KM:
        raise HTTPException(400, f"Radius must be between 0 and {MAX_RADIUS_KM} km")
    dlat = km / 110.574
    dlon = km / (111.320 * max(math.cos(math.radians(lat)), 0.01))
    args = [lat - dlat, lat + dlat, lon - dlon, lon + dlon]
    w = ""
    if country:
        w = " AND z.country = ?"
        args.append(country)
    cand = rows(f"""SELECT z.country, z.iso2, z.zip, z.place, z.admin1, z.admin2, z.admin3, z.lat, z.lon
                    FROM zips_geo g JOIN zips z ON z.id = g.id
                    WHERE g.minlat >= ? AND g.maxlat <= ? AND g.minlon >= ? AND g.maxlon <= ?{w}""", args)
    by_zip = {}
    for r in cand:
        d = haversine(lat, lon, r["lat"], r["lon"])
        if d > km:
            continue
        key = (r["country"], r["zip"])
        cur = by_zip.get(key)
        if cur is None:
            r["distance_km"] = round(d, 2)
            r["places"] = [r["place"]] if r["place"] else []
            by_zip[key] = r
        else:
            if r["place"] and r["place"] not in cur["places"]:
                cur["places"].append(r["place"])
            if d < cur["distance_km"]:
                cur.update({k: r[k] for k in ("place", "admin1", "admin2", "admin3", "lat", "lon")})
                cur["distance_km"] = round(d, 2)
    out = sorted(by_zip.values(), key=lambda r: r["distance_km"])
    return out[:MAX_ROWS], len(out) > MAX_ROWS


@app.get("/api/radius")
def radius(lat: float, lon: float, km: float = 10, country: Optional[str] = None):
    res, truncated = radius_rows(lat, lon, km, country)
    return {"center": {"lat": lat, "lon": lon}, "km": km, "count": len(res), "truncated": truncated, "results": res}


@app.get("/api/area")
def area(country: str, level: str = "admin1", value: str = "", within: Optional[str] = None):
    extra, args = "", [country, value]
    if within is not None and level != "admin1":
        extra, args = " AND admin1=?", [country, value, within]
    if level not in ("admin1", "admin2", "admin3", "place"):
        raise HTTPException(400, "bad level")
    res = rows(f"""SELECT country, iso2, zip, group_concat(DISTINCT place) places, min(place) place, admin1, admin2, admin3,
                   avg(lat) lat, avg(lon) lon FROM zips WHERE country=? AND {level}=?{extra}
                   GROUP BY zip ORDER BY zip LIMIT ?""", (*args, MAX_ROWS))
    for r in res:
        r["places"] = (r["places"] or "").split(",")
    return {"country": country, "level": level, "value": value, "count": len(res), "results": res}


def to_csv(res, filename):
    buf = io.StringIO()
    buf.write("﻿")  # Excel-friendly UTF-8
    w = csv.writer(buf)
    cols = ["country", "zip", "place", "admin1", "admin2", "admin3", "lat", "lon", "distance_km", "places"]
    w.writerow(["Country", "Zip code", "Place", "State / Region", "Province / County", "Community", "Latitude", "Longitude", "Distance (km)", "All places in zip"])
    for r in res:
        w.writerow([("; ".join(r.get(c) or []) if c == "places" else r.get(c, "")) for c in cols])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/radius.csv")
def radius_csv(lat: float, lon: float, km: float = 10, country: Optional[str] = None, label: str = "location"):
    res, _ = radius_rows(lat, lon, km, country)
    safe = re.sub(r"[^\w-]+", "_", label)[:40]
    return to_csv(res, f"zips_{safe}_{km:g}km.csv")


@app.get("/api/area.csv")
def area_csv(country: str, level: str = "admin1", value: str = "", within: Optional[str] = None):
    res = area(country, level, value, within)["results"]
    safe = re.sub(r"[^\w-]+", "_", f"{country}_{value}")[:50]
    return to_csv(res, f"zips_{safe}.csv")


# ---------------------------------------------------------------- Static UI
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))
