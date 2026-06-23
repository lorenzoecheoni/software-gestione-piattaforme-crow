# CrowdOS

**End-to-end Crowdfunding Platform Management.**

Software gestionale interno per piattaforme di crowdfunding (ECSP, Reg. (UE) 2020/1503): compliance, governance, istruttoria offerte, investitori, proponenti, documenti e comunicazioni obbligatorie.

## Avvio

```bash
cd /Users/lorenzo/Documents/Codex/2026-06-15/files-mentioned-by-the-user-brief/outputs/ecsp-compliance-suite
/Users/lorenzo/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 app.py 8772
```

Poi apri:

```text
http://127.0.0.1:8772
```

Il database SQLite viene creato in `data/ecsp_suite.db`; upload e documenti generati finiscono in `uploads/`.

## Ruoli demo

- Alessia Ricci: compliance officer
- Marco Bianchi: legale
- Giulia Ferri: Comitato Tecnico
- Paolo Conti: CoVi
- Elena Martini: CdA
- Sara De Luca: operatore

Il selettore in alto simula utente e piattaforma. Le azioni disponibili cambiano in base al ruolo.

## Flusso deal coperto

- creazione fascicolo deal;
- checklist documentazione;
- verifiche art. 5 e controlli;
- parere Comitato Tecnico con documento generato;
- parere CoVi con documento generato;
- delibera CdA con eventuale integrazione;
- upload documenti e KIIS;
- pubblicazione;
- audit trail;
- report iter generato nel fascicolo.
- sezione Comunicazioni con obblighi ECSP, fonti normative e template ufficiali scaricati.

## Template ufficiali

I file scaricati da CONSOB/Banca d'Italia sono in `templates/official/`.
L'elenco ragionato degli obblighi e' in `docs/COMMUNICATIONS.md` ed e' anche navigabile dalla voce `Comunicazioni` dell'app.

## Note

Il prototipo e' locale e non include autenticazione reale, invio email, firma, cifratura file o integrazioni esterne. I punti di integrazione sono modellati nel dominio e descritti in `docs/ARCHITECTURE.md`.
