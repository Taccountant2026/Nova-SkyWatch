"""NOVA SkyWatch: local orbital prediction and dashboard service."""
import argparse
import csv
import gzip
import io
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler

from flask import Flask, jsonify, request, send_from_directory
from skyfield.api import EarthSatellite, load, wgs84

ROOT = Path(__file__).resolve().parent
CACHE = Path.home() / ".nova-skywatch"
REFRESH = 7200
MAX_EPOCH_DAYS = 7
GROUPS = ("stations", "visual")
UTC = timezone.utc
app = Flask(__name__, static_folder=None)
ts = load.timescale(builtin=True)
lock = threading.RLock()
catalogue = {}
pass_cache = {}
last_check = 0
warning = ""
settings = {}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def parse(raw):
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows or "NORAD_CAT_ID" not in rows[0]:
        raise ValueError("Invalid CelesTrak CSV")
    return {str(s.model.satnum): s for s in
            (EarthSatellite.from_omm(ts, row) for row in rows)}

def get_group(group):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / (group + ".csv")
    attempt = CACHE / (group + ".attempt")
    now = time.time()
    if path.exists() and now - path.stat().st_mtime < REFRESH:
        return parse(path.read_bytes()), ""
    error = "Download retry deferred for two hours"
    if not attempt.exists() or now - attempt.stat().st_mtime >= REFRESH:
        attempt.touch()
        try:
            url = "https://celestrak.org/NORAD/elements/gp.php?GROUP=" + group + "&FORMAT=CSV"
            req = Request(url, headers={"User-Agent": "Nova-SkyWatch/0.2", "Accept-Encoding": "gzip"})
            with build_opener(NoRedirect()).open(req, timeout=20) as resp:
                raw = resp.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise ValueError("Catalogue response too large")
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            result = parse(raw)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(path)
            return result, ""
        except Exception as exc:
            error = str(exc)
    if path.exists():
        return parse(path.read_bytes()), "Cached catalogue: " + error
    raise RuntimeError(error)

def refresh_catalogue():
    global last_check, catalogue, warning
    if last_check and time.monotonic() - last_check < REFRESH:
        return
    last_check = time.monotonic()
    fresh, errors = {}, []
    for group in settings["groups"]:
        try:
            result, note = get_group(group)
            fresh.update(result)
            if note:
                errors.append(group + ": " + note)
        except Exception as exc:
            errors.append(group + ": " + str(exc))
    if fresh:
        catalogue = fresh
        pass_cache.clear()
    warning = "; ".join(errors)
    if not catalogue:
        raise RuntimeError(warning or "No orbital data available")

def position(sat, site, t):
    alt, az, distance = (sat - site).at(t).altaz()
    values = [float(alt.degrees), float(az.degrees), float(distance.km)]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Orbital propagation failed")
    return dict(elevation=round(values[0], 2), azimuth=round(values[1], 2),
                range_km=round(values[2], 1))

def epoch_age(sat, now):
    return abs((now - sat.epoch.utc_datetime()).total_seconds()) / 86400

def predict(sat, site, now, threshold):
    # Search from one hour ago to include a pass already in progress.
    start = ts.from_datetime(now - timedelta(hours=1))
    end = ts.from_datetime(now + timedelta(hours=24))
    times, events = sat.find_events(site, start, end, altitude_degrees=threshold)
    passes = []
    rise, peak = None, None
    for t, event in zip(times, events):
        stamp = t.utc_datetime()
        if event == 0:
            rise, peak = stamp, None
        elif event == 1 and rise is not None:
            candidate = position(sat, site, t)["elevation"]
            if peak is None or candidate > peak[1]:
                peak = (stamp, candidate)
        elif event == 2 and rise is not None:
            if stamp >= now and peak is not None:
                passes.append({"rise": rise.isoformat(), "peak": peak[0].isoformat(),
                               "set": stamp.isoformat(), "peak_elevation": peak[1]})
            rise, peak = None, None
    return passes

def pass_track(sat, site, item):
    a = datetime.fromisoformat(item["rise"])
    b = datetime.fromisoformat(item["set"])
    return [position(sat, site, ts.from_datetime(a + (b-a)*i/40)) for i in range(41)]

@app.after_request
def no_cache(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.get("/")
def dashboard():
    return send_from_directory(ROOT, "index.html")

@app.get("/simulation")
def simulation():
    return send_from_directory(ROOT, "simulation.html")

@app.get("/api/status")
def status():
    with lock:
        try:
            refresh_catalogue()
            now = datetime.now(UTC)
            available = {key: sat for key, sat in catalogue.items()
                         if epoch_age(sat, now) <= MAX_EPOCH_DAYS}
            if not available:
                raise RuntimeError("No orbital elements within seven days of current UTC time")
            requested = request.args.get("satellite")
            key = requested or ("25544" if "25544" in available else next(iter(available)))
            if key not in available:
                return jsonify(error="Satellite unavailable or orbital elements too old"), 404
            sat = available[key]
            site = settings["site"]
            threshold = settings["threshold"]
            cached = pass_cache.get(key)
            if cached is None or time.monotonic() - cached[0] >= 60:
                passes = predict(sat, site, now, threshold)
                for item in passes[:1]:
                    item["track"] = pass_track(sat, site, item)
                pass_cache[key] = (time.monotonic(), passes)
            else:
                passes = cached[1]
            upcoming = [p for p in passes if datetime.fromisoformat(p["set"]) >= now]
            next_pass = upcoming[0] if upcoming else None
            if next_pass and "track" not in next_pass:
                next_pass["track"] = pass_track(sat, site, next_pass)
            p = position(sat, site, ts.from_datetime(now))
            return jsonify(timestamp=now.isoformat(), source="CelesTrak / Skyfield SGP4",
                           warning=warning, threshold=threshold, satellite=key, name=sat.name,
                           epoch=sat.epoch.utc_datetime().isoformat(), **p,
                           above_threshold=p["elevation"] >= threshold,
                           next_pass=next_pass,
                           passes=[{k:v for k,v in p.items() if k != "track"} for p in upcoming],
                           catalogue=[{"id":k,"name":s.name} for k,s in sorted(available.items(),
                                      key=lambda x: (x[0] != "25544", x[1].name))])
        except Exception as exc:
            return jsonify(error=str(exc)), 503

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", required=True, type=float)
    parser.add_argument("--lon", required=True, type=float)
    parser.add_argument("--threshold", type=float, default=20)
    parser.add_argument("--group", choices=GROUPS, default="stations")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not (-90 <= args.lat <= 90 and -180 <= args.lon <= 180 and
            0 <= args.threshold < 90 and 1024 <= args.port <= 65535):
        parser.error("Invalid location, threshold or port")
    settings.update(site=wgs84.latlon(args.lat, args.lon),
                    threshold=args.threshold, groups=[args.group])
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)

if __name__ == "__main__":
    main()
