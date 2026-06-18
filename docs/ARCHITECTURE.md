# ECSP Compliance & Operations Suite

## Lettura del dominio

Il sistema non deve essere un archivio documenti con qualche stato, ma un motore operativo in cui il fascicolo del deal viene costruito progressivamente. La cosa piu importante e che i documenti generati in una fase diventino input della fase successiva, con audit automatico e responsabilita chiare.

La prima versione implementa quindi il nucleo che abilita il resto:

- piattaforme separate: Pariter Equity e ISI Crowd;
- utenti con ruolo operativo;
- compagine come fonte di verita per Comitato Tecnico, CoVi e CdA;
- governance CdA con convocazioni, sedute, verbali e delibere collegate ai deal;
- proponenti con dossier anagrafico/rischio;
- investitori con tipologia, investito, deal collegati e onboarding;
- registri indipendenti per conflitti d'interesse e reclami;
- deal come macchina a stati;
- requisiti documentali, verifiche art. 5, pareri, delibere, integrazioni e report iter;
- archivio documenti aggregato.

## Architettura proposta per produzione

Frontend: applicazione web responsive, preferibilmente React/TypeScript o server-rendered HTML se il team vuole massima semplicita. La UI deve essere densa e operativa: dashboard, liste filtrabili, dettaglio fascicolo, stepper del ciclo deal, inbox per azioni assegnate.

Backend: API modulare con dominio esplicito. Una buona scelta e FastAPI o Django, con moduli per deal lifecycle, documenti, governance, compliance, notifiche e integrazioni. Le transizioni di fase devono stare nel backend, non nella UI.

Persistenza: PostgreSQL per dati transazionali e audit trail; object storage compatibile S3 per file caricati/generati; ricerca full-text su documenti e fascicoli tramite Postgres FTS o motore dedicato se il volume cresce.

Autenticazione e ruoli: SSO/OIDC, RBAC e policy per azione. I ruoli globali non bastano: alcune azioni dipendono anche dal contesto, per esempio il membro del Comitato Tecnico puo compilare il parere solo per la piattaforma e il deal assegnati.

Document generation: template versionati, con rendering server-side e firma/hash del documento generato. Il documento prodotto deve diventare record immutabile del fascicolo.

Integrazioni: adapter isolati per dati piattaforma, email, EasyCross, portali/trasmissioni autorita e futuro assistente IA. Ogni adapter deve scrivere esiti e payload minimi nell'audit trail.

## Generatore comunicazioni

La sezione Comunicazioni resta nel prototipo come workspace separato gia impostato: catalogo obblighi, template ufficiali scaricati e design del flusso di generazione del file finale. Il flusso corretto non e "clicca genera", ma:

1. selezione obbligo, destinatario, template e scadenza;
2. raccolta dei dati strutturati disponibili da piattaforma, deal, proponente, compagine, investitori e registri;
3. indicizzazione dei documenti caricati, inclusi contratti, statuti, verbali, patti, KYC, business plan, KIIS e allegati;
4. estrazione dei dati da documenti con citazione puntuale della fonte;
5. validazione umana dei campi, soprattutto dove l'estrazione e probabilistica o la norma richiede responsabilita;
6. generazione del file finale nel formato richiesto, con allegati, versione del template, hash, audit e ricevuta di invio.

Ogni campo generato dovrebbe avere una provenance esplicita: tabella/record di origine, documento di origine, pagina o sezione, estratto testuale, livello di confidenza, validatore e timestamp. Questo serve sia alla revisione interna sia alla difendibilita in caso di controllo.

Per i documenti caricati conviene prevedere una pipeline asincrona:

- acquisizione file e classificazione;
- OCR/parsing dove necessario;
- estrazione testo e tabelle;
- entity extraction di societa, date, importi, persone, clausole e riferimenti normativi;
- collegamento a deal/proponente/piattaforma;
- mappa campi per ciascun obbligo di comunicazione;
- coda di validazione per i campi mancanti o confliggenti.

In questa fase il prototipo mostra il design del generatore e non implementa ancora parsing documentale, OCR, template engine Excel/DOCX/PDF o trasmissione verso SICROWD/Banca d'Italia.

## Perche il prototipo usa Python + SQLite

Questa consegna deve essere avviabile senza installazioni esterne. Per questo la prima versione usa Python standard library, SQLite e file system locale. La forma del codice ricalca comunque la futura architettura: transizioni centralizzate, tabelle normalizzate, audit trail, documenti come entita, piattaforme separate.

Il prototipo non vuole fingere di essere produzione: non implementa SSO, firma digitale, encryption-at-rest, invio email reale o integrazione EasyCross. Mostra pero il comportamento chiave e rende chiaro dove questi componenti entrano.

