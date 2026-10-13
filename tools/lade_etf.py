#!/usr/bin/env python3
"""
Laedt Tageskurse (Yahoo Finance via yfinance) fuer eine feste ETF-Liste nach data/etf/<TICKER>.csv.
Nutzt dieselben Funktionen wie tools/lade_aktien.py (muss im selben Ordner liegen).

Spalten: date,open,high,low,close,adj_close,volume   (adj_close = inkl. Dividenden)
Aufruf:  python tools/lade_etf.py --von 2000
"""
import argparse, json, os, sys, time
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lade_aktien import hole_batch, yahoo_symbol

ETFS = {
 "Aktien USA":      "SPY QQQ IWM VYM SDY DVY SCHD",
 "Aktien Welt":     "EFA EEM EWG EWJ FEZ EWU EWC EWA EWZ EWY FXI INDA",
 "Sektoren":        "XLK XLF XLE XLV XLI XLP XLY XLU XLB",
 "Anleihen":        "TLT IEF SHY LQD HYG TIP BND EMB JNK BKLN MUB PFF",
 "Rohstoffe":       "GLD SLV USO DBA DBC DBB GDX",
 "Immobilien":      "VNQ RWX",
 "Waehrungen":      "UUP FXE FXY FXB FXA",
 "Geldmarkt/Cash":  "BIL SHV",
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--von", type=int, default=2000)
    ap.add_argument("--ziel", default="data/etf")
    ap.add_argument("--zusatz", default="", help="weitere Ticker, Komma-getrennt")
    a = ap.parse_args()
    sym = sorted({yahoo_symbol(t) for g in ETFS.values() for t in g.split()} |
                 {yahoo_symbol(t) for t in a.zusatz.split(",") if t.strip()})
    os.makedirs(a.ziel, exist_ok=True)
    print(f"{len(sym)} ETFs, Start {a.von}-01-01", flush=True)
    ok, fehl = {}, []
    for i in range(0, len(sym), 20):
        teil = sym[i:i+20]
        try: got = hole_batch(teil, f"{a.von}-01-01")
        except Exception as e: print("  Batch-Fehler:", type(e).__name__, e, flush=True); got = {}
        ok.update(got)
        for s in [t for t in teil if t not in got]:
            for versuch in (1, 2, 3):
                time.sleep(2*versuch)
                try: g = hole_batch([s], f"{a.von}-01-01")
                except Exception: g = {}
                if s in g: ok[s] = g[s]; break
            else: fehl.append(s)
        print(f"  {min(i+20,len(sym))}/{len(sym)} verarbeitet, {len(ok)} mit Daten", flush=True); time.sleep(1)
    man = {"aktualisiert": datetime.now(timezone.utc).isoformat(), "quelle": "yfinance", "gruppen": ETFS, "etf": {}}
    for s, df in sorted(ok.items()):
        df.to_csv(os.path.join(a.ziel, f"{s}.csv"))
        man["etf"][s] = {"n": int(len(df)), "von": str(df.index[0].date()), "bis": str(df.index[-1].date())}
    json.dump(man, open(os.path.join(a.ziel, "ETF_MANIFEST.json"), "w"), indent=1)
    open(os.path.join(a.ziel, "_ohne_daten.txt"), "w").write("\n".join(sorted(fehl)))
    print(f"\nFertig: {len(ok)} geschrieben, {len(fehl)} ohne Daten: {fehl}")
    if len(fehl) > 0.3*len(sym): raise SystemExit("Zu viele Ausfaelle - vermutlich Sperre/Netzproblem.")

if __name__ == "__main__":
    main()
