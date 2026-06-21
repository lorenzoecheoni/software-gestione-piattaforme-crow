#!/usr/bin/env python3
"""Importa l'archivio documentale Pariter dentro la tabella `documents`.

- Legge i file dalla cartella ARCHIVE_DIR (cartella locale, sincronizzata via
  Google Drive come master). NON copia i file dentro il repo: registra solo i
  metadati + un riferimento `storage_path = "@archive/<percorso relativo>"`.
- Mappa la struttura di cartelle e i nomi file su `origin`/`category` usando la
  tassonomia gia' prevista dall'app.
- E' IDEMPOTENTE: rilanciandolo importa solo i file nuovi (per `storage_path`),
  quindi serve anche per recepire gli aggiornamenti (delta).

Uso:
    python3 tools/import_archive.py            # importa nella piattaforma Pariter
    python3 tools/import_archive.py --dry-run  # mostra cosa farebbe, senza scrivere
    ECSP_ARCHIVE_DIR="/percorso/archivio" python3 tools/import_archive.py
"""
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "ecsp_suite.db"
ARCHIVE_DIR = Path(
    os.environ.get("ECSP_ARCHIVE_DIR", str(BASE_DIR.parent / "Pariter Equity — Archivio documentale"))
).resolve()
ARCHIVE_PREFIX = "@archive/"

PLATFORM_ID = 1   # Pariter Equity
CREATED_BY = 1
ALLOWED_EXT = {".pdf", ".docx", ".xlsx", ".xls", ".md", ".csv", ".doc"}

# (regex sul percorso relativo lowercase, origin, category) -- prima corrisp. vince.
# Origin "Autorita" = corrispondenza con l'autorita di vigilanza; altrimenti "Archivio".
RULES = [
    (r"banca[\s_]?d.?italia|\bbdi\b|banca_italia", "Autorita", "Comunicazione Banca d'Italia"),
    (r"lettera.*consob|consob.*lettera|via_libera|riscontro|08_vaglio|rilievi|vaglio", "Autorita", "Comunicazione CONSOB"),
    (r"\bconsob\b", "Autorita", "Comunicazione CONSOB"),
    (r"statuto", "Archivio", "Statuto"),
    (r"verbale.*cda|cda.*verbale", "Archivio", "Verbale CdA"),
    (r"delibera", "Archivio", "Delibera CdA"),
    (r"domanda[\s_]?autorizzaz|istanza|nota_accompagn", "Archivio", "Domanda autorizzazione ECSP"),
    (r"business[\s_]?plan", "Archivio", "Business plan"),
    (r"bilancio", "Archivio", "Bilancio"),
    (r"visura", "Archivio", "Visura camerale"),
    (r"\bkiis\b", "Archivio", "KIIS"),
    (r"\bkyc\b|\baml\b|antiricic|\bart5\b", "Archivio", "KYC / AML"),
    (r"\bnda\b", "Archivio", "NDA"),
    (r"procura", "Archivio", "Procura"),
    (r"delega", "Archivio", "Delega"),
    (r"patti.*parasocial|parasocial", "Archivio", "Patti parasociali"),
    (r"polizza|assicuraz", "Archivio", "Polizza assicurativa"),
    (r"incarico", "Archivio", "Lettera di incarico"),
    (r"contratto.*amministrat|amministrat.*contratto", "Archivio", "Contratto amministratore"),
    (r"outsourcing|esternalizz|\bdora\b|continuit|\bict\b", "Archivio", "Outsourcing"),
    (r"fondi[\s_]?propri|prudenzial|presidi", "Archivio", "Prospetto requisiti prudenziali"),
    (r"trattamento[\s_]?dati|gdpr|data[\s_]?breach|privacy", "Archivio", "Data processing agreement"),
]

# Legenda numero allegato -> categoria (dalla domanda di autorizzazione ECSP).
# Negli archivi storici i file sono solo "Allegato_N.pdf" senza suffisso descrittivo.
ALLEGATO_LEGEND = {
    "4": "Statuto",
    "7": "Data processing agreement",
    "8": "Outsourcing",                       # rischi IT
    "9": "Prospetto requisiti prudenziali",   # presidi prudenziali / piano triennale
    "10": "Prospetto requisiti prudenziali",  # fondi propri
    "11": "Outsourcing",                      # continuita operativa
    "12": "KYC / AML",                        # onorabilita soci
    "13": "KYC / AML",                        # onorabilita gestori
    "18": "KIIS",
}
ALLEGATO_RE = re.compile(r"allegato[\s_]?(\d+)")

LEAD_PREFIX = re.compile(r"^(?:\d{1,2}[a-z]?|[A-Z])_")


def clean_name(name):
    """Toglie prefissi d'ordinamento (09_, A_, 01_) e rende leggibile."""
    name = LEAD_PREFIX.sub("", name)
    name = name.replace("_", " ").replace("  ", " ").strip()
    return name


def classify(rel_path):
    low = rel_path.lower()
    for pattern, origin, category in RULES:
        if re.search(pattern, low):
            return origin, category
    m = ALLEGATO_RE.search(low)
    if m and m.group(1) in ALLEGATO_LEGEND:
        return "Archivio", ALLEGATO_LEGEND[m.group(1)]
    return "Archivio", "Documentazione"


def make_title(rel: Path):
    stem = clean_name(rel.stem)
    parent = rel.parent
    # Prefissa la cartella contenitore quando aggiunge contesto (es. fascicoli datati).
    if parent != Path(".") and parent.name:
        ctx = clean_name(parent.name)
        if ctx and ctx.lower() not in stem.lower():
            return f"{ctx} · {stem}"
    return stem


def main():
    dry = "--dry-run" in sys.argv
    if not ARCHIVE_DIR.exists():
        sys.exit(f"Archivio non trovato: {ARCHIVE_DIR}\n(imposta ECSP_ARCHIVE_DIR)")
    if not DB_PATH.exists():
        sys.exit(f"DB non trovato: {DB_PATH} (avvia prima l'app una volta)")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    existing = {
        r["storage_path"]
        for r in conn.execute(
            "SELECT storage_path FROM documents WHERE platform_id = ? AND storage_path LIKE '@archive/%'",
            (PLATFORM_ID,),
        )
    }

    files = sorted(
        p for p in ARCHIVE_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT and not p.name.startswith("~$")
    )

    now = datetime.now().isoformat(timespec="seconds")
    added = 0
    by_cat = {}
    for p in files:
        rel = p.relative_to(ARCHIVE_DIR)
        storage_path = ARCHIVE_PREFIX + rel.as_posix()
        if storage_path in existing:
            continue
        origin, category = classify(rel.as_posix())
        title = make_title(rel)
        by_cat[category] = by_cat.get(category, 0) + 1
        added += 1
        if dry:
            continue
        conn.execute(
            """
            INSERT INTO documents
              (platform_id, deal_id, proponent_id, origin, category, title, filename, storage_path, generated, created_by, created_at)
            VALUES (?, NULL, NULL, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (PLATFORM_ID, origin, category, title, p.name, storage_path, CREATED_BY, now),
        )
    if not dry:
        conn.commit()
    conn.close()

    print(f"Archivio:      {ARCHIVE_DIR}")
    print(f"File trovati:  {len(files)}")
    print(f"Gia' presenti: {len(existing)}")
    print(f"{'Da importare' if dry else 'Importati'}:    {added}")
    if by_cat:
        print("Per categoria:")
        for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
            print(f"  {n:4d}  {cat}")


if __name__ == "__main__":
    main()