## Modello dati principale

platforms: separa Pariter Equity e ISI Crowd.

users: profili operativi con ruolo.

committee_members: fonte di verita per Comitato Tecnico, CoVi e CdA. I deal selezionano relatori da qui.

shareholders: partecipogramma dei soci qualificati.

person_agreements: accordi per persona, incarichi, NDA e patti collegati all'organigramma.

board_meetings: sedute CdA, convocazioni, ordine del giorno, link riunione e verbali.

proponents: dossier del proponente, con titolari effettivi, esposizione, score interno e note.

deals: fascicolo offerta. La colonna phase rappresenta la macchina a stati. Lo stato sintetico e derivato dalla fase.

deal_requirements: documentazione richiesta e integrazioni richieste dal CdA.

verifications: controlli art. 5, requisiti, conflitti e completezza informativa.

committee_opinions: pareri di Comitato Tecnico e CoVi, collegati a un documento generato.

board_decisions: delibere CdA, collegate a documento generato e a eventuale richiesta di integrazione.

investors e investments: anagrafica investitori, tipologia, onboarding e collegamento agli importi sui deal.

conflicts: registro conflitti d'interesse, con parte correlata, deal, descrizione, mitigazione e stato.

complaints: registro reclami, con data, soggetto, oggetto, canale, stato, esito e owner.

documents: archivio aggregato; ogni file puo appartenere a piattaforma, deal, proponente e origine.

audit_log: storico automatico di ogni azione rilevante.

platform_metrics e compliance_tasks: dashboard e punto di aggancio per dati real-time e adempimenti ricorrenti.

## Macchina a stati deal

Sequenza base:

1. Appena caricato
2. Istruttoria documentazione
3. Verifiche
4. Comitato Tecnico
5. CoVi
6. CdA
7. Integrazione documenti, se richiesta
8. Contratto, se necessario
9. Pre-pubblicazione
10. Pubblicato

Regole implementate:

- non si passa a Verifiche se la documentazione onboarding obbligatoria e incompleta;
- non si passa a Comitato Tecnico se le verifiche non sono tutte OK;
- il parere del Comitato Tecnico genera un documento e porta il deal in CoVi;
- il parere CoVi genera un documento e porta il deal in CdA;
- la delibera CdA genera un documento e sposta il deal verso integrazioni, contratto, pre-pubblicazione o stato Respinta;
- le integrazioni devono essere chiuse prima di procedere;
- prima di Pubblicato serve un documento di categoria KIIS;
- ogni passaggio scrive audit trail.

## Esperienza d'uso

La navigazione e per area operativa: Cruscotto, Deal, Compagine, Governance, Proponenti, Investitori, Conflitti, Reclami, Comunicazioni, Documenti, Architettura.

Il dettaglio deal e la schermata principale. Mostra:

- fase corrente e stepper;
- responsabilita del fascicolo;
- azioni disponibili per il ruolo selezionato;
- documentazione richiesta;
- verifiche;
- pareri e delibere;
- upload documenti e generazione report iter;
- audit trail.

Governance separa il mondo CdA dal dettaglio deal: sedute, convocazioni, verbali, rubrica membri e delibere generate dai fascicoli.

Investitori mette in evidenza il rischio operativo dell'onboarding: test di ingresso, simulazione perdite, soglie e periodo di riflessione.

Conflitti e Reclami sono registri autonomi perche hanno vita, responsabilita e reportistica diverse dal singolo deal, pur potendo collegarsi a un'offerta.

Il selettore in alto simula piattaforma e utente/ruolo. In produzione sarebbe sostituito da autenticazione reale.

## Assistente IA

L'assistente IA dovrebbe leggere da viste curate, non dal database grezzo: stato deal, documenti indicizzati, obblighi, template, audit e knowledge base normativa. Dovrebbe essere soggetto alle stesse policy di visibilita dell'utente, citare record/documenti interni e non compiere azioni senza conferma.

## Rischi e punti aperti

Audit e valore probatorio: servono append-only log, hash dei documenti generati e retention policy.

Normativa: il modello supporta i concetti ECSP, ma template KIIS, reclami e comunicazioni devono essere validati da consulenti legali/compliance.

Integrazioni: EasyCross, email e portali autorita richiedono API, formati e ricevute di trasmissione. Vanno modellati come adapter con retry e audit.

Permessi: il prototipo usa RBAC semplice; produzione richiede policy per piattaforma, organo, assegnazione e segregazione dei dati.

Documenti: in produzione servono antivirus, versioning, classificazione, storage cifrato e controllo accessi per file.

Migrazione dati: dati storici da fogli/email richiedono normalizzazione e import con controllo qualita.
