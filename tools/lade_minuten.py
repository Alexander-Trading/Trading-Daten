#!/usr/bin/env python3
"""Laedt Dukascopy-1-Minuten-Kerzen (Bid+Ask) direkt vom statischen Datafeed,
aggregiert auf 5/15 Minuten und schreibt data/min/<INSTR>_<tf>.csv.gz.

Aufruf: python lade_minuten.py EURUSD 2012 2026 m15,m5 [ausgabeordner]
"""
import sys, os, lzma, struct, time, datetime as dt, threading
from concurrent.futures import ThreadPoolExecutor
import urllib.request, urllib.error
import pandas as pd

BASE = "https://datafeed.dukascopy.com/datafeed"
FAKTOR = {"EURUSD": 1e5, "USDJPY": 1e3, "DEUIDXEUR": 1e3, "USA500IDXUSD": 1e3}
TIMEOUT, RETRIES = 12, 14
DEADLINE = time.time() + float(os.environ.get("DEADLINE_MIN", "300")) * 60
lock = threading.Lock()
stat = {"ok": 0, "leer": 0, "fehler": 0}


def hole(url):
    """Gibt bytes zurueck (leer = kein Handel), None = endgueltig fehlgeschlagen."""
    for v in range(RETRIES):
        if time.time() > DEADLINE:
            return None
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=TIMEOUT) as r:
                if r.status == 200:
                    return r.read()
                time.sleep(min(1 + v, 5))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            time.sleep(min(1 + v, 5))
        except Exception:
            time.sleep(min(1 + v, 5))
    return None


def parse(raw, tag_start, faktor):
    if not raw:
        return None
    d = lzma.decompress(raw)
    n = len(d) // 24
    if n == 0:
        return None
    rows = struct.unpack(">" + "iiiiif" * n, d[:n * 24])
    a = pd.DataFrame({
        "sec": rows[0::6], "o": rows[1::6], "c": rows[2::6],
        "l": rows[3::6], "h": rows[4::6], "v": rows[5::6]})
    a["t"] = tag_start + pd.to_timedelta(a.pop("sec"), unit="s")
    for k in "ochl":
        a[k] = a[k] / faktor
    return a


def tag(instr, day, seite, faktor):
    url = f"{BASE}/{instr}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{seite}_candles_min_1.bi5"
    raw = hole(url)
    if raw is None:
        with lock:
            stat["fehler"] += 1
        return day, seite, "FEHLER"
    try:
        df = parse(raw, pd.Timestamp(day), faktor)
    except Exception as e:
        print("Parse-Fehler", url, e, flush=True)
        with lock:
            stat["fehler"] += 1
        return day, seite, "FEHLER"
    with lock:
        stat["ok" if df is not None else "leer"] += 1
    return day, seite, df


def lade(instr, y0, y1, tfs, out):
    faktor = FAKTOR[instr]
    heute = dt.date.today()
    tage = [d.date() for d in pd.date_range(f"{y0}-01-01", min(pd.Timestamp(f"{y1}-12-31"), pd.Timestamp(heute)))
            if d.weekday() != 5]
    auftraege = [(d, s) for d in tage for s in ("BID", "ASK")]
    res = {"BID": [], "ASK": []}
    fehl = []
    t0 = time.time()
    with ThreadPoolExecutor(24) as ex:
        futs = [ex.submit(tag, instr, d, s, faktor) for d, s in auftraege]
        for i, f in enumerate(futs):
            d, s, df = f.result()
            if isinstance(df, str):
                fehl.append((d, s))
            elif df is not None:
                res[s].append(df)
            if i % 500 == 0:
                print(f"{instr}: {i}/{len(futs)} {stat} {time.time()-t0:.0f}s", flush=True)
    print(f"{instr}: fertig {stat}, fehlgeschlagene Tage: {len(fehl)}", flush=True)
    if not res["BID"] or not res["ASK"]:
        sys.exit(f"{instr}: keine Daten")
    b = pd.concat(res["BID"]).set_index("t").sort_index()
    a = pd.concat(res["ASK"]).set_index("t").sort_index()
    b = b[b.v > 0]; a = a[a.v > 0]          # Phantomkerzen (Volumen 0) raus
    m = b.add_prefix("b_").join(a.add_prefix("a_"), how="inner")
    m["vol"] = m.b_v + m.a_v
    m = m.drop(columns=["b_v", "a_v"])
    os.makedirs(out, exist_ok=True)
    for tf in tfs:
        n = int(tf[1:])
        g = m.resample(f"{n}min", label="left", closed="left")
        r = pd.DataFrame({
            "b_o": g.b_o.first(), "b_h": g.b_h.max(), "b_l": g.b_l.min(), "b_c": g.b_c.last(),
            "a_o": g.a_o.first(), "a_h": g.a_h.max(), "a_l": g.a_l.min(), "a_c": g.a_c.last(),
            "vol": g.vol.sum()}).dropna(subset=["b_o"])
        r["spread_c"] = r.a_c - r.b_c
        p = os.path.join(out, f"{instr}_{tf}_{y0}-{y1}.csv.gz")
        r.to_csv(p, compression="gzip", index_label="time", float_format="%.6g")
        print(p, len(r), r.index.min(), r.index.max(), flush=True)
    if fehl:
        pd.Series([f"{d} {s}" for d, s in fehl]).to_csv(os.path.join(out, f"{instr}_{y0}-{y1}_fehlende_tage.txt"), index=False, header=False)


if __name__ == "__main__":
    instr = sys.argv[1].upper()
    y0, y1 = int(sys.argv[2]), int(sys.argv[3])
    tfs = sys.argv[4].split(",")
    out = sys.argv[5] if len(sys.argv) > 5 else "data/min"
    lade(instr, y0, y1, tfs, out)
