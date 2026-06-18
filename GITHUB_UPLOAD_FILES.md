# File da caricare su GitHub

Cartella progetto:

```text
/Users/lorenzo/Documents/Codex/2026-06-15/files-mentioned-by-the-user-brief/outputs/ecsp-compliance-suite
```

## Carica questi file/cartelle

```text
.gitignore
README.md
CLAUDE.md
CONTESTO_PROGETTO_ECSP.md
GITHUB_UPLOAD_FILES.md
app.py
docs/
static/
templates/
```

Dettaglio:

```text
docs/ARCHITECTURE.md
docs/COMMUNICATIONS.md
static/app.js
static/styles.css
templates/official/BDI-provvedimento-2023-05-31-esternalizzazioni.pdf
templates/official/BDI-provvedimento-2024-05-06-crowdfunding.pdf
templates/official/CONSOB-AIEC-Regolamento-1503.pdf
templates/official/CONSOB-BDI-guida-operativa-crowdfunding-aprile-2025.pdf
templates/official/CONSOB-consultazione-crowdfunding-2025-01-17.pdf
templates/official/CONSOB-delibera-23656-2025-obblighi-comunicazione-crowdfunding.pdf
templates/official/CONSOB-regolamento-22720-2023-crowdfunding.pdf
templates/official/CONSOB-template-domanda-autorizzazione-servizi-crowdfunding.docx
```

## Non caricare questi

```text
__pycache__/
data/ecsp_suite.db
uploads/
ecsp_suite.sqlite3
```

Motivo:

- `__pycache__/` e' cache Python.
- `data/ecsp_suite.db` e' database locale.
- `uploads/` contiene file generati/upload temporanei.
- `ecsp_suite.sqlite3` e' un vecchio DB non usato come DB principale.

## Comando git consigliato

Dopo aver creato il repository vuoto su GitHub:

```bash
cd /Users/lorenzo/Documents/Codex/2026-06-15/files-mentioned-by-the-user-brief/outputs/ecsp-compliance-suite
git init
git add .gitignore README.md CLAUDE.md CONTESTO_PROGETTO_ECSP.md GITHUB_UPLOAD_FILES.md app.py docs static templates
git commit -m "Initial ECSP compliance suite prototype"
git branch -M main
git remote add origin https://github.com/TUO-UTENTE/NOME-REPO.git
git push -u origin main
```

Sostituisci `TUO-UTENTE/NOME-REPO` con il repository reale.

## Contesto per Claude

Il file piu' importante da far leggere a Claude e':

```text
CLAUDE.md
```

Contiene le regole di prodotto, la logica delle sezioni, le preferenze UI e le rotte principali.
