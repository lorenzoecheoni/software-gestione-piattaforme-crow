# OmniCrowd — Contesto e variazioni (handoff cross-PC)

Documento di contesto per chi riprende il lavoro da un altro PC. Riassume **come funziona oggi
il recupero/gestione dei documenti** lungo l'istruttoria e le variazioni più recenti. Il software
si chiama **OmniCrowd** ("The all-in-one crowdfunding operating system"; in passato "ECSP
Compliance Suite"). Stack: Python stdlib + SQLite, `app.py`, avvio `python3 app.py 8772`.

> Regola di lavoro multi-PC: si lavora su un PC alla volta. Prima di cambiare postazione fare
> "aggiorna git" (commit + push). Il DB `data/ecsp_suite.db` e la cartella `uploads/` sono
> versionati di proposito per avere lo stesso scenario ovunque.

---

## 1. Recupero e gestione dei documenti (la parte modificata)

Il flusso documentale segue la procedura ufficiale Pariter (7 fasi; in OmniCrowd la barra è a
**8 fasi** perché l'Advisory è una sessione dedicata). Gestione dei documenti per fase:

### Fase 1 — Registrazione candidatura + presa in carico
- **Registrazione candidatura**: avviene dal portale; carica **anagrafica** (modificabile/
  risalvabile dal fascicolo) + **documenti allegati**. Si può creare una pratica anche
  **manualmente** (pagina "Nuova candidatura") finché non c'è l'API/portale.
- **I 9 documenti di candidatura** (checklist M1, esatti dal manuale onboarding): presentazione
  progetto imprenditoriale; piano finanziario storico + proiezioni 3 anni; visura camerale
  aggiornata; statuto; ultimi due bilanci depositati; informazioni operazione; sito web; key
  manager ed esperienze; documentazione integrativa eventuale.
- **Presa in carico**: pulsante distinto → genera **numero pratica 8 cifre** + comunicazione
  **C2** precompilata da inviare. Reversibile ("Annulla presa in carico").
- I documenti del dossier sono **richiamati** dalla fase precedente; i mancanti si caricano a mano.

### Fase 2 — Ammissibilità, divisa per funzione (pronta per i permessi)
- **2.1 Completezza del fascicolo (Comitato Tecnico)**: prerequisito **soggetti onorabilità**
  (titolare effettivo / legale rappresentante coincidono? → 1 o 2 autodichiarazioni/casellari);
  **lista documentale** con condizione (bloccante/facoltativo) e tasto rapido **"Verificato"**;
  **completezza fascicolo** (Allegato 5_1): documenti *sempre dovuti* vs *"se disponibili"* (non
  bloccanti) + **regola bilanci** (richiesti = ultimi esercizi chiusi e depositati, max 2; neo
  costituita = 0). La **richiesta di integrazione C3** si **precompila automaticamente** con:
  sempre dovuti mancanti + shortfall bilanci + documenti di onorabilità mancanti.
- **2.2 Verifica di ammissibilità (Responsabile funzioni di controllo)**: KYC art. 5 (casellario,
  carichi pendenti, titolare effettivo/giurisdizioni, autodichiarazioni) + limite €5M; pannello
  **onorabilità con una riga per ogni documento** (autodichiarazione + casellario per ciascun
  soggetto, separate per LR e TE se distinti); **relazione art. 5/AML (M4)**.
- **2.3 Validazione**: recap completo dei documenti con flag + invio **esito positivo (C4 →
  CVOI)** o **non ammissibilità (C6)**.
- Comunicazioni: in 2.1/2.2 solo **C3 / C6**; **C4 solo in 2.3** (gate di validazione).

### Fase 3 — Valutazione di merito (CVOI), in due parti
- **3.1 KIIS — produzione e verifica**: la **KIIS è redatta dal proponente** (art. 23 Reg. UE
  2020/1503; il proponente è responsabile del contenuto). Pariter mette a disposizione il
  template (Allegato 18) e può **precompilare come assistenza**, ma la titolarità è del
  proponente che valida la bozza. Poi **verifica del fornitore** (chiarezza/correttezza/
  completezza): esito *coerente / da_correggere / incoerente*; se **da_correggere** parte la
  **segnalazione C3K** al proponente (art. 23 par. 12; in assenza di riscontro: sospensione
  max 30 gg → cancellazione). **Conflitti di merito** (Allegato 14): *nessuno / gestibile (misura)
  / non gestibile (stop)*.
- **Gate 3.1 → 3.2**: **verifica KIIS COERENTE** e **conflitti ∈ {nessuno, gestibile}**.
  Ordine corretto e CHIUSO: **verifica KIIS (M5) → scoring (M6)**, NON invertire.
- **3.2 Verbale CVOI — scoring per criterio**: **dossier documentale completo** richiamato +
  scoring 3 aree (soglia 19,05) + **fascicolo (M7) a 8 sezioni** con sez. 7 (esito conflitti) e
  sez. 8 (esito KIIS) dalla 3.1; la bozza KIIS viaggia col fascicolo.
- **3.3 Trasmissione all'Advisory**: solo se esito positivo + fascicolo completo (sez. 7/8).
  **Nessuna comunicazione di esito al proponente in Fase 3** (l'esito parte post-CdA: C5/C6).

### Fasi 4–8
4 Advisory Committee (parere non vincolante) · 5 Relazione conflitti + delibera CdA (convocazione
→ Governance, verbale) · 6 Strutturazione e pubblicazione (Identificativo offerta = LEI + 8 cifre)
· 7 Raccolta · 8 Post-offerta.

### Trasversale ai documenti
- **Richiamo documenti** delle fasi precedenti (recall) in CVOI/Advisory/CdA.
- **Storico comunicazioni** completo in fondo a ogni fase (registro aggiornato di tutte le mail).
- Relazioni interne con ciclo completo: genera (dal modello) / modifica / firma (PDF via Chrome,
  firma dall'anagrafica team) / carica a mano / rimuovi / valida; anteprima inline.

---

## 2. Modulistica M1–M14 (rinumerazione giugno 2026)

Lo stesso numero può indicare un documento diverso rispetto al vecchio assetto. Numerazione
**vigente** (catalogo `_M_DESC` in `app.py`):

| # | Modulo |
|---|---|
| M1 | Checklist documentale — onboarding proponente |
| M2 | Checklist KYC — art. 5 ECSP |
| M3 | Checklist — limite € 5.000.000 |
| M4 | Relazione controlli art. 5 / AML (attestazione 2ª linea) |
| M5 | Checklist — verifica della scheda KIIS |
| M6 | Modulo valutazione CVOI — scoring (Allegato 5.1) |
| M7 | Fascicolo di valutazione (8 sezioni) |
| M8 | Parere dell'Advisory Committee |
| M9 | Relazione di insussistenza dei conflitti |
| M10 | Verbale CdA — delibera sull'offerta |
| M11 | Modulo classificazione investitore e test (Allegato 19) |
| M12 | Registri — conflitti di interesse e reclami |
| M13 | Notifica data breach — al Garante (GDPR) |
| M14 | Segnalazione incidente ICT grave — a CONSOB (DORA) |

1ª linea (team valutazione: M1, M2, M3, M5, M6, M7) vs 2ª linea (Responsabile attesta M4, M9;
registri M12). Cartella Drive modulistica **vigente**: `1qqxzdiaBfujFcMr25kPucN3QJwwVtCEp`
(OLD/storico, non usare: `1Gg2xFI3rVO9tXn29TZeFDfjbmDYXcOzu`).

---

## 3. Governance vigente (già allineata nel codice)
Presidente CdA **Gaetano De Vito** · Responsabile funzioni di controllo **Stefania Monotoni**
(firma M4/AML e M9/conflitti) · Advisory **Rubina Galeotti, Gioacchino Attanzio** · Sindaco
**Roberto Rizzuto** · Revisore **Gallassi** (transizione da formalizzare in CdA) · Responsabile IT
**Veronika Udod** · Fornitore IT **Code Factory** (gruppo G2R). Compagine: Gruppo 2DueRighe 62% ·
Pariter Partners 19% · Power Money 19%.

---

## 4. Comunicazioni (C1–C9 + C3K)
C1 PEC interna · C2 presa in carico (numero pratica) · C3 richiesta integrazione documentale ·
**C3K segnalazione/correzione KIIS** (art. 23 par. 12) · C4 verifica documentale positiva (→ CVOI)
· C5 approvato (post CdA) · C6 non ammissione/rigetto · C7 pubblicazione · C8 conferma ordine
investitore · C9 CONSOB/SiCrowd. In Fase 3 l'unica uscita verso il proponente è la **C3K**.

---

## 5. Punti già CHIUSI
- Ordine **verifica KIIS (M5) → scoring (M6)**: corretto e chiuso, non invertire. I documenti-
  quadro (Manuale §5, Onboarding §4) sono in allineamento; testi con scoring prima della verifica
  KIIS sono superati.
- Firma delle relazioni di controllo (M4, M9): **Stefania Monotoni** (Responsabile funzioni di
  controllo). Il file demo "Quinte Parallele" (firma Malerba) è vecchio e non fa testo.

---

## 6. Fonti (Allegati, ID Drive)
Allegato 5_1 scoring/fascicolo `1rs3I2cvFXhhGlygPlf12Q3tay6lYgfkq` · Allegato 18 KIIS
`11PKSNQ1MkuXsJlRhjNJCTWhgNY6OiE_d` · Allegato 14 conflitti `1qmBc3JcoroToAvB-4UKxt2idLHcpA7xf` ·
Allegato 19 limiti `1bS2Ywiv6RH3hWBdDVxVJYFGfLJP-MOfA` · Allegato 16 reclami
`1HjXeAKGCbpKDpHgf85adjPmvQIDFBJF5` · Allegato 6_1 governance `1e0xus-FLc09_bEaCkBuTlXKPIodvCn6e`.
Processo onboarding (Google Doc) `1Fk2_AUpJVoNHk1P3_ccpfFAwnpMrMOL_Dlfh2d8u978` — usa ancora la
vecchia numerazione moduli: rimappare ogni "Mx" datato sulla tabella §2.
