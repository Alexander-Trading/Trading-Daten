#!/usr/bin/env python3
"""
Laedt Tageskurse (Yahoo Finance via yfinance) fuer alle Aktien aus data/sp500_members.csv
und schreibt je Ticker eine CSV nach data/stocks/<TICKER>.csv.

Spalten: date,open,high,low,close,adj_close,volume
  close     = Schlusskurs ohne Dividendenbereinigung (Splits sind enthalten)
  adj_close = zusaetzlich um Dividenden bereinigt

Aufruf (lokal oder in GitHub Actions):
  python tools/lade_aktien.py --von 2004 --modus aktuell
  python tools/lade_aktien.py --von 1996 --modus alle        # auch ausgeschiedene Titel (viele ohne Daten)
  python tools/lade_aktien.py --zusatz SPY,QQQ,IWM           # weitere Ticker (z.B. ETFs)

Hinweis zu "modus":
  aktuell = nur Mitglieder der letzten Zeile (heutiger Index) -> Survivorship-Bias im Backtest!
  alle    = jeder Ticker, der je im Index war. Viele ausgeschiedene Titel liefert Yahoo nicht mehr.
"""
import argparse, csv, json, os, sys, time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

FELDER = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
SPALTEN = ["open", "high", "low", "close", "adj_close", "volume"]


def yahoo_symbol(t: str) -> str:
    return t.strip().upper().replace(".", "-")


def lade_universum(pfad: str, modus: str) -> list[str]:
    zeilen = []
    csv.field_size_limit(sys.maxsize)
    with open(pfad, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            zeilen.append(row["tickers"])
    if not zeilen:
        raise SystemExit(f"{pfad} enthaelt keine Zeilen")
    if modus == "aktuell":
        roh = zeilen[-1].split(",")
    else:
        roh = [t for z in zeilen for t in z.split(",")]
    sym = sorted({yahoo_symbol(t) for t in roh if t.strip()})
    return sym


def bereinige(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = SPALTEN
    df = df.dropna(subset=["close"])
    df = df[(df["close"] > 0) & (df["high"] >= df["low"])]
    # Phantom-Bars: kein Handel, alle Preise gleich, Volumen 0
    phantom = (df["volume"].fillna(0) == 0) & (df.open == df.high) & (df.high == df.low) & (df.low == df.close)
    df = df[~phantom]
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.round(4)


def hole_batch(symbole: list[str], start: str):
    """Gibt {symbol: DataFrame} fuer alle Symbole mit Daten zurueck."""
    ergebnis = {}
    if not symbole:
        return ergebnis
    roh = yf.download(symbole, start=start, interval="1d", auto_adjust=False,
                      group_by="ticker", threads=True, progress=False)
    if roh is None or roh.empty:
        return ergebnis
    for s in symbole:
        try:
            if isinstance(roh.columns, pd.MultiIndex):
                if s not in roh.columns.get_level_values(0):
                    continue
                d = roh[s][FELDER]
            else:  # Einzelsymbol ohne MultiIndex
                d = roh[FELDER]
        except KeyError:
            continue
        d = d.dropna(how="all")
        if not d.empty:
            ergebnis[s] = bereinige(d)
    return ergebnis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mitglieder", default="data/sp500_members.csv")
    ap.add_argument("--modus", choices=["aktuell", "alle"], default="aktuell")
    ap.add_argument("--von", type=int, default=2004, help="Startjahr")
    ap.add_argument("--zusatz", default="", help="Zusaetzliche Ticker, durch Komma getrennt")
    ap.add_argument("--ziel", default="data/stocks")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--max-fehlerquote", type=float, default=0.30,
                    help="Bricht mit Fehler ab, wenn mehr Ticker (Anteil) ohne Daten bleiben (nur Modus aktuell)")
    a = ap.parse_args()

    sym = lade_universum(a.mitglieder, a.modus)
    sym += [yahoo_symbol(t) for t in a.zusatz.split(",") if t.strip()]
    sym = sorted(set(sym))
    print(f"Universum: {len(sym)} Ticker (Modus {a.modus}), Start {a.von}-01-01", flush=True)
    os.makedirs(a.ziel, exist_ok=True)
    start = f"{a.von}-01-01"

    ok, fehl = {}, []
    for i in range(0, len(sym), a.batch):
        teil = sym[i:i + a.batch]
        try:
            got = hole_batch(teil, start)
        except Exception as e:                       # Netz-/Rate-Limit-Fehler: Batch spaeter einzeln versuchen
            print(f"  Batch {i//a.batch+1}: Fehler {type(e).__name__}: {e}", flush=True)
            got = {}
        ok.update(got)
        rest = [s for s in teil if s not in got]
        for versuch in (1, 2):                       # Nachholen einzeln
            if not rest:
                break
            time.sleep(2 * versuch)
            for s in list(rest):
                try:
                    g = hole_batch([s], start)
                except Exception:
                    g = {}
                if s in g:
                    ok[s] = g[s]; rest.remove(s)
        fehl += rest
        print(f"  {min(i+a.batch, len(sym))}/{len(sym)} verarbeitet, bisher {len(ok)} mit Daten", flush=True)
        time.sleep(1)

    manifest = {"aktualisiert": datetime.now(timezone.utc).isoformat(), "quelle": "yfinance", "aktien": {}}
    for s, df in sorted(ok.items()):
        df.to_csv(os.path.join(a.ziel, f"{s}.csv"))
        manifest["aktien"][s] = {"n": int(len(df)), "von": str(df.index[0].date()), "bis": str(df.index[-1].date())}
    with open(os.path.join(a.ziel, "STOCKS_MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    with open(os.path.join(a.ziel, "_ohne_daten.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(fehl)))

    quote = len(fehl) / max(1, len(sym))
    print(f"\nFertig: {len(ok)} Ticker geschrieben, {len(fehl)} ohne Daten ({quote:.0%}). Liste: {a.ziel}/_ohne_daten.txt")
    if a.modus == "aktuell" and quote > a.max_fehlerquote:
        raise SystemExit(f"Fehlerquote {quote:.0%} ueber Grenze {a.max_fehlerquote:.0%} - vermutlich Sperre/Netzproblem, nichts committen.")


if __name__ == "__main__":
    main()
