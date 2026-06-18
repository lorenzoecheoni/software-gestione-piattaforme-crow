# Comunicazioni obbligatorie ECSP

Ricognizione svolta il 15/06/2026 su fonti ufficiali UE, CONSOB e Banca d'Italia. I template pubblicamente scaricabili sono stati salvati in `templates/official/`.

## Elenco operativo

| # | Comunicazione | Destinatario | Termine | Fonte principale | Template |
|---|---|---|---|---|---|
| 1 | Domanda di autorizzazione o estensione ECSP | CONSOB o Banca d'Italia, secondo soggetto | Procedimento autorizzativo | Reg. UE 2020/1503 art. 12; Reg. delegato UE 2022/2112; Reg. CONSOB 22720/2023 | DOCX CONSOB scaricato |
| 2 | Avvio, interruzione e riavvio utilizzo autorizzazione | CONSOB e Banca d'Italia | Senza indugio | Reg. CONSOB 22720/2023 art. 7; BDI 6 maggio 2024 | Nessun template pubblico trovato |
| 3 | Modifiche sostanziali condizioni autorizzazione | CONSOB e Banca d'Italia | Senza indugio | Reg. UE 2020/1503 art. 15(3); Delibera CONSOB 23656/2025 tabella 1 | Schema in delibera scaricata |
| 4 | Operativita transfrontaliera / passaporto | Autorita home/SPOC, ESMA, Autorita host | Prima dell'avvio | Reg. UE 2020/1503 art. 18 | Nessun template pubblico trovato |
| 5 | KIIS offerta art. 23 | Investitori; CONSOB via SICROWD | Contestuale alla trasmissione KIIS | Reg. UE 2020/1503 art. 23; Reg. CONSOB 22720/2023 art. 6; Delibera 23656/2025 tabella 2.1 | Excel SICROWD non pubblico |
| 6 | KIIS piattaforma art. 24 | Investitori; CONSOB via SICROWD | Contestuale alla trasmissione KIIS | Reg. UE 2020/1503 art. 24; Delibera 23656/2025 tabella 2.2 | Excel SICROWD non pubblico |
| 7 | Reporting annuale progetti finanziati / offerte | CONSOB o Banca d'Italia | CONSOB fine gennaio; BDI 25 gennaio | Reg. UE 2020/1503 art. 16; Reg. esecuzione UE 2022/2120; Delibera 23656/2025 tabella 3 | Schema in delibera; Excel SICROWD non pubblico |
| 8 | Variazioni accordi di esternalizzazione | Banca d'Italia | 30 aprile | BDI 6 maggio 2024; campo 15 Reg. delegato UE 2022/2112 | Schema in provvedimento scaricato |
| 9 | Segnalazione annuale esternalizzazioni | Banca d'Italia | 30 aprile, dati al 31 dicembre | BDI 31 maggio 2023 | PDF scaricato; allegati tecnici da recuperare pagina BDI |
| 10 | Partecipazioni qualificate nel fornitore specializzato | Banca d'Italia | 10 giorni | BDI 6 maggio 2024 | Schema in provvedimento scaricato |
| 11 | Valutazione idoneita esponenti aziendali | Banca d'Italia | Secondo procedura fit & proper | BDI 6 maggio 2024; Provv. BDI 4 maggio 2021 | Schema in provvedimento scaricato |
| 12 | Reclami clienti | Clienti; registro interno; Autorita su richiesta | Gestione tempestiva | Reg. UE 2020/1503 art. 7; Reg. delegato UE 2022/2117; Delibera 23656/2025 tabella 1 punto 17 | Template cliente da predisporre |
| 13 | Comunicazioni di marketing | Pubblico/investitori; vigilanza CONSOB | Nessuna approvazione ex ante | Reg. UE 2020/1503 art. 27; Reg. CONSOB 22720/2023 artt. 8-11 | Workflow interno |

## File scaricati

- `CONSOB-BDI-guida-operativa-crowdfunding-aprile-2025.pdf`
- `CONSOB-template-domanda-autorizzazione-servizi-crowdfunding.docx`
- `CONSOB-delibera-23656-2025-obblighi-comunicazione-crowdfunding.pdf`
- `CONSOB-regolamento-22720-2023-crowdfunding.pdf`
- `BDI-provvedimento-2024-05-06-crowdfunding.pdf`
- `BDI-provvedimento-2023-05-31-esternalizzazioni.pdf`
- `CONSOB-AIEC-Regolamento-1503.pdf`
- `CONSOB-consultazione-crowdfunding-2025-01-17.pdf`

## Note importanti

- La Delibera CONSOB 23656/2025 indica Excel SICROWD per KIIS e reporting annuale; non ho trovato un download pubblico diretto dei file Excel.
- Il provvedimento Banca d'Italia 31 maggio 2023 rimanda ad Allegato 1 e Allegato 2 pubblicati sulla pagina della segnalazione esternalizzazioni; non li ho recuperati da link pubblico durante questa sessione.
- La produzione dovrebbe conservare ricevute, hash dei file trasmessi, versione del template, firmatario/responsabile e audit immutabile.

## Design del generatore

La pagina Comunicazioni ora prevede un generatore progettuale del file finale. L'idea e che ogni obbligo abbia:

- template di destinazione;
- fonti dati strutturate interne;
- documenti candidati da cui estrarre dati;
- mappa campi con stato `Pronto`, `Da estrarre`, `Da validare`, `Da revisionare`;
- evidenza della fonte per ogni campo;
- bozza validabile prima della generazione definitiva;
- file finale con allegati, hash, audit trail e ricevuta di trasmissione.

Il sistema dovra poter leggere contratti, statuti, verbali, business plan, KIIS, patti, KYC e documenti di governance caricati dagli utenti. La generazione automatica dovra sempre restare assistita da validazione umana per i campi sensibili o normativamente rilevanti.
