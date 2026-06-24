#!/usr/bin/env python3
import base64
import cgi
import html
import io
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates" / "official"
DB_PATH = DATA_DIR / "ecsp_suite.db"

# Archivio documentale master (fuori dal repo: 148+ MB, sincronizzato via Google
# Drive). I documenti dell'archivio hanno storage_path con prefisso "@archive/"
# e vengono risolti qui invece che dentro BASE_DIR. Override con ECSP_ARCHIVE_DIR.
ARCHIVE_PREFIX = "@archive/"
ARCHIVE_DIR = Path(
    os.environ.get("ECSP_ARCHIVE_DIR", str(BASE_DIR.parent / "Pariter Equity — Archivio documentale"))
).resolve()


def resolve_storage_path(storage_path):
    """Risolve lo storage_path di un documento in un file su disco, gestendo sia i
    file interni al repo sia quelli dell'archivio (prefisso @archive/).
    Ritorna (Path, ok) dove ok=True solo se il file esiste ed e' dentro la radice
    consentita (anti path-traversal)."""
    if storage_path and storage_path.startswith(ARCHIVE_PREFIX):
        rel = storage_path[len(ARCHIVE_PREFIX):]
        file_path = (ARCHIVE_DIR / rel).resolve()
        root = ARCHIVE_DIR
    else:
        file_path = (BASE_DIR / storage_path).resolve()
        root = BASE_DIR.resolve()
    ok = str(file_path).startswith(str(root)) and file_path.exists()
    return file_path, ok


# Descrizioni di contenuto dei documenti d'archivio, ricavate dai documenti-indice
# (00_INDICE_GENERALE, 01_KNOWLEDGE_BASE sez. 10, 05_PROCESSO_ONBOARDING). Servono a
# mostrare sotto ogni file una breve descrizione di cosa contiene.
_ALLEGATO_DESC = {
    "4": "Statuto e atto costitutivo",
    "5": "Servizi, piattaforma e selezione delle offerte",
    "8": "Rischi IT e presidi",
    "9": "Presidi prudenziali (art. 11)",
    "10": "Fondi propri",
    "12": "Onorabilita dei soci",
    "13": "Onorabilita e competenze dei gestori",
    "5_1": "Tipologie di servizi e procedura di selezione delle offerte",
    "5_2": "Descrizione della piattaforma",
    "5_3": "Strategia di marketing",
    "6_1": "Governance e assetto organizzativo",
    "7": "Trattamento dei dati personali (GDPR)",
    "8_1": "Rischi IT e presidi",
    "9_1": "Presidi prudenziali (art. 11)",
    "9_4": "Piano triennale",
    "10_1": "Fondi propri",
    "10_2": "Fondi propri",
    "11": "Continuita operativa",
    "12": "Onorabilita dei soci",
    "13": "Onorabilita e competenze dei gestori",
    "14": "Policy sui conflitti di interesse",
    "16": "Gestione dei reclami",
    "17": "Pagamenti e custodia (Banca Sella)",
    "18": "KIIS - scheda con le informazioni chiave sull'investimento",
    "19": "Limiti di investimento e classificazione investitori",
}
# Modulistica vigente M1-M14 (rinumerazione giugno 2026). Lo stesso numero puo' indicare
# un documento diverso rispetto al vecchio assetto: questa e' la numerazione nuova.
_M_DESC = {
    1: "Checklist documentale - onboarding proponente", 2: "Checklist KYC - art. 5 ECSP",
    3: "Checklist - limite EUR 5.000.000", 4: "Relazione controlli art. 5 / AML (attestazione 2a linea)",
    5: "Checklist - verifica della scheda KIIS", 6: "Modulo di valutazione CVOI - scoring (Allegato 5.1)",
    7: "Fascicolo di valutazione (8 sezioni)", 8: "Parere dell'Advisory Committee",
    9: "Relazione di insussistenza dei conflitti", 10: "Verbale CdA - delibera sull'offerta",
    11: "Modulo classificazione investitore e test (Allegato 19)", 12: "Registri - conflitti di interesse e reclami",
    13: "Notifica data breach - al Garante (GDPR)", 14: "Segnalazione incidente ICT grave - a CONSOB (DORA)",
}
_C_DESC = {
    1: "PEC interna di ricezione della candidatura", 2: "Conferma di caricamento al proponente",
    3: "Richiesta di integrazione documentale", 4: "Comunicazione di esito verifica documentale positivo",
    5: "Comunicazione di progetto approvato", 6: "Comunicazione di progetto non ammesso",
    7: "Avviso di pubblicazione dell'offerta", 8: "Conferma dell'ordine all'investitore",
    9: "Comunicazione a CONSOB tramite SiCrowd",
}
_DORA_DESC = {
    1: "Piano di adeguamento e gap analysis DORA", 2: "Delibera CdA di adozione del quadro ICT",
    3: "Politica di gestione e classificazione incidenti ICT", 4: "Registro degli incidenti ICT",
    5: "Registro dei fornitori terzi ICT", 6: "Programma dei test di resilienza",
}
_DP_DESC = {
    1: "Politica di trattamento dati (GDPR)", 2: "Rischi IT e presidi",
    3: "Piano di continuita operativa", 4: "Quadro del rischio ICT (DORA)",
}
_ROOT_DESC = [
    ("knowledge_base", "Quadro d'insieme: anagrafica, autorizzazioni, gruppo, persone, dati, FAQ."),
    ("indice_generale", "Indice generale e guida alla consultazione dell'archivio."),
    ("storico_e_stato", "Storia dell'autorizzazione dal 2019 e stato attuale, con i punti aperti."),
    ("organigramma", "Assetto proprietario/di controllo e struttura organizzativa interna."),
    ("framework_compliance", "Architettura dei controlli: fonti, tre linee di difesa, presidi per rischio."),
    ("manuale_dati", "Presidio operativo GDPR / rischi IT / continuita / DORA."),
    ("processo_onboarding", "Ciclo dell'offerta passo-passo, con le comunicazioni che partono a ogni fase."),
]
_KEYWORD_DESC = [
    ("nuovo_statuto", "Statuto vigente adottato nel 2025."),
    ("domanda_autorizzaz", "Domanda di autorizzazione ai servizi di crowdfunding (ECSP)."),
    ("nota_accompagn", "Nota di accompagnamento alla domanda."),
    ("prospetto_dati", "Prospetto dati del programma di attivita."),
    ("autodichiarazione", "Autodichiarazione resa dal proponente."),
    ("lettera", "Lettera di scambio con l'Autorita di vigilanza."),
    ("riscontro", "Documento di riscontro a una richiesta CONSOB."),
    ("avvio", "Comunicazione di avvio attivita."),
    ("vaglio", "Vaglio del materiale storico rispetto al regime ECSP."),
    ("rilievi", "Rilievi sulla procedura di selezione."),
    ("verbale", "Verbale societario."),
    ("relazione", "Relazione interna della funzione di controllo."),
    ("checklist", "Checklist operativa."),
    ("registro", "Registro operativo."),
    ("leggimi", "Nota di lettura della cartella."),
    ("rettifiche", "Rettifiche e variazioni post-autorizzazione."),
    ("variazioni", "Rettifiche e variazioni post-autorizzazione."),
    ("advisory", "Parere dell'Advisory Committee (modello)."),
    ("valutazione", "Valutazione del progetto (CVOI)."),
    ("cvoi", "Verbale di valutazione del progetto (CVOI)."),
    ("classificazione", "Classificazione dell'investitore."),
    ("onorabilit", "Documenti di onorabilita di soci o gestori."),
    ("conferma", "Comunicazione di conferma."),
    ("notifica", "Notifica a un'autorita."),
    ("segnalazione", "Segnalazione di un evento."),
    ("modello", "Modello operativo di riferimento."),
    ("modulo", "Modulo operativo."),
    ("faq", "FAQ - domande e risposte frequenti."),
    ("guida", "Guida informativa per l'investitore."),
    ("informativa", "Informativa per gli investitori."),
    ("contratto", "Contratto."),
    ("obblighi", "Obblighi del gestore del portale."),
    ("informazioni", "Informazioni per l'investitore."),
    ("investitor", "Materiale informativo per gli investitori."),
    ("education", "Materiale di investor education."),
    ("mail", "Modello di comunicazione email."),
    ("commenti", "Commenti e note di lavoro."),
    ("statuto", "Statuto."),
]


def archive_doc_description(filename, relpath=""):
    """Breve descrizione del contenuto di un documento d'archivio, in base al codice
    (Allegato N, Mx, Cx, DORAx, DPx) o a parole chiave nel nome file."""
    low = (filename or "").lower()
    for key, desc in _ROOT_DESC:
        if key in low:
            return desc
    m = re.match(r"dp(\d)", low)
    if m and int(m.group(1)) in _DP_DESC:
        return _DP_DESC[int(m.group(1))]
    m = re.match(r"dora(\d)", low)
    if m and int(m.group(1)) in _DORA_DESC:
        return _DORA_DESC[int(m.group(1))]
    m = re.match(r"m(\d+)[_ ]", low)
    if m and int(m.group(1)) in _M_DESC:
        return _M_DESC[int(m.group(1))]
    m = re.match(r"c(\d+)[_ ]", low)
    if m and int(m.group(1)) in _C_DESC:
        return _C_DESC[int(m.group(1))]
    m = re.search(r"allegato[_ ]?(\d+)(?:[_ (]+(\d+))?", low)
    if m:
        pair = f"{m.group(1)}_{m.group(2)}" if m.group(2) else ""
        if pair in _ALLEGATO_DESC:
            return _ALLEGATO_DESC[pair]
        if m.group(1) in _ALLEGATO_DESC:
            return _ALLEGATO_DESC[m.group(1)]
    for key, desc in _KEYWORD_DESC:
        if key in low:
            return desc
    return ""

ROLE_LABELS = {
    "compliance": "Compliance officer",
    "legal": "Legale",
    "technical_committee": "Comitato Tecnico",
    "covi": "Advisory Committee",
    "covi_opinion": "Advisory Committee",
    "board": "CdA",
    "operator": "Operatore",
    "admin": "Amministratore",
}

PHASES = [
    ("appena_caricato", "Appena caricato"),
    ("istruttoria_documentazione", "Istruttoria documentazione"),
    ("verifiche", "Verifiche"),
    ("comitato_tecnico", "Comitato Tecnico"),
    ("covi", "Advisory Committee"),
    ("cda", "CdA"),
    ("integrazione_documenti", "Integrazione documenti"),
    ("contratto", "Contratto"),
    ("pre_pubblicazione", "Pre-pubblicazione"),
    ("pubblicato", "Pubblicato"),
    ("raccolta_in_corso", "Raccolta in corso"),
    ("concluso", "Concluso"),
    ("respinta", "Respinta"),
    ("archiviato", "Archiviato"),
]

PHASE_LABELS = dict(PHASES)
PHASE_INDEX = {key: idx for idx, (key, _) in enumerate(PHASES)}

# --- Istruttoria Pariter: macchina a stati delle pratiche (spec sez. 4) ---
PRACTICE_STATUSES = [
    ("dossier_ricevuto", "Dossier ricevuto"),
    ("verifica_documentale", "Verifica documentale in corso"),
    ("da_integrare", "Da integrare"),
    ("fase1_validata", "Fase 1 validata"),
    ("fase2_completata", "Fase 2 completata dal proponente"),
    ("bozza_kiis_ricevuta", "Bozza KIIS ricevuta"),
    ("verifiche_interne", "Verifiche interne Pariter in corso"),
    ("pronto_cvoi", "Pronto per CVOI"),
    ("cvoi_generato", "Report CVOI generato"),
    ("in_advisory", "In Advisory Committee"),
    ("advisory_ricevuto", "Parere Advisory Committee ricevuto"),
    ("attesa_cda", "In attesa delibera CdA"),
    ("cda_positiva", "Delibera CdA positiva"),
    ("cda_positiva_condizioni", "Delibera CdA positiva con condizioni"),
    ("cda_negativa", "Delibera CdA negativa"),
    ("in_pre_golive", "In pre go-live"),
    ("pronta_verifica_finale", "Pronta per verifica finale"),
    ("pronta_golive", "Pronta per go-live"),
    ("pubblicata", "Pubblicata"),
    ("respinta", "Respinta"),
    ("archiviata", "Archiviata"),
]
PRACTICE_STATUS_LABELS = dict(PRACTICE_STATUSES)
PRACTICE_STATUS_INDEX = {key: idx for idx, (key, _) in enumerate(PRACTICE_STATUSES)}

# Transizioni ammesse. Regola cardine (Allegato 5.1): l'Advisory Committee esprime
# il parere DOPO la valutazione CVOI e PRIMA dell'unica delibera del CdA; nessun
# salto di fase. Gli esiti negativi confluiscono in "respinta".
PRACTICE_FLOW = {
    "dossier_ricevuto": {"verifica_documentale", "respinta"},
    "verifica_documentale": {"da_integrare", "fase1_validata", "respinta"},
    "da_integrare": {"verifica_documentale", "fase1_validata", "respinta"},
    "fase1_validata": {"fase2_completata", "respinta"},
    "fase2_completata": {"bozza_kiis_ricevuta", "respinta"},
    "bozza_kiis_ricevuta": {"verifiche_interne", "respinta"},
    "verifiche_interne": {"pronto_cvoi", "da_integrare", "respinta"},
    "pronto_cvoi": {"cvoi_generato", "respinta"},
    "cvoi_generato": {"in_advisory", "respinta"},
    "in_advisory": {"advisory_ricevuto"},
    "advisory_ricevuto": {"attesa_cda"},
    "attesa_cda": {"cda_positiva", "cda_positiva_condizioni", "cda_negativa"},
    "cda_positiva": {"in_pre_golive"},
    "cda_positiva_condizioni": {"in_pre_golive"},
    "cda_negativa": {"respinta"},
    "in_pre_golive": {"pronta_verifica_finale", "da_integrare"},
    "pronta_verifica_finale": {"pronta_golive", "in_pre_golive"},
    "pronta_golive": {"pubblicata"},
    "pubblicata": {"archiviata"},
    "respinta": {"archiviata"},
}

# Bucket per il Deal-hub Pariter (vista istruttoria / in corso / conclusi).
PRACTICE_BUCKET_CONCLUSI = {"respinta", "archiviata", "concluso"}
PRACTICE_BUCKET_IN_CORSO = {"pubblicata"}
# etichetta + classe colore per il bucket della pratica (grigio/verde/blu)
PRACTICE_BUCKET_META = {
    "istruttoria": ("In istruttoria", "bucket-istruttoria"),
    "in_corso": ("In corso", "bucket-in-corso"),
    "conclusi": ("Conclusa", "bucket-conclusi"),
}


def practice_bucket(status):
    if status in PRACTICE_BUCKET_CONCLUSI:
        return "conclusi"
    if status in PRACTICE_BUCKET_IN_CORSO:
        return "in_corso"
    return "istruttoria"


# Aree di scoring CVOI (spec sez. 9): chiave, etichetta, peso, max, soglia minima.
CVOI_AREAS = [
    ("area1", "Completezza documentazione e valutazione management", 0.35, 30, 18),
    ("area2", "Strategia, prodotto/servizio e mercato", 0.35, 35, 21),
    ("area3", "Business Model & Financial Plan", 0.30, 30, 18),
]
CVOI_OVERALL_THRESHOLD = round(sum(thr * w for _, _, w, _, thr in CVOI_AREAS), 2)  # 19.05

# Criteri di valutazione per area, dal template reale "Report valutazione del progetto".
# Ogni criterio vale max 5 punti: area1=6 (30), area2=7 (35), area3=6 (30), totale 95.
CVOI_CRITERIA = {
    "area1": [
        "Il soggetto proponente conosce il mercato oggetto della proposta ed ha maturato esperienza nello stesso",
        "Il soggetto proponente ha gia' maturato esperienze simili significative di successo",
        "Il livello di scolarizzazione e' elevato",
        "Il soggetto proponente e' composto da un team iniziale sufficientemente completo",
        "Esiste una strategia per integrare le figure chiave mancanti (assunzioni, sub-contracting, partnership)",
        "Compagine societaria e ripartizione quote congrue rispetto all'operazione da realizzare",
    ],
    "area2": [
        "La proposta illustra la domanda ed il mercato (disponibile a pagare) per il prodotto/servizio",
        "Sono in essere accordi commerciali remunerativi per l'azienda",
        "Dati e analisi di mercato evidenziano opportunita' di crescita, competizione e redditivita'",
        "Tecnologia differenziale con vantaggio competitivo difendibile nel medio-lungo periodo",
        "La strategia di commercializzazione appare appropriata",
        "Analisi liberta' di operare/anteriorita' (IP) e strategia di protezione, se applicabile",
        "Buona comprensione dei rischi e delle opportunita' tecniche e commerciali",
    ],
    "area3": [
        "Il modello prevede la vendita di un prodotto/servizio che porta sostanziali benefici agli acquirenti",
        "Il bene o servizio genera margini difendibili nel medio-lungo termine",
        "Serve un mercato ampio e raggiungibile in maniera finanziariamente sostenibile",
        "Modello potenzialmente scalabile con risorse incrementali contenute",
        "Ipotesi del business plan supportate da dati quantitativi e verificabili",
        "Proiezioni economiche accurate; fabbisogni finanziari e impieghi coerenti",
    ],
}
CVOI_CRITERION_MAX = 5

# Tab del fascicolo istruttoria (dettaglio pratica).
PRACTICE_TABS = [
    ("riepilogo", "Riepilogo"),
    ("processo", "Processo"),
    ("documentale", "Verifica documentale"),
    ("interne", "Verifiche interne"),
    ("cvoi", "CVOI"),
    ("advisory", "Advisory Committee"),
    ("cda", "Delibera CdA"),
    ("condizioni", "Condizioni pre go-live"),
    ("validazione", "Validazione Fase 4"),
    ("campagna", "Pagina campagna"),
    ("storico", "Storico"),
]
PRACTICE_TAB_LABELS = dict(PRACTICE_TABS)

# Processo onboarding/approvazione offerta (procedura operativa Pariter, 7 fasi).
# Ogni step: chiave, titolo, attore, descrizione, tab operativo collegato, comunicazioni (codici C).
ONBOARDING_STEPS = [
    {"key": "fase1", "fase": "Fase 1", "titolo": "Registrazione e candidatura",
     "attore": "Proponente / Sistema", "tab": "documentale",
     "descrizione": "Il proponente si registra sul portale, carica i 9 documenti di progetto e invia la candidatura. "
                    "All'invio parte la PEC interna di ricezione e la conferma di presa in carico al proponente (con numero pratica).",
     "comms": []},
    {"key": "fase2", "fase": "Fase 2", "titolo": "Istruttoria di ammissibilita'",
     "attore": "Team di valutazione / Responsabile dei controlli", "tab": "documentale",
     "descrizione": "Verifica completezza documentale (eventuale richiesta di integrazione), KYC art. 5 ECSP su titolare e "
                    "titolare effettivo, rispetto del limite di 5 milioni, relazione art. 5/AML. Se positiva, si avvia la valutazione di merito.",
     "comms": []},
    {"key": "fase3", "fase": "Fase 3", "titolo": "Valutazione di merito (CVOI)",
     "attore": "Comitato Valutazione Opportunita' di Investimento", "tab": "cvoi",
     "descrizione": "Scoring su 3 aree (soglia 19,05), reference call e incontri, accertamento conflitti, verifica coerenza "
                    "della KIIS e redazione del fascicolo di valutazione in 8 sezioni. Durata 30 giorni dalla documentazione.",
     "comms": []},
    {"key": "fase4", "fase": "Fase 4", "titolo": "Advisory Committee",
     "attore": "Advisory Committee", "tab": "advisory",
     "descrizione": "Sessione dedicata dell'Advisory Committee: esame del fascicolo CVOI e della bozza KIIS, parere "
                    "obbligatorio non vincolante (favorevole / con condizioni / sospensivo / contrario) con redazione e firma dei membri.",
     "comms": []},
    {"key": "fase5", "fase": "Fase 5", "titolo": "Relazione conflitti e delibera CdA",
     "attore": "Responsabile dei controlli / CdA", "tab": "cda",
     "descrizione": "Valutazione formale finale dei conflitti di interesse (relazione), convocazione del CdA, delibera "
                    "(approva o respinge) e verbale. Comunicazione dell'esito al proponente.",
     "comms": ["C5", "C6"]},
    {"key": "fase6", "fase": "Fase 6", "titolo": "Strutturazione e pubblicazione",
     "attore": "Team / Sistema", "tab": "validazione",
     "descrizione": "Strutturazione dell'offerta, finalizzazione KIIS, assegnazione dell'Identificativo dell'offerta e "
                    "pubblicazione sul portale. Eventuale comunicazione a CONSOB se dovuta.",
     "comms": ["C7", "C9"]},
    {"key": "fase7", "fase": "Fase 7", "titolo": "Onboarding investitore e raccolta",
     "attore": "Investitore / Sistema / Banca Sella", "tab": "",
     "descrizione": "Classificazione investitore (sofisticato/non), test di conoscenza e simulazione perdite, raccolta e "
                    "custodia somme presso Banca Sella, conferma ordine. A chiusura: accredito o restituzione.",
     "comms": ["C8"]},
    {"key": "fase8", "fase": "Fase 8", "titolo": "Post-offerta e obblighi continuativi",
     "attore": "Responsabile delle funzioni di controllo", "tab": "",
     "descrizione": "Monitoraggio campagna ed emittente, comunicazioni a CONSOB di ogni modifica sostanziale (SiCrowd), "
                    "segnalazioni di vigilanza periodiche.",
     "comms": ["C9"]},
]

# Banda del ciclo di vita a cui appartiene ogni fase della barra di processo:
# grigio = istruttoria (fino alla pubblicazione), verde = in corso/online (raccolta),
# blu = conclusa (post-offerta). Coerente con i bucket del deal-hub.
PHASE_BAND = {
    "fase1": "istruttoria", "fase2": "istruttoria", "fase3": "istruttoria",
    "fase4": "istruttoria", "fase5": "istruttoria", "fase6": "istruttoria",
    "fase7": "in_corso",
    "fase8": "conclusi",
}
PHASE_BAND_LABELS = {"istruttoria": "In istruttoria", "in_corso": "In corso (online)", "conclusi": "Conclusa"}


# Comunicazioni del processo (testi precompilati, modificabili al momento dell'invio).
EMAIL_TEMPLATES = {
    "C1": {"label": "C1 - PEC interna di ricezione", "to": "interno",
           "subject": "Ricezione candidatura - {progetto} (pratica {pratica})",
           "body": "Si conferma la ricezione della candidatura per il progetto {progetto} del proponente {proponente}.\n"
                   "Riferimento pratica: {pratica}.\nData/ora di ricezione registrate a sistema."},
    "C2": {"label": "C2 - Presa in carico al proponente", "to": "proponente",
           "subject": "{pratica} - candidatura presa in carico",
           "body": "Gentile {proponente},\nconfermiamo che la documentazione e' stata presa in carico. "
                   "Numero pratica: {pratica}.\nL'istruttoria si concludera' indicativamente entro 30 giorni.\nPariter Equity"},
    "C3K": {"label": "C3K - Segnalazione/correzione KIIS (al proponente)", "to": "proponente",
            "subject": "Scheda KIIS - completamento/correzione - pratica {pratica}",
            "body": "Gentile {proponente},\nla scheda informativa (KIIS) del progetto {progetto} e' redatta e validata da Voi quali titolari del "
                    "progetto (art. 23 Reg. (UE) 2020/1503). Dalla nostra verifica risultano da completare/correggere:\n"
                    "- [campi mancanti]\nVi invitiamo a completare/correggere la scheda. In assenza di riscontro tempestivo l'offerta "
                    "potra' essere sospesa (fino a 30 giorni) e quindi cancellata. Non e' un esito dell'istruttoria.\nPariter Equity"},
    "C3": {"label": "C3 - Richiesta di integrazione", "to": "proponente",
           "subject": "Richiesta di integrazione documentale - pratica {pratica}",
           "body": "Gentile {proponente},\nper proseguire l'istruttoria del progetto {progetto} occorre integrare/chiarire:\n"
                   "- [specificare i documenti mancanti]\nIl termine resta sospeso fino al riscontro.\nPariter Equity"},
    "C4": {"label": "C4 - Verifica documentale positiva", "to": "proponente",
           "subject": "Verifica documentale positiva - pratica {pratica}",
           "body": "Gentile {proponente},\nla verifica documentale ha avuto esito positivo: il progetto {progetto} passa "
                   "alla valutazione di merito del team (CVOI).\nPariter Equity"},
    "C5": {"label": "C5 - Progetto approvato", "to": "proponente",
           "subject": "Progetto approvato - {progetto}",
           "body": "Gentile {proponente},\nil Consiglio di Amministrazione ha approvato il progetto {progetto}. "
                   "Vi contatteremo per avviare la strutturazione e la pubblicazione dell'offerta.\nPariter Equity"},
    "C6": {"label": "C6 - Mancata ammissione", "to": "proponente",
           "subject": "Esito valutazione - {progetto}",
           "body": "Gentile {proponente},\ncomunichiamo la mancata ammissione del progetto {progetto}.\n"
                   "Motivazione sintetica: [specificare].\nPariter Equity"},
    "C7": {"label": "C7 - Avviso di pubblicazione", "to": "proponente",
           "subject": "Offerta pubblicata - {progetto}",
           "body": "Gentile {proponente},\nl'offerta {progetto} e' stata pubblicata sul portale.\n"
                   "Link alla campagna: [link]\nData di avvio raccolta: [data].\nPariter Equity"},
    "C8": {"label": "C8 - Conferma ordine all'investitore", "to": "investitore",
           "subject": "Conferma ordine - {progetto}",
           "body": "Gentile investitore,\nconfermiamo la sottoscrizione dell'ordine sull'offerta {progetto}.\n"
                   "Importo, strumento e condizioni come da riepilogo; restano fermi i diritti di recesso/riflessione.\nPariter Equity"},
    "C9": {"label": "C9 - Comunicazione a CONSOB (SiCrowd)", "to": "consob",
           "subject": "Comunicazione - {progetto}",
           "body": "Comunicazione a CONSOB relativa all'offerta {progetto} (proponente {proponente}).\n"
                   "Oggetto: [pubblicazione / modifica sostanziale ex art. 15.3 ECSP].\nTrasmissione via SiCrowd."},
}

# Documenti attesi nel dossier proponente, per fase (spec sez. 2/7).
PRACTICE_DOC_SEED = [
    # Fase 1 - i 9 documenti di candidatura, esatti dal manuale onboarding (checklist M1)
    ("fase1", "Candidatura", "Presentazione del progetto imprenditoriale", 1),
    ("fase1", "Candidatura", "Piano finanziario storico + proiezioni a 3 anni", 0),
    ("fase1", "Candidatura", "Visura camerale aggiornata", 1),
    ("fase1", "Candidatura", "Statuto", 1),
    ("fase1", "Candidatura", "Ultimi due bilanci depositati", 0),
    ("fase1", "Candidatura", "Informazioni operazione (fabbisogno, valutazione, condizioni)", 1),
    ("fase1", "Candidatura", "Sito web", 0),
    ("fase1", "Candidatura", "Key manager ed esperienze", 1),
    ("fase1", "Candidatura", "Documentazione integrativa eventuale", 0),
    # Fase 2 - integrazione ammissibilita' (richiesti dopo l'invio), per gruppi
    ("fase2", "Dichiarazioni e verifiche KYC", "Autodichiarazione onorabilita' esponenti", 1),
    ("fase2", "Dichiarazioni e verifiche KYC", "Casellario giudiziale esponenti", 1),
    ("fase2", "Dichiarazioni e verifiche KYC", "Dichiarazione titolare effettivo e giurisdizioni non cooperative", 1),
    ("fase2", "Limite di raccolta", "Dichiarazione sul rispetto del limite di 5.000.000 EUR", 1),
    ("fase2", "Limite di raccolta", "Evidenze raccolte ultimi 18 mesi", 1),
    # Fase 3 - approfondimenti per la valutazione di merito (CVOI)
    ("fase3", "Offerta", "Scheda approfondita progetto", 0),
    ("fase3", "Offerta", "Scheda economico-finanziaria", 0),
    ("fase3", "Offerta", "Termini preliminari offerta", 0),
    ("fase3", "KIIS", "Bozza KIIS secondo modello ufficiale", 1),
    ("fase4", "KIIS", "KIIS definitivo", 1),
    ("fase4", "Societaria", "Delibera aumento capitale", 1),
    ("fase4", "Societaria", "Statuto aggiornato o conferma statuto vigente", 1),
    ("fase4", "Contratto", "Contratto Pariter-proponente firmato", 1),
    ("fase4", "Campagna", "Pagina campagna validata", 1),
]

# Relazioni interne Pariter (spec sez. 8).
INTERNAL_REVIEW_TYPES = [
    ("aml_art5", "Relazione controlli art. 5 / AML (M4)"),
    ("conflitti", "Relazione di insussistenza dei conflitti (M9)"),
    ("coerenza_kiis", "Verifica della scheda KIIS (M5)"),
]
INTERNAL_REVIEW_LABELS = dict(INTERNAL_REVIEW_TYPES)
# Fascicolo di valutazione (M7): documento a se, NON nel conteggio del gate ammissibilita'.
INTERNAL_REVIEW_LABELS["fascicolo"] = "Fascicolo di valutazione del progetto (M7)"

DOC_STATUS_LABELS = {
    "presente": "Presente",
    "mancante": "Mancante",
    "da_verificare": "Da verificare",
    "verificato": "Verificato",
    "da_integrare": "Da integrare",
    "incoerente": "Incoerente",
    "non_utilizzabile": "Non utilizzabile",
}


# LEI di Pariter Equity (ISO 17442). Da confermare con il valore reale.
PARITER_LEI = "815600PARITEREQUITY0"


def offer_identifier(practice):
    """Identificativo dell'offerta per la KIIS: LEI + numero pratica a 8 cifre."""
    nr = practice["pratica_no"] if "pratica_no" in practice.keys() and practice["pratica_no"] else ""
    return (PARITER_LEI + nr) if nr else ""


def practice_status_label(status):
    return PRACTICE_STATUS_LABELS.get(status, status)


def practice_next_steps(status):
    return PRACTICE_FLOW.get(status, set())


def next_step_summary(status):
    nxt = [s for s in practice_next_steps(status) if s not in {"respinta", "archiviata"}]
    if not nxt:
        return "-"
    nxt.sort(key=lambda s: PRACTICE_STATUS_INDEX.get(s, 99))
    return ", ".join(practice_status_label(s) for s in nxt)

REQUIREMENT_SEED = [
    ("Documentazione", "Visura camerale aggiornata", 1),
    ("Documentazione", "Bilanci ultimi due esercizi", 1),
    ("Documentazione", "Business plan e piano finanziario", 1),
    ("KYC", "KYC proponente e titolari effettivi", 1),
    ("KIIS", "Bozza KIIS iniziale", 1),
]

VERIFICATION_SEED = [
    "Onorabilita esponenti - art. 5",
    "Requisiti del proponente",
    "Conflitti d'interesse",
    "Completezza informativa",
]

OFFICIAL_TEMPLATES = [
    {
        "title": "Guida operativa domanda autorizzazione ECSP",
        "authority": "CONSOB / Banca d'Italia",
        "filename": "CONSOB-BDI-guida-operativa-crowdfunding-aprile-2025.pdf",
        "source_url": "https://www.consob.it/documents/d/asset-library-1912910/guida_operativa_crowdfunding_consob_bi_aprile_2025",
        "status": "Scaricato",
        "note": "Guida congiunta del 4 aprile 2025 per la compilazione della domanda.",
    },
    {
        "title": "Template domanda autorizzazione servizi crowdfunding",
        "authority": "CONSOB",
        "filename": "CONSOB-template-domanda-autorizzazione-servizi-crowdfunding.docx",
        "source_url": "https://www.consob.it/documents/1912911/1950567/Template_domanda_autorizzazione_servizi_crowdfunding.docx",
        "status": "Scaricato",
        "note": "DOCX operativo per domanda o estensione autorizzativa.",
    },
    {
        "title": "Delibera obblighi comunicazione CSP",
        "authority": "CONSOB",
        "filename": "CONSOB-delibera-23656-2025-obblighi-comunicazione-crowdfunding.pdf",
        "source_url": "https://www.consob.it/documents/d/asset-library-1912910/del_consob_2025_23656",
        "status": "Scaricato",
        "note": "Delibera n. 23656/2025, in vigore dal 29 settembre 2025.",
    },
    {
        "title": "Regolamento crowdfunding",
        "authority": "CONSOB",
        "filename": "CONSOB-regolamento-22720-2023-crowdfunding.pdf",
        "source_url": "https://www.consob.it/documents/1912911/1950567/reg_consob_2023_22720.pdf/24669272-bb2b-bde0-cb3a-257e5812eed2",
        "status": "Scaricato",
        "note": "Regolamento n. 22720/2023.",
    },
    {
        "title": "Provvedimento attuazione TUF per CSP",
        "authority": "Banca d'Italia",
        "filename": "BDI-provvedimento-2024-05-06-crowdfunding.pdf",
        "source_url": "https://www.consob.it/documents/1912911/1919711/bi_20240506.pdf/65182d9b-766c-4cb8-2321-47fbb043eef3",
        "status": "Scaricato",
        "note": "Disposizioni Banca d'Italia del 6 maggio 2024.",
    },
    {
        "title": "Segnalazione esternalizzazioni",
        "authority": "Banca d'Italia",
        "filename": "BDI-provvedimento-2023-05-31-esternalizzazioni.pdf",
        "source_url": "https://www.consob.it/documents/1912911/1919711/bi_31_maggio_2023.pdf/a0443dcf-2fe9-02d8-649d-3fd00e948ec3",
        "status": "Scaricato",
        "note": "Provvedimento Banca d'Italia del 31 maggio 2023.",
    },
    {
        "title": "AIEC per Regolamento 1503",
        "authority": "CONSOB",
        "filename": "CONSOB-AIEC-Regolamento-1503.pdf",
        "source_url": "https://www.consob.it/documents/11973/7117490/AIEC.pdf/d84f1c63-fdfd-4072-5e07-6ecbebdca07c?version=1.0&t=1728639402735null&download=true",
        "status": "Scaricato",
        "note": "Documento CONSOB di chiarimento/istruzioni su AIEC.",
    },
    {
        "title": "Consultazione obblighi informativi crowdfunding",
        "authority": "CONSOB",
        "filename": "CONSOB-consultazione-crowdfunding-2025-01-17.pdf",
        "source_url": "https://www.consob.it/documents/11973/5638890/consultazione_crowdfunding_20250117.pdf/d1eac78e-ec4b-1387-4a77-7ba583c165f1",
        "status": "Scaricato",
        "note": "Documento di consultazione del 17 gennaio 2025.",
    },
]

COMMUNICATION_CATALOG = [
    {
        "title": "Domanda di autorizzazione o estensione ECSP",
        "recipient": "CONSOB o Banca d'Italia, secondo il tipo di soggetto",
        "trigger": "Prima di prestare servizi di crowdfunding o per estendere servizi autorizzati",
        "deadline": "Procedimento autorizzativo; integrazioni nei termini richiesti dall'Autorita",
        "source": "Reg. UE 2020/1503 art. 12; Reg. delegato UE 2022/2112; Reg. CONSOB 22720/2023 art. 3",
        "template": "CONSOB-template-domanda-autorizzazione-servizi-crowdfunding.docx",
        "payload": "Programma attivita, governance, controlli, outsourcing, reclami, conflitti, KIIS, investitori, requisiti esponenti e partecipanti.",
        "status": "Template scaricato",
    },
    {
        "title": "Avvio, interruzione e riavvio dell'utilizzo dell'autorizzazione",
        "recipient": "CONSOB e Banca d'Italia",
        "trigger": "Avvio effettivo, interruzione o riavvio della fornitura dei servizi ECSP",
        "deadline": "Senza indugio",
        "source": "Reg. CONSOB 22720/2023 art. 7; BDI provvedimento 6 maggio 2024",
        "template": "",
        "payload": "Data evento, piattaforma interessata, servizi coinvolti, motivazione, impatti operativi e referente interno.",
        "status": "Template pubblico non individuato",
    },
    {
        "title": "Modifiche sostanziali delle condizioni di autorizzazione",
        "recipient": "CONSOB e Banca d'Italia",
        "trigger": "Variazioni rilevanti rispetto alle condizioni autorizzative",
        "deadline": "Senza indugio",
        "source": "Reg. UE 2020/1503 art. 15(3); Delibera CONSOB 23656/2025 tabella 1; BDI provvedimento 6 maggio 2024",
        "template": "CONSOB-delibera-23656-2025-obblighi-comunicazione-crowdfunding.pdf",
        "payload": "Denominazione, nomi commerciali, siti, sedi, statuto, CdA/controllo, personale, revisore, funzioni controllo, nuovi servizi, cross-border, marketing, selezione offerte, conflitti, reclami, KIIS, investor checks.",
        "status": "Schema in provvedimento scaricato",
    },
    {
        "title": "Operativita transfrontaliera / passaporto europeo",
        "recipient": "Autorita home come single point of contact, ESMA e Autorita host tramite procedura",
        "trigger": "Intenzione di fornire servizi in altro Stato membro",
        "deadline": "Prima dell'avvio; operativita dal ricevimento comunicazione o al piu tardi 15 giorni dopo l'invio",
        "source": "Reg. UE 2020/1503 art. 18; Delibera CONSOB 23656/2025 tabella 1 punto 13",
        "template": "",
        "payload": "Stati membri, responsabili locali, data inizio prevista, altre attivita non ECSP, impatti organizzativi e procedure.",
        "status": "Template pubblico non individuato",
    },
    {
        "title": "KIIS offerta art. 23",
        "recipient": "Investitori; CONSOB tramite SICROWD",
        "trigger": "Prima della messa a disposizione del KIIS ai potenziali investitori",
        "deadline": "Contestuale alla trasmissione del KIIS; il Reg. UE consente eventuale notifica ex ante almeno 7 giorni lavorativi se richiesta dall'Autorita",
        "source": "Reg. UE 2020/1503 art. 23; Reg. CONSOB 22720/2023 art. 6; Delibera CONSOB 23656/2025 tabella 2.1",
        "template": "",
        "payload": "Excel SICROWD con dati offerta, progetto, titolare, strumenti, costi, rischi e campi specifici loan/debt/equity/ICFP/other.",
        "status": "Excel SICROWD non scaricabile pubblicamente",
    },
    {
        "title": "KIIS a livello piattaforma art. 24",
        "recipient": "Investitori; CONSOB tramite SICROWD",
        "trigger": "Prestazione di gestione individuale di portafogli di prestiti",
        "deadline": "Contestuale alla trasmissione del KIIS",
        "source": "Reg. UE 2020/1503 art. 24; Delibera CONSOB 23656/2025 tabella 2.2",
        "template": "",
        "payload": "Excel SICROWD con descrizione gestione, tassi min/max, scadenze, costi, categorie di rischio, quote e tassi default.",
        "status": "Excel SICROWD non scaricabile pubblicamente",
    },
    {
        "title": "Reporting annuale progetti finanziati e dati offerte",
        "recipient": "CONSOB o Banca d'Italia, secondo Autorita autorizzante",
        "trigger": "Chiusura esercizio / anno di riferimento",
        "deadline": "CONSOB: entro fine gennaio; Banca d'Italia: entro 25 gennaio per intermediari da essa autorizzati",
        "source": "Reg. UE 2020/1503 art. 16; Reg. esecuzione UE 2022/2120; Reg. CONSOB 22720/2023 art. 7; BDI provvedimento 6 maggio 2024; Delibera CONSOB 23656/2025 tabella 3",
        "template": "CONSOB-delibera-23656-2025-obblighi-comunicazione-crowdfunding.pdf",
        "payload": "Progetti finanziati, titolare, importo raccolto, strumento, residenza fiscale investitori, sofisticati/non sofisticati, offerte concluse e dati investitori.",
        "status": "Schema in provvedimento scaricato; Excel SICROWD non pubblico",
    },
    {
        "title": "Variazioni accordi di esternalizzazione",
        "recipient": "Banca d'Italia",
        "trigger": "Variazioni intervenute rispetto agli accordi di esternalizzazione in essere",
        "deadline": "Entro 30 aprile di ogni anno; se nessuna variazione, comunicare tale circostanza",
        "source": "BDI provvedimento 6 maggio 2024; campo 15 allegato Reg. delegato UE 2022/2112",
        "template": "BDI-provvedimento-2024-05-06-crowdfunding.pdf",
        "payload": "Accordi outsourcing, funzioni operative, fornitori, impatti e variazioni rispetto all'autorizzazione.",
        "status": "Schema in provvedimento scaricato",
    },
    {
        "title": "Segnalazione annuale esternalizzazioni",
        "recipient": "Banca d'Italia",
        "trigger": "Rilevazione contratti di esternalizzazione al 31 dicembre",
        "deadline": "Entro 30 aprile dell'anno successivo",
        "source": "BDI provvedimento 31 maggio 2023",
        "template": "BDI-provvedimento-2023-05-31-esternalizzazioni.pdf",
        "payload": "Contratti, firmatari/utilizzatori, fornitori/subfornitori, categoria funzione, FEI/FOI, cloud, paesi di erogazione e memorizzazione dati.",
        "status": "Provvedimento scaricato; allegati tecnici BDI da recuperare dalla pagina segnalazione",
    },
    {
        "title": "Partecipazioni qualificate nel fornitore specializzato",
        "recipient": "Banca d'Italia",
        "trigger": "Acquisizione/incremento sopra 20% o controllo; riduzione sotto soglia",
        "deadline": "Entro 10 giorni dall'evento o dalla conoscenza",
        "source": "BDI provvedimento 6 maggio 2024; Reg. UE 2020/1503; Reg. delegato UE 2022/2112 campo 12",
        "template": "BDI-provvedimento-2024-05-06-crowdfunding.pdf",
        "payload": "Operazione, soggetti, soglie, requisiti, nota con informazioni del campo 12 e ogni dato utile.",
        "status": "Schema in provvedimento scaricato",
    },
    {
        "title": "Valutazione idoneita esponenti aziendali",
        "recipient": "Banca d'Italia",
        "trigger": "Nomina o variazione di amministratori, controllo, direzione effettiva",
        "deadline": "Secondo procedura fit & proper applicabile",
        "source": "BDI provvedimento 6 maggio 2024; Provvedimento BDI 4 maggio 2021; Reg. delegato UE 2022/2112 campo 13",
        "template": "BDI-provvedimento-2024-05-06-crowdfunding.pdf",
        "payload": "Verbale valutazione, informazioni campo 13, CV/info ruolo, requisiti onorabilita, competenza, esperienza e disponibilita di tempo.",
        "status": "Schema in provvedimento scaricato",
    },
    {
        "title": "Reclami clienti",
        "recipient": "Clienti; registro interno; Autorita su richiesta o flussi specifici",
        "trigger": "Ricezione reclamo",
        "deadline": "Gestione tempestiva e comunicazione esito entro periodo ragionevole",
        "source": "Reg. UE 2020/1503 art. 7; Reg. delegato UE 2022/2117 su complaint handling; Delibera CONSOB 23656/2025 tabella 1 punto 17",
        "template": "",
        "payload": "Template reclamo gratuito al cliente, registro reclami, misure adottate, variazioni procedure reclami come modifica sostanziale.",
        "status": "Template cliente da predisporre internamente",
    },
    {
        "title": "Comunicazioni di marketing",
        "recipient": "Pubblico/investitori; vigilanza CONSOB",
        "trigger": "Ogni campagna o comunicazione marketing ECSP",
        "deadline": "Nessuna notifica/approvazione ex ante richiesta; controllo continuo",
        "source": "Reg. UE 2020/1503 art. 27; Reg. CONSOB 22720/2023 artt. 8-11; Delibera CONSOB 23656/2025 tabella 1 punto 14",
        "template": "",
        "payload": "Identificazione come marketing, coerenza con KIIS, lingua ammessa, avvertenza 'prima dell'adesione leggere la scheda...', variazioni significative strategia marketing come modifica sostanziale.",
        "status": "Nessun template; serve workflow approvativo interno",
    },
]

COMMUNICATION_WORKFLOWS = [
    {
        "id": "bdi-cf1",
        "title": "Patrimonio di vigilanza e requisiti prudenziali (CF1)",
        "authority": "Banca d'Italia",
        "channel": "INFOSTAT - Data entry",
        "frequency": "Semestrale",
        "deadline": "25 gennaio / 25 luglio",
        "reference": "30 giugno / 30 dicembre",
        "source": "Provvedimento Banca d'Italia 6 maggio 2024; disposizioni di vigilanza e canale INFOSTAT",
        "template": "BDI-provvedimento-2024-05-06-crowdfunding.pdf",
        "output": "Scheda CF1 + fascicolo evidenze bilancio/polizza",
        "required_docs": ["Situazione contabile", "Bilancio", "Polizza assicurativa", "Prospetto requisiti prudenziali"],
        "prefill": ["Dati piattaforma", "Bilanci/situazioni contabili caricati in Compagine", "Polizza assicurativa da documenti", "Capitale e patrimonio netto da bilancio"],
        "fields": [
            ("Periodo di riferimento", "text", "es. 30/06/2026"),
            ("Patrimonio netto", "number", "valore da bilancio"),
            ("Requisito prudenziale applicabile", "number", "calcolo interno"),
            ("Copertura assicurativa", "text", "compagnia, massimale, scadenza"),
            ("Note di quadratura", "textarea", "scostamenti, warning, rettifiche"),
        ],
    },
    {
        "id": "bdi-vig12",
        "title": "Rilevazione statistica crowdfunding (VIG 12)",
        "authority": "Banca d'Italia",
        "channel": "INFOSTAT - Upload file",
        "frequency": "Semestrale",
        "deadline": "25 febbraio / 25 agosto",
        "reference": "30 giugno / 30 dicembre",
        "source": "Provvedimento Banca d'Italia 6 maggio 2024; disposizioni di vigilanza e canale INFOSTAT",
        "template": "BDI-provvedimento-2024-05-06-crowdfunding.pdf",
        "output": "File statistico VIG12 + log dati campagne",
        "required_docs": ["Situazione contabile", "Ultima segnalazione VIG12", "Dati campagne", "Registro offerte"],
        "prefill": ["Deal e raccolte", "Investitori sofisticati/non sofisticati", "Stato offerte", "Documenti proposta"],
        "fields": [
            ("Periodo di riferimento", "text", "es. 30/06/2026"),
            ("Numero offerte pubblicate", "number", "da deal"),
            ("Importo raccolto", "number", "da investimenti/API"),
            ("Numero investitori", "number", "da investitori/API"),
            ("Note controlli statistici", "textarea", "coerenze con periodo precedente"),
        ],
    },
    {
        "id": "consob-report-annuale",
        "title": "Reporting annuale progetti finanziati e dati offerte",
        "authority": "CONSOB",
        "channel": "SICROWD - Excel",
        "frequency": "Annuale",
        "deadline": "Entro fine gennaio",
        "reference": "Anno solare precedente",
        "source": "Reg. UE 2020/1503 art. 16; Delibera CONSOB 23656/2025 tabella 3",
        "template": "CONSOB-delibera-23656-2025-obblighi-comunicazione-crowdfunding.pdf",
        "output": "Excel SICROWD + fascicolo progetto/offerta/investitori",
        "required_docs": ["Registro offerte", "KIIS", "Dati investitori", "Esiti campagne", "Ricevute invio"],
        "prefill": ["Deal conclusi", "Proponenti", "Investimenti via API", "Residenza e classificazione investitori"],
        "fields": [
            ("Anno di riferimento", "text", "es. 2026"),
            ("Offerte concluse", "number", "da deal"),
            ("Totale raccolto", "number", "da investimenti"),
            ("Investitori sofisticati", "number", "da CRM investitori"),
            ("Investitori non sofisticati", "number", "da CRM investitori"),
            ("Note di riconciliazione", "textarea", "anomalie o dati manuali"),
        ],
    },
    {
        "id": "consob-kiis-art23",
        "title": "KIIS offerta art. 23",
        "authority": "CONSOB",
        "channel": "SICROWD - Excel + KIIS",
        "frequency": "Per offerta",
        "deadline": "Prima/con contestuale messa a disposizione agli investitori",
        "reference": "Singola offerta",
        "source": "Reg. UE 2020/1503 art. 23; Delibera CONSOB 23656/2025 tabella 2.1",
        "template": "CONSOB-delibera-23656-2025-obblighi-comunicazione-crowdfunding.pdf",
        "output": "Pacchetto KIIS offerta + dati SICROWD",
        "required_docs": ["KIIS", "Business plan", "Delibera CdA", "Due diligence", "Contratto proponente"],
        "prefill": ["Anagrafica deal", "Anagrafica proponente", "Documenti proposta", "Costi e rischi da documenti"],
        "fields": [
            ("Deal / offerta", "text", "selezione offerta"),
            ("Titolare progetto", "text", "da proponente"),
            ("Importo obiettivo", "number", "da deal"),
            ("Strumento offerto", "text", "equity/debito/altro"),
            ("Warning e rischi specifici", "textarea", "da KIIS e due diligence"),
        ],
    },
    {
        "id": "consob-modifiche",
        "title": "Modifiche sostanziali condizioni di autorizzazione",
        "authority": "CONSOB / Banca d'Italia",
        "channel": "PEC / canale autorita",
        "frequency": "Event-driven",
        "deadline": "Senza indugio",
        "reference": "Evento rilevante",
        "source": "Reg. UE 2020/1503 art. 15(3); Delibera CONSOB 23656/2025 tabella 1",
        "template": "CONSOB-delibera-23656-2025-obblighi-comunicazione-crowdfunding.pdf",
        "output": "Lettera comunicazione + allegati evidenza variazione",
        "required_docs": ["Statuto", "Verbale CdA", "Organigramma", "Contratti outsourcing", "Procedure aggiornate"],
        "prefill": ["Compagine", "Governance", "Fornitori", "Documenti societari", "Procedure"],
        "fields": [
            ("Tipo modifica", "text", "es. sede, CdA, servizi, outsourcing, reclami"),
            ("Data efficacia", "date", ""),
            ("Descrizione modifica", "textarea", "cosa cambia e perche"),
            ("Impatto su autorizzazione", "textarea", "servizi, controlli, rischi, investor protection"),
            ("Allegati da trasmettere", "textarea", "elenco documenti"),
        ],
    },
    {
        "id": "bdi-outs",
        "title": "Segnalazione annuale esternalizzazioni (OUTS)",
        "authority": "Banca d'Italia",
        "channel": "INFOSTAT - Data entry",
        "frequency": "Annuale",
        "deadline": "30 aprile",
        "reference": "31 dicembre",
        "source": "Provvedimento Banca d'Italia 31 maggio 2023 sulle segnalazioni in materia di esternalizzazione",
        "template": "BDI-provvedimento-2023-05-31-esternalizzazioni.pdf",
        "output": "Registro outsourcing INFOSTAT + contratti fornitori",
        "required_docs": ["Contratti fornitori", "SLA", "DPA", "Exit plan", "Registro esternalizzazioni"],
        "prefill": ["Fornitori e contratti da Compagine", "Scadenze contratti", "Aree servizio", "Paesi/cloud da contratto"],
        "fields": [
            ("Data riferimento", "text", "31/12/anno"),
            ("Numero contratti outsourcing", "number", "da fornitori"),
            ("Funzioni essenziali/importanti", "textarea", "classificazione FEI/FOI"),
            ("Cloud e subfornitori", "textarea", "modello, paese, dati"),
            ("Variazioni rispetto all'anno precedente", "textarea", "nuovi, cessati, invariati"),
        ],
    },
    {
        "id": "bdi-ls",
        "title": "Libro soci (LS)",
        "authority": "Banca d'Italia",
        "channel": "INFOSTAT - Data entry",
        "frequency": "Annuale",
        "deadline": "31 maggio",
        "reference": "31 dicembre",
        "source": "Provvedimento Banca d'Italia 6 maggio 2024; disposizioni di vigilanza e canale INFOSTAT",
        "template": "BDI-provvedimento-2024-05-06-crowdfunding.pdf",
        "output": "Segnalazione libro soci + visura",
        "required_docs": ["Visura", "Libro soci", "Partecipogramma", "Patti parasociali"],
        "prefill": ["Partecipanti qualificati in Compagine", "Accordi persona", "Documenti societari"],
        "fields": [
            ("Data riferimento", "text", "31/12/anno"),
            ("Numero soci", "number", "da libro soci"),
            ("Partecipanti qualificati", "textarea", "nome, quota, controllo"),
            ("Variazioni intervenute", "textarea", "ingressi, uscite, soglie"),
        ],
    },
    {
        "id": "bdi-ict-risk",
        "title": "Autovalutazione rischi ICT",
        "authority": "Banca d'Italia",
        "channel": "PEC Supervisione_rischio_ICT",
        "frequency": "Su richiesta / periodica",
        "deadline": "Scadenza indicata nella richiesta",
        "reference": "Perimetro ICT/DORA",
        "source": "Regolamento (UE) 2022/2554 DORA; istruzioni Banca d'Italia per rischi ICT",
        "template": "",
        "output": "Questionario ICT + allegati procedure e contratti",
        "required_docs": ["Allegati autorizzazione 8.1/11", "Contratti IT", "Procedure ICT", "Registro incidenti"],
        "prefill": ["Fornitori IT", "Contratti outsourcing", "Documenti autorizzativi", "Incidenti registrati"],
        "fields": [
            ("Data richiesta autorita", "date", ""),
            ("Perimetro sistemi critici", "textarea", "piattaforma, KYC, pagamenti, conservazione"),
            ("Misure di controllo", "textarea", "sicurezza, BCP, monitoraggio"),
            ("Contratti ICT rilevanti", "textarea", "fornitori e SLA"),
        ],
    },
    {
        "id": "dora-incident",
        "title": "DORA - incidenti gravi ICT",
        "authority": "Banca d'Italia",
        "channel": "INFOSTAT - DORAI",
        "frequency": "Event-driven",
        "deadline": "Secondo tempistiche DORA applicabili",
        "reference": "Incidente ICT grave",
        "source": "Regolamento (UE) 2022/2554 DORA; canale Banca d'Italia per incidenti ICT",
        "template": "",
        "output": "Notifica incidente + timeline + remediation",
        "required_docs": ["Registro incidenti", "Report tecnico", "Comunicazioni utenti", "Piano remediation"],
        "prefill": ["Fornitori ICT", "Contratti SLA", "Log incidente", "Funzioni impattate"],
        "fields": [
            ("Data/ora incidente", "text", "timestamp"),
            ("Servizi impattati", "textarea", "piattaforma, pagamenti, KYC"),
            ("Causa e stato", "textarea", "diagnosi e contenimento"),
            ("Impatto clienti/investitori", "textarea", "numero soggetti e rischi"),
            ("Azioni correttive", "textarea", "remediation e owner"),
        ],
    },
]

COMMUNICATION_SCHEDULE = [
    {
        "workflow_id": "bdi-cf1",
        "period": "1 semestre 2026",
        "due_date": "2026-07-25",
        "status": "Da fare",
        "owner": "Compliance",
        "note": "Preparare dati patrimoniali, situazione contabile e polizza.",
    },
    {
        "workflow_id": "bdi-vig12",
        "period": "1 semestre 2026",
        "due_date": "2026-08-25",
        "status": "Da fare",
        "owner": "Operations",
        "note": "Riconciliare offerte, raccolta e dati investitori.",
    },
    {
        "workflow_id": "consob-kiis-art23",
        "period": "Offerte in pubblicazione",
        "due_date": "",
        "status": "Da fare",
        "owner": "Deal team",
        "note": "Da aprire per ogni nuova offerta prima della messa a disposizione del KIIS.",
    },
    {
        "workflow_id": "consob-modifiche",
        "period": "Eventi societari o autorizzativi",
        "due_date": "",
        "status": "Da fare",
        "owner": "Compliance",
        "note": "Da attivare quando cambia un elemento sostanziale dell'autorizzazione.",
    },
    {
        "workflow_id": "bdi-ict-risk",
        "period": "Perimetro ICT 2026",
        "due_date": "2026-09-30",
        "status": "Da fare",
        "owner": "Risk / IT",
        "note": "Raccogliere contratti ICT, procedure e controlli DORA.",
    },
    {
        "workflow_id": "consob-report-annuale",
        "period": "Anno 2025",
        "due_date": "2026-01-31",
        "status": "Conclusa",
        "owner": "Compliance",
        "note": "Archiviata nel fascicolo annuale.",
    },
    {
        "workflow_id": "bdi-outs",
        "period": "Rilevazione 31/12/2025",
        "due_date": "2026-04-30",
        "status": "Approvata",
        "owner": "Operations",
        "note": "Registro esternalizzazioni validato.",
    },
    {
        "workflow_id": "bdi-ls",
        "period": "Libro soci 31/12/2025",
        "due_date": "2026-05-31",
        "status": "Inviata",
        "owner": "Corporate",
        "note": "In attesa di chiusura interna/ricevuta definitiva.",
    },
    {
        "workflow_id": "dora-incident",
        "period": "Eventi ICT",
        "due_date": "",
        "status": "Da fare",
        "owner": "Risk / IT",
        "note": "Da attivare solo in caso di incidente ICT grave.",
    },
]


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def money(value):
    try:
        return f"EUR {float(value):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "EUR 0"


def today_iso():
    return date.today().isoformat()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def nice_date(value):
    if not value:
        return "-"
    return str(value)[:10]


def status_for_phase(phase):
    if phase in {"appena_caricato", "istruttoria_documentazione", "verifiche"}:
        return "Onboarding"
    if phase in {"comitato_tecnico", "covi", "cda", "contratto", "pre_pubblicazione"}:
        return "In approvazione"
    if phase == "integrazione_documenti":
        return "Integrazione documenti"
    if phase in {"pubblicato", "raccolta_in_corso"}:
        return "In corso"
    if phase == "concluso":
        return "Conclusa"
    if phase == "respinta":
        return "Respinta"
    if phase == "archiviato":
        return "Archiviata"
    return "Da lavorare"


def badge_class(value):
    normalized = (value or "").lower()
    if any(token in normalized for token in ["scad", "issue", "ko", "rifiut", "chiuso", "respint"]):
        return "danger"
    if any(token in normalized for token in ["approv", "ok", "complet", "pubblic", "corso"]):
        return "success"
    if any(token in normalized for token in ["immin", "integraz", "pre-", "attesa"]):
        return "warning"
    return "neutral"


def phase_label(phase):
    return PHASE_LABELS.get(phase, phase.replace("_", " ").title())


def deal_theme(title, proponent_name="", notes=""):
    text = f"{title} {proponent_name} {notes}".lower()
    if any(token in text for token in ["medtech", "diagnost", "health", "medical"]):
        return "MedTech"
    if any(token in text for token in ["green", "sosten", "energia", "manifatt", "robot"]):
        return "Industria sostenibile"
    if any(token in text for token in ["food", "spreco", "benefit"]):
        return "Food / Impact"
    if any(token in text for token in ["tech", "piattaforma", "software"]):
        return "Tecnologia"
    return "Generalist"


def sanitize_filename(name):
    base = Path(name or "documento").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-._")
    return base or "documento"


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rows(sql, params=()):
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def row(sql, params=()):
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql, params=()):
    with connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def ensure_column(conn, table, column, definition):
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / "generated").mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS platforms (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                regulator_profile TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS committee_members (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                committee TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                email TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS shareholders (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                name TEXT NOT NULL,
                subject_type TEXT NOT NULL DEFAULT 'Societa / ente',
                legal_form TEXT NOT NULL DEFAULT '',
                tax_id TEXT NOT NULL DEFAULT '',
                contact_email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                stake_percent REAL NOT NULL DEFAULT 0,
                beneficial_owners TEXT NOT NULL DEFAULT '',
                requisites_status TEXT NOT NULL DEFAULT 'Da verificare',
                status TEXT NOT NULL DEFAULT 'Attivo',
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS proponents (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                name TEXT NOT NULL,
                legal_form TEXT NOT NULL DEFAULT '',
                tax_id TEXT NOT NULL DEFAULT '',
                contact_email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '',
                sector TEXT NOT NULL DEFAULT '',
                beneficial_owners TEXT NOT NULL DEFAULT '',
                exposure REAL NOT NULL DEFAULT 0,
                internal_score TEXT NOT NULL DEFAULT 'Da valutare',
                crm_status TEXT NOT NULL DEFAULT 'In istruttoria',
                onboarding_status TEXT NOT NULL DEFAULT 'Documenti da raccogliere',
                owner_name TEXT NOT NULL DEFAULT '',
                source_system TEXT NOT NULL DEFAULT 'Manuale',
                external_proponent_id TEXT NOT NULL DEFAULT '',
                last_synced_at TEXT NOT NULL DEFAULT '',
                manual_override_notes TEXT NOT NULL DEFAULT '',
                next_action TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                proponent_id INTEGER NOT NULL REFERENCES proponents(id),
                title TEXT NOT NULL,
                funding_target REAL NOT NULL DEFAULT 0,
                platform_fee_percent REAL NOT NULL DEFAULT 5,
                phase TEXT NOT NULL,
                technical_reviewer_id INTEGER REFERENCES committee_members(id),
                covi_reviewer_id INTEGER REFERENCES committee_members(id),
                contract_required INTEGER NOT NULL DEFAULT 1,
                kiis_state TEXT NOT NULL DEFAULT 'Bozza',
                external_offer_id TEXT NOT NULL DEFAULT '',
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deal_requirements (
                id INTEGER PRIMARY KEY,
                deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                category TEXT NOT NULL,
                label TEXT NOT NULL,
                required INTEGER NOT NULL DEFAULT 1,
                completed INTEGER NOT NULL DEFAULT 0,
                due_date TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                completed_by INTEGER REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS verifications (
                id INTEGER PRIMARY KEY,
                deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
                area TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT NOT NULL DEFAULT '',
                owner_user_id INTEGER REFERENCES users(id),
                completed_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                deal_id INTEGER REFERENCES deals(id) ON DELETE SET NULL,
                proponent_id INTEGER REFERENCES proponents(id) ON DELETE SET NULL,
                origin TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                generated INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS committee_opinions (
                id INTEGER PRIMARY KEY,
                deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
                committee TEXT NOT NULL,
                reviewer_member_id INTEGER REFERENCES committee_members(id),
                outcome TEXT NOT NULL,
                summary TEXT NOT NULL,
                generated_document_id INTEGER REFERENCES documents(id),
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS board_decisions (
                id INTEGER PRIMARY KEY,
                deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
                outcome TEXT NOT NULL,
                notes TEXT NOT NULL,
                integration_required INTEGER NOT NULL DEFAULT 0,
                generated_document_id INTEGER REFERENCES documents(id),
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS compliance_tasks (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                area TEXT NOT NULL,
                title TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL,
                owner_role TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS platform_metrics (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                published_offers INTEGER NOT NULL DEFAULT 0,
                active_offers INTEGER NOT NULL DEFAULT 0,
                investors INTEGER NOT NULL DEFAULT 0,
                raised_amount REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS person_agreements (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                person_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                agreement_type TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Attivo',
                signed_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS person_documents (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                person_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                document_type TEXT NOT NULL,
                counterparty TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                signed_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS org_functions (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                area TEXT NOT NULL,
                function_name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'function',
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS org_assignments (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                subject_name TEXT NOT NULL,
                subject_type TEXT NOT NULL DEFAULT 'Persona fisica',
                function_name TEXT NOT NULL,
                area TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '',
                start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT NOT NULL DEFAULT '',
                linked_document_title TEXT NOT NULL DEFAULT '',
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'Attivo',
                notes TEXT NOT NULL DEFAULT '',
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS shareholder_documents (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                shareholder_id INTEGER NOT NULL REFERENCES shareholders(id) ON DELETE CASCADE,
                document_type TEXT NOT NULL,
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                issued_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                name TEXT NOT NULL,
                service_area TEXT NOT NULL DEFAULT '',
                owner_role TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Attivo',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS supplier_contracts (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
                contract_type TEXT NOT NULL,
                title TEXT NOT NULL,
                counterparty TEXT NOT NULL DEFAULT '',
                value REAL NOT NULL DEFAULT 0,
                start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT NOT NULL DEFAULT '',
                renewal_notice TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Attivo',
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS board_meetings (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                title TEXT NOT NULL,
                meeting_date TEXT NOT NULL,
                meeting_link TEXT NOT NULL DEFAULT '',
                agenda TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Pianificata',
                minutes_document_id INTEGER REFERENCES documents(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS communication_outputs (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                workflow_id TEXT NOT NULL,
                period TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Bozza generata',
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                reviewer TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '',
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS finance_costs (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Altro costo',
                amount REAL NOT NULL DEFAULT 0,
                periodicity TEXT NOT NULL DEFAULT 'Annuale',
                due_date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Attivo',
                source TEXT NOT NULL DEFAULT 'Manuale',
                notes TEXT NOT NULL DEFAULT '',
                linked_contract_id INTEGER REFERENCES supplier_contracts(id) ON DELETE SET NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS campaign_updates (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
                as_of_date TEXT NOT NULL,
                raised_amount REAL NOT NULL DEFAULT 0,
                investors_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'Rilevazione',
                source TEXT NOT NULL DEFAULT 'Manuale',
                notes TEXT NOT NULL DEFAULT '',
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS investors (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                investor_type TEXT NOT NULL,
                total_invested REAL NOT NULL DEFAULT 0,
                onboarding_status TEXT NOT NULL DEFAULT 'Da completare',
                entry_test_status TEXT NOT NULL DEFAULT 'Da completare',
                loss_simulation_status TEXT NOT NULL DEFAULT 'Da completare',
                threshold_status TEXT NOT NULL DEFAULT 'Da verificare',
                reflection_status TEXT NOT NULL DEFAULT 'Non applicabile',
                crm_status TEXT NOT NULL DEFAULT 'Attivo',
                preferred_categories TEXT NOT NULL DEFAULT '',
                risk_profile TEXT NOT NULL DEFAULT 'Da profilare',
                preferred_ticket_min REAL NOT NULL DEFAULT 0,
                preferred_ticket_max REAL NOT NULL DEFAULT 0,
                preferred_channel TEXT NOT NULL DEFAULT 'Email',
                recurrence_status TEXT NOT NULL DEFAULT 'Da valutare',
                source_system TEXT NOT NULL DEFAULT 'Manuale',
                external_investor_id TEXT NOT NULL DEFAULT '',
                last_synced_at TEXT NOT NULL DEFAULT '',
                manual_override_notes TEXT NOT NULL DEFAULT '',
                crm_notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS investments (
                id INTEGER PRIMARY KEY,
                investor_id INTEGER NOT NULL REFERENCES investors(id) ON DELETE CASCADE,
                deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
                amount REAL NOT NULL DEFAULT 0,
                invested_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Confermato'
            );

            CREATE TABLE IF NOT EXISTS conflicts (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                subject TEXT NOT NULL,
                related_party TEXT NOT NULL DEFAULT '',
                deal_id INTEGER REFERENCES deals(id) ON DELETE SET NULL,
                description TEXT NOT NULL DEFAULT '',
                mitigation TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Aperto',
                opened_at TEXT NOT NULL,
                closed_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                received_at TEXT NOT NULL,
                complainant TEXT NOT NULL,
                channel TEXT NOT NULL,
                object TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Aperto',
                outcome TEXT NOT NULL DEFAULT '',
                owner_user_id INTEGER REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                actor_id INTEGER REFERENCES users(id),
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            -- === Istruttoria Pariter ===
            CREATE TABLE IF NOT EXISTS practices (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                proponent_id INTEGER REFERENCES proponents(id) ON DELETE SET NULL,
                deal_id INTEGER REFERENCES deals(id) ON DELETE SET NULL,
                project_title TEXT NOT NULL,
                proponent_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'dossier_ricevuto',
                instrument TEXT NOT NULL DEFAULT '',
                target_amount REAL NOT NULL DEFAULT 0,
                max_amount REAL NOT NULL DEFAULT 0,
                pre_money REAL NOT NULL DEFAULT 0,
                equity_percent TEXT NOT NULL DEFAULT '',
                source_system TEXT NOT NULL DEFAULT 'Import file',
                external_ref TEXT NOT NULL DEFAULT '',
                dossier_json TEXT NOT NULL DEFAULT '',
                kiis_state TEXT NOT NULL DEFAULT '',
                internal_owner TEXT NOT NULL DEFAULT '',
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS practice_status_history (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                from_status TEXT NOT NULL DEFAULT '',
                to_status TEXT NOT NULL,
                actor_id INTEGER REFERENCES users(id),
                notes TEXT NOT NULL DEFAULT '',
                conditions TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS practice_documents (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                phase TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL,
                required INTEGER NOT NULL DEFAULT 1,
                doc_status TEXT NOT NULL DEFAULT 'mancante',
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                reviewer_notes TEXT NOT NULL DEFAULT '',
                integration_requested INTEGER NOT NULL DEFAULT 0,
                updated_by INTEGER REFERENCES users(id),
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS practice_onorabilita (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                role TEXT NOT NULL,                 -- 'lr' | 'te' | 'both'
                subject_name TEXT NOT NULL DEFAULT '',
                autodich INTEGER NOT NULL DEFAULT 0,
                casellario INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS internal_reviews (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                review_type TEXT NOT NULL,
                review_status TEXT NOT NULL DEFAULT 'non_generata',
                outcome TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                generated_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                updated_by INTEGER REFERENCES users(id),
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS cvoi_reports (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                weighted_score REAL NOT NULL DEFAULT 0,
                outcome TEXT NOT NULL DEFAULT 'da_integrare',
                conditions TEXT NOT NULL DEFAULT '',
                review_status TEXT NOT NULL DEFAULT 'bozza',
                generated_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cvoi_scores (
                id INTEGER PRIMARY KEY,
                cvoi_report_id INTEGER NOT NULL REFERENCES cvoi_reports(id) ON DELETE CASCADE,
                area_key TEXT NOT NULL,
                weight REAL NOT NULL,
                max_score REAL NOT NULL,
                threshold REAL NOT NULL,
                raw_score REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS practice_board_decisions (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                decision_round INTEGER NOT NULL,
                meeting_date TEXT NOT NULL DEFAULT '',
                attendees TEXT NOT NULL DEFAULT '',
                agenda TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                conditions TEXT NOT NULL DEFAULT '',
                generated_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS advisory_opinions (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                meeting_date TEXT NOT NULL DEFAULT '',
                attendees TEXT NOT NULL DEFAULT '',
                agenda TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                conditions TEXT NOT NULL DEFAULT '',
                generated_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pre_golive_conditions (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                description TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                owner TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'bloccante',
                due_date TEXT NOT NULL DEFAULT '',
                cond_status TEXT NOT NULL DEFAULT 'aperta',
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS integration_requests (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                practice_document_id INTEGER REFERENCES practice_documents(id) ON DELETE SET NULL,
                phase TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'non_bloccante',
                req_status TEXT NOT NULL DEFAULT 'aperta',
                requested_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                resolved_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS practice_alerts (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                severity TEXT NOT NULL DEFAULT 'bloccante',
                source TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                alert_status TEXT NOT NULL DEFAULT 'aperto',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS campaign_page_reviews (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                review_status TEXT NOT NULL DEFAULT 'bozza',
                coherence_notes TEXT NOT NULL DEFAULT '',
                no_yield_promise INTEGER NOT NULL DEFAULT 0,
                generated_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                updated_by INTEGER REFERENCES users(id),
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS cvoi_criteria_scores (
                id INTEGER PRIMARY KEY,
                cvoi_report_id INTEGER NOT NULL REFERENCES cvoi_reports(id) ON DELETE CASCADE,
                area_key TEXT NOT NULL,
                idx INTEGER NOT NULL,
                raw_score REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS cvoi_member_reviews (
                id INTEGER PRIMARY KEY,
                cvoi_report_id INTEGER NOT NULL REFERENCES cvoi_reports(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id),
                member_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'in_attesa',
                comment TEXT NOT NULL DEFAULT '',
                signed_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS cvoi_edit_log (
                id INTEGER PRIMARY KEY,
                cvoi_report_id INTEGER NOT NULL REFERENCES cvoi_reports(id) ON DELETE CASCADE,
                actor_user_id INTEGER REFERENCES users(id),
                actor_name TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS practice_phases (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                phase TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'da_completare',
                updated_at TEXT NOT NULL DEFAULT '',
                updated_by INTEGER REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS board_member_votes (
                id INTEGER PRIMARY KEY,
                board_decision_id INTEGER NOT NULL REFERENCES practice_board_decisions(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id),
                member_name TEXT NOT NULL DEFAULT '',
                vote TEXT NOT NULL DEFAULT 'in_attesa',
                comment TEXT NOT NULL DEFAULT '',
                voted_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS advisory_member_reviews (
                id INTEGER PRIMARY KEY,
                advisory_opinion_id INTEGER NOT NULL REFERENCES advisory_opinions(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id),
                member_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'in_attesa',
                comment TEXT NOT NULL DEFAULT '',
                signed_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS practice_process_steps (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                step_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'da_fare',
                note TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                updated_by INTEGER REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS practice_emails (
                id INTEGER PRIMARY KEY,
                practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
                step_key TEXT NOT NULL DEFAULT '',
                code TEXT NOT NULL DEFAULT '',
                recipient TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL DEFAULT '',
                sent_at TEXT NOT NULL DEFAULT '',
                sent_by INTEGER REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS team_people (
                id INTEGER PRIMARY KEY,
                platform_id INTEGER NOT NULL REFERENCES platforms(id),
                user_id INTEGER REFERENCES users(id),
                name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                cv_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                id_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                firma_path TEXT NOT NULL DEFAULT '',
                firma_kind TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                notes TEXT NOT NULL DEFAULT ''
            );
            """
        )
        ensure_column(conn, "finance_costs", "linked_contract_id", "INTEGER REFERENCES supplier_contracts(id) ON DELETE SET NULL")
        ensure_column(conn, "deals", "platform_fee_percent", "REAL NOT NULL DEFAULT 5")
        ensure_column(conn, "documents", "practice_id", "INTEGER REFERENCES practices(id) ON DELETE SET NULL")
        ensure_column(conn, "documents", "doc_date", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "documents", "description", "TEXT NOT NULL DEFAULT ''")
        # Completezza documentale Fase 2 (Allegato 5_1): esercizi chiusi/depositati (per la
        # regola bilanci) e numero di bilanci presenti. esercizi_chiusi NULL = non indicato.
        ensure_column(conn, "practices", "esercizi_chiusi", "INTEGER")
        ensure_column(conn, "practices", "bilanci_presenti", "INTEGER NOT NULL DEFAULT 0")
        # I documenti "se disponibili" non sono piu' obbligatori generici (regola Allegato 5_1).
        conn.execute("UPDATE practice_documents SET required = 0 WHERE phase = 'fase1' AND label IN "
                     "('Piano finanziario storico + proiezioni a 3 anni', 'Ultimi due bilanci depositati')")
        # Iterazione 2: CVOI collaborativo + chiusura pratica
        ensure_column(conn, "cvoi_reports", "workflow_status", "TEXT NOT NULL DEFAULT 'bozza'")
        ensure_column(conn, "cvoi_reports", "notes_qualitative", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "cvoi_reports", "closing_note", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "cvoi_reports", "data_caricamento", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "cvoi_reports", "data_valutazione", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "cvoi_reports", "mail", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "cvoi_reports", "drafter_user_id", "INTEGER")
        ensure_column(conn, "practices", "closed_at", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "practices", "closure_outcome", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "practices", "closure_note", "TEXT NOT NULL DEFAULT ''")
        # Iterazione 3: voto collegiale CdA + advisory collaborativo
        ensure_column(conn, "practice_board_decisions", "decision_status", "TEXT NOT NULL DEFAULT 'finalizzata'")
        ensure_column(conn, "practice_board_decisions", "finalized_by", "INTEGER")
        ensure_column(conn, "practice_board_decisions", "finalized_at", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "advisory_opinions", "workflow_status", "TEXT NOT NULL DEFAULT 'bozza'")
        ensure_column(conn, "advisory_opinions", "drafter_user_id", "INTEGER")
        # Processo: numero pratica interno + firma relazioni interne
        ensure_column(conn, "practices", "pratica_no", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "documents", "is_pdf", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "documents", "archived", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "internal_reviews", "signed_by", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "internal_reviews", "signed_at", "TEXT NOT NULL DEFAULT ''")
        # Convocazioni CdA collegate alla pratica (compaiono in Governance)
        ensure_column(conn, "board_meetings", "practice_id", "INTEGER REFERENCES practices(id) ON DELETE SET NULL")
        # Bozza KIIS collegata alla pratica (generata da template o caricata)
        ensure_column(conn, "practices", "kiis_document_id", "INTEGER REFERENCES documents(id) ON DELETE SET NULL")
        # Fase 3 (CVOI) - stati di merito: bozza KIIS, conflitti, coerenza KIIS, trasmissione Advisory
        ensure_column(conn, "practices", "m_kiis_stato", "TEXT NOT NULL DEFAULT 'incompleta'")  # incompleta/completa
        ensure_column(conn, "practices", "m_conflitti", "TEXT NOT NULL DEFAULT ''")  # nessuno/gestibile/non_gestibile
        ensure_column(conn, "practices", "m_conflitti_misura", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "practices", "m_kiis_coerenza", "TEXT NOT NULL DEFAULT ''")  # coerente/da_sanare/incoerente
        ensure_column(conn, "practices", "m_advisory_trasmesso", "TEXT NOT NULL DEFAULT ''")
        # CVOI collegiale: punteggi individuali per valutatore + astensioni motivate (per progetto)
        conn.execute("""CREATE TABLE IF NOT EXISTS cvoi_eval_scores (
            id INTEGER PRIMARY KEY,
            practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
            evaluator_id INTEGER NOT NULL,
            area_key TEXT NOT NULL, idx INTEGER NOT NULL, score REAL NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS cvoi_eval_status (
            id INTEGER PRIMARY KEY,
            practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
            evaluator_id INTEGER NOT NULL,
            abstained INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '', confirmed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT '')""")
        ensure_column(conn, "cvoi_eval_status", "confirmed", "INTEGER NOT NULL DEFAULT 0")
        # Firme del fascicolo M7 (una per membro del Comitato Tecnico)
        conn.execute("""CREATE TABLE IF NOT EXISTS m7_signatures (
            id INTEGER PRIMARY KEY,
            practice_id INTEGER NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
            member_id INTEGER NOT NULL, signed_at TEXT NOT NULL DEFAULT '')""")
        # Registri conflitti/reclami secondo modello M12 (Allegati 14 e 16)
        for col in ("reg_no", "soggetti", "natura_fonte", "rilevato_da", "valutazione", "misura", "esito", "atti_collegati"):
            ensure_column(conn, "conflicts", col, "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "complaints", "protocollo", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "complaints", "classificazione", "TEXT NOT NULL DEFAULT 'Reclamo'")
        ensure_column(conn, "complaints", "motivi_danno", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "complaints", "ricevibilita_date", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "complaints", "riscontro_date", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "complaints", "misure", "TEXT NOT NULL DEFAULT ''")
        ensure_column(conn, "complaints", "esborso", "TEXT NOT NULL DEFAULT 'No'")
        ensure_column(conn, "complaints", "esborso_note", "TEXT NOT NULL DEFAULT ''")
        if conn.execute("SELECT COUNT(*) FROM platforms").fetchone()[0] == 0:
            seed(conn)
        ensure_demo_extensions(conn)
        ensure_pariter_real_governance(conn)
        ensure_practice_flow_v2(conn)
        backfill_practice_seed_docs(conn)


def backfill_practice_seed_docs(conn):
    """Riallinea i documenti delle pratiche al seed: rimuove gli orfani (senza file),
    corregge fase/categoria/obbligo dei presenti, aggiunge i mancanti."""
    seed = {label: (phase, category, required)
            for phase, category, label, required in PRACTICE_DOC_SEED}
    practices = conn.execute("SELECT id FROM practices").fetchall()
    for pr in practices:
        docs = conn.execute(
            "SELECT id, label, phase, category, required, document_id FROM practice_documents WHERE practice_id = ?",
            (pr["id"],)).fetchall()
        have = set()
        for d in docs:
            if d["label"] not in seed:
                # documento non previsto dal seed: rimuovilo solo se non c'e' un file caricato
                if not d["document_id"]:
                    conn.execute("DELETE FROM practice_documents WHERE id = ?", (d["id"],))
                else:
                    have.add(d["label"])
                continue
            have.add(d["label"])
            phase, category, required = seed[d["label"]]
            if d["phase"] != phase or d["category"] != category or d["required"] != required:
                conn.execute(
                    "UPDATE practice_documents SET phase = ?, category = ?, required = ? WHERE id = ?",
                    (phase, category, required, d["id"]))
        for label, (phase, category, required) in seed.items():
            if label not in have:
                conn.execute(
                    """INSERT INTO practice_documents(practice_id, phase, category, label, required, doc_status, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'mancante', ?)""",
                    (pr["id"], phase, category, label, required, now_iso()))
    conn.commit()


def ensure_pariter_real_governance(conn):
    """Allinea l'organigramma Pariter ai dati reali estratti dall'archivio
    (CdA, Advisory Committee, organo di controllo, servizi in outsourcing).
    Idempotente: la sostituzione degli organi avviene solo se i nomi reali non
    sono ancora presenti; fornitori e nodi custom si inseriscono se mancanti."""
    # Enforce dei tre organi a OGNI avvio (idempotente): aggiorna in place i membri
    # esistenti (preserva gli id referenziati da committee_opinions ecc.), inserisce
    # gli extra e DISATTIVA i nominativi demo non piu' previsti (es. Paolo Conti,
    # Nadia Galli, che ensure_demo_extensions tenderebbe a re-inserire).
    def set_committee(committee, targets):
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM committee_members WHERE platform_id = 1 AND committee = ? ORDER BY id",
            (committee,),
        )]
        for i, (name, role, email) in enumerate(targets):
            if i < len(ids):
                conn.execute(
                    "UPDATE committee_members SET name = ?, role = ?, email = ?, active = 1 WHERE id = ?",
                    (name, role, email, ids[i]),
                )
            else:
                conn.execute(
                    "INSERT INTO committee_members(platform_id, committee, name, role, email, active) VALUES (1, ?, ?, ?, ?, 1)",
                    (committee, name, role, email),
                )
        for j in range(len(targets), len(ids)):
            conn.execute("UPDATE committee_members SET active = 0 WHERE id = ?", (ids[j],))

    set_committee("CdA", [
        ("Gaetano De Vito", "Presidente e legale rappresentante", "gaetano.devito@example.test"),
        ("Stefania Monotoni", "Consigliere - Responsabile controllo interno", "stefania.monotoni@example.test"),
        ("Fabio Malerba", "Consigliere", "fabio.malerba@example.test"),
    ])
    set_committee("Advisory Committee", [
        ("Rubina Galeotti", "Membro Advisory Committee (dal 12/11/2025)", "rubina.galeotti@example.test"),
        ("Gioacchino Attanzio", "Membro Advisory Committee (dal 12/11/2025)", "gioacchino.attanzio@example.test"),
    ])
    # Team di valutazione (CVOI / Comitato Tecnico): da ricomporre (3 figure, almeno una
    # indipendente dal gruppo). Nessun membro confermato: casella "da censire".
    set_committee("Comitato Tecnico", [])

    # Fornitori reali per i servizi critici in outsourcing. Le diciture contengono
    # le parole-chiave usate dagli slot dell'organigramma (istituto di pagamento,
    # sviluppo piattaforma). Upsert auto-correttivo su service_area/notes.
    for name, area, owner_role, notes in [
        ("Banca Sella S.p.A.", "Istituto di pagamento e custodia (PSD2)", "board",
         "Esternalizzazione pagamento, incassi e custodia somme ex art. 5 ECSP."),
        ("Code Factory S.r.l.", "Sviluppo software piattaforma", "operator",
         "Sviluppo e manutenzione della piattaforma (gruppo G2R); monitoraggio in capo a Stefania Monotoni."),
        ("Amazon Web Services (AWS)", "Cloud e hosting infrastruttura piattaforma", "operator",
         "Esternalizzazione cloud, hosting e infrastruttura della piattaforma."),
        ("Gruppo 2DueRighe (G2R)", "Marketing e comunicazioni", "operator",
         "Marketing e comunicazioni infragruppo; incarico a condizioni di mercato (parti correlate)."),
    ]:
        existing_id = conn.execute(
            "SELECT id FROM suppliers WHERE platform_id = 1 AND name = ?", (name,)
        ).fetchone()
        if existing_id:
            conn.execute(
                "UPDATE suppliers SET service_area = ?, owner_role = ?, status = 'Attivo', notes = ? WHERE id = ?",
                (area, owner_role, notes, existing_id[0]),
            )
        else:
            conn.execute(
                "INSERT INTO suppliers(platform_id, name, service_area, owner_role, status, notes, created_at) "
                "VALUES (1, ?, ?, ?, 'Attivo', ?, ?)",
                (name, area, owner_role, notes, now_iso()),
            )

    # Fornitori demo non riconosciuti. AstraLex subentra a "Studio Legale Verdi"
    # (segue contabilita e compliance). CloudSign e KYC Data Provider rimossi: gli
    # slot "Firma e conservazione" e "KYC/AML data provider" tornano "da censire"
    # (i contratti collegati cadono in cascata, i costi finance vanno a NULL).
    verdi = conn.execute(
        "SELECT id FROM suppliers WHERE platform_id = 1 AND name IN ('Studio Legale Verdi', 'AstraLex')"
    ).fetchone()
    if verdi:
        conn.execute(
            "UPDATE suppliers SET name = 'AstraLex STA', service_area = 'Contabilita e compliance', "
            "owner_role = 'compliance', notes = 'Studio (STA) che segue contabilita e compliance.' WHERE id = ?",
            (verdi[0],),
        )
        conn.execute(
            "UPDATE supplier_contracts SET title = 'Incarico contabilita e compliance', contract_type = 'Lettera incarico' "
            "WHERE supplier_id = ?", (verdi[0],),
        )
    for demo_supplier in ("CloudSign S.r.l.", "KYC Data Provider S.p.A."):
        row_ = conn.execute("SELECT id FROM suppliers WHERE platform_id = 1 AND name = ?", (demo_supplier,)).fetchone()
        if row_:
            conn.execute("DELETE FROM suppliers WHERE id = ?", (row_[0],))
    # Accordi persona demo (Elena Martini, Giulia Ferri, Paolo Conti, Nadia Galli...).
    conn.execute(
        "DELETE FROM person_agreements WHERE platform_id = 1 AND person_name IN "
        "('Elena Martini', 'Luca Serra', 'Giulia Ferri', 'Roberto Neri', 'Paolo Conti', 'Nadia Galli')"
    )

    # Partecipogramma reale di Pariter Equity S.r.l. (cap table, Knowledge Base sez. 3).
    sh_targets = [
        ("Gruppo 2DueRighe S.r.l.", 62.0,
         "Controllata da Ammigest al 51%; presidia anche il 19% di Pariter che passa per Power Money."),
        ("Power Money S.r.l.", 19.0,
         "Soci: Gruppo 2DueRighe 51%, Marcello Aloisi 49%."),
        ("Pariter Partners S.r.l.", 19.0,
         "Socio qualificato. Presidente storico Jari Ognibeni."),
    ]
    sh_ids = [r[0] for r in conn.execute(
        "SELECT id FROM shareholders WHERE platform_id = 1 ORDER BY id"
    )]
    for i, (name, stake, notes) in enumerate(sh_targets):
        if i < len(sh_ids):
            conn.execute(
                "UPDATE shareholders SET name = ?, stake_percent = ?, notes = ? WHERE id = ?",
                (name, stake, notes, sh_ids[i]),
            )
        else:
            conn.execute(
                "INSERT INTO shareholders(platform_id, name, stake_percent, notes) VALUES (1, ?, ?, ?)",
                (name, stake, notes),
            )
    for j in range(len(sh_targets), len(sh_ids)):
        conn.execute("DELETE FROM shareholders WHERE id = ? AND id NOT IN (SELECT shareholder_id FROM shareholder_documents)", (sh_ids[j],))

    # Pulizia una-tantum dei vecchi nodi custom con etichette superate.
    conn.execute("DELETE FROM org_assignments WHERE platform_id = 1 AND function_name = 'Organo di controllo (sindaco)'")
    conn.execute("DELETE FROM org_assignments WHERE platform_id = 1 AND function_name = 'Responsabile IT' AND area = 'Area operativa'")
    conn.execute("DELETE FROM org_functions WHERE platform_id = 1 AND (function_name = 'Organo di controllo (sindaco)' OR (function_name = 'Responsabile IT' AND area = 'Area operativa'))")

    # Pre-assegnazione dei titolari NOTI alle funzioni del funzionigramma (org_assignments).
    # Le funzioni senza titolare certo restano "da assegnare". Idempotente: salta se esiste
    # gia' un'assegnazione (qualsiasi stato) per la coppia, cosi' un'archiviazione fatta
    # dall'utente non viene annullata. Gli altri soggetti si collegano dalle anagrafiche.
    funz_assign = [
        ("Gaetano De Vito", "Gestione e approvazione delle offerte", "Governance", "Presidente", "2025-07-24"),
        ("Stefania Monotoni", "Gestione e approvazione delle offerte", "Governance", "Consigliere", "2025-09-18"),
        ("Fabio Malerba", "Gestione e approvazione delle offerte", "Governance", "Consigliere", "2024-06-05"),
        ("Gaetano De Vito", "Rappresentanza legale e rapporti con la vigilanza", "Governance", "Presidente e legale rappresentante", "2025-07-24"),
        ("Roberto Rizzuto", "Organo di controllo", "Governance", "Sindaco unico", "2023-12-22"),
        ("Fabio Gallassi", "Revisione legale dei conti", "Governance", "Revisore legale (proposto)", "2026-06-01"),
        ("Veronika Udod", "Responsabile IT", "Funzioni responsabili", "Responsabile IT (dipendente G2R)", "2026-02-09"),
        ("Gaetano De Vito", "Continuita operativa", "Funzioni responsabili", "Referente continuita operativa", "2025-07-24"),
        ("Stefania Monotoni", "Controllo di 2 livello (conformita e rischi)", "Area di controllo", "Responsabile delle funzioni di controllo", "2025-09-18"),
        ("Stefania Monotoni", "Antiriciclaggio e adeguata verifica (art. 5)", "Area di controllo", "Responsabile dei controlli", "2025-09-18"),
        ("Stefania Monotoni", "Conflitti di interesse", "Area di controllo", "Valutazione finale dei conflitti", "2025-09-18"),
        ("Stefania Monotoni", "Monitoraggio dei fornitori esternalizzati", "Area di controllo", "Consigliere incaricato dei controlli", "2025-09-18"),
        ("Rubina Galeotti", "Parere indipendente sulle offerte", "Advisory Committee", "Membro Advisory Committee", "2025-11-12"),
        ("Gioacchino Attanzio", "Parere indipendente sulle offerte", "Advisory Committee", "Membro Advisory Committee", "2025-11-12"),
        ("Gaetano De Vito", "Presidi prudenziali / fondi propri", "Funzioni responsabili", "Monitoraggio dei fondi propri", "2024-06-05"),
        ("Stefania Monotoni", "Presidi prudenziali / fondi propri", "Funzioni responsabili", "Monitoraggio dei fondi propri", "2025-09-18"),
        ("Fabio Malerba", "Presidi prudenziali / fondi propri", "Funzioni responsabili", "Monitoraggio dei fondi propri", "2024-06-05"),
    ]
    for subj, func, area, role, start in funz_assign:
        if not conn.execute(
            "SELECT 1 FROM org_assignments WHERE platform_id = 1 AND subject_name = ? AND function_name = ?",
            (subj, func),
        ).fetchone():
            conn.execute(
                "INSERT INTO org_assignments(platform_id, subject_name, subject_type, function_name, area, role, start_date, status, notes, created_by, created_at, updated_at) "
                "VALUES (1, ?, 'Persona', ?, ?, ?, ?, 'Attivo', '', 1, ?, ?)",
                (subj, func, area, role, start, now_iso(), now_iso()),
            )

    # Presidi prudenziali: i nomi dei consiglieri (non la dicitura "Consiglio").
    conn.execute("DELETE FROM org_assignments WHERE platform_id = 1 AND subject_name = 'Consiglio di Amministrazione'")

    conn.commit()


def ensure_practice_flow_v2(conn):
    """Migra i dati esistenti al flusso a delibera CdA unica (Advisory PRIMA del CdA):
    rimappa i vecchi stati a due delibere e rende coerenti le pratiche gia' aperte.
    Idempotente."""
    status_map = {
        "attesa_cda1": "attesa_cda", "attesa_cda2": "attesa_cda",
        "cda1_positiva": "cda_positiva", "cda2_positiva": "cda_positiva",
        "cda1_positiva_condizioni": "cda_positiva_condizioni",
        "cda2_positiva_condizioni": "cda_positiva_condizioni",
        "cda1_negativa": "cda_negativa", "cda2_negativa": "cda_negativa",
    }
    for old, new in status_map.items():
        conn.execute("UPDATE practices SET status = ? WHERE status = ?", (new, old))
        conn.execute("UPDATE practice_status_history SET to_status = ? WHERE to_status = ?", (new, old))
        conn.execute("UPDATE practice_status_history SET from_status = ? WHERE from_status = ?", (new, old))
    # Le delibere collegiali ora usano un unico round.
    conn.execute("UPDATE practice_board_decisions SET decision_round = 1 WHERE decision_round = 2")
    # Pratiche arrivate alla delibera senza parere Advisory registrato: nel nuovo
    # flusso l'Advisory precede il CdA, quindi si seedea un parere unanime coerente.
    pending = conn.execute(
        "SELECT id, created_by FROM practices WHERE status IN ('attesa_cda', 'cda_positiva', 'cda_positiva_condizioni') "
        "AND id NOT IN (SELECT practice_id FROM advisory_opinions)"
    ).fetchall()
    for pr in pending:
        conn.execute(
            """INSERT INTO advisory_opinions(practice_id, meeting_date, attendees, agenda, summary, outcome,
                workflow_status, created_by, created_at)
               VALUES (?, '2026-01-21', 'Rubina Galeotti, Gioacchino Attanzio',
                'Esame fascicolo CVOI e profili di conflitto', 'Parere favorevole non vincolante al CdA.',
                'favorevole', 'unanime', ?, ?)""",
            (pr["id"], pr["created_by"] or 11, now_iso()),
        )
    conn.commit()


def seed(conn):
    now = now_iso()
    conn.executemany(
        "INSERT INTO platforms(id, code, name, regulator_profile) VALUES (?, ?, ?, ?)",
        [
            (1, "PARITER", "Pariter Equity", "ECSP - CONSOB / Banca d'Italia"),
            (2, "ISI", "ISI Crowd", "ECSP - CONSOB / Banca d'Italia"),
        ],
    )
    conn.executemany(
        "INSERT INTO users(id, name, email, role, active) VALUES (?, ?, ?, ?, 1)",
        [
            (1, "Alessia Ricci", "alessia.ricci@example.test", "compliance"),
            (2, "Marco Bianchi", "marco.bianchi@example.test", "legal"),
            (3, "Giulia Ferri", "giulia.ferri@example.test", "technical_committee"),
            (4, "Paolo Conti", "paolo.conti@example.test", "covi"),
            (5, "Elena Martini", "elena.martini@example.test", "board"),
            (6, "Sara De Luca", "sara.deluca@example.test", "operator"),
            (7, "Tommaso Pravettoni", "tommaso.pravettoni@example.test", "technical_committee"),
            (8, "Marta Provera", "marta.provera@example.test", "technical_committee"),
            (9, "Valentina Franchini", "valentina.franchini@example.test", "technical_committee"),
            (10, "Amministratore", "admin@example.test", "admin"),
            (11, "Gaetano De Vito", "gaetano.devito@example.test", "board"),
            (12, "Stefania Monotoni", "stefania.monotoni@example.test", "board"),
            (13, "Mauro Sacchetto", "mauro.sacchetto@example.test", "covi"),
            (14, "Luciano Rodighiero", "luciano.rodighiero@example.test", "covi"),
        ],
    )
    committee_rows = []
    member_id = 1
    for platform_id in (1, 2):
        committee_rows.extend(
            [
                (member_id, platform_id, "Comitato Tecnico", "Giulia Ferri", "Relatore tecnico", "giulia.ferri@example.test", 1),
                (member_id + 1, platform_id, "Comitato Tecnico", "Roberto Neri", "Membro", "roberto.neri@example.test", 1),
                (member_id + 2, platform_id, "Advisory Committee", "Paolo Conti", "Relatore Advisory Committee", "paolo.conti@example.test", 1),
                (member_id + 3, platform_id, "Advisory Committee", "Nadia Galli", "Membro", "nadia.galli@example.test", 1),
                (member_id + 4, platform_id, "CdA", "Elena Martini", "Presidente CdA", "elena.martini@example.test", 1),
                (member_id + 5, platform_id, "CdA", "Luca Serra", "Consigliere", "luca.serra@example.test", 1),
            ]
        )
        member_id += 6
    conn.executemany(
        """
        INSERT INTO committee_members(id, platform_id, committee, name, role, email, active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        committee_rows,
    )
    conn.executemany(
        "INSERT INTO shareholders(platform_id, name, stake_percent, notes) VALUES (?, ?, ?, ?)",
        [
            (1, "Gruppo 2DueRighe S.r.l.", 62.0, "Controllata da Ammigest al 51%; presidia anche il 19% via Power Money."),
            (1, "Power Money S.r.l.", 19.0, "Soci: Gruppo 2DueRighe 51%, Marcello Aloisi 49%."),
            (1, "Pariter Partners S.r.l.", 19.0, "Socio qualificato. Presidente storico Jari Ognibeni."),
            (2, "ISI Holding S.p.A.", 51.0, "Controllo diretto"),
            (2, "Club Investitori", 18.5, "Accordo quadro"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO proponents(
            id, platform_id, name, legal_form, tax_id, contact_email, beneficial_owners,
            exposure, internal_score, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 1, "GreenFab S.r.l.", "S.r.l.", "GFAB123456", "cfo@greenfab.example", "Laura Verde 62%; Marco Riva 18%", 320000, "A-", "PMI manifattura sostenibile", now),
            (2, 1, "MedTech Aurora S.p.A.", "S.p.A.", "MTA987654", "finance@aurora.example", "Aurora Holding 55%; fondatori 20%", 140000, "B+", "Follow-on potenziale", now),
            (3, 2, "FoodLoop Benefit S.r.l.", "S.r.l. benefit", "FLB456789", "admin@foodloop.example", "Elisa Mori 70%", 90000, "B", "Prima istruttoria su ISI Crowd", now),
        ],
    )
    conn.executemany(
        """
        INSERT INTO deals(
            id, platform_id, proponent_id, title, funding_target, phase, technical_reviewer_id,
            covi_reviewer_id, contract_required, kiis_state, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 1, 1, "GreenFab - linea robotizzata", 750000, "verifiche", 1, 3, 1, "Bozza", 1, now, now),
            (2, 1, 2, "MedTech Aurora - dispositivo diagnostico", 1200000, "comitato_tecnico", 1, 3, 1, "Bozza", 1, now, now),
            (3, 2, 3, "FoodLoop - piattaforma anti spreco", 450000, "istruttoria_documentazione", 7, 9, 0, "Bozza", 6, now, now),
        ],
    )
    for deal_id in (1, 2, 3):
        for category, label, required in REQUIREMENT_SEED:
            completed = 1 if deal_id in (1, 2) and label != "Bozza KIIS iniziale" else 0
            conn.execute(
                """
                INSERT INTO deal_requirements(deal_id, kind, category, label, required, completed, completed_at, completed_by)
                VALUES (?, 'onboarding', ?, ?, ?, ?, ?, ?)
                """,
                (deal_id, category, label, required, completed, now if completed else "", 1 if completed else None),
            )
        for area in VERIFICATION_SEED:
            status = "ok" if deal_id == 2 else "pending"
            conn.execute(
                """
                INSERT INTO verifications(deal_id, area, status, result, owner_user_id, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (deal_id, area, status, "Verifica completata in seed demo." if status == "ok" else "", 1, now if status == "ok" else ""),
            )
    due_today = date.today()
    conn.executemany(
        """
        INSERT INTO compliance_tasks(platform_id, area, title, due_date, status, owner_role)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "Reclami", "Aggiornare registro reclami Q2", (due_today - timedelta(days=3)).isoformat(), "Scaduto", "compliance"),
            (1, "Autorita", "Comunicazione periodica CONSOB", (due_today + timedelta(days=12)).isoformat(), "Imminente", "compliance"),
            (1, "Governance", "Verifica composizione Advisory Committee", (due_today + timedelta(days=28)).isoformat(), "Pianificato", "legal"),
            (2, "Autorita", "Flusso vigilanza trimestrale", (due_today + timedelta(days=9)).isoformat(), "Imminente", "compliance"),
            (2, "Conflitti", "Riconciliazione registro conflitti", (due_today + timedelta(days=20)).isoformat(), "Pianificato", "legal"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO platform_metrics(platform_id, published_offers, active_offers, investors, raised_amount, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 18, 3, 4210, 12840000, "adapter:future-platform-api", now),
            (2, 7, 1, 1160, 3160000, "adapter:future-platform-api", now),
        ],
    )
    seed_doc = UPLOAD_DIR / "generated" / "seed-visura-greenfab.txt"
    seed_doc.write_text("Documento demo: visura camerale GreenFab.\n", encoding="utf-8")
    conn.execute(
        """
        INSERT INTO documents(platform_id, deal_id, proponent_id, origin, category, title, filename, storage_path, generated, created_by, created_at)
        VALUES (1, 1, 1, 'Deal', 'Documentazione', 'Visura camerale GreenFab', 'seed-visura-greenfab.txt', ?, 0, 1, ?)
        """,
        (str(seed_doc.relative_to(BASE_DIR)), now),
    )
    conn.executemany(
        """
        INSERT INTO audit_log(platform_id, actor_id, entity_type, entity_id, action, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 1, "deal", 1, "Creazione deal", "Deal creato e portato in verifiche con dati demo.", now),
            (1, 1, "deal", 2, "Invio a Comitato Tecnico", "Verifiche completate, in attesa del parere tecnico.", now),
            (2, 6, "deal", 3, "Avvio istruttoria", "Raccolta documentale in corso.", now),
        ],
    )
    conn.commit()


def ensure_demo_extensions(conn):
    now = now_iso()
    today = date.today()
    investor_columns = [
        ("phone", "TEXT NOT NULL DEFAULT ''"),
        ("crm_status", "TEXT NOT NULL DEFAULT 'Attivo'"),
        ("preferred_categories", "TEXT NOT NULL DEFAULT ''"),
        ("risk_profile", "TEXT NOT NULL DEFAULT 'Da profilare'"),
        ("preferred_ticket_min", "REAL NOT NULL DEFAULT 0"),
        ("preferred_ticket_max", "REAL NOT NULL DEFAULT 0"),
        ("preferred_channel", "TEXT NOT NULL DEFAULT 'Email'"),
        ("recurrence_status", "TEXT NOT NULL DEFAULT 'Da valutare'"),
        ("source_system", "TEXT NOT NULL DEFAULT 'Manuale'"),
        ("external_investor_id", "TEXT NOT NULL DEFAULT ''"),
        ("last_synced_at", "TEXT NOT NULL DEFAULT ''"),
        ("manual_override_notes", "TEXT NOT NULL DEFAULT ''"),
        ("crm_notes", "TEXT NOT NULL DEFAULT ''"),
    ]
    for column, definition in investor_columns:
        ensure_column(conn, "investors", column, definition)
    proponent_columns = [
        ("phone", "TEXT NOT NULL DEFAULT ''"),
        ("website", "TEXT NOT NULL DEFAULT ''"),
        ("sector", "TEXT NOT NULL DEFAULT ''"),
        ("crm_status", "TEXT NOT NULL DEFAULT 'In istruttoria'"),
        ("onboarding_status", "TEXT NOT NULL DEFAULT 'Documenti da raccogliere'"),
        ("owner_name", "TEXT NOT NULL DEFAULT ''"),
        ("source_system", "TEXT NOT NULL DEFAULT 'Manuale'"),
        ("external_proponent_id", "TEXT NOT NULL DEFAULT ''"),
        ("last_synced_at", "TEXT NOT NULL DEFAULT ''"),
        ("manual_override_notes", "TEXT NOT NULL DEFAULT ''"),
        ("next_action", "TEXT NOT NULL DEFAULT ''"),
    ]
    for column, definition in proponent_columns:
        ensure_column(conn, "proponents", column, definition)
    shareholder_columns = [
        ("subject_type", "TEXT NOT NULL DEFAULT 'Societa / ente'"),
        ("legal_form", "TEXT NOT NULL DEFAULT ''"),
        ("tax_id", "TEXT NOT NULL DEFAULT ''"),
        ("contact_email", "TEXT NOT NULL DEFAULT ''"),
        ("phone", "TEXT NOT NULL DEFAULT ''"),
        ("address", "TEXT NOT NULL DEFAULT ''"),
        ("beneficial_owners", "TEXT NOT NULL DEFAULT ''"),
        ("requisites_status", "TEXT NOT NULL DEFAULT 'Da verificare'"),
        ("status", "TEXT NOT NULL DEFAULT 'Attivo'"),
    ]
    for column, definition in shareholder_columns:
        ensure_column(conn, "shareholders", column, definition)
    ensure_column(conn, "communication_outputs", "payload_json", "TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE committee_members SET committee = 'Advisory Committee' WHERE committee = 'CoVi'")
    conn.execute(
        """
        UPDATE committee_members
        SET role = REPLACE(role, 'CoVi', 'Advisory Committee')
        WHERE role LIKE '%CoVi%'
        """
    )
    conn.execute(
        """
        UPDATE compliance_tasks
        SET title = REPLACE(title, 'CoVi', 'Advisory Committee')
        WHERE title LIKE '%CoVi%'
        """
    )
    conn.execute(
        """
        UPDATE person_agreements
        SET role = REPLACE(role, 'CoVi', 'Advisory Committee'),
            scope = REPLACE(scope, 'CoVi', 'Advisory Committee')
        WHERE role LIKE '%CoVi%' OR scope LIKE '%CoVi%'
        """
    )

    dummy_isi_names = ("Giulia Ferri", "Roberto Neri", "Paolo Conti", "Nadia Galli", "Elena Martini", "Luca Serra")
    conn.execute(
        """
        UPDATE deals
        SET technical_reviewer_id = NULL
        WHERE platform_id = 2
        AND technical_reviewer_id IN (
            SELECT id FROM committee_members
            WHERE platform_id = 2 AND name IN (?, ?, ?, ?, ?, ?)
        )
        """,
        dummy_isi_names,
    )
    conn.execute(
        """
        UPDATE deals
        SET covi_reviewer_id = NULL
        WHERE platform_id = 2
        AND covi_reviewer_id IN (
            SELECT id FROM committee_members
            WHERE platform_id = 2 AND name IN (?, ?, ?, ?, ?, ?)
        )
        """,
        dummy_isi_names,
    )
    conn.executemany(
        "DELETE FROM committee_members WHERE platform_id = 2 AND name = ?",
        [(name,) for name in dummy_isi_names],
    )

    def ensure_committee_member(platform_id, committee, name, role, email):
        exists = conn.execute(
            """
            SELECT id FROM committee_members
            WHERE platform_id = ? AND committee = ? AND name = ?
            """,
            (platform_id, committee, name),
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO committee_members(platform_id, committee, name, role, email, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (platform_id, committee, name, role, email),
            )

    def ensure_supplier(platform_id, name, service_area, owner_role, notes):
        existing = conn.execute(
            "SELECT id FROM suppliers WHERE platform_id = ? AND name = ?",
            (platform_id, name),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            """
            INSERT INTO suppliers(platform_id, name, service_area, owner_role, status, notes, created_at)
            VALUES (?, ?, ?, ?, 'Attivo', ?, ?)
            """,
            (platform_id, name, service_area, owner_role, notes, now),
        )
        return cur.lastrowid

    def ensure_supplier_contract(platform_id, supplier_id, contract_type, title, counterparty, value, start_date, end_date, renewal_notice, created_by):
        exists = conn.execute(
            """
            SELECT id FROM supplier_contracts
            WHERE platform_id = ? AND supplier_id = ? AND title = ?
            """,
            (platform_id, supplier_id, title),
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO supplier_contracts(
                    platform_id, supplier_id, contract_type, title, counterparty, value,
                    start_date, end_date, renewal_notice, status, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Attivo', ?, ?)
                """,
                (platform_id, supplier_id, contract_type, title, counterparty, value, start_date, end_date, renewal_notice, created_by, now),
            )

    for committee, name, role, email in [
        ("CdA", "Antonio Ottaiano", "Presidente", "antonio.ottaiano@isi-crowd.example.test"),
        ("CdA", "Emma Venturelli", "Consigliere", "emma.venturelli@isi-crowd.example.test"),
        ("CdA", "Salvatore Sannino", "Consigliere", "salvatore.sannino@isi-crowd.example.test"),
        ("Comitato Tecnico", "Michele Russo", "Membro Comitato Tecnico", "michele.russo@isi-crowd.example.test"),
        ("Comitato Tecnico", "Gabriele Morelli", "Membro Comitato Tecnico", "gabriele.morelli@isi-crowd.example.test"),
        ("Comitato Tecnico", "Teresa Manzone", "Membro Comitato Tecnico", "teresa.manzone@isi-crowd.example.test"),
    ]:
        ensure_committee_member(2, committee, name, role, email)

    # (Advisory Committee di Pariter: roster reale gestito in ensure_pariter_real_governance.)
    isi_technical_reviewer = conn.execute(
        """
        SELECT id FROM committee_members
        WHERE platform_id = 2 AND committee = 'Comitato Tecnico' AND name = 'Michele Russo'
        """
    ).fetchone()
    if isi_technical_reviewer:
        conn.execute(
            """
            UPDATE deals
            SET technical_reviewer_id = ?
            WHERE platform_id = 2
            AND (
                technical_reviewer_id IS NULL
                OR technical_reviewer_id NOT IN (SELECT id FROM committee_members WHERE platform_id = 2)
            )
            """,
            (isi_technical_reviewer["id"],),
        )
    conn.execute(
        """
        UPDATE deals
        SET covi_reviewer_id = NULL
        WHERE platform_id = 2
        AND (
            covi_reviewer_id IS NULL
            OR covi_reviewer_id NOT IN (
                SELECT id FROM committee_members
                WHERE platform_id = 2 AND committee = 'Advisory Committee'
            )
        )
        """
    )

    if conn.execute("SELECT COUNT(*) FROM person_agreements").fetchone()[0] == 0:
        conn.executemany(
            """
            INSERT INTO person_agreements(platform_id, person_name, role, agreement_type, scope, status, signed_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "Elena Martini", "Presidente CdA", "Incarico amministratore", "Mandato CdA e deleghe operative", "Attivo", "2025-04-18", "2028-04-18"),
                (1, "Giulia Ferri", "Relatore tecnico", "NDA", "Accesso ai fascicoli deal e documentazione riservata", "Attivo", "2025-09-12", "2027-09-12"),
                (1, "Paolo Conti", "Relatore Advisory Committee", "Lettera incarico", "Funzione di vigilanza interna offerte", "Attivo", "2025-06-01", "2027-06-01"),
                (2, "Elena Martini", "Presidente CdA", "Incarico amministratore", "Mandato CdA ISI Crowd", "Attivo", "2025-05-09", "2028-05-09"),
                (2, "Nadia Galli", "Membro Advisory Committee", "NDA", "Accesso a verifiche e pareri Advisory Committee", "In rinnovo", "2024-07-01", "2026-07-01"),
            ],
        )
    if conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0] == 0:
        conn.executemany(
            """
            INSERT INTO suppliers(id, platform_id, name, service_area, owner_role, status, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 1, "CloudSign S.r.l.", "Firma elettronica e conservazione", "legal", "Attivo", "Fornitore documentale critico", now),
                (2, 1, "KYC Data Provider S.p.A.", "KYC / AML / verifiche", "compliance", "Attivo", "Provider dati per onboarding e controlli", now),
                (3, 1, "Studio Legale Verdi", "Consulenza legale ECSP", "legal", "Attivo", "Supporto contratti e governance", now),
                (4, 2, "ISI Cloud Operations", "Infrastruttura piattaforma", "operator", "Attivo", "Contratto operativo ISI Crowd", now),
            ],
        )
    if conn.execute("SELECT COUNT(*) FROM supplier_contracts").fetchone()[0] == 0:
        conn.executemany(
            """
            INSERT INTO supplier_contracts(
                platform_id, supplier_id, contract_type, title, counterparty, value,
                start_date, end_date, renewal_notice, status, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 1, "Contratto quadro", "Contratto conservazione e firma", "Pariter Equity", 18000, "2025-01-01", "2026-12-31", "Preavviso 90 giorni", "Attivo", 2, now),
                (1, 2, "Data processing agreement", "Servizi KYC e AML", "Pariter Equity", 24000, "2025-03-15", "2027-03-14", "Rinnovo annuale", "Attivo", 1, now),
                (1, 3, "Lettera incarico", "Consulenza continuativa ECSP", "Pariter Equity", 36000, "2025-05-01", "2026-04-30", "Revisione fee a scadenza", "In rinnovo", 2, now),
                (2, 4, "Outsourcing operativo", "Gestione infrastruttura ISI", "ISI Crowd", 42000, "2025-02-01", "2027-01-31", "Exit plan richiesto", "Attivo", 6, now),
            ],
        )
    for name, service_area, owner_role, notes, contract_type, title, value in [
        ("Keliweb S.r.l.", "Fornitore servizi cloud", "operator", "Cloud e hosting piattaforma ISI Crowd", "Outsourcing ICT", "Servizi cloud e hosting", 18000),
        ("Creditsafe Italia S.r.l.", "Merito creditizio", "compliance", "Provider dati per valutazione merito creditizio", "Servizio dati", "Verifiche merito creditizio", 12000),
        ("Lemonway Sas", "Istituto di pagamento", "operator", "Servizi di pagamento collegati alla piattaforma", "Payment services agreement", "Servizi di pagamento", 24000),
        ("012 Factory S.r.l.", "Contabilita", "operator", "Contabilita in outsourcing", "Outsourcing amministrativo", "Servizi contabilita", 10000),
        ("Avvocati.net", "Compliance esterna", "legal", "Supporto compliance esterna ECSP", "Lettera incarico", "Compliance esterna", 16000),
    ]:
        supplier_id = ensure_supplier(2, name, service_area, owner_role, notes)
        ensure_supplier_contract(2, supplier_id, contract_type, title, "ISI Crowd", value, "2025-01-01", "2027-12-31", "Rinnovo/exit plan da verificare", 1)
    if conn.execute("SELECT COUNT(*) FROM board_meetings").fetchone()[0] == 0:
        conn.executemany(
            """
            INSERT INTO board_meetings(platform_id, title, meeting_date, meeting_link, agenda, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "CdA Pariter - offerte in approvazione",
                    (today + timedelta(days=7)).isoformat(),
                    "https://meet.example/pariter-cda",
                    "1. MedTech Aurora; 2. Integrazioni KIIS; 3. Aggiornamento reclami",
                    "Convocata",
                    now,
                ),
                (
                    1,
                    "CdA Pariter - verbale maggio",
                    (today - timedelta(days=21)).isoformat(),
                    "",
                    "Delibere su pipeline Q2 e assetti comitati",
                    "Verbale archiviato",
                    now,
                ),
                (
                    2,
                    "CdA ISI Crowd - pipeline FoodLoop",
                    (today + timedelta(days=12)).isoformat(),
                    "https://meet.example/isi-cda",
                    "1. Stato istruttoria FoodLoop; 2. Registro conflitti",
                    "Pianificata",
                    now,
                ),
            ],
        )
    if conn.execute("SELECT COUNT(*) FROM investors").fetchone()[0] == 0:
        conn.executemany(
            """
            INSERT INTO investors(
                id, platform_id, name, email, investor_type, total_invested, onboarding_status,
                entry_test_status, loss_simulation_status, threshold_status, reflection_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, 1, "Anna Colombo", "anna.colombo@example.test", "Non sofisticato", 18000, "Completo", "Superato", "Completata", "Sotto soglia", "Non applicabile", now),
                (2, 1, "Club Beta Invest", "operations@clubbeta.example", "Sofisticato", 145000, "Completo", "Non richiesto", "Non richiesta", "Verificata", "Non applicabile", now),
                (3, 1, "Lorenzo Villa", "lorenzo.villa@example.test", "Non sofisticato", 52000, "In revisione", "Superato", "Completata", "Soglia superata", "In corso", now),
                (4, 2, "Maria Fontana", "maria.fontana@example.test", "Non sofisticato", 9000, "Da completare", "Da completare", "Da completare", "Da verificare", "Non applicabile", now),
                (5, 2, "Holding Gamma S.r.l.", "investimenti@gamma.example", "Sofisticato", 220000, "Completo", "Non richiesto", "Non richiesta", "Verificata", "Non applicabile", now),
            ],
        )
    demo_investor_profiles = [
        (1, 1, "+39 333 010 1101", "Industria sostenibile, energia, PMI produttive", "Bilanciato", 8000, 25000, "Email", "Ricorrente", "adapter:future-platform-api", "PAR-INV-0001", now, "", "Preferisce aggiornamenti sintetici e follow-up a raccolta avviata."),
        (2, 1, "+39 02 5550 2030", "MedTech, follow-on, club deal", "Professionale", 50000, 150000, "Email", "Ricorrente", "adapter:future-platform-api", "PAR-INV-0002", now, "Contatto operativo corretto manualmente dopo import API.", "Soggetto collettivo: verificare referente prima di sollecitazioni nominali."),
        (3, 1, "+39 347 020 4402", "MedTech, impatto sociale, tecnologia", "Dinamico", 15000, 60000, "Telefono", "Ricorrente", "adapter:future-platform-api", "PAR-INV-0003", now, "", "Interessato a deal innovativi ma da gestire con attenzione per soglia e riflessione."),
        (4, 2, "+39 349 888 1200", "Food, impact, sostenibilita", "Prudente", 2000, 12000, "Email", "Prospect", "adapter:future-platform-api", "ISI-INV-0001", now, "", "Onboarding da completare prima di qualunque campagna."),
        (5, 2, "+39 02 4440 9000", "FoodTech, lending, operazioni istituzionali", "Professionale", 50000, 250000, "PEC", "Ricorrente", "adapter:future-platform-api", "ISI-INV-0002", now, "", "Investitore istituzionale: canale formale e materiali completi."),
    ]
    for profile in demo_investor_profiles:
        (
            investor_id,
            platform_id,
            phone,
            preferred_categories,
            risk_profile,
            ticket_min,
            ticket_max,
            preferred_channel,
            recurrence_status,
            source_system,
            external_id,
            synced_at,
            override_notes,
            crm_notes,
        ) = profile
        conn.execute(
            """
            UPDATE investors
            SET phone = CASE WHEN phone = '' THEN ? ELSE phone END,
                preferred_categories = CASE WHEN preferred_categories = '' THEN ? ELSE preferred_categories END,
                risk_profile = CASE WHEN risk_profile = '' OR risk_profile = 'Da profilare' THEN ? ELSE risk_profile END,
                preferred_ticket_min = CASE WHEN preferred_ticket_min = 0 THEN ? ELSE preferred_ticket_min END,
                preferred_ticket_max = CASE WHEN preferred_ticket_max = 0 THEN ? ELSE preferred_ticket_max END,
                preferred_channel = CASE WHEN preferred_channel = '' OR preferred_channel = 'Email' THEN ? ELSE preferred_channel END,
                recurrence_status = CASE WHEN recurrence_status = '' OR recurrence_status = 'Da valutare' THEN ? ELSE recurrence_status END,
                source_system = CASE WHEN source_system = '' OR source_system = 'Manuale' THEN ? ELSE source_system END,
                external_investor_id = CASE WHEN external_investor_id = '' THEN ? ELSE external_investor_id END,
                last_synced_at = CASE WHEN last_synced_at = '' THEN ? ELSE last_synced_at END,
                manual_override_notes = CASE WHEN manual_override_notes = '' THEN ? ELSE manual_override_notes END,
                crm_notes = CASE WHEN crm_notes = '' THEN ? ELSE crm_notes END
            WHERE id = ? AND platform_id = ?
            """,
            (
                phone,
                preferred_categories,
                risk_profile,
                ticket_min,
                ticket_max,
                preferred_channel,
                recurrence_status,
                source_system,
                external_id,
                synced_at,
                override_notes,
                crm_notes,
                investor_id,
                platform_id,
            ),
        )
    demo_proponent_profiles = [
        (1, 1, "+39 0522 010 500", "https://greenfab.example", "Industria sostenibile", "Attivo", "Documentazione in verifica", "Alessia Ricci", "adapter:future-platform-api", "PAR-PROP-0001", now, "", "Completare KIIS e verifica titolari effettivi."),
        (2, 1, "+39 02 0202 9090", "https://aurora-medtech.example", "MedTech", "Prioritario", "Comitato tecnico", "Giulia Ferri", "adapter:future-platform-api", "PAR-PROP-0002", now, "Score aggiornato manualmente dopo call con il referente finance.", "Preparare parere tecnico e condizioni CdA."),
        (3, 2, "+39 051 404 808", "https://foodloop.example", "Food / Impact", "In istruttoria", "Documenti da raccogliere", "Sara De Luca", "adapter:future-platform-api", "ISI-PROP-0001", now, "", "Richiedere integrazione business plan e contratti pilota."),
    ]
    for profile in demo_proponent_profiles:
        (
            proponent_id,
            platform_id,
            phone,
            website,
            sector,
            crm_status,
            onboarding_status,
            owner_name,
            source_system,
            external_id,
            synced_at,
            override_notes,
            next_action,
        ) = profile
        conn.execute(
            """
            UPDATE proponents
            SET phone = CASE WHEN phone = '' THEN ? ELSE phone END,
                website = CASE WHEN website = '' THEN ? ELSE website END,
                sector = CASE WHEN sector = '' THEN ? ELSE sector END,
                crm_status = CASE WHEN crm_status = '' OR crm_status = 'In istruttoria' THEN ? ELSE crm_status END,
                onboarding_status = CASE WHEN onboarding_status = '' OR onboarding_status = 'Documenti da raccogliere' THEN ? ELSE onboarding_status END,
                owner_name = CASE WHEN owner_name = '' THEN ? ELSE owner_name END,
                source_system = CASE WHEN source_system = '' OR source_system = 'Manuale' THEN ? ELSE source_system END,
                external_proponent_id = CASE WHEN external_proponent_id = '' THEN ? ELSE external_proponent_id END,
                last_synced_at = CASE WHEN last_synced_at = '' THEN ? ELSE last_synced_at END,
                manual_override_notes = CASE WHEN manual_override_notes = '' THEN ? ELSE manual_override_notes END,
                next_action = CASE WHEN next_action = '' THEN ? ELSE next_action END
            WHERE id = ? AND platform_id = ?
            """,
            (
                phone,
                website,
                sector,
                crm_status,
                onboarding_status,
                owner_name,
                source_system,
                external_id,
                synced_at,
                override_notes,
                next_action,
                proponent_id,
                platform_id,
            ),
        )
    if conn.execute("SELECT COUNT(*) FROM investments").fetchone()[0] == 0:
        deal_ids = {r["id"] for r in conn.execute("SELECT id FROM deals").fetchall()}
        investment_rows = []
        if 1 in deal_ids:
            investment_rows.extend([(1, 1, 12000, (today - timedelta(days=45)).isoformat(), "Confermato"), (3, 1, 18000, (today - timedelta(days=38)).isoformat(), "Confermato")])
        if 2 in deal_ids:
            investment_rows.extend([(2, 2, 85000, (today - timedelta(days=14)).isoformat(), "Impegno raccolto"), (3, 2, 34000, (today - timedelta(days=9)).isoformat(), "In attesa periodo riflessione")])
        if 3 in deal_ids:
            investment_rows.extend([(5, 3, 70000, (today - timedelta(days=11)).isoformat(), "Indicazione interesse")])
        if investment_rows:
            conn.executemany(
                "INSERT INTO investments(investor_id, deal_id, amount, invested_at, status) VALUES (?, ?, ?, ?, ?)",
                investment_rows,
            )
    if conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0] == 0:
        deal_ids = {r["id"] for r in conn.execute("SELECT id FROM deals").fetchall()}
        conn.executemany(
            """
            INSERT INTO conflicts(platform_id, subject, related_party, deal_id, description, mitigation, status, opened_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "Partecipazione indiretta in proponente",
                    "Consigliere CdA / MedTech Aurora",
                    2 if 2 in deal_ids else None,
                    "Segnalata possibile relazione commerciale pregressa.",
                    "Astensione del consigliere dalla delibera e disclosure nel fascicolo.",
                    "Aperto",
                    (today - timedelta(days=5)).isoformat(),
                    "",
                ),
                (
                    1,
                    "Advisor comune",
                    "Advisor legale / GreenFab",
                    1 if 1 in deal_ids else None,
                    "Advisor gia incaricato da societa collegata.",
                    "Valutazione indipendenza completata e nota archiviata.",
                    "Mitigato",
                    (today - timedelta(days=34)).isoformat(),
                    (today - timedelta(days=20)).isoformat(),
                ),
                (
                    2,
                    "Investitore rilevante collegato",
                    "Holding Gamma / FoodLoop",
                    3 if 3 in deal_ids else None,
                    "Investitore sofisticato con rapporti commerciali col proponente.",
                    "Disclosure e monitoraggio soglie prima della pubblicazione.",
                    "In analisi",
                    (today - timedelta(days=8)).isoformat(),
                    "",
                ),
            ],
        )
    if conn.execute("SELECT COUNT(*) FROM complaints").fetchone()[0] == 0:
        conn.executemany(
            """
            INSERT INTO complaints(platform_id, received_at, complainant, channel, object, status, outcome, owner_user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, (today - timedelta(days=18)).isoformat(), "Investitore privato", "Email", "Richiesta chiarimenti su periodo di riflessione", "In istruttoria", "Risposta legale in preparazione", 1),
                (1, (today - timedelta(days=48)).isoformat(), "Anna Colombo", "Portale", "Errore visualizzazione importo sottoscritto", "Chiuso", "Rettifica confermata e comunicata", 6),
                (2, (today - timedelta(days=9)).isoformat(), "Maria Fontana", "Email", "Test di ingresso non salvato", "Aperto", "Ticket tecnico aperto", 1),
            ],
        )
    ensure_demo_practice(conn)
    ensure_demo_practice_cda(conn)
    ensure_demo_team(conn)
    conn.commit()


def ensure_demo_team(conn):
    """Anagrafica team Pariter, seedata dagli utenti-attore (firme/CV da caricare)."""
    if conn.execute("SELECT COUNT(*) FROM team_people WHERE platform_id = 1").fetchone()[0] > 0:
        return
    role_label = {"technical_committee": "Comitato Valutazione (CVOI)", "covi": "Advisory Committee",
                  "board": "Consiglio di Amministrazione", "compliance": "Responsabile funzioni di controllo",
                  "legal": "Legale", "operator": "Operatore", "admin": "Amministratore"}
    users = conn.execute(
        "SELECT id, name, email, role FROM users WHERE role != 'admin' ORDER BY id").fetchall()
    for u in users:
        conn.execute(
            "INSERT INTO team_people(platform_id, user_id, name, role, email, active) VALUES (1, ?, ?, ?, ?, 1)",
            (u["id"], u["name"], role_label.get(u["role"], u["role"]), u["email"]),
        )


def ensure_demo_practice_cda(conn):
    """Seconda pratica demo ferma sull'unica delibera CdA con votazione APERTA,
    DOPO il parere dell'Advisory Committee, per mostrare dal vivo il voto del CdA."""
    if conn.execute("SELECT COUNT(*) FROM practices WHERE platform_id = 1").fetchone()[0] >= 2:
        return
    now = now_iso()
    cur = conn.execute(
        """INSERT INTO practices(platform_id, project_title, proponent_name, status, instrument,
            target_amount, max_amount, pre_money, equity_percent, source_system, kiis_state,
            internal_owner, created_by, created_at, updated_at)
           VALUES (1, 'MedTech Aurora - dispositivo diagnostico', 'MedTech Aurora S.p.A.', 'attesa_cda',
            'Quote di S.r.l.', 250000, 500000, 2500000, '9%', 'Import file', 'completata',
            'Alessia Ricci', 1, ?, ?)""",
        (now, now),
    )
    pid = cur.lastrowid
    for phase in ("fase1", "fase2", "fase3", "fase4"):
        conn.execute("INSERT INTO practice_phases(practice_id, phase, status, updated_at) VALUES (?, ?, ?, ?)",
                     (pid, phase, "completata" if phase in ("fase1", "fase2", "fase3") else "da_completare", now))
    for phase, category, label, required in PRACTICE_DOC_SEED:
        st = "verificato" if (phase in ("fase1", "fase2", "fase3") and required) else "mancante"
        conn.execute(
            "INSERT INTO practice_documents(practice_id, phase, category, label, required, doc_status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, phase, category, label, required, st, now),
        )
    for rtype, _label in INTERNAL_REVIEW_TYPES:
        conn.execute(
            "INSERT INTO internal_reviews(practice_id, review_type, review_status, updated_at) VALUES (?, ?, 'validata', ?)",
            (pid, rtype, now),
        )
    # CVOI gia' unanime (cosi' la CdA1 e' sbloccata)
    cur2 = conn.execute(
        """INSERT INTO cvoi_reports(practice_id, weighted_score, outcome, review_status, workflow_status,
            data_valutazione, created_by, created_at, updated_at)
           VALUES (?, 25.4, 'superato', 'bozza', 'unanime', '2026-01-20', 1, ?, ?)""",
        (pid, now, now),
    )
    rid = cur2.lastrowid
    for key, _l, _w, _m, _t in CVOI_AREAS:
        for i in range(len(CVOI_CRITERIA[key])):
            conn.execute("INSERT INTO cvoi_criteria_scores(cvoi_report_id, area_key, idx, raw_score) VALUES (?, ?, ?, 4)",
                         (rid, key, i))
    for uid in (3, 7, 8, 9):
        nm = conn.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
        conn.execute(
            "INSERT INTO cvoi_member_reviews(cvoi_report_id, user_id, member_name, role, status, signed_at, updated_at) VALUES (?, ?, ?, 'technical_committee', 'approvato', ?, ?)",
            (rid, uid, nm["name"] if nm else "", now, now),
        )
    # Parere Advisory Committee gia' reso in versione unanime (precede la delibera CdA).
    conn.execute(
        """INSERT INTO advisory_opinions(practice_id, meeting_date, attendees, agenda, summary, outcome,
            workflow_status, created_by, created_at)
           VALUES (?, '2026-01-21', 'Rubina Galeotti, Gioacchino Attanzio',
            'Esame fascicolo CVOI e profili di conflitto', 'Parere favorevole non vincolante al CdA.',
            'favorevole', 'unanime', 11, ?)""",
        (pid, now),
    )
    # Unica delibera CdA gia' APERTA in votazione (nessun voto ancora espresso).
    conn.execute(
        """INSERT INTO practice_board_decisions(practice_id, decision_round, meeting_date, agenda, outcome, decision_status, created_by, created_at)
           VALUES (?, 1, '2026-01-22', 'Valutazione pratica MedTech Aurora', '', 'in_votazione', 11, ?)""",
        (pid, now),
    )
    conn.execute(
        """INSERT INTO practice_status_history(practice_id, from_status, to_status, actor_id, notes, created_at)
           VALUES (?, '', 'attesa_cda', 1, 'Demo: delibera CdA aperta dopo parere Advisory unanime', ?)""",
        (pid, now),
    )


def ensure_demo_practice(conn):
    """Pratica demo Pariter (Quinte Parallele) ingerita tramite la pipeline reale."""
    if conn.execute("SELECT COUNT(*) FROM practices WHERE platform_id = 1").fetchone()[0] > 0:
        return
    dossier = {
        "jsons": {
            "dati_struttura": {
                "meta": {"piattaforma": "Pariter Equity", "esitoFase1Label": "Idoneo", "statoFase2": "completata"},
                "societa": {
                    "denominazione": "Quinte Parallele S.r.l.",
                    "forma": "S.r.l.",
                    "sedeLegale": "Via dei Musicisti 12, Milano",
                    "pIva": "12345670961",
                    "pec": "quinteparallele@pec.it",
                },
                "legaleRappresentante": {"nome": "Fabio Malerba", "carica": "Amministratore unico"},
                "offertaFase1": {
                    "importoTarget": "150000",
                    "importoMax": "300000",
                    "preMoney": "1200000",
                    "equity": "11%",
                    "strumento": "Quote di S.r.l.",
                    "diritti": "Diritti economici e amministrativi secondo statuto",
                    "useOfProceeds": "Sviluppo piattaforma, marketing, capitale circolante",
                },
            },
            "kiis_dati": {
                "gestorePortale": "Pariter Equity",
                "statoFase3": "in lavorazione",
                "completamentoPct": 72,
                "alertBloccanti": [
                    "Parte F (diritti): manca riferimento alla fonte giuridica (statuto/delibera).",
                ],
                "campiPariter": [
                    "Costi a carico dell'investitore (Parte H)",
                    "Periodo di riflessione",
                ],
                "panoramica": {"ov_progetto": "Quinte Parallele - produzione e distribuzione musicale"},
            },
        },
        "files": [],
    }
    mapped = map_dossier_to_practice(dossier, 1, "Quinte Parallele - produzione musicale")
    practice_id = ingest_practice(conn, dossier, mapped, 1, 1)
    # avanza la demo a uno stato attivo e marca alcuni documenti come verificati
    now = now_iso()
    conn.execute(
        "UPDATE practices SET status = 'verifiche_interne', internal_owner = 'Alessia Ricci', updated_at = ? WHERE id = ?",
        (now, practice_id),
    )
    for to_status, note in [
        ("verifica_documentale", "Avvio verifica documentale"),
        ("fase1_validata", "Fase 1 validata"),
        ("verifiche_interne", "Avvio verifiche interne Pariter"),
    ]:
        conn.execute(
            """INSERT INTO practice_status_history(practice_id, from_status, to_status, actor_id, notes, created_at)
               VALUES (?, '', ?, 1, ?, ?)""",
            (practice_id, to_status, note, now),
        )
    conn.execute(
        """UPDATE practice_documents SET doc_status = 'verificato', updated_at = ?
           WHERE practice_id = ? AND phase = 'fase1' AND required = 1""",
        (now, practice_id),
    )


def log_audit(conn, platform_id, actor_id, entity_type, entity_id, action, details=""):
    conn.execute(
        """
        INSERT INTO audit_log(platform_id, actor_id, entity_type, entity_id, action, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (platform_id, actor_id, entity_type, entity_id, action, details, now_iso()),
    )


def rel_url(path, ctx, extra=None):
    params = {"platform": ctx["platform_id"], "user": ctx["user_id"]}
    if extra:
        params.update(extra)
    return f"{path}?{urlencode(params)}"


def hidden_ctx(ctx):
    return f"""
    <input type="hidden" name="platform" value="{ctx['platform_id']}">
    <input type="hidden" name="user" value="{ctx['user_id']}">
    """


def option_rows(items, selected, value_key="id", label_key="name"):
    chunks = []
    for item in items:
        value = item[value_key]
        label = item[label_key]
        sel = " selected" if str(value) == str(selected) else ""
        chunks.append(f'<option value="{esc(value)}"{sel}>{esc(label)}</option>')
    return "\n".join(chunks)


def option_values(options, selected):
    return "\n".join(
        f'<option value="{esc(option)}"{" selected" if str(option) == str(selected) else ""}>{esc(option)}</option>'
        for option in options
    )


def user_can(user, action):
    role = user["role"]
    if role == "admin":  # override: l'amministratore puo' agire da ogni lato
        return True
    permissions = {
        "create_deal": {"compliance", "legal", "operator"},
        "edit_requirement": {"compliance", "legal", "operator"},
        "verify": {"compliance", "legal"},
        "technical_opinion": {"technical_committee"},
        "covi_opinion": {"covi"},
        "board_decision": {"board"},
        "finalize": {"compliance", "legal", "operator"},
        "manage_compagine": {"compliance", "legal"},
        "upload_document": {"compliance", "legal", "operator"},
        "manage_governance": {"compliance", "legal", "board"},
        "manage_investors": {"compliance", "legal", "operator"},
        "manage_registers": {"compliance", "legal"},
        "manage_proponents": {"compliance", "legal", "operator"},
        "manage_finance": {"compliance", "legal", "operator"},
        "manage_practice": {"compliance", "legal", "operator"},
        "cvoi_draft": {"technical_committee"},
        "cvoi_sign": {"technical_committee"},
        "view_comitato_tecnico": {"technical_committee"},
        "advisory_opinion": {"covi"},
        "close_practice": {"compliance", "legal"},
    }
    return role in permissions.get(action, set())


def fetch_deal(deal_id):
    return row(
        """
        SELECT d.*, p.name AS proponent_name, p.legal_form, p.contact_email,
               tm.name AS technical_reviewer_name, cm.name AS covi_reviewer_name
        FROM deals d
        JOIN proponents p ON p.id = d.proponent_id
        LEFT JOIN committee_members tm ON tm.id = d.technical_reviewer_id
        LEFT JOIN committee_members cm ON cm.id = d.covi_reviewer_id
        WHERE d.id = ?
        """,
        (deal_id,),
    )


def update_deal_phase(conn, deal, target_phase, actor_id, details):
    conn.execute(
        "UPDATE deals SET phase = ?, updated_at = ? WHERE id = ?",
        (target_phase, now_iso(), deal["id"]),
    )
    log_audit(conn, deal["platform_id"], actor_id, "deal", deal["id"], f"Fase: {phase_label(target_phase)}", details)


def generated_document(conn, platform_id, deal_id, proponent_id, origin, category, title, filename_hint, content, actor_id):
    safe = sanitize_filename(filename_hint)
    stamped = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}-{safe}"
    path = UPLOAD_DIR / "generated" / stamped
    path.write_text(content, encoding="utf-8")
    cur = conn.execute(
        """
        INSERT INTO documents(platform_id, deal_id, proponent_id, origin, category, title, filename, storage_path, generated, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (platform_id, deal_id, proponent_id, origin, category, title, stamped, str(path.relative_to(BASE_DIR)), actor_id, now_iso()),
    )
    return cur.lastrowid


def save_uploaded_document(conn, file_item, platform_id, deal_id, proponent_id, origin, category, title, actor_id,
                           doc_date="", description=""):
    filename = sanitize_filename(file_item.filename)
    folder = UPLOAD_DIR / datetime.now().strftime("%Y%m")
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}-{filename}"
    path = folder / stored
    file_item.file.seek(0)
    with path.open("wb") as out:
        shutil.copyfileobj(file_item.file, out)
    cur = conn.execute(
        """
        INSERT INTO documents(platform_id, deal_id, proponent_id, origin, category, title, filename, storage_path, generated, created_by, created_at, doc_date, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            platform_id,
            deal_id,
            proponent_id,
            origin,
            category,
            title or filename,
            filename,
            str(path.relative_to(BASE_DIR)),
            actor_id,
            now_iso(),
            doc_date or "",
            description or "",
        ),
    )
    return cur.lastrowid


CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def html_to_pdf(html_str, out_path):
    """Converte HTML in PDF tramite Chrome headless (nessuna libreria Python)."""
    if not os.path.exists(CHROME_BIN):
        return False
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tf:
            tf.write(html_str)
            tmp = tf.name
        subprocess.run(
            [CHROME_BIN, "--headless", "--disable-gpu", f"--print-to-pdf={out_path}",
             "--no-pdf-header-footer", "file://" + tmp],
            timeout=45, capture_output=True,
        )
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception:
        return False
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def archive_pdf(conn, platform_id, practice_id, proponent_id, origin, category, title, html_str, actor_id):
    """Genera il PDF (Chrome) e lo archivia in documents; fallback a HTML se Chrome assente."""
    folder = UPLOAD_DIR / "generated"
    folder.mkdir(parents=True, exist_ok=True)
    base = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}-{sanitize_filename(title)}"
    pdf_path = folder / (base + ".pdf")
    ok = html_to_pdf(html_str, str(pdf_path))
    if ok:
        stored, is_pdf = pdf_path, 1
    else:
        stored = folder / (base + ".html")
        stored.write_text(html_str, encoding="utf-8")
        is_pdf = 0
    cur = conn.execute(
        """INSERT INTO documents(platform_id, deal_id, proponent_id, practice_id, origin, category, title, filename,
               storage_path, generated, created_by, created_at, is_pdf, archived)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 1)""",
        (platform_id, proponent_id, practice_id, origin, category, title, stored.name,
         str(stored.relative_to(BASE_DIR)), actor_id, now_iso(), is_pdf),
    )
    return cur.lastrowid, is_pdf


def team_signature(conn, platform_id, name):
    """(firma_path, valida). Firma valida se c'e' immagine firma E documento d'identita' in anagrafica."""
    r = conn.execute(
        "SELECT firma_path, id_document_id FROM team_people WHERE platform_id = ? AND name = ? AND active = 1 LIMIT 1",
        (platform_id, name),
    ).fetchone()
    if not r:
        return "", False
    return (r["firma_path"] or ""), bool(r["firma_path"]) and bool(r["id_document_id"])


def signature_block_html(name, firma_path, data, valid):
    img = ""
    if firma_path:
        full = (BASE_DIR / firma_path)
        if full.exists():
            ext = firma_path.rsplit(".", 1)[-1].lower()
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
            b64 = base64.b64encode(full.read_bytes()).decode()
            img = f'<img src="data:{mime};base64,{b64}" alt="firma" style="max-height:72px;display:block;margin:4px 0">'
    note = "" if valid else '<span style="color:#9a6a22">(firma non validata: manca firma o documento in anagrafica)</span>'
    return (f'<div style="margin-top:26px"><p class="meta">Data: {esc(data or "-")}</p>{img}'
            f'<p><strong>{esc(name)}</strong> {note}</p></div>')


def apply_signature_html(html_str, name, firma_path, data, valid):
    block = signature_block_html(name, firma_path, data, valid)
    if "</body>" in html_str:
        return html_str.replace("</body>", block + "</body>")
    return html_str + block


def link_document_practice(conn, document_id, practice_id):
    """Aggancia un documento (creato dagli helper condivisi) a una pratica."""
    if document_id and practice_id:
        conn.execute("UPDATE documents SET practice_id = ? WHERE id = ?", (practice_id, document_id))


def store_practice_file(conn, platform_id, practice_id, proponent_id, origin, category, title, filename, data, actor_id):
    """Salva su disco bytes grezzi (da import dossier) e li registra in documents."""
    safe = sanitize_filename(filename or "documento.bin")
    folder = UPLOAD_DIR / datetime.now().strftime("%Y%m")
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}-{safe}"
    path = folder / stored
    path.write_bytes(data)
    cur = conn.execute(
        """
        INSERT INTO documents(platform_id, deal_id, proponent_id, practice_id, origin, category, title, filename, storage_path, generated, created_by, created_at)
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (platform_id, proponent_id, practice_id, origin, category, title or safe, safe,
         str(path.relative_to(BASE_DIR)), actor_id, now_iso()),
    )
    return cur.lastrowid


# ----- Ingestione dossier proponente (confine astratto trasporto/mappatura) -----

DOSSIER_JSON_KEYS = {
    "dati_struttura.json": "dati_struttura",
    "kiis_dati.json": "kiis_dati",
    "dati_completi.json": "dati_completi",
}


def _dossier_phase_from_path(name):
    low = name.lower()
    for ph in ("fase1", "fase2", "fase3", "fase4"):
        if ph in low:
            return ph
    return ""


def read_dossier_from_zip(file_item):
    """STRATO TRASPORTO. Legge un .zip dossier e restituisce il dict canonico.
    Sostituibile in futuro con un feed live senza toccare mappatura/persistenza."""
    file_item.file.seek(0)
    raw = file_item.file.read()
    jsons = {}
    files = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            base = info.filename.rsplit("/", 1)[-1]
            data = zf.read(info.filename)
            key = DOSSIER_JSON_KEYS.get(base.lower())
            if key:
                try:
                    jsons[key] = json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    pass
                continue
            # i .json di dati non vanno tra i file allegati; tutto il resto sì
            if base.lower().endswith(".json"):
                continue
            files.append((info.filename, data))
    return {"jsons": jsons, "files": files}


def read_dossier_from_json(struttura_item=None, kiis_item=None):
    """STRATO TRASPORTO (variante): singoli JSON di fase."""
    jsons = {}
    for item, key in ((struttura_item, "dati_struttura"), (kiis_item, "kiis_dati")):
        if item is not None:
            item.file.seek(0)
            try:
                jsons[key] = json.loads(item.file.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pass
    return {"jsons": jsons, "files": []}


def _parse_amount(value):
    """Converte importi in formato libero/italiano in float (tollerante)."""
    if value in (None, ""):
        return 0.0
    s = re.sub(r"[^0-9.,]", "", str(value))
    if not s:
        return 0.0
    if "." in s and "," in s:  # 1.234.567,89 -> 1234567.89
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:  # 1234,89 -> 1234.89
        s = s.replace(",", ".")
    elif "." in s:
        # solo punto: separatore migliaia se piu' punti o 3 cifre dopo l'ultimo
        # (200.000 -> 200000); decimale se 1-2 cifre dopo l'unico punto (12.50)
        if s.count(".") > 1 or len(s.rsplit(".", 1)[1]) == 3:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def map_dossier_to_practice(dossier, platform_id, project_title_override=""):
    """STRATO MAPPATURA PURA: dossier canonico -> record pratica + alert.
    Nessun I/O, nessun DB. Tollerante a chiavi mancanti / fasi parziali."""
    js = dossier.get("jsons", {})
    ds = js.get("dati_struttura") or {}
    dc = js.get("dati_completi") or {}
    kd = js.get("kiis_dati") or {}
    dc_data = dc.get("DATA") or {}

    societa = ds.get("societa") or {}
    offerta = ds.get("offertaFase1") or {}
    denom = societa.get("denominazione") or dc_data.get("denom") or ""

    target = offerta.get("importoTarget") or dc_data.get("impTarget")
    massimo = offerta.get("importoMax") or dc_data.get("impMax")
    premoney = offerta.get("preMoney") or dc_data.get("preMoney")
    equity = offerta.get("equity") or dc_data.get("pctEquity") or ""
    strumento = offerta.get("strumento") or dc_data.get("strumento") or ""

    title = (project_title_override or "").strip()
    if not title:
        panoramica = kd.get("panoramica") or {}
        title = panoramica.get("ov_progetto") or dc_data.get("ov_progetto") or ""
    if not title:
        title = f"Offerta {denom}".strip() if denom else "Pratica importata"

    practice = {
        "project_title": title,
        "proponent_name": denom,
        "instrument": strumento,
        "target_amount": _parse_amount(target),
        "max_amount": _parse_amount(massimo),
        "pre_money": _parse_amount(premoney),
        "equity_percent": str(equity),
        "kiis_state": kd.get("statoFase3") or "",
        "external_ref": (ds.get("meta") or {}).get("piattaforma") or kd.get("gestorePortale") or "",
        "dossier_json": json.dumps(js, ensure_ascii=False),
    }

    alerts = []
    for msg in (kd.get("alertBloccanti") or []):
        alerts.append({"severity": "bloccante", "source": "import_dossier", "message": str(msg)})
    for campo in (kd.get("campiPariter") or []):
        alerts.append({"severity": "non_bloccante", "source": "import_dossier",
                       "message": f"Campo KIIS da compilare da Pariter: {campo}"})
    if not js:
        alerts.append({"severity": "non_bloccante", "source": "import_dossier",
                       "message": "Nessun JSON dati riconosciuto nel pacchetto: verificare l'export del proponente."})
    return {"practice": practice, "alerts": alerts}


def ingest_practice(conn, dossier, mapped, platform_id, actor_id, proponent_id=None):
    """STRATO PERSISTENZA: scrive pratica, checklist documentale, alert e file."""
    now = now_iso()
    p = mapped["practice"]
    cur = conn.execute(
        """
        INSERT INTO practices(platform_id, proponent_id, project_title, proponent_name, status,
            instrument, target_amount, max_amount, pre_money, equity_percent,
            source_system, external_ref, dossier_json, kiis_state, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'dossier_ricevuto', ?, ?, ?, ?, ?, 'Import file', ?, ?, ?, ?, ?, ?)
        """,
        (platform_id, proponent_id, p["project_title"], p["proponent_name"],
         p["instrument"], p["target_amount"], p["max_amount"], p["pre_money"], p["equity_percent"],
         p["external_ref"], p["dossier_json"], p["kiis_state"], actor_id, now, now),
    )
    practice_id = cur.lastrowid

    conn.execute(
        """INSERT INTO practice_status_history(practice_id, from_status, to_status, actor_id, notes, created_at)
           VALUES (?, '', 'dossier_ricevuto', ?, 'Dossier importato dal software proponente', ?)""",
        (practice_id, actor_id, now),
    )

    for phase in ("fase1", "fase2", "fase3", "fase4"):
        conn.execute(
            "INSERT INTO practice_phases(practice_id, phase, status, updated_at) VALUES (?, ?, 'da_completare', ?)",
            (practice_id, phase, now),
        )

    # Checklist documentale attesa.
    seed_doc_ids = {}
    for phase, category, label, required in PRACTICE_DOC_SEED:
        c = conn.execute(
            """INSERT INTO practice_documents(practice_id, phase, category, label, required, doc_status, updated_at)
               VALUES (?, ?, ?, ?, ?, 'mancante', ?)""",
            (practice_id, phase, category, label, required, now),
        )
        seed_doc_ids.setdefault(phase, []).append((c.lastrowid, label.lower()))

    # File del dossier -> documents (con practice_id) + match alla checklist.
    for name, data in dossier.get("files", []):
        base = name.rsplit("/", 1)[-1]
        phase = _dossier_phase_from_path(name)
        doc_id = store_practice_file(
            conn, platform_id, practice_id, proponent_id,
            "Proponente", phase or "dossier", base, base, data, actor_id,
        )
        # match euristico per fase + parola chiave nel nome file
        low = base.lower()
        for row_id, label in seed_doc_ids.get(phase, []):
            token = label.split()[0]
            if token and token in low:
                conn.execute(
                    """UPDATE practice_documents SET doc_status='da_verificare', document_id=?, updated_at=?
                       WHERE id=? AND doc_status='mancante'""",
                    (doc_id, now, row_id),
                )
                break

    for a in mapped.get("alerts", []):
        conn.execute(
            """INSERT INTO practice_alerts(practice_id, severity, source, message, alert_status, created_at)
               VALUES (?, ?, ?, ?, 'aperto', ?)""",
            (practice_id, a["severity"], a["source"], a["message"], now),
        )

    log_audit(conn, platform_id, actor_id, "practice", practice_id, "Import dossier",
              f"{p['project_title']} ({p['proponent_name']})")
    return practice_id


def practice_doc_shell(title, practice, sections_html, ai_generated=True):
    """Documento HTML stampabile per output istruttoria (stile sobrio, marcato IA)."""
    banner = (
        '<div class="ai-flag">Bozza generata da IA &mdash; da verificare. La validazione finale e\' sempre umana.</div>'
        if ai_generated else ""
    )
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><title>{esc(title)} - {esc(practice['project_title'])}</title>
<style>
  body{{font-family:Georgia,'Times New Roman',serif;color:#1f1b17;max-width:820px;margin:24px auto;padding:0 28px;line-height:1.5;}}
  h1{{font-size:21px;margin:0 0 4px;}} h2{{font-size:15px;margin:22px 0 6px;border-bottom:1px solid #d8d0c4;padding-bottom:3px;}}
  .meta{{font-family:Consolas,monospace;font-size:12px;color:#5f5a55;margin:2px 0;}}
  .ai-flag{{font-family:Consolas,monospace;font-size:11px;color:#9a6a22;border:1px solid #d8c39a;background:#faf4e6;padding:7px 10px;margin:0 0 18px;}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0;}} td,th{{border:1px solid #d8d0c4;padding:6px 8px;text-align:left;}}
  p{{margin:6px 0;}} .muted{{color:#76706a;}}
</style></head><body>
{banner}
<h1>{esc(title)}</h1>
<p class="meta">Proponente: {esc(practice['proponent_name'] or '-')}</p>
<p class="meta">Progetto: {esc(practice['project_title'])}</p>
<p class="meta">Pratica #{practice['id']} &middot; generato il {esc(now_iso())}</p>
{sections_html}
<h2>Validazione</h2>
<p class="muted">Documento da sottoporre a validazione del responsabile competente prima dell'allegazione al fascicolo.</p>
</body></html>"""


def compose_internal_review_draft(practice, review_type):
    """Bozza editabile della relazione interna, precompilata col modello reale e i dati del dossier.

    Convenzione di formato (resa fedele in PDF da review_text_to_html):
    - una riga "N. Titolo" da sola = intestazione di sezione;
    - righe "Etichetta: valore" consecutive = tabella a due colonne;
    - righe che iniziano con "- " = elenco puntato;
    - blocchi separati da riga vuota.
    """
    js = {}
    try:
        js = json.loads(practice["dossier_json"] or "{}")
    except (ValueError, TypeError):
        js = {}
    ds = js.get("dati_struttura") or {}
    societa = ds.get("societa") or {}
    rep = ds.get("legaleRappresentante") or {}
    kd = js.get("kiis_dati") or {}
    prop = practice["proponent_name"] or societa.get("denominazione") or "-"
    if review_type == "fascicolo":
        offerta = ds.get("offertaFase1") or {}
        cf = _practice_val(practice, "m_conflitti")
        cf_lbl = CONFLITTI_MERITO_LABELS.get(cf, "non valutato")
        cf_mis = _practice_val(practice, "m_conflitti_misura")
        if cf == "gestibile" and cf_mis:
            cf_lbl += f" - misura: {cf_mis}"
        kc_lbl = KIIS_COERENZA_LABELS.get(_practice_val(practice, "m_kiis_coerenza"), "non verificata")
        return f"""Fascicolo di valutazione del progetto (M7) - {prop}

Sintesi dell'istruttoria del Comitato Valutazione Opportunita' di Investimento (CVOI). La parte
descrittiva e' precompilata come assistenza: integrare/correggere prima della firma.

1. Team / Management
[Descrivere esperienza, completezza e affidabilita' del team. Dati: key manager ed esperienze.]

2. Tecnologia
[Descrivere la soluzione tecnologica, maturita' e difendibilita'.]

3. Proprieta' intellettuale
[Brevetti, marchi, know-how e relativa titolarita'.]

4. Mercato
[Dimensione, segmenti, concorrenza, posizionamento.]

5. Business Model
[Modello di ricavo, struttura costi, scalabilita'. Strumento: {offerta.get('strumento') or '-'}; pre-money: {offerta.get('preMoney') or '-'}; equity: {offerta.get('equity') or '-'}.]

6. Roadmap
[Tappe, milestone e impieghi della raccolta (target {offerta.get('importoTarget') or '-'}, max {offerta.get('importoMax') or '-'}).]

7. Esito conflitti di interesse (da 3.1)
{cf_lbl}

8. Esito verifica KIIS (da 3.1)
{kc_lbl}

Allegati: scheda di scoring (M6) e bozza KIIS. La presente valutazione, una volta firmata dai
membri del CVOI, e' trasmessa all'Advisory Committee per il parere."""
    if review_type == "aml_art5":
        text = f"""1. Oggetto
Controlli ex art. 5 Reg. (UE) 2020/1503 sul titolare del progetto e sul titolare effettivo, ai fini dell'ammissione dell'offerta sulla piattaforma Pariter Equity.

2. Soggetti
Denominazione proponente: {prop}
Sede: {societa.get('sedeLegale') or '-'}
P. IVA: {societa.get('pIva') or '-'}
Legale rappresentante: {rep.get('nome') or '-'} ({rep.get('carica') or '-'})

3. Verifiche svolte
- Assenza di stabilimento/residenza in giurisdizioni non cooperative a fini fiscali;
- assenza di stabilimento/residenza in Paesi terzi ad alto rischio;
- assenza di precedenti penali rilevanti in capo agli esponenti;
- coerenza anagrafica tra visura, statuto e autodichiarazioni.

4. Documentazione esaminata
Visura camerale, statuto, autodichiarazione onorabilita', dichiarazione su giurisdizioni. Eventuale casellario giudiziale da acquisire.

5. Conclusioni allo stato degli atti
Allo stato degli atti non emergono elementi ostativi ai sensi dell'art. 5. Da completare con la documentazione ancora in acquisizione."""
    elif review_type == "conflitti":
        text = f"""1. Oggetto
Verifica sull'insussistenza/sussistenza di conflitti di interesse tra Pariter Equity e il proponente {prop} in relazione al progetto.

2. Soggetti coinvolti
Proponente, esponenti, soci, advisor e collaboratori coinvolti nell'istruttoria.

3. Conflitti rilevati
Conflitti attuali: nessuno rilevato allo stato degli atti
Conflitti potenziali: da monitorare secondo il registro conflitti
Rapporti Pariter-proponente: da dichiarare/aggiornare

4. Misure di mitigazione
Annotazione nel registro conflitti, astensione dei soggetti interessati ove ricorrano i presupposti.

5. Attestazione finale
Allo stato degli atti si attesta l'insussistenza di conflitti di interesse ostativi, salvo aggiornamenti."""
    else:  # coerenza_kiis
        offerta = ds.get("offertaFase1") or {}
        alert = '; '.join(kd.get('alertBloccanti') or []) or 'Nessun alert bloccante segnalato dal proponente.'
        text = f"""1. Oggetto
Verifica di coerenza tra bozza KIIS, dati di offerta, business plan e documentazione di progetto.

2. Dati di offerta confrontati
Importo target: {offerta.get('importoTarget') or '-'}
Importo massimo: {offerta.get('importoMax') or '-'}
Pre-money: {offerta.get('preMoney') or '-'}
Equity offerta: {offerta.get('equity') or '-'}
Strumento: {offerta.get('strumento') or '-'}

3. Controlli di coerenza
- Importi target/massimo coerenti tra KIIS e dati offerta;
- diritti degli investitori coerenti con statuto/delibera;
- rischi coerenti con il business plan;
- uso dei fondi coerente;
- claim non fuorvianti e privi di promesse di rendimento.

4. Alert rilevati
{alert}

5. Esito
Coerenza complessiva da confermare previa chiusura degli alert e dei campi di competenza Pariter."""
    return text


def review_text_to_html(text):
    """Converte la bozza editabile della relazione nel modello HTML (intestazioni, tabelle, elenchi)."""
    out = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [l.rstrip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        # intestazione di sezione: riga "N. Titolo" breve, da sola in cima al blocco
        if re.match(r"^\d+\.\s+\S", lines[0]) and len(lines[0]) <= 48 and "." not in lines[0][lines[0].find('.')+1:]:
            out.append(f"<h2>{esc(lines[0])}</h2>")
            lines = lines[1:]
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.lstrip().startswith("- "):
                items = []
                while i < len(lines) and lines[i].lstrip().startswith("- "):
                    items.append(f"<li>{esc(lines[i].lstrip()[2:].rstrip(';.'))}</li>")
                    i += 1
                out.append("<ul>" + "".join(items) + "</ul>")
            elif ": " in ln and not ln.lstrip().startswith("-"):
                rows_html = []
                while i < len(lines) and ": " in lines[i] and not lines[i].lstrip().startswith("-"):
                    k, v = lines[i].split(": ", 1)
                    rows_html.append(f"<tr><th>{esc(k.strip())}</th><td>{esc(v.strip())}</td></tr>")
                    i += 1
                out.append("<table>" + "".join(rows_html) + "</table>")
            else:
                para = []
                while i < len(lines) and not lines[i].lstrip().startswith("- ") and ": " not in lines[i]:
                    para.append(esc(lines[i]))
                    i += 1
                out.append("<p>" + "<br>".join(para) + "</p>")
    return "".join(out)


def build_kiis_draft_html(practice):
    """Bozza KIIS (scheda informativa) da template, precompilata con i dati di offerta del dossier."""
    js = {}
    try:
        js = json.loads(practice["dossier_json"] or "{}")
    except (ValueError, TypeError):
        js = {}
    ds = js.get("dati_struttura") or {}
    societa = ds.get("societa") or {}
    offerta = ds.get("offertaFase1") or {}
    ident = offer_identifier(practice)
    prop = practice["proponent_name"] or societa.get("denominazione") or "-"
    sections = f"""
<h2>A. Identificativo dell'offerta</h2>
<table>
  <tr><th>Identificativo offerta</th><td>{esc(ident)}</td></tr>
  <tr><th>Titolare del progetto</th><td>{esc(prop)}</td></tr>
  <tr><th>Sede</th><td>{esc(societa.get('sedeLegale') or '-')}</td></tr>
  <tr><th>P. IVA</th><td>{esc(societa.get('pIva') or '-')}</td></tr>
</table>
<h2>B. Panoramica dell'offerta</h2>
<table>
  <tr><th>Importo target</th><td>{esc(offerta.get('importoTarget') or '-')}</td></tr>
  <tr><th>Importo massimo</th><td>{esc(offerta.get('importoMax') or '-')}</td></tr>
  <tr><th>Valutazione pre-money</th><td>{esc(offerta.get('preMoney') or '-')}</td></tr>
  <tr><th>Quota offerta (equity)</th><td>{esc(offerta.get('equity') or '-')}</td></tr>
  <tr><th>Strumento finanziario</th><td>{esc(offerta.get('strumento') or '-')}</td></tr>
</table>
<h2>C. Diritti connessi agli strumenti</h2>
<p>{esc(offerta.get('diritti') or 'Da completare con i diritti patrimoniali e amministrativi connessi agli strumenti offerti.')}</p>
<h2>D. Utilizzo dei proventi (use of proceeds)</h2>
<p>{esc(offerta.get('useOfProceeds') or 'Da completare con la destinazione dei fondi raccolti.')}</p>
<h2>E. Fattori di rischio</h2>
<p>Da completare: rischi connessi all'emittente, al settore, allo strumento e alla mancanza di un mercato secondario. Nessuna promessa di rendimento.</p>
<h2>F. Avvertenze</h2>
<p>L'investimento in crowdfunding comporta il rischio di perdita totale del capitale e di illiquidita'. La presente scheda non e' approvata da alcuna autorita'.</p>"""
    return practice_doc_shell("Scheda KIIS (bozza) - " + (practice["project_title"] or ""), practice, sections)


def store_review_draft(conn, practice, review_type, body, actor_id, prev_doc_id=None):
    """Rigenera il documento HTML scaricabile della bozza di relazione (sostituendo il precedente non firmato)."""
    if prev_doc_id:
        d = conn.execute("SELECT storage_path, is_pdf, archived FROM documents WHERE id = ?", (prev_doc_id,)).fetchone()
        if d and not d["is_pdf"] and not d["archived"]:
            try:
                (BASE_DIR / d["storage_path"]).unlink(missing_ok=True)
            except OSError:
                pass
            conn.execute("DELETE FROM documents WHERE id = ?", (prev_doc_id,))
    title = INTERNAL_REVIEW_LABELS.get(review_type, review_type)
    html_doc = practice_doc_shell(title, practice, review_text_to_html(body), ai_generated=True)
    doc_id = generated_document(
        conn, practice["platform_id"], None, practice["proponent_id"],
        "Verifiche interne", "relazione (bozza)", f"{title} - {practice['project_title']}",
        f"{review_type}-bozza.html", html_doc, actor_id,
    )
    link_document_practice(conn, doc_id, practice["id"])
    return doc_id


def build_internal_review_html(practice, review_type):
    """Genera la bozza di relazione interna (AML art.5 / conflitti / coerenza KIIS)."""
    js = {}
    try:
        js = json.loads(practice["dossier_json"] or "{}")
    except (ValueError, TypeError):
        js = {}
    ds = js.get("dati_struttura") or {}
    societa = ds.get("societa") or {}
    rep = ds.get("legaleRappresentante") or {}
    kd = js.get("kiis_dati") or {}

    if review_type == "aml_art5":
        sections = f"""
<h2>1. Oggetto</h2>
<p>Controlli ex art. 5 Reg. (UE) 2020/1503 sul titolare del progetto e sul titolare effettivo, ai fini dell'ammissione dell'offerta sulla piattaforma Pariter Equity.</p>
<h2>2. Soggetti</h2>
<table>
  <tr><th>Denominazione proponente</th><td>{esc(practice['proponent_name'] or societa.get('denominazione') or '-')}</td></tr>
  <tr><th>Sede</th><td>{esc(societa.get('sedeLegale') or '-')}</td></tr>
  <tr><th>P. IVA</th><td>{esc(societa.get('pIva') or '-')}</td></tr>
  <tr><th>Legale rappresentante</th><td>{esc(rep.get('nome') or '-')} ({esc(rep.get('carica') or '-')})</td></tr>
</table>
<h2>3. Verifiche svolte</h2>
<p>- Assenza di stabilimento/residenza in giurisdizioni non cooperative a fini fiscali;<br>
- assenza di stabilimento/residenza in Paesi terzi ad alto rischio;<br>
- assenza di precedenti penali rilevanti in capo agli esponenti;<br>
- coerenza anagrafica tra visura, statuto e autodichiarazioni.</p>
<h2>4. Documentazione esaminata</h2>
<p>Visura camerale, statuto, autodichiarazione onorabilita', dichiarazione su giurisdizioni. Eventuale casellario giudiziale da acquisire.</p>
<h2>5. Conclusioni allo stato degli atti</h2>
<p>Allo stato degli atti non emergono elementi ostativi ai sensi dell'art. 5. Da completare con la documentazione ancora in acquisizione.</p>
"""
        title = "Relazione art. 5 / AML"
    elif review_type == "conflitti":
        sections = f"""
<h2>1. Oggetto</h2>
<p>Verifica sull'insussistenza/sussistenza di conflitti di interesse tra Pariter Equity e il proponente {esc(practice['proponent_name'] or '-')} in relazione al progetto.</p>
<h2>2. Soggetti coinvolti</h2>
<p>Proponente, esponenti, soci, advisor e collaboratori coinvolti nell'istruttoria.</p>
<h2>3. Conflitti rilevati</h2>
<table>
  <tr><th>Conflitti attuali</th><td>Nessuno rilevato allo stato degli atti</td></tr>
  <tr><th>Conflitti potenziali</th><td>Da monitorare secondo il registro conflitti</td></tr>
  <tr><th>Rapporti Pariter-proponente</th><td>Da dichiarare/aggiornare</td></tr>
</table>
<h2>4. Misure di mitigazione</h2>
<p>Annotazione nel registro conflitti, astensione dei soggetti interessati ove ricorrano i presupposti.</p>
<h2>5. Attestazione finale</h2>
<p>Allo stato degli atti si attesta l'insussistenza di conflitti di interesse ostativi, salvo aggiornamenti.</p>
"""
        title = "Relazione insussistenza conflitti di interesse"
    else:  # coerenza_kiis
        offerta = ds.get("offertaFase1") or {}
        sections = f"""
<h2>1. Oggetto</h2>
<p>Verifica di coerenza tra bozza KIIS, dati di offerta, business plan e documentazione di progetto.</p>
<h2>2. Dati di offerta confrontati</h2>
<table>
  <tr><th>Importo target</th><td>{esc(offerta.get('importoTarget') or '-')}</td></tr>
  <tr><th>Importo massimo</th><td>{esc(offerta.get('importoMax') or '-')}</td></tr>
  <tr><th>Pre-money</th><td>{esc(offerta.get('preMoney') or '-')}</td></tr>
  <tr><th>Equity offerta</th><td>{esc(offerta.get('equity') or '-')}</td></tr>
  <tr><th>Strumento</th><td>{esc(offerta.get('strumento') or '-')}</td></tr>
</table>
<h2>3. Controlli di coerenza</h2>
<p>- Importi target/massimo coerenti tra KIIS e dati offerta;<br>
- diritti degli investitori coerenti con statuto/delibera;<br>
- rischi coerenti con il business plan;<br>
- uso dei fondi coerente;<br>
- claim non fuorvianti e privi di promesse di rendimento.</p>
<h2>4. Alert rilevati</h2>
<p>{esc('; '.join(kd.get('alertBloccanti') or []) or 'Nessun alert bloccante segnalato dal proponente.')}</p>
<h2>5. Esito</h2>
<p>Coerenza complessiva da confermare previa chiusura degli alert e dei campi di competenza Pariter.</p>
"""
        title = "Report coerenza KIIS / offerta / documenti"
    return title, practice_doc_shell(title, practice, sections)


CVOI_OUTCOME_LABELS = {
    "superato": "Superato",
    "superato_condizioni": "Superato con condizioni",
    "non_superato": "Non superato",
    "da_integrare": "Da integrare",
}


def compute_cvoi(score_inputs):
    """score_inputs: lista dict {weight, threshold, raw}. Ritorna (weighted, outcome)."""
    weighted = round(sum(s["raw"] * s["weight"] for s in score_inputs), 2)
    below = [s for s in score_inputs if s["raw"] < s["threshold"]]
    if weighted >= CVOI_OVERALL_THRESHOLD and not below:
        outcome = "superato"
    elif weighted >= CVOI_OVERALL_THRESHOLD:
        outcome = "superato_condizioni"
    else:
        outcome = "non_superato"
    return weighted, outcome


def compute_cvoi_from_criteria(criteria_scores):
    """criteria_scores: {area_key: [punteggi]}. Ritorna (area_totals, weighted, outcome, total_raw, total_max)."""
    score_inputs = []
    area_totals = {}
    total_raw = 0.0
    total_max = 0
    for key, _label, w, mx, thr in CVOI_AREAS:
        vals = criteria_scores.get(key, [])
        raw = round(sum(vals), 2)
        area_totals[key] = raw
        total_raw += raw
        total_max += mx
        score_inputs.append({"weight": w, "threshold": thr, "raw": raw})
    weighted, outcome = compute_cvoi(score_inputs)
    return area_totals, weighted, outcome, round(total_raw, 2), total_max


# Minimi PONDERATI per area (Allegato 5.1): thr x peso. Coincidono con i minimi raw 18/21/18.
CVOI_AREA_MIN_WEIGHTED = {"area1": round(18 * 0.35, 2), "area2": round(21 * 0.35, 2), "area3": round(18 * 0.30, 2)}


def compute_cvoi_collegial(conn, practice):
    """Valutazione collegiale: media dei punteggi dei valutatori, criterio per criterio,
    esclusi gli astenuti. Serve un minimo di 2 valutatori non astenuti."""
    pid = practice["id"]
    evaluators = cvoi_committee_members(conn)
    status = {r["evaluator_id"]: r for r in
              conn.execute("SELECT * FROM cvoi_eval_status WHERE practice_id = ?", (pid,)).fetchall()}
    abstained = {eid for eid, s in status.items() if s["abstained"]}
    scores = {}
    for r in conn.execute("SELECT evaluator_id, area_key, idx, score FROM cvoi_eval_scores WHERE practice_id = ?", (pid,)):
        scores[(r["evaluator_id"], r["area_key"], r["idx"])] = r["score"]

    def has_any(eid):
        return any((eid, k, i) in scores for k, _l, _w, _m, _t in CVOI_AREAS for i in range(len(CVOI_CRITERIA[k])))

    active = [e for e in evaluators if e["id"] not in abstained and has_any(e["id"])]
    criteria_scores = {}
    detail = {}
    for key, _l, _w, _m, _t in CVOI_AREAS:
        medias = []
        for i in range(len(CVOI_CRITERIA[key])):
            vals = [scores[(e["id"], key, i)] for e in active if (e["id"], key, i) in scores]
            media = round(sum(vals) / len(vals), 2) if vals else 0.0
            medias.append(media)
            detail[(key, i)] = {"per": {e["id"]: scores.get((e["id"], key, i)) for e in evaluators},
                                "media": media, "n": len(vals)}
        criteria_scores[key] = medias
    area_totals, weighted, outcome, total_raw, total_max = compute_cvoi_from_criteria(criteria_scores)
    n_val = len(active)
    valid = n_val >= 2

    def ev_state(eid):
        if eid in abstained:
            return "astenuto"
        if not has_any(eid):
            return "da_compilare"
        st = status.get(eid)
        return "validato" if (st and st["confirmed"]) else "salvato"

    ev_status = {e["id"]: ev_state(e["id"]) for e in evaluators}
    n_confirmed = sum(1 for e in evaluators if ev_status[e["id"]] == "validato")
    # scheda completa: almeno 2 validati e nessuno lasciato 'da compilare'/'salvato'
    all_done = (n_confirmed >= 2) and all(ev_status[e["id"]] in ("validato", "astenuto") for e in evaluators)
    return {"evaluators": evaluators, "abstained": abstained, "status": status, "scores": scores,
            "active": active, "n_val": n_val, "valid": valid, "criteria_scores": criteria_scores,
            "area_totals": area_totals, "weighted": weighted,
            "outcome": outcome if valid else "da_integrare", "detail": detail,
            "total_raw": total_raw, "total_max": total_max,
            "ev_status": ev_status, "n_confirmed": n_confirmed, "all_done": all_done}


def save_cvoi_collegial(conn, practice, actor_id):
    """Ricalcola dai punteggi individuali e aggiorna cvoi_reports + cvoi_criteria_scores (le medie),
    cosi' fascicolo M7 e cvoi_summary_for restano coerenti."""
    c = compute_cvoi_collegial(conn, practice)
    pid = practice["id"]
    now = now_iso()
    rep = conn.execute("SELECT id FROM cvoi_reports WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    if rep:
        rid = rep["id"]
        conn.execute("UPDATE cvoi_reports SET weighted_score = ?, outcome = ?, updated_at = ? WHERE id = ?",
                     (c["weighted"], c["outcome"], now, rid))
    else:
        cur = conn.execute(
            "INSERT INTO cvoi_reports(practice_id, weighted_score, outcome, review_status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, 'bozza', ?, ?, ?)", (pid, c["weighted"], c["outcome"], actor_id, now, now))
        rid = cur.lastrowid
    conn.execute("DELETE FROM cvoi_criteria_scores WHERE cvoi_report_id = ?", (rid,))
    for key, _l, _w, _m, _t in CVOI_AREAS:
        for i, media in enumerate(c["criteria_scores"][key]):
            conn.execute("INSERT INTO cvoi_criteria_scores(cvoi_report_id, area_key, idx, raw_score) VALUES (?, ?, ?, ?)",
                         (rid, key, i, media))
    return rid, c


def build_cvoi_html(practice, report_fields, criteria_scores):
    """Verbale di valutazione del progetto (CVOI), fedele al template reale."""
    area_totals, weighted, outcome, total_raw, total_max = compute_cvoi_from_criteria(criteria_scores)
    # tabella riepilogo aree
    summary_rows = ""
    for key, label, w, mx, thr in CVOI_AREAS:
        raw = area_totals.get(key, 0)
        summary_rows += (f"<tr><td>{esc(label)}</td><td>{raw:g}/{int(mx)}</td>"
                         f"<td>{int(w*100)}%</td><td>{round(raw*w, 2):g}</td></tr>")
    formula = " + ".join(f"({area_totals.get(k,0):g}x{w:g})" for k, _l, w, _m, _t in CVOI_AREAS)
    # sezioni per criterio
    crit_sections = ""
    for n, (key, label, w, mx, thr) in enumerate(CVOI_AREAS, start=1):
        vals = criteria_scores.get(key, [])
        rows_html = "".join(
            f"<tr><td>{esc(c)}</td><td>{(vals[i] if i < len(vals) else 0):g}</td></tr>"
            for i, c in enumerate(CVOI_CRITERIA[key])
        )
        crit_sections += f"""
<h2>{n}) {esc(label)}</h2>
<table><tr><th>Criterio di valutazione</th><th>Punteggio</th></tr>{rows_html}</table>
<p class="muted">Punteggio minimo richiesto: {int(thr)} su {int(mx)}. Peso {int(w*100)}% sul giudizio finale.</p>"""
    # Sezioni 7 e 8 del fascicolo (Allegato 5_1), obbligatorie: esiti da 3.1
    cf = _practice_val(practice, "m_conflitti")
    cf_mis = _practice_val(practice, "m_conflitti_misura")
    kc = _practice_val(practice, "m_kiis_coerenza")
    cf_txt = CONFLITTI_MERITO_LABELS.get(cf, "non valutato")
    if cf == "gestibile" and cf_mis:
        cf_txt += f" &mdash; misura: {esc(cf_mis)}"
    kc_txt = KIIS_COERENZA_LABELS.get(kc, "non verificata")
    fascicolo_78 = f"""
<h2>7) Esito conflitti di interesse</h2>
<p>Accertamento del team (Allegato 14): <strong>{cf_txt}</strong>.</p>
<h2>8) Esito verifica KIIS</h2>
<p>Coerenza/correttezza/completezza della scheda KIIS rispetto al patrimonio informativo: <strong>{esc(kc_txt)}</strong>. La bozza di KIIS e' allegata al fascicolo.</p>"""
    notes = report_fields.get("notes_qualitative") or ""
    closing = report_fields.get("closing_note") or (
        "La presente valutazione puo' essere inoltrata al CdA per l'approvazione del progetto, salvo che non "
        "emergano ulteriori criticita' non riscontrate in questa sede. Si ricorda al CdA che la pubblicazione "
        "del progetto potra' avvenire unicamente a seguito dell'invio del casellario giudiziale di tutti i membri "
        "dell'Organo Amministrativo del Proponente.")
    signatures = report_fields.get("signatures_html") or '<p class="muted">_____________________ &nbsp; _____________________ &nbsp; _____________________</p>'
    sections = f"""
<table>
  <tr><th>Proponente</th><td>{esc(practice['proponent_name'] or '-')}</td></tr>
  <tr><th>Mail</th><td>{esc(report_fields.get('mail') or '-')}</td></tr>
  <tr><th>Data caricamento</th><td>{esc(report_fields.get('data_caricamento') or '-')}</td></tr>
  <tr><th>Data valutazione</th><td>{esc(report_fields.get('data_valutazione') or '-')}</td></tr>
  <tr><th>Punteggio di valutazione</th><td>{formula} = {weighted:g}</td></tr>
</table>
<p>Valutazione elaborata dal Comitato Valutazione Opportunita' di Investimento (CVOI), che ha analizzato la
documentazione prodotta dal Proponente (visura, statuto, business plan, financial plan, presentazione, bilanci),
tenuto colloqui con il Proponente e visionato le autodichiarazioni (mancato superamento 5.000.000, titolare
effettivo, assenza precedenti penali).</p>
<p>Il punteggio complessivo minimo per superare il first screening - media ponderata - e' stabilito in
{CVOI_OVERALL_THRESHOLD:g} punti ((18x0,35)+(21x0,35)+(18x0,30)).</p>
<h2>Aree di valutazione</h2>
<table>
  <tr><th>Area</th><th>Punteggio</th><th>Peso</th><th>Media ponderata</th></tr>
  {summary_rows}
  <tr><th>Complessivo</th><th>{total_raw:g}/{total_max}</th><th>100%</th><th>{weighted:g}</th></tr>
</table>
<p><strong>Esito first screening: {esc(CVOI_OUTCOME_LABELS.get(outcome, outcome))}</strong> (soglia {CVOI_OVERALL_THRESHOLD:g}).</p>
{crit_sections}
{fascicolo_78}
<h2>Note di valutazione</h2>
<p>{esc(notes or 'Nessuna nota.').replace(chr(10), '<br>')}</p>
<p>***</p>
<p>{esc(closing).replace(chr(10), '<br>')}</p>
<h2>Firme componenti CVOI</h2>
{signatures}
"""
    return practice_doc_shell("Verbale di valutazione del progetto - CVOI", practice, sections, ai_generated=False)


DECISION_OUTCOMES = [
    ("approvata", "Approvata - prosecuzione iter"),
    ("approvata_condizioni", "Approvata con condizioni"),
    ("sospesa", "Sospesa - richiesta revisioni/integrazioni"),
    ("respinta", "Rigetto"),
]
DECISION_OUTCOME_LABELS = dict(DECISION_OUTCOMES)
BOARD_VOTE_LABELS = {
    "approva": "Favorevole",
    "contrario": "Contrario",
    "astenuto": "Astenuto",
    "in_attesa": "In attesa",
}
ADVISORY_OUTCOMES = [
    ("favorevole", "Favorevole"),
    ("favorevole_condizioni", "Favorevole con raccomandazioni"),
    ("sospensivo", "Sospensivo / richiesta integrazioni"),
    ("contrario", "Contrario"),
]
ADVISORY_OUTCOME_LABELS = dict(ADVISORY_OUTCOMES)


def set_practice_status(conn, practice, to_status, actor_id, notes="", conditions=""):
    conn.execute(
        "UPDATE practices SET status = ?, updated_at = ? WHERE id = ?",
        (to_status, now_iso(), practice["id"]),
    )
    conn.execute(
        """INSERT INTO practice_status_history(practice_id, from_status, to_status, actor_id, notes, conditions, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (practice["id"], practice["status"], to_status, actor_id, notes, conditions, now_iso()),
    )
    log_audit(conn, practice["platform_id"], actor_id, "practice", practice["id"],
              f"Stato: {practice_status_label(to_status)}", notes)


def cvoi_is_validated(conn, practice_id):
    """CVOI utilizzabile per l'Advisory Committee: nel modello collegiale il segnale
    e' la trasmissione del fascicolo M7 (3.3, m_advisory_trasmesso); resta valido anche
    il vecchio flusso (versione unanime / validazione admin)."""
    trasm = conn.execute(
        "SELECT 1 FROM practices WHERE id = ? AND m_advisory_trasmesso IS NOT NULL AND m_advisory_trasmesso != '' LIMIT 1",
        (practice_id,),
    ).fetchone()
    if trasm:
        return True
    return conn.execute(
        "SELECT 1 FROM cvoi_reports WHERE practice_id = ? AND (workflow_status = 'unanime' OR review_status = 'validato') LIMIT 1",
        (practice_id,),
    ).fetchone() is not None


def cvoi_committee_members(conn):
    return conn.execute("SELECT id, name FROM users WHERE role = 'technical_committee' AND active = 1 ORDER BY id").fetchall()


def board_members(conn):
    return conn.execute("SELECT id, name FROM users WHERE role = 'board' AND active = 1 ORDER BY id").fetchall()


def advisory_members(conn):
    return conn.execute("SELECT id, name FROM users WHERE role = 'covi' AND active = 1 ORDER BY id").fetchall()


def recompute_cvoi_unanime(conn, report_id):
    """Imposta workflow_status='unanime' se tutti i membri del Comitato Tecnico hanno approvato e firmato."""
    members = cvoi_committee_members(conn)
    if not members:
        return False
    reviews = {r["user_id"]: r for r in conn.execute(
        "SELECT user_id, status, signed_at FROM cvoi_member_reviews WHERE cvoi_report_id = ?", (report_id,)
    ).fetchall()}
    for m in members:
        rv = reviews.get(m["id"])
        if not rv or rv["status"] != "approvato" or not rv["signed_at"]:
            conn.execute("UPDATE cvoi_reports SET workflow_status = 'in_revisione' WHERE id = ? AND workflow_status = 'unanime'", (report_id,))
            return False
    conn.execute("UPDATE cvoi_reports SET workflow_status = 'unanime' WHERE id = ?", (report_id,))
    return True


def board_decision_outcome(conn, practice_id, round_no):
    r = conn.execute(
        "SELECT outcome FROM practice_board_decisions WHERE practice_id = ? AND decision_round = ? ORDER BY id DESC LIMIT 1",
        (practice_id, round_no),
    ).fetchone()
    return r["outcome"] if r else None


def advisory_is_expressed(conn, practice_id):
    return conn.execute(
        "SELECT 1 FROM advisory_opinions WHERE practice_id = ? LIMIT 1", (practice_id,)
    ).fetchone() is not None


def advisory_is_unanime(conn, practice_id):
    return conn.execute(
        "SELECT 1 FROM advisory_opinions WHERE practice_id = ? AND workflow_status = 'unanime' LIMIT 1", (practice_id,)
    ).fetchone() is not None


def recompute_advisory_unanime(conn, advisory_id):
    members = advisory_members(conn)
    if not members:
        return False
    reviews = {r["user_id"]: r for r in conn.execute(
        "SELECT user_id, status, signed_at FROM advisory_member_reviews WHERE advisory_opinion_id = ?", (advisory_id,)
    ).fetchall()}
    for m in members:
        rv = reviews.get(m["id"])
        if not rv or rv["status"] != "approvato" or not rv["signed_at"]:
            conn.execute("UPDATE advisory_opinions SET workflow_status = 'in_revisione' WHERE id = ? AND workflow_status = 'unanime'", (advisory_id,))
            return False
    conn.execute("UPDATE advisory_opinions SET workflow_status = 'unanime' WHERE id = ?", (advisory_id,))
    return True


DOCS_SEMPRE_DOVUTI = [
    "Presentazione del progetto imprenditoriale",
    "Visura camerale aggiornata",
    "Statuto",
    "Informazioni operazione (fabbisogno, valutazione, condizioni)",
    "Key manager ed esperienze",
]
DOCS_SE_DISPONIBILI = [
    "Ultimi due bilanci depositati",
    "Piano finanziario storico + proiezioni a 3 anni",
    "Sito web",
]


def _practice_val(practice, key, default=""):
    try:
        return practice[key] if key in practice.keys() else default
    except (IndexError, KeyError):
        return default


def kiis_missing_fields(practice):
    """Campi della bozza KIIS non ancora valorizzati dal patrimonio informativo."""
    try:
        js = json.loads(practice["dossier_json"] or "{}")
    except (ValueError, TypeError):
        js = {}
    off = (js.get("dati_struttura") or {}).get("offertaFase1") or {}
    checks = [
        ("Importo obiettivo (target)", off.get("importoTarget")),
        ("Importo massimo", off.get("importoMax")),
        ("Valutazione pre-money", off.get("preMoney")),
        ("Quota offerta (equity)", off.get("equity")),
        ("Strumento finanziario", off.get("strumento")),
        ("Diritti dei soci / clausole", off.get("diritti")),
        ("Utilizzo dei proventi", off.get("useOfProceeds")),
    ]
    return [label for label, val in checks if not (val and str(val).strip())]


CONFLITTI_MERITO_LABELS = {"nessuno": "Nessun conflitto", "gestibile": "Gestibile (con misura)",
                          "non_gestibile": "Non gestibile (stop)"}
# Verifica del fornitore sulla KIIS (art. 23 Reg. UE 2020/1503)
KIIS_COERENZA_LABELS = {"coerente": "Coerente, corretta e completa",
                        "da_correggere": "Da correggere (segnalazione al proponente, art. 23 par. 12)",
                        "incoerente": "Incoerente/incompleta - bloccante"}


def fase3_gate(practice):
    """Gate di merito 3.1 -> 3.2 (art. 23): KIIS verificata COERENTE AND conflitti in {nessuno, gestibile}."""
    reasons = []
    cf = _practice_val(practice, "m_conflitti")
    if cf == "non_gestibile":
        reasons.append("conflitti NON gestibili: stop (non ammissione)")
    elif cf not in ("nessuno", "gestibile"):
        reasons.append("conflitti di merito non ancora valutati")
    kc = _practice_val(practice, "m_kiis_coerenza")
    if kc == "incoerente":
        reasons.append("KIIS incoerente/incompleta: bloccante")
    elif kc == "da_correggere":
        reasons.append("KIIS da correggere: segnalazione al proponente, in attesa di correzione")
    elif kc != "coerente":
        reasons.append("verifica KIIS del fornitore non ancora effettuata")
    return (not reasons), reasons


def fascicolo_completezza(conn, practice):
    """Completezza del fascicolo del proponente in Fase 2 (Allegato 5_1, sez. 5.1.e).

    - SEMPRE DOVUTI (presentazione, visura, statuto, informazioni operazione, key manager):
      la loro assenza rende il fascicolo incompleto -> richiesta di integrazione (C3).
    - CONDIZIONATI "se disponibili" (ultimi due bilanci, piano finanziario storico, sito web):
      l'assenza per indisponibilita' oggettiva NON e' bloccante ("non disponibile").
    - BILANCI: bilanci_dovuti = min(2, esercizi_chiusi); completi se presenti >= dovuti.
      Neo costituita (0 esercizi chiusi) -> 0 dovuti -> completi anche senza bilanci.
    - fascicolo_completo = tutti i sempre dovuti presenti AND bilanci_completi.
    """
    pid = practice["id"]
    docs = {d["label"]: d for d in conn.execute(
        "SELECT label, document_id FROM practice_documents WHERE practice_id = ? AND phase = 'fase1'", (pid,))}

    def present(label):
        d = docs.get(label)
        return bool(d and d["document_id"])

    sempre = [{"label": l, "present": present(l)} for l in DOCS_SEMPRE_DOVUTI]
    missing_sempre = [s["label"] for s in sempre if not s["present"]]

    keys = practice.keys()
    esercizi = practice["esercizi_chiusi"] if "esercizi_chiusi" in keys else None
    bil_pres = (practice["bilanci_presenti"] if "bilanci_presenti" in keys else 0) or 0
    bil_dovuti = min(2, esercizi) if esercizi is not None else None
    bilanci_completi = (bil_dovuti is not None) and (bil_pres >= bil_dovuti)

    cond = [{"label": l, "present": present(l)}
            for l in ("Piano finanziario storico + proiezioni a 3 anni", "Sito web")]

    fascicolo_completo = (not missing_sempre) and bilanci_completi
    integrazione = list(missing_sempre)
    if bil_dovuti is not None and bil_pres < bil_dovuti:
        manca = bil_dovuti - bil_pres
        integrazione.append(
            f"Ultimi {bil_dovuti} bilanci depositati (manca{'no' if manca != 1 else ''} {manca} di {bil_dovuti})")
    return {
        "sempre": sempre, "missing_sempre": missing_sempre, "condizionati": cond,
        "esercizi": esercizi, "esercizi_set": esercizi is not None,
        "bilanci_dovuti": bil_dovuti, "bilanci_presenti": bil_pres,
        "bilanci_completi": bilanci_completi,
        "fascicolo_completo": fascicolo_completo, "integrazione": integrazione,
    }


ONORAB_ROLE_LABELS = {
    "lr": "Legale rappresentante",
    "te": "Titolare effettivo",
    "both": "Legale rappresentante e titolare effettivo (stessa persona)",
}


def onorabilita_subjects(conn, practice_id):
    return conn.execute(
        "SELECT * FROM practice_onorabilita WHERE practice_id = ? "
        "ORDER BY CASE role WHEN 'both' THEN 0 WHEN 'lr' THEN 1 ELSE 2 END",
        (practice_id,),
    ).fetchall()


def onorabilita_status(conn, practice_id):
    """Stato dell'onorabilita' art. 5, par. 2, lett. a.

    - L'AUTODICHIARAZIONE di ciascun soggetto dovuto rende la pratica PROCEDIBILE
      (istruttoria, scoring, parere Advisory, delibera CdA): il casellario NON e'
      bloccante in queste fasi.
    - I CASELLARI di TUTTI i soggetti dovuti (1 se i ruoli coincidono, 2 se distinti)
      sono il riscontro documentale e diventano BLOCCANTI solo alla Fase 5: solo allora
      la pratica e' PUBBLICABILE.
    """
    subs = onorabilita_subjects(conn, practice_id)
    if not subs:
        return {"configured": False, "coincide": None, "subjects": [],
                "procedibile": False, "pubblicabile": False,
                "missing_autodich": [], "missing_casellario": [], "due_casellari": 0}
    coincide = any(s["role"] == "both" for s in subs)
    missing_ad = [ONORAB_ROLE_LABELS.get(s["role"], s["role"]) for s in subs if not s["autodich"]]
    missing_cas = [ONORAB_ROLE_LABELS.get(s["role"], s["role"]) for s in subs if not s["casellario"]]
    procedibile = not missing_ad
    return {"configured": True, "coincide": coincide, "subjects": subs,
            "procedibile": procedibile, "pubblicabile": procedibile and not missing_cas,
            "missing_autodich": missing_ad, "missing_casellario": missing_cas,
            "due_casellari": len(subs)}


def golive_blockers(conn, practice_id):
    """Elenco dei requisiti bloccanti ancora aperti per il go-live (spec sez. 22)."""
    blockers = []
    # Cancello di pubblicazione onorabilita' art. 5: tutti i casellari dovuti presenti.
    ob = onorabilita_status(conn, practice_id)
    if not ob["configured"]:
        blockers.append("Onorabilita' art. 5: definire i soggetti (legale rappresentante / titolare effettivo) e acquisire i casellari.")
    elif ob["missing_autodich"]:
        blockers.append("Onorabilita' art. 5: autodichiarazione mancante per " + ", ".join(ob["missing_autodich"]) + ".")
    elif ob["missing_casellario"]:
        blockers.append("Casellario giudiziale mancante per " + ", ".join(ob["missing_casellario"]) + " (richiesto per la pubblicazione).")
    if conn.execute(
        "SELECT 1 FROM practice_alerts WHERE practice_id = ? AND severity = 'bloccante' AND alert_status = 'aperto' LIMIT 1", (practice_id,)
    ).fetchone():
        blockers.append("Alert bloccanti aperti.")
    open_conditions = conn.execute(
        "SELECT COUNT(*) FROM pre_golive_conditions WHERE practice_id = ? AND priority = 'bloccante' AND cond_status NOT IN ('soddisfatta','non_applicabile')", (practice_id,)
    ).fetchone()[0]
    if open_conditions:
        blockers.append(f"Condizioni pre go-live bloccanti non soddisfatte: {open_conditions}.")
    missing = conn.execute(
        "SELECT COUNT(*) FROM practice_documents WHERE practice_id = ? AND phase = 'fase4' AND required = 1 AND doc_status <> 'verificato'", (practice_id,)
    ).fetchone()[0]
    if missing:
        blockers.append(f"Checklist Fase 4 incompleta: {missing} documenti non verificati.")
    if board_decision_outcome(conn, practice_id, 1) not in {"approvata", "approvata_condizioni"}:
        blockers.append("Manca la delibera CdA positiva.")
    if not advisory_is_expressed(conn, practice_id):
        blockers.append("Manca il parere Advisory Committee.")
    return blockers


def can_transition_practice(conn, practice, target):
    """Ritorna '' se la transizione e' ammessa, altrimenti il motivo del blocco."""
    if target not in PRACTICE_FLOW.get(practice["status"], set()):
        return "Transizione non ammessa dallo stato corrente."
    if target == "pronto_cvoi":
        n = conn.execute(
            "SELECT COUNT(*) FROM internal_reviews WHERE practice_id = ? AND review_status = 'validata'",
            (practice["id"],),
        ).fetchone()[0]
        if n < len(INTERNAL_REVIEW_TYPES):
            return "Completare e validare le verifiche interne (AML, conflitti, coerenza) prima di inviare al Comitato Tecnico."
    if target == "pronta_golive":
        blockers = golive_blockers(conn, practice["id"])
        if blockers:
            return blockers[0]
    return ""


PARITER_LEGAL_HEADER = ("PARITER EQUITY S.R.L. - Viale Parioli 39/c, 00197 Roma - capitale sociale "
                        "Euro 13.157,90 i.v. - C.F./P.IVA 02551670223 - REA RM-1768060")


def cvoi_summary_for(conn, practice_id):
    """Sintesi del CVOI piu' recente per i verbali a valle (None se assente)."""
    report = conn.execute(
        "SELECT * FROM cvoi_reports WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (practice_id,)
    ).fetchone()
    if not report:
        return None
    scores = {(s["area_key"], s["idx"]): s["raw_score"]
              for s in conn.execute("SELECT area_key, idx, raw_score FROM cvoi_criteria_scores WHERE cvoi_report_id = ?", (report["id"],)).fetchall()}
    areas = []
    for key, label, w, mx, thr in CVOI_AREAS:
        raw = sum(v for (k, _i), v in scores.items() if k == key)
        areas.append((label, raw, mx, w, round(raw * w, 2)))
    return {
        "weighted": report["weighted_score"],
        "outcome": report["outcome"],
        "data_valutazione": report["data_valutazione"] if "data_valutazione" in report.keys() else "",
        "areas": areas,
    }


def _cvoi_summary_html(cvoi):
    if not cvoi:
        return "<p class='muted'>Verbale CVOI non ancora disponibile.</p>"
    area_rows = "".join(
        f"<tr><td>{esc(label)}</td><td>{raw:g}/{int(mx)}</td><td>{int(w*100)}%</td><td>{wm:g}</td></tr>"
        for (label, raw, mx, w, wm) in cvoi["areas"]
    )
    return f"""<table>
  <tr><th>Punteggio complessivo (media ponderata)</th><td>{cvoi['weighted']:g}</td></tr>
  <tr><th>Soglia minima (First Screening)</th><td>{CVOI_OVERALL_THRESHOLD:g}</td></tr>
  <tr><th>Data valutazione CVOI</th><td>{esc(cvoi['data_valutazione'] or '-')}</td></tr>
</table>
<table>
  <tr><th>Area di valutazione</th><th>Punteggio</th><th>Peso</th><th>Media ponderata</th></tr>
  {area_rows}
</table>
<p>Esito CVOI: <strong>{esc(CVOI_OUTCOME_LABELS.get(cvoi['outcome'], cvoi['outcome']))}</strong>.</p>"""


def text_to_html(text):
    """Converte testo libero (bozza editabile) in HTML per il documento ufficiale."""
    blocks = re.split(r"\n\s*\n", (text or "").strip())
    return "".join("<p>" + esc(b).replace("\n", "<br>") + "</p>" for b in blocks if b.strip())


def wrap_practice_doc(title, practice, body_text):
    return practice_doc_shell(title, practice, text_to_html(body_text), ai_generated=False)


def _cvoi_summary_text(cvoi):
    if not cvoi:
        return "Verbale CVOI non ancora disponibile."
    lines = [f"Esito CVOI: {CVOI_OUTCOME_LABELS.get(cvoi['outcome'], cvoi['outcome'])} "
             f"- punteggio ponderato {cvoi['weighted']:g} su soglia {CVOI_OVERALL_THRESHOLD:g}."]
    for label, raw, mx, w, wm in cvoi["areas"]:
        lines.append(f"  - {label}: {raw:g}/{int(mx)} (peso {int(w*100)}%, ponderato {wm:g})")
    return "\n".join(lines)


def compose_decision_draft(practice, round_no, fields, cvoi, votes, extra_lines=None):
    """Bozza editabile del verbale CdA, precompilata con i dati caricati."""
    odg = ("Valutazione finale del progetto, presa d'atto del parere dell'Advisory Committee e deliberazione; "
           "autorizzazione alla prosecuzione verso il pre go-live")
    vote_lines = []
    if votes:
        for n, v in votes:
            vote_lines.append(f"  - {n}: {BOARD_VOTE_LABELS.get(v, v)}")
    fav = sum(1 for _n, v in (votes or []) if v == "approva")
    con = sum(1 for _n, v in (votes or []) if v == "contrario")
    ast = sum(1 for _n, v in (votes or []) if v == "astenuto")
    extra = "\n".join(f"{k}: {v}" for k, v in (extra_lines or []))
    parts = [
        PARITER_LEGAL_HEADER,
        "",
        f"VERBALE DI CONSIGLIO DI AMMINISTRAZIONE DEL {fields.get('meeting_date') or '__________'}",
        "",
        ("Il giorno indicato, in modalita' mista, si e' riunito il Consiglio di Amministrazione di Pariter Equity "
         f"S.r.l. per discutere e deliberare in merito al progetto \"{practice['project_title']}\" del proponente "
         f"{practice['proponent_name'] or '-'}."),
        "",
        f"Ordine del giorno: {odg}.",
        "",
        f"Presenti: {fields.get('attendees') or '________________________'}.",
        "",
        "Esito dell'istruttoria CVOI:",
        _cvoi_summary_text(cvoi),
    ]
    if extra:
        parts += ["", extra]
    parts += [
        "",
        ("Il Responsabile della funzione di controllo rappresenta che il Proponente ha depositato le autodichiarazioni "
         "richieste e che le verifiche su conflitti di interesse e art. 5 Reg. UE 1503/2020 hanno dato esito negativo "
         "(assenza di motivi ostativi). Si ricorda che, in assenza dei casellari giudiziali dell'organo amministrativo "
         "del Proponente e della delibera di aumento di capitale, il progetto non potra' essere pubblicato."),
        "",
        "Esiti del voto:",
        *(vote_lines or ["  - (nessun voto registrato)"]),
        f"Favorevoli: {fav} - Contrari: {con} - Astenuti: {ast}.",
        "",
        f"Il Consiglio, dopo ampio confronto, DELIBERA: {DECISION_OUTCOME_LABELS.get(fields.get('outcome'), '____________')}.",
        "",
        (fields.get("summary") or ""),
        "",
        f"Condizioni: {fields.get('conditions') or 'nessuna'}.",
        "",
        "Letto, approvato e sottoscritto. La seduta e' sciolta.",
        "",
        "Il Presidente _____________________     Il Segretario _____________________",
    ]
    return "\n".join(parts)


def compose_advisory_draft(practice, fields, cvoi):
    """Bozza editabile del parere Advisory, precompilata e fedele al template."""
    parts = [
        "ADVISORY COMMITTEE - PARERE NON VINCOLANTE AL CONSIGLIO DI AMMINISTRAZIONE",
        f"Progetto: {practice['project_title']}",
        f"Proponente: {practice['proponent_name'] or '-'}",
        f"Data: {fields.get('meeting_date') or '__________'}",
        "",
        "1. Premessa e perimetro del presente parere",
        ("Il presente documento costituisce il parere dell'Advisory Committee, reso ai sensi della procedura di selezione "
         "(Allegato 5.1), ed e' obbligatorio ma non vincolante ai fini delle determinazioni del Consiglio di Amministrazione."),
        "",
        "2. Riferimenti procedurali e documentazione esaminata",
        ("L'Advisory Committee ha esaminato il fascicolo e la documentazione istruttoria e il verbale di valutazione del "
         "Comitato Valutazione Opportunita' di Investimento (CVOI), con autonomia e indipendenza di giudizio."),
        "",
        "3. Sintesi dell'esito del First Screening (CVOI)",
        _cvoi_summary_text(cvoi),
        "",
        "4. Valutazioni qualitative e profili di rischio",
        (fields.get("summary") or "Si rinvia alle note di valutazione del CVOI."),
        "",
        "5. Verifiche di processo: conflitti di interesse e coerenza informativa",
        ("All'esito dell'esame, l'Advisory Committee non ha rilevato profili ostativi per ragioni di conflitto di interessi; "
         "resta ferma la necessita' che il CdA espleti le valutazioni finali."),
        "",
        "6. Osservazioni e raccomandazioni al CdA (parere non vincolante)",
        (f"L'Advisory Committee esprime: {ADVISORY_OUTCOME_LABELS.get(fields.get('outcome'), fields.get('outcome'))}. "
         "Si raccomanda di condizionare l'eventuale pubblicazione all'acquisizione integrale della documentazione richiesta "
         "e, in particolare, all'invio del casellario giudiziale di tutti i membri dell'organo amministrativo del Proponente."),
        (fields.get("conditions") or ""),
        "",
        "7. Conclusione",
        ("Il presente parere e' reso in forma non vincolante e viene trasmesso al CdA unitamente al fascicolo di valutazione "
         "e alle tabelle di punteggio, per le determinazioni di competenza."),
        "",
        f"Per l'Advisory Committee: {fields.get('attendees') or '_____________________  _____________________'}",
    ]
    return "\n".join(parts)


def build_decision_html(practice, round_no, fields, cvoi, extra_lines=None, votes=None):
    """Verbale di Consiglio di Amministrazione (fedele al template Pariter)."""
    round_label = "Delibera CdA (definitiva)"
    extra_html = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in (extra_lines or []))
    outcome_label = DECISION_OUTCOME_LABELS.get(fields.get("outcome"), fields.get("outcome"))
    votes_html = ""
    if votes:
        vrows = "".join(f"<tr><td>{esc(n)}</td><td>{esc(BOARD_VOTE_LABELS.get(v, v))}</td></tr>" for n, v in votes)
        favor = sum(1 for _n, v in votes if v == "approva")
        contr = sum(1 for _n, v in votes if v == "contrario")
        asten = sum(1 for _n, v in votes if v == "astenuto")
        votes_html = f"""
<h2>Esiti del voto</h2>
<table><tr><th>Consigliere</th><th>Voto</th></tr>{vrows}</table>
<p>Favorevoli: {favor} &middot; Contrari: {contr} &middot; Astenuti: {asten}.</p>"""
    sections = f"""
<p class="meta">{esc(PARITER_LEGAL_HEADER)}</p>
<h2>Verbale di Consiglio di Amministrazione del {esc(fields.get('meeting_date') or '-')}</h2>
<p>Il Consiglio di Amministrazione della Societa' si e' riunito in modalita' mista per discutere e deliberare
in merito alla pratica di equity crowdfunding <strong>{esc(practice['project_title'])}</strong>
del proponente {esc(practice['proponent_name'] or '-')}.</p>
<h2>Presenti</h2>
<p>{esc(fields.get('attendees') or '-').replace(chr(10), '<br>')}</p>
<h2>Ordine del giorno</h2>
<p>{esc(fields.get('agenda') or '-').replace(chr(10), '<br>')}</p>
<h2>Esito istruttoria CVOI</h2>
{_cvoi_summary_html(cvoi)}
{('<h2>Riferimenti</h2><table>' + extra_html + '</table>') if extra_html else ''}
<h2>Presa d'atto delle verifiche</h2>
<p>Il Responsabile della funzione di controllo rappresenta che il Proponente ha depositato le autodichiarazioni
richieste (rispetto limiti art. 1 par. 2 lett. c Reg. UE 1503/2020; correttezza e completezza dei dati) e che le
verifiche sul conflitto di interessi e sull'art. 5 del Reg. UE 1503/2020 hanno dato esito negativo (assenza di
motivi ostativi). Si ricorda che, in assenza dei casellari giudiziali di tutti i membri dell'organo amministrativo
del Proponente e della delibera di aumento di capitale, il progetto non potra' essere pubblicato.</p>
{votes_html}
<h2>Delibera</h2>
<p>Il Consiglio, dopo ampio confronto, <strong>DELIBERA: {esc(outcome_label)}</strong>.</p>
<p>{esc(fields.get('summary') or '').replace(chr(10), '<br>')}</p>
<h2>Condizioni</h2>
<p>{esc(fields.get('conditions') or 'Nessuna condizione.').replace(chr(10), '<br>')}</p>
<p>Letto, approvato e sottoscritto. La seduta e' sciolta.</p>
<h2>Firme</h2>
<p class="muted">Il Presidente _____________________ &nbsp;&nbsp; Il Segretario _____________________</p>
"""
    return practice_doc_shell(round_label, practice, sections, ai_generated=False)


def build_advisory_html(practice, fields, cvoi):
    """Parere Advisory Committee non vincolante (fedele al template)."""
    outcome_label = ADVISORY_OUTCOME_LABELS.get(fields.get("outcome"), fields.get("outcome"))
    sections = f"""
<h2>1. Premessa e perimetro del presente parere</h2>
<p>Il presente documento costituisce il parere dell'Advisory Committee, reso ai sensi della procedura di selezione
(Allegato 5.1), ed e' <strong>obbligatorio ma non vincolante</strong> ai fini delle determinazioni del Consiglio di
Amministrazione.</p>
<h2>2. Riferimenti procedurali e documentazione esaminata</h2>
<p>L'Advisory Committee ha esaminato il fascicolo e la documentazione istruttoria predisposti dal team interno e il
verbale di valutazione del Comitato Valutazione Opportunita' di Investimento (CVOI), con autonomia e indipendenza di
giudizio, con particolare riguardo ai profili di conflitto di interessi e alla coerenza/correttezza/completezza delle
informazioni chiave sull'investimento.</p>
<h2>3. Sintesi dell'esito del First Screening (CVOI)</h2>
{_cvoi_summary_html(cvoi)}
<h2>4. Valutazioni qualitative e profili di rischio</h2>
<p>{esc(fields.get('summary') or 'Si rinvia alle note di valutazione del CVOI.').replace(chr(10), '<br>')}</p>
<h2>5. Verifiche di processo: conflitti di interesse e coerenza informativa</h2>
<p>All'esito dell'esame dei materiali ricevuti, l'Advisory Committee non ha rilevato, allo stato, profili ostativi o
incompatibilita' tali da suggerire il rigetto dell'iniziativa per ragioni di conflitto di interessi; resta ferma la
necessita' che il CdA, con il supporto del Responsabile delle funzioni di controllo, espleti le valutazioni finali.</p>
<h2>6. Osservazioni e raccomandazioni al CdA (parere non vincolante)</h2>
<p>L'Advisory Committee esprime <strong>{esc(outcome_label)}</strong>. Si raccomanda al CdA di prendere atto del
superamento della soglia minima di First Screening e di condizionare l'eventuale pubblicazione all'acquisizione
integrale della documentazione richiesta e, in particolare, all'invio del casellario giudiziale di tutti i membri
dell'organo amministrativo del Proponente.</p>
<p>{esc(fields.get('conditions') or '').replace(chr(10), '<br>')}</p>
<h2>7. Conclusione</h2>
<p>Il presente parere e' reso in forma non vincolante e viene trasmesso al CdA unitamente al fascicolo di valutazione
e alle tabelle di punteggio, per le determinazioni di competenza.</p>
<h2>Per l'Advisory Committee</h2>
<p class="muted">{esc(fields.get('attendees') or '').replace(chr(10), '<br>') or '_____________________ &nbsp; _____________________'}</p>
"""
    return practice_doc_shell("Advisory Committee - parere non vincolante al CdA", practice, sections, ai_generated=False)


CONDITION_STATUS_LABELS = {
    "aperta": "Aperta",
    "in_corso": "In lavorazione",
    "soddisfatta": "Soddisfatta",
    "non_applicabile": "Non piu' applicabile",
}

# Opzioni guidate del registro conflitti (valori suggeriti dal modello M12).
CONFLICT_TIPO_SOGGETTO = ["Soggetto Rilevante", "Proponente", "Cliente / Investitore", "Socio",
                          "Esponente aziendale", "Advisor / Collaboratore", "Altro"]
CONFLICT_NATURA = ["rapporto d'affari", "rapporto partecipativo", "parentela o affinita'",
                   "investimento di Soggetto Rilevante", "altro"]
CONFLICT_FONTE = ["segnalazione di dipendente/collaboratore", "dichiarazione annuale degli interessi",
                  "processo di selezione del progetto", "relazione di insussistenza dei conflitti", "altro"]
CONFLICT_FONDATEZZA = ["fondato", "non fondato"]
CONFLICT_GESTIBILITA = ["gestibile", "non gestibile"]
CONFLICT_MISURA = ["astensione del soggetto in CdA", "delibera di insussistenza di anomalie",
                   "informativa ai clienti con disclaimer", "non ammissione dell'iniziativa", "altro"]
CONFLICT_ESITO = ["in lavorazione", "in monitoraggio", "gestito", "non ammesso"]
CONFLICT_ATTI = ["verbale del CdA", "relazione di insussistenza dei conflitti", "disclaimer in piattaforma",
                 "aggiornamento del registro", "nessuno", "altro"]


def org_responsabile(platform_id, keywords, fallback=""):
    """Restituisce il soggetto incaricato (dall'organigramma) la cui funzione contiene una keyword."""
    assigns = rows(
        "SELECT function_name, subject_name, role FROM org_assignments WHERE platform_id = ? AND status = 'Attivo'",
        (platform_id,),
    )
    for kw in keywords:
        for a in assigns:
            if kw in (a["function_name"] or "").lower():
                return a["subject_name"], a["function_name"]
    return fallback, ""


def build_campaign_review_html(practice, review):
    js = {}
    try:
        js = json.loads(practice["dossier_json"] or "{}")
    except (ValueError, TypeError):
        js = {}
    offerta = (js.get("dati_struttura") or {}).get("offertaFase1") or {}
    sections = f"""
<h2>1. Oggetto</h2>
<p>Revisione della pagina campagna del progetto {esc(practice['project_title'])} ai fini della pubblicazione.</p>
<h2>2. Controlli di coerenza</h2>
<table>
  <tr><th>Coerenza con KIIS</th><td>Da confermare</td></tr>
  <tr><th>Coerenza con delibera aumento capitale</th><td>Da confermare</td></tr>
  <tr><th>Coerenza con business plan</th><td>Da confermare</td></tr>
  <tr><th>Equity / strumento dichiarati</th><td>{esc(offerta.get('equity') or '-')} / {esc(offerta.get('strumento') or '-')}</td></tr>
  <tr><th>Assenza di promesse di rendimento</th><td>{'Verificata' if review and review['no_yield_promise'] else 'Da verificare'}</td></tr>
</table>
<h2>3. Note di revisione</h2>
<p>{esc((review['coherence_notes'] if review else '') or 'Nessuna nota.').replace(chr(10), '<br>')}</p>
<h2>4. Esito</h2>
<p><strong>{esc((review['review_status'] if review else 'bozza').replace('_', ' '))}</strong></p>
"""
    return practice_doc_shell("Report revisione pagina campagna", practice, sections)


def build_practice_report_html(conn, practice):
    pid = practice["id"]
    docs = conn.execute("SELECT * FROM practice_documents WHERE practice_id = ? ORDER BY phase, id", (pid,)).fetchall()
    reviews = conn.execute("SELECT * FROM internal_reviews WHERE practice_id = ?", (pid,)).fetchall()
    cvoi = conn.execute("SELECT * FROM cvoi_reports WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    decisions = conn.execute("SELECT * FROM practice_board_decisions WHERE practice_id = ? ORDER BY decision_round", (pid,)).fetchall()
    advisory = conn.execute("SELECT * FROM advisory_opinions WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    conds = conn.execute("SELECT * FROM pre_golive_conditions WHERE practice_id = ? ORDER BY priority, id", (pid,)).fetchall()
    history = conn.execute("SELECT * FROM practice_status_history WHERE practice_id = ? ORDER BY id", (pid,)).fetchall()

    doc_rows = "".join(
        f"<tr><td>{esc((d['phase'] or '').upper())}</td><td>{esc(d['label'])}</td><td>{esc(DOC_STATUS_LABELS.get(d['doc_status'], d['doc_status']))}</td></tr>"
        for d in docs
    )
    review_rows = "".join(
        f"<tr><td>{esc(INTERNAL_REVIEW_LABELS.get(r['review_type'], r['review_type']))}</td><td>{esc(r['review_status'].replace('_',' '))}</td></tr>"
        for r in reviews
    ) or "<tr><td colspan='2' class='muted'>Nessuna relazione generata.</td></tr>"
    cvoi_html = (
        f"<p>Esito: <strong>{esc(CVOI_OUTCOME_LABELS.get(cvoi['outcome'], cvoi['outcome']))}</strong> &middot; punteggio {cvoi['weighted_score']:g} / soglia {CVOI_OVERALL_THRESHOLD:g} &middot; stato {esc(cvoi['review_status'])}</p>"
        if cvoi else "<p class='muted'>Report CVOI non generato.</p>"
    )
    dec_rows = "".join(
        f"<tr><td>{'Prima' if d['decision_round']==1 else 'Seconda'} delibera</td><td>{esc(DECISION_OUTCOME_LABELS.get(d['outcome'], d['outcome']))}</td><td>{esc(d['meeting_date'] or '-')}</td></tr>"
        for d in decisions
    ) or "<tr><td colspan='3' class='muted'>Nessuna delibera.</td></tr>"
    adv_html = (
        f"<p>Parere (non vincolante): <strong>{esc(ADVISORY_OUTCOME_LABELS.get(advisory['outcome'], advisory['outcome']))}</strong></p>"
        if advisory else "<p class='muted'>Parere Advisory non espresso.</p>"
    )
    cond_rows = "".join(
        f"<tr><td>{esc(c['description'])}</td><td>{esc(c['priority'])}</td><td>{esc(CONDITION_STATUS_LABELS.get(c['cond_status'], c['cond_status']))}</td></tr>"
        for c in conds
    ) or "<tr><td colspan='3' class='muted'>Nessuna condizione.</td></tr>"
    hist_rows = "".join(
        f"<tr><td>{esc((h['created_at'] or '')[:16])}</td><td>{esc(practice_status_label(h['to_status']))}</td><td>{esc(h['notes'] or '')}</td></tr>"
        for h in history
    ) or "<tr><td colspan='3' class='muted'>-</td></tr>"

    sections = f"""
<h2>Dati offerta</h2>
<table>
  <tr><th>Proponente</th><td>{esc(practice['proponent_name'] or '-')}</td></tr>
  <tr><th>Strumento</th><td>{esc(practice['instrument'] or '-')}</td></tr>
  <tr><th>Target / Massimo</th><td>{money(practice['target_amount'])} / {money(practice['max_amount'])}</td></tr>
  <tr><th>Pre-money / Equity</th><td>{money(practice['pre_money'])} / {esc(practice['equity_percent'] or '-')}</td></tr>
  <tr><th>Stato pratica</th><td>{esc(practice_status_label(practice['status']))}</td></tr>
</table>
<h2>Verifica documentale</h2>
<table><tr><th>Fase</th><th>Documento</th><th>Stato</th></tr>{doc_rows}</table>
<h2>Verifiche interne</h2>
<table><tr><th>Relazione</th><th>Stato</th></tr>{review_rows}</table>
<h2>CVOI</h2>
{cvoi_html}
<h2>Delibere CdA</h2>
<table><tr><th>Organo</th><th>Esito</th><th>Data</th></tr>{dec_rows}</table>
<h2>Advisory Committee</h2>
{adv_html}
<h2>Condizioni pre go-live</h2>
<table><tr><th>Condizione</th><th>Priorita'</th><th>Stato</th></tr>{cond_rows}</table>
<h2>Storico stati</h2>
<table><tr><th>Data</th><th>Stato</th><th>Note</th></tr>{hist_rows}</table>
"""
    return practice_doc_shell(f"Fascicolo istruttorio - {practice['project_title']}", practice, sections, ai_generated=False)


def build_opinion_html(deal, committee, reviewer, outcome, summary):
    return f"""<!doctype html>
<html lang="it">
<head><meta charset="utf-8"><title>{esc(committee)} - {esc(deal['title'])}</title></head>
<body>
<h1>{esc(committee)}</h1>
<h2>{esc(deal['title'])}</h2>
<p><strong>Proponente:</strong> {esc(deal['proponent_name'])}</p>
<p><strong>Relatore:</strong> {esc(reviewer['name'] if reviewer else 'Non indicato')}</p>
<p><strong>Esito:</strong> {esc(outcome)}</p>
<p><strong>Data generazione:</strong> {esc(now_iso())}</p>
<h3>Valutazione</h3>
<p>{esc(summary).replace(chr(10), '<br>')}</p>
</body>
</html>
"""


def build_board_html(deal, outcome, notes, integration_required):
    return f"""<!doctype html>
<html lang="it">
<head><meta charset="utf-8"><title>Delibera CdA - {esc(deal['title'])}</title></head>
<body>
<h1>Delibera CdA</h1>
<h2>{esc(deal['title'])}</h2>
<p><strong>Proponente:</strong> {esc(deal['proponent_name'])}</p>
<p><strong>Esito:</strong> {esc(outcome)}</p>
<p><strong>Integrazione documenti:</strong> {'Richiesta' if integration_required else 'Non richiesta'}</p>
<p><strong>Data:</strong> {esc(now_iso())}</p>
<h3>Note</h3>
<p>{esc(notes).replace(chr(10), '<br>')}</p>
</body>
</html>
"""


def build_iter_report(deal_id):
    deal = fetch_deal(deal_id)
    requirements = rows("SELECT * FROM deal_requirements WHERE deal_id = ? ORDER BY kind, category, id", (deal_id,))
    verifications = rows("SELECT * FROM verifications WHERE deal_id = ? ORDER BY id", (deal_id,))
    opinions = rows(
        """
        SELECT o.*, m.name AS reviewer_name, d.title AS document_title
        FROM committee_opinions o
        LEFT JOIN committee_members m ON m.id = o.reviewer_member_id
        LEFT JOIN documents d ON d.id = o.generated_document_id
        WHERE o.deal_id = ?
        ORDER BY o.created_at
        """,
        (deal_id,),
    )
    decisions = rows("SELECT * FROM board_decisions WHERE deal_id = ? ORDER BY created_at", (deal_id,))
    docs = rows("SELECT * FROM documents WHERE deal_id = ? ORDER BY created_at", (deal_id,))
    audit = rows(
        """
        SELECT a.*, u.name AS actor_name
        FROM audit_log a
        LEFT JOIN users u ON u.id = a.actor_id
        WHERE a.entity_type = 'deal' AND a.entity_id = ?
        ORDER BY a.created_at
        """,
        (deal_id,),
    )
    req_rows = "".join(
        f"<tr><td>{esc(r['category'])}</td><td>{esc(r['label'])}</td><td>{'Completato' if r['completed'] else 'Aperto'}</td></tr>"
        for r in requirements
    )
    ver_rows = "".join(
        f"<tr><td>{esc(v['area'])}</td><td>{esc(v['status'])}</td><td>{esc(v['result'])}</td></tr>"
        for v in verifications
    )
    op_rows = "".join(
        f"<tr><td>{esc(o['committee'])}</td><td>{esc(o['reviewer_name'])}</td><td>{esc(o['outcome'])}</td><td>{esc(o['document_title'])}</td></tr>"
        for o in opinions
    )
    dec_rows = "".join(
        f"<tr><td>{esc(d['created_at'])}</td><td>{esc(d['outcome'])}</td><td>{'Si' if d['integration_required'] else 'No'}</td><td>{esc(d['notes'])}</td></tr>"
        for d in decisions
    )
    doc_rows = "".join(
        f"<tr><td>{esc(d['created_at'])}</td><td>{esc(d['category'])}</td><td>{esc(d['title'])}</td><td>{'Generato' if d['generated'] else 'Caricato'}</td></tr>"
        for d in docs
    )
    audit_rows = "".join(
        f"<tr><td>{esc(a['created_at'])}</td><td>{esc(a['actor_name'])}</td><td>{esc(a['action'])}</td><td>{esc(a['details'])}</td></tr>"
        for a in audit
    )
    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<title>Report iter - {esc(deal['title'])}</title>
<style>
body {{ font-family: Arial, sans-serif; color: #1d2730; margin: 32px; }}
h1, h2 {{ margin-bottom: 4px; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0 28px; }}
th, td {{ border: 1px solid #d7dee5; padding: 8px; text-align: left; vertical-align: top; }}
th {{ background: #edf3f2; }}
.meta {{ color: #52616f; }}
</style>
</head>
<body>
<h1>Report iter deal</h1>
<h2>{esc(deal['title'])}</h2>
<p class="meta">Proponente: {esc(deal['proponent_name'])} - Fase: {esc(phase_label(deal['phase']))} - Stato: {esc(status_for_phase(deal['phase']))}</p>
<h2>Documentazione e integrazioni</h2><table><tr><th>Categoria</th><th>Elemento</th><th>Stato</th></tr>{req_rows}</table>
<h2>Verifiche</h2><table><tr><th>Area</th><th>Stato</th><th>Esito</th></tr>{ver_rows}</table>
<h2>Pareri comitati</h2><table><tr><th>Comitato</th><th>Relatore</th><th>Esito</th><th>Documento</th></tr>{op_rows}</table>
<h2>Delibere CdA</h2><table><tr><th>Data</th><th>Esito</th><th>Integrazione</th><th>Note</th></tr>{dec_rows}</table>
<h2>Documenti del fascicolo</h2><table><tr><th>Data</th><th>Categoria</th><th>Titolo</th><th>Origine</th></tr>{doc_rows}</table>
<h2>Audit trail</h2><table><tr><th>Quando</th><th>Chi</th><th>Azione</th><th>Dettagli</th></tr>{audit_rows}</table>
</body>
</html>
"""


class App(BaseHTTPRequestHandler):
    server_version = "ECSPSuite/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/static/"):
            self.serve_static(path)
            return
        if path == "/":
            self.page_dashboard()
        elif path == "/finance":
            self.page_finance()
        elif path == "/deals":
            self.page_deals()
        elif path == "/deals/new":
            self.page_deal_new()
        elif re.fullmatch(r"/deals/\d+", path):
            self.page_deal_detail(int(path.rsplit("/", 1)[1]))
        elif re.fullmatch(r"/deals/\d+/report", path):
            self.page_deal_report(int(path.split("/")[2]))
        elif path == "/pariter/comitato-tecnico":
            self.page_comitato_tecnico()
        elif path == "/pariter/team":
            self.page_team()
        elif path == "/pariter/practices/import":
            self.page_practice_import()
        elif re.fullmatch(r"/pariter/practices/\d+", path):
            self.page_practice_detail(int(path.rsplit("/", 1)[1]))
        elif re.fullmatch(r"/pariter/practices/\d+/report", path):
            self.page_practice_report(int(path.split("/")[3]))
        elif re.fullmatch(r"/pariter/practices/\d+/export", path):
            self.export_practice_zip(int(path.split("/")[3]))
        elif path == "/compagine":
            self.page_compagine()
        elif path == "/governance":
            self.page_governance()
        elif path == "/proponents":
            self.page_proponents()
        elif path == "/proponents/new":
            self.page_proponent_new()
        elif re.fullmatch(r"/proponents/\d+", path):
            self.page_proponent_detail(int(path.rsplit("/", 1)[1]))
        elif path == "/investors":
            self.page_investors()
        elif re.fullmatch(r"/investors/\d+", path):
            self.page_investor_detail(int(path.rsplit("/", 1)[1]))
        elif path == "/conflicts":
            self.page_conflicts()
        elif path == "/conflicts/export":
            self.export_conflicts_register()
        elif path == "/complaints":
            self.page_complaints()
        elif path == "/complaints/export":
            self.export_complaints_register()
        elif path == "/documents":
            self.page_documents()
        elif path in {"/person-documents/upload", "/supplier-contracts/upload"}:
            self.redirect("/compagine", self.get_ctx(), "Apri Compagine per collegare documenti a persone, fornitori e contratti.")
        elif re.fullmatch(r"/documents/\d+/download", path):
            self.download_document(int(path.split("/")[2]))
        elif path == "/communications":
            self.page_communications()
        elif path.startswith("/official-templates/"):
            self.download_official_template(path)
        elif path == "/assistant":
            self.page_assistant()
        elif path == "/architecture":
            self.page_architecture()
        else:
            self.not_found()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        form, files = self.parse_post()
        try:
            if path == "/deals/create":
                self.post_deal_create(form)
            elif re.fullmatch(r"/deals/\d+/requirement", path):
                self.post_requirement(int(path.split("/")[2]), form)
            elif re.fullmatch(r"/deals/\d+/verification", path):
                self.post_verification(int(path.split("/")[2]), form)
            elif re.fullmatch(r"/deals/\d+/opinion", path):
                self.post_opinion(int(path.split("/")[2]), form)
            elif re.fullmatch(r"/deals/\d+/board-decision", path):
                self.post_board_decision(int(path.split("/")[2]), form)
            elif re.fullmatch(r"/deals/\d+/transition", path):
                self.post_transition(int(path.split("/")[2]), form)
            elif re.fullmatch(r"/deals/\d+/upload", path):
                self.post_deal_upload(int(path.split("/")[2]), form, files)
            elif re.fullmatch(r"/deals/\d+/generate-report", path):
                self.post_generate_report(int(path.split("/")[2]), form)
            elif path == "/pariter/practices/import":
                self.post_practice_import(form, files)
            elif path == "/pariter/team/save":
                self.post_team_save(form)
            elif path == "/pariter/team/upload":
                self.post_team_upload(form, files)
            elif path == "/pariter/team/firma-draw":
                self.post_team_firma_draw(form)
            elif re.fullmatch(r"/pariter/practices/\d+/document-status", path):
                self.post_practice_document_status(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/onorabilita", path):
                self.post_practice_onorabilita(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/completezza", path):
                self.post_practice_completezza(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/phase", path):
                self.post_practice_phase(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/intake", path):
                self.post_practice_intake(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/step", path):
                self.post_practice_step(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/email", path):
                self.post_practice_email(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/document-upload", path):
                self.post_practice_document_upload(int(path.split("/")[3]), form, files)
            elif re.fullmatch(r"/pariter/practices/\d+/internal-review", path):
                self.post_practice_internal_review(int(path.split("/")[3]), form, files)
            elif re.fullmatch(r"/pariter/practices/\d+/integration-request", path):
                self.post_practice_integration_request(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/alert", path):
                self.post_practice_alert(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/cvoi", path):
                self.post_practice_cvoi(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/cvoi-member", path):
                self.post_practice_cvoi_member(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/close", path):
                self.post_practice_close(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/board-decision", path):
                self.post_practice_board_decision(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/cda-convoca", path):
                self.post_practice_cda_convoca(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/cda-verbale", path):
                self.post_practice_cda_verbale(int(path.split("/")[3]), form, files)
            elif re.fullmatch(r"/pariter/practices/\d+/kiis", path):
                self.post_practice_kiis(int(path.split("/")[3]), form, files)
            elif re.fullmatch(r"/pariter/practices/\d+/merito", path):
                self.post_practice_merito(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/trasmetti-advisory", path):
                self.post_practice_trasmetti_advisory(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/fascicolo-firma", path):
                self.post_practice_fascicolo_firma(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/validate-ammissibilita", path):
                self.post_practice_validate_ammissibilita(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/anagrafica", path):
                self.post_practice_anagrafica(int(path.split("/")[3]), form)
            elif path == "/pariter/practices/create-manual":
                self.post_practice_create_manual(form)
            elif re.fullmatch(r"/pariter/practices/\d+/advisory", path):
                self.post_practice_advisory(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/advisory-member", path):
                self.post_practice_advisory_member(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/condition", path):
                self.post_practice_condition(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/transition", path):
                self.post_practice_transition(int(path.split("/")[3]), form)
            elif re.fullmatch(r"/pariter/practices/\d+/campaign-review", path):
                self.post_practice_campaign_review(int(path.split("/")[3]), form)
            elif path == "/proponents/create":
                self.post_proponent_create(form)
            elif re.fullmatch(r"/proponents/\d+/update", path):
                self.post_proponent_update(int(path.split("/")[2]), form)
            elif path == "/committee/create":
                self.post_committee_create(form)
            elif path == "/shareholders/create":
                self.post_shareholder_create(form)
            elif re.fullmatch(r"/shareholders/\d+/update", path):
                self.post_shareholder_update(int(path.split("/")[2]), form)
            elif re.fullmatch(r"/shareholders/\d+/document-upload", path):
                self.post_shareholder_document_upload(int(path.split("/")[2]), form, files)
            elif path == "/compagine/function-save":
                self.post_org_function_save(form)
            elif path == "/compagine/assignment-save":
                self.post_org_assignment_save(form, files)
            elif path == "/compagine/assignment-delete":
                self.post_org_assignment_delete(form)
            elif path == "/governance/meeting-create":
                self.post_board_meeting_create(form)
            elif path == "/person-documents/upload":
                self.post_person_document_upload(form, files)
            elif path == "/supplier-contracts/upload":
                self.post_supplier_contract_upload(form, files)
            elif path == "/investors/create":
                self.post_investor_create(form)
            elif re.fullmatch(r"/investors/\d+/update", path):
                self.post_investor_update(int(path.split("/")[2]), form)
            elif path == "/conflicts/create":
                self.post_conflict_create(form)
            elif path == "/conflicts/update":
                self.post_conflict_update(form)
            elif path == "/complaints/create":
                self.post_complaint_create(form)
            elif path == "/complaints/update":
                self.post_complaint_update(form)
            elif path == "/communications/generate":
                self.post_communication_generate(form)
            elif path == "/communications/output-status":
                self.post_communication_output_status(form)
            elif path == "/communications/output-delete":
                self.post_communication_output_delete(form)
            elif path == "/documents/upload":
                self.post_document_upload(form, files)
            elif path in {"/finance/cost-create", "/finance/cost-save"}:
                self.post_finance_cost_save(form)
            elif path == "/finance/contract-update":
                self.post_finance_contract_update(form)
            elif path == "/finance/contract-to-manual":
                self.post_finance_contract_to_manual(form)
            elif path == "/finance/campaign-update":
                self.post_campaign_update(form)
            else:
                self.not_found()
        except Exception as exc:  # keep prototype failures visible
            self.error_page(exc)

    def parse_post(self):
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data"):
            fs = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype},
            )
            form = {}
            files = {}
            keys = fs.keys() if hasattr(fs, "keys") else []
            for key in keys:
                item = fs[key]
                if isinstance(item, list):
                    item = item[0]
                if getattr(item, "filename", None):
                    files[key] = item
                else:
                    form[key] = item.value
            return form, files
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(raw, keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items()}, {}

    def get_ctx(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        platforms = rows("SELECT * FROM platforms ORDER BY id")
        users = rows("SELECT * FROM users WHERE active = 1 ORDER BY id")
        platform_id = int((params.get("platform") or [platforms[0]["id"]])[0])
        user_id = int((params.get("user") or [users[0]["id"]])[0])
        platform = row("SELECT * FROM platforms WHERE id = ?", (platform_id,)) or platforms[0]
        user = row("SELECT * FROM users WHERE id = ?", (user_id,)) or users[0]
        notice = (params.get("notice") or [""])[0]
        return {
            "platform_id": platform["id"],
            "user_id": user["id"],
            "platform": platform,
            "user": user,
            "platforms": platforms,
            "users": users,
            "notice": notice,
            "path": parsed.path,
        }

    def ctx_from_form(self, form):
        platform_id = int(form.get("platform") or 1)
        user_id = int(form.get("user") or 1)
        platform = row("SELECT * FROM platforms WHERE id = ?", (platform_id,))
        user = row("SELECT * FROM users WHERE id = ?", (user_id,))
        return {"platform_id": platform_id, "user_id": user_id, "platform": platform, "user": user}

    def send_html(self, html_doc, status=200):
        body = html_doc.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, path, ctx, notice="", extra=None):
        params = {"platform": ctx["platform_id"], "user": ctx["user_id"]}
        if extra:
            params.update(extra)
        if notice:
            params["notice"] = notice
        self.send_response(303)
        self.send_header("Location", f"{path}?{urlencode(params)}")
        self.end_headers()

    def serve_static(self, path):
        rel = path.removeprefix("/static/").strip("/")
        file_path = (STATIC_DIR / rel).resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())) or not file_path.exists():
            self.not_found()
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(file_path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def render(self, title, body, active):
        ctx = self.get_ctx()
        role_label = ROLE_LABELS.get(ctx["user"]["role"], ctx["user"]["role"])
        nav_items = [
            ("Dashboard", "/", "dashboard"),
            ("Compagine", "/compagine", "compagine"),
            ("Finance", "/finance", "finance"),
            ("Governance", "/governance", "governance"),
            ("Deal", "/deals", "deals"),
            ("Proponenti", "/proponents", "proponents"),
            ("Investitori", "/investors", "investors"),
            ("Conflitti d'int.", "/conflicts", "conflicts"),
            ("Reclami", "/complaints", "complaints"),
            ("Comunicazioni", "/communications", "communications"),
            ("Documenti", "/documents", "documents"),
            ("Assistente IA", "/assistant", "assistant"),
        ]
        # (La voce "Team" e' stata integrata nel fascicolo persona di Compagine: CV,
        # documento d'identita e firma si gestiscono dall'anagrafica del soggetto.)
        nav = "".join(
            f'<a class="nav-link {"active" if key == active else ""}" href="{rel_url(href, ctx)}">{label}</a>'
            for label, href, key in nav_items
        )
        platform_switch = "".join(
            f'<a class="platform-tab {"active" if p["id"] == ctx["platform_id"] else ""}" href="{rel_url(ctx["path"], ctx, {"platform": p["id"]})}">{esc(p["name"])}</a>'
            for p in ctx["platforms"]
        )
        # Amministratore in cima; etichetta con il ruolo per orientarsi sulle viste
        u_sorted = sorted(ctx["users"], key=lambda u: (0 if u["role"] == "admin" else 1, u["id"]))

        def _ulabel(u):
            rl = ROLE_LABELS.get(u["role"], u["role"])
            return u["name"] if u["name"] == rl else f"{u['name']} ({rl})"
        user_opts = "".join(
            f'<option value="{u["id"]}"{" selected" if u["id"] == ctx["user_id"] else ""}>{esc(_ulabel(u))}</option>'
            for u in u_sorted)
        notice = f'<div class="notice">{esc(ctx["notice"])}</div>' if ctx["notice"] else ""
        html_doc = f"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} - OmniCrowd</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <header class="app-header">
    <div class="shell header-inner">
      <div class="brand-copy">
        <p class="eyebrow">OmniCrowd - ECSP - Reg. (UE) 2020/1503</p>
        <h1>OmniCrowd</h1>
        <p>The all-in-one crowdfunding operating system</p>
      </div>
      <div class="header-actions">
        <div class="platform-switch">{platform_switch}</div>
        <form class="user-menu" method="get" action="{esc(ctx['path'])}">
          <input type="hidden" name="platform" value="{ctx['platform_id']}">
          <label><span>Utente</span><select name="user" data-autosubmit>{user_opts}</select></label>
          <span class="role-pill">{esc(role_label)}</span>
          <button class="logout-button" type="button">Esci</button>
        </form>
      </div>
    </div>
    <nav class="top-nav"><div class="shell nav-row">{nav}</div></nav>
  </header>
  <main class="main">
    <div class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">{esc(ctx['platform']['regulator_profile'])}</p>
        <h1>{esc(title)}</h1>
      </div>
    </header>
    {notice}
    {body}
    </div>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>"""
        self.send_html(html_doc)

    def page_dashboard(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        metrics = row("SELECT * FROM platform_metrics WHERE platform_id = ?", (pid,))
        cost_rows = rows(
            """
            SELECT sc.*, s.name AS supplier_name, s.service_area
            FROM supplier_contracts sc
            JOIN suppliers s ON s.id = sc.supplier_id
            WHERE sc.platform_id = ?
              AND sc.status IN ('Attivo', 'In rinnovo', 'Da firmare')
            ORDER BY sc.end_date = '', sc.end_date, sc.title
            """,
            (pid,),
        )
        insurance_cost = sum(
            float(c["value"] or 0)
            for c in cost_rows
            if "assic" in " ".join([c["title"] or "", c["contract_type"] or "", c["service_area"] or ""]).lower()
        )
        supplier_cost = sum(float(c["value"] or 0) for c in cost_rows) - insurance_cost
        consob_fee = 0
        people_cost = 0
        known_structural_cost = supplier_cost + insurance_cost + consob_fee + people_cost
        estimated_revenue = 0
        break_even_gap = max(known_structural_cost - estimated_revenue, 0)
        cost_detail_rows = "".join(
            f"""<tr>
                <td>{esc(c['title'])}<br><span class="muted">{esc(c['supplier_name'])}</span></td>
                <td>{esc(c['service_area'] or c['contract_type'])}</td>
                <td>{money(c['value'])}</td>
                <td>{esc(c['end_date'] or 'senza scadenza')}</td>
            </tr>"""
            for c in cost_rows[:5]
        ) or '<tr><td colspan="4" class="empty-state">Nessun contratto attivo censito.</td></tr>'
        counts = {
            "open_deals": row(
                "SELECT COUNT(*) AS c FROM deals WHERE platform_id = ? AND phase NOT IN ('pubblicato','raccolta_in_corso','concluso','respinta','archiviato')",
                (pid,),
            )["c"],
            "approval": row(
                "SELECT COUNT(*) AS c FROM deals WHERE platform_id = ? AND phase IN ('comitato_tecnico','covi','cda','contratto','pre_pubblicazione')",
                (pid,),
            )["c"],
            "published": row(
                "SELECT COUNT(*) AS c FROM deals WHERE platform_id = ? AND phase IN ('pubblicato','raccolta_in_corso')",
                (pid,),
            )["c"],
            "missing": row(
                """
                SELECT COUNT(*) AS c
                FROM deal_requirements r
                JOIN deals d ON d.id = r.deal_id
                WHERE d.platform_id = ? AND r.required = 1 AND r.completed = 0
                """,
                (pid,),
            )["c"],
        }
        task_rows = rows("SELECT * FROM compliance_tasks WHERE platform_id = ? ORDER BY due_date", (pid,))
        deals = rows(
            """
            SELECT d.*, p.name AS proponent_name
            FROM deals d JOIN proponents p ON p.id = d.proponent_id
            WHERE d.platform_id = ?
            ORDER BY d.updated_at DESC
            """,
            (pid,),
        )
        task_html = "".join(self.task_row(task) for task in task_rows)
        deal_html = "".join(
            f"""<tr>
                <td><a href="{rel_url('/deals/' + str(d['id']), ctx)}">{esc(d['title'])}</a></td>
                <td>{esc(d['proponent_name'])}</td>
                <td><span class="badge {badge_class(status_for_phase(d['phase']))}">{esc(status_for_phase(d['phase']))}</span></td>
                <td>{esc(phase_label(d['phase']))}</td>
                <td>{money(d['funding_target'])}</td>
            </tr>"""
            for d in deals
        )
        body = f"""
<section class="metric-grid">
  <div class="metric"><span>Deal in preparazione</span><strong>{counts['open_deals']}</strong></div>
  <div class="metric"><span>In approvazione</span><strong>{counts['approval']}</strong></div>
  <div class="metric"><span>Offerte online</span><strong>{counts['published']}</strong></div>
  <div class="metric"><span>Elementi mancanti</span><strong>{counts['missing']}</strong></div>
</section>
<section class="panel structural-cost-panel">
  <div class="section-head">
    <h2>Costi strutturali</h2>
    <span class="source-chip">Break even operativo</span>
  </div>
  <div class="compact-metrics structural-cost-metrics">
    <div><span>Tassa CONSOB</span><strong>{money(consob_fee)}</strong><small>Da collegare al tariffario applicabile</small></div>
    <div><span>Fornitori e outsourcing</span><strong>{money(supplier_cost)}</strong><small>{len(cost_rows)} contratti attivi o da firmare</small></div>
    <div><span>Assicurazione</span><strong>{money(insurance_cost)}</strong><small>Da contratti assicurativi collegati</small></div>
    <div><span>Persone e funzioni</span><strong>{money(people_cost)}</strong><small>Da compensi / accordi organigramma</small></div>
    <div><span>Costo strutturale annuo</span><strong>{money(known_structural_cost)}</strong><small>Totale parziale dei dati censiti</small></div>
    <div><span>Gap break even</span><strong>{money(break_even_gap)}</strong><small>Ricavi annui impostati: {money(estimated_revenue)}</small></div>
  </div>
  <table class="data-table compact structural-cost-table">
    <thead><tr><th>Voce collegata</th><th>Area</th><th>Costo annuo</th><th>Scadenza</th></tr></thead>
    <tbody>{cost_detail_rows}</tbody>
  </table>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head">
      <h2>Metriche piattaforma</h2>
      <span class="source-chip">{esc(metrics['source'] if metrics else 'adapter non collegato')}</span>
    </div>
    <div class="compact-metrics">
      <div><span>Offerte pubblicate</span><strong>{metrics['published_offers'] if metrics else 0}</strong></div>
      <div><span>Offerte in corso</span><strong>{metrics['active_offers'] if metrics else 0}</strong></div>
      <div><span>Investitori</span><strong>{metrics['investors'] if metrics else 0}</strong></div>
      <div><span>Raccolta</span><strong>{money(metrics['raised_amount'] if metrics else 0)}</strong></div>
    </div>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Scadenze e adempimenti</h2></div>
    <table class="data-table">
      <thead><tr><th>Area</th><th>Attivita</th><th>Scadenza</th><th>Stato</th></tr></thead>
      <tbody>{task_html}</tbody>
    </table>
  </div>
</section>
<section class="panel">
  <div class="section-head">
    <h2>Pipeline deal</h2>
    <a class="button secondary" href="{rel_url('/deals', ctx)}">Apri lista</a>
  </div>
  <table class="data-table">
    <thead><tr><th>Deal</th><th>Proponente</th><th>Stato</th><th>Fase</th><th>Target</th></tr></thead>
    <tbody>{deal_html}</tbody>
  </table>
</section>
"""
        self.render("Cruscotto", body, "dashboard")

    def task_row(self, task):
        due = date.fromisoformat(task["due_date"])
        days = (due - date.today()).days
        state = task["status"]
        if days < 0:
            state = "Scaduto"
        elif days <= 14 and state != "Scaduto":
            state = "Imminente"
        return f"""<tr>
            <td>{esc(task['area'])}</td>
            <td>{esc(task['title'])}</td>
            <td>{esc(nice_date(task['due_date']))}</td>
            <td><span class="badge {badge_class(state)}">{esc(state)}</span></td>
        </tr>"""

    def page_finance(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        params = parse_qs(urlparse(self.path).query)

        def to_int(value, default=0):
            try:
                return int(value or default)
            except (TypeError, ValueError):
                return default

        mode = (params.get("mode") or [""])[0]
        selected_cost_id = to_int((params.get("cost_id") or ["0"])[0])
        selected_contract_id = to_int((params.get("contract_id") or ["0"])[0])
        selected_deal_id = to_int((params.get("deal_id") or ["0"])[0])
        selected_update_id = to_int((params.get("update_id") or ["0"])[0])

        def annualized(amount, periodicity):
            value = float(amount or 0)
            return value * {"Mensile": 12, "Trimestrale": 4, "Semestrale": 2, "Annuale": 1, "Una tantum": 1}.get(periodicity or "Annuale", 1)

        def alert_class(due_date, status=""):
            if status in {"Pagato", "Chiuso"}:
                return "ok"
            try:
                days = (date.fromisoformat(due_date) - date.today()).days
            except (TypeError, ValueError):
                return "neutral"
            if days < 0:
                return "urgent"
            if days <= 30:
                return "soon"
            return "ok"

        def row_value(item, key, default=""):
            if item and key in item.keys() and item[key] is not None:
                return item[key]
            return default

        contract_costs = rows(
            """
            SELECT sc.*, s.name AS supplier_name, s.service_area, doc.title AS document_title
            FROM supplier_contracts sc
            JOIN suppliers s ON s.id = sc.supplier_id
            LEFT JOIN documents doc ON doc.id = sc.document_id
            WHERE sc.platform_id = ?
              AND sc.status IN ('Attivo', 'In rinnovo', 'Da firmare', 'Scaduto')
              AND sc.id NOT IN (
                  SELECT linked_contract_id
                  FROM finance_costs
                  WHERE platform_id = ?
                    AND linked_contract_id IS NOT NULL
                    AND status != 'Archiviato'
              )
            ORDER BY sc.end_date = '', sc.end_date, s.name
            """,
            (pid, pid),
        )
        manual_costs = rows(
            """
            SELECT fc.*, sc.title AS linked_contract_title, sc.document_id AS linked_document_id
            FROM finance_costs fc
            LEFT JOIN supplier_contracts sc ON sc.id = fc.linked_contract_id
            WHERE fc.platform_id = ?
            ORDER BY fc.due_date = '', fc.due_date, fc.category, fc.title
            """,
            (pid,),
        )
        campaigns = rows(
            """
            SELECT d.*, p.name AS proponent_name,
                   COALESCE(SUM(i.amount), 0) AS tracked_raised,
                   COUNT(DISTINCT i.investor_id) AS tracked_investors
            FROM deals d
            JOIN proponents p ON p.id = d.proponent_id
            LEFT JOIN investments i ON i.deal_id = d.id
            WHERE d.platform_id = ?
            GROUP BY d.id
            ORDER BY d.updated_at DESC
            """,
            (pid,),
        )
        updates = rows(
            "SELECT * FROM campaign_updates WHERE platform_id = ? ORDER BY as_of_date DESC, created_at DESC",
            (pid,),
        )
        update_history = rows(
            """
            SELECT cu.*, d.title AS deal_title
            FROM campaign_updates cu
            JOIN deals d ON d.id = cu.deal_id
            WHERE cu.platform_id = ?
            ORDER BY cu.as_of_date DESC, cu.created_at DESC
            LIMIT 12
            """,
            (pid,),
        )
        selected_cost = row(
            "SELECT * FROM finance_costs WHERE id = ? AND platform_id = ?",
            (selected_cost_id, pid),
        ) if selected_cost_id else None
        selected_contract = row(
            """
            SELECT sc.*, s.name AS supplier_name, s.service_area, doc.title AS document_title
            FROM supplier_contracts sc
            JOIN suppliers s ON s.id = sc.supplier_id
            LEFT JOIN documents doc ON doc.id = sc.document_id
            WHERE sc.id = ? AND sc.platform_id = ?
            """,
            (selected_contract_id, pid),
        ) if selected_contract_id else None
        selected_update = row(
            """
            SELECT cu.*, d.title AS deal_title, d.funding_target, d.platform_fee_percent
            FROM campaign_updates cu
            JOIN deals d ON d.id = cu.deal_id
            WHERE cu.id = ? AND cu.platform_id = ?
            """,
            (selected_update_id, pid),
        ) if selected_update_id else None
        if selected_update:
            selected_deal_id = selected_update["deal_id"]
        latest_update = {}
        for update in updates:
            latest_update.setdefault(update["deal_id"], update)

        contract_total = sum(float(c["value"] or 0) for c in contract_costs)
        manual_total = sum(annualized(c["amount"], c["periodicity"]) for c in manual_costs if c["status"] != "Archiviato")
        structural_total = contract_total + manual_total
        campaign_total = 0
        estimated_revenue = 0
        campaign_rows = []
        for deal in campaigns:
            latest = latest_update.get(deal["id"])
            raised = float(latest["raised_amount"] if latest and latest["raised_amount"] else deal["tracked_raised"] or 0)
            investors_count = int(latest["investors_count"] if latest and latest["investors_count"] else deal["tracked_investors"] or 0)
            target = float(deal["funding_target"] or 0)
            fee_percent = float(deal["platform_fee_percent"] if deal["platform_fee_percent"] is not None else 5)
            fee_amount = raised * fee_percent / 100
            progress = min(100, (raised / target) * 100) if target else 0
            progress_label = f"{progress:.1f}".rstrip("0").rstrip(".")
            campaign_total += raised
            estimated_revenue += fee_amount
            campaign_link_extra = {"mode": "campaign", "deal_id": deal["id"]}
            if latest:
                campaign_link_extra["update_id"] = latest["id"]
            campaign_rows.append(
                f"""<tr>
                  <td><a href="{rel_url('/deals/' + str(deal['id']), ctx)}"><strong>{esc(deal['title'])}</strong></a><br><span class="muted">{esc(deal['proponent_name'])}</span></td>
                  <td>{money(target)}</td>
                  <td>{money(raised)}<br><span class="muted">Avanzamento {progress_label}%</span><div class="finance-progress"><span style="width:{progress}%"></span></div></td>
                  <td>{fee_percent:.2f}%</td>
                  <td>{money(fee_amount)}</td>
                  <td>{investors_count}</td>
                  <td><span class="badge {badge_class(status_for_phase(deal['phase']))}">{esc(status_for_phase(deal['phase']))}</span><br><span class="muted">{esc(phase_label(deal['phase']))}</span></td>
                  <td>{esc(nice_date(latest['as_of_date']) if latest else 'investimenti/API')}</td>
                  <td><a class="button ghost" href="{rel_url('/finance', ctx, campaign_link_extra)}">Modifica</a></td>
                </tr>"""
            )
        break_even_gap = max(structural_total - estimated_revenue, 0)

        def contract_document_url(item):
            if item["document_id"]:
                return rel_url("/documents/" + str(item["document_id"]) + "/download", ctx)
            return rel_url("/documents", ctx, {"origin": "Contratto fornitore"})

        def manual_source_cell(item):
            if item["linked_contract_id"]:
                label = item["linked_contract_title"] or "contratto collegato"
                href = (
                    rel_url("/documents/" + str(item["linked_document_id"]) + "/download", ctx)
                    if item["linked_document_id"]
                    else rel_url("/finance", ctx, {"mode": "contract", "contract_id": item["linked_contract_id"]})
                )
                return f'<a href="{href}">Manuale</a><br><span class="muted">da {esc(label)}</span>'
            return esc(item["source"])

        contract_rows = "".join(
            f"""<tr>
              <td><strong>{esc(c['title'])}</strong><br><span class="muted">{esc(c['supplier_name'])}</span></td>
              <td>{esc(c['service_area'] or c['contract_type'])}</td>
              <td>{money(c['value'])}</td>
              <td><span class="deadline-dot {alert_class(c['end_date'], c['status'])}"></span>{esc(nice_date(c['end_date']) or 'senza scadenza')}</td>
              <td><span class="badge {badge_class(c['status'])}">{esc(c['status'])}</span></td>
              <td><a href="{contract_document_url(c)}">Contratto</a><br><span class="muted">{esc(c['document_title'] or 'documento da collegare')}</span></td>
              <td><div class="inline-actions">
                <a class="button ghost" href="{rel_url('/finance', ctx, {'mode': 'contract', 'contract_id': c['id']})}">Modifica</a>
                <form class="inline-action-form" method="post" action="/finance/contract-to-manual">
                  {hidden_ctx(ctx)}
                  <input type="hidden" name="contract_id" value="{c['id']}">
                  <button class="button secondary" type="submit">Manuale</button>
                </form>
              </div></td>
            </tr>"""
            for c in contract_costs
        )
        manual_rows = "".join(
            f"""<tr>
              <td><strong>{esc(c['title'])}</strong><br><span class="muted">{esc(c['notes'])}</span></td>
              <td>{esc(c['category'])}</td>
              <td>{money(c['amount'])}<br><span class="muted">{esc(c['periodicity'])} / annuo {money(annualized(c['amount'], c['periodicity']))}</span></td>
              <td><span class="deadline-dot {alert_class(c['due_date'], c['status'])}"></span>{esc(nice_date(c['due_date']) or 'senza scadenza')}</td>
              <td><span class="badge {badge_class(c['status'])}">{esc(c['status'])}</span></td>
              <td>{manual_source_cell(c)}</td>
              <td><a class="button ghost" href="{rel_url('/finance', ctx, {'mode': 'cost', 'cost_id': c['id']})}">Modifica</a></td>
            </tr>"""
            for c in manual_costs
        )
        all_cost_rows = contract_rows + manual_rows or '<tr><td colspan="7" class="empty-row">Nessun costo censito.</td></tr>'
        history_rows = "".join(
            f"""<tr>
              <td>{esc(nice_date(u['as_of_date']))}</td>
              <td><strong>{esc(u['deal_title'])}</strong></td>
              <td>{money(u['raised_amount'])}</td>
              <td>{esc(u['investors_count'])}</td>
              <td><span class="badge {badge_class(u['status'])}">{esc(u['status'])}</span></td>
              <td>{esc(u['source'])}</td>
              <td>{esc(u['notes'])}</td>
              <td><a class="button ghost" href="{rel_url('/finance', ctx, {'mode': 'campaign', 'update_id': u['id']})}">Modifica</a></td>
            </tr>"""
            for u in update_history
        ) or '<tr><td colspan="8" class="empty-row">Nessuna rilevazione manuale registrata.</td></tr>'
        selected_campaign = next((d for d in campaigns if d["id"] == selected_deal_id), None)
        if not selected_campaign and campaigns and mode == "campaign":
            selected_campaign = campaigns[0]
            selected_deal_id = selected_campaign["id"]
        deal_options = "".join(
            f'<option value="{d["id"]}" data-target="{esc(d["funding_target"])}" data-fee="{esc(d["platform_fee_percent"] if d["platform_fee_percent"] is not None else 5)}"{" selected" if d["id"] == selected_deal_id else ""}>{esc(d["title"])}</option>'
            for d in campaigns
        )
        close_url = rel_url("/finance", ctx)
        cost_modal = ""
        if mode == "cost":
            cost_title = row_value(selected_cost, "title")
            cost_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal finance-modal">
    <div class="section-head">
      <h2>{'Modifica costo' if selected_cost else 'Nuovo costo'}</h2>
      <a class="modal-close" href="{close_url}">x</a>
    </div>
    <form class="form-grid" method="post" action="/finance/cost-save">
      {hidden_ctx(ctx)}
      <input type="hidden" name="cost_id" value="{esc(selected_cost_id if selected_cost else '')}">
      <label>Titolo<input name="title" required value="{esc(cost_title)}" placeholder="es. Tassa CONSOB, assicurazione, advisor"></label>
      <label>Categoria<select name="category">{option_values(['Tassa autorita', 'Fornitore', 'Assicurazione', 'Personale / incarichi', 'Marketing', 'Altro costo'], row_value(selected_cost, 'category', 'Altro costo'))}</select></label>
      <label>Importo<input type="number" step="0.01" min="0" name="amount" required value="{esc(row_value(selected_cost, 'amount', ''))}"></label>
      <label>Periodicita<select name="periodicity">{option_values(['Annuale', 'Mensile', 'Trimestrale', 'Semestrale', 'Una tantum'], row_value(selected_cost, 'periodicity', 'Annuale'))}</select></label>
      <label>Scadenza<input type="date" name="due_date" value="{esc(row_value(selected_cost, 'due_date'))}"></label>
      <label>Stato<select name="status">{option_values(['Attivo', 'Da pagare', 'Pagato', 'Stimato', 'Archiviato'], row_value(selected_cost, 'status', 'Attivo'))}</select></label>
      <label class="full-span">Note<textarea name="notes" rows="3" placeholder="Fonte, contratto collegato, criterio di stima, rinnovo">{esc(row_value(selected_cost, 'notes'))}</textarea></label>
      <div class="form-actions left full-span"><button class="button primary" type="submit">Salva costo</button><a class="button secondary" href="{close_url}">Annulla</a></div>
    </form>
  </section>
</div>"""
        contract_modal = ""
        if mode == "contract" and selected_contract:
            contract_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal finance-modal">
    <div class="section-head">
      <h2>Modifica costo da contratto</h2>
      <a class="modal-close" href="{close_url}">x</a>
    </div>
    <p class="page-copy"><a href="{contract_document_url(selected_contract)}">Apri contratto</a> - {esc(selected_contract['document_title'] or 'documento non collegato, apri archivio contratti')}</p>
    <form class="form-grid" method="post" action="/finance/contract-update">
      {hidden_ctx(ctx)}
      <input type="hidden" name="contract_id" value="{esc(selected_contract['id'])}">
      <label>Titolo contratto<input name="title" required value="{esc(selected_contract['title'])}"></label>
      <label>Fornitore<input value="{esc(selected_contract['supplier_name'])}" readonly></label>
      <label>Area servizio<input name="service_area" value="{esc(selected_contract['service_area'])}"></label>
      <label>Tipo contratto<input name="contract_type" value="{esc(selected_contract['contract_type'])}"></label>
      <label>Costo annuo<input type="number" step="0.01" min="0" name="value" value="{esc(selected_contract['value'])}"></label>
      <label>Data inizio<input type="date" name="start_date" value="{esc(selected_contract['start_date'])}"></label>
      <label>Scadenza<input type="date" name="end_date" value="{esc(selected_contract['end_date'])}"></label>
      <label>Stato<select name="status">{option_values(['Attivo', 'In rinnovo', 'Da firmare', 'Scaduto', 'Chiuso', 'Archiviato'], selected_contract['status'])}</select></label>
      <label class="full-span">Preavviso / rinnovo / exit<textarea name="renewal_notice" rows="3">{esc(selected_contract['renewal_notice'])}</textarea></label>
      <div class="form-actions left full-span"><button class="button primary" type="submit">Salva contratto</button><a class="button secondary" href="{close_url}">Annulla</a></div>
    </form>
  </section>
</div>"""
        campaign_modal = ""
        if mode == "campaign" and selected_campaign:
            selected_latest = latest_update.get(selected_campaign["id"])
            campaign_target = float(row_value(selected_campaign, "funding_target", 0) or 0)
            campaign_raised = float(row_value(selected_update, "raised_amount", row_value(selected_latest, "raised_amount", row_value(selected_campaign, "tracked_raised", 0))) or 0)
            campaign_investors = int(row_value(selected_update, "investors_count", row_value(selected_latest, "investors_count", row_value(selected_campaign, "tracked_investors", 0))) or 0)
            campaign_date = row_value(selected_update, "as_of_date", today_iso())
            campaign_status = row_value(selected_update, "status", "Rilevazione")
            campaign_notes = row_value(selected_update, "notes", "")
            campaign_fee_percent = float(row_value(selected_campaign, "platform_fee_percent", 5) or 5)
            campaign_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal finance-modal">
    <div class="section-head">
      <h2>{'Modifica rilevazione campagna' if selected_update else 'Aggiorna campagna'}</h2>
      <a class="modal-close" href="{close_url}">x</a>
    </div>
    <form class="form-grid" method="post" action="/finance/campaign-update" data-campaign-form>
      {hidden_ctx(ctx)}
      <input type="hidden" name="update_id" value="{esc(selected_update_id if selected_update else '')}">
      <label class="full-span">Campagna / deal<select name="deal_id" required data-deal-select>{deal_options}</select></label>
      <label>Target campagna<input type="number" step="0.01" min="0" name="funding_target" value="{campaign_target:.2f}" data-campaign-target></label>
      <label>Raccolta aggiornata<input type="number" step="0.01" min="0" name="raised_amount" value="{campaign_raised:.2f}" data-campaign-raised></label>
      <label>Fee trattenuta %<input type="number" step="0.01" min="0" max="100" name="platform_fee_percent" value="{campaign_fee_percent:.2f}" data-campaign-fee></label>
      <label>Investitori<input type="number" min="0" name="investors_count" value="{campaign_investors}"></label>
      <label>Data rilevazione<input type="date" name="as_of_date" value="{esc(campaign_date)}"></label>
      <label>Stato<select name="status">{option_values(['Rilevazione', 'In crescita', 'Sotto target', 'Target raggiunto', 'Chiusa'], campaign_status)}</select></label>
      <label class="full-span">Note<textarea name="notes" rows="3" placeholder="Fonte API, aggiornamento manuale, osservazioni andamento">{esc(campaign_notes)}</textarea></label>
      <div class="form-actions left full-span"><button class="button primary" type="submit">Salva campagna</button><a class="button secondary" href="{close_url}">Annulla</a></div>
    </form>
  </section>
</div>"""
        sync_script = """
<script>
document.querySelectorAll('[data-campaign-form]').forEach((form) => {
  const target = form.querySelector('[data-campaign-target]');
  const fee = form.querySelector('[data-campaign-fee]');
  const dealSelect = form.querySelector('[data-deal-select]');
  if (dealSelect) {
    dealSelect.addEventListener('change', () => {
      const option = dealSelect.options[dealSelect.selectedIndex];
      if (option && option.dataset.target) target.value = Number.parseFloat(option.dataset.target || '0').toFixed(2);
      if (option && option.dataset.fee && fee) fee.value = Number.parseFloat(option.dataset.fee || '5').toFixed(2);
    });
  }
});
</script>
"""
        body = f"""
<p class="page-copy">Quadro economico operativo: costi strutturali complessivi, contratti raggiungibili, voci manuali e andamento campagne. La fee piattaforma e' impostata al 5% di default e puo' variare per singola campagna.</p>
<section class="metric-grid">
  <div class="metric"><span>Costi strutturali annui</span><strong>{money(structural_total)}</strong></div>
  <div class="metric"><span>Raccolta tracciata</span><strong>{money(campaign_total)}</strong></div>
  <div class="metric"><span>Ricavi stimati da fee</span><strong>{money(estimated_revenue)}</strong></div>
  <div class="metric"><span>Gap break even</span><strong>{money(break_even_gap)}</strong></div>
</section>
<section class="panel finance-toolbar">
  <div>
    <p class="panel-kicker">Azioni operative</p>
    <h2>Aggiornamenti economici</h2>
  </div>
  <div class="inline-actions left">
    <a class="button primary" href="{rel_url('/finance', ctx, {'mode': 'cost'})}">+ Nuovo costo</a>
    <a class="button secondary" href="{rel_url('/finance', ctx, {'mode': 'campaign'})}">+ Aggiorna campagna</a>
  </div>
</section>
<section class="panel">
  <div class="section-head"><h2>Costi e scadenze</h2><span class="source-chip">Costi complessivi</span></div>
  <table class="data-table roomy">
    <thead><tr><th>Voce</th><th>Categoria</th><th>Importo</th><th>Scadenza</th><th>Stato</th><th>Origine / contratto</th><th>Azioni</th></tr></thead>
    <tbody>{all_cost_rows}</tbody>
  </table>
</section>
<section class="panel">
  <div class="section-head"><h2>Andamento campagne</h2><span class="source-chip">API / investimenti / manuale</span></div>
  <table class="data-table roomy finance-campaign-table">
    <thead><tr><th>Campagna</th><th>Target</th><th>Raccolta</th><th>Fee %</th><th>Ricavo stimato</th><th>Investitori</th><th>Stato</th><th>Aggiornato</th><th>Azioni</th></tr></thead>
    <tbody>{''.join(campaign_rows) or '<tr><td colspan="9" class="empty-row">Nessuna campagna.</td></tr>'}</tbody>
  </table>
</section>
<section class="panel">
  <div class="section-head"><h2>Storico rilevazioni campagne</h2><span class="source-chip">trend operativo</span></div>
  <table class="data-table compact finance-history-table">
    <thead><tr><th>Data</th><th>Campagna</th><th>Raccolta</th><th>Investitori</th><th>Stato</th><th>Fonte</th><th>Note</th><th>Azioni</th></tr></thead>
    <tbody>{history_rows}</tbody>
  </table>
</section>
{cost_modal}
{contract_modal}
{campaign_modal}
{sync_script}
"""
        self.render("Finance", body, "finance")

    def page_deals(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        if pid == 1:  # Pariter: hub istruttoria/in corso/conclusi
            self.page_pariter_hub(ctx)
            return
        deals = rows(
            """
            SELECT d.*, p.name AS proponent_name,
                   tm.name AS technical_reviewer_name, cm.name AS covi_reviewer_name,
                   SUM(CASE WHEN r.required = 1 AND r.completed = 0 THEN 1 ELSE 0 END) AS missing_count
            FROM deals d
            JOIN proponents p ON p.id = d.proponent_id
            LEFT JOIN committee_members tm ON tm.id = d.technical_reviewer_id
            LEFT JOIN committee_members cm ON cm.id = d.covi_reviewer_id
            LEFT JOIN deal_requirements r ON r.deal_id = d.id
            WHERE d.platform_id = ?
            GROUP BY d.id
            ORDER BY d.updated_at DESC
            """,
            (pid,),
        )
        create = (
            f'<a class="button primary" href="{rel_url("/deals/new", ctx)}">Nuovo deal</a>'
            if user_can(ctx["user"], "create_deal")
            else ""
        )
        deal_rows = "".join(
            f"""<tr>
              <td><a href="{rel_url('/deals/' + str(d['id']), ctx)}"><strong>{esc(d['title'])}</strong></a></td>
              <td>{esc(d['proponent_name'])}</td>
              <td><span class="badge {badge_class(status_for_phase(d['phase']))}">{esc(status_for_phase(d['phase']))}</span></td>
              <td>{esc(phase_label(d['phase']))}</td>
              <td>{esc(d['technical_reviewer_name'] or '-')}</td>
              <td>{esc(d['covi_reviewer_name'] or '-')}</td>
              <td>{esc(d['missing_count'] or 0)}</td>
            </tr>"""
            for d in deals
        )
        body = f"""
<section class="panel">
  <div class="section-head">
    <h2>Deal della piattaforma</h2>
    {create}
  </div>
  <table class="data-table roomy">
    <thead><tr><th>Deal</th><th>Proponente</th><th>Stato</th><th>Fase</th><th>Relatore CT</th><th>Relatore Advisory</th><th>Mancanti</th></tr></thead>
    <tbody>{deal_rows}</tbody>
  </table>
</section>
"""
        self.render("Deal", body, "deals")

    # ===================== Istruttoria Pariter =====================

    def page_pariter_hub(self, ctx):
        pid = ctx["platform_id"]
        practices = rows(
            """
            SELECT pr.*,
                (SELECT COUNT(*) FROM practice_alerts a
                   WHERE a.practice_id = pr.id AND a.severity = 'bloccante' AND a.alert_status = 'aperto') AS blocking_alerts,
                (SELECT COUNT(*) FROM practice_alerts a
                   WHERE a.practice_id = pr.id AND a.severity = 'non_bloccante' AND a.alert_status = 'aperto') AS soft_alerts,
                (SELECT COUNT(*) FROM practice_documents d
                   WHERE d.practice_id = pr.id AND d.required = 1 AND d.doc_status IN ('mancante','da_integrare')) AS missing_docs
            FROM practices pr
            WHERE pr.platform_id = ?
            ORDER BY pr.updated_at DESC
            """,
            (pid,),
        )
        buckets = {"istruttoria": [], "in_corso": [], "conclusi": []}
        for pr in practices:
            buckets[practice_bucket(pr["status"])].append(pr)
        vista = (self.get_query_param("vista") or "tutti")
        if vista not in buckets and vista != "tutti":
            vista = "tutti"
        tab_defs = [
            ("tutti", "Tutti", len(practices)),
            ("istruttoria", "In istruttoria", len(buckets["istruttoria"])),
            ("in_corso", "In corso", len(buckets["in_corso"])),
            ("conclusi", "Conclusi", len(buckets["conclusi"])),
        ]
        tabs = "".join(
            f'<a class="subtab {"active" if key == vista else ""}" '
            f'href="{rel_url("/deals", ctx, {"vista": key})}">{label} ({n})</a>'
            for key, label, n in tab_defs
        )
        selected = practices if vista == "tutti" else buckets[vista]
        if selected:
            def bucket_badge(status):
                b = practice_bucket(status)
                label, cls = PRACTICE_BUCKET_META[b]
                return f'<span class="badge {cls}">{esc(label)}</span>'
            body_rows = "".join(
                f"""<tr>
                  <td><a href="{rel_url('/pariter/practices/' + str(pr['id']), ctx)}"><strong>{esc(pr['project_title'])}</strong></a></td>
                  <td>{esc(pr['proponent_name'] or '-')}</td>
                  <td>{bucket_badge(pr['status'])}</td>
                  <td><span class="badge {badge_class(practice_status_label(pr['status']))}">{esc(practice_status_label(pr['status']))}</span></td>
                  <td>{('<span class="badge danger">' + str(pr['blocking_alerts']) + ' bloc.</span>') if pr['blocking_alerts'] else '-'}{(' <span class="badge warning">' + str(pr['soft_alerts']) + '</span>') if pr['soft_alerts'] else ''}</td>
                  <td>{esc(pr['missing_docs'] or 0)}</td>
                  <td class="muted">{esc(next_step_summary(pr['status']))}</td>
                  <td class="muted">{esc((pr['updated_at'] or '')[:10])}</td>
                  <td><a class="button tiny" href="{rel_url('/pariter/practices/' + str(pr['id']), ctx)}">Apri fascicolo</a></td>
                </tr>"""
                for pr in selected
            )
            table = f"""
  <table class="data-table compact">
    <thead><tr><th>Progetto</th><th>Proponente</th><th>Fase</th><th>Stato</th><th>Alert</th><th>Doc mancanti</th><th>Prossimo step</th><th>Aggiornato</th><th></th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>"""
        else:
            table = '<p class="muted" style="padding:14px 2px;">Nessuna pratica in questa vista.</p>'
        body = f"""
<section class="panel">
  <div class="section-head">
    <h2>Deal e istruttoria</h2>
    <a class="button primary" href="{rel_url('/pariter/practices/import', ctx)}">Importa dossier</a>
  </div>
  <div class="subtabs">{tabs}</div>
  {table}
</section>
"""
        self.render("Deal", body, "deals")

    def page_comitato_tecnico(self):
        ctx = self.get_ctx()
        if ctx["platform_id"] != 1:
            self.redirect("/deals", ctx, "Il Comitato Tecnico e' attivo solo su Pariter Equity.")
            return
        if not user_can(ctx["user"], "view_comitato_tecnico"):
            self.redirect("/", ctx, "Area riservata ai membri del Comitato Tecnico.")
            return
        practices = rows(
            """
            SELECT pr.*, cr.workflow_status AS cvoi_status, cr.weighted_score AS cvoi_score
            FROM practices pr
            LEFT JOIN cvoi_reports cr ON cr.practice_id = pr.id
            WHERE pr.platform_id = 1 AND (pr.status = 'pronto_cvoi' OR cr.id IS NOT NULL)
            ORDER BY pr.updated_at DESC
            """,
        )
        if practices:
            body_rows = "".join(
                f"""<tr>
                  <td><a href="{rel_url('/pariter/practices/' + str(pr['id']), ctx, {'fase': 'fase3', 'sub': 'scoring'})}"><strong>{esc(pr['project_title'])}</strong></a></td>
                  <td>{esc(pr['proponent_name'] or '-')}</td>
                  <td><span class="badge {badge_class(practice_status_label(pr['status']))}">{esc(practice_status_label(pr['status']))}</span></td>
                  <td>{esc((pr['cvoi_status'] or 'da redigere'))}{(' &middot; ' + format(pr['cvoi_score'], 'g')) if pr['cvoi_score'] else ''}</td>
                  <td><a class="button tiny" href="{rel_url('/pariter/practices/' + str(pr['id']), ctx, {'fase': 'fase3', 'sub': 'scoring'})}">Apri CVOI</a>
                      <a class="button tiny" href="{rel_url('/pariter/practices/' + str(pr['id']), ctx, {'tab': 'documentale'})}">Dossier</a></td>
                </tr>"""
                for pr in practices
            )
            table = f"""<table class="data-table compact">
    <thead><tr><th>Progetto</th><th>Proponente</th><th>Stato pratica</th><th>CVOI</th><th></th></tr></thead>
    <tbody>{body_rows}</tbody></table>"""
        else:
            table = '<p class="muted" style="padding:14px 2px;">Nessuna pratica in valutazione dal Comitato Tecnico.</p>'
        body = f"""
<section class="panel">
  <div class="section-head"><h2>Comitato Tecnico - pratiche in valutazione</h2></div>
  <p class="muted">Pratiche per cui le verifiche interne Pariter sono validate e il dossier e' pronto per la valutazione CVOI.</p>
  {table}
</section>"""
        self.render("Comitato Tecnico", body, "comitato_tecnico")

    def get_query_param(self, name):
        params = parse_qs(urlparse(self.path).query)
        return (params.get(name) or [""])[0]

    def page_team(self):
        ctx = self.get_ctx()
        # Pagina Team ritirata: CV, documento e firma sono ora nel fascicolo persona
        # di Compagine (apri il soggetto dall'organigramma o dalla Lista anagrafica).
        self.redirect("/compagine", ctx, "L'anagrafica (CV, documento, firma) e' ora nel fascicolo persona di Compagine.")
        return
        if ctx["platform_id"] != 1:
            self.redirect("/compagine", ctx, "Anagrafica team disponibile su Pariter.")
            return
        can_edit = user_can(ctx["user"], "manage_compagine")
        people = rows("SELECT * FROM team_people WHERE platform_id = 1 AND active = 1 ORDER BY id", ())
        sel_id = self.get_query_param("id")
        rows_html = ""
        for p in people:
            firma_ok = bool(p["firma_path"]) and bool(p["id_document_id"])
            firma_badge = ('<span class="badge success">valida</span>' if firma_ok else
                           ('<span class="badge warning">solo firma</span>' if p["firma_path"] else '<span class="badge neutral">assente</span>'))
            rows_html += f"""<tr>
              <td>{esc(p['name'])}</td><td>{esc(p['role'])}</td>
              <td>{'si' if p['cv_document_id'] else '-'}</td>
              <td>{'si' if p['id_document_id'] else '-'}</td>
              <td>{firma_badge}</td>
              <td>{(f'<a class="button tiny" href="' + rel_url('/pariter/team', ctx, {'id': p['id']}) + '">Apri scheda</a>') if can_edit else ''}</td>
            </tr>"""
        detail = ""
        if can_edit and sel_id.isdigit():
            p = row("SELECT * FROM team_people WHERE id = ? AND platform_id = 1", (int(sel_id),))
            if p:
                def docrow(label, kind, doc_id):
                    cur = (f'<a class="button tiny" href="{rel_url("/documents/" + str(doc_id) + "/download", ctx)}">Apri</a>'
                           if doc_id else '<span class="muted">non caricato</span>')
                    return f"""<div class="form-actions" style="justify-content:flex-start;gap:10px;align-items:center">
                      <span style="min-width:160px">{esc(label)}</span>{cur}
                      <form method="post" action="/pariter/team/upload" enctype="multipart/form-data" class="inline-form">
                        {hidden_ctx(ctx)}<input type="hidden" name="id" value="{p['id']}"><input type="hidden" name="kind" value="{kind}">
                        <input type="file" name="file" required><button class="button tiny" type="submit">Carica</button></form>
                    </div>"""
                firma_img = ""
                if p["firma_path"] and (BASE_DIR / p["firma_path"]).exists():
                    b64 = base64.b64encode((BASE_DIR / p["firma_path"]).read_bytes()).decode()
                    ext = p["firma_path"].rsplit(".", 1)[-1].lower()
                    mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
                    firma_img = f'<img src="data:{mime};base64,{b64}" style="max-height:80px;border:1px solid var(--line);background:#fff">'
                detail = f"""
<section class="panel">
  <div class="section-head"><h2>Scheda: {esc(p['name'])}</h2><a class="button tiny" href="{rel_url('/pariter/team', ctx)}">Chiudi</a></div>
  <form class="form-grid" method="post" action="/pariter/team/save">
    {hidden_ctx(ctx)}<input type="hidden" name="id" value="{p['id']}">
    <label>Nome<input name="name" value="{esc(p['name'])}" required></label>
    <label>Ruolo<input name="role" value="{esc(p['role'])}"></label>
    <label>Email<input name="email" value="{esc(p['email'])}"></label>
    <div class="form-actions"><button class="button primary" type="submit">Salva anagrafica</button></div>
  </form>
  <h3>Documenti</h3>
  {docrow("Curriculum vitae", "cv", p["cv_document_id"])}
  {docrow("Documento d'identita'", "documento", p["id_document_id"])}
  <h3>Firma</h3>
  <p class="muted">Carica un'immagine della firma oppure disegnala qui sotto. La firma e' valida ai fini dei documenti se in anagrafica e' presente anche il documento d'identita'.</p>
  <div class="form-actions" style="justify-content:flex-start;gap:14px;align-items:flex-start">
    <div>{firma_img or '<span class="muted">nessuna firma</span>'}</div>
    <form method="post" action="/pariter/team/upload" enctype="multipart/form-data" class="inline-form">
      {hidden_ctx(ctx)}<input type="hidden" name="id" value="{p['id']}"><input type="hidden" name="kind" value="firma">
      <input type="file" name="file" accept="image/*" required><button class="button tiny" type="submit">Carica firma</button></form>
  </div>
  <form method="post" action="/pariter/team/firma-draw" id="firmaForm" style="margin-top:12px">
    {hidden_ctx(ctx)}<input type="hidden" name="id" value="{p['id']}"><input type="hidden" name="firma_data" id="firmaData">
    <canvas id="firmaCanvas" width="420" height="140" class="firma-canvas"></canvas>
    <div class="form-actions left"><button type="button" class="button ghost" id="firmaClear">Cancella</button><button class="button primary" type="submit">Salva firma disegnata</button></div>
  </form>
</section>"""
        body = f"""
<section class="panel">
  <div class="section-head"><h2>Anagrafica del team</h2></div>
  <p class="muted">Membri di Comitato Tecnico, Advisory, CdA e funzioni di controllo. Per ciascuno: CV, documento d'identita' e firma (caricata o disegnata). La firma preimpostata qui viene applicata automaticamente quando la persona firma una relazione o un verbale.</p>
  <table class="data-table compact">
    <thead><tr><th>Nome</th><th>Ruolo</th><th>CV</th><th>Documento</th><th>Firma</th><th></th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</section>
{detail}
"""
        self.render("Team", body, "compagine")

    def post_team_save(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine"):
            self.redirect("/pariter/team", ctx, "Ruolo non abilitato.")
            return
        with connect() as conn:
            conn.execute("UPDATE team_people SET name = ?, role = ?, email = ? WHERE id = ? AND platform_id = 1",
                         (form.get("name", "").strip(), form.get("role", ""), form.get("email", ""), int(form["id"])))
            conn.commit()
        self.redirect("/pariter/team", ctx, "Anagrafica aggiornata.", {"id": form["id"]})

    def _team_id_from_form(self, conn, ctx, form):
        """Risolve la riga team_people da `id` o, se assente, dal `person_name`
        (la crea se non esiste). Cosi' CV/firma si gestiscono dal fascicolo persona."""
        if form.get("id"):
            return int(form["id"])
        name = (form.get("person_name") or "").strip()
        if not name:
            return None
        r = conn.execute(
            "SELECT id FROM team_people WHERE platform_id = ? AND name = ? ORDER BY id LIMIT 1",
            (ctx["platform_id"], name),
        ).fetchone()
        if r:
            return r["id"]
        cur = conn.execute(
            "INSERT INTO team_people(platform_id, name, role, active) VALUES (?, ?, ?, 1)",
            (ctx["platform_id"], name, form.get("person_role", "")),
        )
        return cur.lastrowid

    def _team_redirect(self, ctx, form, msg):
        name = (form.get("person_name") or "").strip()
        if name:
            self.redirect("/compagine", ctx, msg, {"person": name, "role": form.get("person_role") or "Anagrafica organigramma"})
        else:
            self.redirect("/pariter/team", ctx, msg, {"id": form.get("id", "")})

    def post_team_upload(self, form, files):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine"):
            self._team_redirect(ctx, form, "Ruolo non abilitato.")
            return
        kind = form.get("kind", "")
        file_item = files.get("file")
        if file_item is None or not getattr(file_item, "filename", ""):
            self._team_redirect(ctx, form, "Nessun file.")
            return
        with connect() as conn:
            tid = self._team_id_from_form(conn, ctx, form)
            if tid is None:
                conn.commit()
                self._team_redirect(ctx, form, "Soggetto non valido.")
                return
            if kind == "firma":
                # salva l'immagine firma come file e registra il percorso
                filename = sanitize_filename(file_item.filename)
                folder = UPLOAD_DIR / "firme"
                folder.mkdir(parents=True, exist_ok=True)
                stored = folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}-{filename}"
                file_item.file.seek(0)
                with stored.open("wb") as out:
                    shutil.copyfileobj(file_item.file, out)
                conn.execute("UPDATE team_people SET firma_path = ?, firma_kind = 'caricata' WHERE id = ?",
                             (str(stored.relative_to(BASE_DIR)), tid))
            else:
                doc_id = save_uploaded_document(conn, file_item, ctx["platform_id"], None, None,
                                                "Team", ("CV" if kind == "cv" else "Documento identita"),
                                                file_item.filename, ctx["user_id"])
                col = "cv_document_id" if kind == "cv" else "id_document_id"
                conn.execute(f"UPDATE team_people SET {col} = ? WHERE id = ?", (doc_id, tid))
            conn.commit()
        self._team_redirect(ctx, form, "Caricato.")

    def post_team_firma_draw(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine"):
            self._team_redirect(ctx, form, "Ruolo non abilitato.")
            return
        data = form.get("firma_data", "")
        with connect() as conn:
            tid = self._team_id_from_form(conn, ctx, form)
            if tid is not None and data.startswith("data:image/png;base64,"):
                raw = base64.b64decode(data.split(",", 1)[1])
                folder = UPLOAD_DIR / "firme"
                folder.mkdir(parents=True, exist_ok=True)
                stored = folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}-firma.png"
                stored.write_bytes(raw)
                conn.execute("UPDATE team_people SET firma_path = ?, firma_kind = 'disegnata' WHERE id = ?",
                             (str(stored.relative_to(BASE_DIR)), tid))
                conn.commit()
                msg = "Firma disegnata salvata."
            else:
                conn.commit()
                msg = "Disegna la firma prima di salvare."
        self._team_redirect(ctx, form, msg)

    def get_practice(self, practice_id):
        return row("SELECT * FROM practices WHERE id = ?", (practice_id,))

    def recall_documents_html(self, ctx, practice, origins, title="Documenti richiamati per la valutazione"):
        placeholders = ",".join("?" for _ in origins)
        docs = rows(
            f"SELECT * FROM documents WHERE practice_id = ? AND origin IN ({placeholders}) ORDER BY id",
            (practice["id"], *origins),
        )
        if not docs:
            body = '<p class="muted">Nessun documento delle fasi precedenti disponibile.</p>'
        else:
            items = "".join(
                f'<li><span class="muted">[{esc(d["origin"])}]</span> {esc(d["title"])} '
                f'<a class="button tiny" href="{rel_url("/documents/" + str(d["id"]) + "/download", ctx)}">Apri</a></li>'
                for d in docs
            )
            body = f'<ul class="clean-list">{items}</ul>'
        return f'<section class="panel"><div class="section-head"><h2>{esc(title)}</h2></div>{body}</section>'

    def render_practice_shell(self, ctx, practice, active_fase, inner):
        pid = practice["id"]
        step_state = {r["step_key"]: r["status"] for r in rows(
            "SELECT step_key, status FROM practice_process_steps WHERE practice_id = ?", (pid,))}
        done = sum(1 for s in ONBOARDING_STEPS if step_state.get(s["key"]) == "completata")
        total = len(ONBOARDING_STEPS)
        pct = int(done * 100 / total) if total else 0
        current_band = practice_bucket(practice["status"])
        segs = ""
        for n, s in enumerate(ONBOARDING_STEPS, start=1):
            st_done = step_state.get(s["key"]) == "completata"
            band = PHASE_BAND.get(s["key"], "istruttoria")
            cls = ("proc-seg proc-band-" + band
                   + (" band-current" if band == current_band else "")
                   + (" active" if s["key"] == active_fase else "")
                   + (" done" if st_done else ""))
            segs += (f'<a class="{cls}" href="{rel_url("/pariter/practices/" + str(pid), ctx, {"fase": s["key"]})}">'
                     f'<span class="proc-num">{("&check;" if st_done else n)}</span>'
                     f'<span class="proc-lab">{esc(s["fase"])}<br>{esc(s["titolo"])}</span></a>')
        band_legend = (
            '<div class="band-legend">'
            '<span class="band-key band-key-istruttoria">In istruttoria</span>'
            '<span class="band-key band-key-in-corso">In corso (online)</span>'
            '<span class="band-key band-key-conclusi">Conclusa</span>'
            '</div>')
        st = practice["status"]
        closed = practice["closed_at"] if "closed_at" in practice.keys() else ""
        closed_banner = (f'<div class="ai-flag" style="color:var(--red);border-color:#d8b3b0;background:#f7ecea">'
                         f'Pratica chiusa il {esc((closed or "")[:16])} - sola lettura. Le azioni sono disabilitate.</div>'
                         if closed else "")
        def navlink(fase, label):
            active = " active" if fase == active_fase else ""
            return f'<a class="button tiny{active}" href="{rel_url("/pariter/practices/" + str(pid), ctx, {"fase": fase})}">{label}</a>'
        header = f"""
{closed_banner}
<section class="panel">
  <div class="section-head">
    <div>
      <h2 style="margin:0">{esc(practice['project_title'])}</h2>
      <p class="muted" style="margin:4px 0 0">{esc(practice['proponent_name'] or '-')} &middot; pratica #{practice['id']} &middot; <span class="badge {badge_class(practice_status_label(st))}">{esc(practice_status_label(st))}</span></p>
    </div>
    <div class="header-badges">
      {navlink('riepilogo', 'Riepilogo')}{navlink('storico', 'Storico')}
      <a class="button tiny" href="{rel_url('/pariter/practices/' + str(pid) + '/report', ctx)}">Report</a>
      <a class="button tiny" href="{rel_url('/pariter/practices/' + str(pid) + '/export', ctx)}">Export ZIP</a>
    </div>
  </div>
  <div class="progress-track"><div class="progress-fill" style="width:{pct}%"></div></div>
  <div class="process-bar">{segs}</div>
  {band_legend}
</section>
{inner}
"""
        self.render(practice["project_title"], header, "deals")

    def page_practice_detail(self, practice_id):
        ctx = self.get_ctx()
        practice = self.get_practice(practice_id)
        if not practice or practice["platform_id"] != ctx["platform_id"]:
            self.not_found()
            return
        valid = {s["key"] for s in ONBOARDING_STEPS} | {"riepilogo", "storico"}
        # mappa i vecchi tab (usati nei redirect degli handler) sulle nuove fasi
        tab_to_fase = {"documentale": "fase2", "interne": "fase2", "cvoi": "fase3",
                       "advisory": "fase4", "cda": "fase5", "condizioni": "fase6",
                       "validazione": "fase6", "campagna": "fase6", "processo": "riepilogo"}
        fase = self.get_query_param("fase") or self.get_query_param("tab")
        if fase in tab_to_fase:
            fase = tab_to_fase[fase]
        if fase not in valid:
            # default: prima fase non completata, altrimenti fase1
            step_state = {r["step_key"]: r["status"] for r in rows(
                "SELECT step_key, status FROM practice_process_steps WHERE practice_id = ?", (practice_id,))}
            fase = next((s["key"] for s in ONBOARDING_STEPS if step_state.get(s["key"]) != "completata"),
                        ONBOARDING_STEPS[0]["key"])
        inner = self.practice_phase_body(ctx, practice, fase)
        self.render_practice_shell(ctx, practice, fase, inner)

    def practice_phase_body(self, ctx, practice, fase):
        if fase == "riepilogo":
            return self.practice_tab_riepilogo(ctx, practice)
        if fase == "storico":
            return self.practice_tab_storico(ctx, practice)
        step = next((s for s in ONBOARDING_STEPS if s["key"] == fase), None)
        if not step:
            return self.practice_tab_riepilogo(ctx, practice)
        # intestazione fase + descrizione + azione "segna completata"
        st_done = (row("SELECT status FROM practice_process_steps WHERE practice_id = ? AND step_key = ?",
                       (practice["id"], fase)) or {"status": "da_fare"})
        is_done = (st_done["status"] == "completata") if hasattr(st_done, "keys") and "status" in st_done.keys() else (st_done == "completata")
        can_edit = user_can(ctx["user"], "manage_practice") and not practice["closed_at"]
        toggle = ""
        if can_edit:
            toggle = (f'<form method="post" action="/pariter/practices/{practice["id"]}/step" style="display:inline">'
                      f'{hidden_ctx(ctx)}<input type="hidden" name="step_key" value="{fase}">'
                      f'<input type="hidden" name="status" value="{"da_fare" if is_done else "completata"}">'
                      f'<button class="button tiny" type="submit">{"Riapri fase" if is_done else "Segna fase completata"}</button></form>')
        state_badge = '<span class="badge success">Completata</span>' if is_done else '<span class="badge neutral">In corso</span>'
        head = (f'<section class="panel"><div class="section-head">'
                f'<div><h2 style="margin:0">{esc(step["fase"])} - {esc(step["titolo"])}</h2>'
                f'<p class="muted" style="margin:4px 0 0">{esc(step["attore"])}</p></div>'
                f'<div class="header-badges">{state_badge}{toggle}</div>'
                f'</div><p>{esc(step["descrizione"])}</p></section>')
        # corpo specifico della fase, componendo i renderer esistenti
        if fase == "fase1":
            # Flusso guidato: registrazione candidatura (anagrafica + documenti, automatica) ->
            # presa in carico (pulsante -> numero pratica) -> comunicazioni C1/C2 da inviare ->
            # documenti allegati -> storico comunicazioni.
            nr = practice["pratica_no"] if "pratica_no" in practice.keys() else ""
            comms = ""
            if nr:
                comms = self.phase_emails_html(
                    ctx, practice, ["C1", "C2"], "fase1",
                    title="Comunicazioni di presa in carico",
                    intro="Presa in carico effettuata: invia la PEC interna (C1) e l'email di presa in carico al proponente (C2) con il numero pratica. L'invio viene salvato nello storico.",
                    open_codes=["C2"])
            body = (self.render_registrazione_panel(ctx, practice)
                    + self.render_anagrafica_panel(ctx, practice)
                    + self.render_intake_panel(ctx, practice)
                    + comms
                    + self.render_docs_block(ctx, practice, ["fase1"], "Documenti allegati (i 9 di candidatura)",
                                             intro="Documenti inviati dal proponente. I documenti mancanti vengono richiesti e validati nella Fase 2 (ammissibilita')."))
        elif fase == "fase2":
            # Fase 2 divisa per funzione (pronta per i permessi):
            # 2.1 Completezza del fascicolo (Comitato Tecnico) · 2.2 Verifica ammissibilita' (Resp. compliance) ·
            # 2.3 Validazione ammissibilita' (esito + C4 -> CVOI).
            subs = [
                ("completezza", "2.1 Completezza del fascicolo", "Comitato Tecnico (CVOI)"),
                ("ammissibilita", "2.2 Verifica di ammissibilita'", "Responsabile delle funzioni di controllo"),
                ("validazione", "2.3 Validazione ammissibilita'", "Esito documentale e passaggio al CVOI"),
            ]
            sub = self.get_query_param("sub") or "completezza"
            if sub not in {s[0] for s in subs}:
                sub = "completezza"
            subnav = '<div class="subtabs">' + "".join(
                f'<a class="subtab {"active" if k == sub else ""}" href="{rel_url("/pariter/practices/" + str(practice["id"]), ctx, {"fase": "fase2", "sub": k})}">{esc(t)}</a>'
                for k, t, _f in subs) + '</div>'
            cur = next(s for s in subs if s[0] == sub)
            sec_head = (f'<section class="panel"><p class="eyebrow">{esc(cur[1])}</p>'
                        f'<div class="section-head"><h2 style="margin:0">{esc(cur[1].split(" ", 1)[1])}</h2>'
                        f'<span class="badge neutral">Funzione: {esc(cur[2])}</span></div></section>')
            # In 2.1 e 2.2 si possono inviare solo C3 (integrazione) o C6 (non ammissibilita');
            # la verifica documentale positiva (C4) e' possibile solo in 2.3.
            comms_amm = self.phase_emails_html(
                ctx, practice, ["C3", "C6"], "fase2",
                title="Comunicazioni disponibili in questa fase",
                intro="In ammissibilita' si invia solo la richiesta di integrazione (C3) o la comunicazione di non ammissibilita' (C6). "
                      "La verifica documentale positiva (C4) si invia solo al punto 2.3.",
                back_sub=sub)
            if sub == "completezza":
                section = (self.render_onorabilita_config(ctx, practice)
                           + self.render_docs_block(
                                ctx, practice, ["fase1", "fase2"],
                                "Istruttoria documentale (completezza e regolarita')",
                                intro="I 9 documenti di candidatura sono richiamati dalla fase precedente; si aggiungono le dichiarazioni "
                                      "KYC e le evidenze sul limite di 5 milioni. Marca 'verificato' ogni documento; i documenti obbligatori mancanti sono condizione per proseguire.")
                           + self.render_completezza_panel(ctx, practice)
                           + self.practice_documentale_requests(ctx, practice)
                           + comms_amm)
            elif sub == "ammissibilita":
                section = (self.render_ammissibilita_checklist(ctx, practice)
                           + self.practice_onorabilita_panel(ctx, practice)
                           + self.render_internal_reviews(ctx, practice, ["aml_art5"])
                           + comms_amm)
            else:  # 2.3 validazione: recap documentale + esito positivo (C4) o negativo (C6)
                section = (self.render_docs_block(
                                ctx, practice, ["fase1", "fase2"],
                                "Recap documentale acquisito",
                                intro="Riepilogo di tutta la documentazione del fascicolo con lo stato/flag di verifica. "
                                      "Da qui si invia l'esito: positivo (C4, avvia il CVOI) oppure non ammissibilita' (C6).")
                           + self.render_validazione_ammissibilita(ctx, practice)
                           + self.phase_emails_html(ctx, practice, ["C6"], "fase2",
                                                    title="Esito negativo - non ammissibilita' (C6)",
                                                    intro="Se l'ammissibilita' non e' superata (es. limite 5M non riducibile, requisiti art. 5 non soddisfatti), invia qui la comunicazione di non ammissibilita'.",
                                                    back_sub="validazione"))
            body = subnav + sec_head + section
        elif fase == "fase3":
            # CVOI in tre parti: 3.1 Verifiche (M5) · 3.2 Scoring (M6) · 3.3 Fascicolo (M7) + trasmissione.
            subs3 = [
                ("verifiche", "3.1 Verifiche - KIIS (M5) e conflitti"),
                ("scoring", "3.2 Scoring CVOI (M6)"),
                ("fascicolo", "3.3 Fascicolo di valutazione (M7)"),
            ]
            sub = self.get_query_param("sub") or "verifiche"
            if sub not in {s[0] for s in subs3}:
                sub = "verifiche"
            subnav = '<div class="subtabs">' + "".join(
                f'<a class="subtab {"active" if k == sub else ""}" href="{rel_url("/pariter/practices/" + str(practice["id"]), ctx, {"fase": "fase3", "sub": k})}">{esc(t)}</a>'
                for k, t in subs3) + '</div>'

            def gate_block(target_label, target_sub, reasons):
                lis = "".join(f"<li>{esc(r)}</li>" for r in reasons)
                return (f'<section class="panel"><div class="section-head"><h2>{esc(target_label)}</h2>'
                        f'<span class="badge warning">Bloccato</span></div>'
                        f'<p class="muted">Per procedere mancano:</p><ul class="clean-list">{lis}</ul>'
                        f'<a class="button tiny" href="{rel_url("/pariter/practices/" + str(practice["id"]), ctx, {"fase": "fase3", "sub": target_sub})}">Torna indietro</a></section>')

            passed, reasons = fase3_gate(practice)
            with connect() as conn:
                _cc = compute_cvoi_collegial(conn, practice)
            scoring_done = _cc["all_done"]

            if sub == "verifiche":
                # 3.1: KIIS (redatta dal proponente) -> verifica del fornitore (M5) + conflitti -> gate
                section = (self.render_kiis_panel(ctx, practice)
                           + self.render_kiis_stato_panel(ctx, practice)
                           + self.phase_emails_html(ctx, practice, ["C3K"], "fase3",
                                                    title="Segnalazione al proponente - completa/correggi la KIIS (art. 23 par. 12)",
                                                    intro="Unica comunicazione verso il proponente in Fase 3: segnalazione per completare/correggere la KIIS (non e' un esito). In assenza di riscontro: sospensione (max 30 gg) e poi cancellazione.",
                                                    back_sub="verifiche")
                           + self.render_internal_reviews(ctx, practice, ["coerenza_kiis"])
                           + self.render_verifiche_merito_panel(ctx, practice))
            elif sub == "scoring":
                # 3.2: scoring CVOI (M6), valutazione collegiale. Sempre compilabile; il gate 3.1
                # serve solo a mettere agli atti / proseguire, non a impedire la compilazione.
                warn = ""
                if not passed:
                    lis = "".join(f"<li>{esc(r)}</li>" for r in reasons)
                    warn = (f'<section class="panel"><div class="section-head"><h2>Scoring CVOI (M6)</h2>'
                            f'<span class="badge warning">Verifiche 3.1 da completare</span></div>'
                            f'<p class="muted">Si puo\' gia\' compilare lo scoring; per <strong>mettere agli atti</strong> e procedere serve completare la 3.1:</p>'
                            f'<ul class="clean-list">{lis}</ul></section>')
                section = warn + self.practice_tab_cvoi(ctx, practice)
            else:  # fascicolo (3.3)
                # 3.3: fascicolo M7 con richiamo doc 3.1/3.2, bozza descrittiva (IA) + firma -> trasmissione Advisory
                if not passed:
                    section = gate_block("Fascicolo di valutazione (M7)", "verifiche", reasons)
                elif not scoring_done:
                    section = gate_block("Fascicolo di valutazione (M7)", "scoring",
                                         ["la scheda di scoring (M6) non e' completa: tutti i valutatori devono validare (salvo astensioni), min. 2"])
                else:
                    section = (self.recall_documents_html(
                                   ctx, practice, ["CVOI", "KIIS", "Verifiche interne"],
                                   "Documenti richiamati (3.1 verifiche M5 e 3.2 scoring M6)")
                               + self.render_fascicolo_m7(ctx, practice))
            body = subnav + section
        elif fase == "fase4":
            # Sessione dedicata Advisory Committee: esamina CVOI + bozza KIIS e rende il parere non vincolante.
            body = (self.recall_documents_html(ctx, practice, ["CVOI", "KIIS", "Verifiche interne"],
                                               "Fascicolo per l'Advisory Committee (CVOI, bozza KIIS, verifiche)")
                    + self.practice_tab_advisory(ctx, practice))
        elif fase == "fase5":
            # Relazione conflitti d'interesse (ultimo presidio) -> convocazione/delibera/verbale CdA.
            body = (self.render_internal_reviews(ctx, practice, ["conflitti"])
                    + self.practice_tab_decision(ctx, practice, 1))
        elif fase == "fase6":
            nr = practice["pratica_no"] if "pratica_no" in practice.keys() else ""
            ident = offer_identifier(practice)
            if nr:
                ident_inner = (f'<p>LEI Pariter <strong>{esc(PARITER_LEI)}</strong> + numero pratica '
                               f'<strong>{esc(nr)}</strong> = identificativo <strong>{esc(ident)}</strong></p>'
                               '<p class="muted">Da riportare nella sezione "Panoramica dell\'offerta" della KIIS '
                               '(Allegato 18, Reg. del. UE 2022/2119).</p>')
            else:
                ident_inner = '<p class="muted">Registra prima la presa in carico (Fase 1) per generare il numero pratica e l\'identificativo.</p>'
            ident_panel = (f'<section class="panel"><div class="section-head">'
                           f'<h2>Identificativo dell\'offerta (KIIS)</h2></div>{ident_inner}'
                           '<p class="muted">L\'aumento di capitale e i casellari giudiziali sono condizioni '
                           'pre-pubblicazione (post delibera CdA): vanno acquisiti qui prima del go-live.</p></section>')
            body = (ident_panel + self.practice_onorabilita_panel(ctx, practice)
                    + self.practice_tab_validazione(ctx, practice) + self.practice_tab_campagna(ctx, practice))
        elif fase == "fase7":
            body = ('<section class="panel"><div class="section-head"><h2>Onboarding investitore e raccolta</h2></div>'
                    '<p class="muted">Classificazione investitore (sofisticato/non), test di conoscenza e simulazione perdite, '
                    'raccolta e custodia presso Banca Sella. Sezione design-first.</p></section>')
        else:  # fase8
            body = ('<section class="panel"><div class="section-head"><h2>Post-offerta e obblighi continuativi</h2></div>'
                    '<p class="muted">Monitoraggio campagna/emittente e comunicazioni continuative a CONSOB (SiCrowd).</p></section>')
        emails = self.phase_emails_html(ctx, practice, step["comms"], fase)
        # storico completo di tutte le comunicazioni (sempre in fondo, registro aggiornato)
        history = self.render_comms_history(ctx, practice)
        return head + body + emails + history

    def practice_email_defaults(self, practice, code):
        t = EMAIL_TEMPLATES[code]
        js = {}
        try:
            js = json.loads(practice["dossier_json"] or "{}")
        except (ValueError, TypeError):
            js = {}
        societa = (js.get("dati_struttura") or {}).get("societa") or {}
        proponente = practice["proponent_name"] or "-"
        nr = practice["pratica_no"] if "pratica_no" in practice.keys() and practice["pratica_no"] else ""
        pratica = ("PRA" + nr) if nr else f"PRA-{practice['id']:04d}"
        progetto = practice["project_title"]
        fill = lambda s: s.format(proponente=proponente, progetto=progetto, pratica=pratica)
        to_map = {"interno": "pariterequity@legalmail.it", "proponente": societa.get("pec") or "",
                  "investitore": "", "consob": ""}
        body = fill(t["body"])
        if code == "C3":
            # precompila l'elenco con la completezza del fascicolo (sempre dovuti + regola bilanci)
            # piu' i documenti di onorabilita' (autodichiarazioni/casellari) ancora mancanti.
            items = []
            with connect() as conn:
                comp = fascicolo_completezza(conn, practice)
                items.extend(comp["integrazione"])  # sempre dovuti mancanti + "Bilanci depositati: X su Y dovuti"
                ob = onorabilita_status(conn, practice["id"])
            if ob["configured"]:
                for s in ob["subjects"]:
                    role = ONORAB_ROLE_LABELS.get(s["role"], s["role"])
                    name = s["subject_name"] or "-"
                    if not s["autodich"]:
                        items.append(f"Autodichiarazione di onorabilita' - {role}: {name}")
                    if not s["casellario"]:
                        items.append(f"Casellario giudiziale - {role}: {name} (da acquisire prima della pubblicazione)")
            elenco = "\n".join(f"- {x}" for x in items) or "- (nessun documento mancante rilevato)"
            body = body.replace("- [specificare i documenti mancanti]", elenco)
        if code == "C3K":
            campi = kiis_missing_fields(practice)
            elenco = "\n".join(f"- {x}" for x in campi) or "- (nessun campo mancante rilevato)"
            body = body.replace("- [campi mancanti]", elenco)
        return {"label": t["label"], "recipient": to_map.get(t["to"], ""),
                "subject": fill(t["subject"]), "body": body}

    def practice_tab_processo(self, ctx, practice):
        pid = practice["id"]
        can_edit = user_can(ctx["user"], "manage_practice")
        step_state = {r["step_key"]: r["status"] for r in rows(
            "SELECT step_key, status FROM practice_process_steps WHERE practice_id = ?", (pid,))}
        sent = {}
        for em in rows("SELECT * FROM practice_emails WHERE practice_id = ? ORDER BY id DESC", (pid,)):
            sent.setdefault(em["code"], em)
        done = sum(1 for s in ONBOARDING_STEPS if step_state.get(s["key"]) == "completata")
        total = len(ONBOARDING_STEPS)
        pct = int(done * 100 / total) if total else 0
        cards = ""
        for n, s in enumerate(ONBOARDING_STEPS, start=1):
            st_done = step_state.get(s["key"]) == "completata"
            badge = '<span class="badge success">Completata</span>' if st_done else '<span class="badge neutral">Da fare</span>'
            toggle = ""
            if can_edit:
                toggle = f'''<form method="post" action="/pariter/practices/{pid}/step" style="display:inline">
                    {hidden_ctx(ctx)}<input type="hidden" name="step_key" value="{s['key']}">
                    <input type="hidden" name="status" value="{'da_fare' if st_done else 'completata'}">
                    <button class="button tiny" type="submit">{'Riapri' if st_done else 'Segna completata'}</button></form>'''
            goto = (f'<a class="button tiny" href="{rel_url("/pariter/practices/" + str(pid), ctx, {"tab": s["tab"]})}">Vai alla sezione</a>'
                    if s["tab"] else "")
            comms_html = ""
            for code in s["comms"]:
                d = self.practice_email_defaults(practice, code)
                last = sent.get(code)
                sent_info = (f'<span class="badge success">Inviata il {esc((last["sent_at"] or "")[:16])}</span>'
                             if last else '<span class="badge warning">Non inviata</span>')
                pre_to = esc(last["recipient"]) if last else esc(d["recipient"])
                pre_su = esc(last["subject"]) if last else esc(d["subject"])
                pre_bo = esc(last["body"]) if last else esc(d["body"])
                if can_edit:
                    comms_html += f"""
      <details class="comm-block">
        <summary><strong>{esc(d['label'])}</strong> &middot; {sent_info}</summary>
        <form class="form-grid" method="post" action="/pariter/practices/{pid}/email">
          {hidden_ctx(ctx)}<input type="hidden" name="code" value="{code}"><input type="hidden" name="step_key" value="{s['key']}">
          <label>Destinatario<input name="recipient" value="{pre_to}"></label>
          <label class="full-span">Oggetto<input name="subject" value="{pre_su}"></label>
          <label class="full-span">Testo<textarea name="body" rows="5">{pre_bo}</textarea></label>
          <div class="form-actions left">
            <button type="button" class="button secondary mailto-send">Apri nel client email</button>
            <button type="submit" class="button primary">Registra come inviata</button>
          </div>
        </form>
      </details>"""
                else:
                    comms_html += f'<p class="muted">{esc(d["label"])} - {sent_info}</p>'
            cards += f"""
<section class="panel step-card {'done' if st_done else ''}">
  <div class="section-head">
    <div><span class="step-num">{n}</span> <strong>{esc(s['fase'])} - {esc(s['titolo'])}</strong><br><span class="muted">{esc(s['attore'])}</span></div>
    <div class="header-badges">{badge}{goto}{toggle}</div>
  </div>
  <p>{esc(s['descrizione'])}</p>
  {('<div class="comm-list">' + comms_html + '</div>') if comms_html else ''}
</section>"""
        header = f"""
<section class="panel">
  <div class="section-head"><h2>Processo di onboarding e approvazione</h2><span class="badge {('success' if pct==100 else 'warning')}">{done}/{total} fasi completate</span></div>
  <p class="muted">Procedura operativa passo-passo (Reg. UE 2020/1503): dalla candidatura del proponente alla pubblicazione e raccolta. Completa ogni fase e invia le comunicazioni previste direttamente da qui.</p>
  <div class="progress-track"><div class="progress-fill" style="width:{pct}%"></div></div>
</section>"""
        return header + cards

    def practice_tab_riepilogo(self, ctx, practice):
        alerts = rows(
            "SELECT * FROM practice_alerts WHERE practice_id = ? AND alert_status = 'aperto' ORDER BY severity, id",
            (practice["id"],),
        )
        if alerts:
            alert_rows = "".join(
                f'''<li><span class="badge {"danger" if a["severity"]=="bloccante" else "warning"}">{esc(a["severity"])}</span> {esc(a["message"])} <span class="muted">({esc(a["source"])})</span>
                <form method="post" action="/pariter/practices/{practice['id']}/alert" style="display:inline;float:right">{hidden_ctx(ctx)}<input type="hidden" name="resolve_id" value="{a['id']}"><button class="button tiny" type="submit">Risolvi</button></form></li>'''
                for a in alerts
            )
            alerts_html = f'<ul class="clean-list">{alert_rows}</ul>'
        else:
            alerts_html = '<p class="muted">Nessun alert aperto.</p>'
        add_alert = f"""
  <form class="inline-form" method="post" action="/pariter/practices/{practice['id']}/alert" style="margin-top:12px">
    {hidden_ctx(ctx)}
    <select name="severity"><option value="bloccante">bloccante</option><option value="non_bloccante" selected>non bloccante</option></select>
    <input name="message" placeholder="Nuovo alert" required>
    <button class="button tiny" type="submit">Aggiungi</button>
  </form>"""
        nxt = next_step_summary(practice["status"])
        closed = practice["closed_at"] if "closed_at" in practice.keys() else ""
        if closed:
            is_admin = ctx["user"]["role"] == "admin"
            reopen = (f'''<form method="post" action="/pariter/practices/{practice['id']}/close" style="display:inline">
                {hidden_ctx(ctx)}<input type="hidden" name="action" value="reopen">
                <button class="button secondary" type="submit">Riapri pratica</button></form>''' if is_admin else "")
            closure_panel = f"""
<section class="panel">
  <div class="section-head"><h2>Pratica chiusa</h2><span class="badge neutral">Sola lettura</span></div>
  <div class="meta-grid">
    <div><span class="muted">Esito finale</span><br>{esc(practice['closure_outcome'] or '-')}</div>
    <div><span class="muted">Chiusa il</span><br>{esc((closed or '')[:16])}</div>
  </div>
  <p>{esc(practice['closure_note'] or '').replace(chr(10), '<br>')}</p>
  <div class="form-actions">{reopen}</div>
</section>"""
        elif user_can(ctx["user"], "close_practice"):
            closure_panel = f"""
<section class="panel">
  <div class="section-head"><h2>Chiusura / finalizzazione pratica</h2></div>
  <p class="muted">La chiusura archivia la pratica e ne blocca le modifiche (resta consultabile ed esportabile).</p>
  <form class="form-grid" method="post" action="/pariter/practices/{practice['id']}/close">
    {hidden_ctx(ctx)}<input type="hidden" name="action" value="close">
    <label>Esito finale<select name="closure_outcome"><option>Pubblicata / conclusa</option><option>Respinta</option><option>Archiviata senza seguito</option><option>Ritirata dal proponente</option></select></label>
    <label class="span2">Nota di chiusura<textarea name="closure_note" rows="2"></textarea></label>
    <div class="form-actions"><button class="button primary" type="submit">Chiudi pratica</button></div>
  </form>
</section>"""
        else:
            closure_panel = ""
        return f"""
<section class="panel">
  <div class="section-head"><h2>Riepilogo pratica</h2></div>
  <div class="meta-grid">
    <div><span class="muted">Stato</span><br><span class="badge {badge_class(practice_status_label(practice['status']))}">{esc(practice_status_label(practice['status']))}</span></div>
    <div><span class="muted">Prossimo step</span><br>{esc(nxt)}</div>
    <div><span class="muted">Origine dati</span><br>{esc(practice['source_system'] or '-')}</div>
    <div><span class="muted">Responsabile interno</span><br>{esc(practice['internal_owner'] or '-')}</div>
    <div><span class="muted">Creata</span><br>{esc((practice['created_at'] or '')[:16])}</div>
    <div><span class="muted">Aggiornata</span><br>{esc((practice['updated_at'] or '')[:16])}</div>
  </div>
</section>
<section class="panel">
  <div class="section-head"><h2>Alert aperti</h2></div>
  {alerts_html}
  {add_alert}
</section>
{closure_panel}
"""

    def practice_tab_storico(self, ctx, practice):
        history = rows(
            """
            SELECT h.*, u.name AS actor_name
            FROM practice_status_history h
            LEFT JOIN users u ON u.id = h.actor_id
            WHERE h.practice_id = ?
            ORDER BY h.id DESC
            """,
            (practice["id"],),
        )
        if not history:
            inner = '<p class="muted">Nessun passaggio di stato registrato.</p>'
        else:
            items = "".join(
                f"""<tr>
                  <td class="muted">{esc((h['created_at'] or '')[:16])}</td>
                  <td>{esc(practice_status_label(h['from_status']) if h['from_status'] else '-')} &rarr; <strong>{esc(practice_status_label(h['to_status']))}</strong></td>
                  <td>{esc(h['actor_name'] or '-')}</td>
                  <td>{esc(h['notes'] or '')}</td>
                </tr>"""
                for h in history
            )
            inner = f"""<table class="data-table compact">
    <thead><tr><th>Data</th><th>Transizione</th><th>Utente</th><th>Note</th></tr></thead>
    <tbody>{items}</tbody></table>"""
        return f'<section class="panel"><div class="section-head"><h2>Storico stati</h2></div>{inner}</section>'

    # ---- helper riusati dalle fasi del processo ----
    def render_internal_reviews(self, ctx, practice, types):
        existing = {r["review_type"]: r for r in rows(
            "SELECT * FROM internal_reviews WHERE practice_id = ?", (practice["id"],))}
        locked = bool(practice["closed_at"])
        resp_name, _f = org_responsabile(
            practice["platform_id"], ["controllo di 2", "funzioni di controllo", "controlli", "conflitt"],
            fallback="Responsabile delle funzioni di controllo")
        trow = row("SELECT firma_path, id_document_id FROM team_people WHERE platform_id = ? AND name = ? AND active = 1 LIMIT 1",
                   (practice["platform_id"], resp_name))
        firma_valid = bool(trow and trow["firma_path"] and trow["id_document_id"])
        pid = practice["id"]
        url = f"/pariter/practices/{pid}/internal-review"

        def remove_btn(rtype, label="Rimuovi"):
            return (f'<form method="post" action="{url}" style="display:inline" onsubmit="return confirm(\'Rimuovere la relazione?\')">'
                    f'{hidden_ctx(ctx)}<input type="hidden" name="review_type" value="{rtype}">'
                    f'<input type="hidden" name="action" value="remove"><button class="button tiny" type="submit">{label}</button></form>')

        def upload_form(rtype, label="Carica relazione a mano"):
            return (f'<form class="inline-form" method="post" action="{url}" enctype="multipart/form-data" style="margin-top:8px">'
                    f'{hidden_ctx(ctx)}<input type="hidden" name="review_type" value="{rtype}">'
                    f'<input type="hidden" name="action" value="upload"><input type="file" name="file" required>'
                    f'<button class="button tiny" type="submit">{label}</button></form>')

        def validate_btn(rtype):
            return (f'<form method="post" action="{url}" style="display:inline">'
                    f'{hidden_ctx(ctx)}<input type="hidden" name="review_type" value="{rtype}">'
                    f'<input type="hidden" name="action" value="validate"><button class="button primary" type="submit">Valida</button></form>')

        def preview(doc_id, open_default=False):
            """Anteprima inline del documento, sotto il titolo della relazione."""
            if not doc_id:
                return ""
            src = rel_url("/documents/" + str(doc_id) + "/download", ctx)
            return (f'<details class="doc-preview"{" open" if open_default else ""}><summary>Visualizza documento</summary>'
                    f'<iframe src="{src}" title="anteprima relazione"></iframe></details>')

        cards = ""
        for rtype in types:
            label = INTERNAL_REVIEW_LABELS.get(rtype, rtype)
            rev = existing.get(rtype)
            status = rev["review_status"] if rev else "non_generata"
            signed_by = rev["signed_by"] if (rev and "signed_by" in rev.keys()) else ""
            signed_at = rev["signed_at"] if (rev and "signed_at" in rev.keys()) else ""
            doc_id = rev["generated_document_id"] if (rev and rev["generated_document_id"]) else None
            dl = (f'<a class="button tiny" href="{rel_url("/documents/" + str(doc_id) + "/download", ctx)}">Scarica</a>'
                  if doc_id else "")

            if status == "validata":
                badge = '<span class="badge success">Validata - in istruttoria</span>'
                line = f'<p class="muted">Documento validato.{(" Firmata da <strong>" + esc(signed_by) + "</strong>.") if signed_by else ""}</p>'
                actions = dl + ("" if locked else " " + remove_btn(rtype, "Rimuovi e rifai"))
                body_html = line + preview(doc_id) + f'<div class="form-actions">{actions}</div>'
            elif status in ("firmata", "caricata") and doc_id:
                if status == "firmata":
                    badge = '<span class="badge warning">Firmata - da validare</span>'
                    line = f'<p class="muted">Firmata da <strong>{esc(signed_by or resp_name)}</strong> il {esc((signed_at or "")[:16])}. Manca la validazione.</p>'
                else:
                    badge = '<span class="badge warning">Caricata - da validare</span>'
                    line = '<p class="muted">Relazione caricata a mano. Manca la validazione.</p>'
                if locked:
                    actions = dl
                else:
                    actions = dl + " " + validate_btn(rtype) + " " + remove_btn(rtype)
                body_html = line + preview(doc_id) + f'<div class="form-actions">{actions}</div>'
            elif status == "bozza" and not locked:
                badge = '<span class="badge neutral">Bozza</span>'
                body_val = esc(rev["body"] or compose_internal_review_draft(practice, rtype))
                if firma_valid:
                    sign_btn = (f'<button class="button secondary" type="submit" name="action" value="sign" '
                                f'formaction="{url}">Firma ({esc(resp_name)})</button>')
                    firma_note = ""
                else:
                    sign_btn = ""
                    firma_note = (f'<p class="muted">Per firmare, carica firma e documento d\'identita\' di '
                                  f'<strong>{esc(resp_name)}</strong> nell\'<a href="{rel_url("/pariter/team", ctx)}">anagrafica team</a>. '
                                  f'In alternativa carica la relazione gia\' firmata.</p>')
                body_html = f"""
  {firma_note}
  <form class="form-grid" method="post" action="{url}">
    {hidden_ctx(ctx)}<input type="hidden" name="review_type" value="{rtype}"><input type="hidden" name="action" value="save_body">
    <label class="full-span">Testo della relazione (modificabile)<textarea name="body" rows="12" class="doc-draft">{body_val}</textarea></label>
    <div class="form-actions left"><button class="button secondary" type="submit">Salva modifiche</button>{dl}{sign_btn}{validate_btn(rtype)}{remove_btn(rtype)}</div>
  </form>
  {preview(doc_id)}
  {upload_form(rtype, "Oppure carica la relazione a mano")}"""
            else:
                badge = '<span class="badge neutral">Da generare</span>'
                if locked:
                    body_html = '<p class="muted">Pratica chiusa: relazione non generata.</p>'
                else:
                    body_html = f"""
  <p class="muted">Relazione non ancora generata. Generala dal modello (precompilata con i dati della pratica) e poi modificala, firmala e validala; oppure caricala a mano.</p>
  <form method="post" action="{url}" style="display:inline">
    {hidden_ctx(ctx)}<input type="hidden" name="review_type" value="{rtype}"><input type="hidden" name="action" value="generate">
    <button class="button primary" type="submit">Genera bozza</button>
  </form>
  {upload_form(rtype)}"""
            cards += f"""
<section class="panel">
  <div class="section-head"><h2>{esc(label)}</h2>{badge}</div>
  {body_html}
</section>"""
        return cards

    def render_docs_block(self, ctx, practice, phases, title, intro=""):
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        placeholders = ",".join("?" for _ in phases)
        docs = rows(f"SELECT * FROM practice_documents WHERE practice_id = ? AND phase IN ({placeholders}) ORDER BY phase, id",
                    (pid, *phases))
        missing_block = 0
        body_rows = ""
        for d in docs:
            present = bool(d["document_id"])
            # condizione bloccante: documento obbligatorio non ancora presente/da integrare
            is_block = bool(d["required"]) and (not present or d["doc_status"] in ("mancante", "da_integrare", "non_utilizzabile"))
            if is_block:
                missing_block += 1
            cond = ('<span class="badge danger">Bloccante</span>' if d["required"]
                    else '<span class="badge neutral">Facoltativo</span>')
            caricato = (f'<a class="button tiny" href="{rel_url("/documents/" + str(d["document_id"]) + "/download", ctx)}">Apri</a>'
                        if present else '<span class="badge warning">Mancante</span>')
            if locked:
                stato = f'<span class="badge {badge_class(DOC_STATUS_LABELS.get(d["doc_status"], d["doc_status"]))}">{esc(DOC_STATUS_LABELS.get(d["doc_status"], d["doc_status"]))}</span>'
            else:
                status_opts = "".join(
                    f'<option value="{k}"{" selected" if k == d["doc_status"] else ""}>{esc(v)}</option>'
                    for k, v in DOC_STATUS_LABELS.items())
                stato = f"""<form method="post" action="/pariter/practices/{pid}/document-status" class="inline-form">
                  {hidden_ctx(ctx)}<input type="hidden" name="doc_id" value="{d['id']}"><input type="hidden" name="back_fase" value="{phases[-1]}">
                  <select name="doc_status">{status_opts}</select>
                  <input name="reviewer_notes" placeholder="Note" value="{esc(d['reviewer_notes'] or '')}">
                  <button class="button tiny" type="submit">Salva</button></form>"""
                # tasto rapido "Verificato" quando il file e' presente e non gia' verificato
                if present and d["doc_status"] != "verificato":
                    stato += (f"""<form method="post" action="/pariter/practices/{pid}/document-status" class="inline-form" style="margin-top:4px">
                      {hidden_ctx(ctx)}<input type="hidden" name="doc_id" value="{d['id']}"><input type="hidden" name="back_fase" value="{phases[-1]}">
                      <input type="hidden" name="doc_status" value="verificato">
                      <button class="button tiny primary" type="submit">&check; Verificato</button></form>""")
            verif_badge = ('<span class="badge success">verificato</span>' if d["doc_status"] == "verificato" else "")
            body_rows += f"""<tr>
              <td>{esc(d['label'])} {verif_badge}</td>
              <td>{cond}</td>
              <td>{caricato}</td>
              <td>{stato}</td>
            </tr>"""
        if not body_rows:
            body_rows = '<tr><td colspan="4" class="muted">Nessun documento atteso.</td></tr>'
        summary = (f'<p><span class="badge danger">{missing_block} documenti obbligatori mancanti</span> '
                   f'<span class="muted">condizione per proseguire: integrare prima di passare alla valutazione.</span></p>'
                   if missing_block else
                   '<p><span class="badge success">Documentazione obbligatoria completa</span></p>')
        upload = ""
        if not locked and docs:
            upload_opts = "".join(f'<option value="{d["id"]}">{esc(d["label"])}</option>' for d in docs)
            upload = f"""
  <form class="inline-form" method="post" action="/pariter/practices/{pid}/document-upload" enctype="multipart/form-data" style="margin-top:10px">
    {hidden_ctx(ctx)}<input type="hidden" name="back_fase" value="{phases[-1]}">
    <select name="doc_id">{upload_opts}</select>
    <input type="file" name="file" required>
    <button class="button tiny" type="submit">Carica e collega</button>
  </form>"""
        intro_html = f'<p class="muted">{esc(intro)}</p>' if intro else ""
        return f"""
<section class="panel">
  <div class="section-head"><h2>{esc(title)}</h2></div>
  {intro_html}
  {summary}
  <table class="data-table compact"><thead><tr><th>Documento</th><th>Condizione</th><th>File</th><th>Stato</th></tr></thead><tbody>{body_rows}</tbody></table>
  {upload}
</section>"""

    # Controllo -> (etichetta, parole chiave per richiamare i documenti da esaminare)
    # (chiave, gruppo, etichetta controllo, parole chiave per richiamare i documenti)
    AMMISSIBILITA_CHECKS = [
        ("chk_casellario", "KYC art. 5 ECSP (checklist M2)", "Casellario giudiziale esponenti",
         ("casellario", "penal")),
        ("chk_carichi", "KYC art. 5 ECSP (checklist M2)", "Carichi pendenti esponenti",
         ("carichi", "pendenti")),
        ("chk_te", "KYC art. 5 ECSP (checklist M2)", "Titolare effettivo, residenza e giurisdizioni non cooperative",
         ("titolare", "giurisdiz", "residenza")),
        ("chk_autodich", "KYC art. 5 ECSP (checklist M2)", "Autodichiarazioni di onorabilita' acquisite",
         ("autodichiar", "onorabil", "dichiaraz")),
        ("chk_5m", "Limite di raccolta (checklist M3)", "Rispetto del limite di € 5.000.000 (raccolte ultimi 18 mesi)",
         ("limite", "raccolt", "5.000.000", "evidenze", "bilanci")),
    ]

    def render_ammissibilita_checklist(self, ctx, practice):
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        state = {r["step_key"]: r["status"] for r in rows(
            "SELECT step_key, status FROM practice_process_steps WHERE practice_id = ?", (pid,))}
        docs = rows("SELECT label, document_id FROM practice_documents WHERE practice_id = ? AND document_id IS NOT NULL",
                    (pid,))

        def docs_for(keywords):
            pool = [d for d in docs if any(k in (d["label"] or "").lower() for k in keywords)]
            if not pool:
                return '<span class="muted">nessun documento collegato</span>'
            return " ".join(
                f'<a class="button tiny" href="{rel_url("/documents/" + str(d["document_id"]) + "/download", ctx)}">{esc(d["label"])}</a>'
                for d in pool)

        rows_html = ""
        last_group = None
        for key, group, label, keywords in self.AMMISSIBILITA_CHECKS:
            if group != last_group:
                rows_html += f'<tr class="group-row"><td colspan="3"><strong>{esc(group)}</strong></td></tr>'
                last_group = group
            done = state.get(key) == "completata"
            badge = '<span class="badge success">Verificato</span>' if done else '<span class="badge neutral">Da verificare</span>'
            action = ""
            if not locked:
                action = (f'<form method="post" action="/pariter/practices/{pid}/step" style="display:inline">'
                          f'{hidden_ctx(ctx)}<input type="hidden" name="step_key" value="{key}">'
                          f'<input type="hidden" name="status" value="{"da_fare" if done else "completata"}">'
                          f'<input type="hidden" name="back_fase" value="fase2">'
                          f'<button class="button tiny" type="submit">{"Annulla" if done else "Segna verificato"}</button></form>')
            rows_html += (f'<tr><td>{esc(label)}<br><span class="doc-links">{docs_for(keywords)}</span></td>'
                          f'<td>{badge}</td><td>{action}</td></tr>')
        return f"""
<section class="panel">
  <div class="section-head"><h2>Verifiche di ammissibilita' (responsabile compliance)</h2></div>
  <p class="muted">KYC art. 5 ECSP sul titolare del progetto e sul titolare effettivo, e rispetto del limite di 5 milioni. Apri i documenti collegati, verificali e spunta "verificato". Sotto si formalizza la relazione art. 5/AML.</p>
  <table class="data-table compact"><thead><tr><th>Controllo e documenti</th><th>Stato</th><th></th></tr></thead><tbody>{rows_html}</tbody></table>
</section>"""

    def render_completezza_panel(self, ctx, practice):
        """Completezza del fascicolo in Fase 2: sempre dovuti vs 'se disponibili' + regola bilanci."""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        with connect() as conn:
            comp = fascicolo_completezza(conn, practice)

        def line(label, present, present_txt="Presente", absent_txt="Mancante", absent_cls="danger"):
            b = (f'<span class="badge success">{present_txt}</span>' if present
                 else f'<span class="badge {absent_cls}">{absent_txt}</span>')
            return f'<tr><td>{esc(label)}</td><td>{b}</td></tr>'

        sempre_rows = "".join(line(s["label"], s["present"]) for s in comp["sempre"])
        # condizionati non-bilanci: assenza = "non disponibile", mai bloccante
        cond_rows = "".join(
            line(c["label"], c["present"], absent_txt="Non disponibile", absent_cls="neutral")
            for c in comp["condizionati"])

        # blocco bilanci: si chiedono gli ultimi due bilanci, ma solo per gli esercizi gia' chiusi e depositati
        if comp["esercizi_set"]:
            dov = comp["bilanci_dovuti"]
            pres = comp["bilanci_presenti"]
            if dov == 0:
                bil_badge = '<span class="badge success">Nessun bilancio richiesto</span>'
                bil_state = ('<p><strong>Bilanci richiesti: 0.</strong> La societa\' non ha ancora esercizi chiusi e depositati '
                             '(neo costituita): nessun bilancio dovuto.</p>')
            else:
                manca = max(0, dov - pres)
                if comp["bilanci_completi"]:
                    bil_badge = '<span class="badge success">Completo</span>'
                    stato_txt = "tutti presenti."
                else:
                    bil_badge = f'<span class="badge danger">Manca{"no" if manca != 1 else ""} {manca}</span>'
                    stato_txt = f"ne manca{'no' if manca != 1 else ''} {manca}."
                quanti = f"ultimi {dov} esercizi chiusi e depositati" if dov > 1 else "ultimo esercizio chiuso e depositato"
                bil_state = (f'<p><strong>Bilanci richiesti: {dov}</strong> ({quanti}, su {comp["esercizi"]} totali). '
                             f'Presenti: <strong>{pres}</strong> &mdash; {stato_txt} {bil_badge}</p>')
        else:
            bil_badge = ""
            bil_state = ('<p><span class="badge warning">Da indicare</span> Inserisci quanti esercizi sono gia\' chiusi e depositati '
                         'e quanti bilanci sono stati prodotti: si richiedono gli ultimi due bilanci (meno se la societa\' e\' piu\' giovane).</p>')

        bil_form = ""
        if not locked:
            bil_form = (f'<form method="post" action="/pariter/practices/{pid}/completezza" class="inline-form" style="margin-top:8px">'
                        f'{hidden_ctx(ctx)}<input type="hidden" name="back_fase" value="fase2">'
                        f'<label>Esercizi chiusi e depositati '
                        f'<input type="number" min="0" max="50" name="esercizi_chiusi" value="{comp["esercizi"] if comp["esercizi_set"] else ""}" style="width:80px"></label> '
                        f'<label>Bilanci presenti '
                        f'<input type="number" min="0" max="2" name="bilanci_presenti" value="{comp["bilanci_presenti"]}" style="width:80px"></label> '
                        f'<button class="button tiny" type="submit">Salva</button></form>')

        if comp["fascicolo_completo"]:
            head_badge = '<span class="badge success">Fascicolo completo &mdash; procedibile</span>'
        else:
            head_badge = '<span class="badge danger">Fascicolo incompleto</span>'

        integr = ""
        if comp["integrazione"]:
            lis = "".join(f"<li>{esc(x)}</li>" for x in comp["integrazione"])
            integr = (f'<p class="muted">Integrazione (C3) dovuta solo per i <strong>sempre dovuti</strong> mancanti e per i bilanci dovuti:</p>'
                      f'<ul class="clean-list">{lis}</ul>')
        else:
            integr = '<p class="muted">Nessuna integrazione necessaria: i documenti sempre dovuti sono presenti e la regola bilanci e\' soddisfatta. I documenti "se disponibili" assenti non sono bloccanti.</p>'

        return f"""
<section class="panel">
  <div class="section-head"><h2>Completezza del fascicolo (Allegato 5_1, sez. 5.1.e)</h2>{head_badge}</div>
  <p class="muted">Distingue i documenti <strong>sempre dovuti</strong> (la cui assenza richiede integrazione) dai documenti <strong>"se disponibili"</strong> (la cui assenza per indisponibilita' oggettiva non e' bloccante). Una neo costituita priva di bilanci, piano storico e sito risulta comunque completa.</p>
  <h3>Documenti sempre dovuti</h3>
  <table class="data-table compact"><thead><tr><th>Documento</th><th>Stato</th></tr></thead><tbody>{sempre_rows}</tbody></table>
  <h3>Bilanci (regola sugli esercizi chiusi)</h3>
  {bil_state}
  {bil_form}
  <h3>Documenti "se disponibili" (non bloccanti)</h3>
  <table class="data-table compact"><thead><tr><th>Documento</th><th>Stato</th></tr></thead><tbody>{cond_rows}</tbody></table>
  {integr}
</section>"""

    def ammissibilita_blockers(self, practice):
        """Motivi che impediscono di validare l'ammissibilita' e passare alla valutazione."""
        pid = practice["id"]
        reasons = []
        with connect() as conn:
            comp = fascicolo_completezza(conn, practice)
        if not comp["esercizi_set"]:
            reasons.append("indicare il numero di esercizi chiusi e depositati (regola bilanci)")
        if comp["missing_sempre"]:
            reasons.append(f"{len(comp['missing_sempre'])} documenti sempre dovuti mancanti")
        if comp["esercizi_set"] and not comp["bilanci_completi"]:
            reasons.append(f"bilanci depositati: {comp['bilanci_presenti']} su {comp['bilanci_dovuti']} dovuti")
        # altri documenti obbligatori di Fase 2 (es. KYC), distinti dai sempre dovuti di Fase 1
        missing_f2 = rows(
            """SELECT label FROM practice_documents WHERE practice_id = ? AND required = 1
               AND phase = 'fase2' AND (document_id IS NULL OR doc_status IN ('mancante','da_integrare','non_utilizzabile'))""",
            (pid,))
        if missing_f2:
            reasons.append(f"{len(missing_f2)} documenti obbligatori di Fase 2 da integrare")
        aml = row("SELECT review_status FROM internal_reviews WHERE practice_id = ? AND review_type = 'aml_art5'", (pid,))
        if not (aml and aml["review_status"] == "validata"):
            reasons.append("relazione art. 5/AML non ancora validata")
        return reasons

    def render_validazione_ammissibilita(self, ctx, practice):
        """Gate finale di Fase 2: se i documenti e l'AML sono validati, invia C4 e si passa alla valutazione."""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        already = PRACTICE_STATUS_INDEX.get(practice["status"], 0) >= PRACTICE_STATUS_INDEX.get("pronto_cvoi", 999)
        if already:
            return ('<section class="panel"><div class="section-head"><h2>Validazione ammissibilita\'</h2>'
                    '<span class="badge success">Ammissibilita\' validata</span></div>'
                    '<p class="muted">Esito documentale positivo (C4) inviato: la pratica e\' passata alla valutazione di merito (CVOI).</p></section>')
        reasons = self.ammissibilita_blockers(practice)
        if reasons:
            lis = "".join(f"<li>{esc(r)}</li>" for r in reasons)
            inner = (f'<p class="muted">Per validare l\'ammissibilita\' e passare alla valutazione restano da completare:</p>'
                     f'<ul class="clean-list">{lis}</ul>'
                     f'<div class="form-actions"><button class="button primary" type="submit" disabled>Valida e avvia valutazione</button></div>')
        else:
            btn = ('<button class="button primary" type="submit">Valida ammissibilita\', invia C4 e avvia valutazione</button>'
                   if not locked else "")
            inner = (f'<p>Documenti obbligatori completi e relazione art. 5/AML validata: l\'ammissibilita\' puo\' essere validata. '
                     f'Verra\' inviata la comunicazione di esito positivo (C4) e la pratica passera\' alla valutazione di merito (CVOI).</p>'
                     f'<form method="post" action="/pariter/practices/{pid}/validate-ammissibilita">{hidden_ctx(ctx)}'
                     f'<div class="form-actions">{btn}</div></form>')
        return f"""
<section class="panel">
  <div class="section-head"><h2>Validazione ammissibilita'</h2><span class="badge warning">Da validare</span></div>
  {inner}
</section>"""

    def practice_documentale_requests(self, ctx, practice):
        reqs = rows("SELECT * FROM integration_requests WHERE practice_id = ? ORDER BY id DESC", (practice["id"],))
        if not reqs:
            return ""
        req_rows = "".join(
            f"""<tr><td>{esc(r['subject'])}</td><td><span class="badge {badge_class(r['req_status'])}">{esc(r['req_status'])}</span></td>
            <td class="muted">{esc((r['created_at'] or '')[:10])}</td></tr>"""
            for r in reqs)
        return f"""
<section class="panel">
  <div class="section-head"><h2>Richieste di integrazione</h2></div>
  <table class="data-table compact"><thead><tr><th>Oggetto</th><th>Stato</th><th>Aperta</th></tr></thead><tbody>{req_rows}</tbody></table>
</section>"""

    def render_registrazione_panel(self, ctx, practice):
        """Registrazione candidatura: avvenuta dal portale, carica anagrafica + documenti allegati."""
        return f"""
<section class="panel">
  <div class="section-head"><h2>Registrazione candidatura</h2><span class="badge success">Registrata</span></div>
  <p class="muted">Candidatura registrata dal portale www.pariterequity.com (e PEC interna): sono stati caricati l'anagrafica del proponente e i documenti allegati (sotto). <em>Connessione PEC/portale: design-first (connettore da collegare).</em></p>
</section>"""

    def render_intake_panel(self, ctx, practice):
        """Presa in carico: pulsante che genera il numero pratica e abilita la comunicazione C2."""
        pid = practice["id"]
        nr = practice["pratica_no"] if "pratica_no" in practice.keys() else ""
        locked = bool(practice["closed_at"])
        can = user_can(ctx["user"], "manage_practice") and not locked
        if nr:
            ident = offer_identifier(practice)
            annul = ""
            if can:
                annul = (f'<form method="post" action="/pariter/practices/{pid}/intake" style="display:inline" '
                         f'onsubmit="return confirm(\'Annullare la presa in carico? Il numero pratica verra\\\' azzerato.\')">'
                         f'{hidden_ctx(ctx)}<input type="hidden" name="action" value="annul">'
                         f'<button class="button tiny" type="submit">Annulla presa in carico</button></form>')
            return f"""
<section class="panel">
  <div class="section-head"><h2>Presa in carico</h2><span class="badge success">Presa in carico</span></div>
  <div class="meta-grid">
    <div><span class="muted">Numero pratica (interno)</span><br><strong>{esc(nr)}</strong></div>
    <div><span class="muted">Identificativo offerta (in KIIS, alla pubblicazione)</span><br><span class="muted">LEI + nr →</span> {esc(ident)}</div>
  </div>
  <p class="muted">Numero pratica generato. Invia ora la comunicazione di presa in carico (C2) al proponente, qui sotto. Lo stesso numero, unito al LEI di Pariter, forma l'Identificativo dell'offerta nella KIIS (pubblicazione).</p>
  <div class="form-actions">{annul}</div>
</section>"""
        action = ""
        if can:
            action = (f'<form method="post" action="/pariter/practices/{pid}/intake">{hidden_ctx(ctx)}'
                      f'<input type="hidden" name="action" value="take">'
                      f'<button class="button primary" type="submit">Prendi in carico (genera numero pratica)</button></form>')
        return f"""
<section class="panel">
  <div class="section-head"><h2>Presa in carico</h2><span class="badge warning">Da prendere in carico</span></div>
  <p class="muted">Premi <strong>Prendi in carico</strong> per generare il numero pratica a 8 cifre e precompilare la comunicazione di presa in carico (C2) da inviare al proponente.</p>
  <div class="form-actions">{action}</div>
</section>"""

    def render_anagrafica_panel(self, ctx, practice):
        """Anagrafica del proponente: dati caricati dal dossier, modificabili e risalvabili."""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        can = user_can(ctx["user"], "manage_practice") and not locked
        js = {}
        try:
            js = json.loads(practice["dossier_json"] or "{}")
        except (ValueError, TypeError):
            js = {}
        ds = js.get("dati_struttura") or {}
        soc = ds.get("societa") or {}
        rep = ds.get("legaleRappresentante") or {}
        off = ds.get("offertaFase1") or {}
        denom = practice["proponent_name"] or soc.get("denominazione") or ""
        def cell(lbl, val):
            return f'<div><span class="muted">{esc(lbl)}</span><br><strong>{esc(val or "-")}</strong></div>'
        rows_html = "".join([
            cell("Denominazione", denom),
            cell("Progetto", practice["project_title"]),
            cell("Forma giuridica", soc.get("forma")),
            cell("Sede legale", soc.get("sedeLegale")),
            cell("P. IVA", soc.get("pIva")),
            cell("PEC", soc.get("pec")),
            cell("Legale rappresentante", (rep.get("nome") or "") + ((" (" + rep.get("carica") + ")") if rep.get("carica") else "")),
            cell("Importo target", off.get("importoTarget")),
            cell("Importo massimo", off.get("importoMax")),
            cell("Pre-money", off.get("preMoney")),
            cell("Equity offerta", off.get("equity")),
            cell("Strumento", off.get("strumento")),
        ])
        edit = ""
        if can:
            def fld(name, label, val):
                return f'<label>{esc(label)}<input name="{name}" value="{esc(val or "")}"></label>'
            edit = f"""
  <details class="comm-block">
    <summary>Modifica anagrafica</summary>
    <form class="form-grid" method="post" action="/pariter/practices/{pid}/anagrafica">
      {hidden_ctx(ctx)}
      {fld("denominazione", "Denominazione", denom)}
      {fld("project_title", "Progetto", practice["project_title"])}
      {fld("forma", "Forma giuridica", soc.get("forma"))}
      {fld("sedeLegale", "Sede legale", soc.get("sedeLegale"))}
      {fld("pIva", "P. IVA", soc.get("pIva"))}
      {fld("pec", "PEC", soc.get("pec"))}
      {fld("rep_nome", "Legale rappresentante", rep.get("nome"))}
      {fld("rep_carica", "Carica", rep.get("carica"))}
      {fld("importoTarget", "Importo target", off.get("importoTarget"))}
      {fld("importoMax", "Importo massimo", off.get("importoMax"))}
      {fld("preMoney", "Pre-money", off.get("preMoney"))}
      {fld("equity", "Equity offerta", off.get("equity"))}
      {fld("strumento", "Strumento", off.get("strumento"))}
      <div class="form-actions"><button class="button primary" type="submit">Salva anagrafica</button></div>
    </form>
  </details>"""
        return f"""
<section class="panel">
  <div class="section-head"><h2>Anagrafica proponente</h2><span class="badge neutral">Dati caricati</span></div>
  <p class="muted">Dati acquisiti dal dossier inviato dal proponente. Puoi modificarli e risalvarli (fino al collegamento dell'API/portale, l'inserimento e' anche manuale).</p>
  <div class="meta-grid">{rows_html}</div>
  {edit}
</section>"""

    def render_full_dossier(self, ctx, practice, title="Dossier documentale completo per la valutazione"):
        """Elenco di tutti i documenti collegati alla pratica (per il CVOI)."""
        ds = rows("SELECT id, origin, category, title FROM documents WHERE practice_id = ? ORDER BY origin, id",
                  (practice["id"],))
        if not ds:
            inner = '<p class="muted">Nessun documento collegato alla pratica.</p>'
        else:
            items = "".join(
                f'<li><span class="muted">[{esc(d["origin"] or "-")}]</span> {esc(d["title"])} '
                f'<a class="button tiny" href="{rel_url("/documents/" + str(d["id"]) + "/download", ctx)}">Apri</a></li>'
                for d in ds)
            inner = f'<ul class="clean-list">{items}</ul>'
        return f"""
<section class="panel">
  <div class="section-head"><h2>{esc(title)}</h2><span class="badge neutral">{len(ds)} documenti</span></div>
  <p class="muted">Il CVOI riceve l'intero dossier documentale della pratica insieme al template di valutazione (scoring) qui sotto.</p>
  {inner}
</section>"""

    def render_comms_history(self, ctx, practice):
        """Storico delle comunicazioni inviate (tutte le fasi)."""
        ems = rows("SELECT * FROM practice_emails WHERE practice_id = ? ORDER BY id DESC", (practice["id"],))
        if not ems:
            inner = '<p class="muted">Nessuna comunicazione inviata finora.</p>'
        else:
            items = "".join(
                f"""<tr><td><strong>{esc(e['code'])}</strong></td><td>{esc(e['recipient'] or '-')}</td>
                <td>{esc(e['subject'] or '-')}</td><td class="muted">{esc((e['sent_at'] or '')[:16])}</td></tr>"""
                for e in ems)
            inner = (f'<table class="data-table compact"><thead><tr><th>Codice</th><th>Destinatario</th>'
                     f'<th>Oggetto</th><th>Inviata</th></tr></thead><tbody>{items}</tbody></table>')
        return f"""
<section class="panel">
  <div class="section-head"><h2>Storico comunicazioni</h2></div>
  {inner}
</section>"""

    def phase_emails_html(self, ctx, practice, codes, fase="",
                          title="Comunicazioni di questa fase",
                          intro="La mail compare qui, nel momento del processo. Modificala, aprila nel client e registrala come inviata.",
                          open_codes=(), back_sub=""):
        if not codes:
            return ""
        sub_field = f'<input type="hidden" name="back_sub" value="{esc(back_sub)}">' if back_sub else ""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        sent = {}
        for em in rows("SELECT * FROM practice_emails WHERE practice_id = ? ORDER BY id DESC", (pid,)):
            sent.setdefault(em["code"], em)
        blocks = ""
        for code in codes:
            d = self.practice_email_defaults(practice, code)
            last = sent.get(code)
            badge = (f'<span class="badge success">Inviata il {esc((last["sent_at"] or "")[:16])}</span>' if last
                     else '<span class="badge warning">Non inviata</span>')
            to_v = esc(last["recipient"]) if last else esc(d["recipient"])
            su_v = esc(last["subject"]) if last else esc(d["subject"])
            bo_v = esc(last["body"]) if last else esc(d["body"])
            if code == "C1":
                # La PEC la invia direttamente il portale: qui si attesta soltanto l'invio.
                attest = "" if (locked or last) else (
                    f'<form method="post" action="/pariter/practices/{pid}/email" style="display:inline">'
                    f'{hidden_ctx(ctx)}<input type="hidden" name="code" value="C1"><input type="hidden" name="step_key" value="{fase}">'
                    f'<input type="hidden" name="back_fase" value="{fase}"><input type="hidden" name="recipient" value="{esc(d["recipient"])}">'
                    f'<input type="hidden" name="subject" value="{esc(d["subject"])}"><input type="hidden" name="body" value="PEC di ricezione inviata automaticamente dal portale.">'
                    f'<button class="button tiny" type="submit">Attesta invio PEC</button></form>')
                blocks += f'<p>{esc(d["label"])} &middot; <span class="muted">inviata dal portale</span> &middot; {badge} {attest}</p>'
                continue
            if locked:
                blocks += f'<p class="muted">{esc(d["label"])} - {badge}</p>'
                continue
            is_open = (code in open_codes) and not last
            blocks += f"""
      <details class="comm-block"{' open' if is_open else ''}>
        <summary><strong>{esc(d['label'])}</strong> &middot; {badge}</summary>
        <form class="form-grid" method="post" action="/pariter/practices/{pid}/email">
          {hidden_ctx(ctx)}<input type="hidden" name="code" value="{code}"><input type="hidden" name="step_key" value="{fase}"><input type="hidden" name="back_fase" value="{fase}">{sub_field}
          <label>Destinatario<input name="recipient" value="{to_v}"></label>
          <label class="full-span">Oggetto<input name="subject" value="{su_v}"></label>
          <label class="full-span">Testo<textarea name="body" rows="5">{bo_v}</textarea></label>
          <div class="form-actions left">
            <button type="button" class="button secondary mailto-send">Apri nel client email</button>
            <button type="submit" class="button primary">Registra come inviata</button>
          </div>
        </form>
      </details>"""
        return f"""
<section class="panel">
  <div class="section-head"><h2>{esc(title)}</h2></div>
  <p class="muted">{esc(intro)}</p>
  {blocks}
</section>"""

    def practice_tab_documentale(self, ctx, practice):
        pid = practice["id"]
        phase_order = ["fase1", "fase2", "fase3", "fase4"]
        phase_labels = {"fase1": "Fase 1 - Onboarding", "fase2": "Fase 2 - Offerta",
                        "fase3": "Fase 3 - KIIS", "fase4": "Fase 4 - Pre go-live"}
        phase_state = {r["phase"]: r["status"] for r in rows(
            "SELECT phase, status FROM practice_phases WHERE practice_id = ?", (pid,)
        )}
        for ph in phase_order:
            phase_state.setdefault(ph, "da_completare")
        dtab = self.get_query_param("dtab") or "fase1"
        if dtab not in phase_order:
            dtab = "fase1"
        # sotto-schede orizzontali per fase, con spunta di completamento
        sub = ""
        for i, ph in enumerate(phase_order):
            done = phase_state[ph] == "completata"
            prev_done = i == 0 or phase_state[phase_order[i - 1]] == "completata"
            mark = " &check;" if done else ("" if prev_done else " &middot;")
            sub += (f'<a class="subtab {"active" if ph == dtab else ""}" '
                    f'href="{rel_url("/pariter/practices/" + str(pid), ctx, {"tab": "documentale", "dtab": ph})}">'
                    f'{esc(phase_labels[ph])}{mark}</a>')

        docs = rows("SELECT * FROM practice_documents WHERE practice_id = ? AND phase = ? ORDER BY id", (pid, dtab))
        doc_rows = ""
        for d in docs:
            status_opts = "".join(
                f'<option value="{k}"{" selected" if k == d["doc_status"] else ""}>{esc(v)}</option>'
                for k, v in DOC_STATUS_LABELS.items()
            )
            link = (
                f'<a class="button tiny" href="{rel_url("/documents/" + str(d["document_id"]) + "/download", ctx)}">File</a>'
                if d["document_id"] else '<span class="muted">-</span>'
            )
            integ = (
                '<span class="badge warning">Integraz. richiesta</span>'
                if d["integration_requested"] else
                f'''<form method="post" action="/pariter/practices/{pid}/integration-request" style="display:inline">
                    {hidden_ctx(ctx)}<input type="hidden" name="practice_document_id" value="{d['id']}">
                    <input type="hidden" name="subject" value="Integrazione: {esc(d['label'])}">
                    <button class="button tiny" type="submit">Richiedi integrazione</button></form>'''
            )
            doc_rows += f"""<tr>
              <td>{esc(d['label'])}{' <span class="muted">*</span>' if d['required'] else ''}</td>
              <td>
                <form method="post" action="/pariter/practices/{pid}/document-status" class="inline-form">
                  {hidden_ctx(ctx)}<input type="hidden" name="doc_id" value="{d['id']}">
                  <select name="doc_status">{status_opts}</select>
                  <input name="reviewer_notes" placeholder="Note revisore" value="{esc(d['reviewer_notes'] or '')}">
                  <button class="button tiny" type="submit">Salva</button>
                </form>
              </td>
              <td>{link}</td>
              <td>{integ}</td>
            </tr>"""
        if not doc_rows:
            doc_rows = '<tr><td colspan="4" class="muted">Nessun documento atteso in questa fase.</td></tr>'

        done = phase_state[dtab] == "completata"
        idx = phase_order.index(dtab)
        prev_done = idx == 0 or phase_state[phase_order[idx - 1]] == "completata"
        complete_action = (
            f'''<form method="post" action="/pariter/practices/{pid}/phase" style="display:inline">
                {hidden_ctx(ctx)}<input type="hidden" name="phase" value="{dtab}">
                <input type="hidden" name="status" value="{'da_completare' if done else 'completata'}">
                <button class="button {'secondary' if done else 'primary'}" type="submit">{'Riapri fase' if done else 'Segna fase completata'}</button></form>'''
        )
        phase_badge = (f'<span class="badge success">Completata</span>' if done
                       else (f'<span class="badge neutral">Da completare</span>' if prev_done
                             else f'<span class="badge warning">In attesa fase precedente</span>'))
        unlock_note = ""
        if not done and idx < 3:
            nxt = phase_labels[phase_order[idx + 1]]
            unlock_note = f'<p class="muted">Completando questa fase si sblocca: <strong>{esc(nxt)}</strong> (anche lato proponente nel sistema unico).</p>'

        upload_opts = "".join(
            f'<option value="{d["id"]}">{esc(d["label"])}</option>' for d in docs
        )
        upload = f"""
<section class="panel">
  <div class="section-head"><h2>Carica documento ({esc(phase_labels[dtab])})</h2></div>
  <form class="form-grid" method="post" action="/pariter/practices/{pid}/document-upload" enctype="multipart/form-data">
    {hidden_ctx(ctx)}
    <label>Voce checklist<select name="doc_id">{upload_opts}</select></label>
    <label>File<input type="file" name="file" required></label>
    <div class="form-actions"><button class="button primary" type="submit">Carica e collega</button></div>
  </form>
</section>""" if upload_opts else ""
        phase_panel = f"""
<div class="subtabs wrap">{sub}</div>
<section class="panel">
  <div class="section-head"><h2>{esc(phase_labels[dtab])}</h2><div class="header-badges">{phase_badge}{complete_action}</div></div>
  {unlock_note}
  <table class="data-table compact">
    <thead><tr><th>Documento</th><th>Stato / note revisore</th><th>File</th><th>Integrazione</th></tr></thead>
    <tbody>{doc_rows}</tbody>
  </table>
</section>"""
        reqs = rows(
            "SELECT * FROM integration_requests WHERE practice_id = ? ORDER BY id DESC",
            (practice["id"],),
        )
        if reqs:
            req_rows = "".join(
                f"""<tr><td>{esc(r['subject'])}</td><td><span class="badge {badge_class(r['req_status'])}">{esc(r['req_status'])}</span></td>
                <td class="muted">{esc((r['created_at'] or '')[:10])}</td>
                <td>{'' if r['req_status']=='chiusa' else f'''<form method="post" action="/pariter/practices/{practice['id']}/integration-request" style="display:inline">{hidden_ctx(ctx)}<input type="hidden" name="close_id" value="{r['id']}"><button class="button tiny" type="submit">Chiudi</button></form>'''}</td></tr>"""
                for r in reqs
            )
            req_panel = f"""
<section class="panel">
  <div class="section-head"><h2>Richieste di integrazione</h2></div>
  <table class="data-table compact"><thead><tr><th>Oggetto</th><th>Stato</th><th>Aperta</th><th></th></tr></thead><tbody>{req_rows}</tbody></table>
</section>"""
        else:
            req_panel = ""
        return self.practice_onorabilita_panel(ctx, practice) + phase_panel + upload + req_panel

    def practice_tab_interne(self, ctx, practice):
        existing = {r["review_type"]: r for r in rows(
            "SELECT * FROM internal_reviews WHERE practice_id = ?", (practice["id"],)
        )}
        cards = []
        for rtype, label in INTERNAL_REVIEW_TYPES:
            rev = existing.get(rtype)
            status = rev["review_status"] if rev else "non_generata"
            doc_link = (
                f'<a class="button tiny" href="{rel_url("/documents/" + str(rev["generated_document_id"]) + "/download", ctx)}">Apri bozza</a>'
                if rev and rev["generated_document_id"] else ""
            )
            validate_btn = (
                f'''<form method="post" action="/pariter/practices/{practice['id']}/internal-review" style="display:inline">
                    {hidden_ctx(ctx)}<input type="hidden" name="review_type" value="{rtype}"><input type="hidden" name="action" value="validate">
                    <button class="button tiny" type="submit">Valida</button></form>'''
                if rev and rev["review_status"] in {"bozza", "da_integrare"} else ""
            )
            cards.append(f"""
<section class="panel">
  <div class="section-head">
    <h2>{esc(label)}</h2>
    <span class="badge {badge_class(status)}">{esc(status.replace('_',' '))}</span>
  </div>
  <p class="muted">{esc(rev['outcome']) if rev and rev['outcome'] else 'Relazione interna generata dai dati del dossier e validata manualmente.'}</p>
  <div class="form-actions">
    <form method="post" action="/pariter/practices/{practice['id']}/internal-review" style="display:inline">
      {hidden_ctx(ctx)}<input type="hidden" name="review_type" value="{rtype}"><input type="hidden" name="action" value="generate">
      <button class="button {'secondary' if rev else 'primary'}" type="submit">{'Rigenera bozza' if rev else 'Genera bozza (IA)'}</button>
    </form>
    {doc_link}
    {validate_btn}
  </div>
</section>""")
        validated = sum(1 for r in existing.values() if r["review_status"] == "validata")
        total = len(INTERNAL_REVIEW_TYPES)
        already_sent = PRACTICE_STATUS_INDEX.get(practice["status"], 0) >= PRACTICE_STATUS_INDEX["pronto_cvoi"]
        if already_sent:
            send_panel = '<section class="panel"><p class="badge success">Dossier gia\' inviato al Comitato Tecnico.</p></section>'
        elif validated >= total:
            send_panel = f"""
<section class="panel">
  <div class="section-head"><h2>Invia al Comitato Tecnico</h2><span class="badge success">{validated}/{total} verifiche validate</span></div>
  <p class="muted">Le verifiche interne sono complete e validate: il dossier puo' essere trasmesso al Comitato Tecnico per la valutazione CVOI.</p>
  <form method="post" action="/pariter/practices/{practice['id']}/transition" style="display:inline">
    {hidden_ctx(ctx)}<input type="hidden" name="target" value="pronto_cvoi">
    <button class="button primary" type="submit">Invia al Comitato Tecnico</button></form>
</section>"""
        else:
            send_panel = f'<section class="panel"><div class="section-head"><h2>Invia al Comitato Tecnico</h2><span class="badge warning">{validated}/{total} verifiche validate</span></div><p class="muted">Genera e valida tutte le verifiche interne per sbloccare l\'invio.</p></section>'
        return "".join(cards) + send_panel

    def practice_tab_cvoi(self, ctx, practice):
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        is_admin = ctx["user"]["role"] == "admin"
        with connect() as conn:
            c = compute_cvoi_collegial(conn, practice)
            report = conn.execute("SELECT * FROM cvoi_reports WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
        evaluators = c["evaluators"]
        ev_ids = [e["id"] for e in evaluators]
        # valutatore da compilare: admin sceglie (?ev=), altrimenti se stesso
        sel = None
        q = self.get_query_param("ev")
        if is_admin and q and q.isdigit() and int(q) in ev_ids:
            sel = int(q)
        elif ctx["user_id"] in ev_ids:
            sel = ctx["user_id"]
        elif is_admin and ev_ids:
            sel = ev_ids[0]
        can_fill = (not locked) and (sel is not None) and (is_admin or sel == ctx["user_id"])

        # --- Stato valutatori (fatto/fatto/fatto) ---
        st_badge = {"da_compilare": '<span class="badge neutral">Da compilare</span>',
                    "salvato": '<span class="badge warning">Salvato (da validare)</span>',
                    "validato": '<span class="badge success">Validato &check;</span>',
                    "astenuto": '<span class="badge neutral">Astenuto</span>'}
        st_rows = "".join(
            f'<tr><td>{esc(e["name"])}</td><td>{st_badge.get(c["ev_status"][e["id"]], c["ev_status"][e["id"]])}</td></tr>'
            for e in evaluators)
        status_panel = f"""
<section class="panel">
  <div class="section-head"><h2>Stato della valutazione collegiale</h2>
    <span class="badge {"success" if c["all_done"] else "warning"}">{c["n_confirmed"]} validati su {len(evaluators)}</span></div>
  <p class="muted">Ogni componente compila i propri punteggi, li salva e li <strong>valida</strong>. Quando tutti hanno validato (salvo astensioni) la scheda e' completa e si mette agli atti.</p>
  <table class="data-table compact"><thead><tr><th>Valutatore</th><th>Stato</th></tr></thead><tbody>{st_rows}</tbody></table>
</section>"""

        # --- Esito collegiale ---
        outcome_lbl = CVOI_OUTCOME_LABELS.get(c["outcome"], c["outcome"]) if c["valid"] else "Da completare (min. 2 valutatori)"
        area_lines = ""
        for key, label, w, mx, thr in CVOI_AREAS:
            raw = c["area_totals"].get(key, 0)
            wsc = round(raw * w, 2)
            minw = CVOI_AREA_MIN_WEIGHTED[key]
            ok_badge = ('<span class="badge success">ok</span>' if wsc >= minw
                        else '<span class="badge danger">sotto soglia</span>')
            area_lines += (f'<tr><td>{esc(label)}</td><td>{raw:g}/{int(mx)}</td><td>{wsc:g}</td>'
                           f'<td>{minw:g}</td><td>{ok_badge}</td></tr>')
        valbadge = (f'<span class="badge success">{c["n_val"]} valutatori</span>' if c["valid"]
                    else f'<span class="badge warning">{c["n_val"]} valutatori (min. 2)</span>')
        summary = f"""
<section class="panel">
  <div class="section-head"><h2>Esito collegiale CVOI (M6)</h2><span class="badge {badge_class(outcome_lbl)}">{esc(outcome_lbl)}</span></div>
  <div class="meta-grid">
    <div><span class="muted">Media ponderata</span><br><strong>{c['weighted']:g}</strong> / soglia {CVOI_OVERALL_THRESHOLD:g}</div>
    <div><span class="muted">Partecipazione</span><br>{valbadge}</div>
  </div>
  <table class="data-table compact"><thead><tr><th>Area</th><th>Punteggio (somma medie)</th><th>Ponderato</th><th>Min. ponderato</th><th></th></tr></thead><tbody>{area_lines}</tbody></table>
  <p class="muted">La valutazione e' collegiale: il punteggio e' la <strong>media dei valutatori, criterio per criterio</strong> (esclusi gli astenuti). Non si vota a maggioranza. Soglia complessiva {CVOI_OVERALL_THRESHOLD:g}/95; minimi per area 6,30 / 7,35 / 5,40.</p>
</section>"""

        # --- Tabella collegiale: criteri x valutatori + media + astenuti ---
        head_ev = "".join(f"<th>{esc((e['name'] or '').split()[0])}</th>" for e in evaluators)
        body = ""
        for n, (key, label, w, mx, thr) in enumerate(CVOI_AREAS, start=1):
            body += f'<tr class="group-row"><td colspan="{len(evaluators)+3}"><strong>{n}) {esc(label)}</strong> &middot; peso {int(w*100)}% &middot; min ponderato {CVOI_AREA_MIN_WEIGHTED[key]:g}</td></tr>'
            for i, crit in enumerate(CVOI_CRITERIA[key]):
                d = c["detail"][(key, i)]
                cells = ""
                for e in evaluators:
                    if e["id"] in c["abstained"]:
                        cells += '<td class="muted">ast.</td>'
                    else:
                        v = d["per"].get(e["id"])
                        cells += f"<td>{(f'{v:g}' if v is not None else '–')}</td>"
                n_abst = sum(1 for e in evaluators if e["id"] in c["abstained"])
                body += (f'<tr><td>{esc(crit)}</td>{cells}'
                         f'<td><strong>{d["media"]:g}</strong></td><td class="muted">{n_abst}</td></tr>')
        table = f"""
<section class="panel">
  <div class="section-head"><h2>Punteggi collegiali (per valutatore)</h2></div>
  <table class="data-table compact"><thead><tr><th>Criterio</th>{head_ev}<th>Media</th><th>Astenuti</th></tr></thead><tbody>{body}</tbody></table>
  <p class="muted">I punteggi individuali, le astensioni motivate e le note restano tracciati nel fascicolo: la media non cancella il dettaglio.</p>
</section>"""

        # --- Form di compilazione del singolo valutatore ---
        fill = ""
        if evaluators:
            ev_pick = ""
            if is_admin:
                opts = "".join(f'<option value="{e["id"]}"{" selected" if e["id"]==sel else ""}>{esc(e["name"])}</option>' for e in evaluators)
                ev_pick = (f'<form method="get" class="inline-form" style="margin-bottom:8px"><input type="hidden" name="platform" value="{ctx["platform_id"]}">'
                           f'<input type="hidden" name="user" value="{ctx["user_id"]}"><input type="hidden" name="fase" value="fase3"><input type="hidden" name="sub" value="scoring">'
                           f'<label>Compila per (admin): <select name="ev" data-autosubmit>{opts}</select></label></form>')
            if can_fill:
                sel_name = next((e["name"] for e in evaluators if e["id"] == sel), "")
                is_abst = sel in c["abstained"]
                reason = c["status"].get(sel, {})
                reason_v = reason["reason"] if reason else ""
                sel_state = c["ev_status"].get(sel, "da_compilare")
                state_lbl = st_badge.get(sel_state, sel_state)
                if is_abst:
                    inner_fill = '<p class="muted">Valutatore astenuto su questo progetto: nessun punteggio conteggiato.</p>'
                else:
                    crit_inputs = ""
                    for n, (key, label, w, mx, thr) in enumerate(CVOI_AREAS, start=1):
                        rows_in = ""
                        for i, crit in enumerate(CVOI_CRITERIA[key]):
                            v = c["scores"].get((sel, key, i))
                            vs = f"{v:g}" if v is not None else ""
                            rows_in += (f'<div class="crit-row"><span>{esc(crit)}</span>'
                                        f'<input name="raw_{key}_{i}" type="number" min="0" max="{CVOI_CRITERION_MAX}" step="1" value="{vs}"></div>')
                        crit_inputs += f'<fieldset class="cvoi-area-block"><legend>{n}) {esc(label)}</legend>{rows_in}</fieldset>'
                    # form sempre disponibile, dietro una voce esplicita "Compila"; aperto se non ancora validato
                    summary_txt = ("Modifica i punteggi (gia' validati)" if sel_state == "validato"
                                   else f"Compila i punteggi di {sel_name}")
                    open_attr = "" if sel_state == "validato" else " open"
                    note = ('<p class="muted">Punteggi gia\' validati: modificandoli dovrai rivalidare.</p>'
                            if sel_state == "validato" else
                            f'<p class="muted">Assegna 0-{CVOI_CRITERION_MAX} a ogni criterio, <strong>Salva</strong> e poi <strong>Valida</strong>. La media tra i valutatori e\' calcolata automaticamente.</p>')
                    inner_fill = f"""
  <details class="comm-block"{open_attr}>
    <summary><strong>{esc(summary_txt)}</strong></summary>
    {note}
    <form method="post" action="/pariter/practices/{pid}/cvoi">
      {hidden_ctx(ctx)}<input type="hidden" name="evaluator_id" value="{sel}">
      {crit_inputs}
      <div class="form-actions left">
        <button class="button secondary" type="submit" name="action" value="save_scores">Salva i punteggi</button>
        <button class="button primary" type="submit" name="action" value="confirm_scores">Valida i punteggi</button>
      </div>
    </form>
  </details>"""
                abst_form = f"""
  <form class="form-grid" method="post" action="/pariter/practices/{pid}/cvoi" style="margin-top:10px">
    {hidden_ctx(ctx)}<input type="hidden" name="action" value="abstain"><input type="hidden" name="evaluator_id" value="{sel}">
    <label class="span2">Astensione motivata (solo per causa legittima: conflitto/impedimento)<input name="reason" value="{esc(reason_v)}" placeholder="motivo a verbale"></label>
    <div class="form-actions"><button class="button secondary" type="submit">{'Revoca astensione' if is_abst else 'Astieniti su questo progetto'}</button></div>
  </form>"""
                fill = f"""
<section class="panel">
  <div class="section-head"><h2>Punteggi - {esc(sel_name)}</h2>{state_lbl}</div>
  {ev_pick}
  {inner_fill}
  {abst_form}
</section>"""
            elif is_admin:
                fill = f'<section class="panel"><div class="section-head"><h2>Punteggi valutatore</h2></div>{ev_pick}</section>'

        # --- Scheda completa: tutti hanno validato -> metti agli atti e procedi ---
        completa = ""
        dl = (f'<a class="button tiny" href="{rel_url("/documents/" + str(report["generated_document_id"]) + "/download", ctx)}">Apri scheda M6</a>'
              if report and report["generated_document_id"] else "")
        if c["all_done"]:
            genera = ""
            if not locked:
                genera = (f'<form method="post" action="/pariter/practices/{pid}/cvoi" style="display:inline">'
                          f'{hidden_ctx(ctx)}<input type="hidden" name="action" value="genera">'
                          f'<button class="button primary" type="submit">Metti agli atti (genera scheda M6)</button></form>')
            avanti = f'<a class="button tiny" href="{rel_url("/pariter/practices/" + str(pid), ctx, {"fase": "fase3", "sub": "fascicolo"})}">Vai a 3.3 - Fascicolo (M7)</a>'
            completa = f"""
<section class="panel">
  <div class="section-head"><h2>Scheda di valutazione completa</h2><span class="badge success">fatto &times;{c["n_confirmed"]}</span></div>
  <p>Tutti i valutatori hanno validato (salvo astensioni). La scheda M6 e' completa: mettila agli atti e prosegui alla redazione del fascicolo (M7), che sara' firmato e trasmesso all'Advisory.</p>
  <p class="muted">La scheda M6 non viene "formata"/firmata qui: e' solo costruita e messa agli atti. L'unica firma e' quella del fascicolo M7 che va all'Advisory.</p>
  <div class="form-actions">{genera} {dl} {avanti}</div>
</section>"""
        elif c["valid"]:
            completa = (f'<section class="panel"><div class="section-head"><h2>Scheda di valutazione</h2>'
                        f'<span class="badge warning">in corso</span></div>'
                        f'<p class="muted">La scheda sara\' completa quando tutti i valutatori avranno validato i propri punteggi '
                        f'(attuali validati: {c["n_confirmed"]}/{len(evaluators)}, esclusi gli astenuti).</p>{dl}</section>')
        log_panel = self.cvoi_log_panel(report) if report else ""
        # Punteggi in chiaro degli altri solo a valutazione conclusa (scoring "alla cieca"); l'admin vede sempre.
        reveal = is_admin or c["all_done"]
        if reveal:
            scores_block = summary + table
        else:
            scores_block = ('<section class="panel"><div class="section-head"><h2>Punteggi degli altri valutatori</h2>'
                            '<span class="badge neutral">Riservati</span></div>'
                            '<p class="muted">I punteggi e la media in chiaro degli altri valutatori compaiono solo quando '
                            'tutti hanno completato e validato (valutazione collegiale senza reciproca influenza). Tu vedi la tua scheda qui sopra.</p></section>')
        return status_panel + fill + scores_block + completa + log_panel

    def cvoi_members_panel(self, ctx, practice, report):
        pid = practice["id"]
        is_admin = ctx["user"]["role"] == "admin"
        members = rows("SELECT id, name FROM users WHERE role = 'technical_committee' AND active = 1 ORDER BY id")
        reviews = {r["user_id"]: r for r in rows("SELECT * FROM cvoi_member_reviews WHERE cvoi_report_id = ?", (report["id"],))}
        unanime = (report["workflow_status"] == "unanime") if "workflow_status" in report.keys() else False
        rows_html = ""
        for m in members:
            rv = reviews.get(m["id"])
            status = rv["status"] if rv else "in_attesa"
            signed = rv["signed_at"] if rv else ""
            can_act = (m["id"] == ctx["user_id"]) or is_admin
            badge = {"approvato": "success", "contrario": "danger", "modifica_richiesta": "warning"}.get(status, "neutral")
            actions = ""
            if can_act and (not unanime or is_admin):
                actions = "".join(
                    f'''<form method="post" action="/pariter/practices/{pid}/cvoi-member" class="inline-form" style="display:inline">
                      {hidden_ctx(ctx)}<input type="hidden" name="member_id" value="{m['id']}"><input type="hidden" name="action" value="{ak}">
                      <button class="button tiny" type="submit">{al}</button></form>'''
                    for ak, al in (("sign", "Favorevole"), ("contrario", "Contrario"), ("request_change", "Modifica"))
                )
            rows_html += f"""<tr>
              <td>{esc(m['name'])}</td>
              <td><span class="badge {badge}">{esc(status.replace('_',' '))}</span></td>
              <td class="muted">{esc((signed or '')[:16]) or '-'}</td>
              <td>{actions}</td>
            </tr>"""
        force = ""
        if is_admin and not unanime:
            force = f'''<form method="post" action="/pariter/practices/{pid}/cvoi-member" style="display:inline">
                {hidden_ctx(ctx)}<input type="hidden" name="action" value="force_unanime">
                <button class="button secondary" type="submit">Forza versione unanime (admin)</button></form>'''
        head_badge = '<span class="badge success">Versione unanime - firmata da tutti i membri</span>' if unanime else '<span class="badge warning">In revisione - manca l\'unanimita\'</span>'
        return f"""
<section class="panel">
  <div class="section-head"><h2>Comitato Tecnico - approvazioni e firme</h2>{head_badge}</div>
  <table class="data-table compact">
    <thead><tr><th>Membro</th><th>Stato</th><th>Firmato il</th><th></th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  <div class="form-actions">{force}</div>
  <p class="muted">Un membro redige (salva il verbale), gli altri approvano/firmano o chiedono modifica. Quando tutti firmano la versione e' unanime e puo' essere trasmessa al CdA. Ogni nuova modifica azzera le firme.</p>
</section>"""

    def cvoi_log_panel(self, report):
        log = rows("SELECT * FROM cvoi_edit_log WHERE cvoi_report_id = ? ORDER BY id DESC", (report["id"],))
        if not log:
            return ""
        items = "".join(
            f"<tr><td class='muted'>{esc((e['created_at'] or '')[:16])}</td><td>{esc(e['actor_name'] or '-')}</td><td>{esc(e['action'])}</td><td>{esc(e['summary'] or '')}</td></tr>"
            for e in log
        )
        return f"""
<section class="panel">
  <div class="section-head"><h2>Storico modifiche e firme</h2></div>
  <table class="data-table compact"><thead><tr><th>Data</th><th>Autore</th><th>Azione</th><th>Dettaglio</th></tr></thead><tbody>{items}</tbody></table>
</section>"""

    def render_cda_convocazione(self, ctx, practice, can_act):
        """Convocazione del CdA (finisce in Governance) + upload verbale, dal fascicolo pratica."""
        pid = practice["id"]
        meetings = rows(
            """SELECT bm.*, doc.title AS minutes_title
               FROM board_meetings bm LEFT JOIN documents doc ON doc.id = bm.minutes_document_id
               WHERE bm.practice_id = ? ORDER BY bm.meeting_date DESC, bm.id DESC""", (pid,))
        items = ""
        for m in meetings:
            verb = (f'<a class="button tiny" href="{rel_url("/documents/" + str(m["minutes_document_id"]) + "/download", ctx)}">Verbale</a>'
                    if m["minutes_document_id"] else '<span class="muted">verbale non caricato</span>')
            items += (f'<tr><td>{esc(m["title"])}</td><td>{esc(nice_date(m["meeting_date"]))}</td>'
                      f'<td><span class="badge neutral">{esc(m["status"])}</span></td><td>{verb}</td></tr>')
        table = (f'<table class="data-table compact"><thead><tr><th>Convocazione</th><th>Data</th><th>Stato</th><th></th></tr></thead>'
                 f'<tbody>{items}</tbody></table>' if meetings
                 else '<p class="muted">Nessuna convocazione CdA per questa pratica.</p>')
        forms = ""
        if can_act:
            forms = f"""
  <details class="comm-block"><summary>Convoca il CdA</summary>
    <form class="form-grid" method="post" action="/pariter/practices/{pid}/cda-convoca">
      {hidden_ctx(ctx)}
      <label>Data seduta<input name="meeting_date" type="date" required></label>
      <label>Ora<input name="meeting_time" placeholder="es. 18:00"></label>
      <label>Modalita<input name="meeting_mode" placeholder="In presenza / Mista / Videoconferenza"></label>
      <label>Luogo / link<input name="meeting_place" placeholder="Sede / link riunione"></label>
      <label class="span2">Ordine del giorno<input name="agenda" value="Valutazione e delibera sulla pratica {esc(practice['project_title'])}"></label>
      <div class="form-actions"><button class="button primary" type="submit">Crea convocazione (in Governance)</button></div>
    </form>
  </details>
  <details class="comm-block"><summary>Carica verbale del CdA</summary>
    <form class="inline-form" method="post" action="/pariter/practices/{pid}/cda-verbale" enctype="multipart/form-data">
      {hidden_ctx(ctx)}<input type="file" name="file" required>
      <button class="button tiny" type="submit">Carica verbale</button>
    </form>
  </details>"""
        return f"""
<section class="panel">
  <div class="section-head"><h2>Convocazione e verbale CdA</h2>
    <a class="button tiny" href="{rel_url('/governance', ctx, {'tab': 'convocazioni'})}">Apri Governance</a></div>
  {table}
  {forms}
</section>"""

    def render_kiis_panel(self, ctx, practice):
        """Bozza KIIS: genera da template o carica a mano; scaricabile. Sotto il CVOI in fase3."""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        can_edit = user_can(ctx["user"], "manage_practice") and not locked
        doc_id = practice["kiis_document_id"] if "kiis_document_id" in practice.keys() else None
        url = f"/pariter/practices/{pid}/kiis"
        if doc_id:
            badge = '<span class="badge success">Bozza KIIS presente</span>'
            dl = f'<a class="button tiny" href="{rel_url("/documents/" + str(doc_id) + "/download", ctx)}">Scarica/Apri</a>'
            actions = dl
            if can_edit:
                actions += (f' <form method="post" action="{url}" style="display:inline"><input type="hidden" name="action" value="generate">'
                            f'{hidden_ctx(ctx)}<button class="button tiny" type="submit">Rigenera da template</button></form>'
                            f' <form method="post" action="{url}" style="display:inline" onsubmit="return confirm(\'Rimuovere la bozza KIIS?\')">'
                            f'<input type="hidden" name="action" value="remove">{hidden_ctx(ctx)}<button class="button tiny" type="submit">Rimuovi</button></form>')
            upload = ""
            if can_edit:
                upload = (f'<form class="inline-form" method="post" action="{url}" enctype="multipart/form-data" style="margin-top:8px">'
                          f'{hidden_ctx(ctx)}<input type="hidden" name="action" value="upload"><input type="file" name="file" required>'
                          f'<button class="button tiny" type="submit">Sostituisci con file caricato</button></form>')
            inner = f'<p class="muted">La KIIS e\' redatta e validata dal proponente (art. 23 Reg. UE 2020/1503); Pariter precompila come assistenza e verifica. La bozza e\' allegata al fascicolo. Verra\' finalizzata e pubblicata in Fase 5.</p><div class="form-actions">{actions}</div>{upload}'
        else:
            badge = '<span class="badge neutral">Da acquisire</span>'
            if not can_edit:
                inner = '<p class="muted">Bozza KIIS non ancora disponibile.</p>'
            else:
                inner = f"""
  <p class="muted">La <strong>KIIS e' redatta dal proponente</strong> (titolare del progetto, art. 23): e' lui responsabile del contenuto. Pariter mette a disposizione il template (Allegato 18) e puo' <strong>precompilare i campi come assistenza</strong>, ma la titolarita' resta del proponente che valida la bozza.</p>
  <form method="post" action="{url}" style="display:inline">{hidden_ctx(ctx)}<input type="hidden" name="action" value="generate">
    <button class="button secondary" type="submit">Precompila bozza dal template (assistenza)</button></form>
  <form class="inline-form" method="post" action="{url}" enctype="multipart/form-data" style="margin-top:8px">
    {hidden_ctx(ctx)}<input type="hidden" name="action" value="upload"><input type="file" name="file" required>
    <button class="button primary" type="submit">Carica la KIIS del proponente</button></form>"""
        return f"""
<section class="panel">
  <div class="section-head"><h2>KIIS provvisoria (redatta dal proponente)</h2>{badge}</div>
  {inner}
</section>"""

    def render_kiis_stato_panel(self, ctx, practice):
        """(a) Validazione della bozza KIIS da parte del proponente + campi mancanti."""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        can = user_can(ctx["user"], "manage_practice") and not locked
        missing = kiis_missing_fields(practice)
        stato = _practice_val(practice, "m_kiis_stato", "incompleta")
        if stato == "completa":
            badge = '<span class="badge success">Validata dal proponente</span>'
        else:
            badge = '<span class="badge warning">In compilazione</span>'
        if missing:
            lis = "".join(f"<li>{esc(x)}</li>" for x in missing)
            miss_html = f'<p class="muted">Campi mancanti o incompleti della bozza KIIS:</p><ul class="clean-list">{lis}</ul>'
        else:
            miss_html = '<p><span class="badge success">Tutti i campi previsti sono valorizzati.</span></p>'
        actions = ""
        if can:
            if stato != "completa":
                actions = (f'<form method="post" action="/pariter/practices/{pid}/merito" style="display:inline">'
                           f'{hidden_ctx(ctx)}<input type="hidden" name="action" value="kiis_stato"><input type="hidden" name="val" value="completa">'
                           f'<button class="button primary" type="submit"{" disabled" if missing else ""}>Bozza validata dal proponente</button></form>')
                if missing:
                    actions += ' <span class="muted">(il proponente deve completare i campi mancanti)</span>'
            else:
                actions = (f'<form method="post" action="/pariter/practices/{pid}/merito" style="display:inline">'
                           f'{hidden_ctx(ctx)}<input type="hidden" name="action" value="kiis_stato"><input type="hidden" name="val" value="incompleta">'
                           f'<button class="button tiny" type="submit">Riapri (in compilazione)</button></form>')
        return f"""
<section class="panel">
  <div class="section-head"><h2>Validazione bozza KIIS (proponente)</h2>{badge}</div>
  <p class="muted">Il contenuto della KIIS e' responsabilita' del proponente (art. 23 par. 10). Una volta che il proponente ha compilato/validato la bozza, il fornitore puo' svolgere la verifica.</p>
  {miss_html}
  <div class="form-actions">{actions}</div>
</section>"""

    def render_verifiche_merito_panel(self, ctx, practice):
        """(c) Verifiche di merito: conflitti (Allegato 14) + coerenza KIIS, con gate verso 3.2."""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        can = user_can(ctx["user"], "manage_practice") and not locked
        kiis_completa = _practice_val(practice, "m_kiis_stato", "incompleta") == "completa"
        cf = _practice_val(practice, "m_conflitti")
        cf_mis = _practice_val(practice, "m_conflitti_misura")
        kc = _practice_val(practice, "m_kiis_coerenza")

        def sel(name, options, current):
            opts = "".join(f'<option value="{k}"{" selected" if k == current else ""}>{esc(v)}</option>'
                           for k, v in options)
            return f'<select name="{name}">{opts}</select>'

        segnalazione = ""
        if kc == "da_correggere":
            segnalazione = ('<p class="muted"><span class="badge warning">In attesa di correzione del proponente</span> '
                            'Inviata segnalazione (art. 23 par. 12). Se il proponente non corregge tempestivamente, '
                            "l'offerta puo' essere sospesa (max 30 giorni) e poi cancellata. La segnalazione si invia dal blocco C3K qui sopra.</p>")
        elif kc == "incoerente":
            segnalazione = ('<p class="muted"><span class="badge danger">Bloccante</span> KIIS incoerente/incompleta non sanabile: la pratica non prosegue.</p>')
        if not kiis_completa:
            inner = ('<p class="muted">La verifica del fornitore si svolge <strong>dopo che il proponente ha validato la bozza KIIS</strong> '
                     '(non si verifica una scheda non ancora redatta). Attendi la validazione qui sopra.</p>')
        elif not can:
            cf_lbl = CONFLITTI_MERITO_LABELS.get(cf, "non valutato")
            kc_lbl = KIIS_COERENZA_LABELS.get(kc, "non verificata")
            inner = f'<p>Conflitti: <strong>{esc(cf_lbl)}</strong>. Verifica KIIS: <strong>{esc(kc_lbl)}</strong>.</p>{segnalazione}'
        else:
            cf_options = [("", "- seleziona -"), ("nessuno", "Nessun conflitto"),
                          ("gestibile", "Gestibile (registra misura)"), ("non_gestibile", "Non gestibile (stop)")]
            kc_options = [("", "- seleziona -"), ("coerente", "Coerente, corretta e completa"),
                          ("da_correggere", "Da correggere (segnala al proponente, art. 23 par. 12)"),
                          ("incoerente", "Incoerente/incompleta (bloccante)")]
            inner = f"""
  <h3>A) Conflitti di interesse (Allegato 14) &mdash; accertamento del team</h3>
  <form class="form-grid" method="post" action="/pariter/practices/{pid}/merito">
    {hidden_ctx(ctx)}<input type="hidden" name="action" value="conflitti">
    <label>Esito{sel("val", cf_options, cf)}</label>
    <label class="span2">Misura di gestione (se gestibile)<input name="misura" value="{esc(cf_mis)}"></label>
    <div class="form-actions"><button class="button secondary" type="submit">Salva esito conflitti</button></div>
  </form>
  <h3>B) Verifica del fornitore sulla KIIS (art. 23): chiarezza, correttezza, completezza</h3>
  <form class="form-grid" method="post" action="/pariter/practices/{pid}/merito">
    {hidden_ctx(ctx)}<input type="hidden" name="action" value="coerenza">
    <label>Esito{sel("val", kc_options, kc)}</label>
    <div class="form-actions"><button class="button secondary" type="submit">Salva esito verifica KIIS</button></div>
  </form>
  {segnalazione}"""
        passed, reasons = fase3_gate(practice)
        if passed:
            gate = '<p><span class="badge success">Gate superato</span> Si puo\' passare a 3.2 (scoring + fascicolo).</p>'
        else:
            lis = "".join(f"<li>{esc(r)}</li>" for r in reasons)
            gate = f'<p><span class="badge warning">Gate non superato</span></p><ul class="clean-list">{lis}</ul>'
        return f"""
<section class="panel">
  <div class="section-head"><h2>Verifiche di merito (gate 3.1 &rarr; 3.2)</h2></div>
  {inner}
  <hr style="border:none;border-top:1px solid var(--line);margin:12px 0">
  {gate}
</section>"""

    def render_fascicolo_m7(self, ctx, practice):
        """3.3: dashboard di recap + fascicolo M7 (genera/modifica/salva) + firma per ogni membro +
        invio all'Advisory (solo se idoneo) oppure comunicazione di non accoglimento (C6)."""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        is_admin = ctx["user"]["role"] == "admin"
        url = f"/pariter/practices/{pid}/internal-review"
        with connect() as conn:
            c = compute_cvoi_collegial(conn, practice)
            fasc = conn.execute("SELECT * FROM internal_reviews WHERE practice_id = ? AND review_type = 'fascicolo'", (pid,)).fetchone()
            evals = cvoi_committee_members(conn)
            sigs = {r["member_id"]: r["signed_at"] for r in
                    conn.execute("SELECT member_id, signed_at FROM m7_signatures WHERE practice_id = ?", (pid,)).fetchall()}
            ndocs = conn.execute("SELECT COUNT(*) FROM documents WHERE practice_id = ? AND origin IN ('CVOI','KIIS','Verifiche interne')", (pid,)).fetchone()[0]
        outcome = c["outcome"]
        esito_pos = outcome in ("superato", "superato_condizioni")
        esito_lbl = CVOI_OUTCOME_LABELS.get(outcome, outcome)
        kc_lbl = KIIS_COERENZA_LABELS.get(_practice_val(practice, "m_kiis_coerenza"), "non verificata")
        cf_lbl = CONFLITTI_MERITO_LABELS.get(_practice_val(practice, "m_conflitti"), "non valutato")

        # --- Dashboard recap ---
        dash = f"""
<section class="panel">
  <div class="section-head"><h2>3.3 Fascicolo di valutazione (M7) - recap</h2>
    <span class="badge {badge_class(esito_lbl)}">{esc(esito_lbl)}</span></div>
  <div class="meta-grid">
    <div><span class="muted">Scoring CVOI (M6)</span><br><strong>{c['weighted']:g}</strong>/{CVOI_OVERALL_THRESHOLD:g} &middot; {c['n_confirmed']} validati</div>
    <div><span class="muted">Verifica KIIS (M5)</span><br>{esc(kc_lbl)}</div>
    <div><span class="muted">Conflitti</span><br>{esc(cf_lbl)}</div>
    <div><span class="muted">Documenti richiamati</span><br>{ndocs}</div>
  </div>
</section>"""

        # --- Documento M7: genera (IA) / modifica / salva / scarica ---
        has_doc = bool(fasc and (fasc["body"] or fasc["generated_document_id"]))
        doc_id = fasc["generated_document_id"] if fasc else None
        dl = (f'<a class="button tiny" href="{rel_url("/documents/" + str(doc_id) + "/download", ctx)}">Scarica</a>'
              if doc_id else "")
        if locked:
            docpanel = f'<section class="panel"><div class="section-head"><h2>Documento M7</h2></div>{dl}</section>'
        elif not has_doc:
            docpanel = f"""
<section class="panel">
  <div class="section-head"><h2>Documento M7</h2><span class="badge neutral">Da generare</span></div>
  <p class="muted">Genera la bozza descrittiva (IA) a 8 sezioni dai dati di 3.1 e 3.2, poi modificala e falla firmare ai membri.</p>
  <form method="post" action="{url}" style="display:inline">{hidden_ctx(ctx)}<input type="hidden" name="review_type" value="fascicolo"><input type="hidden" name="action" value="generate">
    <button class="button primary" type="submit">Genera bozza M7 (IA)</button></form>
</section>"""
        else:
            body_val = esc(fasc["body"] or compose_internal_review_draft(practice, "fascicolo"))
            docpanel = f"""
<section class="panel">
  <div class="section-head"><h2>Documento M7</h2><span class="badge warning">Bozza modificabile</span></div>
  <form class="form-grid" method="post" action="{url}">
    {hidden_ctx(ctx)}<input type="hidden" name="review_type" value="fascicolo"><input type="hidden" name="action" value="save_body">
    <label class="full-span">Testo del fascicolo (modificabile)<textarea name="body" rows="14" class="doc-draft">{body_val}</textarea></label>
    <div class="form-actions left"><button class="button secondary" type="submit">Salva</button>{dl}</div>
  </form>
</section>"""

        # --- Firme dei membri ---
        signers = [e for e in evals if e["id"] not in c["abstained"]]
        all_signed = bool(signers) and all(e["id"] in sigs for e in signers)
        sig_rows = ""
        for e in evals:
            if e["id"] in c["abstained"]:
                sig_rows += f'<tr><td>{esc(e["name"])}</td><td><span class="badge neutral">Astenuto</span></td><td></td></tr>'
                continue
            signed = sigs.get(e["id"])
            can_act = has_doc and not locked and (is_admin or e["id"] == ctx["user_id"])
            if signed:
                badge = f'<span class="badge success">Firmato &middot; {esc(signed[:16])}</span>'
                btn = (f'<form method="post" action="/pariter/practices/{pid}/fascicolo-firma" style="display:inline"><input type="hidden" name="action" value="unsign"><input type="hidden" name="member_id" value="{e["id"]}">{hidden_ctx(ctx)}<button class="button tiny" type="submit">Annulla firma</button></form>'
                       if can_act else "")
            else:
                badge = '<span class="badge warning">Da firmare</span>'
                btn = (f'<form method="post" action="/pariter/practices/{pid}/fascicolo-firma" style="display:inline"><input type="hidden" name="action" value="sign"><input type="hidden" name="member_id" value="{e["id"]}">{hidden_ctx(ctx)}<button class="button tiny primary" type="submit">Firma</button></form>'
                       if can_act else "")
            sig_rows += f'<tr><td>{esc(e["name"])}</td><td>{badge}</td><td>{btn}</td></tr>'
        sigpanel = f"""
<section class="panel">
  <div class="section-head"><h2>Firme del fascicolo (Comitato Tecnico)</h2>
    <span class="badge {"success" if all_signed else "warning"}">{sum(1 for e in signers if e["id"] in sigs)}/{len(signers)} firme</span></div>
  <p class="muted">Ogni membro firma il fascicolo M7 (gli astenuti sono esclusi). L'admin puo' firmare per ogni membro. Serve generare il documento prima di firmare.</p>
  <table class="data-table compact"><thead><tr><th>Membro</th><th>Stato</th><th></th></tr></thead><tbody>{sig_rows}</tbody></table>
</section>"""

        # --- Trasmissione Advisory (idoneo) oppure non accoglimento (C6) ---
        gia = _practice_val(practice, "m_advisory_trasmesso")
        if esito_pos:
            reasons = []
            if not has_doc:
                reasons.append("documento M7 non ancora generato")
            if not all_signed:
                reasons.append("mancano le firme dei membri")
            if gia:
                trasm = (f'<p><span class="badge success">Trasmesso il {esc(gia[:16])}</span> '
                         f'<a class="button tiny" href="{rel_url("/pariter/practices/" + str(pid), ctx, {"fase": "fase4"})}">Vai all\'Advisory (Fase 4)</a></p>')
            elif reasons:
                lis = "".join(f"<li>{esc(r)}</li>" for r in reasons)
                trasm = (f'<p class="muted">Per trasmettere mancano:</p><ul class="clean-list">{lis}</ul>'
                         f'<div class="form-actions"><button class="button primary" disabled>Invia all\'Advisory Committee</button></div>')
            else:
                btn = (f'<form method="post" action="/pariter/practices/{pid}/trasmetti-advisory">{hidden_ctx(ctx)}'
                       f'<div class="form-actions"><button class="button primary" type="submit">Invia all\'Advisory Committee</button></div></form>'
                       if not locked else "")
                trasm = f'<p>Esito idoneo, documento firmato da tutti: trasmissibile. Nessuna comunicazione di esito al proponente in questa fase.</p>{btn}'
            trasmpanel = f"""<section class="panel"><div class="section-head"><h2>Trasmissione all'Advisory Committee</h2></div>{trasm}</section>"""
        else:
            c6 = self.phase_emails_html(ctx, practice, ["C6"], "fase3",
                                        title="Comunicazione al proponente - pratica non accolta (C6)",
                                        intro="Esito CVOI non idoneo: il progetto non supera la valutazione di merito. Comunica al proponente che la pratica non e' stata accolta.",
                                        back_sub="fascicolo")
            trasmpanel = f"""
<section class="panel">
  <div class="section-head"><h2>Trasmissione all'Advisory Committee</h2><span class="badge danger">Esito non idoneo</span></div>
  <p class="muted">Lo scoring e' sotto soglia: non si trasmette all'Advisory.</p>
  <div class="form-actions"><button class="button primary" disabled>Invia all'Advisory Committee</button></div>
</section>{c6}"""
        return dash + docpanel + sigpanel + trasmpanel

    def render_trasmissione_advisory(self, ctx, practice):
        """3.3: trasmissione del fascicolo (CVOI + tabella + bozza KIIS) all'Advisory Committee."""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        can = user_can(ctx["user"], "manage_practice") and not locked
        with connect() as conn:
            cvoi = cvoi_summary_for(conn, pid)
            fasc = conn.execute(
                "SELECT review_status FROM internal_reviews WHERE practice_id = ? AND review_type = 'fascicolo'", (pid,)
            ).fetchone()
        esito_pos = bool(cvoi and cvoi.get("outcome") in ("superato", "superato_condizioni"))
        # sez. 7 e 8 del fascicolo obbligatorie: esiti conflitti e KIIS dalla 3.1
        sez7 = _practice_val(practice, "m_conflitti") in ("nessuno", "gestibile")
        sez8 = _practice_val(practice, "m_kiis_coerenza") == "coerente"
        fasc_ok = bool(fasc and fasc["review_status"] in ("firmata", "caricata", "validata"))
        gia = _practice_val(practice, "m_advisory_trasmesso")
        reasons = []
        if not esito_pos:
            reasons.append("scoring CVOI (M6) senza esito positivo (sotto soglia o non redatto)")
        if not fasc_ok:
            reasons.append("fascicolo di valutazione (M7) non ancora firmato/prodotto")
        if not sez7:
            reasons.append("sez. 7 fascicolo: esito conflitti mancante")
        if not sez8:
            reasons.append("sez. 8 fascicolo: esito verifica KIIS mancante")
        if gia:
            inner = (f'<p><span class="badge success">Trasmesso il {esc(gia[:16])}</span> Il fascicolo e\' stato inviato '
                     f'all\'Advisory Committee (Fase 4). Nessuna comunicazione di esito al proponente in Fase 3.</p>'
                     f'<a class="button tiny" href="{rel_url("/pariter/practices/" + str(pid), ctx, {"fase": "fase4"})}">Vai all\'Advisory (Fase 4)</a>')
        elif reasons:
            lis = "".join(f"<li>{esc(r)}</li>" for r in reasons)
            inner = (f'<p class="muted">Il fascicolo (CVOI + tabella punteggi + bozza KIIS) si trasmette all\'Advisory solo se '
                     f'completo e con esito positivo. Mancano:</p><ul class="clean-list">{lis}</ul>'
                     f'<div class="form-actions"><button class="button primary" type="submit" disabled>Trasmetti all\'Advisory</button></div>')
        else:
            btn = (f'<form method="post" action="/pariter/practices/{pid}/trasmetti-advisory">{hidden_ctx(ctx)}'
                   f'<div class="form-actions"><button class="button primary" type="submit">Trasmetti il fascicolo all\'Advisory Committee</button></div></form>'
                   if can else "")
            inner = (f'<p>Fascicolo completo (incl. sez. 7 conflitti e sez. 8 KIIS) ed esito positivo: trasmissibile all\'Advisory. '
                     f'<strong>Nessuna comunicazione di esito al proponente in questa fase</strong> (l\'esito parte post-delibera CdA, C5/C6).</p>{btn}')
        return f"""
<section class="panel">
  <div class="section-head"><h2>3.3 Trasmissione all'Advisory Committee</h2></div>
  {inner}
</section>"""

    def post_practice_kiis(self, practice_id, form, files=None):
        files = files or {}
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "fase3", perm="manage_practice")
        if not practice:
            return
        action = form.get("action", "generate")
        prev = practice["kiis_document_id"] if "kiis_document_id" in practice.keys() else None
        with connect() as conn:
            def _drop(doc_id):
                if not doc_id:
                    return
                d = conn.execute("SELECT storage_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
                if d:
                    try:
                        (BASE_DIR / d["storage_path"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            if action == "remove":
                _drop(prev)
                conn.execute("UPDATE practices SET kiis_document_id = NULL, kiis_state = 'da_generare', updated_at = ? WHERE id = ?",
                             (now_iso(), practice_id))
                msg = "Bozza KIIS rimossa."
            elif action == "upload":
                file_item = files.get("file")
                if file_item is None or not getattr(file_item, "filename", ""):
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Nessun file KIIS.", {"fase": "fase3"})
                    return
                _drop(prev)
                doc_id = save_uploaded_document(
                    conn, file_item, practice["platform_id"], None, practice["proponent_id"],
                    "KIIS", "kiis", f"Bozza KIIS - {practice['project_title']}", ctx["user_id"])
                link_document_practice(conn, doc_id, practice_id)
                conn.execute("UPDATE practices SET kiis_document_id = ?, kiis_state = 'Bozza (caricata)', updated_at = ? WHERE id = ?",
                             (doc_id, now_iso(), practice_id))
                msg = "Bozza KIIS caricata."
            else:  # generate
                _drop(prev)
                html_doc = build_kiis_draft_html(practice)
                doc_id = generated_document(
                    conn, practice["platform_id"], None, practice["proponent_id"],
                    "KIIS", "kiis", f"Bozza KIIS - {practice['project_title']}", "kiis-bozza.html", html_doc, ctx["user_id"])
                link_document_practice(conn, doc_id, practice_id)
                conn.execute("UPDATE practices SET kiis_document_id = ?, kiis_state = 'Bozza (generata)', updated_at = ? WHERE id = ?",
                             (doc_id, now_iso(), practice_id))
                msg = "Bozza KIIS generata dal template."
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Bozza KIIS", action)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, {"fase": "fase3", "sub": "verifiche"})

    def post_practice_merito(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "fase3")
        if not practice:
            return
        action = form.get("action", "")
        val = (form.get("val", "") or "").strip()
        with connect() as conn:
            if action == "kiis_stato":
                stato = "completa" if val == "completa" else "incompleta"
                conn.execute("UPDATE practices SET m_kiis_stato = ?, updated_at = ? WHERE id = ?",
                             (stato, now_iso(), practice_id))
                msg = f"Bozza KIIS segnata {stato.upper()}."
            elif action == "conflitti":
                if val not in ("", "nessuno", "gestibile", "non_gestibile"):
                    val = ""
                misura = (form.get("misura", "") or "").strip()
                conn.execute("UPDATE practices SET m_conflitti = ?, m_conflitti_misura = ?, updated_at = ? WHERE id = ?",
                             (val, misura, now_iso(), practice_id))
                msg = "Esito conflitti di merito salvato."
            elif action == "coerenza":
                if val not in ("", "coerente", "da_correggere", "incoerente"):
                    val = ""
                conn.execute("UPDATE practices SET m_kiis_coerenza = ?, updated_at = ? WHERE id = ?",
                             (val, now_iso(), practice_id))
                msg = "Esito verifica KIIS salvato."
            else:
                msg = "Azione non riconosciuta."
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Verifiche di merito", f"{action}={val}")
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, {"fase": "fase3", "sub": "verifiche"})

    def post_practice_fascicolo_firma(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "fase3", perm="cvoi_draft")
        if not practice:
            return
        is_admin = ctx["user"]["role"] == "admin"
        action = form.get("action", "sign")
        raw = (form.get("member_id", "") or "").strip()
        mid = int(raw) if raw.isdigit() else ctx["user_id"]
        with connect() as conn:
            evals = {e["id"] for e in cvoi_committee_members(conn)}
            if mid not in evals or (not is_admin and mid != ctx["user_id"]):
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Firma non consentita.", {"fase": "fase3", "sub": "fascicolo"}); return
            conn.execute("DELETE FROM m7_signatures WHERE practice_id = ? AND member_id = ?", (practice_id, mid))
            if action == "sign":
                conn.execute("INSERT INTO m7_signatures(practice_id, member_id, signed_at) VALUES (?, ?, ?)",
                             (practice_id, mid, now_iso()))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Firma fascicolo M7", f"{action} membro {mid}")
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx,
                      "Firma registrata." if action == "sign" else "Firma annullata.", {"fase": "fase3", "sub": "fascicolo"})

    def post_practice_trasmetti_advisory(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "fase3", perm="manage_practice")
        if not practice:
            return
        passed, reasons = fase3_gate(practice)
        with connect() as conn:
            c = compute_cvoi_collegial(conn, practice)
            fasc = conn.execute(
                "SELECT body, generated_document_id FROM internal_reviews WHERE practice_id = ? AND review_type = 'fascicolo'", (practice_id,)
            ).fetchone()
            sigs = {r["member_id"] for r in conn.execute("SELECT member_id FROM m7_signatures WHERE practice_id = ?", (practice_id,)).fetchall()}
            signers = [e["id"] for e in cvoi_committee_members(conn) if e["id"] not in c["abstained"]]
        esito_pos = c["outcome"] in ("superato", "superato_condizioni")
        has_doc = bool(fasc and (fasc["body"] or fasc["generated_document_id"]))
        all_signed = bool(signers) and all(mid in sigs for mid in signers)
        if not passed:
            self.redirect(f"/pariter/practices/{practice_id}", ctx,
                          "Non trasmissibile: " + "; ".join(reasons) + ".", {"fase": "fase3", "sub": "fascicolo"})
            return
        if not esito_pos:
            self.redirect(f"/pariter/practices/{practice_id}", ctx,
                          "Non trasmissibile: lo scoring CVOI (M6) non ha esito positivo (sotto soglia o non redatto).",
                          {"fase": "fase3", "sub": "fascicolo"})
            return
        if not has_doc:
            self.redirect(f"/pariter/practices/{practice_id}", ctx,
                          "Non trasmissibile: il fascicolo di valutazione (M7) non e' ancora generato.",
                          {"fase": "fase3", "sub": "fascicolo"})
            return
        if not all_signed:
            self.redirect(f"/pariter/practices/{practice_id}", ctx,
                          "Non trasmissibile: mancano le firme dei membri sul fascicolo M7.",
                          {"fase": "fase3", "sub": "fascicolo"})
            return
        with connect() as conn:
            conn.execute("UPDATE practices SET m_advisory_trasmesso = ?, updated_at = ? WHERE id = ?",
                         (now_iso(), now_iso(), practice_id))
            block = can_transition_practice(conn, practice, "in_advisory")
            if not block:
                conn.execute("UPDATE practices SET status = 'in_advisory' WHERE id = ?", (practice_id,))
                conn.execute(
                    """INSERT INTO practice_status_history(practice_id, from_status, to_status, actor_id, notes, created_at)
                       VALUES (?, ?, 'in_advisory', ?, 'Fascicolo CVOI trasmesso all''Advisory Committee', ?)""",
                    (practice_id, practice["status"], ctx["user_id"], now_iso()))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Trasmissione Advisory", "fascicolo CVOI")
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx,
                      "Fascicolo trasmesso all'Advisory Committee (Fase 4).", {"fase": "fase4"})

    def practice_tab_decision(self, ctx, practice, round_no):
        pid = practice["id"]
        title = "Delibera CdA"
        can_act = user_can(ctx["user"], "board_decision")  # board + admin
        with connect() as conn:
            locked = not advisory_is_unanime(conn, pid)
            lock_msg = "Per deliberare serve il parere dell'Advisory Committee in versione unanime (interviene prima della delibera del CdA)."
            origins = ["CVOI", "Advisory Committee", "Verifiche interne"]
            decision = conn.execute(
                "SELECT * FROM practice_board_decisions WHERE practice_id = ? AND decision_round = ? ORDER BY id DESC LIMIT 1",
                (pid, round_no),
            ).fetchone()
            members = board_members(conn)
            votes = {}
            if decision:
                votes = {v["user_id"]: v for v in conn.execute(
                    "SELECT * FROM board_member_votes WHERE board_decision_id = ?", (decision["id"],)
                ).fetchall()}
            cvoi = cvoi_summary_for(conn, pid)
        recall = self.recall_documents_html(ctx, practice, origins,
                                            "Documenti richiamati per la delibera")
        recall += self.render_cda_convocazione(ctx, practice, can_act and not practice["closed_at"])
        if locked and not decision:
            return recall + f'<section class="panel"><div class="section-head"><h2>{esc(title)}</h2></div><p class="muted">{esc(lock_msg)}</p></section>'

        if not decision:
            open_form = f"""
<section class="panel">
  <div class="section-head"><h2>{esc(title)}</h2></div>
  <p class="muted">Apri la delibera per raccogliere i voti dei consiglieri e finalizzare l'esito.</p>
  <form class="form-grid" method="post" action="/pariter/practices/{pid}/board-decision">
    {hidden_ctx(ctx)}<input type="hidden" name="round" value="{round_no}"><input type="hidden" name="action" value="open">
    <label>Data seduta<input name="meeting_date" type="date"></label>
    <label class="span2">Ordine del giorno<input name="agenda" value="Valutazione pratica {esc(practice['project_title'])}"></label>
    <div class="form-actions"><button class="button primary" type="submit" {'disabled' if not can_act else ''}>Apri delibera CdA</button></div>
  </form>
</section>"""
            return recall + open_form

        status = decision["decision_status"] if "decision_status" in decision.keys() else "finalizzata"
        doc_link = (f'<a class="button tiny" href="{rel_url("/documents/" + str(decision["generated_document_id"]) + "/download", ctx)}">Apri verbale</a>'
                    if decision["generated_document_id"] else "")

        if status == "in_votazione":
            # pannello voti membri
            vrows = ""
            for m in members:
                v = votes.get(m["id"])
                vote = v["vote"] if v else "in_attesa"
                badge = {"approva": "success", "contrario": "danger", "astenuto": "neutral"}.get(vote, "warning")
                actions = ""
                if can_act and ((m["id"] == ctx["user_id"]) or ctx["user"]["role"] == "admin"):
                    btns = "".join(
                        f'''<form method="post" action="/pariter/practices/{pid}/board-decision" style="display:inline">
                        {hidden_ctx(ctx)}<input type="hidden" name="action" value="vote"><input type="hidden" name="round" value="{round_no}">
                        <input type="hidden" name="member_id" value="{m['id']}"><input type="hidden" name="vote" value="{vk}">
                        <button class="button tiny" type="submit">{vl}</button></form>'''
                        for vk, vl in (("approva", "Favorevole"), ("contrario", "Contrario"), ("astenuto", "Astenuto"))
                    )
                    actions = btns
                vrows += f"""<tr><td>{esc(m['name'])}</td><td><span class="badge {badge}">{esc(BOARD_VOTE_LABELS.get(vote, vote))}</span></td><td>{actions}</td></tr>"""
            outcome_opts = "".join(f'<option value="{k}">{esc(v)}</option>' for k, v in DECISION_OUTCOMES)
            finalize = ""
            if can_act:
                votes_list = [(m["name"], votes[m["id"]]["vote"] if m["id"] in votes else "in_attesa") for m in members]
                draft_fields = {"meeting_date": decision["meeting_date"] or "", "attendees": decision["attendees"] or "",
                                "summary": "", "conditions": "", "outcome": ""}
                draft = compose_decision_draft(practice, round_no, draft_fields, cvoi, votes_list)
                finalize = f"""
<section class="panel">
  <div class="section-head"><h2>Finalizza delibera</h2></div>
  <form class="form-grid" method="post" action="/pariter/practices/{pid}/board-decision">
    {hidden_ctx(ctx)}<input type="hidden" name="action" value="finalize"><input type="hidden" name="round" value="{round_no}">
    <label>Presenti<input name="attendees" value="{esc(decision['attendees'] or '')}" placeholder="Consiglieri presenti"></label>
    <label>Esito deliberato<select name="outcome">{outcome_opts}</select></label>
    <label class="span2">Condizioni (se approvata con condizioni)<textarea name="conditions" rows="2"></textarea></label>
    <label class="span2">Bozza verbale (precompilata, modificabile prima di generare)<textarea name="verbale_text" rows="16" class="doc-draft">{esc(draft)}</textarea></label>
    <div class="form-actions"><button class="button primary" type="submit">Genera verbale ufficiale</button></div>
  </form>
</section>"""
            panel = f"""
<section class="panel">
  <div class="section-head"><h2>{esc(title)} - votazione in corso</h2><span class="badge warning">In votazione</span></div>
  <table class="data-table compact"><thead><tr><th>Consigliere</th><th>Voto</th><th></th></tr></thead><tbody>{vrows}</tbody></table>
  <p class="muted">Ogni consigliere esprime il proprio voto; il Presidente o l'amministratore finalizza la delibera. L'esito e i voti finiscono nel verbale.</p>
</section>{finalize}"""
            return recall + panel

        # finalizzata o sospesa
        vsum = ""
        if votes:
            vsum = " &middot; ".join(f"{esc(votes[m['id']]['vote'] if m['id'] in votes else 'in_attesa')}" for m in members)
        outcome_label = DECISION_OUTCOME_LABELS.get(decision["outcome"], decision["outcome"])
        reopen = ""
        if status == "sospesa" and can_act:
            reopen = f'''<form method="post" action="/pariter/practices/{pid}/board-decision" style="display:inline">
                {hidden_ctx(ctx)}<input type="hidden" name="action" value="reopen"><input type="hidden" name="round" value="{round_no}">
                <button class="button secondary" type="submit">Riapri votazione (ridelibera)</button></form>'''
        susp_note = '<p class="muted">Pratica sospesa in attesa di revisioni/integrazioni: carica le integrazioni richieste e ridelibera.</p>' if status == "sospesa" else ""
        panel = f"""
<section class="panel">
  <div class="section-head"><h2>{esc(title)}</h2><span class="badge {badge_class(outcome_label)}">{esc(outcome_label)}</span></div>
  <p class="muted">Seduta {esc(decision['meeting_date'] or '-')} &middot; voti: {vsum or '-'}</p>
  <p>{esc(decision['summary'] or '')}</p>
  {susp_note}
  <div class="form-actions">{doc_link} {reopen}</div>
</section>"""
        return recall + panel

    def practice_tab_advisory(self, ctx, practice):
        pid = practice["id"]
        with connect() as conn:
            locked = not cvoi_is_validated(conn, pid)
            advisory = conn.execute("SELECT * FROM advisory_opinions WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
            cvoi = cvoi_summary_for(conn, pid)
        recall = self.recall_documents_html(ctx, practice, ["CVOI", "Verifiche interne"],
                                            "Documenti richiamati per il parere")
        if locked and not advisory:
            return recall + '<section class="panel"><div class="section-head"><h2>Advisory Committee</h2></div><p class="muted">L\'Advisory Committee interviene dopo la valutazione CVOI (versione unanime) e prima dell\'unica delibera del CdA.</p></section>'
        summary_panel = ""
        if advisory:
            wf = advisory["workflow_status"] if "workflow_status" in advisory.keys() else "bozza"
            doc_link = (f'<a class="button tiny" href="{rel_url("/documents/" + str(advisory["generated_document_id"]) + "/download", ctx)}">Apri parere</a>'
                        if advisory["generated_document_id"] else "")
            summary_panel = f"""
<section class="panel">
  <div class="section-head"><h2>Parere Advisory</h2><span class="badge {badge_class(ADVISORY_OUTCOME_LABELS.get(advisory['outcome'], advisory['outcome']))}">{esc(ADVISORY_OUTCOME_LABELS.get(advisory['outcome'], advisory['outcome']))}</span></div>
  <div class="meta-grid"><div><span class="muted">Stato redazione</span><br>{esc(wf)}</div></div>
  <p class="muted">{esc(advisory['summary'] or '')}</p>
  <div class="form-actions">{doc_link}</div>
</section>"""
        can_draft = user_can(ctx["user"], "advisory_opinion")
        outcome_opts = "".join(
            f'<option value="{k}"{" selected" if advisory and advisory["outcome"]==k else ""}>{esc(v)}</option>'
            for k, v in ADVISORY_OUTCOMES
        )
        draft_form = ""
        if can_draft:
            df = {
                "meeting_date": advisory["meeting_date"] if advisory else "",
                "attendees": advisory["attendees"] if advisory else "",
                "summary": advisory["summary"] if advisory else "",
                "conditions": advisory["conditions"] if advisory else "",
                "outcome": advisory["outcome"] if advisory else "favorevole",
            }
            parere_draft = compose_advisory_draft(practice, df, cvoi)
            draft_form = f"""
<section class="panel">
  <div class="section-head"><h2>Redazione parere (non vincolante)</h2></div>
  <form class="form-grid" method="post" action="/pariter/practices/{pid}/advisory">
    {hidden_ctx(ctx)}<input type="hidden" name="action" value="save">
    <label>Data seduta<input name="meeting_date" type="date" value="{esc(advisory['meeting_date']) if advisory else ''}"></label>
    <label>Componenti<input name="attendees" value="{esc(advisory['attendees']) if advisory else ''}"></label>
    <label>Esito<select name="outcome">{outcome_opts}</select></label>
    <label class="span2">Bozza parere (precompilata, modificabile prima di generare)<textarea name="parere_text" rows="18" class="doc-draft">{esc(parere_draft)}</textarea></label>
    <div class="form-actions"><button class="button primary" type="submit">{'Rigenera parere' if advisory else 'Genera parere ufficiale'}</button></div>
  </form>
</section>"""
        members_panel = self.advisory_members_panel(ctx, practice, advisory) if advisory else ""
        return recall + summary_panel + members_panel + draft_form

    def advisory_members_panel(self, ctx, practice, advisory):
        pid = practice["id"]
        is_admin = ctx["user"]["role"] == "admin"
        members = rows("SELECT id, name FROM users WHERE role = 'covi' AND active = 1 ORDER BY id")
        reviews = {r["user_id"]: r for r in rows("SELECT * FROM advisory_member_reviews WHERE advisory_opinion_id = ?", (advisory["id"],))}
        unanime = (advisory["workflow_status"] == "unanime") if "workflow_status" in advisory.keys() else False
        rows_html = ""
        for m in members:
            rv = reviews.get(m["id"])
            status = rv["status"] if rv else "in_attesa"
            signed = rv["signed_at"] if rv else ""
            can_act = (m["id"] == ctx["user_id"]) or is_admin
            badge = {"approvato": "success", "contrario": "danger", "modifica_richiesta": "warning"}.get(status, "neutral")
            actions = ""
            if can_act and (not unanime or is_admin):
                actions = "".join(
                    f'''<form method="post" action="/pariter/practices/{pid}/advisory-member" class="inline-form" style="display:inline">
                      {hidden_ctx(ctx)}<input type="hidden" name="member_id" value="{m['id']}"><input type="hidden" name="action" value="{ak}">
                      <button class="button tiny" type="submit">{al}</button></form>'''
                    for ak, al in (("sign", "Favorevole"), ("contrario", "Contrario"), ("request_change", "Modifica"))
                )
            rows_html += f"""<tr><td>{esc(m['name'])}</td><td><span class="badge {badge}">{esc(status.replace('_',' '))}</span></td><td class="muted">{esc((signed or '')[:16]) or '-'}</td><td>{actions}</td></tr>"""
        force = ""
        if is_admin and not unanime:
            force = f'''<form method="post" action="/pariter/practices/{pid}/advisory-member" style="display:inline">
                {hidden_ctx(ctx)}<input type="hidden" name="action" value="force_unanime">
                <button class="button secondary" type="submit">Forza versione unanime (admin)</button></form>'''
        head_badge = '<span class="badge success">Parere unanime - firmato da tutti i membri</span>' if unanime else '<span class="badge warning">In revisione - manca l\'unanimita\'</span>'
        return f"""
<section class="panel">
  <div class="section-head"><h2>Advisory Committee - approvazioni e firme</h2>{head_badge}</div>
  <table class="data-table compact"><thead><tr><th>Membro</th><th>Stato</th><th>Firmato il</th><th></th></tr></thead><tbody>{rows_html}</tbody></table>
  <div class="form-actions">{force}</div>
  <p class="muted">Come il CVOI: un membro redige, gli altri firmano o chiedono modifica; con tutte le firme il parere e' unanime e abilita la delibera del CdA. Ogni modifica azzera le firme.</p>
</section>"""

    def practice_tab_condizioni(self, ctx, practice):
        conds = rows("SELECT * FROM pre_golive_conditions WHERE practice_id = ? ORDER BY priority, id", (practice["id"],))
        if conds:
            cond_rows = ""
            for c in conds:
                status_opts = "".join(
                    f'<option value="{k}"{" selected" if k == c["cond_status"] else ""}>{esc(v)}</option>'
                    for k, v in CONDITION_STATUS_LABELS.items()
                )
                cond_rows += f"""<tr>
                  <td>{esc(c['description'])}</td>
                  <td><span class="badge {'danger' if c['priority']=='bloccante' else 'neutral'}">{esc(c['priority'])}</span></td>
                  <td class="muted">{esc(c['source'] or '-')} / {esc(c['owner'] or '-')}</td>
                  <td class="muted">{esc(c['due_date'] or '-')}</td>
                  <td><form class="inline-form" method="post" action="/pariter/practices/{practice['id']}/condition">{hidden_ctx(ctx)}<input type="hidden" name="cond_id" value="{c['id']}"><select name="cond_status">{status_opts}</select><button class="button tiny" type="submit">Salva</button></form></td>
                </tr>"""
            table = f"""<table class="data-table compact">
    <thead><tr><th>Condizione</th><th>Priorita'</th><th>Fonte / responsabile</th><th>Scadenza</th><th>Stato</th></tr></thead>
    <tbody>{cond_rows}</tbody></table>"""
        else:
            table = '<p class="muted">Nessuna condizione registrata.</p>'
        add = f"""
  <form class="form-grid" method="post" action="/pariter/practices/{practice['id']}/condition" style="margin-top:14px">
    {hidden_ctx(ctx)}
    <label class="span2">Descrizione<input name="description" required></label>
    <label>Fonte<input name="source" placeholder="CVOI / Delibera / Advisory"></label>
    <label>Responsabile<select name="owner"><option>proponente</option><option>Pariter</option><option>notaio</option><option>advisor</option><option>altro</option></select></label>
    <label>Priorita'<select name="priority"><option value="bloccante">bloccante</option><option value="non_bloccante">non bloccante</option></select></label>
    <label>Scadenza<input name="due_date" type="date"></label>
    <div class="form-actions"><button class="button primary" type="submit">Aggiungi condizione</button></div>
  </form>"""
        return f'<section class="panel"><div class="section-head"><h2>Condizioni pre go-live</h2></div>{table}{add}</section>'

    def practice_tab_validazione(self, ctx, practice):
        pid = practice["id"]
        with connect() as conn:
            f4_docs = conn.execute(
                "SELECT * FROM practice_documents WHERE practice_id = ? AND phase = 'fase4' ORDER BY id", (pid,)
            ).fetchall()
            blockers = golive_blockers(conn, pid)
        check_rows = "".join(
            f"""<tr><td>{esc(d['label'])}</td><td><span class="badge {'success' if d['doc_status']=='verificato' else 'warning'}">{esc(DOC_STATUS_LABELS.get(d['doc_status'], d['doc_status']))}</span></td></tr>"""
            for d in f4_docs
        )
        checklist = f"""<table class="data-table compact"><thead><tr><th>Documento Fase 4</th><th>Stato</th></tr></thead><tbody>{check_rows}</tbody></table>""" if f4_docs else '<p class="muted">Nessun documento Fase 4 in checklist.</p>'
        if blockers:
            blk = "".join(f"<li>{esc(b)}</li>" for b in blockers)
            blockers_html = f'<div class="ai-flag">Requisiti bloccanti aperti:<ul style="margin:6px 0 0">{blk}</ul></div>'
        else:
            blockers_html = '<p class="badge success">Nessun requisito bloccante aperto.</p>'
        status = practice["status"]
        # azione contestuale in base allo stato
        action_btn = ""
        if status in {"in_pre_golive", "da_integrare"}:
            action_btn = f'''<form method="post" action="/pariter/practices/{pid}/transition" style="display:inline">{hidden_ctx(ctx)}<input type="hidden" name="target" value="pronta_verifica_finale"><button class="button primary" type="submit">Avvia verifica finale</button></form>'''
        elif status == "pronta_verifica_finale":
            disabled = " disabled" if blockers else ""
            action_btn = f'''<form method="post" action="/pariter/practices/{pid}/transition" style="display:inline">{hidden_ctx(ctx)}<input type="hidden" name="target" value="pronta_golive"><button class="button primary" type="submit"{disabled}>Conferma pronta per go-live</button></form>'''
        elif status == "pronta_golive":
            action_btn = f'''<form method="post" action="/pariter/practices/{pid}/transition" style="display:inline">{hidden_ctx(ctx)}<input type="hidden" name="target" value="pubblicata"><button class="button primary" type="submit">Autorizza go-live</button></form>'''
        elif status == "pubblicata":
            action_btn = '<span class="badge success">Pratica pubblicata - go-live autorizzato</span>'
        return f"""
<section class="panel">
  <div class="section-head"><h2>Validazione pre go-live</h2><span class="badge {badge_class(practice_status_label(status))}">{esc(practice_status_label(status))}</span></div>
  {blockers_html}
  <div class="form-actions" style="margin-top:10px">{action_btn}</div>
</section>
<section class="panel">
  <div class="section-head"><h2>Checklist documenti Fase 4</h2></div>
  {checklist}
  <p class="muted">Aggiorna gli stati documento dal tab "Verifica documentale".</p>
</section>"""

    def practice_tab_campagna(self, ctx, practice):
        review = row("SELECT * FROM campaign_page_reviews WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (practice["id"],))
        doc_link = ""
        badge = ""
        if review:
            badge = f'<span class="badge {badge_class(review["review_status"])}">{esc(review["review_status"].replace("_"," "))}</span>'
            if review["generated_document_id"]:
                doc_link = f'<a class="button tiny" href="{rel_url("/documents/" + str(review["generated_document_id"]) + "/download", ctx)}">Apri report</a>'
        checked = "checked" if (review and review["no_yield_promise"]) else ""
        notes_val = esc(review["coherence_notes"]) if review else ""
        return f"""
<section class="panel">
  <div class="section-head"><h2>Revisione pagina campagna</h2>{badge}</div>
  <p class="muted">Confronto con KIIS, delibera aumento capitale e business plan; verifica assenza di promesse di rendimento e claim non supportati.</p>
  <form class="form-grid" method="post" action="/pariter/practices/{practice['id']}/campaign-review">
    {hidden_ctx(ctx)}
    <label class="span2">Note di coerenza / revisione<textarea name="coherence_notes" rows="4">{notes_val}</textarea></label>
    <label class="checkbox-row"><input type="checkbox" name="no_yield_promise" value="1" {checked}> Verificata assenza di promesse di rendimento</label>
    <label>Esito<select name="review_status"><option value="in_revisione">In revisione</option><option value="da_correggere">Da correggere</option><option value="validata">Validata</option></select></label>
    <div class="form-actions"><button class="button primary" type="submit">Salva e genera report</button>{doc_link}</div>
  </form>
</section>"""

    def page_practice_import(self):
        ctx = self.get_ctx()
        if ctx["platform_id"] != 1:
            self.redirect("/deals", ctx, "L'istruttoria Pariter e' disponibile solo per Pariter Equity.")
            return
        body = f"""
<section class="panel narrow">
  <div class="section-head"><h2>Nuova candidatura</h2></div>
  <p class="muted">Crea una nuova candidatura importando il dossier del proponente, oppure inserendo l'anagrafica manualmente (fino al collegamento dell'API/portale). Dopo la creazione potrai modificare l'anagrafica, caricare i documenti e prendere in carico.</p>
  <h3 style="margin:14px 0 6px">Da dossier (import)</h3>
  <form class="form-grid" method="post" action="/pariter/practices/import" enctype="multipart/form-data">
    {hidden_ctx(ctx)}
    <label>Pacchetto dossier (.zip)<input type="file" name="dossier_zip" accept=".zip"></label>
    <label>oppure dati_struttura.json<input type="file" name="struttura_json" accept=".json,application/json"></label>
    <label>oppure KIIS_dati.json<input type="file" name="kiis_json" accept=".json,application/json"></label>
    <label>Titolo progetto (se non desumibile)<input name="project_title" placeholder="Lascia vuoto per usare il dato del dossier"></label>
    <div class="form-actions"><button class="button primary" type="submit">Importa e crea pratica</button></div>
  </form>
  <h3 style="margin:20px 0 6px">Inserimento manuale</h3>
  <form class="form-grid" method="post" action="/pariter/practices/create-manual">
    {hidden_ctx(ctx)}
    <label>Denominazione proponente *<input name="denominazione" required></label>
    <label>Titolo progetto<input name="project_title"></label>
    <label>Forma giuridica<input name="forma" placeholder="S.r.l. / S.p.A."></label>
    <label>Sede legale<input name="sedeLegale"></label>
    <label>P. IVA<input name="pIva"></label>
    <label>PEC<input name="pec"></label>
    <label>Legale rappresentante<input name="rep_nome"></label>
    <label>Carica<input name="rep_carica"></label>
    <label>Importo target<input name="importoTarget"></label>
    <label>Importo massimo<input name="importoMax"></label>
    <label>Pre-money<input name="preMoney"></label>
    <label>Equity offerta<input name="equity"></label>
    <label>Strumento<input name="strumento" placeholder="Quote di S.r.l. / Azioni"></label>
    <div class="form-actions"><button class="button primary" type="submit">Crea candidatura manuale</button></div>
  </form>
</section>
"""
        self.render("Nuova candidatura", body, "deals")

    def page_practice_report(self, practice_id):
        ctx = self.get_ctx()
        practice = self.get_practice(practice_id)
        if not practice or practice["platform_id"] != ctx["platform_id"]:
            self.not_found()
            return
        with connect() as conn:
            report_html = build_practice_report_html(conn, practice)
        body = f"""
<section class="panel">
  <div class="section-head">
    <h2>Report fascicolo istruttorio</h2>
    <a class="button tiny" href="{rel_url('/pariter/practices/' + str(practice_id) + '/export', ctx)}">Export ZIP</a>
  </div>
  <iframe class="report-frame" srcdoc="{esc(report_html)}"></iframe>
  <p class="muted">Usa la stampa del browser (Cmd+P) per salvare il report in PDF.</p>
</section>"""
        self.render("Report fascicolo", body, "deals")

    def export_practice_zip(self, practice_id):
        ctx = self.get_ctx()
        practice = self.get_practice(practice_id)
        if not practice or practice["platform_id"] != ctx["platform_id"]:
            self.not_found()
            return
        with connect() as conn:
            report_html = build_practice_report_html(conn, practice)
            docs = conn.execute(
                "SELECT * FROM documents WHERE practice_id = ? ORDER BY id", (practice_id,)
            ).fetchall()
        buf = io.BytesIO()
        index = [
            f"FASCICOLO ISTRUTTORIO PARITER - {practice['project_title']}",
            f"Proponente: {practice['proponent_name'] or '-'}",
            f"Stato pratica: {practice_status_label(practice['status'])}",
            "", "=== DOCUMENTI ===",
        ]
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("Fascicolo/00_Report_istruttoria.html", report_html)
            zf.writestr("Fascicolo/dossier_dati.json", practice["dossier_json"] or "{}")
            for d in docs:
                src = BASE_DIR / d["storage_path"]
                arc = f"Fascicolo/{sanitize_filename(d['origin'] or 'doc')}/{d['id']}_{sanitize_filename(d['filename'] or 'documento')}"
                try:
                    zf.writestr(arc, src.read_bytes())
                    index.append(f"  [{d['origin']}] {d['title']} -> {arc}")
                except OSError:
                    index.append(f"  [{d['origin']}] {d['title']} (file non disponibile)")
            zf.writestr("Fascicolo/INDICE.txt", "\n".join(index))
        data = buf.getvalue()
        filename = f"Fascicolo_Pariter_{sanitize_filename(practice['proponent_name'] or 'proponente')}_{sanitize_filename(practice['project_title'])}.zip"
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def post_practice_import(self, form, files):
        ctx = self.ctx_from_form(form)
        if ctx["platform_id"] != 1:
            self.redirect("/deals", ctx, "L'istruttoria Pariter e' disponibile solo per Pariter Equity.")
            return
        if not user_can(ctx["user"], "manage_practice"):
            self.redirect("/deals", ctx, "Ruolo non abilitato all'import dossier.")
            return
        zip_item = files.get("dossier_zip")
        struttura = files.get("struttura_json")
        kiis = files.get("kiis_json")
        if zip_item is not None and getattr(zip_item, "filename", ""):
            dossier = read_dossier_from_zip(zip_item)
        elif (struttura and getattr(struttura, "filename", "")) or (kiis and getattr(kiis, "filename", "")):
            dossier = read_dossier_from_json(
                struttura if (struttura and getattr(struttura, "filename", "")) else None,
                kiis if (kiis and getattr(kiis, "filename", "")) else None,
            )
        else:
            self.redirect("/pariter/practices/import", ctx, "Carica un pacchetto .zip o almeno un file JSON di fase.")
            return
        mapped = map_dossier_to_practice(dossier, ctx["platform_id"], form.get("project_title", ""))
        with connect() as conn:
            practice_id = ingest_practice(conn, dossier, mapped, ctx["platform_id"], ctx["user_id"])
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Dossier importato: pratica creata.")

    def _practice_guard(self, ctx, practice_id, back_tab="riepilogo", perm="manage_practice", allow_closed=False):
        practice = self.get_practice(practice_id)
        if not practice or practice["platform_id"] != ctx["platform_id"]:
            self.not_found()
            return None
        if not user_can(ctx["user"], perm):
            self.redirect(f"/pariter/practices/{practice_id}", ctx, "Ruolo non abilitato.", {"tab": back_tab})
            return None
        closed = practice["closed_at"] if "closed_at" in practice.keys() else ""
        if closed and not allow_closed:
            self.redirect(f"/pariter/practices/{practice_id}", ctx, "Pratica chiusa: sola lettura.", {"tab": back_tab})
            return None
        return practice

    def post_practice_document_status(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "documentale")
        if not practice:
            return
        with connect() as conn:
            conn.execute(
                """UPDATE practice_documents SET doc_status = ?, reviewer_notes = ?, updated_by = ?, updated_at = ?
                   WHERE id = ? AND practice_id = ?""",
                (form.get("doc_status", "da_verificare"), form.get("reviewer_notes", ""),
                 ctx["user_id"], now_iso(), int(form["doc_id"]), practice_id),
            )
            conn.execute("UPDATE practices SET updated_at = ? WHERE id = ?", (now_iso(), practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Verifica documentale", form.get("doc_status", ""))
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Stato documento aggiornato.", {"tab": "documentale"})

    def post_practice_onorabilita(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "documentale")
        if not practice:
            return
        action = form.get("action", "")
        now = now_iso()
        with connect() as conn:
            if action in ("configure", "reset"):
                conn.execute("DELETE FROM practice_onorabilita WHERE practice_id = ?", (practice_id,))
                if action == "configure":
                    coincide = form.get("coincide") == "si"
                    lr = (form.get("lr_name") or "").strip()
                    te = (form.get("te_name") or "").strip()
                    if coincide:
                        conn.execute(
                            "INSERT INTO practice_onorabilita(practice_id, role, subject_name, updated_at) VALUES (?, 'both', ?, ?)",
                            (practice_id, lr or te, now))
                    else:
                        conn.execute(
                            "INSERT INTO practice_onorabilita(practice_id, role, subject_name, updated_at) VALUES (?, 'lr', ?, ?)",
                            (practice_id, lr, now))
                        conn.execute(
                            "INSERT INTO practice_onorabilita(practice_id, role, subject_name, updated_at) VALUES (?, 'te', ?, ?)",
                            (practice_id, te, now))
                msg = "Soggetti onorabilita' art. 5 aggiornati."
            elif action == "toggle":
                role = form.get("role", "")
                kind = form.get("kind", "")
                val = 1 if form.get("val") == "1" else 0
                if kind in ("autodich", "casellario") and role in ("lr", "te", "both"):
                    conn.execute(
                        f"UPDATE practice_onorabilita SET {kind} = ?, updated_at = ? WHERE practice_id = ? AND role = ?",
                        (val, now, practice_id, role))
                msg = "Stato onorabilita' aggiornato."
            else:
                msg = "Azione non riconosciuta."
            conn.execute("UPDATE practices SET updated_at = ? WHERE id = ?", (now, practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Onorabilita art. 5", action)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg,
                      {"fase": "fase2", "sub": form.get("back_sub") or "ammissibilita"})

    def post_practice_completezza(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "documentale")
        if not practice:
            return

        def parse_int(name, lo, hi):
            raw = (form.get(name) or "").strip()
            if raw == "":
                return None
            try:
                return max(lo, min(hi, int(raw)))
            except ValueError:
                return None

        esercizi = parse_int("esercizi_chiusi", 0, 50)
        bil = parse_int("bilanci_presenti", 0, 2)
        with connect() as conn:
            conn.execute("UPDATE practices SET esercizi_chiusi = ?, bilanci_presenti = ?, updated_at = ? WHERE id = ?",
                         (esercizi, bil if bil is not None else 0, now_iso(), practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id,
                      "Completezza fascicolo", f"esercizi={esercizi} bilanci={bil}")
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Completezza fascicolo aggiornata.", {"tab": "documentale"})

    def render_onorabilita_config(self, ctx, practice):
        """Prerequisito (in 2.1, prima della raccolta documenti): titolare effettivo e legale
        rappresentante coincidono? Determina quante autodichiarazioni/casellari servono."""
        pid = practice["id"]
        locked = bool(practice["closed_at"])
        can = user_can(ctx["user"], "manage_practice") and not locked
        with connect() as conn:
            ob = onorabilita_status(conn, pid)
        js = {}
        try:
            js = json.loads(practice["dossier_json"] or "{}")
        except (ValueError, TypeError):
            js = {}
        ds = js.get("dati_struttura") or {}
        prefill_lr = (ds.get("legaleRappresentante") or {}).get("nome") or (js.get("rep") or {}).get("nome") or ""
        if not ob["configured"]:
            if not can:
                inner = '<p class="muted">Soggetti del controllo onorabilita\' non ancora impostati.</p>'
            else:
                inner = f"""
  <p class="muted">Prima di raccogliere la documentazione: il <strong>titolare effettivo</strong> coincide con il <strong>legale rappresentante</strong>? Se coincidono basta <strong>una</strong> autodichiarazione e <strong>un</strong> casellario; se sono persone distinte ne servono <strong>due</strong> per ciascuno.</p>
  <form class="form-grid" method="post" action="/pariter/practices/{pid}/onorabilita">
    {hidden_ctx(ctx)}<input type="hidden" name="action" value="configure"><input type="hidden" name="back_sub" value="completezza">
    <label>Titolare effettivo e legale rappresentante coincidono?
      <select name="coincide"><option value="si">Si - stessa persona</option><option value="no">No - persone distinte</option></select>
    </label>
    <label>Legale rappresentante<input name="lr_name" value="{esc(prefill_lr)}"></label>
    <label>Titolare effettivo<input name="te_name" placeholder="compila solo se diverso dal legale rappresentante"></label>
    <div class="form-actions"><button class="button primary" type="submit">Imposta soggetti</button></div>
  </form>"""
            badge = '<span class="badge warning">Da impostare</span>'
        else:
            coincide = ob["coincide"]
            n = 1 if coincide else 2
            names = ", ".join(esc(s["subject_name"] or "-") for s in ob["subjects"])
            badge = '<span class="badge success">Impostati</span>'
            reconf = ""
            if can:
                reconf = (f'<form method="post" action="/pariter/practices/{pid}/onorabilita" style="display:inline" '
                          f'onsubmit="return confirm(\'Riconfigurare i soggetti? Le spunte verranno azzerate.\')">'
                          f'{hidden_ctx(ctx)}<input type="hidden" name="action" value="reset"><input type="hidden" name="back_sub" value="completezza">'
                          f'<button class="button tiny secondary" type="submit">Riconfigura</button></form>')
            inner = (f'<p>Esito: <strong>{"coincidono" if coincide else "persone distinte"}</strong> &mdash; soggetti: {names}.</p>'
                     f'<p class="muted">Documenti di onorabilita\' dovuti: <strong>{n}</strong> autodichiarazione/i (per procedere) e '
                     f'<strong>{n}</strong> casellario/i (per pubblicare). La verifica si completa al punto 2.2.</p>'
                     f'<div class="form-actions left">{reconf}</div>')
        return f"""
<section class="panel">
  <div class="section-head"><h2>Soggetti del controllo onorabilita' (art. 5)</h2>{badge}</div>
  {inner}
</section>"""

    def practice_onorabilita_panel(self, ctx, practice):
        pid = practice["id"]
        with connect() as conn:
            ob = onorabilita_status(conn, pid)
        js = json.loads(practice["dossier_json"] or "{}")
        rep = js.get("rep") or {}
        prefill_lr = rep.get("nome") or ""
        prefill_te = js.get("titolare_effettivo") or (js.get("societa") or {}).get("titolare_effettivo") or ""
        proc_badge = ('<span class="badge success">Procedibile</span>' if ob["procedibile"]
                      else '<span class="badge warning">Non procedibile</span>')
        pub_badge = ('<span class="badge success">Pubblicabile</span>' if ob["pubblicabile"]
                     else '<span class="badge danger">Non pubblicabile</span>')
        if not ob["configured"]:
            body = ('<p class="muted">Imposta prima i <strong>soggetti del controllo onorabilita\'</strong> '
                    'nel punto <strong>2.1 Completezza del fascicolo</strong> (titolare effettivo / legale rappresentante): '
                    'da li si determina se serve una o due autodichiarazioni/casellari.</p>')
        else:
            locked = bool(practice["closed_at"])
            # una riga per ogni documento: per ciascun soggetto, autodichiarazione + casellario.
            # Se i soggetti sono distinti compaiono righe separate per legale rappresentante e titolare effettivo.
            doc_kinds = [
                ("autodich", "Documento di onorabilita' (autodichiarazione)", "Per procedere"),
                ("casellario", "Casellario giudiziale", "Per pubblicare"),
            ]
            rows_html = ""
            for s in ob["subjects"]:
                role_lbl = ONORAB_ROLE_LABELS.get(s["role"], s["role"])
                who = (esc(s["subject_name"] or "-") + " &mdash; " + esc(role_lbl))
                for kind, kind_lbl, scope in doc_kinds:
                    cur = s[kind]
                    badge = ('<span class="badge success">Acquisito</span>' if cur
                             else f'<span class="badge {"warning" if kind == "autodich" else "danger"}">Mancante</span>')
                    tog = ""
                    if not locked:
                        tog = (f'<form method="post" action="/pariter/practices/{pid}/onorabilita" style="display:inline">'
                               f'{hidden_ctx(ctx)}<input type="hidden" name="action" value="toggle">'
                               f'<input type="hidden" name="role" value="{s["role"]}"><input type="hidden" name="kind" value="{kind}">'
                               f'<input type="hidden" name="val" value="{0 if cur else 1}">'
                               f'<button class="button tiny" type="submit">{"Segna mancante" if cur else "Segna acquisito"}</button></form>')
                    rows_html += (f'<tr><td><strong>{kind_lbl}</strong><br><span class="muted">{who}</span></td>'
                                  f'<td><span class="muted">{scope}</span></td>'
                                  f'<td>{badge}</td><td>{tog}</td></tr>')
            if not ob["procedibile"]:
                note = f'<p class="muted">Mancano le autodichiarazioni per: <strong>{esc(", ".join(ob["missing_autodich"]))}</strong>. Senza, la pratica non e\' procedibile.</p>'
            elif not ob["pubblicabile"]:
                note = (f'<p class="muted">Autodichiarazioni acquisite: la pratica <strong>procede</strong> (istruttoria, scoring, Advisory, CdA). '
                        f'Per <strong>pubblicare</strong> servono i casellari ancora mancanti: <strong>{esc(", ".join(ob["missing_casellario"]))}</strong>.</p>')
            else:
                note = '<p class="muted">Tutti i casellari dovuti sono presenti: requisito di onorabilita\' soddisfatto anche per la pubblicazione.</p>'
            reconf = (f'<form method="post" action="/pariter/practices/{pid}/onorabilita" style="display:inline" '
                      f'onsubmit="return confirm(\'Riconfigurare i soggetti? Le spunte verranno azzerate.\')">'
                      f'{hidden_ctx(ctx)}<input type="hidden" name="action" value="reset">'
                      f'<button class="button tiny secondary" type="submit">Riconfigura soggetti</button></form>')
            body = f"""
  {note}
  <table class="data-table compact"><thead><tr><th>Documento di onorabilita'</th><th>A cosa serve</th><th>Stato</th><th></th></tr></thead><tbody>{rows_html}</tbody></table>
  <div class="form-actions left">{reconf}</div>"""
        return f"""
<section class="panel">
  <div class="section-head"><h2>Onorabilita' art. 5 - legale rappresentante e titolare effettivo</h2><div class="header-badges">{proc_badge}{pub_badge}</div></div>
  {body}
</section>"""

    def post_practice_phase(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "documentale")
        if not practice:
            return
        phase = form.get("phase", "")
        status = "completata" if form.get("status") == "completata" else "da_completare"
        with connect() as conn:
            updated = conn.execute(
                "UPDATE practice_phases SET status = ?, updated_at = ?, updated_by = ? WHERE practice_id = ? AND phase = ?",
                (status, now_iso(), ctx["user_id"], practice_id, phase),
            ).rowcount
            if not updated:
                conn.execute(
                    "INSERT INTO practice_phases(practice_id, phase, status, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)",
                    (practice_id, phase, status, now_iso(), ctx["user_id"]),
                )
            conn.execute("UPDATE practices SET updated_at = ? WHERE id = ?", (now_iso(), practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Completamento fase", f"{phase}: {status}")
            conn.commit()
        msg = "Fase segnata come completata." if status == "completata" else "Fase riaperta."
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, {"tab": "documentale", "dtab": phase})

    def post_practice_intake(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "fase1")
        if not practice:
            return
        action = form.get("action", "take")
        with connect() as conn:
            if action == "annul":
                conn.execute("UPDATE practices SET pratica_no = '', updated_at = ? WHERE id = ?", (now_iso(), practice_id))
                log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Presa in carico annullata", "")
                conn.commit()
                msg = "Presa in carico annullata: la pratica torna 'da prendere in carico'."
            elif not practice["pratica_no"]:
                nr = f"{int(uuid.uuid4().int % 100000000):08d}"
                conn.execute("UPDATE practices SET pratica_no = ?, updated_at = ? WHERE id = ?", (nr, now_iso(), practice_id))
                log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Presa in carico", f"numero pratica {nr}")
                conn.commit()
                msg = f"Presa in carico effettuata. Numero pratica: {nr}. Invia ora la comunicazione C2 al proponente."
            else:
                msg = "Presa in carico gia' effettuata."
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, {"fase": "fase1"})

    def post_practice_validate_ammissibilita(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "fase2")
        if not practice:
            return
        reasons = self.ammissibilita_blockers(practice)
        if reasons:
            self.redirect(f"/pariter/practices/{practice_id}", ctx,
                          "Non si puo' validare: " + "; ".join(reasons) + ".", {"fase": "fase2", "sub": "validazione"})
            return
        d = self.practice_email_defaults(practice, "C4")
        with connect() as conn:
            # invia/registra C4 (verifica documentale positiva)
            conn.execute(
                """INSERT INTO practice_emails(practice_id, step_key, code, recipient, subject, body, sent_at, sent_by)
                   VALUES (?, 'fase2', 'C4', ?, ?, ?, ?, ?)""",
                (practice_id, d["recipient"], d["subject"], d["body"], now_iso(), ctx["user_id"]))
            # marca completata la fase ammissibilita'
            updated = conn.execute(
                "UPDATE practice_process_steps SET status='completata', updated_at=?, updated_by=? WHERE practice_id=? AND step_key='fase2'",
                (now_iso(), ctx["user_id"], practice_id)).rowcount
            if not updated:
                conn.execute(
                    "INSERT INTO practice_process_steps(practice_id, step_key, status, updated_at, updated_by) VALUES (?, 'fase2', 'completata', ?, ?)",
                    (practice_id, now_iso(), ctx["user_id"]))
            # avanza lo stato verso la valutazione di merito, se ammesso dalla macchina a stati
            block = can_transition_practice(conn, practice, "pronto_cvoi")
            if not block:
                conn.execute("UPDATE practices SET status='pronto_cvoi', updated_at=? WHERE id=?", (now_iso(), practice_id))
                conn.execute(
                    """INSERT INTO practice_status_history(practice_id, from_status, to_status, actor_id, notes, created_at)
                       VALUES (?, ?, 'pronto_cvoi', ?, 'Ammissibilita validata: avvio valutazione di merito', ?)""",
                    (practice_id, practice["status"], ctx["user_id"], now_iso()))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Ammissibilita validata", "C4 inviata")
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx,
                      "Ammissibilita' validata: C4 inviata, pratica in valutazione di merito (CVOI).", {"fase": "fase3"})

    def post_practice_anagrafica(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "fase1")
        if not practice:
            return
        try:
            js = json.loads(practice["dossier_json"] or "{}")
        except (ValueError, TypeError):
            js = {}
        ds = js.setdefault("dati_struttura", {})
        soc = ds.setdefault("societa", {})
        rep = ds.setdefault("legaleRappresentante", {})
        off = ds.setdefault("offertaFase1", {})
        g = lambda k: (form.get(k, "") or "").strip()
        denom = g("denominazione")
        soc["denominazione"] = denom
        soc["forma"] = g("forma")
        soc["sedeLegale"] = g("sedeLegale")
        soc["pIva"] = g("pIva")
        soc["pec"] = g("pec")
        rep["nome"] = g("rep_nome")
        rep["carica"] = g("rep_carica")
        off["importoTarget"] = g("importoTarget")
        off["importoMax"] = g("importoMax")
        off["preMoney"] = g("preMoney")
        off["equity"] = g("equity")
        off["strumento"] = g("strumento")
        project_title = g("project_title") or practice["project_title"]
        with connect() as conn:
            conn.execute(
                """UPDATE practices SET proponent_name = ?, project_title = ?, instrument = ?,
                   target_amount = ?, max_amount = ?, pre_money = ?, equity_percent = ?,
                   dossier_json = ?, updated_at = ? WHERE id = ?""",
                (denom or practice["proponent_name"], project_title, off["strumento"],
                 _parse_amount(off["importoTarget"]), _parse_amount(off["importoMax"]),
                 _parse_amount(off["preMoney"]), off["equity"],
                 json.dumps(js, ensure_ascii=False), now_iso(), practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Anagrafica aggiornata", denom)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Anagrafica salvata.", {"fase": "fase1"})

    def post_practice_create_manual(self, form):
        ctx = self.ctx_from_form(form)
        if ctx["platform_id"] != 1 or not user_can(ctx["user"], "manage_practice"):
            self.redirect("/deals", ctx, "Ruolo non abilitato.")
            return
        g = lambda k: (form.get(k, "") or "").strip()
        denom = g("denominazione")
        title = g("project_title") or (f"Offerta {denom}" if denom else "Nuova candidatura")
        if not denom:
            self.redirect("/pariter/practices/import", ctx, "Inserisci almeno la denominazione del proponente.")
            return
        dossier = {"jsons": {"dati_struttura": {
            "meta": {"piattaforma": "Inserimento manuale"},
            "societa": {"denominazione": denom, "forma": g("forma"), "sedeLegale": g("sedeLegale"),
                        "pIva": g("pIva"), "pec": g("pec")},
            "legaleRappresentante": {"nome": g("rep_nome"), "carica": g("rep_carica")},
            "offertaFase1": {"importoTarget": g("importoTarget"), "importoMax": g("importoMax"),
                             "preMoney": g("preMoney"), "equity": g("equity"), "strumento": g("strumento")},
        }}, "files": []}
        with connect() as conn:
            mapped = map_dossier_to_practice(dossier, 1, title)
            practice_id = ingest_practice(conn, dossier, mapped, 1, ctx["user_id"])
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Nuova candidatura manuale", denom)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx,
                      "Nuova candidatura creata: completa l'anagrafica, carica i documenti e prendi in carico.", {"fase": "fase1"})

    def post_practice_step(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "processo")
        if not practice:
            return
        step_key = form.get("step_key", "")
        status = "completata" if form.get("status") == "completata" else "da_fare"
        with connect() as conn:
            updated = conn.execute(
                "UPDATE practice_process_steps SET status = ?, updated_at = ?, updated_by = ? WHERE practice_id = ? AND step_key = ?",
                (status, now_iso(), ctx["user_id"], practice_id, step_key),
            ).rowcount
            if not updated:
                conn.execute(
                    "INSERT INTO practice_process_steps(practice_id, step_key, status, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)",
                    (practice_id, step_key, status, now_iso(), ctx["user_id"]),
                )
            conn.execute("UPDATE practices SET updated_at = ? WHERE id = ?", (now_iso(), practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Processo", f"{step_key}: {status}")
            conn.commit()
        back = form.get("back_fase") or (step_key if step_key in {s["key"] for s in ONBOARDING_STEPS} else "fase2")
        params = {"fase": back}
        if form.get("back_sub"):
            params["sub"] = form.get("back_sub")
        elif step_key.startswith("chk_"):
            params["sub"] = "ammissibilita"
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Fase aggiornata." if status == "completata" else "Fase riaperta.", params)

    def post_practice_email(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "processo")
        if not practice:
            return
        with connect() as conn:
            conn.execute(
                """INSERT INTO practice_emails(practice_id, step_key, code, recipient, subject, body, sent_at, sent_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (practice_id, form.get("step_key", ""), form.get("code", ""), form.get("recipient", ""),
                 form.get("subject", ""), form.get("body", ""), now_iso(), ctx["user_id"]),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Comunicazione inviata", form.get("code", ""))
            conn.commit()
        params = {"fase": form.get("back_fase") or "riepilogo"}
        if form.get("back_sub"):
            params["sub"] = form.get("back_sub")
        self.redirect(f"/pariter/practices/{practice_id}", ctx, f"Comunicazione {form.get('code','')} registrata come inviata.", params)

    def post_practice_document_upload(self, practice_id, form, files):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "documentale")
        if not practice:
            return
        file_item = files.get("file")
        if not (file_item and getattr(file_item, "filename", "")):
            self.redirect(f"/pariter/practices/{practice_id}", ctx, "Nessun file caricato.", {"tab": "documentale"})
            return
        doc_row = row("SELECT * FROM practice_documents WHERE id = ? AND practice_id = ?", (int(form["doc_id"]), practice_id))
        with connect() as conn:
            document_id = save_uploaded_document(
                conn, file_item, ctx["platform_id"], None, practice["proponent_id"],
                "Proponente", (doc_row["phase"] if doc_row else "dossier"),
                doc_row["label"] if doc_row else file_item.filename, ctx["user_id"],
            )
            link_document_practice(conn, document_id, practice_id)
            conn.execute(
                """UPDATE practice_documents SET document_id = ?, doc_status = 'da_verificare', updated_by = ?, updated_at = ?
                   WHERE id = ? AND practice_id = ?""",
                (document_id, ctx["user_id"], now_iso(), int(form["doc_id"]), practice_id),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Upload documento pratica", doc_row["label"] if doc_row else "")
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Documento caricato e collegato.", {"tab": "documentale"})

    def post_practice_internal_review(self, practice_id, form, files=None):
        files = files or {}
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "interne")
        if not practice:
            return
        rtype = form.get("review_type", "")
        action = form.get("action", "generate")
        # ogni relazione vive nella sua fase: AML->fase2, coerenza KIIS->fase3, conflitti->fase5
        back = {"aml_art5": {"fase": "fase2", "sub": "ammissibilita"},
                "coerenza_kiis": {"fase": "fase3", "sub": "verifiche"},
                "fascicolo": {"fase": "fase3", "sub": "fascicolo"},
                "conflitti": {"fase": "fase5"}}.get(rtype, {"fase": "fase2"})
        if rtype not in INTERNAL_REVIEW_LABELS:
            self.redirect(f"/pariter/practices/{practice_id}", ctx, "Tipo relazione non valido.", back)
            return
        with connect() as conn:
            existing = conn.execute(
                "SELECT * FROM internal_reviews WHERE practice_id = ? AND review_type = ?", (practice_id, rtype)
            ).fetchone()

            def _upsert(fields):
                cols = ", ".join(f"{k} = ?" for k in fields)
                vals = list(fields.values())
                if existing:
                    conn.execute(
                        f"UPDATE internal_reviews SET {cols}, updated_by = ?, updated_at = ? WHERE id = ?",
                        vals + [ctx["user_id"], now_iso(), existing["id"]],
                    )
                    return existing["id"]
                keys = ["practice_id", "review_type"] + list(fields) + ["updated_by", "updated_at"]
                ph = ", ".join("?" for _ in keys)
                cur = conn.execute(
                    f"INSERT INTO internal_reviews({', '.join(keys)}) VALUES ({ph})",
                    [practice_id, rtype] + vals + [ctx["user_id"], now_iso()],
                )
                return cur.lastrowid

            cur_status = existing["review_status"] if existing else "non_generata"
            # una relazione validata e' definitiva: per rigenerarla/modificarla va prima rimossa
            if cur_status == "validata" and action in ("generate", "save_body", "sign", "upload"):
                self.redirect(f"/pariter/practices/{practice_id}", ctx,
                              "Relazione gia' validata e in istruttoria: rimuovila per rigenerarla.", back)
                return

            prev_doc = existing["generated_document_id"] if existing else None

            def _drop_doc(doc_id):
                """Elimina file+record del documento collegato (qualsiasi tipo)."""
                if not doc_id:
                    return
                d = conn.execute("SELECT storage_path FROM documents WHERE id = ?", (doc_id,)).fetchone()
                if d:
                    try:
                        (BASE_DIR / d["storage_path"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

            if action == "save_body":
                body = (form.get("body", "") or "").strip()
                doc_id = store_review_draft(conn, practice, rtype, body, ctx["user_id"], prev_doc)
                _upsert({"body": body, "review_status": "bozza", "generated_document_id": doc_id})
                msg = "Bozza salvata: puoi scaricarla, modificarla, firmarla o validarla."
            elif action == "remove":
                _drop_doc(prev_doc)
                if existing:
                    conn.execute(
                        """UPDATE internal_reviews SET review_status = 'non_generata', body = '',
                           generated_document_id = NULL, signed_by = '', signed_at = '', updated_by = ?, updated_at = ?
                           WHERE id = ?""",
                        (ctx["user_id"], now_iso(), existing["id"]),
                    )
                msg = "Relazione rimossa: tornata allo stato 'da generare'."
            elif action == "upload":
                file_item = files.get("file")
                if file_item is None or not getattr(file_item, "filename", ""):
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Nessun file relazione.", back)
                    return
                _drop_doc(prev_doc)
                title = INTERNAL_REVIEW_LABELS.get(rtype, rtype)
                doc_id = save_uploaded_document(
                    conn, file_item, practice["platform_id"], None, practice["proponent_id"],
                    "Verifiche interne", "relazione", f"{title} - {practice['project_title']}", ctx["user_id"])
                link_document_practice(conn, doc_id, practice_id)
                _upsert({"review_status": "caricata", "generated_document_id": doc_id, "signed_by": "", "signed_at": ""})
                msg = "Relazione caricata a mano: ora puoi validarla."
            elif action == "validate":
                if not (existing and existing["generated_document_id"]):
                    self.redirect(f"/pariter/practices/{practice_id}", ctx,
                                  "Genera/firma o carica la relazione prima di validarla.", back)
                    return
                conn.execute(
                    "UPDATE internal_reviews SET review_status = 'validata', updated_by = ?, updated_at = ? WHERE id = ?",
                    (ctx["user_id"], now_iso(), existing["id"]),
                )
                msg = "Relazione validata: entra nell'istruttoria."
            elif action == "sign":
                body = (form.get("body", "") or "").strip()
                if not body and existing and existing["body"]:
                    body = existing["body"]
                if not body:
                    body = compose_internal_review_draft(practice, rtype)
                resp_name, _f = org_responsabile(
                    practice["platform_id"], ["controllo di 2", "funzioni di controllo", "controlli", "conflitt"],
                    fallback="Responsabile delle funzioni di controllo")
                firma_path, valid = team_signature(conn, practice["platform_id"], resp_name)
                if not valid:
                    self.redirect(f"/pariter/practices/{practice_id}", ctx,
                                  f"Firma non disponibile: carica firma e documento d'identita' di {resp_name} in anagrafica team prima di firmare.",
                                  back)
                    return
                # rimuove la bozza HTML scaricabile prima di archiviare il PDF definitivo
                if prev_doc:
                    d = conn.execute("SELECT storage_path, is_pdf, archived FROM documents WHERE id = ?", (prev_doc,)).fetchone()
                    if d and not d["is_pdf"] and not d["archived"]:
                        try:
                            (BASE_DIR / d["storage_path"]).unlink(missing_ok=True)
                        except OSError:
                            pass
                        conn.execute("DELETE FROM documents WHERE id = ?", (prev_doc,))
                title = INTERNAL_REVIEW_LABELS.get(rtype, rtype)
                html_doc = practice_doc_shell(title, practice, review_text_to_html(body), ai_generated=False)
                signed_at = now_iso()
                data_firma = signed_at[:10]
                html_doc = apply_signature_html(html_doc, resp_name, firma_path, data_firma, valid)
                document_id, _is_pdf = archive_pdf(
                    conn, practice["platform_id"], practice_id, practice["proponent_id"],
                    "Verifiche interne", "relazione", f"{title} - {practice['project_title']}",
                    html_doc, ctx["user_id"],
                )
                _upsert({"body": body, "review_status": "firmata", "generated_document_id": document_id,
                         "signed_by": resp_name, "signed_at": signed_at})
                fmt = "PDF" if _is_pdf else "HTML (Chrome non disponibile)"
                msg = f"Relazione firmata da {resp_name} ({fmt}). Validala per farla entrare nell'istruttoria."
            else:  # generate (precompila la bozza dal modello + documento scaricabile)
                body = compose_internal_review_draft(practice, rtype)
                doc_id = store_review_draft(conn, practice, rtype, body, ctx["user_id"], prev_doc)
                _upsert({"body": body, "review_status": "bozza", "generated_document_id": doc_id})
                msg = "Bozza generata dal modello e precompilata: modificala e poi firmala."
            conn.execute("UPDATE practices SET updated_at = ? WHERE id = ?", (now_iso(), practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, f"Relazione {rtype}", action)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, back)

    def post_practice_cda_convoca(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "fase5", perm="board_decision")
        if not practice:
            return
        meeting_date = form.get("meeting_date") or today_iso()
        title = (form.get("title") or f"CdA - Delibera {practice['project_title']}").strip()
        agenda = form.get("agenda") or f"Valutazione e delibera sulla pratica {practice['project_title']} ({practice['proponent_name'] or '-'})"
        meta = []
        if form.get("meeting_time"):
            meta.append(f"Ora: {form.get('meeting_time')}")
        if form.get("meeting_mode"):
            meta.append(f"Modalita: {form.get('meeting_mode')}")
        if form.get("meeting_place"):
            meta.append(f"Luogo: {form.get('meeting_place')}")
        if meta:
            agenda = agenda + "\n\n" + " / ".join(meta)
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO board_meetings(platform_id, title, meeting_date, meeting_link, agenda, status, created_at, practice_id)
                   VALUES (?, ?, ?, ?, ?, 'Pianificata', ?, ?)""",
                (practice["platform_id"], title, meeting_date, form.get("meeting_link", ""), agenda, now_iso(), practice_id),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "board_meeting", cur.lastrowid,
                      "Convocazione CdA da fascicolo pratica", title)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx,
                      "Convocazione CdA creata: visibile in Governance > Convocazioni.", {"fase": "fase5"})

    def post_practice_cda_verbale(self, practice_id, form, files):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "fase5", perm="board_decision")
        if not practice:
            return
        file_item = files.get("file")
        if file_item is None or not getattr(file_item, "filename", ""):
            self.redirect(f"/pariter/practices/{practice_id}", ctx, "Nessun file verbale.", {"fase": "fase5"})
            return
        with connect() as conn:
            doc_id = save_uploaded_document(
                conn, file_item, practice["platform_id"], None, practice["proponent_id"],
                "Delibera CdA", "verbale", f"Verbale CdA - {practice['project_title']}", ctx["user_id"])
            link_document_practice(conn, doc_id, practice_id)
            decision = conn.execute(
                "SELECT id FROM practice_board_decisions WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (practice_id,)
            ).fetchone()
            if decision:
                conn.execute("UPDATE practice_board_decisions SET generated_document_id = ? WHERE id = ?",
                             (doc_id, decision["id"]))
            meeting = conn.execute(
                "SELECT id FROM board_meetings WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (practice_id,)
            ).fetchone()
            if meeting:
                conn.execute("UPDATE board_meetings SET minutes_document_id = ?, status = 'Verbalizzata' WHERE id = ?",
                             (doc_id, meeting["id"]))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Verbale CdA caricato", "")
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Verbale del CdA caricato e collegato.", {"fase": "fase5"})

    def post_practice_integration_request(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "documentale")
        if not practice:
            return
        with connect() as conn:
            if form.get("close_id"):
                conn.execute(
                    "UPDATE integration_requests SET req_status = 'chiusa', resolved_at = ? WHERE id = ? AND practice_id = ?",
                    (now_iso(), int(form["close_id"]), practice_id),
                )
                msg = "Richiesta di integrazione chiusa."
            else:
                pdoc_id = int(form["practice_document_id"]) if form.get("practice_document_id") else None
                conn.execute(
                    """INSERT INTO integration_requests(practice_id, practice_document_id, phase, subject, detail, priority, req_status, requested_by, created_at)
                       VALUES (?, ?, '', ?, ?, ?, 'inviata', ?, ?)""",
                    (practice_id, pdoc_id, form.get("subject", "Integrazione"), form.get("detail", ""),
                     form.get("priority", "non_bloccante"), ctx["user_id"], now_iso()),
                )
                if pdoc_id:
                    conn.execute(
                        "UPDATE practice_documents SET integration_requested = 1, doc_status = 'da_integrare', updated_at = ? WHERE id = ? AND practice_id = ?",
                        (now_iso(), pdoc_id, practice_id),
                    )
                msg = "Richiesta di integrazione inviata al proponente."
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Richiesta integrazione", form.get("subject", ""))
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, {"tab": "documentale"})

    def post_practice_cvoi(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "cvoi", perm="cvoi_draft")
        if not practice:
            return
        action = form.get("action", "save_scores")
        is_admin = ctx["user"]["role"] == "admin"
        back = {"fase": "fase3", "sub": "scoring"}
        now = now_iso()
        with connect() as conn:
            evaluators = {e["id"]: e["name"] for e in cvoi_committee_members(conn)}

            def _ev_from_form():
                raw = (form.get("evaluator_id", "") or "").strip()
                eid = int(raw) if raw.isdigit() else ctx["user_id"]
                return eid

            def _set_confirmed(eid, val):
                cur = conn.execute("SELECT id FROM cvoi_eval_status WHERE practice_id = ? AND evaluator_id = ?", (practice_id, eid)).fetchone()
                if cur:
                    conn.execute("UPDATE cvoi_eval_status SET confirmed = ?, updated_at = ? WHERE id = ?", (val, now, cur["id"]))
                else:
                    conn.execute("INSERT INTO cvoi_eval_status(practice_id, evaluator_id, abstained, confirmed, updated_at) VALUES (?, ?, 0, ?, ?)",
                                 (practice_id, eid, val, now))

            if action in ("save_scores", "confirm_scores"):
                eid = _ev_from_form()
                if eid not in evaluators:
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Valutatore non valido.", back); return
                if not is_admin and eid != ctx["user_id"]:
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Puoi inserire solo i tuoi punteggi.", back); return
                conn.execute("DELETE FROM cvoi_eval_scores WHERE practice_id = ? AND evaluator_id = ?", (practice_id, eid))
                for key, _l, _w, _m, _t in CVOI_AREAS:
                    for i in range(len(CVOI_CRITERIA[key])):
                        raw = (form.get(f"raw_{key}_{i}", "") or "").strip()
                        if raw == "":
                            continue
                        try:
                            v = max(0.0, min(float(raw), CVOI_CRITERION_MAX))
                        except ValueError:
                            continue
                        conn.execute("INSERT INTO cvoi_eval_scores(practice_id, evaluator_id, area_key, idx, score) VALUES (?, ?, ?, ?, ?)",
                                     (practice_id, eid, key, i, v))
                confirmed = 1 if action == "confirm_scores" else 0
                _set_confirmed(eid, confirmed)
                rid, c = save_cvoi_collegial(conn, practice, ctx["user_id"])
                az = "validazione punteggi" if confirmed else "punteggi"
                conn.execute("INSERT INTO cvoi_edit_log(cvoi_report_id, actor_user_id, actor_name, action, summary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                             (rid, ctx["user_id"], ctx["user"]["name"], az, f"{evaluators[eid]} - media ponderata {c['weighted']:g} ({c['n_val']} valutatori)", now))
                msg = (f"Punteggi di {evaluators[eid]} {'validati' if confirmed else 'salvati'}. Media ponderata {c['weighted']:g}/{CVOI_OVERALL_THRESHOLD:g}.")
            elif action == "unconfirm":
                eid = _ev_from_form()
                if eid not in evaluators or (not is_admin and eid != ctx["user_id"]):
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Azione non consentita.", back); return
                _set_confirmed(eid, 0)
                save_cvoi_collegial(conn, practice, ctx["user_id"])
                msg = f"Validazione di {evaluators[eid]} annullata: puoi modificare i punteggi."
            elif action == "abstain":
                eid = _ev_from_form()
                if eid not in evaluators or (not is_admin and eid != ctx["user_id"]):
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Azione non consentita.", back); return
                cur = conn.execute("SELECT id, abstained FROM cvoi_eval_status WHERE practice_id = ? AND evaluator_id = ?", (practice_id, eid)).fetchone()
                new_abst = 0 if (cur and cur["abstained"]) else 1
                reason = (form.get("reason", "") or "").strip()
                if cur:
                    conn.execute("UPDATE cvoi_eval_status SET abstained = ?, reason = ?, updated_at = ? WHERE id = ?",
                                 (new_abst, reason, now, cur["id"]))
                else:
                    conn.execute("INSERT INTO cvoi_eval_status(practice_id, evaluator_id, abstained, reason, updated_at) VALUES (?, ?, ?, ?, ?)",
                                 (practice_id, eid, new_abst, reason, now))
                rid, c = save_cvoi_collegial(conn, practice, ctx["user_id"])
                conn.execute("INSERT INTO cvoi_edit_log(cvoi_report_id, actor_user_id, actor_name, action, summary, created_at) VALUES (?, ?, ?, 'astensione', ?, ?)",
                             (rid, ctx["user_id"], ctx["user"]["name"], f"{evaluators[eid]}: {'astensione' if new_abst else 'revoca astensione'}{(' - ' + reason) if (new_abst and reason) else ''}", now))
                msg = f"{evaluators[eid]}: {'astensione registrata' if new_abst else 'astensione revocata'}."
            elif action == "save_meta":
                rid, _c = save_cvoi_collegial(conn, practice, ctx["user_id"])
                conn.execute("""UPDATE cvoi_reports SET mail = ?, data_caricamento = ?, data_valutazione = ?,
                                notes_qualitative = ?, updated_at = ? WHERE id = ?""",
                             (form.get("mail", ""), form.get("data_caricamento", ""), form.get("data_valutazione", ""),
                              form.get("notes_qualitative", ""), now, rid))
                msg = "Dati di scoring salvati."
            elif action == "genera":
                rid, c = save_cvoi_collegial(conn, practice, ctx["user_id"])
                if not c["valid"]:
                    self.redirect(f"/pariter/practices/{practice_id}", ctx,
                                  "Servono almeno 2 valutatori non astenuti per generare la scheda M6.", back); return
                rep = conn.execute("SELECT * FROM cvoi_reports WHERE id = ?", (rid,)).fetchone()
                report_fields = {"mail": rep["mail"] if "mail" in rep.keys() else "",
                                 "data_caricamento": rep["data_caricamento"] if "data_caricamento" in rep.keys() else "",
                                 "data_valutazione": rep["data_valutazione"] if "data_valutazione" in rep.keys() else "",
                                 "notes_qualitative": rep["notes_qualitative"] if "notes_qualitative" in rep.keys() else ""}
                html_doc = build_cvoi_html(practice, report_fields, c["criteria_scores"])
                document_id = generated_document(
                    conn, ctx["platform_id"], None, practice["proponent_id"],
                    "CVOI", "verbale", f"Scheda di scoring CVOI (M6) - {practice['project_title']}",
                    "scoring_m6.html", html_doc, ctx["user_id"])
                link_document_practice(conn, document_id, practice_id)
                conn.execute("UPDATE cvoi_reports SET generated_document_id = ?, updated_at = ? WHERE id = ?",
                             (document_id, now, rid))
                msg = "Scheda di scoring (M6) generata (non firmata)."
            else:
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Azione non riconosciuta.", back); return
            conn.execute("UPDATE practices SET updated_at = ? WHERE id = ?", (now, practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "CVOI", action)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, back)

    def post_practice_close(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "riepilogo", perm="close_practice", allow_closed=True)
        if not practice:
            return
        action = form.get("action", "close")
        with connect() as conn:
            if action == "reopen":
                if ctx["user"]["role"] != "admin":
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Solo l'amministratore puo' riaprire una pratica.", {"tab": "riepilogo"})
                    return
                conn.execute("UPDATE practices SET closed_at = '', updated_at = ? WHERE id = ?", (now_iso(), practice_id))
                log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Riapertura pratica", "")
                conn.commit()
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Pratica riaperta.", {"tab": "riepilogo"})
                return
            outcome = form.get("closure_outcome", "Archiviata")
            note = form.get("closure_note", "")
            now = now_iso()
            conn.execute(
                "UPDATE practices SET closed_at = ?, closure_outcome = ?, closure_note = ? WHERE id = ?",
                (now, outcome, note, practice_id),
            )
            set_practice_status(conn, practice, "archiviata", ctx["user_id"], f"Chiusura pratica: {outcome}", note)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Pratica chiusa e archiviata.", {"tab": "riepilogo"})

    def post_practice_cvoi_member(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self.get_practice(practice_id)
        if not practice or practice["platform_id"] != ctx["platform_id"]:
            self.not_found()
            return
        if practice["closed_at"]:
            self.redirect(f"/pariter/practices/{practice_id}", ctx, "Pratica chiusa: sola lettura.", {"fase": "fase3", "sub": "scoring"})
            return
        if not user_can(ctx["user"], "cvoi_sign"):
            self.redirect(f"/pariter/practices/{practice_id}", ctx, "Azione riservata ai membri del Comitato Tecnico.", {"fase": "fase3", "sub": "scoring"})
            return
        is_admin = ctx["user"]["role"] == "admin"
        action = form.get("action", "")
        now = now_iso()
        with connect() as conn:
            report = conn.execute("SELECT * FROM cvoi_reports WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (practice_id,)).fetchone()
            if not report:
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Genera prima il verbale CVOI.", {"fase": "fase3", "sub": "scoring"})
                return
            rid = report["id"]

            def upsert(member_id, member_name, status, signed):
                updated = conn.execute(
                    "UPDATE cvoi_member_reviews SET status = ?, signed_at = ?, member_name = ?, updated_at = ? WHERE cvoi_report_id = ? AND user_id = ?",
                    (status, signed, member_name, now, rid, member_id),
                ).rowcount
                if not updated:
                    conn.execute(
                        "INSERT INTO cvoi_member_reviews(cvoi_report_id, user_id, member_name, role, status, signed_at, updated_at) VALUES (?, ?, ?, 'technical_committee', ?, ?, ?)",
                        (rid, member_id, member_name, status, signed, now),
                    )

            if action == "force_unanime":
                if not is_admin:
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Solo l'amministratore puo' forzare l'unanimita'.", {"fase": "fase3", "sub": "scoring"})
                    return
                for m in cvoi_committee_members(conn):
                    upsert(m["id"], m["name"], "approvato", now)
                conn.execute("UPDATE cvoi_reports SET workflow_status = 'unanime' WHERE id = ?", (rid,))
                conn.execute(
                    "INSERT INTO cvoi_edit_log(cvoi_report_id, actor_user_id, actor_name, action, summary, created_at) VALUES (?, ?, ?, 'forzatura', 'Versione resa unanime (admin)', ?)",
                    (rid, ctx["user_id"], ctx["user"]["name"], now),
                )
                msg = "Versione resa unanime dall'amministratore."
            else:
                member_id = int(form.get("member_id") or ctx["user_id"])
                if not is_admin and member_id != ctx["user_id"]:
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Puoi firmare solo per te stesso.", {"fase": "fase3", "sub": "scoring"})
                    return
                m = conn.execute("SELECT name FROM users WHERE id = ?", (member_id,)).fetchone()
                member_name = m["name"] if m else ""
                if action == "sign":
                    upsert(member_id, member_name, "approvato", now)
                    log_action, log_summary = "firma", f"{member_name}: favorevole e firmato"
                    msg = "Voto favorevole registrato."
                elif action == "contrario":
                    upsert(member_id, member_name, "contrario", "")
                    log_action, log_summary = "contrario", f"{member_name}: contrario"
                    msg = "Voto contrario registrato."
                else:  # request_change
                    upsert(member_id, member_name, "modifica_richiesta", "")
                    log_action, log_summary = "modifica", f"{member_name}: richiesta modifica"
                    msg = "Richiesta di modifica registrata."
                conn.execute(
                    "INSERT INTO cvoi_edit_log(cvoi_report_id, actor_user_id, actor_name, action, summary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (rid, ctx["user_id"], ctx["user"]["name"], log_action, log_summary, now),
                )
                if recompute_cvoi_unanime(conn, rid):
                    msg += " La versione e' ora unanime."
            conn.execute("UPDATE practices SET updated_at = ? WHERE id = ?", (now, practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "CVOI - approvazione membro", action)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, {"fase": "fase3", "sub": "scoring"})

    def post_practice_board_decision(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        round_no = 1  # delibera unica del CdA, dopo il parere dell'Advisory Committee
        back_tab = "cda"
        practice = self._practice_guard(ctx, practice_id, back_tab, perm="board_decision")
        if not practice:
            return
        action = form.get("action", "finalize")
        is_admin = ctx["user"]["role"] == "admin"
        now = now_iso()
        with connect() as conn:
            decision = conn.execute(
                "SELECT * FROM practice_board_decisions WHERE practice_id = ? AND decision_round = ? ORDER BY id DESC LIMIT 1",
                (practice_id, round_no),
            ).fetchone()

            if action == "open":
                if not advisory_is_unanime(conn, practice_id):
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Serve il parere Advisory in versione unanime prima della delibera del CdA.", {"tab": back_tab})
                    return
                if decision and decision["decision_status"] == "in_votazione":
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Delibera gia' aperta.", {"tab": back_tab})
                    return
                conn.execute(
                    """INSERT INTO practice_board_decisions(practice_id, decision_round, meeting_date, agenda, outcome, decision_status, created_by, created_at)
                       VALUES (?, ?, ?, ?, '', 'in_votazione', ?, ?)""",
                    (practice_id, round_no, form.get("meeting_date", ""), form.get("agenda", ""), ctx["user_id"], now),
                )
                log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, f"Delibera CdA {round_no}", "apertura votazione")
                conn.commit()
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Delibera aperta: i consiglieri possono votare.", {"tab": back_tab})
                return

            if not decision:
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Apri prima la delibera.", {"tab": back_tab})
                return
            rid = decision["id"]

            if action == "vote":
                if decision["decision_status"] != "in_votazione":
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "La votazione non e' aperta.", {"tab": back_tab})
                    return
                member_id = int(form.get("member_id") or ctx["user_id"])
                if not is_admin and member_id != ctx["user_id"]:
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Puoi votare solo per te stesso.", {"tab": back_tab})
                    return
                vote = form.get("vote", "astenuto")
                if vote not in {"approva", "contrario", "astenuto"}:
                    vote = "astenuto"
                m = conn.execute("SELECT name FROM users WHERE id = ?", (member_id,)).fetchone()
                mname = m["name"] if m else ""
                updated = conn.execute(
                    "UPDATE board_member_votes SET vote = ?, member_name = ?, voted_at = ? WHERE board_decision_id = ? AND user_id = ?",
                    (vote, mname, now, rid, member_id),
                ).rowcount
                if not updated:
                    conn.execute(
                        "INSERT INTO board_member_votes(board_decision_id, user_id, member_name, vote, voted_at) VALUES (?, ?, ?, ?, ?)",
                        (rid, member_id, mname, vote, now),
                    )
                conn.commit()
                self.redirect(f"/pariter/practices/{practice_id}", ctx, f"Voto registrato: {BOARD_VOTE_LABELS.get(vote, vote)}.", {"tab": back_tab})
                return

            if action == "reopen":
                conn.execute("UPDATE practice_board_decisions SET decision_status = 'in_votazione', outcome = '' WHERE id = ?", (rid,))
                conn.execute("DELETE FROM board_member_votes WHERE board_decision_id = ?", (rid,))
                set_practice_status(conn, practice, "attesa_cda", ctx["user_id"], "Ridelibera CdA dopo sospensione")
                conn.commit()
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Votazione riaperta per ridelibera.", {"tab": back_tab})
                return

            # finalize
            if decision["decision_status"] != "in_votazione":
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Delibera non in votazione.", {"tab": back_tab})
                return
            outcome = form.get("outcome", "sospesa")
            votes_rows = conn.execute(
                "SELECT user_id, member_name, vote FROM board_member_votes WHERE board_decision_id = ?", (rid,)
            ).fetchall()
            vote_map = {v["user_id"]: v["vote"] for v in votes_rows}
            votes = [(m["name"], vote_map.get(m["id"], "in_attesa")) for m in board_members(conn)]
            fields = {
                "meeting_date": decision["meeting_date"] or form.get("meeting_date", ""),
                "attendees": form.get("attendees", "") or decision["attendees"] or "",
                "agenda": decision["agenda"] or "",
                "summary": form.get("summary", ""),
                "conditions": form.get("conditions", ""),
                "outcome": outcome,
            }
            cvoi = cvoi_summary_for(conn, practice_id)
            extra_lines = []
            adv = conn.execute("SELECT outcome FROM advisory_opinions WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (practice_id,)).fetchone()
            if adv:
                extra_lines.append(("Parere Advisory Committee", ADVISORY_OUTCOME_LABELS.get(adv["outcome"], adv["outcome"])))
            verbale_text = form.get("verbale_text", "").strip()
            round_label = "Delibera CdA"
            if verbale_text:
                html_doc = wrap_practice_doc(round_label, practice, verbale_text)
            else:
                html_doc = build_decision_html(practice, round_no, fields, cvoi, extra_lines, votes)
            document_id = generated_document(
                conn, ctx["platform_id"], None, practice["proponent_id"],
                "Delibera CdA", "delibera",
                f"Delibera CdA - {practice['project_title']}",
                "delibera_cda.html", html_doc, ctx["user_id"],
            )
            link_document_practice(conn, document_id, practice_id)
            new_status = "sospesa" if outcome == "sospesa" else "finalizzata"
            conn.execute(
                """UPDATE practice_board_decisions SET attendees = ?, summary = ?, outcome = ?, conditions = ?,
                   generated_document_id = ?, decision_status = ?, finalized_by = ?, finalized_at = ? WHERE id = ?""",
                (fields["attendees"], fields["summary"], outcome, fields["conditions"], document_id, new_status, ctx["user_id"], now, rid),
            )
            target = {"approvata": "in_pre_golive", "approvata_condizioni": "in_pre_golive",
                      "respinta": "respinta", "sospesa": "da_integrare"}.get(outcome)
            if target:
                set_practice_status(conn, practice, target, ctx["user_id"],
                                    f"Delibera CdA: {DECISION_OUTCOME_LABELS.get(outcome, outcome)}", fields["conditions"])
            conn.commit()
        if outcome.startswith("approvata"):
            nxt = "Delibera approvata: fase pre go-live sbloccata."
        elif outcome == "sospesa":
            nxt = "Pratica sospesa: in attesa di revisioni/integrazioni (resta schedulata in CdA)."
        else:
            nxt = "Pratica respinta."
        self.redirect(f"/pariter/practices/{practice_id}", ctx, nxt, {"tab": back_tab})

    def post_practice_advisory(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "advisory", perm="advisory_opinion")
        if not practice:
            return
        outcome = form.get("outcome", "favorevole")
        fields = {k: form.get(k, "") for k in ("meeting_date", "attendees", "summary", "conditions")}
        fields["outcome"] = outcome
        now = now_iso()
        with connect() as conn:
            if not cvoi_is_validated(conn, practice_id):
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Serve un Report CVOI in versione unanime prima del parere Advisory.", {"tab": "advisory"})
                return
            cvoi = cvoi_summary_for(conn, practice_id)
            parere_text = form.get("parere_text", "").strip()
            if parere_text:
                html_doc = wrap_practice_doc("Advisory Committee - parere non vincolante", practice, parere_text)
                fields["summary"] = "Parere redatto (vedi documento)."
            else:
                html_doc = build_advisory_html(practice, fields, cvoi)
            document_id = generated_document(
                conn, ctx["platform_id"], None, practice["proponent_id"],
                "Advisory Committee", "parere", f"Parere Advisory Committee - {practice['project_title']}",
                "parere_advisory.html", html_doc, ctx["user_id"],
            )
            link_document_practice(conn, document_id, practice_id)
            existing = conn.execute("SELECT id FROM advisory_opinions WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (practice_id,)).fetchone()
            if existing:
                conn.execute(
                    """UPDATE advisory_opinions SET meeting_date = ?, attendees = ?, summary = ?, outcome = ?, conditions = ?,
                       generated_document_id = ?, workflow_status = 'in_revisione', drafter_user_id = ? WHERE id = ?""",
                    (fields["meeting_date"], fields["attendees"], fields["summary"], outcome, fields["conditions"],
                     document_id, ctx["user_id"], existing["id"]),
                )
                conn.execute("DELETE FROM advisory_member_reviews WHERE advisory_opinion_id = ?", (existing["id"],))
            else:
                conn.execute(
                    """INSERT INTO advisory_opinions(practice_id, meeting_date, attendees, agenda, summary, outcome, conditions, generated_document_id, workflow_status, drafter_user_id, created_by, created_at)
                       VALUES (?, ?, ?, '', ?, ?, ?, ?, 'in_revisione', ?, ?, ?)""",
                    (practice_id, fields["meeting_date"], fields["attendees"], fields["summary"], outcome,
                     fields["conditions"], document_id, ctx["user_id"], ctx["user_id"], now),
                )
            if PRACTICE_STATUS_INDEX.get(practice["status"], 0) < PRACTICE_STATUS_INDEX["in_advisory"]:
                set_practice_status(conn, practice, "in_advisory", ctx["user_id"], "Parere Advisory in redazione")
            else:
                conn.execute("UPDATE practices SET updated_at = ? WHERE id = ?", (now, practice_id))
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Parere Advisory redatto. Ora i membri firmano per l'unanimita'.", {"tab": "advisory"})

    def post_practice_advisory_member(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self.get_practice(practice_id)
        if not practice or practice["platform_id"] != ctx["platform_id"]:
            self.not_found()
            return
        if practice["closed_at"]:
            self.redirect(f"/pariter/practices/{practice_id}", ctx, "Pratica chiusa: sola lettura.", {"tab": "advisory"})
            return
        if not user_can(ctx["user"], "advisory_opinion"):
            self.redirect(f"/pariter/practices/{practice_id}", ctx, "Azione riservata all'Advisory Committee.", {"tab": "advisory"})
            return
        is_admin = ctx["user"]["role"] == "admin"
        action = form.get("action", "")
        now = now_iso()
        with connect() as conn:
            advisory = conn.execute("SELECT * FROM advisory_opinions WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (practice_id,)).fetchone()
            if not advisory:
                self.redirect(f"/pariter/practices/{practice_id}", ctx, "Redigi prima il parere.", {"tab": "advisory"})
                return
            aid = advisory["id"]

            def upsert(member_id, member_name, status, signed):
                updated = conn.execute(
                    "UPDATE advisory_member_reviews SET status = ?, signed_at = ?, member_name = ?, updated_at = ? WHERE advisory_opinion_id = ? AND user_id = ?",
                    (status, signed, member_name, now, aid, member_id),
                ).rowcount
                if not updated:
                    conn.execute(
                        "INSERT INTO advisory_member_reviews(advisory_opinion_id, user_id, member_name, status, signed_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (aid, member_id, member_name, status, signed, now),
                    )

            if action == "force_unanime":
                if not is_admin:
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Solo l'amministratore puo' forzare.", {"tab": "advisory"})
                    return
                for m in advisory_members(conn):
                    upsert(m["id"], m["name"], "approvato", now)
                conn.execute("UPDATE advisory_opinions SET workflow_status = 'unanime' WHERE id = ?", (aid,))
                became_unanime = True
            else:
                member_id = int(form.get("member_id") or ctx["user_id"])
                if not is_admin and member_id != ctx["user_id"]:
                    self.redirect(f"/pariter/practices/{practice_id}", ctx, "Puoi firmare solo per te stesso.", {"tab": "advisory"})
                    return
                m = conn.execute("SELECT name FROM users WHERE id = ?", (member_id,)).fetchone()
                new_status = {"sign": "approvato", "contrario": "contrario"}.get(action, "modifica_richiesta")
                upsert(member_id, m["name"] if m else "", new_status, now if action == "sign" else "")
                became_unanime = recompute_advisory_unanime(conn, aid)
            if became_unanime and PRACTICE_STATUS_INDEX.get(practice["status"], 0) < PRACTICE_STATUS_INDEX["advisory_ricevuto"]:
                set_practice_status(conn, practice, "advisory_ricevuto", ctx["user_id"], "Parere Advisory unanime: delibera CdA sbloccata")
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Advisory - firma membro", action)
            conn.commit()
        msg = "Parere Advisory ora unanime: delibera CdA sbloccata." if became_unanime else "Registrato."
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, {"tab": "advisory"})

    def post_practice_condition(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "condizioni")
        if not practice:
            return
        with connect() as conn:
            if form.get("cond_id"):
                conn.execute(
                    "UPDATE pre_golive_conditions SET cond_status = ? WHERE id = ? AND practice_id = ?",
                    (form.get("cond_status", "aperta"), int(form["cond_id"]), practice_id),
                )
                msg = "Condizione aggiornata."
            else:
                conn.execute(
                    """INSERT INTO pre_golive_conditions(practice_id, description, source, owner, priority, due_date, cond_status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'aperta', ?)""",
                    (practice_id, form.get("description", ""), form.get("source", ""), form.get("owner", ""),
                     form.get("priority", "bloccante"), form.get("due_date", ""), now_iso()),
                )
                msg = "Condizione aggiunta."
            conn.execute("UPDATE practices SET updated_at = ? WHERE id = ?", (now_iso(), practice_id))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Condizione pre go-live", msg)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, {"tab": "condizioni"})

    def post_practice_transition(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "validazione")
        if not practice:
            return
        target = form.get("target", "")
        with connect() as conn:
            reason = can_transition_practice(conn, practice, target)
            if reason:
                self.redirect(f"/pariter/practices/{practice_id}", ctx, f"Transizione bloccata: {reason}", {"tab": "validazione"})
                return
            set_practice_status(conn, practice, target, ctx["user_id"], "Transizione manuale")
            conn.commit()
        label = practice_status_label(target)
        self.redirect(f"/pariter/practices/{practice_id}", ctx, f"Stato aggiornato: {label}.", {"tab": "validazione"})

    def post_practice_campaign_review(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "campagna")
        if not practice:
            return
        review_status = form.get("review_status", "in_revisione")
        no_yield = 1 if form.get("no_yield_promise") else 0
        notes = form.get("coherence_notes", "")
        with connect() as conn:
            existing = conn.execute(
                "SELECT * FROM campaign_page_reviews WHERE practice_id = ? ORDER BY id DESC LIMIT 1", (practice_id,)
            ).fetchone()
            review_data = {"coherence_notes": notes, "no_yield_promise": no_yield, "review_status": review_status}
            html_doc = build_campaign_review_html(practice, review_data)
            document_id = generated_document(
                conn, ctx["platform_id"], None, practice["proponent_id"],
                "Pagina campagna", "report", f"Revisione pagina campagna - {practice['project_title']}",
                "revisione_campagna.html", html_doc, ctx["user_id"],
            )
            link_document_practice(conn, document_id, practice_id)
            if existing:
                conn.execute(
                    "UPDATE campaign_page_reviews SET review_status = ?, coherence_notes = ?, no_yield_promise = ?, generated_document_id = ?, updated_by = ?, updated_at = ? WHERE id = ?",
                    (review_status, notes, no_yield, document_id, ctx["user_id"], now_iso(), existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO campaign_page_reviews(practice_id, review_status, coherence_notes, no_yield_promise, generated_document_id, updated_by, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (practice_id, review_status, notes, no_yield, document_id, ctx["user_id"], now_iso()),
                )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "practice", practice_id, "Revisione pagina campagna", review_status)
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, "Revisione pagina campagna salvata.", {"tab": "campagna"})

    def post_practice_alert(self, practice_id, form):
        ctx = self.ctx_from_form(form)
        practice = self._practice_guard(ctx, practice_id, "riepilogo")
        if not practice:
            return
        with connect() as conn:
            if form.get("resolve_id"):
                conn.execute(
                    "UPDATE practice_alerts SET alert_status = 'risolto' WHERE id = ? AND practice_id = ?",
                    (int(form["resolve_id"]), practice_id),
                )
                msg = "Alert risolto."
            else:
                conn.execute(
                    """INSERT INTO practice_alerts(practice_id, severity, source, message, alert_status, created_at)
                       VALUES (?, ?, 'manuale', ?, 'aperto', ?)""",
                    (practice_id, form.get("severity", "non_bloccante"), form.get("message", ""), now_iso()),
                )
                msg = "Alert aggiunto."
            conn.commit()
        self.redirect(f"/pariter/practices/{practice_id}", ctx, msg, {"tab": "riepilogo"})

    def page_deal_new(self):
        ctx = self.get_ctx()
        if not user_can(ctx["user"], "create_deal"):
            self.render("Nuovo deal", '<section class="panel"><p>Ruolo non abilitato alla creazione deal.</p></section>', "deals")
            return
        pid = ctx["platform_id"]
        proponents = rows("SELECT * FROM proponents WHERE platform_id = ? ORDER BY name", (pid,))
        tech = rows(
            "SELECT * FROM committee_members WHERE platform_id = ? AND committee = 'Comitato Tecnico' AND active = 1 ORDER BY name",
            (pid,),
        )
        covi = rows(
            "SELECT * FROM committee_members WHERE platform_id = ? AND committee = 'Advisory Committee' AND active = 1 ORDER BY name",
            (pid,),
        )
        body = f"""
<section class="panel narrow">
  <div class="section-head"><h2>Apri nuovo fascicolo deal</h2></div>
  <form class="form-grid" method="post" action="/deals/create">
    {hidden_ctx(ctx)}
    <label>Titolo deal<input name="title" required placeholder="Nome offerta"></label>
    <label>Proponente<select name="proponent_id" required>{option_rows(proponents, '')}</select></label>
    <label>Target raccolta<input name="funding_target" type="number" min="0" step="1000" value="500000"></label>
    <label>Relatore Comitato Tecnico<select name="technical_reviewer_id">{option_rows(tech, '')}</select></label>
    <label>Relatore Advisory Committee<select name="covi_reviewer_id">{option_rows(covi, '')}</select></label>
    <label>Contratto<select name="contract_required"><option value="1">Richiesto</option><option value="0">Non necessario</option></select></label>
    <div class="form-actions"><button class="button primary" type="submit">Crea deal</button></div>
  </form>
</section>
"""
        self.render("Nuovo deal", body, "deals")

    def page_deal_detail(self, deal_id):
        ctx = self.get_ctx()
        deal = fetch_deal(deal_id)
        if not deal:
            self.not_found()
            return
        requirements = rows("SELECT * FROM deal_requirements WHERE deal_id = ? ORDER BY kind, category, id", (deal_id,))
        verifications = rows("SELECT * FROM verifications WHERE deal_id = ? ORDER BY id", (deal_id,))
        docs = rows("SELECT * FROM documents WHERE deal_id = ? ORDER BY created_at DESC", (deal_id,))
        opinions = rows(
            """
            SELECT o.*, m.name AS reviewer_name, doc.title AS document_title
            FROM committee_opinions o
            LEFT JOIN committee_members m ON m.id = o.reviewer_member_id
            LEFT JOIN documents doc ON doc.id = o.generated_document_id
            WHERE o.deal_id = ?
            ORDER BY o.created_at DESC
            """,
            (deal_id,),
        )
        decisions = rows("SELECT * FROM board_decisions WHERE deal_id = ? ORDER BY created_at DESC", (deal_id,))
        audit = rows(
            """
            SELECT a.*, u.name AS actor_name
            FROM audit_log a
            LEFT JOIN users u ON u.id = a.actor_id
            WHERE a.entity_type = 'deal' AND a.entity_id = ?
            ORDER BY a.created_at DESC
            LIMIT 12
            """,
            (deal_id,),
        )
        body = f"""
<section class="deal-header">
  <div>
    <p class="eyebrow">{esc(deal['proponent_name'])} - {money(deal['funding_target'])}</p>
    <h2>{esc(deal['title'])}</h2>
  </div>
  <div class="header-badges">
    <span class="badge {badge_class(status_for_phase(deal['phase']))}">{esc(status_for_phase(deal['phase']))}</span>
    <span class="badge neutral">{esc(phase_label(deal['phase']))}</span>
  </div>
</section>
{self.phase_stepper(deal['phase'])}
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Responsabilita</h2></div>
    <dl class="definition-list">
      <dt>Relatore Comitato Tecnico</dt><dd>{esc(deal['technical_reviewer_name'] or '-')}</dd>
      <dt>Relatore Advisory Committee</dt><dd>{esc(deal['covi_reviewer_name'] or '-')}</dd>
      <dt>KIIS</dt><dd>{esc(deal['kiis_state'])}</dd>
      <dt>Contratto</dt><dd>{'Richiesto' if deal['contract_required'] else 'Non necessario'}</dd>
    </dl>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Azioni fase</h2></div>
    {self.phase_actions(ctx, deal, requirements, verifications, docs)}
  </div>
</section>
<section class="panel">
  <div class="section-head"><h2>Documentazione richiesta</h2></div>
  {self.requirements_table(ctx, deal, requirements)}
</section>
<section class="panel">
  <div class="section-head"><h2>Verifiche art. 5 e controlli</h2></div>
  {self.verifications_table(ctx, deal, verifications)}
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Pareri comitati</h2></div>
    {self.opinions_panel(ctx, deal, opinions)}
  </div>
  <div class="panel">
    <div class="section-head"><h2>CdA</h2></div>
    {self.board_panel(ctx, deal, decisions)}
  </div>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Fascicolo documenti</h2></div>
    {self.documents_for_deal(ctx, deal, docs)}
  </div>
  <div class="panel">
    <div class="section-head"><h2>Audit trail</h2></div>
    {self.audit_table(audit)}
  </div>
</section>
"""
        self.render(deal["title"], body, "deals")

    def phase_stepper(self, current):
        keys = [
            "appena_caricato",
            "istruttoria_documentazione",
            "verifiche",
            "comitato_tecnico",
            "covi",
            "cda",
            "integrazione_documenti",
            "contratto",
            "pre_pubblicazione",
            "pubblicato",
        ]
        compact_labels = {
            "appena_caricato": "Caricato",
            "istruttoria_documentazione": "Istruttoria doc.",
            "comitato_tecnico": "Comitato Tec.",
            "integrazione_documenti": "Integrazione doc.",
            "pre_pubblicazione": "Pre-pubbl.",
        }
        cur = PHASE_INDEX.get(current, 0)
        chunks = []
        for key in keys:
            if key == "integrazione_documenti" and current != key:
                css = "optional"
            elif PHASE_INDEX[key] < cur:
                css = "done"
            elif key == current:
                css = "current"
            else:
                css = "future"
            chunks.append(f'<li class="{css}"><span></span>{esc(compact_labels.get(key, phase_label(key)))}</li>')
        return f'<ol class="stepper">{"".join(chunks)}</ol>'

    def requirements_table(self, ctx, deal, requirements):
        can = user_can(ctx["user"], "edit_requirement")
        rows_html = []
        for req in requirements:
            status = "Completato" if req["completed"] else "Aperto"
            action = ""
            if can:
                button = "Riapri" if req["completed"] else "Completa"
                next_value = "0" if req["completed"] else "1"
                action = f"""
                <form method="post" action="/deals/{deal['id']}/requirement" class="inline-form">
                  {hidden_ctx(ctx)}
                  <input type="hidden" name="requirement_id" value="{req['id']}">
                  <button class="button ghost" name="completed" value="{next_value}" type="submit">{button}</button>
                </form>"""
            rows_html.append(
                f"""<tr>
                  <td>{esc(req['kind'])}</td>
                  <td>{esc(req['category'])}</td>
                  <td>{esc(req['label'])}</td>
                  <td><span class="badge {badge_class(status)}">{status}</span></td>
                  <td>{action}</td>
                </tr>"""
            )
        return f"""<table class="data-table">
        <thead><tr><th>Tipo</th><th>Categoria</th><th>Elemento</th><th>Stato</th><th></th></tr></thead>
        <tbody>{''.join(rows_html)}</tbody></table>"""

    def verifications_table(self, ctx, deal, verifications):
        can = user_can(ctx["user"], "verify")
        rows_html = []
        for ver in verifications:
            action = ""
            if can and PHASE_INDEX.get(deal["phase"], 0) >= PHASE_INDEX["verifiche"]:
                action = f"""
                <form method="post" action="/deals/{deal['id']}/verification" class="verification-form">
                  {hidden_ctx(ctx)}
                  <input type="hidden" name="verification_id" value="{ver['id']}">
                  <select name="status">
                    <option value="pending" {'selected' if ver['status'] == 'pending' else ''}>In attesa</option>
                    <option value="ok" {'selected' if ver['status'] == 'ok' else ''}>OK</option>
                    <option value="issue" {'selected' if ver['status'] == 'issue' else ''}>Criticita</option>
                  </select>
                  <input name="result" value="{esc(ver['result'])}" placeholder="Esito sintetico">
                  <button class="button ghost" type="submit">Salva</button>
                </form>"""
            rows_html.append(
                f"""<tr>
                  <td>{esc(ver['area'])}</td>
                  <td><span class="badge {badge_class(ver['status'])}">{esc(ver['status'])}</span></td>
                  <td>{esc(ver['result'] or '-')}</td>
                  <td>{action}</td>
                </tr>"""
            )
        return f"""<table class="data-table">
        <thead><tr><th>Area</th><th>Stato</th><th>Esito</th><th>Azione</th></tr></thead>
        <tbody>{''.join(rows_html)}</tbody></table>"""

    def opinions_panel(self, ctx, deal, opinions):
        existing = "".join(
            f"""<tr>
              <td>{esc(op['committee'])}</td><td>{esc(op['reviewer_name'] or '-')}</td>
              <td><span class="badge {badge_class(op['outcome'])}">{esc(op['outcome'])}</span></td>
              <td><a href="{rel_url('/documents/' + str(op['generated_document_id']) + '/download', ctx)}">{esc(op['document_title'] or 'Documento')}</a></td>
            </tr>"""
            for op in opinions
        )
        table = (
            f"""<table class="data-table compact"><thead><tr><th>Comitato</th><th>Relatore</th><th>Esito</th><th>Documento</th></tr></thead><tbody>{existing}</tbody></table>"""
            if existing
            else '<p class="muted">Nessun parere generato.</p>'
        )
        form = ""
        if deal["phase"] == "comitato_tecnico":
            form = self.opinion_form(ctx, deal, "Comitato Tecnico", "technical_opinion", "Genera parere e invia ad Advisory Committee")
        elif deal["phase"] == "covi":
            form = self.opinion_form(ctx, deal, "Advisory Committee", "covi_opinion", "Genera parere e invia al CdA")
        return table + form

    def opinion_form(self, ctx, deal, committee, permission, button_label):
        if not user_can(ctx["user"], permission):
            return f'<p class="muted">In attesa del ruolo: {esc(ROLE_LABELS.get(permission, committee))}.</p>'
        members = rows(
            "SELECT * FROM committee_members WHERE platform_id = ? AND committee = ? AND active = 1 ORDER BY name",
            (deal["platform_id"], committee),
        )
        selected = deal["technical_reviewer_id"] if committee == "Comitato Tecnico" else deal["covi_reviewer_id"]
        return f"""
        <form class="stacked-form bordered" method="post" action="/deals/{deal['id']}/opinion">
          {hidden_ctx(ctx)}
          <input type="hidden" name="committee" value="{esc(committee)}">
          <label>Relatore<select name="reviewer_member_id">{option_rows(members, selected)}</select></label>
          <label>Esito<select name="outcome"><option>Favorevole</option><option>Favorevole con condizioni</option><option>Non favorevole</option></select></label>
          <label>Valutazione<textarea name="summary" rows="5" required placeholder="Valutazione del progetto, presidi e condizioni"></textarea></label>
          <button class="button primary" type="submit">{esc(button_label)}</button>
        </form>"""

    def board_panel(self, ctx, deal, decisions):
        decision_rows = "".join(
            f"""<tr>
              <td>{esc(nice_date(d['created_at']))}</td>
              <td><span class="badge {badge_class(d['outcome'])}">{esc(d['outcome'])}</span></td>
              <td>{'Si' if d['integration_required'] else 'No'}</td>
              <td><a href="{rel_url('/documents/' + str(d['generated_document_id']) + '/download', ctx)}">Delibera</a></td>
            </tr>"""
            for d in decisions
        )
        table = (
            f"""<table class="data-table compact"><thead><tr><th>Data</th><th>Esito</th><th>Integrazione</th><th>Documento</th></tr></thead><tbody>{decision_rows}</tbody></table>"""
            if decision_rows
            else '<p class="muted">Nessuna delibera registrata.</p>'
        )
        form = ""
        if deal["phase"] == "cda":
            if user_can(ctx["user"], "board_decision"):
                form = f"""
                <form class="stacked-form bordered" method="post" action="/deals/{deal['id']}/board-decision">
                  {hidden_ctx(ctx)}
                  <label>Esito
                    <select name="outcome">
                      <option value="Approvato">Approvato</option>
                      <option value="Approvato con integrazioni">Approvato con integrazioni</option>
                      <option value="Non approvato">Non approvato</option>
                    </select>
                  </label>
                  <label>Note delibera<textarea name="notes" rows="5" required></textarea></label>
                  <button class="button primary" type="submit">Registra delibera</button>
                </form>"""
            else:
                form = '<p class="muted">Delibera riservata al ruolo CdA.</p>'
        return table + form

    def documents_for_deal(self, ctx, deal, docs):
        rows_html = "".join(
            f"""<tr>
              <td>{esc(d['category'])}</td>
              <td><a href="{rel_url('/documents/' + str(d['id']) + '/download', ctx)}">{esc(d['title'])}</a></td>
              <td>{'Generato' if d['generated'] else 'Caricato'}</td>
              <td>{esc(nice_date(d['created_at']))}</td>
            </tr>"""
            for d in docs
        )
        upload = ""
        if user_can(ctx["user"], "upload_document"):
            upload = f"""
            <form class="upload-form" method="post" action="/deals/{deal['id']}/upload" enctype="multipart/form-data">
              {hidden_ctx(ctx)}
              <label>Titolo<input name="title" required></label>
              <label>Categoria<select name="category"><option>Documentazione</option><option>KYC</option><option>KIIS</option><option>Contratto</option><option>Atto societario</option><option>Altro</option></select></label>
              <label>File<input name="file" type="file" required></label>
              <button class="button ghost" type="submit">Carica</button>
            </form>"""
        report = f"""
        <form method="post" action="/deals/{deal['id']}/generate-report" class="inline-form top-gap">
          {hidden_ctx(ctx)}
          <button class="button secondary" type="submit">Genera report iter</button>
        </form>"""
        return f"""<table class="data-table compact"><thead><tr><th>Categoria</th><th>Titolo</th><th>Origine</th><th>Data</th></tr></thead><tbody>{rows_html}</tbody></table>{upload}{report}"""

    def audit_table(self, audit):
        rows_html = "".join(
            f"""<tr><td>{esc(nice_date(a['created_at']))}</td><td>{esc(a['actor_name'] or '-')}</td><td>{esc(a['action'])}</td><td>{esc(a['details'])}</td></tr>"""
            for a in audit
        )
        return f"""<table class="data-table compact"><thead><tr><th>Quando</th><th>Chi</th><th>Azione</th><th>Dettagli</th></tr></thead><tbody>{rows_html}</tbody></table>"""

    def phase_actions(self, ctx, deal, requirements, verifications, docs):
        phase = deal["phase"]
        if phase in {"comitato_tecnico", "covi", "cda"}:
            return '<p class="muted">La fase avanza con il documento prodotto dal ruolo competente.</p>'
        if phase == "respinta":
            return '<p class="muted">Deal respinto dal CdA.</p>'
        if phase == "archiviato":
            return '<p class="muted">Deal chiuso.</p>'
        actions = {
            "appena_caricato": ("istruttoria_documentazione", "Apri istruttoria documentale"),
            "istruttoria_documentazione": ("verifiche", "Passa a verifiche"),
            "verifiche": ("comitato_tecnico", "Invia a Comitato Tecnico"),
            "integrazione_documenti": ("contratto" if deal["contract_required"] else "pre_pubblicazione", "Chiudi integrazioni"),
            "contratto": ("pre_pubblicazione", "Vai a pre-pubblicazione"),
            "pre_pubblicazione": ("pubblicato", "Pubblica offerta"),
        }
        if phase not in actions:
            return '<p class="muted">Nessuna azione manuale disponibile.</p>'
        if not user_can(ctx["user"], "finalize") and phase not in {"appena_caricato", "istruttoria_documentazione", "verifiche"}:
            return '<p class="muted">Ruolo non abilitato all avanzamento manuale.</p>'
        target, label = actions[phase]
        return f"""
        <form method="post" action="/deals/{deal['id']}/transition" class="stacked-form">
          {hidden_ctx(ctx)}
          <input type="hidden" name="target_phase" value="{target}">
          <button class="button primary" type="submit">{esc(label)}</button>
        </form>
        <a class="button secondary" href="{rel_url('/deals/' + str(deal['id']) + '/report', ctx)}">Vista report</a>
        """

    def page_deal_report(self, deal_id):
        ctx = self.get_ctx()
        report_html = build_iter_report(deal_id)
        body = f"""
<section class="panel">
  <div class="section-head">
    <h2>Report iter</h2>
    <form method="post" action="/deals/{deal_id}/generate-report">
      {hidden_ctx(ctx)}
      <button class="button primary" type="submit">Salva nel fascicolo</button>
    </form>
  </div>
  <iframe class="report-frame" srcdoc="{esc(report_html)}"></iframe>
</section>
"""
        self.render("Report iter", body, "deals")

    def page_compagine(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        params = parse_qs(urlparse(self.path).query)
        active_tab = (params.get("tab") or ["organigramma"])[0]
        if active_tab not in {"organigramma", "anagrafiche"}:
            active_tab = "organigramma"
        selected_person = (params.get("person") or [""])[0]
        selected_role = (params.get("role") or [""])[0]
        selected_shareholder_id = int((params.get("shareholder") or ["0"])[0] or 0)
        members = rows("SELECT * FROM committee_members WHERE platform_id = ? ORDER BY committee, name", (pid,))
        shareholders = rows("SELECT * FROM shareholders WHERE platform_id = ? ORDER BY stake_percent DESC", (pid,))
        selected_shareholder = row(
            "SELECT * FROM shareholders WHERE id = ? AND platform_id = ?",
            (selected_shareholder_id, pid),
        ) if selected_shareholder_id else None
        agreements = rows("SELECT * FROM person_agreements WHERE platform_id = ? ORDER BY person_name, agreement_type", (pid,))
        docs = rows(
            """
            SELECT * FROM documents
            WHERE platform_id = ?
              AND (
                origin = 'Compagine'
                OR (origin IN ('Archivio', 'Autorita') AND category IN (
                  'Statuto', 'Domanda autorizzazione ECSP', 'Comunicazione CONSOB',
                  'Comunicazione Banca d''Italia', 'Visura camerale', 'Patti parasociali',
                  'Verbale CdA', 'Delibera CdA', 'Bilancio', 'Situazione contabile',
                  'Prospetto requisiti prudenziali', 'Polizza assicurativa',
                  'Data processing agreement', 'Business plan', 'Outsourcing'
                ))
              )
            ORDER BY created_at DESC
            """,
            (pid,),
        )
        balance_docs = [d for d in docs if d["category"] in {"Bilancio", "Situazione contabile", "Relazione revisore", "Prospetto requisiti prudenziali", "Polizza assicurativa"}]
        authorization_keywords = [
            "domanda autorizzazione",
            "autorizzazione ecsp",
            "programma attivita",
            "programma di attivita",
            "allegato autorizzazione",
            "governance autorizzazione",
            "controlli interni",
            "requisiti esponenti",
            "partecipazioni qualificate",
            "organigramma",
            "outsourcing autorizzazione",
            "ricevuta autorita",
            "consob",
            "banca d'italia",
        ]
        authorization_docs = [
            d for d in docs
            if d["category"] in {"Domanda autorizzazione ECSP", "Allegato autorizzazione", "Programma attivita", "Ricevuta autorita", "Governance autorizzazione"}
            or any(keyword in f"{d['title']} {d['category']}".lower() for keyword in authorization_keywords)
        ]
        authorization_doc_ids = {d["id"] for d in authorization_docs}
        balance_doc_ids = {d["id"] for d in balance_docs}
        corporate_docs = [
            d for d in docs
            if d["id"] not in authorization_doc_ids
            and d["id"] not in balance_doc_ids
            and d["category"] not in {"Bilancio", "Situazione contabile", "Relazione revisore", "Prospetto requisiti prudenziali", "Polizza assicurativa"}
        ]
        suppliers = rows("SELECT * FROM suppliers WHERE platform_id = ? ORDER BY name", (pid,))
        supplier_contracts = rows(
            """
            SELECT sc.*, s.name AS supplier_name, s.service_area, doc.title AS doc_title
            FROM supplier_contracts sc
            JOIN suppliers s ON s.id = sc.supplier_id
            LEFT JOIN documents doc ON doc.id = sc.document_id
            WHERE sc.platform_id = ?
            ORDER BY sc.end_date, s.name
            """,
            (pid,),
        )
        person_docs = rows(
            """
            SELECT pd.*, doc.id AS doc_id, doc.title AS doc_title
            FROM person_documents pd
            LEFT JOIN documents doc ON doc.id = pd.document_id
            WHERE pd.platform_id = ? AND pd.person_name = ?
            ORDER BY pd.created_at DESC
            """,
            (pid, selected_person),
        ) if selected_person else []
        shareholder_docs = rows(
            """
            SELECT sd.*, doc.id AS doc_id, doc.title AS doc_title
            FROM shareholder_documents sd
            LEFT JOIN documents doc ON doc.id = sd.document_id
            WHERE sd.platform_id = ? AND sd.shareholder_id = ?
            ORDER BY sd.created_at DESC
            """,
            (pid, selected_shareholder_id),
        ) if selected_shareholder else []
        custom_functions = rows(
            """
            SELECT * FROM org_functions
            WHERE platform_id = ? AND active = 1
            ORDER BY area, function_name
            """,
            (pid,),
        )
        org_assignments = rows(
            """
            SELECT oa.*, doc.title AS doc_title
            FROM org_assignments oa
            LEFT JOIN documents doc ON doc.id = oa.document_id
            WHERE oa.platform_id = ?
            ORDER BY oa.function_name, oa.subject_name
            """,
            (pid,),
        )
        active_assignments_by_function = {}
        archived_assignments_by_function = {}
        assignment_by_pair = {}
        assignment_status_by_pair = {}
        subject_type_by_name = {}
        for assignment in org_assignments:
            pair = (assignment["subject_name"], assignment["function_name"])
            assignment_status_by_pair[pair] = assignment["status"]
            subject_type_by_name[assignment["subject_name"]] = assignment["subject_type"]
            if assignment["status"] == "Attivo":
                active_assignments_by_function.setdefault(assignment["function_name"], []).append(assignment)
                assignment_by_pair[pair] = assignment
            elif assignment["status"] in {"Archiviato", "Rimosso"}:
                archived_assignments_by_function.setdefault(assignment["function_name"], []).append(assignment)
        removed_assignment_pairs = {
            pair for pair, status in assignment_status_by_pair.items()
            if status in {"Archiviato", "Rimosso"}
        }
        groups = {}
        for member in members:
            groups.setdefault(member["committee"], []).append(member)
        compliance_user = row("SELECT * FROM users WHERE role = 'compliance' ORDER BY id LIMIT 1")
        legal_user = row("SELECT * FROM users WHERE role = 'legal' ORDER BY id LIMIT 1")
        is_isi = ctx["platform"]["code"] == "ISI"
        isi_function_owners = {
            "Legale rappresentante": "Antonio Ottaiano",
            "Revisore dei conti": "Luca Porta",
            "Gestione reclami e default": "Sebastian Caputo",
            "Conflitti di interesse": "Renato d'Alessandro",
            "Continuita operativa": "Antonio Ottaiano",
            "Prevenzione frodi": "Renato d'Alessandro",
            "Whistleblowing": "Emma Venturelli",
            "Privacy e archiviazione": "Renato d'Alessandro",
            "Contabilita": "Sebastian Caputo",
            "Compliance interna": "Renato d'Alessandro",
            "Antiriciclaggio / antiterrorismo": "Renato d'Alessandro",
            "Risk control": "Renato d'Alessandro",
            "Questionario appropriatezza": "Renato d'Alessandro",
            "Responsabile outsourcing": "Michele Mattera",
            "Presidio demo architettura": "Mario Rossi",
            "Sviluppatore software": "Daniele Santandrea",
            "Customer service": "Giuseppina Scatozza",
            "Marketing": "Tecnotravel Group S.r.l.S.",
        }

        def person_url(name, role_label):
            return rel_url("/compagine", ctx, {"person": name, "role": role_label})

        def shareholder_url(shareholder_id):
            return rel_url("/compagine", ctx, {"shareholder": shareholder_id})

        def anagraphic_person_url(name):
            return rel_url("/compagine", ctx, {"tab": "anagrafiche", "person": name, "role": "Anagrafica organigramma"})

        def person_link(name, role_label):
            return f'<a href="{person_url(name, role_label)}">{esc(name)}</a>'

        def names_for(group_name):
            names = [m["name"] for m in groups.get(group_name, []) if m["active"]]
            return ", ".join(names)

        # Titolari reali delle funzioni Pariter (dai documenti d'archivio). Le funzioni
        # non note restano vuote ("da censire"): niente piu' nomi demo come fallback.
        # Funzionigramma aggiornato (CdA - documento completo, giugno 2026): chi presidia
        # oggi ogni funzione. Le caselle scoperte restano vuote ("da censire/designare").
        pariter_function_owners = {
            "Legale rappresentante": "Gaetano De Vito",
            "Revisore dei conti": "Fabio Gallassi (proposto)",
            "Continuita operativa": "Gaetano De Vito",
            "Conflitti di interesse": "Stefania Monotoni",
            "Contabilita": "AstraLex STA",
            "Gestione reclami e default": "",        # da designare (un consigliere)
            "Privacy e archiviazione": "",           # responsabile privacy da designare
            "Compliance interna": "Stefania Monotoni",
            "Antiriciclaggio / antiterrorismo": "Stefania Monotoni",
            "Risk control": "Stefania Monotoni",
            "Responsabile outsourcing": "Stefania Monotoni",
            "Questionario appropriatezza": "",
        }

        def owner_for(label, fallback=""):
            if is_isi:
                return isi_function_owners.get(label, fallback)
            return pariter_function_owners.get(label, "")

        def org_node(label, people):
            if isinstance(people, str):
                people = [name.strip() for name in people.split(",") if name.strip()]
            people = [p for p in people if p]
            css = "org-node" if people else "org-node empty"
            shown = ", ".join(person_link(p, label) for p in people) if people else "da assegnare"
            return f'<div class="{css}"><span>{esc(label)}</span><strong>{shown}</strong></div>'

        def node_controls(label):
            return f"""<div class="node-actions" aria-label="Azioni {esc(label)}">
              <button type="button" title="Aggiungi soggetto" data-open-assignment data-function="{esc(label)}">+</button>
            </div>"""

        person_registry = {}
        supplier_registry = {}
        function_catalog = []
        rendered_function_labels = set()

        def responsibility_node(label, owner, note="", kind="function", href=""):
            owner = owner or ""
            owners = [part.strip() for part in owner.split(",") if part.strip()]
            owners = [name for name in owners if (name, label) not in removed_assignment_pairs]
            for assignment in active_assignments_by_function.get(label, []):
                if assignment["subject_name"] not in owners:
                    owners.append(assignment["subject_name"])
            css = f"responsibility-node {kind}" + ("" if owners else " empty")
            rendered_function_labels.add(label)
            function_catalog.append({
                "label": label,
                "kind": kind,
                "note": note,
                "owners": owners,
                "href": href,
            })
            def owner_chip(name):
                if kind == "outsourcing":
                    supplier_registry.setdefault(name, {"name": name, "functions": [], "href": href})
                    supplier_registry[name]["functions"].append(label)
                else:
                    person_registry.setdefault(name, {"name": name, "type": subject_type_by_name.get(name, "Persona fisica"), "functions": [], "roles": set()})
                    person_registry[name]["functions"].append(label)
                    person_registry[name]["roles"].add(kind)
                linked_name = f'<a href="{href}">{esc(name)}</a>' if href and kind == "outsourcing" else person_link(name, label)
                return linked_name

            owner_html = ", ".join(owner_chip(name) for name in owners) if owners else '<span class="empty-owner">da assegnare</span>'
            source_link = f'<a class="node-source-link" href="{href}">archivio</a>' if href else ""
            archived = archived_assignments_by_function.get(label, [])
            storico_html = ""
            if archived:
                past = ", ".join(
                    esc(a["subject_name"]) + (f" (fino {nice_date(a['end_date'])})" if a["end_date"] else "")
                    for a in archived
                )
                storico_html = f'<small class="node-storico">Storico funzione: {past}</small>'
            return f"""<div class="{css}">
              <div class="node-topline"><span>{esc(label)}</span>{node_controls(label)}</div>
              <strong>{owner_html}</strong>
              <small>{esc(note)}</small>
              {storico_html}
              <div class="node-bottom-actions"><button type="button" title="Elimina blocco" data-delete-block data-function="{esc(label)}">-</button></div>
              {source_link}
            </div>"""

        def custom_nodes_for(area):
            return [
                responsibility_node(
                    item["function_name"],
                    "",
                    item["note"] or "Funzione censita manualmente: collegare soggetti, date e contratti.",
                    item["kind"] or self.org_kind_for_area(item["area"]),
                )
                for item in custom_functions
                if item["area"] == area and item["function_name"] not in rendered_function_labels
            ]

        def outsourcing_node(contract):
            href = rel_url("/documents", ctx, {"origin": "Contratto fornitore"})
            return f"""<a class="responsibility-node outsourcing" href="{href}">
              <span>{esc(contract['contract_type'])}</span>
              <strong>{esc(contract['supplier_name'])}</strong>
              <small>{esc(contract['service_area'] or contract['title'])} - {esc(contract['status'])}</small>
            </a>"""

        def supplier_slot(label, keywords, fallback_note, kind="outsourcing"):
            haystack_items = []
            for contract in supplier_contracts:
                haystack_items.append(
                    (
                        contract,
                        f"{contract['title']} {contract['contract_type']} {contract['supplier_name']} {contract['service_area']}".lower(),
                    )
                )
            for supplier in suppliers:
                haystack_items.append(
                    (
                        supplier,
                        f"{supplier['name']} {supplier['service_area']} {supplier['notes']}".lower(),
                    )
                )
            scored = [
                (sum(1 for keyword in keywords if keyword in hay), item)
                for item, hay in haystack_items
            ]
            score, match = max(scored, key=lambda scored_item: scored_item[0]) if scored else (0, None)
            if score == 0:
                match = None
            href = rel_url("/documents", ctx, {"origin": "Contratto fornitore"})
            if match:
                name = match["supplier_name"] if "supplier_name" in match.keys() else match["name"]
                detail = match["service_area"] or (match["title"] if "title" in match.keys() else "")
                status = match["status"] if "status" in match.keys() else ""
                return responsibility_node(label, name, f"{detail}{(' - ' + status) if status else ''}", kind, href)
            return responsibility_node(label, "", fallback_note, kind)

        def supplier_named_node(label, supplier_name, fallback_note, kind="outsourcing"):
            match = next((supplier for supplier in suppliers if supplier["name"] == supplier_name), None)
            if not match:
                return responsibility_node(label, supplier_name, fallback_note, kind)
            href = rel_url("/documents", ctx, {"origin": "Contratto fornitore"})
            return responsibility_node(label, match["name"], f"{match['service_area'] or fallback_note} - {match['status']}", kind, href)

        def responsibility_group(title, kicker, nodes):
            return f"""<section class="responsibility-group" data-group-name="{esc(title)}">
              <div class="responsibility-head">
                <div><h3>{esc(title)}</h3><span>{esc(kicker)}</span></div>
                <div class="group-actions"><button class="drag-handle" type="button" title="Sposta blocco" draggable="true">::</button><button type="button" title="Aggiungi blocco" data-open-block data-lock-group="true" data-group="{esc(title)}">+</button></div>
              </div>
              <div class="responsibility-list">{''.join(nodes)}</div>
            </section>"""

        def committee_column(name, caption):
            group = groups.get(name, [])
            if group:
                member_html = "".join(
                    f"""<li>
                      <strong>{person_link(m['name'], m['role'])}</strong>
                      <span>{esc(m['role'])}</span>
                      <small>{esc(m['email'])}</small>
                    </li>"""
                    for m in group
                )
            else:
                member_html = '<li class="empty-row">Nessun membro.</li>'
            return f"""<div class="committee-column">
              <p class="panel-kicker">{esc(caption)}</p>
              <ul class="committee-list">{member_html}</ul>
              <button class="button ghost" type="button">+ Membro</button>
            </div>"""

        shareholder_cards = "".join(
            f"""<div class="shareholder-row">
              <a class="shareholder-main" href="{shareholder_url(s['id'])}">
                <strong>{esc(s['name'])}</strong>
                <small>{esc(s['subject_type'])} - {esc(s['legal_form'] or 'forma da censire')} - {esc(s['notes'] or 'profilo da completare')}</small>
              </a>
              <form class="shareholder-meta shareholder-percent-form" method="post" action="/shareholders/{s['id']}/update">
                {hidden_ctx(ctx)}
                <input type="hidden" name="name" value="{esc(s['name'])}">
                <input type="hidden" name="subject_type" value="{esc(s['subject_type'])}">
                <input type="hidden" name="legal_form" value="{esc(s['legal_form'])}">
                <input type="hidden" name="tax_id" value="{esc(s['tax_id'])}">
                <input type="hidden" name="contact_email" value="{esc(s['contact_email'])}">
                <input type="hidden" name="phone" value="{esc(s['phone'])}">
                <input type="hidden" name="address" value="{esc(s['address'])}">
                <input type="hidden" name="beneficial_owners" value="{esc(s['beneficial_owners'])}">
                <input type="hidden" name="requisites_status" value="{esc(s['requisites_status'])}">
                <input type="hidden" name="status" value="{esc(s['status'])}">
                <input type="hidden" name="notes" value="{esc(s['notes'])}">
                <label><span>Quota %</span><input type="number" step="0.01" min="0" max="100" name="stake_percent" value="{s['stake_percent']:.2f}"></label>
                <span class="badge {badge_class(s['requisites_status'])}">{esc(s['requisites_status'])}</span>
                <button type="submit">Salva</button>
              </form>
            </div>"""
            for s in shareholders
        ) or '<p class="empty-state">Nessun partecipante qualificato.</p>'
        doc_cards = "".join(
            f"""<div class="document-row">
              <div><strong>{esc(d['title'])}</strong><span>{esc(d['category'])} - {esc(nice_date(d['created_at']))}</span></div>
              <a class="button ghost" href="{rel_url('/documents/' + str(d['id']) + '/download', ctx)}">Apri</a>
            </div>"""
            for d in corporate_docs
        ) or '<p class="empty-state">Nessun atto societario puro registrato. Domanda, allegati autorizzativi e bilanci sono organizzati nei fascicoli dedicati sotto.</p>'
        balance_cards = "".join(
            f"""<div class="document-row">
              <div><strong>{esc(d['title'])}</strong><span>{esc(d['category'])} - {esc(nice_date(d['created_at']))}</span></div>
              <a class="button ghost" href="{rel_url('/documents/' + str(d['id']) + '/download', ctx)}">Apri</a>
            </div>"""
            for d in balance_docs
        ) or '<p class="empty-state">Nessun bilancio o prospetto contabile caricato.</p>'

        # Bucketizzazione dei documenti d'archivio per cartella (coerente con
        # l'architettura dell'archivio): statuto, fascicolo autorizzazione +
        # aggiornamenti, scambi con l'Autorita, template/allegati, documenti interni.
        archive_docs = rows(
            "SELECT * FROM documents WHERE platform_id = ? AND storage_path LIKE '@archive/%' ORDER BY title",
            (pid,),
        )
        real_balance_cats = {"Bilancio", "Situazione contabile"}
        # Sottocartelle di 09_ARCHIVIO_STORICO che sono SCAMBI con la vigilanza
        # (lettere, riscontri CONSOB, vigilanza BdI) e non versioni depositate.
        exchange_keys = ("Lettere", "CONSOB", "Vigilanza", "Cambio_assetto")
        statuto_docs, auth_fascicolo_docs, auth_update_docs = [], [], []
        auth_history_docs, authority_docs = [], []
        template_docs, internal_docs, bilanci_docs = [], [], []
        legacy_docs, cda_docs = [], []
        cda_cats = ("Verbale CdA", "Delibera CdA", "Verbale assemblea soci")

        def _arel(d):
            sp = d["storage_path"] or ""
            return sp[len("@archive/"):] if sp.startswith("@archive/") else sp

        for d in archive_docs:
            r = _arel(d)
            cat = d["category"]
            seg = r.split("/")
            sub = seg[1] if len(seg) > 1 else ""
            if "12_LEGACY" in r:
                legacy_docs.append(d)            # vecchio regime 2019-2021
            elif cat == "Statuto":
                statuto_docs.append(d)
            elif cat in real_balance_cats:
                bilanci_docs.append(d)
            elif cat in cda_cats:
                cda_docs.append(d)               # verbali CdA / delibere / assemblee
            elif r.startswith("06_AUTORIZZAZIONE_VIGENTE/B_"):
                auth_update_docs.append(d)
            elif r.startswith("06_AUTORIZZAZIONE_VIGENTE/A_"):
                auth_fascicolo_docs.append(d)
            elif r.startswith("08_VAGLIO") or cat in ("Comunicazione CONSOB", "Comunicazione Banca d'Italia"):
                authority_docs.append(d)
            elif r.startswith("09_ARCHIVIO_STORICO"):
                if any(k in sub for k in exchange_keys):
                    authority_docs.append(d)
                else:
                    auth_history_docs.append(d)  # domande, integrazioni, depositi
            elif r.startswith("07_TEMPLATE"):
                template_docs.append(d)
            else:
                internal_docs.append(d)

        # Documenti caricati a mano dalla pagina (non in archivio): confluiscono nelle
        # sezioni giuste per categoria, cosi' i tasti "+ Aggiungi" funzionano.
        manual_docs = [d for d in docs if not (d["storage_path"] or "").startswith("@archive/")]
        for d in manual_docs:
            cat = d["category"]
            if cat in cda_cats:
                cda_docs.append(d)
            elif cat == "Aggiornamento autorizzazione":
                auth_update_docs.append(d)
            elif cat in ("Comunicazione CONSOB", "Comunicazione Banca d'Italia"):
                authority_docs.append(d)
            elif cat == "Documento interno":
                internal_docs.append(d)
            elif cat == "Allegato o template":
                template_docs.append(d)

        def _doc_card(d):
            dd = (d["doc_date"] or "") if "doc_date" in d.keys() else ""
            desc = ((d["description"] or "") if "description" in d.keys() else "") or archive_doc_description(d["filename"], _arel(d))
            desc_html = f'<small>{esc(desc)}</small>' if desc else ''
            badge = f'<span class="badge neutral">{esc(dd[:7])}</span>' if dd else ''
            return (f'<div class="document-row"><div><strong>{esc(d["title"])}</strong>'
                    f'<span class="doc-filename">{esc(d["filename"])}</span>{desc_html}</div>'
                    f'<div class="doc-row-actions">{badge}'
                    f'<a class="button ghost" href="{rel_url("/documents/" + str(d["id"]) + "/download", ctx)}">Apri</a></div></div>')

        def _paginated(cards, visible=5, noun="documenti"):
            """Mostra i primi `visible` elementi; il resto in un <details> richiamabile."""
            cards = list(cards)
            if len(cards) <= visible:
                return "".join(cards)
            head = "".join(cards[:visible])
            rest = "".join(cards[visible:])
            return (f'{head}<details class="more-docs"><summary>Mostra altri {len(cards) - visible} {noun}</summary>'
                    f'<div class="document-list compact-document-list">{rest}</div></details>')

        def card_list(doclist, empty, visible=5):
            cards = [_doc_card(d) for d in doclist]
            return _paginated(cards, visible) or f'<p class="empty-state">{esc(empty)}</p>'

        def _clean_seg(name):
            return re.sub(r"^[0-9A-Za-z]{1,3}_", "", name).replace("_", " ").strip()

        def _dossier_date(key):
            if "05_2024-12-22_Deposito" in key:
                return "2023-12-22"  # nome cartella con anno errato: in realta' dic 2023 (prima integrazione)
            m = re.search(r"\d{4}-\d{2}-\d{2}", key)
            if m:
                return m.group(0)
            m = re.search(r"\d{4}-\d{2}(?!\d)", key)
            if m:
                return m.group(0) + "-00"
            m = re.search(r"(?:19|20)\d{2}", key)
            if m:
                return m.group(0) + "-00-00"
            return "0000-00-00"

        def _dossier_cat(key, text):
            k = key.lower()
            # il nome del dossier ha priorita' sul contenuto dei file
            if "vigilanza" in k or "_bdi" in k or "banca_d" in k:
                return "bdi"
            if "vaglio" in k or "rilievi" in k:
                return "vaglio"
            if "avvio" in k or "cambio_assetto" in k:
                return "avvio"
            if "consob" in k or "lettere" in k:
                return "consob"
            t = text.lower()
            if "vigilanza" in t or "banca d" in t:
                return "bdi"
            return "consob"

        def dossier_blocks(doclist, empty, visible=5, filterable=False):
            groups, order = {}, []
            for d in doclist:
                parts = _arel(d).split("/")
                folder = parts[:-1]  # cartelle dossier, escluso il filename
                key = "/".join(folder[:2]) if folder else parts[0]
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(d)
            items = [(key, _clean_seg(key.split("/")[-1]), groups[key]) for key in order]
            items.sort(key=lambda it: (_dossier_date(it[0]), it[1]), reverse=True)  # dal piu' recente
            blocks = []
            for key, title, dd in items:
                date = _dossier_date(key)
                date_label = date[:7] if date != "0000-00-00" else ""
                kicker = f"{len(dd)} file" + (f" &middot; {date_label}" if date_label else "")
                inner = _paginated([_doc_card(d) for d in dd], visible, "file")
                attrs = ""
                if filterable:
                    text = (title + " " + " ".join(x["filename"] for x in dd)).lower()
                    attrs = f' data-cat="{_dossier_cat(key, text)}" data-text="{esc(text)}"'
                blocks.append(
                    f'<div class="dossier-block"{attrs}><div class="section-head compact-head">'
                    f'<h3>{esc(title)}</h3><span class="panel-kicker">{kicker}</span></div>'
                    f'<div class="document-list compact-document-list">{inner}</div></div>'
                )
            return "".join(blocks) or f'<p class="empty-state">{esc(empty)}</p>'

        def _statuto_info(d):
            r = _arel(d)
            low = r.lower()
            if "nuovo_statuto_2025" in low:
                return ("Vigente", "Statuto vigente (2025)",
                        "Statuto adottato nel 2025; supera l'Allegato 4 dell'autorizzazione.")
            if "verbale_aucap" in low:
                return ("Vigente", "Adozione nuovo statuto (2025)",
                        "Verbale di aumento di capitale e adozione del nuovo statuto (lug. 2025).")
            if low.startswith("06_autorizzazione_vigente/a_"):
                part = " - parte 4.1a" if "4_1a" in low else (" - parte 4.1b" if "4_1b" in low else "")
                return ("Autorizzato 2024", f"Statuto autorizzato 2024{part}",
                        "Statuto come depositato nel fascicolo autorizzato da CONSOB (giugno 2024).")
            seg = r.split("/")
            folder = seg[1] if len(seg) > 1 else ""
            m = re.search(r"(\d{4}-\d{2}-\d{2})", folder)
            when = f" ({m.group(1)})" if m else ""
            return ("Storico", f"Statuto - {_clean_seg(folder)}",
                    f"Versione dello statuto depositata in questo passaggio{when}.")

        def _statuto_card(d):
            tag, title, desc = _statuto_info(d)
            badge = {"Vigente": "ok", "Autorizzato 2024": "neutral", "Storico": "warning"}.get(tag, "neutral")
            return (f'<div class="document-row"><div>'
                    f'<strong>{esc(title)}</strong>'
                    f'<span class="doc-filename">{esc(d["filename"])}</span>'
                    f'<small>{esc(desc)}</small></div>'
                    f'<div class="doc-row-actions"><span class="badge {badge}">{esc(tag)}</span>'
                    f'<a class="button ghost" href="{rel_url("/documents/" + str(d["id"]) + "/download", ctx)}">Apri</a></div></div>')

        _statuto_rank = {"Vigente": 0, "Autorizzato 2024": 1, "Storico": 2}
        statuto_sorted = sorted(statuto_docs, key=lambda d: (_statuto_rank.get(_statuto_info(d)[0], 9), _arel(d)))
        statuto_current_cards = "".join(
            _statuto_card(d) for d in statuto_sorted if _statuto_info(d)[0] != "Storico"
        ) or '<p class="empty-state">Nessuno statuto vigente o autorizzato in archivio.</p>'
        statuto_history_cards = "".join(
            _statuto_card(d) for d in statuto_sorted if _statuto_info(d)[0] == "Storico"
        ) or '<p class="empty-state">Nessuna versione storica dello statuto.</p>'
        # Indice ordinato del fascicolo autorizzato: Domanda + Allegati 4->19 in
        # sequenza, con pallino presente/mancante. Cosi' un legale vede subito se e'
        # completo e quale e' la versione finale depositata.
        FASCICOLO_ORDER = [
            ("domanda", "Domanda di autorizzazione ECSP", "Istanza di autorizzazione ai servizi di crowdfunding."),
            ("4", "Allegato 4 - Statuto", "Statuto e atto costitutivo."),
            ("5_1", "Allegato 5.1 - Servizi e selezione", "Tipologie di servizi e procedura di selezione delle offerte."),
            ("5_2", "Allegato 5.2 - Piattaforma", "Descrizione della piattaforma."),
            ("5_3", "Allegato 5.3 - Marketing", "Strategia di marketing."),
            ("6_1", "Allegato 6.1 - Governance", "Governance e assetto organizzativo."),
            ("7", "Allegato 7 - Trattamento dati", "Trattamento dei dati personali (GDPR)."),
            ("8_1", "Allegato 8.1 - Rischi IT", "Rischi IT e presidi."),
            ("9_1", "Allegato 9.1 - Presidi prudenziali", "Presidi prudenziali (art. 11)."),
            ("9_4", "Allegato 9.4 - Piano triennale", "Piano triennale."),
            ("10_1", "Allegato 10.1 - Fondi propri", "Fondi propri."),
            ("10_2", "Allegato 10.2 - Fondi propri", "Fondi propri (integrazione)."),
            ("11", "Allegato 11 - Continuita operativa", "Continuita operativa."),
            ("12", "Allegato 12 - Onorabilita soci", "Onorabilita dei soci."),
            ("13", "Allegato 13 - Onorabilita gestori", "Onorabilita e competenze dei gestori."),
            ("14", "Allegato 14 - Conflitti di interesse", "Policy sui conflitti di interesse."),
            ("16", "Allegato 16 - Reclami", "Gestione dei reclami."),
            ("17", "Allegato 17 - Pagamenti", "Pagamenti e custodia (Banca Sella)."),
            ("18", "Allegato 18 - KIIS", "Scheda con le informazioni chiave sull'investimento."),
            ("19", "Allegato 19 - Limiti investitori", "Limiti di investimento e classificazione investitori."),
        ]
        _fasc_known_pairs = {"5_1", "5_2", "5_3", "6_1", "8_1", "9_1", "9_4", "10_1", "10_2"}

        def _fascicolo_key(d):
            low = d["filename"].lower()
            if "domanda_autorizz" in low:
                return "domanda"
            m = re.search(r"allegato[_ ]?(\d+)(?:[_ (]+(\d+))?", low)
            if not m:
                return None
            pair = f"{m.group(1)}_{m.group(2)}" if m.group(2) else ""
            return pair if pair in _fasc_known_pairs else m.group(1)

        # Lo statuto del fascicolo (Allegato 4) e' mostrato nella sezione Statuto: lo
        # includo qui solo per far risultare l'Allegato 4 presente nell'indice.
        _fasc_source = auth_fascicolo_docs + [
            d for d in statuto_docs if _arel(d).startswith("06_AUTORIZZAZIONE_VIGENTE/A_")
        ]
        _fasc_by_key = {}
        for d in _fasc_source:
            _fasc_by_key.setdefault(_fascicolo_key(d), []).append(d)
        _fasc_rows = []
        for key, label, desc in FASCICOLO_ORDER:
            items = _fasc_by_key.get(key, [])
            if items:
                links = "".join(
                    f'<a class="button tiny" href="{rel_url("/documents/" + str(x["id"]) + "/download", ctx)}">Apri</a>'
                    for x in items
                )
                dot, state = "ok", (f"{len(items)} file" if len(items) > 1 else "presente")
            else:
                links, dot, state = "", "missing", "non presente"
            _fasc_rows.append(
                f'<li class="fascicolo-row"><span class="deadline-dot {dot}"></span>'
                f'<div><strong>{esc(label)}</strong><small>{esc(desc)}</small></div>'
                f'<div class="fascicolo-state"><span class="muted">{esc(state)}</span>{links}</div></li>'
            )
        fascicolo_index = "".join(_fasc_rows)

        auth_update_cards = card_list(auth_update_docs, "Nessun aggiornamento vigente registrato.")
        auth_history_cards = dossier_blocks(auth_history_docs, "Nessuna versione storica depositata.")
        legacy_dossiers = dossier_blocks(legacy_docs, "Nessun documento del vecchio regime.")

        # Scambi con l'Autorita: UN solo elenco cronologico (niente blocchi), ogni riga
        # con categoria e testo per il filtro al volo (chip + ricerca).
        def _scambio_row(d):
            r = _arel(d)
            cat = _dossier_cat(r, (d["filename"] + " " + r).lower())
            manual_date = (d["doc_date"] or "") if "doc_date" in d.keys() else ""
            date = manual_date or _dossier_date(r)
            if not date or date == "0000-00-00":
                date_label = ""
            elif date[5:7] == "00":
                date_label = date[:4]          # solo anno, mese ignoto
            else:
                date_label = date[:7]          # anno-mese
            text = (d["title"] + " " + d["filename"]).lower()
            manual_desc = (d["description"] or "") if "description" in d.keys() else ""
            desc = manual_desc or archive_doc_description(d["filename"], r)
            desc_html = f'<small>{esc(desc)}</small>' if desc else ''
            badge = f'<span class="badge neutral">{esc(date_label)}</span>' if date_label else ''
            return (f'<div class="document-row" data-cat="{cat}" data-text="{esc(text)}">'
                    f'<div><strong>{esc(d["title"])}</strong>'
                    f'<span class="doc-filename">{esc(d["filename"])}</span>{desc_html}</div>'
                    f'<div class="doc-row-actions">{badge}'
                    f'<a class="button ghost" href="{rel_url("/documents/" + str(d["id"]) + "/download", ctx)}">Apri</a></div></div>')

        def _sort_date(d):
            md = (d["doc_date"] or "") if "doc_date" in d.keys() else ""
            return md or _dossier_date(_arel(d))
        scambi_sorted = sorted(authority_docs, key=lambda d: (_sort_date(d), _arel(d)), reverse=True)
        authority_list = "".join(_scambio_row(d) for d in scambi_sorted) or '<p class="empty-state">Nessuno scambio con l\'Autorita in archivio.</p>'
        cda_cards = card_list(sorted(cda_docs, key=lambda d: d["created_at"], reverse=True),
                              "Nessun verbale di CdA o assemblea in archivio. Usa \"+ Aggiungi\" per caricarne uno.")
        internal_cards = card_list(internal_docs, "Nessun documento interno in archivio.")
        template_cards = card_list(template_docs, "Nessun allegato o template in archivio.")
        bilanci_cards = card_list(bilanci_docs, "Nessun bilancio o situazione contabile in archivio.")

        # Categorie predeterminate per il form documenti dedicato di Compagine.
        compagine_doc_categories = [
            "Statuto", "Atto societario", "Patti parasociali",
            "Domanda autorizzazione ECSP", "Aggiornamento autorizzazione",
            "Comunicazione CONSOB", "Comunicazione Banca d'Italia",
            "Verbale CdA", "Delibera CdA", "Verbale assemblea soci",
            "Bilancio", "Situazione contabile", "Visura camerale",
            "Contratto fornitore", "Polizza assicurativa",
            "Documento interno", "Allegato o template", "Documentazione",
        ]
        compagine_doc_category_options = "".join(
            f'<option value="{esc(c)}">{esc(c)}</option>' for c in compagine_doc_categories
        )

        # Documento "Storico e stato attuale" richiamabile sotto il banner autorizzazione.
        storico_doc = next((d for d in internal_docs if "storico_e_stato" in d["filename"].lower()), None)
        storico_link = (
            f'<a class="button ghost tiny" href="{rel_url("/documents/" + str(storico_doc["id"]) + "/download", ctx)}">Apri lo Storico e stato attuale</a>'
            if storico_doc else ""
        )

        auth_requirements = [
            ("Domanda autorizzazione ECSP", "Template/domanda firmata e versione inviata"),
            ("Programma attivita", "Servizi ECSP, modello operativo, mercati e canali"),
            ("Governance e controlli", "Organigramma, CdA, compliance, risk, AML, reclami"),
            ("Requisiti esponenti e soci", "Fit & proper, partecipazioni qualificate, deleghe"),
            ("Outsourcing e ICT", "Contratti critici, SLA, cloud, exit plan"),
            ("Ricevute e scambi autorita", "PEC, protocolli, richieste integrazione, esiti"),
        ]
        auth_doc_cards = "".join(
            f"""<div class="document-row auth-document-row">
              <div><strong>{esc(d['title'])}</strong><span>{esc(d['category'])} - {esc(nice_date(d['created_at']))}</span></div>
              <a class="button ghost" href="{rel_url('/documents/' + str(d['id']) + '/download', ctx)}">Apri</a>
            </div>"""
            for d in authorization_docs
        ) or '<p class="empty-state">Nessuna domanda o allegato autorizzativo ancora caricato.</p>'
        auth_check_rows = "".join(
            f"""<li class="auth-check {'found' if any(req.lower().split()[0] in f"{d['title']} {d['category']}".lower() for d in authorization_docs) else 'missing'}">
              <span></span>
              <div><strong>{esc(req)}</strong><small>{esc(note)}</small></div>
            </li>"""
            for req, note in auth_requirements
        )
        context_links = [
            (
                "Domanda e allegati autorizzativi",
                f"{len(authorization_docs)} documenti",
                "Domanda, programma attivita, allegati, ricevute e scambi con Autorita.",
                rel_url("/documents", ctx, {"origin": "Compagine", "category": "Domanda autorizzazione ECSP"}),
            ),
            (
                "Organigramma e funzioni",
                f"{len(members)} persone / funzioni",
                "CdA, legale rappresentante, compliance, risk, AML, reclami e comitati.",
                rel_url("/compagine", ctx),
            ),
            (
                "Partecipazioni e accordi persona",
                f"{len(shareholders)} soci qualificati / {len(agreements)} accordi",
                "Partecipogramma, patti, incarichi, deleghe, NDA e requisiti dei titolari.",
                rel_url("/documents", ctx, {"origin": "Persona"}),
            ),
            (
                "Fornitori e contratti critici",
                f"{len(supplier_contracts)} contratti",
                "Outsourcing, ICT, KYC/AML, conservazione, SLA, DPA ed exit.",
                rel_url("/documents", ctx, {"origin": "Contratto fornitore"}),
            ),
            (
                "Bilanci e requisiti prudenziali",
                f"{len(balance_docs)} documenti",
                "Bilanci, situazione contabile, polizze e prospetti per CF1/comunicazioni.",
                rel_url("/documents", ctx, {"origin": "Compagine", "category": "Bilancio"}),
            ),
        ]
        context_matrix = "".join(
            f"""<a class="context-link-card" href="{href}">
              <span>{esc(title)}</span>
              <strong>{esc(count)}</strong>
              <small>{esc(description)}</small>
            </a>"""
            for title, count, description, href in context_links
        )
        cda_names = [m["name"] for m in groups.get("CdA", []) if m["active"]]
        technical_members = [m for m in groups.get("Comitato Tecnico", []) if m["active"]]
        technical_nodes = [
            responsibility_node(
                "Membri Comitato Tecnico",
                ", ".join(m["name"] for m in technical_members),
                "Valutazione offerte e pareri tecnici",
                "committee",
            )
        ] if technical_members else [responsibility_node("Comitato tecnico progetti", "", "Membri da censire", "committee")]
        advisory_members = [m for m in groups.get("Advisory Committee", []) if m["active"]]
        advisory_nodes = [
            responsibility_node(
                "Membri Advisory Committee",
                ", ".join(m["name"] for m in advisory_members),
                "Approfondimento successivo alla valutazione tecnica",
                "advisory",
            )
        ] if advisory_members else [responsibility_node("Advisory Committee", "", "Non presente su ISI Crowd" if is_isi else "Membri da censire", "advisory")]
        if is_isi:
            outsourcing_nodes = [
                supplier_named_node("Fornitore servizi cloud", "Keliweb S.r.l.", "Cloud / hosting / infrastruttura"),
                supplier_named_node("Merito creditizio", "Creditsafe Italia S.r.l.", "Provider merito creditizio"),
                supplier_named_node("Istituto di pagamento", "Lemonway Sas", "Payment institution / PSP"),
                supplier_named_node("Contabilita", "012 Factory S.r.l.", "Fornitore contabilita"),
                supplier_named_node("Compliance esterna", "Avvocati.net", "Advisor compliance esterno"),
                responsibility_node("Assicurazione / polizza", "", "Polizza professionale e coperture operative da censire", "outsourcing"),
                responsibility_node("Firma e conservazione", "", "Da censire se distinto dai sistemi documentali", "outsourcing"),
                responsibility_node("KYC / AML data provider", "", "Da censire se distinto da provider dati e AML", "outsourcing"),
            ]
            operational_nodes = [
                responsibility_node("Sviluppatore software", owner_for("Sviluppatore software"), "Sviluppo e manutenzione piattaforma", "operational"),
                responsibility_node("Customer service", owner_for("Customer service"), "Assistenza utenti", "operational"),
                responsibility_node("Marketing", owner_for("Marketing"), "Comunicazioni commerciali e campagne", "operational"),
            ]
        else:
            outsourcing_nodes = [
                supplier_slot("Fornitore servizi cloud", ["aws", "amazon web", "isi cloud", "gestione infrastruttura", "cloud operations", "infrastruttura piattaforma", "servizi cloud", "infrastruttura", "hosting", "cloud"], "Cloud / hosting / infrastruttura da censire"),
                supplier_slot("Istituto di pagamento", ["pagamento", "payment", "lemonway", "istituto", "sella"], "Payment institution / PSP da censire"),
                supplier_slot("Contabilita", ["contabil", "bilancio", "amministrazione"], "Fornitore contabilita da censire"),
                supplier_slot("Compliance esterna", ["compliance", "legale", "avvocati", "consulenza"], "Advisor compliance/legale da censire"),
                supplier_slot("Assicurazione / polizza", ["assicurazione", "polizza", "copertura", "professionale"], "Polizza professionale e coperture operative da censire"),
                supplier_slot("Firma e conservazione", ["firma", "conservazione", "documentale", "archiviazione"], "Firma, conservazione e archivio documentale da censire"),
                supplier_slot("KYC / AML data provider", ["kyc", "aml", "verifiche", "data provider"], "Provider KYC/AML da censire"),
            ]
            operational_nodes = [
                supplier_slot("Sviluppatore software", ["software", "svilupp", "developer", "piattaforma"], "Da collegare a fornitore/contratto", "operational"),
                supplier_slot("Customer service", ["customer service", "customer", "assistenza utenti"], "Assistenza investitori e proponenti da censire", "operational"),
                supplier_slot("Marketing", ["marketing", "campagne", "comunicazioni commerciali"], "Comunicazioni commerciali e campagne da censire", "operational"),
            ]
        if is_isi:
            responsibility_map = "".join(
                [
                    responsibility_group(
                        "Governance",
                        "organo e supervisione",
                        [
                            responsibility_node("Consiglio di Amministrazione", ", ".join(cda_names), "Organo di gestione", "governance"),
                            responsibility_node("Legale rappresentante", owner_for("Legale rappresentante", cda_names[0] if cda_names else ""), "Firma, rappresentanza, deleghe", "governance"),
                            responsibility_node("Revisore dei conti", owner_for("Revisore dei conti"), "Da collegare a incarico/revisione", "governance"),
                        ] + custom_nodes_for("Governance"),
                    ),
                    responsibility_group(
                        "Funzioni responsabili",
                        "presidi interni",
                        [
                            responsibility_node("Gestione reclami e default", owner_for("Gestione reclami e default", compliance_user["name"] if compliance_user else ""), "Reclami, tassi di default, comunicazioni clienti", "function"),
                            responsibility_node("Conflitti di interesse", owner_for("Conflitti di interesse", legal_user["name"] if legal_user else ""), "Policy conflitti, parti correlate, presidi", "function"),
                            responsibility_node("Continuita operativa", owner_for("Continuita operativa", cda_names[0] if cda_names else ""), "BCP, incidenti, continuita servizi", "function"),
                            responsibility_node("Prevenzione frodi", owner_for("Prevenzione frodi"), "Presidi antifrode e anomalie operative", "function"),
                            responsibility_node("Whistleblowing", owner_for("Whistleblowing"), "Canali interni e segnalazioni", "function"),
                            responsibility_node("Privacy e archiviazione", owner_for("Privacy e archiviazione", legal_user["name"] if legal_user else ""), "Privacy, conservazione, documentazione", "function"),
                            responsibility_node("Contabilita", owner_for("Contabilita"), "Bilanci e dati prudenziali da collegare", "function"),
                        ] + custom_nodes_for("Funzioni responsabili"),
                    ),
                    responsibility_group(
                        "Area di controllo",
                        "compliance, AML, risk",
                        [
                            responsibility_node("Compliance interna", owner_for("Compliance interna", compliance_user["name"] if compliance_user else ""), "Regole ECSP, controlli e monitoraggio", "control"),
                            responsibility_node("Antiriciclaggio / antiterrorismo", owner_for("Antiriciclaggio / antiterrorismo", legal_user["name"] if legal_user else ""), "AML, KYC, questionario appropriatezza", "control"),
                            responsibility_node("Risk control", owner_for("Risk control", names_for("Advisory Committee")), "Controlli interni e vigilanza", "control"),
                            responsibility_node("Questionario appropriatezza", owner_for("Questionario appropriatezza"), "Presidio questionari e soglie investitori", "control"),
                            responsibility_node("Responsabile outsourcing", owner_for("Responsabile outsourcing"), "Coordinamento fornitori critici", "control"),
                        ] + custom_nodes_for("Area di controllo"),
                    ),
                    responsibility_group("Servizi in outsourcing", "fornitori critici", outsourcing_nodes + custom_nodes_for("Servizi in outsourcing")),
                    responsibility_group("Comitato tecnico progetti", "valutazione offerte", technical_nodes + custom_nodes_for("Comitato tecnico progetti")),
                    responsibility_group("Advisory Committee", "approvazione successiva", advisory_nodes + custom_nodes_for("Advisory Committee")),
                    responsibility_group("Area operativa", "servizi e utenti", operational_nodes + custom_nodes_for("Area operativa")),
                ]
            )
        else:
            # Organigramma Pariter = funzionigramma del fascicolo autorizzato (con allegato).
            adv_names = ", ".join(m["name"] for m in advisory_members) or "Membri da censire"
            cda_str = ", ".join(cda_names)
            responsibility_map = "".join(
                [
                    responsibility_group("Governance", "organi e amministrazione", [
                        responsibility_node("Gestione e approvazione delle offerte", "", "Consiglio di Amministrazione - All. 6.1", "governance"),
                        responsibility_node("Rappresentanza legale e rapporti con la vigilanza", "", "Presidente del CdA - All. 6.1", "governance"),
                        responsibility_node("Organo di controllo", "", "Sindaco unico - All. 6.1", "governance"),
                        responsibility_node("Revisione legale dei conti", "", "Soggetto distinto dal sindaco - All. 6.1", "governance"),
                    ] + custom_nodes_for("Governance")),
                    responsibility_group("Funzioni responsabili", "tutela investitori, dati, IT, continuita, prudenziale", [
                        responsibility_node("Tutela dell'investitore", "", "Classificazione, test, simulazione, limiti (processo team/piattaforma) - All. 18, 19", "function"),
                        responsibility_node("Protezione dei dati personali", "", "Responsabile privacy - All. 7", "function"),
                        responsibility_node("Responsabile IT", "", "Sistemi informativi - All. 8.1", "function"),
                        responsibility_node("Gestione reclami", "", "Responsabile reclami (un Consigliere) - All. 16", "function"),
                        responsibility_node("Continuita operativa", "", "Presidente CdA + referenti esternalizzazioni - All. 11", "function"),
                        responsibility_node("Presidi prudenziali / fondi propri", "", "Monitoraggio del Consiglio - All. 9.1, 10.1, 10.2", "function"),
                    ] + custom_nodes_for("Funzioni responsabili")),
                    responsibility_group("Area di controllo", "controlli interni di 2 livello", [
                        responsibility_node("Controllo di 2 livello (conformita e rischi)", "", "Responsabile delle funzioni di controllo - All. 6.1", "control"),
                        responsibility_node("Antiriciclaggio e adeguata verifica (art. 5)", "", "Valutazione finale del Responsabile dei controlli - All. 5.1", "control"),
                        responsibility_node("Conflitti di interesse", "", "Team + Advisory + Responsabile dei controlli - All. 14", "control"),
                        responsibility_node("Monitoraggio dei fornitori esternalizzati", "", "Consigliere incaricato dei controlli - All. 8.1, 11", "control"),
                    ] + custom_nodes_for("Area di controllo")),
                    responsibility_group("Comitato tecnico progetti", "team di valutazione (CVOI)", [
                        responsibility_node("Valutazione e selezione delle offerte", "", "Scoring, fascicolo, coerenza KIIS (team da ricomporre) - All. 5.1, 6.1", "committee"),
                        responsibility_node("Marketing editoriale delle offerte", "", "Presentazione delle singole offerte, distinto dal marketing corporate - All. 5.3, 6.1", "committee"),
                    ] + custom_nodes_for("Comitato tecnico progetti")),
                    responsibility_group("Advisory Committee", "parere indipendente sulle offerte", [
                        responsibility_node("Parere indipendente sulle offerte", "", "Membri esterni indipendenti - All. 6.1, 5.1", "advisory"),
                    ] + custom_nodes_for("Advisory Committee")),
                    responsibility_group("Servizi in outsourcing", "esternalizzazioni", [
                        supplier_slot("Gestione e manutenzione della piattaforma", ["software", "svilupp", "piattaforma", "code factory"], "Da censire - All. 8.1", "outsourcing"),
                        supplier_slot("Infrastruttura cloud / hosting", ["aws", "amazon web", "cloud", "hosting", "infrastruttura"], "Da censire - All. 8.1", "outsourcing"),
                        supplier_slot("Pagamenti e custodia", ["pagamento", "payment", "sella", "istituto", "custodia"], "Da censire - All. 17", "outsourcing"),
                        supplier_slot("Marketing e comunicazioni", ["marketing", "comunicazioni", "2duerighe", "g2r"], "Da censire (infragruppo)", "outsourcing"),
                        supplier_slot("Supporto legale, governance e compliance", ["compliance", "legale", "governance", "astralex"], "Da censire (infragruppo)", "outsourcing"),
                        supplier_slot("Tenuta della contabilita ordinaria", ["contabil", "amministrazione", "astralex"], "Da censire (infragruppo)", "outsourcing"),
                        responsibility_node("Coperture assicurative", "", "Polizza / compagnia da indicare", "outsourcing"),
                    ] + custom_nodes_for("Servizi in outsourcing")),
                ]
            )
        agreement_rows = "".join(
            f"""<tr>
              <td>{person_link(a['person_name'], a['role'])}</td>
              <td>{esc(a['role'])}</td>
              <td>{esc(a['agreement_type'])}</td>
              <td>{esc(a['scope'])}</td>
              <td><span class="badge {badge_class(a['status'])}">{esc(a['status'])}</span></td>
              <td>{esc(nice_date(a['expires_at']))}</td>
            </tr>"""
            for a in agreements
        )
        anagraphic_agreement_rows = "".join(
            f"""<tr>
              <td>{person_link(a['person_name'], a['role'])}</td>
              <td>{esc(a['agreement_type'])}</td>
              <td>{esc(a['scope'])}</td>
              <td>{esc(nice_date(a['expires_at']))}</td>
            </tr>"""
            for a in agreements
        ) or '<tr><td colspan="4" class="empty-row">Nessun accordo collegato a funzioni.</td></tr>'
        person_doc_rows = "".join(
            f"""<div class="document-row">
              <div>
                <strong>{esc(pd['title'])}</strong>
                <span>{esc(pd['document_type'])} - {esc(pd['counterparty'] or 'controparte non indicata')} - {esc(nice_date(pd['created_at']))}</span>
              </div>
              <a class="button ghost" href="{rel_url('/documents/' + str(pd['doc_id']) + '/download', ctx) if pd['doc_id'] else '#'}">Apri</a>
            </div>"""
            for pd in person_docs
        ) or '<p class="empty-state">Nessun documento collegato a questa persona.</p>'
        if selected_person == "Mario Rossi":
            person_doc_rows = """
            <div class="document-row">
              <div>
                <strong>Lettera di incarico - presidio demo</strong>
                <span>Contratto / incarico - ISI Crowd - 15/06/2026</span>
              </div>
              <button class="button ghost" type="button" data-open-action data-action="Documento Mario Rossi">Apri</button>
            </div>
            <div class="document-row">
              <div>
                <strong>Delega operativa funzione demo</strong>
                <span>Delega - Consiglio di Amministrazione - 15/06/2026</span>
              </div>
              <button class="button ghost" type="button" data-open-action data-action="Delega Mario Rossi">Apri</button>
            </div>"""
        def contract_alert(contract):
            if contract["status"] == "Scaduto":
                return "urgent", "scaduto"
            try:
                days = (date.fromisoformat(contract["end_date"]) - date.today()).days
            except (TypeError, ValueError):
                return "neutral", "senza scadenza"
            if days < 0:
                return "urgent", f"scaduto da {-days} gg"
            if days <= 60:
                return "soon", f"{days} gg"
            return "ok", nice_date(contract["end_date"])

        active_contracts = [c for c in supplier_contracts if c["status"] in {"Attivo", "In rinnovo", "Scaduto", "Da firmare"}]
        contract_rows = "".join(
            f"""<div class="contract-alert-row">
              <div>
                <strong>{esc(c['title'])}</strong>
                <span>{esc(c['supplier_name'])} - {esc(c['contract_type'])}</span>
              </div>
              <div class="contract-alert-meta">
                <span class="deadline-dot {contract_alert(c)[0]}"></span>
                <strong>{esc(contract_alert(c)[1])}</strong>
                <small>{esc(c['status'])}</small>
              </div>
            </div>"""
            for c in active_contracts
        ) or '<p class="empty-state">Nessun contratto attivo o in alert.</p>'
        person_agreement_counts = {}
        for agreement in agreements:
            person_agreement_counts[agreement["person_name"]] = person_agreement_counts.get(agreement["person_name"], 0) + 1
        person_document_counts = {}
        for doc in rows("SELECT person_name, COUNT(*) AS c FROM person_documents WHERE platform_id = ? GROUP BY person_name", (pid,)):
            person_document_counts[doc["person_name"]] = doc["c"]

        supplier_contract_counts = {}
        supplier_due_dates = {}
        for contract in supplier_contracts:
            supplier_contract_counts[contract["supplier_name"]] = supplier_contract_counts.get(contract["supplier_name"], 0) + 1
            if contract["end_date"]:
                current = supplier_due_dates.get(contract["supplier_name"])
                supplier_due_dates[contract["supplier_name"]] = min(current, contract["end_date"]) if current else contract["end_date"]
        subject_entries = []
        for name, data in person_registry.items():
            subject_entries.append({
                "name": name,
                "type": data.get("type") or subject_type_by_name.get(name, "Persona fisica"),
                "functions": sorted(set(data["functions"])),
                "agreements": person_agreement_counts.get(name, 0),
                "documents": person_document_counts.get(name, 0),
                "deadline": "",
                "href": anagraphic_person_url(name),
            })
        for name, data in supplier_registry.items():
            subject_entries.append({
                "name": name,
                "type": "Societa / ente",
                "functions": sorted(set(data["functions"])),
                "agreements": supplier_contract_counts.get(name, 0),
                "documents": 0,
                "deadline": supplier_due_dates.get(name, ""),
                "href": data["href"] or rel_url("/documents", ctx, {"origin": "Contratto fornitore"}),
            })
        subject_directory = "".join(
            f"""<tr class="subject-directory-row">
              <td><a href="{entry['href']}"><strong>{esc(entry['name'])}</strong></a><br><span class="muted">{esc(entry['type'])}</span></td>
              <td>{esc(', '.join(entry['functions']))}</td>
              <td>{entry['agreements']}</td>
              <td>{entry['documents']}</td>
              <td>{esc(nice_date(entry['deadline']) or '-')}</td>
              <td><span class="badge {badge_class('Attivo' if entry['functions'] else 'Da censire')}">{'Attivo' if entry['functions'] else 'Da censire'}</span></td>
              <td><a class="button ghost" href="{entry['href']}">Apri</a></td>
            </tr>"""
            for entry in sorted(subject_entries, key=lambda item: (item["type"], item["name"]))
        ) or '<tr><td colspan="7" class="empty-row">Nessun soggetto assegnato nell organigramma.</td></tr>'
        subject_options = '<option value="">Seleziona soggetto in anagrafica</option>' + "".join(
            f"""<option value="{esc(entry['name'])}"
              data-type="{esc(entry['type'])}"
              data-functions="{esc(', '.join(entry['functions']) or '-')}"
              data-agreements="{entry['agreements']}"
              data-documents="{entry['documents']}"
              data-deadline="{esc(nice_date(entry['deadline']) or '-')}"
              data-status="{'Attivo' if entry['functions'] else 'Da censire'}">{esc(entry['name'])} - {esc(entry['type'])}</option>"""
            for entry in sorted(subject_entries, key=lambda item: item["name"])
        )
        kind_labels = {
            "governance": "Governance",
            "function": "Funzione responsabile",
            "control": "Area di controllo",
            "outsourcing": "Outsourcing / servizio",
            "committee": "Comitato tecnico",
            "advisory": "Advisory Committee",
            "operational": "Area operativa",
        }
        unique_functions = {}
        for item in function_catalog:
            label = item["label"]
            unique_functions.setdefault(label, {
                "label": label,
                "kind": kind_labels.get(item["kind"], item["kind"]),
                "note": item["note"],
                "owners": item["owners"],
                "href": item["href"],
            })
        function_entries = sorted(unique_functions.values(), key=lambda item: (item["kind"], item["label"]))
        function_kind_lookup = {item["label"]: item["kind"] for item in function_entries}
        function_select_options = '<option value="">Seleziona funzione dall organigramma</option>' + "".join(
            f'<option value="{esc(item["label"])}" data-area="{esc(item["kind"])}">{esc(item["label"])} - {esc(item["kind"])}</option>'
            for item in function_entries
        ) + '<option value="__new__">+ Nuova funzione non ancora in organigramma</option>'
        function_options = "".join(
            f'<option value="{esc(item["label"])}">{esc(item["kind"])}</option>'
            for item in function_entries
        )
        document_catalog = []
        for pd in person_docs:
            document_catalog.append((pd["title"], f"{pd['document_type']} - {pd['counterparty'] or 'senza controparte'}"))
        if selected_person == "Mario Rossi":
            document_catalog.extend([
                ("Lettera di incarico - presidio demo", "Contratto / incarico - scadenza 31/12/2027"),
                ("Delega operativa funzione demo", "Delega - scadenza 30/06/2027"),
            ])
        for d in list(corporate_docs)[:6] + list(authorization_docs)[:6] + list(balance_docs)[:4]:
            document_catalog.append((d["title"], f"{d['category']} - {nice_date(d['created_at'])}"))
        for c in supplier_contracts[:8]:
            document_catalog.append((c["title"], f"{c['supplier_name']} - scadenza {nice_date(c['end_date']) or 'non indicata'}"))
        seen_docs = set()
        document_options_parts = [
            '<option value="">Nessun documento</option>',
            '<option value="__upload__">Nuovo documento</option>',
        ]
        for title, detail in document_catalog:
            if title in seen_docs:
                continue
            seen_docs.add(title)
            document_options_parts.append(f'<option value="{esc(title)}">{esc(title)} - {esc(detail)}</option>')
        document_options = "".join(document_options_parts)
        selected_person_functions = sorted(set(person_registry.get(selected_person, {}).get("functions", [])))
        def function_relation_meta(function_name):
            assignment = assignment_by_pair.get((selected_person, function_name))
            if assignment:
                start = nice_date(assignment["start_date"]) if assignment["start_date"] else "--"
                end = nice_date(assignment["end_date"]) if assignment["end_date"] else "--"
                document_title = assignment["linked_document_title"] or assignment["doc_title"] or "Nessun documento collegato"
                return {
                    "role": assignment["role"] or "Da definire",
                    "start_date": assignment["start_date"],
                    "end_date": assignment["end_date"],
                    "date_range": f"Dal {start} / al {end}",
                    "document": document_title,
                    "document_state": "linked" if assignment["linked_document_title"] or assignment["document_id"] else "missing",
                    "deadline": nice_date(assignment["end_date"]) if assignment["end_date"] else "-",
                    "context": assignment["notes"] or "Relazione salvata nel modello dati organigramma.",
                }
            if selected_person == "Mario Rossi" and function_name == "Presidio demo architettura":
                return {
                    "role": "Responsabile funzione demo",
                    "start_date": "2026-06-15",
                    "end_date": "2027-12-31",
                    "date_range": "Dal 15/06/2026 / al 31/12/2027",
                    "document": "Lettera di incarico - presidio demo",
                    "document_state": "linked",
                    "deadline": "31/12/2027",
                    "context": "Contratto collegato alla funzione e riutilizzabile come fonte documentale.",
                }
            if selected_person == "Mario Rossi":
                return {
                    "role": "Delegato operativo",
                    "start_date": "2026-06-15",
                    "end_date": "2027-06-30",
                    "date_range": "Dal 15/06/2026 / al 30/06/2027",
                    "document": "Delega operativa funzione demo",
                    "document_state": "linked",
                    "deadline": "30/06/2027",
                    "context": "Documento collegato a piu funzioni, richiamabile nello scadenziario.",
                }
            return {
                "role": "Da definire",
                "start_date": "",
                "end_date": "",
                "date_range": "Dal -- / al --",
                "document": "Nessun documento collegato",
                "document_state": "missing",
                "deadline": "-",
                "context": "Relazione da completare con ruolo, date e contratto o documento collegato.",
            }

        selected_person_function_parts = []
        for function_name in selected_person_functions:
            meta = function_relation_meta(function_name)
            selected_person_function_parts.append(f"""<li class="assignment-linked-row">
              <div>
                <strong>{esc(function_name)}</strong>
                <span>{esc(function_kind_lookup.get(function_name, 'Area da classificare'))} - {esc(meta['role'])}</span>
                <span>{esc(meta['date_range'])}</span>
                <small>{esc(meta['context'])}</small>
              </div>
              <div class="assignment-meta">
                <em><span class="deadline-dot {'ok' if meta['document_state'] == 'linked' else 'neutral'}"></span>{esc(meta['document'])}</em>
                <span class="assignment-deadline">Scadenza {esc(meta['deadline'])}</span>
                <button type="button" data-open-action data-action="Collegamento funzione" data-function="{esc(function_name)}" data-role="{esc(meta['role'])}" data-start="{esc(meta['start_date'])}" data-end="{esc(meta['end_date'])}" data-document="{esc(meta['document'] if meta['document_state'] == 'linked' else '')}">Modifica</button>
                <form class="inline-action-form" method="post" action="/compagine/assignment-delete" onsubmit="return window.confirm('Archiviare questa funzione dal profilo selezionato?')">
                  {hidden_ctx(ctx)}
                  <input type="hidden" name="subject_name" value="{esc(selected_person)}">
                  <input type="hidden" name="function_name" value="{esc(function_name)}">
                  <input type="hidden" name="status" value="Archiviato">
                  <button type="submit">Archivia</button>
                </form>
                <form class="inline-action-form" method="post" action="/compagine/assignment-delete" onsubmit="return window.confirm('Eliminare questa funzione dal profilo selezionato?')">
                  {hidden_ctx(ctx)}
                  <input type="hidden" name="subject_name" value="{esc(selected_person)}">
                  <input type="hidden" name="function_name" value="{esc(function_name)}">
                  <input type="hidden" name="status" value="Rimosso">
                  <button type="submit" title="Elimina funzione">x</button>
                </form>
              </div>
            </li>""")
        selected_person_function_rows = "".join(selected_person_function_parts) or '<li class="empty-row">Nessuna funzione assegnata nell organigramma.</li>'
        selected_agreement_rows = "".join(
            f"""<tr>
              <td>{esc(a['agreement_type'])}</td>
              <td>{esc(a['scope'])}</td>
              <td><span class="badge {badge_class(a['status'])}">{esc(a['status'])}</span></td>
              <td>{esc(nice_date(a['expires_at']))}</td>
            </tr>"""
            for a in agreements
            if a["person_name"] == selected_person
        ) or '<tr><td colspan="4" class="empty-row">Nessun accordo collegato alla persona.</td></tr>'
        if selected_person == "Mario Rossi":
            selected_agreement_rows = """
            <tr>
              <td>Lettera di incarico</td>
              <td>Presidio demo architettura</td>
              <td><span class="badge ok">Attivo</span></td>
              <td>31/12/2027</td>
            </tr>
            <tr>
              <td>Delega operativa</td>
              <td>Supporto a funzione interna</td>
              <td><span class="badge warning">Da verificare</span></td>
              <td>30/06/2027</td>
            </tr>"""
        selected_parts = selected_person.split(" ", 1)
        selected_first_name = selected_parts[0] if selected_parts else ""
        selected_last_name = selected_parts[1] if len(selected_parts) > 1 else ""
        selected_subject_type = subject_type_by_name.get(
            selected_person,
            person_registry.get(selected_person, {}).get("type", "Persona fisica") if selected_person else "Persona fisica",
        )
        tab_html = "".join(
            f'<a class="subtab {"active" if key == active_tab else ""}" href="{rel_url("/compagine", ctx, {"tab": key})}">{label}</a>'
            for key, label in [("organigramma", "Organigramma"), ("anagrafiche", "Lista anagrafica")]
        )
        # CV, documento d'identita e firma del soggetto: integrati nel fascicolo persona
        # (ex pagina Team). Solo per soggetti Persona; i dati stanno in team_people.
        cv_firma_section = ""
        if selected_person and "Persona" in selected_subject_type:
            tp = row("SELECT * FROM team_people WHERE platform_id = ? AND name = ? ORDER BY id LIMIT 1", (pid, selected_person))

            def _cvf_doc(doc_id, label, kind):
                cur = (f'<a class="button tiny" href="{rel_url("/documents/" + str(doc_id) + "/download", ctx)}">Apri</a>'
                       if doc_id else '<span class="muted">non caricato</span>')
                return f"""<div class="cvf-row"><span>{esc(label)}</span>{cur}
                  <form method="post" action="/pariter/team/upload" enctype="multipart/form-data" class="inline-form">
                    {hidden_ctx(ctx)}<input type="hidden" name="person_name" value="{esc(selected_person)}"><input type="hidden" name="person_role" value="{esc(selected_role or 'Anagrafica organigramma')}"><input type="hidden" name="kind" value="{kind}">
                    <input type="file" name="file" required><button class="button tiny" type="submit">Carica</button></form></div>"""

            firma_img = '<span class="muted">nessuna firma</span>'
            if tp and tp["firma_path"] and (BASE_DIR / tp["firma_path"]).exists():
                b64 = base64.b64encode((BASE_DIR / tp["firma_path"]).read_bytes()).decode()
                ext = tp["firma_path"].rsplit(".", 1)[-1].lower()
                mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
                firma_img = f'<img src="data:{mime};base64,{b64}" style="max-height:72px;border:1px solid var(--line);background:#fff">'
            cv_id = tp["cv_document_id"] if tp else None
            id_doc = tp["id_document_id"] if tp else None
            cv_firma_section = f"""
      <div class="section-head compact-head"><h3>Curriculum, documento e firma</h3><span class="panel-kicker">soggetto persona</span></div>
      <div class="cv-firma">
        {_cvf_doc(cv_id, "Curriculum vitae", "cv")}
        {_cvf_doc(id_doc, "Documento d'identita", "documento")}
        <div class="cvf-row"><span>Firma</span>{firma_img}
          <form method="post" action="/pariter/team/upload" enctype="multipart/form-data" class="inline-form">
            {hidden_ctx(ctx)}<input type="hidden" name="person_name" value="{esc(selected_person)}"><input type="hidden" name="person_role" value="{esc(selected_role or 'Anagrafica organigramma')}"><input type="hidden" name="kind" value="firma">
            <input type="file" name="file" accept="image/*" required><button class="button tiny" type="submit">Carica firma</button></form>
        </div>
        <form method="post" action="/pariter/team/firma-draw" id="firmaForm" style="margin-top:8px">
          {hidden_ctx(ctx)}<input type="hidden" name="person_name" value="{esc(selected_person)}"><input type="hidden" name="person_role" value="{esc(selected_role or 'Anagrafica organigramma')}"><input type="hidden" name="firma_data" id="firmaData">
          <canvas id="firmaCanvas" width="420" height="120" class="firma-canvas"></canvas>
          <div class="form-actions left"><button type="button" class="button ghost" id="firmaClear">Cancella</button><button class="button primary" type="submit">Salva firma disegnata</button></div>
        </form>
        <p class="muted" style="font-size:11px">La firma vale per relazioni e verbali se la persona ha anche il documento d'identita caricato.</p>
      </div>"""
        cv_firma_panel = f'<section class="panel">{cv_firma_section}</section>' if cv_firma_section else ""

        person_modal = ""
        if selected_person and active_tab != "anagrafiche":
            selected_status = "Attivo" if selected_person_functions else "Da censire"
            selected_next_deadline = next((a["expires_at"] for a in agreements if a["person_name"] == selected_person and a["expires_at"]), "")
            person_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal">
    <div class="section-head">
      <h2>{esc(selected_role or 'Fascicolo persona')}</h2>
      <a class="modal-close" href="{rel_url('/compagine', ctx)}">x</a>
    </div>
    <section class="identity-hero">
      <div>
        <p class="eyebrow">Anagrafica organigramma</p>
        <h2>{esc(selected_person)}</h2>
        <p class="muted">{esc(', '.join(selected_person_functions) or 'Nessuna funzione assegnata')}</p>
      </div>
      <div class="header-badges">
        <span class="badge {badge_class(selected_status)}">{esc(selected_status)}</span>
        <span class="badge neutral">{len(selected_person_functions)} funzioni</span>
      </div>
    </section>
    <section class="metric-grid compact-metric-grid">
      <div class="metric"><span>Accordi</span><strong>{person_agreement_counts.get(selected_person, 0)}</strong></div>
      <div class="metric"><span>Documenti</span><strong>{person_document_counts.get(selected_person, 0)}</strong></div>
      <div class="metric"><span>Prossima scadenza</span><strong>{esc(nice_date(selected_next_deadline) or '-')}</strong></div>
      <div class="metric"><span>Stato dossier</span><strong>{esc(selected_status)}</strong></div>
    </section>
    <section class="workspace-grid modal-grid">
      <div class="panel inset-panel">
        <div class="section-head compact-head"><h3>Dati anagrafici</h3><span class="panel-kicker">profilo</span></div>
        <dl class="definition-list compact-definition">
          <dt>Nome</dt><dd>{esc(selected_first_name or '-')}</dd>
          <dt>Cognome</dt><dd>{esc(selected_last_name or '-')}</dd>
          <dt>Email</dt><dd>-</dd>
          <dt>Telefono</dt><dd>-</dd>
          <dt>Note</dt><dd>Da completare nella scheda anagrafica.</dd>
        </dl>
      </div>
      <div class="panel inset-panel">
        <div class="section-head compact-head"><h3>Azioni anagrafica</h3><span class="panel-kicker">design</span></div>
        <div class="inline-actions left">
          <button class="button primary" type="button" data-open-action data-action="Modifica dati">Modifica dati</button>
          <button class="button ghost" type="button" data-open-action data-action="Nuovo documento">+ Documento</button>
          <button class="button secondary" type="button" data-open-action data-action="Aggiungi funzione">+ Funzione</button>
        </div>
      </div>
    </section>
    <div class="person-document-list">
      <div class="section-head compact-head">
        <h3>Funzioni in organigramma</h3>
        <button class="button primary" type="button" data-open-assignment data-function="{esc(selected_role or '')}">+ Aggiungi funzione</button>
      </div>
      <ul class="assignment-list">{selected_person_function_rows}</ul>
      <p class="panel-kicker">Accordi / contratti collegati</p>
      <table class="data-table compact">
        <thead><tr><th>Accordo</th><th>Funzioni / ambito</th><th>Stato</th><th>Scadenza</th></tr></thead>
        <tbody>{selected_agreement_rows}</tbody>
      </table>
      <div class="section-head compact-head">
        <h3>Documenti collegati</h3>
        <button class="button ghost" type="button" data-open-action data-action="Collega documento">+ Collega documento</button>
      </div>
      {person_doc_rows}
      {cv_firma_section}
    </div>
    <datalist id="org-function-options">{function_options}</datalist>
  </section>
</div>"""
        shareholder_modal = ""
        if selected_shareholder:
            shareholder_doc_rows = "".join(
                f"""<div class="document-row">
                  <div>
                    <strong>{esc(sd['title'])}</strong>
                    <span>{esc(sd['document_type'])} - {esc(nice_date(sd['created_at']))}{' - scadenza ' + esc(nice_date(sd['expires_at'])) if sd['expires_at'] else ''}</span>
                    <small>{esc(sd['notes'])}</small>
                  </div>
                  <a class="button ghost" href="{rel_url('/documents/' + str(sd['doc_id']) + '/download', ctx) if sd['doc_id'] else '#'}">Apri</a>
                </div>"""
                for sd in shareholder_docs
            ) or '<p class="empty-state">Nessun documento caricato per questo partecipante.</p>'
            shareholder_doc_text = " ".join(f"{sd['document_type']} {sd['title']}" for sd in shareholder_docs).lower()
            shareholder_requirements = [
                ("Statuto / atto costitutivo", "Statuto aggiornato o atto costitutivo del socio."),
                ("Visura camerale", "Visura aggiornata o documento equivalente."),
                ("Libro soci / cap table", "Dati soci, catena partecipativa e quote rilevanti."),
                ("Patti parasociali", "Patti o dichiarazione di assenza patti."),
                ("Titolari effettivi", "Identificazione titolari effettivi e assetto di controllo."),
                ("Dichiarazioni onorabilita", "Dichiarazioni requisiti di onorabilita dei soggetti rilevanti."),
            ]
            shareholder_check_rows = "".join(
                f"""<li class="auth-check {'found' if any(token in shareholder_doc_text for token in label.lower().replace('/', ' ').split()) else 'missing'}">
                  <span></span>
                  <div><strong>{esc(label)}</strong><small>{esc(note)}</small></div>
                </li>"""
                for label, note in shareholder_requirements
            )
            shareholder_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal shareholder-modal">
    <div class="section-head">
      <h2>{esc(selected_shareholder['name'])}</h2>
      <a class="modal-close" href="{rel_url('/compagine', ctx)}">x</a>
    </div>
    <section class="identity-hero">
      <div>
        <p class="eyebrow">Partecipante qualificato</p>
        <h2>{esc(selected_shareholder['name'])}</h2>
        <p class="muted">{esc(selected_shareholder['subject_type'])} - quota {selected_shareholder['stake_percent']:.2f}%</p>
      </div>
      <div class="header-badges">
        <span class="badge {badge_class(selected_shareholder['status'])}">{esc(selected_shareholder['status'])}</span>
        <span class="badge {badge_class(selected_shareholder['requisites_status'])}">{esc(selected_shareholder['requisites_status'])}</span>
      </div>
    </section>
    <section class="metric-grid compact-metric-grid">
      <div class="metric"><span>Quota</span><strong>{selected_shareholder['stake_percent']:.2f}%</strong></div>
      <div class="metric"><span>Documenti</span><strong>{len(shareholder_docs)}</strong></div>
      <div class="metric"><span>Requisiti</span><strong>{esc(selected_shareholder['requisites_status'])}</strong></div>
      <div class="metric"><span>Stato</span><strong>{esc(selected_shareholder['status'])}</strong></div>
    </section>
    <section class="workspace-grid modal-grid">
      <div class="panel inset-panel">
        <div class="section-head compact-head"><h3>Dati societari</h3><span class="panel-kicker">anagrafica</span></div>
        <form class="form-grid" method="post" action="/shareholders/{selected_shareholder['id']}/update">
          {hidden_ctx(ctx)}
          <label>Tipo soggetto<select name="subject_type">
            {''.join(f'<option {"selected" if selected_shareholder["subject_type"] == option else ""}>{option}</option>' for option in ["Societa / ente", "Persona fisica", "Holding", "Trust / veicolo"])}
          </select></label>
          <label>Nome / ragione sociale<input name="name" value="{esc(selected_shareholder['name'])}" required></label>
          <label>Forma giuridica<input name="legal_form" value="{esc(selected_shareholder['legal_form'])}" placeholder="es. S.r.l., S.p.A."></label>
          <label>Codice fiscale / P.IVA<input name="tax_id" value="{esc(selected_shareholder['tax_id'])}"></label>
          <label>Email<input name="contact_email" value="{esc(selected_shareholder['contact_email'])}"></label>
          <label>Telefono<input name="phone" value="{esc(selected_shareholder['phone'])}"></label>
          <label>Quota %<input type="number" step="0.01" min="0" max="100" name="stake_percent" value="{selected_shareholder['stake_percent']:.2f}"></label>
          <label>Stato requisiti<select name="requisites_status">
            {''.join(f'<option {"selected" if selected_shareholder["requisites_status"] == option else ""}>{option}</option>' for option in ["Da verificare", "In verifica", "Completo", "Da integrare", "Non conforme"])}
          </select></label>
          <label>Stato<select name="status">
            {''.join(f'<option {"selected" if selected_shareholder["status"] == option else ""}>{option}</option>' for option in ["Attivo", "In verifica", "Da integrare", "Archiviato"])}
          </select></label>
          <label class="full-span">Sede / indirizzo<input name="address" value="{esc(selected_shareholder['address'])}"></label>
          <label class="full-span">Dati soci / titolari effettivi<textarea name="beneficial_owners" rows="4" placeholder="Catena partecipativa, soci diretti/indiretti, titolari effettivi">{esc(selected_shareholder['beneficial_owners'])}</textarea></label>
          <label class="full-span">Note<textarea name="notes" rows="3">{esc(selected_shareholder['notes'])}</textarea></label>
          <div class="form-actions left full-span"><button class="button primary" type="submit">Salva partecipante</button></div>
        </form>
      </div>
      <div class="panel inset-panel">
        <div class="section-head compact-head"><h3>Checklist documentale</h3><span class="panel-kicker">requisiti</span></div>
        <ul class="auth-check-list">{shareholder_check_rows}</ul>
      </div>
    </section>
    <section class="panel inset-panel">
      <div class="section-head compact-head"><h3>Documenti del partecipante</h3><span class="panel-kicker">statuto, visura, soci, onorabilita</span></div>
      <form class="form-grid upload-form" method="post" action="/shareholders/{selected_shareholder['id']}/document-upload" enctype="multipart/form-data">
        {hidden_ctx(ctx)}
        <label>Tipo documento<select name="document_type">
          <option>Statuto / atto costitutivo</option>
          <option>Visura camerale</option>
          <option>Libro soci / cap table</option>
          <option>Patti parasociali</option>
          <option>Titolari effettivi</option>
          <option>Dichiarazioni onorabilita</option>
          <option>Documento identita</option>
          <option>Altro documento socio</option>
        </select></label>
        <label>Titolo<input name="title" placeholder="es. Visura aggiornata socio"></label>
        <label>Data documento<input type="date" name="issued_at"></label>
        <label>Scadenza / aggiornamento<input type="date" name="expires_at"></label>
        <label class="full-span">Note<textarea name="notes" rows="2" placeholder="Ambito, soci coperti, soggetti firmatari, integrazioni richieste"></textarea></label>
        <label class="full-span">File<input type="file" name="file" required></label>
        <div class="form-actions left full-span"><button class="button primary" type="submit">Carica documento</button></div>
      </form>
      <div class="document-list compact-document-list">{shareholder_doc_rows}</div>
    </section>
  </section>
</div>"""
        body = f"""
<p class="page-copy">Mappa operativa di governance, funzioni responsabili, controlli, outsourcing e documenti societari collegati. Ogni nodo persona apre il fascicolo con incarichi, deleghe e documenti.</p>
<nav class="subtabs">{tab_html}</nav>
<section class="panel org-panel">
  <div class="section-head">
    <div><h2>Organigramma operativo</h2><span class="muted">Vista per presidi: responsabili interni, controlli, comitato progetti e servizi in outsourcing.</span></div>
    <div class="inline-actions">
      <button class="button primary" type="button" data-open-block data-group="Governance">+ Nuovo blocco</button>
      <span class="panel-kicker">funzioni e fonti</span>
    </div>
  </div>
  <div class="responsibility-map">{responsibility_map}</div>
  <div class="org-legend">
    <span class="legend-chip governance">Governance</span>
    <span class="legend-chip function">Responsabili funzioni</span>
    <span class="legend-chip control">Area di controllo</span>
    <span class="legend-chip outsourcing">Servizi in outsourcing</span>
    <span class="legend-chip committee">Comitato tecnico progetti</span>
    <span class="legend-chip advisory">Advisory Committee</span>
    <span class="legend-chip operational">Area operativa</span>
  </div>
  <div class="participation-head">
    <p class="panel-kicker centered">Partecipogramma - partecipanti qualificati (&ge;20%)</p>
    <button type="button" title="Aggiungi partecipante qualificato" data-open-shareholder>+</button>
  </div>
  <div class="shareholder-list">{shareholder_cards}</div>
</section>
<section class="workspace-grid">
  <div class="panel statute-panel">
    <div class="section-head">
      <div><h2>Statuto</h2><span class="muted">Atto costitutivo e statuto vigente.</span></div>
      <button type="button" class="button primary" data-open-doc data-category="Statuto" data-doc-title="Aggiungi statuto">+ Statuto</button>
    </div>
    <div class="statute-list">
      <div><span>Sede</span><strong>Roma, Viale Parioli 39/C</strong></div>
      <div><span>Capitale</span><strong>Euro 13.157,90 i.v.</strong></div>
      <div><span>Durata</span><strong>fino al 31/12/2050</strong></div>
      <div><span>Oggetto</span><strong>Equity crowdfunding (Reg. UE 2020/1503)</strong></div>
    </div>
    <div class="document-list compact-document-list">{statuto_current_cards}</div>
    <details class="archive-history">
      <summary>Storico delle versioni dello statuto</summary>
      <div class="document-list compact-document-list">{statuto_history_cards}</div>
    </details>
  </div>
  <div class="panel">
    <div class="section-head">
      <div><h2>Autorizzazione ECSP vigente</h2><span class="muted">Il fascicolo come autorizzato da CONSOB: versione finale di ogni allegato, in ordine.</span></div>
      <span class="badge ok">VIGENTE</span>
    </div>
    <p class="auth-banner"><strong>Delibera CONSOB n. 23141 del 05/06/2024</strong> &middot; regime ECSP (Reg. UE 2020/1503) &middot; fascicolo CONSOB 178553. Per ogni allegato la versione vigente e' l'ultima depositata prima del 05/06/2024: questo e' il set definitivo. Le revisioni successive sono nella sezione "Aggiornamenti".</p>
    <div class="auth-banner-links">{storico_link}</div>
    <ul class="fascicolo-index">{fascicolo_index}</ul>
    <div class="authorization-context">
      <div class="section-head compact-head">
        <div><p class="panel-kicker">Aggiornamenti successivi (variazioni 2025-2026)</p>
        <p class="muted">Quando cambiano persone o assetti, qui si aggiunge l'allegato aggiornato (es. "Allegato 14 aggiornato"), senza rifare il fascicolo.</p></div>
        <button type="button" class="button primary" data-open-doc data-category="Aggiornamento autorizzazione" data-doc-title="Aggiungi aggiornamento all'autorizzazione">+ Aggiungi aggiornamento</button>
      </div>
      <div class="document-list compact-document-list">{auth_update_cards}</div>
    </div>
    <details class="archive-history">
      <summary>Storico delle versioni depositate (domanda originale, integrazioni, depositi)</summary>
      {auth_history_cards}
    </details>
  </div>
</section>
<section class="panel" id="scambi-panel">
  <div class="section-head">
    <div><h2>Scambi con l'Autorita</h2><span class="muted">Corrispondenza con la vigilanza in ordine cronologico, per dossier. Filtra al volo per categoria o testo.</span></div>
    <button type="button" class="button primary" data-open-doc data-category="Comunicazione CONSOB" data-doc-title="Aggiungi scambio con l'Autorita">+ Aggiungi scambio</button>
  </div>
  <div class="scambi-toolbar">
    <input type="search" id="scambi-search" placeholder="Cerca per testo o nome file..." autocomplete="off">
    <div class="scambi-chips" id="scambi-chips">
      <button type="button" class="chip active" data-cat="">Tutti</button>
      <button type="button" class="chip" data-cat="consob">CONSOB</button>
      <button type="button" class="chip" data-cat="bdi">Banca d'Italia</button>
      <button type="button" class="chip" data-cat="avvio">Avvio attivita</button>
      <button type="button" class="chip" data-cat="vaglio">Vaglio / rilievi</button>
    </div>
  </div>
  <div class="document-list" id="scambi-list">{authority_list}</div>
  <p class="empty-state" id="scambi-empty" hidden>Nessuno scambio corrisponde al filtro.</p>
  <div class="more-row"><button type="button" class="button ghost" id="scambi-more" hidden>Mostra tutti</button></div>
  <details class="archive-history">
    <summary>Vecchio regime 2019-2021 (materiale legacy, non rilevante)</summary>
    <div class="dossier-list">{legacy_dossiers}</div>
  </details>
</section>
<section class="panel">
  <div class="section-head">
    <div><h2>CdA e assemblee dei soci</h2><span class="muted">Archivio dei verbali di Consiglio di Amministrazione e delle assemblee dei soci.</span></div>
    <div class="inline-actions">
      <button type="button" class="button primary" data-open-doc data-category="Verbale CdA" data-doc-title="Aggiungi verbale di CdA">+ Verbale CdA</button>
      <button type="button" class="button ghost" data-open-doc data-category="Verbale assemblea soci" data-doc-title="Aggiungi verbale di assemblea soci">+ Assemblea soci</button>
    </div>
  </div>
  <div class="document-list">{cda_cards}</div>
</section>
<section class="panel">
  <div class="section-head">
    <div><h2>Documenti interni</h2><span class="muted">Knowledge base, framework compliance, procedure permanenti, DORA, organigramma e processo di onboarding.</span></div>
    <button type="button" class="button primary" data-open-doc data-category="Documento interno" data-doc-title="Aggiungi documento interno">+ Aggiungi</button>
  </div>
  <div class="document-list">{internal_cards}</div>
</section>
<section class="panel">
  <div class="section-head">
    <div><h2>Bilanci e situazione contabile</h2><span class="muted">Solo bilanci e situazioni contabili. Fonti per CF1, VIG12 e patrimonio di vigilanza.</span></div>
    <button type="button" class="button primary" data-open-doc data-category="Bilancio" data-doc-title="Aggiungi bilancio o situazione contabile">+ Bilancio</button>
  </div>
  <div class="document-list">{bilanci_cards}</div>
</section>
<section class="panel">
  <div class="section-head">
    <div><h2>Contratti e scadenze</h2><span class="muted">Contratti di fornitori e servizi critici in outsourcing, con relative scadenze operative.</span></div>
    <a class="button primary" href="{rel_url('/documents', ctx, {'origin': 'Contratto fornitore', 'mode': 'upload'})}">+ Contratto</a>
  </div>
  <div class="contract-alert-list">{contract_rows}</div>
</section>
<section class="panel">
  <div class="section-head">
    <div><h2>Allegati e template</h2><span class="muted">Modulistica, comunicazioni e modelli reali della cartella Template dell'archivio (M1-M14, C1-C9, modelli).</span></div>
    <button type="button" class="button primary" data-open-doc data-category="Allegato o template" data-doc-title="Aggiungi allegato o template">+ Aggiungi</button>
  </div>
  <div class="document-list">{template_cards}</div>
</section>
<section class="panel">
  <div class="section-head"><h2>Accordi collegati</h2><span class="panel-kicker">Incarichi, patti, NDA</span></div>
  <table class="data-table roomy">
    <thead><tr><th>Persona</th><th>Ruolo</th><th>Accordo</th><th>Ambito</th><th>Stato</th><th>Scadenza</th></tr></thead>
    <tbody>{agreement_rows}</tbody>
  </table>
</section>
{person_modal}
{shareholder_modal}
<div class="assignment-modal" data-doc-modal hidden>
  <section class="assignment-dialog">
    <div class="section-head">
      <div><h2 data-doc-title-h>Aggiungi documento</h2><span class="muted">Caricalo nell'archivio Compagine con la categoria corretta gia' impostata.</span></div>
      <button class="modal-close buttonless" type="button" data-close-doc>x</button>
    </div>
    <form class="form-grid assignment-form" method="post" action="/documents/upload" enctype="multipart/form-data">
      {hidden_ctx(ctx)}
      <input type="hidden" name="origin" value="Compagine">
      <label>Categoria
        <select name="category" data-doc-category>{compagine_doc_category_options}</select>
      </label>
      <label>Data documento
        <input type="date" name="doc_date">
      </label>
      <label>Titolo (opzionale)
        <input name="title" placeholder="Se vuoto, usa il nome del file">
      </label>
      <label class="full-span">Descrizione (cosa contiene, in breve)
        <textarea name="description" rows="2" placeholder="Es. Riscontro a CONSOB sulla prova d'uso; lettera di richiesta integrazioni; ..."></textarea>
      </label>
      <label class="full-span">File
        <input type="file" name="file" required>
      </label>
      <div class="form-actions left full-span">
        <button class="button primary" type="submit">Carica documento</button>
        <button class="button ghost" type="button" data-close-doc>Annulla</button>
      </div>
    </form>
  </section>
</div>
<div class="assignment-modal" data-shareholder-modal hidden>
  <section class="assignment-dialog">
    <div class="section-head">
      <div>
        <h2>Nuovo partecipante qualificato</h2>
        <span class="muted">Censisci societa, persona o veicolo con quota, requisiti e dati per il fascicolo partecipogramma.</span>
      </div>
      <button class="modal-close buttonless" type="button" data-close-shareholder>x</button>
    </div>
    <form class="form-grid assignment-form" method="post" action="/shareholders/create">
      {hidden_ctx(ctx)}
      <label>Tipo soggetto<select name="subject_type">
        <option>Societa / ente</option>
        <option>Persona fisica</option>
        <option>Holding</option>
        <option>Trust / veicolo</option>
      </select></label>
      <label>Nome / ragione sociale<input name="name" required placeholder="es. Gruppo 2DueRighe S.r.l."></label>
      <label>Forma giuridica<input name="legal_form" placeholder="es. S.r.l., S.p.A."></label>
      <label>Codice fiscale / P.IVA<input name="tax_id"></label>
      <label>Email<input name="contact_email"></label>
      <label>Telefono<input name="phone"></label>
      <label>Quota %<input type="number" step="0.01" min="0" max="100" name="stake_percent" required></label>
      <label>Stato requisiti<select name="requisites_status">
        <option>Da verificare</option>
        <option>In verifica</option>
        <option>Completo</option>
        <option>Da integrare</option>
        <option>Non conforme</option>
      </select></label>
      <label class="full-span">Sede / indirizzo<input name="address" placeholder="Sede legale o domicilio rilevante"></label>
      <label class="full-span">Dati soci / titolari effettivi<textarea name="beneficial_owners" rows="3" placeholder="Soci diretti/indiretti, catena partecipativa, titolari effettivi"></textarea></label>
      <label class="full-span">Note<textarea name="notes" rows="2" placeholder="Patti, vincoli, documenti da chiedere, scadenze"></textarea></label>
      <input type="hidden" name="status" value="Attivo">
      <div class="form-actions left full-span">
        <button class="button primary" type="submit">Crea partecipante</button>
        <button class="button ghost" type="button" data-close-shareholder>Annulla</button>
      </div>
    </form>
  </section>
</div>
<div class="assignment-modal" data-assignment-modal hidden>
  <section class="assignment-dialog">
    <div class="section-head">
      <div>
        <h2>Aggiungi soggetto al box</h2>
        <span class="muted">Scegli un soggetto gia' in anagrafica oppure censiscine uno nuovo per questa funzione.</span>
      </div>
      <button class="modal-close buttonless" type="button" data-close-assignment>x</button>
    </div>
    <form class="form-grid assignment-form" method="post" action="/compagine/assignment-save" enctype="multipart/form-data">
      {hidden_ctx(ctx)}
      <input type="hidden" name="function_area" data-assignment-area value="">
      <label class="full-span">Funzione organigramma
        <select name="function_name" data-assignment-function>
          {function_select_options}
        </select>
      </label>
      <div class="full-span linked-subform" data-assignment-new-function hidden>
        <label>Titolo nuova funzione
          <input name="new_function_title" data-assignment-new-title placeholder="es. Responsabile continuita operativa">
        </label>
        <label>Quadrante
          <select name="new_function_group">
            <option>Governance</option>
            <option>Funzioni responsabili</option>
            <option>Area di controllo</option>
            <option>Servizi in outsourcing</option>
            <option>Comitato tecnico progetti</option>
            <option>Advisory Committee</option>
            <option>Area operativa</option>
          </select>
        </label>
      </div>
      <label>Modalita
        <select name="assignment_mode" data-assignment-mode>
          <option value="existing">Soggetto gia' in anagrafica</option>
          <option value="new">Nuovo soggetto</option>
        </select>
      </label>
      <div class="full-span linked-subform subject-mode-panel" data-existing-subject-panel>
        <label class="full-span">Soggetto in anagrafica
          <select name="existing_subject" data-existing-subject>
            {subject_options}
          </select>
        </label>
        <input type="hidden" name="existing_subject_type" data-existing-subject-type value="">
        <div class="subject-preview full-span" data-subject-preview>
          <span>Seleziona un soggetto per caricare i dati gia' censiti.</span>
        </div>
      </div>
      <div class="full-span linked-subform subject-mode-panel" data-new-subject-panel hidden>
        <label>Tipo soggetto
          <select name="subject_type">
            <option>Persona fisica</option>
            <option>Societa / ente</option>
          </select>
        </label>
        <label>Nome / ragione sociale
          <input name="subject_name" placeholder="Nome persona o societa">
        </label>
      </div>
      <label>Ruolo nel rapporto
        <input name="role" placeholder="Responsabile, referente, societa incaricata...">
      </label>
      <label>Data inizio
        <input type="date" name="start_date">
      </label>
      <label>Data fine / scadenza
        <input type="date" name="end_date">
      </label>
      <label class="full-span">Contratto / documento collegato
        <select name="linked_document" data-linked-document>
          {document_options}
        </select>
      </label>
      <div class="full-span linked-subform document-upload-panel" data-new-document-panel hidden>
        <p class="full-span form-hint">Il documento caricato verra' collegato a questa funzione e richiamato nelle sezioni anagrafica, documenti e scadenze collegate.</p>
        <label>Titolo documento
          <input name="new_document_title" placeholder="es. Contratto, delega, incarico">
        </label>
        <label>Tipo documento
          <select name="new_document_type">
            <option>Contratto</option>
            <option>Delega</option>
            <option>Procura</option>
            <option>Lettera di incarico</option>
            <option>Altro documento</option>
          </select>
        </label>
        <label class="full-span">File
          <input type="file" name="new_document_file">
        </label>
      </div>
      <label class="full-span">Note
        <textarea name="notes" placeholder="Contratto, delega, SLA, delibera o condizioni rilevanti"></textarea>
      </label>
      <div class="form-actions left full-span">
        <button class="button primary" type="submit">Collega al box</button>
        <button class="button ghost" type="button" data-close-assignment>Annulla</button>
      </div>
    </form>
  </section>
</div>
<div class="assignment-modal" data-block-modal hidden>
  <section class="assignment-dialog">
    <div class="section-head">
      <div>
        <h2>Nuovo blocco nel quadrante</h2>
        <span class="muted">Aggiunge una funzione/box dentro Governance, Area di controllo, outsourcing o altri gruppi.</span>
      </div>
      <button class="modal-close buttonless" type="button" data-close-block>x</button>
    </div>
    <form class="form-grid assignment-form" method="post" action="/compagine/function-save">
      {hidden_ctx(ctx)}
      <label>Quadrante
        <select name="group_name" data-block-group>
          <option>Governance</option>
          <option>Funzioni responsabili</option>
          <option>Area di controllo</option>
          <option>Servizi in outsourcing</option>
          <option>Comitato tecnico progetti</option>
          <option>Advisory Committee</option>
          <option>Area operativa</option>
          <option>Organigramma operativo</option>
        </select>
      </label>
      <label>Titolo blocco
        <input name="block_title" data-block-title placeholder="es. nuovo presidio governance">
      </label>
      <label class="full-span">Descrizione / presidio
        <textarea name="block_note" data-block-note placeholder="Cosa copre questo blocco e quali fonti documentali servono"></textarea>
      </label>
      <div class="form-actions left full-span">
        <button class="button primary" type="submit">Aggiungi blocco</button>
        <button class="button ghost" type="button" data-close-block>Annulla</button>
      </div>
    </form>
  </section>
</div>
<div class="assignment-modal" data-action-modal hidden>
  <section class="assignment-dialog action-dialog">
    <div class="section-head">
      <div>
        <h2 data-action-title>Collegamento funzione</h2>
        <span class="muted">Persone/societa, funzioni e contratti sono entita separate: qui si modifica solo il collegamento.</span>
      </div>
      <button class="modal-close buttonless" type="button" data-close-action>x</button>
    </div>
    <form class="form-grid assignment-form" method="post" action="/compagine/assignment-save" enctype="multipart/form-data">
      {hidden_ctx(ctx)}
      <input type="hidden" name="subject_name" value="{esc(selected_person or '')}">
      <input type="hidden" name="subject_type" value="{esc(selected_subject_type)}">
      <input type="hidden" name="function_area" data-action-function-area value="">
      <div class="taxonomy-strip full-span">
        <div><span>Area</span><strong data-action-area>Da organigramma</strong></div>
        <div><span>Funzione</span><strong data-action-function-label>Seleziona funzione</strong></div>
        <div><span>Soggetto</span><strong>{esc(selected_person or 'Soggetto selezionato')}</strong></div>
        <div><span>Contratto</span><strong>Uno o piu funzioni</strong></div>
      </div>
      <p class="form-hint full-span">Il soggetto non si modifica da questa finestra perche sei gia' nel suo profilo. Se devi correggere anagrafica o tipo soggetto, usa la scheda anagrafica.</p>
      <label class="full-span">Funzione organigramma
        <select name="function_name" data-action-function-select>
          {function_select_options}
        </select>
      </label>
      <div class="full-span linked-subform" data-action-new-function hidden>
        <label>Titolo nuova funzione
          <input name="new_function_title" data-action-new-title placeholder="es. Responsabile continuita operativa">
        </label>
        <label>Quadrante organigramma
          <select name="new_function_group">
            <option>Governance</option>
            <option>Funzioni responsabili</option>
            <option>Area di controllo</option>
            <option>Servizi in outsourcing</option>
            <option>Comitato tecnico progetti</option>
            <option>Advisory Committee</option>
            <option>Area operativa</option>
          </select>
        </label>
      </div>
      <label>Ruolo / responsabilita
        <input name="role" placeholder="es. responsabile, referente, societa incaricata">
      </label>
      <label>Data inizio
        <input type="date" name="start_date">
      </label>
      <label>Data fine / scadenza
        <input type="date" name="end_date">
      </label>
      <label class="full-span">Contratto / documento collegato
        <select name="linked_document" data-action-linked-document>
          {document_options}
        </select>
      </label>
      <div class="full-span linked-subform document-upload-panel" data-action-new-document hidden>
        <p class="full-span form-hint">Il nuovo documento verra' censito come fonte della funzione e potra' disciplinare anche altre funzioni.</p>
        <label>Titolo documento
          <input name="new_document_title" placeholder="es. Lettera di incarico, contratto quadro, delega">
        </label>
        <label>Tipo documento
          <select name="new_document_type">
            <option>Contratto</option>
            <option>Lettera di incarico</option>
            <option>Delega</option>
            <option>Procura</option>
            <option>Altro documento</option>
          </select>
        </label>
        <label class="full-span">File
          <input type="file" name="new_document_file">
        </label>
      </div>
      <label class="full-span">Note operative
        <textarea placeholder="Note sul collegamento: perimetro della funzione, limiti, deleghe, SLA o condizioni rilevanti."></textarea>
      </label>
      <div class="form-actions left full-span">
        <button class="button primary" type="submit">Salva collegamento</button>
        <button class="button ghost" type="button" data-close-action>Annulla</button>
      </div>
    </form>
  </section>
</div>
<script>
const orgMap = document.querySelector('.responsibility-map');
let draggedGroup = null;
const assignmentModal = document.querySelector('[data-assignment-modal]');
const assignmentFunctionInput = document.querySelector('[data-assignment-function]');
const assignmentAreaInput = document.querySelector('[data-assignment-area]');
const assignmentNewFunctionFields = document.querySelector('[data-assignment-new-function]');
const assignmentNewFunctionTitle = document.querySelector('[data-assignment-new-title]');
const assignmentMode = document.querySelector('[data-assignment-mode]');
const existingSubjectPanel = document.querySelector('[data-existing-subject-panel]');
const newSubjectPanel = document.querySelector('[data-new-subject-panel]');
const existingSubjectSelect = document.querySelector('[data-existing-subject]');
const existingSubjectTypeInput = document.querySelector('[data-existing-subject-type]');
const subjectPreview = document.querySelector('[data-subject-preview]');
const linkedDocumentSelect = document.querySelector('[data-linked-document]');
const newDocumentPanel = document.querySelector('[data-new-document-panel]');
const shareholderModal = document.querySelector('[data-shareholder-modal]');
const blockModal = document.querySelector('[data-block-modal]');
const blockGroupInput = document.querySelector('[data-block-group]');
const blockTitleInput = document.querySelector('[data-block-title]');
const blockNoteInput = document.querySelector('[data-block-note]');
const actionModal = document.querySelector('[data-action-modal]');
const actionTitle = document.querySelector('[data-action-title]');
const actionName = document.querySelector('[data-action-name]');
const actionFunctionSelect = document.querySelector('[data-action-function-select]');
const actionNewFunctionFields = document.querySelector('[data-action-new-function]');
const actionNewFunctionTitle = document.querySelector('[data-action-new-title]');
const actionArea = document.querySelector('[data-action-area]');
const actionFunctionLabel = document.querySelector('[data-action-function-label]');
const actionFunctionAreaInput = document.querySelector('[data-action-function-area]');
const actionLinkedDocument = document.querySelector('[data-action-linked-document]');
const actionNewDocumentPanel = document.querySelector('[data-action-new-document]');

function cssKindForGroup(groupName) {{
  const normalized = (groupName || '').toLowerCase();
  if (normalized.includes('governance')) return 'governance';
  if (normalized.includes('controllo')) return 'control';
  if (normalized.includes('outsourcing')) return 'outsourcing';
  if (normalized.includes('comitato tecnico')) return 'committee';
  if (normalized.includes('advisory')) return 'advisory';
  if (normalized.includes('operativa')) return 'operational';
  return 'function';
}}

function openAssignment(functionName) {{
  if (assignmentModal) assignmentModal.hidden = false;
  setFunctionChoice(assignmentFunctionInput, functionName || '', assignmentNewFunctionTitle, assignmentNewFunctionFields);
  setAssignmentMode('existing');
  setDocumentMode();
  if (assignmentFunctionInput) assignmentFunctionInput.focus();
}}

function attachNodeActions(node) {{
  const addButton = node.querySelector('[data-open-assignment]');
  if (addButton) {{
    addButton.addEventListener('click', () => openAssignment(addButton.dataset.function || ''));
  }}
  const deleteButton = node.querySelector('[data-delete-block]');
  if (deleteButton) {{
    deleteButton.addEventListener('click', () => {{
      const title = deleteButton.dataset.function || 'questo blocco';
      if (window.confirm(`Eliminare il blocco "${{title}}" dall'organigramma?`)) {{
        node.classList.add('is-archived-demo');
      }}
    }});
  }}
}}

function createEmptyResponsibilityNode(title, groupName, note) {{
  const node = document.createElement('div');
  node.className = `responsibility-node ${{cssKindForGroup(groupName)}} empty`;

  const top = document.createElement('div');
  top.className = 'node-topline';

  const label = document.createElement('span');
  label.textContent = title;

  const actions = document.createElement('div');
  actions.className = 'node-actions';
  actions.setAttribute('aria-label', `Azioni ${{title}}`);
  const addButton = document.createElement('button');
  addButton.type = 'button';
  addButton.title = 'Aggiungi soggetto';
  addButton.textContent = '+';
  addButton.dataset.openAssignment = '';
  addButton.dataset.function = title;
  actions.appendChild(addButton);

  top.appendChild(label);
  top.appendChild(actions);

  const owner = document.createElement('strong');
  const empty = document.createElement('span');
  empty.className = 'empty-owner';
  empty.textContent = 'da censire';
  owner.appendChild(empty);

  const small = document.createElement('small');
  small.textContent = note || 'Blocco creato nel quadrante: collegare anagrafiche, documenti e scadenze.';

  const bottom = document.createElement('div');
  bottom.className = 'node-bottom-actions';
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.title = 'Elimina blocco';
  remove.textContent = '-';
  remove.dataset.deleteBlock = '';
  remove.dataset.function = title;
  bottom.appendChild(remove);

  node.appendChild(top);
  node.appendChild(owner);
  node.appendChild(small);
  node.appendChild(bottom);
  attachNodeActions(node);
  return node;
}}

function setFunctionChoice(select, value, newTitleInput, newFields) {{
  if (!select) return;
  const hasOption = Array.from(select.options).some((option) => option.value === value);
  if (value && hasOption) {{
    select.value = value;
  }} else if (value) {{
    select.value = '__new__';
    if (newTitleInput) newTitleInput.value = value;
  }} else {{
    select.value = '';
  }}
  if (newFields) newFields.hidden = select.value !== '__new__';
  if (select === assignmentFunctionInput) syncAssignmentFunctionArea();
}}

function addFunctionOption(title, groupName) {{
  document.querySelectorAll('[data-assignment-function], [data-action-function-select]').forEach((select) => {{
    const exists = Array.from(select.options).some((option) => option.value === title);
    if (exists) return;
    const option = document.createElement('option');
    option.value = title;
    option.textContent = `${{title}} - ${{groupName}}`;
    const newOption = Array.from(select.options).find((item) => item.value === '__new__');
    select.insertBefore(option, newOption || null);
  }});
  const dataList = document.getElementById('org-function-options');
  if (dataList && !Array.from(dataList.options).some((option) => option.value === title)) {{
    const option = document.createElement('option');
    option.value = title;
    option.textContent = groupName;
    dataList.appendChild(option);
  }}
}}

if (assignmentFunctionInput) {{
  assignmentFunctionInput.addEventListener('change', () => {{
    if (assignmentNewFunctionFields) assignmentNewFunctionFields.hidden = assignmentFunctionInput.value !== '__new__';
    syncAssignmentFunctionArea();
  }});
}}

function selectedAreaFrom(select, fallback) {{
  if (!select) return fallback || '';
  const selected = select.selectedOptions[0];
  if (select.value === '__new__') {{
    const groupSelect = select.closest('form')?.querySelector('select[name="new_function_group"]');
    return groupSelect?.value || fallback || 'Funzioni responsabili';
  }}
  return selected?.dataset.area || (selected?.textContent || '').split(' - ').slice(1).join(' - ') || fallback || '';
}}

function syncAssignmentFunctionArea() {{
  if (assignmentAreaInput) assignmentAreaInput.value = selectedAreaFrom(assignmentFunctionInput, 'Funzioni responsabili');
}}

document.querySelectorAll('select[name="new_function_group"]').forEach((select) => {{
  select.addEventListener('change', () => {{
    syncAssignmentFunctionArea();
    syncActionTaxonomy();
  }});
}});

function renderSubjectPreview() {{
  if (!subjectPreview || !existingSubjectSelect) return;
  const option = existingSubjectSelect.selectedOptions[0];
  if (!option || !option.value) {{
    if (existingSubjectTypeInput) existingSubjectTypeInput.value = '';
    subjectPreview.innerHTML = '<span>Seleziona un soggetto per caricare i dati gia\\' censiti.</span>';
    return;
  }}
  if (existingSubjectTypeInput) existingSubjectTypeInput.value = option.dataset.type || 'Persona fisica';
  subjectPreview.innerHTML = `
    <dl>
      <dt>Tipo</dt><dd>${{option.dataset.type || '-'}}</dd>
      <dt>Funzioni attuali</dt><dd>${{option.dataset.functions || '-'}}</dd>
      <dt>Accordi / contratti</dt><dd>${{option.dataset.agreements || '0'}}</dd>
      <dt>Documenti</dt><dd>${{option.dataset.documents || '0'}}</dd>
      <dt>Prossima scadenza</dt><dd>${{option.dataset.deadline || '-'}}</dd>
      <dt>Stato</dt><dd>${{option.dataset.status || '-'}}</dd>
    </dl>`;
}}

function setAssignmentMode(mode) {{
  const isNew = mode === 'new';
  if (existingSubjectPanel) existingSubjectPanel.hidden = isNew;
  if (newSubjectPanel) newSubjectPanel.hidden = !isNew;
  if (assignmentMode) assignmentMode.value = isNew ? 'new' : 'existing';
  if (!isNew) renderSubjectPreview();
}}

if (assignmentMode) {{
  assignmentMode.addEventListener('change', () => setAssignmentMode(assignmentMode.value));
}}

if (existingSubjectSelect) {{
  existingSubjectSelect.addEventListener('change', renderSubjectPreview);
}}

function setDocumentMode() {{
  if (!newDocumentPanel || !linkedDocumentSelect) return;
  newDocumentPanel.hidden = linkedDocumentSelect.value !== '__upload__';
}}

if (linkedDocumentSelect) {{
  linkedDocumentSelect.addEventListener('change', setDocumentMode);
}}

function setActionFunctionChoice(value) {{
  if (!actionFunctionSelect) return;
  const hasOption = Array.from(actionFunctionSelect.options).some((option) => option.value === value);
  if (value && hasOption) {{
    actionFunctionSelect.value = value;
  }} else if (value) {{
    actionFunctionSelect.value = '__new__';
    if (actionNewFunctionTitle) actionNewFunctionTitle.value = value;
  }} else {{
    actionFunctionSelect.value = '';
  }}
  if (actionNewFunctionFields) actionNewFunctionFields.hidden = actionFunctionSelect.value !== '__new__';
  syncActionTaxonomy();
}}

if (actionFunctionSelect) {{
  actionFunctionSelect.addEventListener('change', () => {{
    if (actionNewFunctionFields) actionNewFunctionFields.hidden = actionFunctionSelect.value !== '__new__';
    syncActionTaxonomy();
  }});
}}

function syncActionTaxonomy() {{
  if (!actionFunctionSelect) return;
  const selected = actionFunctionSelect.selectedOptions[0];
  const text = selected?.textContent || '';
  const parts = text.split(' - ');
  const fn = actionFunctionSelect.value === '__new__'
    ? (actionNewFunctionTitle?.value || 'Nuova funzione')
    : (parts[0] || 'Seleziona funzione');
  const area = actionFunctionSelect.value === '__new__'
    ? selectedAreaFrom(actionFunctionSelect, 'Nuova area da scegliere')
    : (selected?.dataset.area || parts.slice(1).join(' - ') || 'Da organigramma');
  if (actionFunctionLabel) actionFunctionLabel.textContent = fn;
  if (actionArea) actionArea.textContent = area;
  if (actionFunctionAreaInput) actionFunctionAreaInput.value = area;
}}

if (actionNewFunctionTitle) {{
  actionNewFunctionTitle.addEventListener('input', syncActionTaxonomy);
}}

function setActionDocumentMode() {{
  if (!actionNewDocumentPanel || !actionLinkedDocument) return;
  actionNewDocumentPanel.hidden = actionLinkedDocument.value !== '__upload__';
}}

if (actionLinkedDocument) {{
  actionLinkedDocument.addEventListener('change', setActionDocumentMode);
}}

document.querySelectorAll('[data-open-action]').forEach((button) => {{
  button.addEventListener('click', () => {{
    const action = button.dataset.action || 'Collegamento funzione';
    if (actionTitle) actionTitle.textContent = action;
    if (actionName) actionName.value = action;
    setActionFunctionChoice(button.dataset.function || '');
    const form = actionModal?.querySelector('form');
    if (form) {{
      const roleInput = form.querySelector('input[name="role"]');
      const startInput = form.querySelector('input[name="start_date"]');
      const endInput = form.querySelector('input[name="end_date"]');
      if (roleInput) roleInput.value = button.dataset.role || '';
      if (startInput) startInput.value = button.dataset.start || '';
      if (endInput) endInput.value = button.dataset.end || '';
      if (actionLinkedDocument) {{
        const docValue = button.dataset.document || '';
        const hasDocumentOption = Array.from(actionLinkedDocument.options).some((option) => option.value === docValue);
        actionLinkedDocument.value = hasDocumentOption ? docValue : '';
      }}
    }}
    setActionDocumentMode();
    if (actionModal) actionModal.hidden = false;
  }});
}});

document.querySelectorAll('[data-close-action]').forEach((button) => {{
  button.addEventListener('click', () => {{
    if (actionModal) actionModal.hidden = true;
  }});
}});

if (actionModal) {{
  actionModal.addEventListener('click', (event) => {{
    if (event.target === actionModal) actionModal.hidden = true;
  }});
}}

document.querySelectorAll('[data-confirm-action]').forEach((button) => {{
  button.addEventListener('click', () => {{
    const action = button.dataset.action || 'procedere';
    const fn = button.dataset.function || 'questa funzione';
    if (window.confirm(`Confermi di ${{action}} "${{fn}}"?`)) {{
      button.closest('li')?.classList.add('is-archived-demo');
    }}
  }});
}});

function clearDropHints() {{
  document.querySelectorAll('.responsibility-group.drop-before, .responsibility-group.drop-after').forEach((group) => {{
    group.classList.remove('drop-before', 'drop-after');
  }});
}}

function closestDropTarget(x, y) {{
  const groups = Array.from(orgMap.querySelectorAll('.responsibility-group:not(.dragging)'));
  if (!groups.length) return {{ target: null, after: false }};
  return groups.reduce((best, group) => {{
    const box = group.getBoundingClientRect();
    const centerX = box.left + box.width / 2;
    const centerY = box.top + box.height / 2;
    const distance = Math.hypot(x - centerX, y - centerY);
    const after = y > centerY || (Math.abs(y - centerY) < box.height * 0.22 && x > centerX);
    return distance < best.distance ? {{ target: group, after, distance }} : best;
  }}, {{ target: null, after: false, distance: Number.POSITIVE_INFINITY }});
}}

document.querySelectorAll('.drag-handle').forEach((handle) => {{
  handle.addEventListener('dragstart', (event) => {{
    draggedGroup = handle.closest('.responsibility-group');
    draggedGroup.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', draggedGroup.querySelector('h3').textContent);
  }});
  handle.addEventListener('dragend', () => {{
    if (draggedGroup) draggedGroup.classList.remove('dragging');
    draggedGroup = null;
    clearDropHints();
  }});
}});

if (orgMap) {{
  orgMap.addEventListener('dragover', (event) => {{
    if (!draggedGroup) return;
    event.preventDefault();
    clearDropHints();
    const drop = closestDropTarget(event.clientX, event.clientY);
    if (drop.target) {{
      drop.target.classList.add(drop.after ? 'drop-after' : 'drop-before');
    }}
  }});
  orgMap.addEventListener('drop', (event) => {{
    if (!draggedGroup) return;
    event.preventDefault();
    const drop = closestDropTarget(event.clientX, event.clientY);
    clearDropHints();
    if (!drop.target) {{
      orgMap.appendChild(draggedGroup);
    }} else if (drop.after) {{
      orgMap.insertBefore(draggedGroup, drop.target.nextSibling);
    }} else {{
      orgMap.insertBefore(draggedGroup, drop.target);
    }}
  }});
}}

document.querySelectorAll('[data-open-assignment]').forEach((button) => {{
  button.addEventListener('click', () => openAssignment(button.dataset.function || ''));
}});

document.querySelectorAll('[data-open-block]').forEach((button) => {{
  button.addEventListener('click', () => {{
    if (blockModal) blockModal.hidden = false;
    if (blockGroupInput) {{
      blockGroupInput.value = button.dataset.group || 'Governance';
      blockGroupInput.focus();
    }}
    if (blockTitleInput) blockTitleInput.value = '';
    if (blockNoteInput) blockNoteInput.value = '';
  }});
}});

document.querySelectorAll('[data-close-block]').forEach((button) => {{
  button.addEventListener('click', () => {{
    if (blockGroupInput) blockGroupInput.disabled = false;
    if (blockModal) blockModal.hidden = true;
  }});
}});

document.querySelectorAll('[data-save-block]').forEach((button) => {{
  button.addEventListener('click', () => {{
    const groupName = blockGroupInput?.value || 'Governance';
    const title = (blockTitleInput?.value || '').trim();
    const note = (blockNoteInput?.value || '').trim();
    if (!title) {{
      window.alert('Inserisci il titolo del blocco.');
      blockTitleInput?.focus();
      return;
    }}
    const targetGroup = Array.from(document.querySelectorAll('.responsibility-group')).find((group) => group.dataset.groupName === groupName);
    const targetList = targetGroup?.querySelector('.responsibility-list');
    if (!targetList) {{
      window.alert('Seleziona un quadrante valido dell organigramma.');
      return;
    }}
    targetList.appendChild(createEmptyResponsibilityNode(title, groupName, note));
    addFunctionOption(title, groupName);
    if (blockGroupInput) blockGroupInput.disabled = false;
    if (blockModal) blockModal.hidden = true;
  }});
}});

if (blockModal) {{
  blockModal.addEventListener('click', (event) => {{
    if (event.target === blockModal) blockModal.hidden = true;
  }});
}}

document.querySelectorAll('[data-delete-block]').forEach((button) => {{
  button.addEventListener('click', () => {{
    const title = button.dataset.function || 'questo blocco';
    if (window.confirm(`Eliminare il blocco "${{title}}" dall'organigramma?`)) {{
      button.closest('.responsibility-node')?.classList.add('is-archived-demo');
    }}
  }});
}});

document.querySelectorAll('[data-close-assignment]').forEach((button) => {{
  button.addEventListener('click', () => {{
    if (assignmentModal) assignmentModal.hidden = true;
  }});
}});

if (assignmentModal) {{
  assignmentModal.addEventListener('click', (event) => {{
    if (event.target === assignmentModal) assignmentModal.hidden = true;
  }});
}}

document.querySelectorAll('[data-open-shareholder]').forEach((button) => {{
  button.addEventListener('click', () => {{
    if (shareholderModal) shareholderModal.hidden = false;
    shareholderModal?.querySelector('input[name="name"]')?.focus();
  }});
}});

document.querySelectorAll('[data-close-shareholder]').forEach((button) => {{
  button.addEventListener('click', () => {{
    if (shareholderModal) shareholderModal.hidden = true;
  }});
}});

if (shareholderModal) {{
  shareholderModal.addEventListener('click', (event) => {{
    if (event.target === shareholderModal) shareholderModal.hidden = true;
  }});
}}
</script>
"""
        if active_tab == "anagrafiche" and selected_person:
            selected_status = "Attivo" if selected_person_functions else "Da censire"
            selected_next_deadline = next((a["expires_at"] for a in agreements if a["person_name"] == selected_person and a["expires_at"]), "")
            body = f"""
<p class="page-copy">Scheda anagrafica operativa con funzioni, accordi, documenti e scadenze collegate all'organigramma.</p>
<nav class="subtabs">{tab_html}</nav>
<section class="deal-header">
  <div>
    <p class="eyebrow">CRM organigramma</p>
    <h2>{esc(selected_person)}</h2>
    <p class="muted">{esc(', '.join(selected_person_functions) or 'Nessuna funzione assegnata')}</p>
  </div>
  <div class="header-badges">
    <span class="badge {badge_class(selected_status)}">{esc(selected_status)}</span>
    <span class="badge neutral">{len(selected_person_functions)} funzioni</span>
    <button class="button primary" type="button" data-open-action data-action="Modifica anagrafica">Modifica anagrafica</button>
    <a class="button ghost" href="{rel_url('/compagine', ctx, {'tab': 'anagrafiche'})}">Torna alla lista</a>
  </div>
</section>
<section class="metric-grid">
  <div class="metric"><span>Funzioni</span><strong>{len(selected_person_functions)}</strong></div>
  <div class="metric"><span>Accordi</span><strong>{person_agreement_counts.get(selected_person, 0)}</strong></div>
  <div class="metric"><span>Documenti</span><strong>{person_document_counts.get(selected_person, 0)}</strong></div>
  <div class="metric"><span>Prossima scadenza</span><strong>{esc(nice_date(selected_next_deadline) or '-')}</strong></div>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Profilo anagrafico</h2><span class="panel-kicker">persona</span></div>
    <dl class="definition-list">
      <dt>Nome</dt><dd>{esc(selected_first_name or '-')}</dd>
      <dt>Cognome</dt><dd>{esc(selected_last_name or '-')}</dd>
      <dt>Email</dt><dd>-</dd>
      <dt>Telefono</dt><dd>-</dd>
      <dt>Stato</dt><dd>{esc(selected_status)}</dd>
      <dt>Note</dt><dd>Da completare nella scheda anagrafica.</dd>
    </dl>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Azioni operative</h2><span class="panel-kicker">funzioni e documenti</span></div>
    <div class="inline-actions left">
      <button class="button primary" type="button" data-open-action data-action="Aggiungi funzione">+ Aggiungi funzione</button>
      <button class="button ghost" type="button" data-open-action data-action="Collega documento">+ Collega documento</button>
      <button class="button secondary" type="button" data-open-action data-action="Collega accordo">+ Collega accordo</button>
    </div>
  </div>
</section>
<section class="panel">
  <div class="section-head"><h2>Funzioni in organigramma</h2><span class="panel-kicker">inizio, fine, documenti</span></div>
  <ul class="assignment-list">{selected_person_function_rows}</ul>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Accordi / contratti collegati</h2><span class="panel-kicker">scadenze</span></div>
    <table class="data-table compact">
      <thead><tr><th>Accordo</th><th>Funzioni / ambito</th><th>Stato</th><th>Scadenza</th></tr></thead>
      <tbody>{selected_agreement_rows}</tbody>
    </table>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Documenti collegati</h2><button class="button ghost" type="button" data-open-action data-action="Nuovo documento">+ Documento</button></div>
    <div class="document-list">{person_doc_rows}</div>
  </div>
</section>
{cv_firma_panel}
<div class="assignment-modal" data-action-modal hidden>
  <section class="assignment-dialog action-dialog">
    <div class="section-head">
      <div>
        <h2 data-action-title>Azione soggetto</h2>
        <span class="muted">Popup demo: qui andranno modifica dati, collegamento documenti, funzioni e accordi.</span>
      </div>
      <button class="modal-close buttonless" type="button" data-close-action>x</button>
    </div>
    <form class="form-grid assignment-form">
      <label>Azione
        <input data-action-name value="" placeholder="Azione selezionata">
      </label>
      <label>Soggetto
        <input value="{esc(selected_person)}">
      </label>
      <label class="full-span">Funzione organigramma
        <select name="function_name" data-action-function-select>
          {function_select_options}
        </select>
      </label>
      <div class="full-span linked-subform" data-action-new-function hidden>
        <label>Titolo nuova funzione
          <input name="new_function_title" data-action-new-title placeholder="es. Responsabile continuita operativa">
        </label>
        <label>Quadrante organigramma
          <select name="new_function_group">
            <option>Governance</option>
            <option>Funzioni responsabili</option>
            <option>Area di controllo</option>
            <option>Servizi in outsourcing</option>
            <option>Comitato tecnico progetti</option>
            <option>Advisory Committee</option>
            <option>Area operativa</option>
          </select>
        </label>
      </div>
      <label>Ruolo / responsabilita
        <input name="role" placeholder="es. responsabile, referente, societa incaricata">
      </label>
      <label>Data inizio
        <input type="date" name="start_date">
      </label>
      <label>Data fine / scadenza
        <input type="date" name="end_date">
      </label>
      <label class="full-span">Documento collegato alla funzione
        <select name="linked_document">
          {document_options}
        </select>
      </label>
      <label>Alert scadenza
        <select name="deadline_alert">
          <option>60 giorni prima</option>
          <option>30 giorni prima</option>
          <option>15 giorni prima</option>
          <option>Nessun alert</option>
        </select>
      </label>
      <label class="full-span">Note operative
        <textarea placeholder="Cosa copre la funzione, quale documento prova il collegamento e per quali comunicazioni o controlli serve."></textarea>
      </label>
      <div class="form-actions left full-span">
        <button class="button primary" type="button" data-close-action>Salva collegamento</button>
        <button class="button ghost" type="button" data-close-action>Annulla</button>
      </div>
    </form>
  </section>
</div>
<script>
const actionModal = document.querySelector('[data-action-modal]');
const actionTitle = document.querySelector('[data-action-title]');
const actionName = document.querySelector('[data-action-name]');
const actionFunctionSelect = document.querySelector('[data-action-function-select]');
const actionNewFunctionFields = document.querySelector('[data-action-new-function]');
const actionNewFunctionTitle = document.querySelector('[data-action-new-title]');

function setActionFunctionChoice(value) {{
  if (!actionFunctionSelect) return;
  const hasOption = Array.from(actionFunctionSelect.options).some((option) => option.value === value);
  if (value && hasOption) {{
    actionFunctionSelect.value = value;
  }} else if (value) {{
    actionFunctionSelect.value = '__new__';
    if (actionNewFunctionTitle) actionNewFunctionTitle.value = value;
  }} else {{
    actionFunctionSelect.value = '';
  }}
  if (actionNewFunctionFields) actionNewFunctionFields.hidden = actionFunctionSelect.value !== '__new__';
}}

if (actionFunctionSelect) {{
  actionFunctionSelect.addEventListener('change', () => {{
    if (actionNewFunctionFields) actionNewFunctionFields.hidden = actionFunctionSelect.value !== '__new__';
  }});
}}

document.querySelectorAll('[data-open-action]').forEach((button) => {{
  button.addEventListener('click', () => {{
    const action = button.dataset.action || 'Azione soggetto';
    if (actionTitle) actionTitle.textContent = action;
    if (actionName) actionName.value = action;
    setActionFunctionChoice(button.dataset.function || '');
    if (actionModal) actionModal.hidden = false;
  }});
}});

document.querySelectorAll('[data-close-action]').forEach((button) => {{
  button.addEventListener('click', () => {{
    if (actionModal) actionModal.hidden = true;
  }});
}});

if (actionModal) {{
  actionModal.addEventListener('click', (event) => {{
    if (event.target === actionModal) actionModal.hidden = true;
  }});
}}

document.querySelectorAll('[data-confirm-action]').forEach((button) => {{
  button.addEventListener('click', () => {{
    const action = button.dataset.action || 'procedere';
    const fn = button.dataset.function || 'questa funzione';
    if (window.confirm(`Confermi di ${{action}} "${{fn}}"?`)) {{
      button.closest('li')?.classList.add('is-archived-demo');
    }}
  }});
}});
</script>
"""
        elif active_tab == "anagrafiche":
            body = f"""
<p class="page-copy">Anagrafica unica dei soggetti collegati all'organigramma: persone fisiche, societa ed enti possono coprire qualunque funzione, con accordi, documenti e scadenze collegati.</p>
<nav class="subtabs">{tab_html}</nav>
<section class="metric-grid">
  <div class="metric"><span>Soggetti</span><strong>{len(subject_entries)}</strong></div>
  <div class="metric"><span>Persone fisiche</span><strong>{len(person_registry)}</strong></div>
  <div class="metric"><span>Societa / enti</span><strong>{len(supplier_registry)}</strong></div>
  <div class="metric"><span>Accordi</span><strong>{len(agreements)}</strong></div>
</section>
<section class="panel">
  <div class="section-head">
    <div><h2>Lista soggetti</h2><span class="muted">Da qui si censiscono natura del soggetto, funzioni collegate, accordi, documenti e scadenze operative.</span></div>
    <button class="button primary" type="button">+ Nuovo</button>
  </div>
  <div class="table-scroll">
    <table class="data-table compact directory-table">
      <thead><tr><th>Soggetto</th><th>Funzioni</th><th>Accordi / contratti</th><th>Documenti</th><th>Scadenza</th><th>Stato</th><th></th></tr></thead>
      <tbody>{subject_directory}</tbody>
    </table>
  </div>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Censisci soggetto / collega accordo</h2><span class="panel-kicker">design flusso</span></div>
    <form class="form-grid">
      <label>Tipo soggetto<select><option>Persona fisica</option><option>Societa / ente</option></select></label>
      <label>Soggetto<input placeholder="nome persona o ragione sociale"></label>
      <label>Tipo accordo<select><option>Lettera di incarico</option><option>NDA</option><option>Contratto fornitore</option><option>Delega</option><option>SLA</option><option>Altro</option></select></label>
      <label>Ruolo nel rapporto<input placeholder="es. responsabile, societa incaricata, advisor"></label>
      <label class="full-span">Funzioni coperte<input list="org-function-options" placeholder="es. Compliance interna, Risk control, Reclami"></label>
      <label>Data decorrenza<input type="date"></label>
      <label>Scadenza<input type="date"></label>
      <label class="full-span">Documento / contratto<input type="file"></label>
      <div class="form-actions"><button class="button primary" type="button">Collega accordo</button></div>
    </form>
    <datalist id="org-function-options">{function_options}</datalist>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Scadenze accordi</h2><span class="panel-kicker">alert</span></div>
    <table class="data-table compact">
      <thead><tr><th>Soggetto</th><th>Accordo</th><th>Funzioni / ambito</th><th>Scadenza</th></tr></thead>
      <tbody>{anagraphic_agreement_rows}</tbody>
    </table>
  </div>
</section>
{person_modal}
"""
        self.render("Compagine - organigramma - partecipogramma", body, "compagine")

    def page_governance(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        meetings = rows(
            """
            SELECT bm.*, doc.title AS minutes_title
            FROM board_meetings bm
            LEFT JOIN documents doc ON doc.id = bm.minutes_document_id
            WHERE bm.platform_id = ?
            ORDER BY bm.meeting_date DESC
            """,
            (pid,),
        )
        board_members = rows(
            "SELECT * FROM committee_members WHERE platform_id = ? AND committee = 'CdA' AND active = 1 ORDER BY role, name",
            (pid,),
        )
        decisions = rows(
            """
            SELECT bd.*, d.title AS deal_title, doc.title AS document_title
            FROM board_decisions bd
            JOIN deals d ON d.id = bd.deal_id
            LEFT JOIN documents doc ON doc.id = bd.generated_document_id
            WHERE d.platform_id = ?
            ORDER BY bd.created_at DESC
            """,
            (pid,),
        )
        params = parse_qs(urlparse(self.path).query)
        active_tab = (params.get("tab") or ["convocazioni"])[0]
        if active_tab not in {"convocazioni", "sedute", "membri"}:
            active_tab = "convocazioni"
        mode = (params.get("mode") or [""])[0]
        selected_meeting_id = int((params.get("meeting") or [0])[0] or 0)
        selected_meeting = None if mode == "new" else next((m for m in meetings if m["id"] == selected_meeting_id), None)
        if not selected_meeting and mode != "new":
            selected_meeting = meetings[0] if meetings else None
        selected_member = board_members[0] if board_members else None
        selected_date = nice_date(selected_meeting["meeting_date"]) if selected_meeting else ""
        selected_title = selected_meeting["title"] if selected_meeting else "CdA - (data da definire)"
        selected_agenda = selected_meeting["agenda"] if selected_meeting else ""
        selected_link = selected_meeting["meeting_link"] if selected_meeting else ""
        invited_count = len(board_members)
        board_emails = ",".join(m["email"] for m in board_members)
        tabs = [
            ("convocazioni", "Convocazioni"),
            ("sedute", "Sedute & verbali"),
            ("membri", "Membri CdA"),
        ]
        tab_html = "".join(
            f'<a class="subtab {"active" if key == active_tab else ""}" href="{rel_url("/governance", ctx, {"tab": key})}">{label}</a>'
            for key, label in tabs
        )
        new_convocation_item = (
            '<a class="record-item active" href="#"><strong>CdA - (data da definire)</strong><span>Mista - ore 18:00 - 0 invitati</span><small>Bozza</small></a>'
            if active_tab == "convocazioni" and mode == "new"
            else ""
        )
        meeting_items = "".join(
            f"""<a class="record-item {"active" if selected_meeting and m['id'] == selected_meeting['id'] else ""}" href="{rel_url('/governance', ctx, {'tab': 'convocazioni', 'meeting': m['id']})}">
              <strong>{esc(m['title'] or 'CdA - (data da definire)')}</strong>
              <span>{esc('Mista')} - {esc(nice_date(m['meeting_date']))} - {len(board_members)} invitati</span>
              <small>{esc(m['status'])}</small>
            </a>"""
            for idx, m in enumerate(meetings)
        )
        meeting_items = new_convocation_item + (meeting_items or '<p class="empty-state">Nessuna convocazione.</p>')
        new_seduta_item = (
            '<a class="record-item active" href="#"><strong>(senza titolo)</strong><span>Seduta ordinaria - data da definire</span><small>Bozza</small></a>'
            if active_tab == "sedute" and mode == "new"
            else ""
        )
        seduta_items = "".join(
            f"""<a class="record-item {"active" if selected_meeting and m['id'] == selected_meeting['id'] else ""}" href="{rel_url('/governance', ctx, {'tab': 'sedute', 'meeting': m['id']})}">
              <strong>{esc(m['title'] or '(senza titolo)')}</strong>
              <span>Seduta ordinaria - {esc(nice_date(m['meeting_date']))}</span>
              <small>{esc(m['status'])}</small>
            </a>"""
            for idx, m in enumerate(meetings)
        )
        seduta_items = new_seduta_item + (seduta_items or '<p class="empty-state">Nessuna seduta.</p>')
        member_items = "".join(
            f"""<div class="record-item {"active" if idx == 0 else ""}">
              <strong>{esc(m['name'])}</strong>
              <span>{esc(m['role'])}</span>
              <small>{esc(m['email'])}</small>
            </div>"""
            for idx, m in enumerate(board_members)
        ) or '<p class="empty-state">Nessun membro in rubrica.</p>'
        invite_warning = (
            f"<p class=\"muted\">{invited_count} membri in rubrica CdA pronti per la convocazione.</p>"
            if invited_count
            else '<p class="danger-text">Nessun membro in rubrica. Aggiungili in "Membri CdA" per poterli convocare.</p>'
        )
        convocation_text = f"""Oggetto: Convocazione del Consiglio di Amministrazione di {ctx['platform']['name']} - {selected_date or '[data]'}

Egregi Consiglieri,
con la presente si convoca il Consiglio di Amministrazione di {ctx['platform']['name']}, che si terra il giorno {selected_date or '[data]'} alle ore 18:00, presso la sede legale, con possibilita di collegamento da remoto al link: {selected_link or '[link riunione]'}, per discutere e deliberare sul seguente

ORDINE DEL GIORNO
{selected_agenda or '1. [punto all ordine del giorno]'}

Si prega di confermare la propria presenza.
"""
        mail_subject = f"Convocazione CdA {ctx['platform']['name']} - {selected_date or '[data]'}"
        mailto_url = f"mailto:{quote(board_emails, safe='@,.')}?subject={quote(mail_subject)}&body={quote(convocation_text)}"
        decision_rows = "".join(
            f"""<li>
              <strong>{esc(d['deal_title'])}</strong>
              <span>{esc(d['outcome'])} - {esc(nice_date(d['created_at']))}</span>
            </li>"""
            for d in decisions
        ) or '<li class="empty-row">Nessuna delibera collegata.</li>'
        convocation_view = f"""
<section class="governance-board">
  <div class="panel">
    <div class="section-head"><h2>Convocazioni</h2><a class="button primary" href="{rel_url('/governance', ctx, {'tab': 'convocazioni', 'mode': 'new'})}">+ Nuova</a></div>
    <div class="record-list">{meeting_items}</div>
  </div>
  <div class="panel detail-panel">
    <div class="section-head"><h2>Dettaglio convocazione</h2><span class="panel-kicker">Email & link</span></div>
    <form class="form-grid governance-form" method="post" action="/governance/meeting-create">
      {hidden_ctx(ctx)}
      <input type="hidden" name="status" value="Convocata">
      <label>Data<input name="meeting_date" type="date" value="{esc(selected_date)}" required></label>
      <label>Ora<input name="meeting_time" value="18:00"></label>
      <label>Modalita<select name="meeting_mode"><option>Mista</option><option>Presenza</option><option>Videoconferenza</option></select></label>
      <label>Luogo<input name="meeting_place" value="sede legale"></label>
      <label class="full-span">Oggetto convocazione<input name="title" value="{esc(selected_title)}"></label>
      <label class="full-span link-row">Link riunione (videoconferenza)<span><input id="meeting-link" name="meeting_link" value="{esc(selected_link)}" placeholder="incolla qui il link Meet / Zoom / Teams"><button class="button ghost" type="button" data-generate-meeting-link="meet" data-target="#meeting-link">Crea Meet</button><button class="button ghost" type="button" data-generate-meeting-link="zoom" data-target="#meeting-link">Crea Zoom</button></span></label>
      <div class="full-span">
        <p class="panel-kicker">Invitati (rubrica CdA)</p>
        {invite_warning}
        <p class="muted">Destinatari: {esc(board_emails or 'nessun indirizzo disponibile')}</p>
      </div>
      <label class="full-span">Ordine del giorno (una voce per riga)<textarea name="agenda" rows="5">{esc(selected_agenda)}</textarea></label>
      <div class="form-actions left"><button class="button primary" type="submit">Salva convocazione</button><a class="button ghost" href="{rel_url('/governance', ctx, {'tab': 'convocazioni'})}">Annulla</a></div>
      <label class="full-span">Testo della convocazione<textarea id="convocation-text" class="template-text" rows="12">{esc(convocation_text)}</textarea></label>
      <div class="form-actions left"><a class="button primary" href="{esc(mailto_url)}">Apri email a tutti</a><button class="button ghost" type="button" data-copy-from="#convocation-text">Copia testo</button></div>
    </form>
  </div>
</section>"""
        sedute_view = f"""
<section class="governance-board">
  <div class="panel">
    <div class="section-head"><h2>Sedute & verbali</h2><a class="button primary" href="{rel_url('/governance', ctx, {'tab': 'sedute', 'mode': 'new'})}">+ Nuova seduta</a></div>
    <div class="record-list">{seduta_items}</div>
  </div>
  <div class="panel detail-panel">
    <div class="section-head"><h2>Dettaglio seduta</h2><span class="panel-kicker">Relazione & iter</span></div>
    <ol class="mini-progress"><li class="active">Bozza</li><li>In Comitato Tecnico</li><li>In Advisory Committee</li><li>In approvazione Board</li><li>Esito</li></ol>
    <form class="form-grid governance-form" method="post" action="/governance/meeting-create">
      {hidden_ctx(ctx)}
      <label class="full-span">Oggetto della seduta<input name="title" value="{esc('' if mode == 'new' and active_tab == 'sedute' else selected_title)}" placeholder="es. Seduta CdA del..." required></label>
      <label>Data seduta<input name="meeting_date" type="date" value="{esc(selected_date)}" required></label>
      <label>Tipo<select name="meeting_type"><option>Seduta ordinaria</option><option>Seduta straordinaria</option></select></label>
      <label>Stato<select name="status"><option>Bozza</option><option>Convocata</option><option>In preparazione verbale</option><option>Verbale archiviato</option></select></label>
      <label class="full-span">Ordine del giorno<textarea name="agenda" rows="6" placeholder="Punti all ordine del giorno...">{esc('' if mode == 'new' and active_tab == 'sedute' else selected_agenda)}</textarea></label>
      <div class="full-span">
        <p class="panel-kicker">Documenti a corredo</p>
        <p class="muted">Nessun allegato (relazione, KIIS, pareri, due diligence...).</p>
        <button class="button ghost" type="button">+ Allegato</button>
      </div>
      <label class="full-span">Delibere / verbale<textarea rows="4" placeholder="Delibere assunte, esiti, condizioni..."></textarea></label>
      <div class="form-actions left"><button class="button primary" type="submit">Salva seduta</button><a class="button ghost danger-button" href="{rel_url('/governance', ctx, {'tab': 'sedute'})}">Elimina</a></div>
    </form>
    <div class="linked-decisions"><p class="panel-kicker">Delibere generate dai deal</p><ul>{decision_rows}</ul></div>
  </div>
</section>"""
        members_view = f"""
<section class="governance-board">
  <div class="panel">
    <div class="section-head"><h2>Membri CdA</h2><button class="button primary" type="button">+ Membro</button></div>
    <div class="record-list">{member_items}</div>
  </div>
  <div class="panel detail-panel">
    <div class="section-head"><h2>Dettaglio membro CdA</h2><span class="panel-kicker">Rubrica & mandato</span></div>
    <form class="form-grid governance-form">
      <label>Nome<input value="{esc(selected_member['name'] if selected_member else '')}"></label>
      <label>Ruolo<input value="{esc(selected_member['role'] if selected_member else '')}"></label>
      <label>Email<input value="{esc(selected_member['email'] if selected_member else '')}"></label>
      <label>Stato<select><option>Attivo</option><option>Non attivo</option></select></label>
      <label>Inizio mandato<input type="date"></label>
      <label>Fine mandato<input type="date"></label>
      <label class="full-span">Note incarico<textarea rows="5" placeholder="Deleghe, patti, accordi collegati..."></textarea></label>
      <div class="form-actions left"><button class="button primary" type="button">Salva membro</button><button class="button ghost" type="button">Collega accordo</button></div>
    </form>
  </div>
</section>"""
        tab_view = {"convocazioni": convocation_view, "sedute": sedute_view, "membri": members_view}[active_tab]
        body = f"""
<p class="page-copy">Il mondo del CdA: convocazione delle sedute con email automatica e link di riunione, registrazione delle sedute con verbali e relativo archivio, rubrica dei membri.</p>
<div class="subtabs">{tab_html}</div>
{tab_view}
"""
        self.render("Governance · Consiglio di Amministrazione", body, "governance")

    def page_investors(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        params = parse_qs(urlparse(self.path).query)
        mode = (params.get("mode") or [""])[0]
        investors = rows(
            """
            SELECT inv.*, COUNT(i.id) AS deal_count, COALESCE(SUM(i.amount), 0) AS tracked_total
            FROM investors inv
            LEFT JOIN investments i ON i.investor_id = inv.id
            WHERE inv.platform_id = ?
            GROUP BY inv.id
            ORDER BY inv.name
            """,
            (pid,),
        )
        investments = rows(
            """
            SELECT i.*, inv.name AS investor_name, d.title AS deal_title
            FROM investments i
            JOIN investors inv ON inv.id = i.investor_id
            JOIN deals d ON d.id = i.deal_id
            WHERE inv.platform_id = ?
            ORDER BY i.invested_at DESC
            """,
            (pid,),
        )
        total_invested = sum(float(i["total_invested"] or 0) for i in investors)
        non_sophisticated = sum(1 for i in investors if i["investor_type"] == "Non sofisticato")
        onboarding_open = sum(1 for i in investors if i["onboarding_status"] != "Completo")
        api_linked = sum(1 for i in investors if (i["source_system"] or "").startswith("adapter:"))
        recurring = sum(1 for i in investors if i["recurrence_status"] == "Ricorrente" or int(i["deal_count"] or 0) > 1)
        investor_rows = []
        for inv in investors:
            display_total = max(float(inv["total_invested"] or 0), float(inv["tracked_total"] or 0))
            investor_rows.append(
                f"""<tr>
                  <td><a href="{rel_url('/investors/' + str(inv['id']), ctx)}"><strong>{esc(inv['name'])}</strong></a><br><span class="muted">{esc(inv['email'])}</span></td>
                  <td>{esc(inv['phone'] or '-')}</td>
                  <td>{esc(inv['investor_type'])}</td>
                  <td>{money(display_total)}</td>
                  <td>{inv['deal_count']}</td>
                  <td>{esc(inv['recurrence_status'])}<br><span class="muted">{esc(inv['preferred_channel'])}</span></td>
                  <td>{esc(inv['preferred_categories'] or '-')}</td>
                  <td><span class="badge {badge_class(inv['onboarding_status'])}">{esc(inv['onboarding_status'])}</span></td>
                  <td>{esc(inv['source_system'] or 'Manuale')}<br><span class="muted">{esc(nice_date(inv['last_synced_at']))}</span></td>
                </tr>"""
            )
        investment_rows = "".join(
            f"""<tr>
              <td>{esc(nice_date(i['invested_at']))}</td>
              <td><a href="{rel_url('/investors/' + str(i['investor_id']), ctx)}">{esc(i['investor_name'])}</a></td>
              <td>{esc(i['deal_title'])}</td>
              <td>{money(i['amount'])}</td>
              <td><span class="badge {badge_class(i['status'])}">{esc(i['status'])}</span></td>
            </tr>"""
            for i in investments
        )
        action_button = ""
        create_modal = ""
        if user_can(ctx["user"], "manage_investors"):
            action_button = f'<a class="button primary" href="{rel_url("/investors", ctx, {"mode": "new"})}">+ Nuovo investitore</a>'
            if mode == "new":
                create_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal">
    <div class="section-head">
      <h2>Nuovo investitore</h2>
      <a class="modal-close" href="{rel_url('/investors', ctx)}">x</a>
    </div>
    <form class="form-grid" method="post" action="/investors/create">
      {hidden_ctx(ctx)}
      <label>Nome<input name="name" required></label>
      <label>Email<input name="email" type="email" required></label>
      <label>Telefono<input name="phone" placeholder="+39 ..."></label>
      <label>Tipologia<select name="investor_type"><option>Non sofisticato</option><option>Sofisticato</option></select></label>
      <label>Totale investito<input name="total_invested" type="number" min="0" step="100" value="0"></label>
      <label>Stato CRM<select name="crm_status"><option>Attivo</option><option>Prospect</option><option>Da ricontattare</option><option>In pausa</option></select></label>
      <label>Ricorrenza<select name="recurrence_status"><option>Da valutare</option><option>Prospect</option><option>Occasionale</option><option>Ricorrente</option></select></label>
      <label>Canale preferito<select name="preferred_channel"><option>Email</option><option>Telefono</option><option>PEC</option><option>WhatsApp</option><option>Portale</option></select></label>
      <label>Profilo rischio<select name="risk_profile"><option>Da profilare</option><option>Prudente</option><option>Bilanciato</option><option>Dinamico</option><option>Professionale</option></select></label>
      <label>Ticket minimo<input name="preferred_ticket_min" type="number" min="0" step="1000" value="0"></label>
      <label>Ticket massimo<input name="preferred_ticket_max" type="number" min="0" step="1000" value="0"></label>
      <label class="full-span">Preferenze deal<input name="preferred_categories" placeholder="es. MedTech, energia, PMI produttive"></label>
      <label>Onboarding<select name="onboarding_status"><option>Da completare</option><option>In revisione</option><option>Completo</option></select></label>
      <label>Test ingresso<select name="entry_test_status"><option>Da completare</option><option>Superato</option><option>Non richiesto</option></select></label>
      <label>Simulazione perdite<select name="loss_simulation_status"><option>Da completare</option><option>Completata</option><option>Non richiesta</option></select></label>
      <label>Soglie<select name="threshold_status"><option>Da verificare</option><option>Sotto soglia</option><option>Soglia superata</option><option>Verificata</option></select></label>
      <label>Periodo riflessione<select name="reflection_status"><option>Non applicabile</option><option>In corso</option><option>Decorso</option></select></label>
      <label>Fonte dato<select name="source_system"><option>Manuale</option><option>adapter:future-platform-api</option><option>Import CSV</option><option>Backoffice</option></select></label>
      <label>ID esterno<input name="external_investor_id" placeholder="ID piattaforma sorgente"></label>
      <label class="full-span">Note CRM<textarea name="crm_notes" rows="3"></textarea></label>
      <div class="form-actions left"><button class="button primary" type="submit">Crea anagrafica</button><a class="button ghost" href="{rel_url('/investors', ctx)}">Annulla</a></div>
    </form>
  </section>
</div>"""
        body = f"""
<section class="metric-grid">
  <div class="metric"><span>Investitori</span><strong>{len(investors)}</strong></div>
  <div class="metric"><span>Non sofisticati</span><strong>{non_sophisticated}</strong></div>
  <div class="metric"><span>Ricorrenti</span><strong>{recurring}</strong></div>
  <div class="metric"><span>Da API</span><strong>{api_linked}</strong></div>
  <div class="metric"><span>Totale investito</span><strong>{money(total_invested)}</strong></div>
</section>
<section class="panel">
  <div class="section-head"><h2>Anagrafica e onboarding</h2>{action_button}</div>
  <table class="data-table roomy">
    <thead><tr><th>Investitore</th><th>Telefono</th><th>Tipo</th><th>Totale</th><th>Deal</th><th>Relazione</th><th>Preferenze</th><th>Onboarding</th><th>Fonte</th></tr></thead>
    <tbody>{''.join(investor_rows)}</tbody>
  </table>
</section>
<section class="panel">
  <div class="section-head"><h2>Deal e importi</h2></div>
  <table class="data-table compact"><thead><tr><th>Data</th><th>Investitore</th><th>Deal</th><th>Importo</th><th>Stato</th></tr></thead><tbody>{investment_rows}</tbody></table>
</section>
{create_modal}
"""
        self.render("Investitori", body, "investors")

    def page_investor_detail(self, investor_id):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        params = parse_qs(urlparse(self.path).query)
        mode = (params.get("mode") or [""])[0]
        investor = row("SELECT * FROM investors WHERE id = ? AND platform_id = ?", (investor_id, pid))
        if not investor:
            self.not_found()
            return
        investments = rows(
            """
            SELECT i.*, d.title AS deal_title, d.phase, d.funding_target, p.name AS proponent_name, p.notes AS proponent_notes
            FROM investments i
            JOIN deals d ON d.id = i.deal_id
            JOIN proponents p ON p.id = d.proponent_id
            WHERE i.investor_id = ?
            ORDER BY i.invested_at DESC
            """,
            (investor_id,),
        )
        all_deals = rows(
            """
            SELECT d.*, p.name AS proponent_name, p.notes AS proponent_notes, p.internal_score
            FROM deals d
            JOIN proponents p ON p.id = d.proponent_id
            WHERE d.platform_id = ?
            ORDER BY d.updated_at DESC
            """,
            (pid,),
        )
        total_tracked = sum(float(i["amount"] or 0) for i in investments)
        display_total = max(float(investor["total_invested"] or 0), total_tracked)
        avg_ticket = total_tracked / len(investments) if investments else 0
        first_investment = min((i["invested_at"] for i in investments), default="")
        last_investment = max((i["invested_at"] for i in investments), default="")
        try:
            days_in_crm = (date.today() - date.fromisoformat(investor["created_at"][:10])).days
        except ValueError:
            days_in_crm = 0

        if investor["onboarding_status"] != "Completo":
            lifecycle = "Onboarding"
        elif investor["reflection_status"] == "In corso":
            lifecycle = "Periodo riflessione"
        elif investments:
            lifecycle = "Attivo"
        else:
            lifecycle = "Prospect"
        observed_themes = {}
        for inv in investments:
            theme = deal_theme(inv["deal_title"], inv["proponent_name"], inv["proponent_notes"])
            observed_themes[theme] = observed_themes.get(theme, 0) + 1
        theme_labels = [theme for theme, _ in sorted(observed_themes.items(), key=lambda item: (-item[1], item[0]))]
        preferred_terms = [term.strip().lower() for term in re.split(r"[,;/]+", investor["preferred_categories"] or "") if term.strip()]
        effective_recurrence = investor["recurrence_status"]
        if len(investments) > 1 and effective_recurrence in {"", "Da valutare", "Occasionale"}:
            effective_recurrence = "Ricorrente"
        if display_total >= 100000:
            segment = "High value"
        elif display_total >= 25000:
            segment = "Core"
        elif investments:
            segment = "Retail attivo"
        else:
            segment = "Prospect"

        investment_rows = "".join(
            f"""<tr>
              <td>{esc(nice_date(i['invested_at']))}</td>
              <td><a href="{rel_url('/deals/' + str(i['deal_id']), ctx)}"><strong>{esc(i['deal_title'])}</strong></a><br><span class="muted">{esc(i['proponent_name'])}</span></td>
              <td>{money(i['amount'])}</td>
              <td>{esc(phase_label(i['phase']))}</td>
              <td><span class="badge {badge_class(i['status'])}">{esc(i['status'])}</span></td>
            </tr>"""
            for i in investments
        ) or '<tr><td colspan="5" class="empty-row">Nessun investimento registrato.</td></tr>'

        invested_deal_ids = {int(i["deal_id"]) for i in investments}
        ticket_min = float(investor["preferred_ticket_min"] or 0)
        ticket_max = float(investor["preferred_ticket_max"] or 0)
        match_cards = []
        for deal in all_deals:
            if int(deal["id"]) in invested_deal_ids:
                continue
            theme = deal_theme(deal["title"], deal["proponent_name"], deal["proponent_notes"])
            haystack = f"{deal['title']} {deal['proponent_name']} {deal['proponent_notes']} {theme}".lower()
            score = 45
            reasons = []
            if preferred_terms and any(term in haystack for term in preferred_terms):
                score += 25
                reasons.append("preferenze dichiarate")
            if theme in theme_labels:
                score += 15
                reasons.append("tema gia investito")
            if avg_ticket and (not ticket_min or avg_ticket >= ticket_min) and (not ticket_max or avg_ticket <= ticket_max):
                score += 10
                reasons.append("ticket coerente")
            if deal["phase"] in {"pubblicato", "raccolta_in_corso", "pre_pubblicazione"}:
                score += 10
                reasons.append("timing sollecitabile")
            score = min(score, 95)
            reason_text = ", ".join(reasons) if reasons else "profilo da validare"
            match_cards.append((score, deal, theme, reason_text))
        match_cards = sorted(match_cards, key=lambda item: item[0], reverse=True)[:3]
        match_html = "".join(
            f"""<div class="match-card">
              <div>
                <span>{esc(theme)} - match {score}%</span>
                <strong><a href="{rel_url('/deals/' + str(deal['id']), ctx)}">{esc(deal['title'])}</a></strong>
                <small>{esc(deal['proponent_name'])} - {esc(phase_label(deal['phase']))} - {esc(reason)}</small>
              </div>
              <em>{money(deal['funding_target'])}</em>
            </div>"""
            for score, deal, theme, reason in match_cards
        ) or '<p class="empty-state">Nessun deal compatibile da proporre in questa vista.</p>'

        matching_notes = []
        if effective_recurrence == "Ricorrente":
            matching_notes.append("Inserire in campagne prioritarie con aggiornamenti mirati sui deal coerenti.")
        elif effective_recurrence == "Occasionale":
            matching_notes.append("Usare sollecitazioni leggere: interesse da validare prima di invii frequenti.")
        else:
            matching_notes.append("Profilo ancora da qualificare: completare preferenze e range ticket.")
        if investor["preferred_channel"]:
            matching_notes.append(f"Canale consigliato: {investor['preferred_channel']}.")
        if theme_labels:
            matching_notes.append(f"Pattern osservato: {', '.join(theme_labels[:3])}.")
        matching_note_html = "".join(f"<li>{esc(item)}</li>" for item in matching_notes)

        compliance_items = [
            ("Classificazione", investor["investor_type"], "Sophisticated / non sophisticated"),
            ("Onboarding", investor["onboarding_status"], "Identificazione, dati cliente, consensi"),
            ("Test ingresso", investor["entry_test_status"], "Conoscenza ed esperienza"),
            ("Simulazione perdite", investor["loss_simulation_status"], "Capacita di sostenere perdite"),
            ("Soglie", investor["threshold_status"], "Controllo soglia e warning"),
            ("Riflessione", investor["reflection_status"], "Periodo di riflessione precontrattuale"),
        ]
        compliance_html = "".join(
            f"""<div class="crm-check">
              <span>{esc(label)}</span>
              <strong><span class="badge {badge_class(status)}">{esc(status)}</span></strong>
              <small>{esc(note)}</small>
            </div>"""
            for label, status, note in compliance_items
        )

        actions = []
        if investor["onboarding_status"] != "Completo":
            actions.append("Completare onboarding e verifiche anagrafiche prima di nuove sottoscrizioni.")
        if investor["investor_type"] == "Non sofisticato" and investor["entry_test_status"] != "Superato":
            actions.append("Richiedere o aggiornare il test d'ingresso per investitore non sofisticato.")
        if investor["investor_type"] == "Non sofisticato" and investor["loss_simulation_status"] not in {"Completata", "Non richiesta"}:
            actions.append("Completare simulazione della capacita di sostenere perdite.")
        if investor["threshold_status"] == "Soglia superata":
            actions.append("Verificare superamento soglia e warning prima dell'adesione.")
        if investor["reflection_status"] == "In corso":
            actions.append("Monitorare decorrenza del periodo di riflessione prima di confermare l'ordine.")
        if investor["crm_status"] == "Da ricontattare":
            actions.append(f"Pianificare ricontatto sul canale preferito: {investor['preferred_channel'] or 'da definire'}.")
        if not investor["preferred_categories"]:
            actions.append("Completare preferenze deal per abilitare matching e segmentazione campagne.")
        if (investor["source_system"] or "").startswith("adapter:") and not investor["last_synced_at"]:
            actions.append("Record collegato ad API ma senza ultima sincronizzazione: verificare mapping sorgente.")
        if not actions:
            actions.append("Profilo operativo: mantenere monitoraggio periodico e aggiornamento classificazione.")
        action_html = "".join(f"<li>{esc(item)}</li>" for item in actions)

        timeline_items = [
            (investor["created_at"], "Registrazione investitore", f"Profilo creato come {investor['investor_type']}."),
        ]
        for inv in investments:
            timeline_items.append((inv["invested_at"], f"Investimento in {inv['deal_title']}", f"{money(inv['amount'])} - {inv['status']}"))
        timeline_items.append((today_iso(), "Stato compliance corrente", f"Onboarding: {investor['onboarding_status']}; soglie: {investor['threshold_status']}; riflessione: {investor['reflection_status']}."))
        timeline_items = sorted(timeline_items, key=lambda item: item[0] or "", reverse=True)
        timeline_html = "".join(
            f"""<li>
              <span>{esc(nice_date(moment))}</span>
              <strong>{esc(title)}</strong>
              <small>{esc(detail)}</small>
            </li>"""
            for moment, title, detail in timeline_items
        )

        edit_button = ""
        edit_modal = ""
        if user_can(ctx["user"], "manage_investors"):
            edit_button = f'<a class="button primary" href="{rel_url("/investors/" + str(investor_id), ctx, {"mode": "edit"})}">Modifica investitore</a>'
            if mode == "edit":
                edit_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal">
    <div class="section-head">
      <h2>Modifica investitore</h2>
      <a class="modal-close" href="{rel_url('/investors/' + str(investor_id), ctx)}">x</a>
    </div>
    <form class="form-grid" method="post" action="/investors/{investor_id}/update">
      {hidden_ctx(ctx)}
      <label>Nome<input name="name" value="{esc(investor['name'])}" required></label>
      <label>Email<input name="email" type="email" value="{esc(investor['email'])}" required></label>
      <label>Telefono<input name="phone" value="{esc(investor['phone'])}" placeholder="+39 ..."></label>
      <label>Tipologia<select name="investor_type">{option_values(['Non sofisticato', 'Sofisticato'], investor['investor_type'])}</select></label>
      <label>Totale investito<input name="total_invested" type="number" min="0" step="100" value="{esc(investor['total_invested'])}"></label>
      <label>Stato CRM<select name="crm_status">{option_values(['Attivo', 'Prospect', 'Da ricontattare', 'In pausa'], investor['crm_status'])}</select></label>
      <label>Ricorrenza<select name="recurrence_status">{option_values(['Da valutare', 'Prospect', 'Occasionale', 'Ricorrente'], investor['recurrence_status'])}</select></label>
      <label>Canale preferito<select name="preferred_channel">{option_values(['Email', 'Telefono', 'PEC', 'WhatsApp', 'Portale'], investor['preferred_channel'])}</select></label>
      <label>Profilo rischio<select name="risk_profile">{option_values(['Da profilare', 'Prudente', 'Bilanciato', 'Dinamico', 'Professionale'], investor['risk_profile'])}</select></label>
      <label>Ticket minimo<input name="preferred_ticket_min" type="number" min="0" step="1000" value="{esc(investor['preferred_ticket_min'])}"></label>
      <label>Ticket massimo<input name="preferred_ticket_max" type="number" min="0" step="1000" value="{esc(investor['preferred_ticket_max'])}"></label>
      <label class="full-span">Preferenze deal<input name="preferred_categories" value="{esc(investor['preferred_categories'])}" placeholder="es. MedTech, energia, PMI produttive"></label>
      <label>Onboarding<select name="onboarding_status">{option_values(['Da completare', 'In revisione', 'Completo'], investor['onboarding_status'])}</select></label>
      <label>Test ingresso<select name="entry_test_status">{option_values(['Da completare', 'Superato', 'Non richiesto'], investor['entry_test_status'])}</select></label>
      <label>Simulazione perdite<select name="loss_simulation_status">{option_values(['Da completare', 'Completata', 'Non richiesta'], investor['loss_simulation_status'])}</select></label>
      <label>Soglie<select name="threshold_status">{option_values(['Da verificare', 'Sotto soglia', 'Soglia superata', 'Verificata'], investor['threshold_status'])}</select></label>
      <label>Periodo riflessione<select name="reflection_status">{option_values(['Non applicabile', 'In corso', 'Decorso'], investor['reflection_status'])}</select></label>
      <label>Fonte dato<select name="source_system">{option_values(['Manuale', 'adapter:future-platform-api', 'Import CSV', 'Backoffice'], investor['source_system'])}</select></label>
      <label>ID esterno<input name="external_investor_id" value="{esc(investor['external_investor_id'])}" placeholder="ID piattaforma sorgente"></label>
      <label class="full-span">Note override manuale<textarea name="manual_override_notes" rows="3">{esc(investor['manual_override_notes'])}</textarea></label>
      <label class="full-span">Note CRM<textarea name="crm_notes" rows="3">{esc(investor['crm_notes'])}</textarea></label>
      <div class="form-actions left"><button class="button primary" type="submit">Salva modifiche</button><a class="button ghost" href="{rel_url('/investors/' + str(investor_id), ctx)}">Annulla</a></div>
    </form>
  </section>
</div>"""

        body = f"""
<section class="deal-header">
  <div>
    <p class="eyebrow">CRM investitore</p>
    <h2>{esc(investor['name'])}</h2>
    <p class="muted">{esc(investor['email'])} - {esc(investor['phone'] or 'telefono non indicato')}</p>
  </div>
  <div class="header-badges">
    <span class="badge {badge_class(investor['investor_type'])}">{esc(investor['investor_type'])}</span>
    <span class="badge {badge_class(investor['crm_status'])}">{esc(investor['crm_status'])}</span>
    {edit_button}
    <a class="button ghost" href="{rel_url('/investors', ctx)}">Torna agli investitori</a>
  </div>
</section>
<section class="metric-grid">
  <div class="metric"><span>Investito tracciato</span><strong>{money(total_tracked)}</strong></div>
  <div class="metric"><span>Deal sottoscritti</span><strong>{len(investments)}</strong></div>
  <div class="metric"><span>Ticket medio</span><strong>{money(avg_ticket)}</strong></div>
  <div class="metric"><span>Ricorrenza</span><strong>{esc(effective_recurrence)}</strong></div>
  <div class="metric"><span>Giorni in CRM</span><strong>{days_in_crm}</strong></div>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Profilo CRM</h2><span class="panel-kicker">{esc(segment)}</span></div>
    <dl class="definition-list">
      <dt>Registrazione</dt><dd>{esc(nice_date(investor['created_at']))}</dd>
      <dt>Ciclo vita</dt><dd>{esc(lifecycle)}</dd>
      <dt>Classificazione</dt><dd>{esc(investor['investor_type'])}</dd>
      <dt>Telefono</dt><dd>{esc(investor['phone'] or '-')}</dd>
      <dt>Canale preferito</dt><dd>{esc(investor['preferred_channel'] or '-')}</dd>
      <dt>Profilo rischio</dt><dd>{esc(investor['risk_profile'])}</dd>
      <dt>Totale dichiarato</dt><dd>{money(investor['total_invested'])}</dd>
      <dt>Ticket preferito</dt><dd>{money(investor['preferred_ticket_min'])} - {money(investor['preferred_ticket_max'])}</dd>
      <dt>Primo investimento</dt><dd>{esc(nice_date(first_investment))}</dd>
      <dt>Ultimo investimento</dt><dd>{esc(nice_date(last_investment))}</dd>
    </dl>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Compliance investitore</h2><span class="panel-kicker">ECSP</span></div>
    <div class="crm-check-grid">{compliance_html}</div>
  </div>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Dati piattaforma e override</h2><span class="panel-kicker">API + manuale</span></div>
    <dl class="definition-list">
      <dt>Fonte</dt><dd>{esc(investor['source_system'] or 'Manuale')}</dd>
      <dt>ID esterno</dt><dd>{esc(investor['external_investor_id'] or '-')}</dd>
      <dt>Ultimo sync</dt><dd>{esc(nice_date(investor['last_synced_at']))}</dd>
      <dt>Override</dt><dd>{esc(investor['manual_override_notes'] or 'Nessuna correzione manuale registrata.')}</dd>
      <dt>Note CRM</dt><dd>{esc(investor['crm_notes'] or '-')}</dd>
    </dl>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Mappa matching</h2><span class="panel-kicker">sollecitazione</span></div>
    <div class="crm-check-grid">
      <div class="crm-check"><span>Preferenze dichiarate</span><strong>{esc(investor['preferred_categories'] or 'da completare')}</strong><small>Campo modificabile o importato da piattaforma.</small></div>
      <div class="crm-check"><span>Pattern osservato</span><strong>{esc(', '.join(theme_labels) or 'nessuno storico')}</strong><small>Derivato dagli investimenti gia presenti.</small></div>
      <div class="crm-check"><span>Ricorrenza</span><strong>{esc(effective_recurrence)}</strong><small>Usata per priorita campagne e follow-up.</small></div>
      <div class="crm-check"><span>Canale</span><strong>{esc(investor['preferred_channel'] or '-')}</strong><small>Canale operativo per contatto e sollecitazione.</small></div>
    </div>
  </div>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Deal suggeriti</h2><span class="panel-kicker">matching</span></div>
    <div class="match-list">{match_html}</div>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Regole di sollecitazione</h2><span class="panel-kicker">CRM</span></div>
    <ul class="plain-list">{matching_note_html}</ul>
  </div>
</section>
<section class="panel">
  <div class="section-head"><h2>Storico investimenti</h2><span class="panel-kicker">{len(investments)} deal</span></div>
  <table class="data-table roomy">
    <thead><tr><th>Data</th><th>Deal / proponente</th><th>Importo</th><th>Fase deal</th><th>Stato ordine</th></tr></thead>
    <tbody>{investment_rows}</tbody>
  </table>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Timeline CRM</h2><span class="panel-kicker">eventi</span></div>
    <ol class="crm-timeline">{timeline_html}</ol>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Prossime azioni</h2><span class="panel-kicker">operativo</span></div>
    <ul class="plain-list">{action_html}</ul>
  </div>
</section>
{edit_modal}
"""
        self.render(investor["name"], body, "investors")

    def page_conflicts(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        params = parse_qs(urlparse(self.path).query)
        mode = (params.get("mode") or [""])[0]
        edit_id = (params.get("id") or ["0"])[0]
        conflicts = rows(
            """
            SELECT c.*, d.title AS deal_title
            FROM conflicts c
            LEFT JOIN deals d ON d.id = c.deal_id
            WHERE c.platform_id = ?
            ORDER BY c.opened_at DESC, c.id DESC
            """,
            (pid,),
        )

        def cv(c, key, *fallbacks):
            v = c[key] if key in c.keys() and c[key] else ""
            for fb in fallbacks:
                if v:
                    break
                v = c[fb] if fb in c.keys() and c[fb] else ""
            return v

        open_count = sum(1 for c in conflicts if c["status"] in {"Aperto", "In analisi"})
        mitigated_count = sum(1 for c in conflicts if c["status"] in {"Mitigato", "Chiuso"})
        resp_name, resp_func = org_responsabile(
            pid, ["conflitt", "funzioni di controllo", "controllo di 2", "controlli"],
            fallback="Consigliere Incaricato (da assegnare)",
        )
        can_edit = user_can(ctx["user"], "manage_registers")
        rows_html = ""
        for i, c in enumerate(conflicts, start=1):
            esito = cv(c, "esito", "status")
            edit_link = f'<a class="button tiny" href="{rel_url("/conflicts", ctx, {"mode": "edit", "id": c["id"]})}">Modifica</a>' if can_edit else ""
            rows_html += f"""<tr>
              <td>{esc(cv(c, 'reg_no') or i)}</td>
              <td class="muted">{esc(nice_date(c['opened_at']))}</td>
              <td>{esc(cv(c, 'soggetti', 'subject'))}{('<br><span class="muted">' + esc(c['related_party']) + '</span>') if c['related_party'] else ''}</td>
              <td>{esc(cv(c, 'natura_fonte', 'description'))}</td>
              <td>{esc(cv(c, 'rilevato_da'))}</td>
              <td>{esc(cv(c, 'valutazione'))}</td>
              <td>{esc(cv(c, 'misura', 'mitigation'))}</td>
              <td><span class="badge {badge_class(esito)}">{esc(esito or '-')}</span></td>
              <td>{esc(cv(c, 'atti_collegati') or (c['deal_title'] or '-'))}</td>
              <td>{edit_link}</td>
            </tr>"""
        if not rows_html:
            rows_html = '<tr><td colspan="10" class="muted">Registro vuoto.</td></tr>'

        action_button = ""
        modal = ""
        if can_edit:
            action_button = (
                f'<a class="button tiny" href="{rel_url("/conflicts/export", ctx)}">Esporta registro</a> '
                f'<a class="button primary" href="{rel_url("/conflicts", ctx, {"mode": "new"})}">+ Nuova voce</a>'
            )
            editing = None
            if mode == "edit" and edit_id.isdigit():
                editing = row("SELECT * FROM conflicts WHERE id = ? AND platform_id = ?", (int(edit_id), pid))
            if mode == "new" or editing:
                e = editing
                stored_val = (cv(e, "valutazione") if e else "")
                def sel(name, options, current, label, hint):
                    altro_token = "altro" if "altro" in options else ("Altro" if "Altro" in options else None)
                    is_custom = bool(current) and current not in options and altro_token
                    selected = altro_token if is_custom else current
                    opts = option_values(options, selected)
                    extra = ""
                    if altro_token:
                        show = "block" if is_custom else "none"
                        val = esc(current) if is_custom else ""
                        extra = (f'<input name="{name}_altro" class="altro-input" placeholder="Specifica..." '
                                 f'value="{val}" style="display:{show};margin-top:5px">')
                    return (f'<label>{label}<small class="muted">{esc(hint)}</small>'
                            f'<select name="{name}" class="guided-select">{opts}</select>{extra}</label>')
                action = "/conflicts/update" if editing else "/conflicts/create"
                hid = f'<input type="hidden" name="id" value="{editing["id"]}">' if editing else ""
                fond_cur = "non fondato" if "non fondato" in stored_val else "fondato"
                gest_cur = "non gestibile" if "non gestibile" in stored_val else "gestibile"
                modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal wide">
    <div class="section-head"><h2>{'Modifica voce' if editing else 'Nuova voce'} - Registro conflitti</h2><a class="modal-close" href="{rel_url('/conflicts', ctx)}">x</a></div>
    <p class="muted" style="margin:0 0 10px">Compila i campi guidati: scegli dalle tendine i valori previsti dal modello (Allegato 14). Aggiungi un nominativo e, se serve, una nota libera.</p>
    <form class="form-grid" method="post" action="{action}">
      {hidden_ctx(ctx)}{hid}
      <label>N. / protocollo<small class="muted">lascia vuoto per numerazione automatica</small><input name="reg_no" value="{esc(cv(e,'reg_no')) if e else ''}" placeholder="es. 2026/001"></label>
      <label>Data<small class="muted">data di rilevazione</small><input name="opened_at" type="date" value="{(e['opened_at'][:10] if e and e['opened_at'] else today_iso())}"></label>
      {sel("tipo_soggetto", CONFLICT_TIPO_SOGGETTO, "", "Tipo di soggetto", "chi e' coinvolto")}
      <label>Nominativo / soggetto<small class="muted">nome o ragione sociale</small><input name="nominativo" value="{esc(cv(e,'soggetti','subject')) if e else ''}" placeholder="es. Mario Rossi / Quinte Parallele S.r.l." required></label>
      {sel("natura_fonte", CONFLICT_NATURA, cv(e,"natura_fonte") if e else "", "Natura e fonte del conflitto", "tipo di rapporto")}
      {sel("rilevato_da", CONFLICT_FONTE, cv(e,"rilevato_da") if e else "", "Rilevato / segnalato da", "come e' emerso")}
      {sel("fondatezza", CONFLICT_FONDATEZZA, fond_cur, "Valutazione: fondatezza", "il conflitto sussiste?")}
      {sel("gestibilita", CONFLICT_GESTIBILITA, gest_cur, "Valutazione: gestibilita'", "e' gestibile?")}
      {sel("misura", CONFLICT_MISURA, cv(e,"misura","mitigation") if e else "", "Misura adottata", "presidio applicato")}
      {sel("esito", CONFLICT_ESITO, cv(e,"esito") if e else "in lavorazione", "Esito", "stato del caso")}
      {sel("atti_collegati", CONFLICT_ATTI, cv(e,"atti_collegati") if e else "", "Atti collegati", "documento di riferimento")}
      <label class="full-span">Note (facoltative)<small class="muted">dettagli aggiuntivi liberi</small><textarea name="note" rows="2"></textarea></label>
      <div class="form-actions left"><button class="button primary" type="submit">{'Salva modifiche' if editing else 'Registra voce'}</button><a class="button ghost" href="{rel_url('/conflicts', ctx)}">Annulla</a></div>
    </form>
  </section>
</div>"""
        body = f"""
<section class="panel">
  <div class="section-head"><h2>Registro dei conflitti di interesse</h2><div class="header-badges">{action_button}</div></div>
  <p class="muted">Responsabile della tenuta: <strong>{esc(resp_name)}</strong>{(' &middot; ' + esc(resp_func)) if resp_func else ' <span class="badge warning">assegna la funzione in Compagine</span>'} <span class="muted">(dall'organigramma)</span>. Base: Allegato 14 - Reg. del. (UE) 2022/2111 - art. 8 ECSP. Aggiornamento continuativo; presa visione del CdA semestrale; relazione scritta annuale.</p>
</section>
<section class="metric-grid">
  <div class="metric"><span>Voci a registro</span><strong>{len(conflicts)}</strong></div>
  <div class="metric"><span>Aperti / in analisi</span><strong>{open_count}</strong></div>
  <div class="metric"><span>Gestiti / chiusi</span><strong>{mitigated_count}</strong></div>
  <div class="metric"><span>Collegati a deal</span><strong>{sum(1 for c in conflicts if c['deal_id'])}</strong></div>
</section>
<section class="panel">
  <table class="data-table compact">
    <thead><tr><th>N.</th><th>Data</th><th>Soggetti coinvolti</th><th>Natura e fonte</th><th>Rilevato/segnalato da</th><th>Valutazione</th><th>Misura adottata</th><th>Esito</th><th>Atti collegati</th><th></th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</section>
<section class="panel info-panel">
  <div class="section-head"><h2>Cosa si fa in questa pagina</h2></div>
  <p>Qui si tiene il <strong>registro dei conflitti di interesse</strong> della piattaforma. Ogni volta che emerge un possibile conflitto - da una segnalazione, dalla dichiarazione annuale degli interessi o durante la selezione di un progetto - lo si <strong>annota come nuova voce</strong> indicando soggetti, natura, valutazione e la misura adottata (es. astensione in CdA, disclaimer, non ammissione).</p>
  <p>Il responsabile aggiorna il registro in via continuativa; il CdA ne prende visione ogni semestre e riceve una relazione annuale. Con <strong>"Esporta registro"</strong> si ottiene il documento ufficiale (o il CSV) conforme all'Allegato 14.</p>
</section>
{modal}
"""
        self.render("Conflitti", body, "conflicts")

    def page_complaints(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        params = parse_qs(urlparse(self.path).query)
        mode = (params.get("mode") or [""])[0]
        complaints = rows(
            """
            SELECT c.*, u.name AS owner_name
            FROM complaints c
            LEFT JOIN users u ON u.id = c.owner_user_id
            WHERE c.platform_id = ?
            ORDER BY c.received_at DESC
            """,
            (pid,),
        )
        users = rows("SELECT id, name FROM users WHERE active = 1 ORDER BY name")
        edit_id = (params.get("id") or ["0"])[0]
        can_edit = user_can(ctx["user"], "manage_registers")

        def cv(c, key, *fallbacks):
            v = c[key] if key in c.keys() and c[key] else ""
            for fb in fallbacks:
                if v:
                    break
                v = c[fb] if fb in c.keys() and c[fb] else ""
            return v

        open_count = sum(1 for c in complaints if c["status"] != "Chiuso")
        closed_count = sum(1 for c in complaints if c["status"] == "Chiuso")
        overdue_count = 0
        for c in complaints:
            if c["status"] != "Chiuso" and (date.today() - date.fromisoformat(c["received_at"][:10])).days > 30:
                overdue_count += 1
        resp_name, resp_func = org_responsabile(pid, ["reclam"], fallback="Responsabile Reclami (da assegnare)")
        rows_html = ""
        for i, c in enumerate(complaints, start=1):
            rows_html += f"""<tr>
              <td>{esc(cv(c, 'protocollo') or i)}</td>
              <td class="muted">{esc(nice_date(c['received_at']))}</td>
              <td>{esc(c['complainant'])}</td>
              <td>{esc(cv(c, 'classificazione') or 'Reclamo')}</td>
              <td>{esc(cv(c, 'motivi_danno', 'object'))}</td>
              <td class="muted">{esc(nice_date(c['ricevibilita_date']) if cv(c,'ricevibilita_date') else '-')}</td>
              <td class="muted">{esc(nice_date(c['riscontro_date']) if cv(c,'riscontro_date') else '-')}</td>
              <td>{esc(cv(c, 'misure', 'outcome'))}</td>
              <td>{esc(cv(c, 'esborso') or 'No')}</td>
              <td><span class="badge {badge_class(c['status'])}">{esc(c['status'])}</span></td>
              <td>{(f'<a class="button tiny" href="' + rel_url("/complaints", ctx, {"mode": "edit", "id": c["id"]}) + '">Modifica</a>') if can_edit else ''}</td>
            </tr>"""
        if not rows_html:
            rows_html = '<tr><td colspan="11" class="muted">Registro vuoto.</td></tr>'

        action_button = ""
        modal = ""
        if can_edit:
            action_button = (
                f'<a class="button tiny" href="{rel_url("/complaints/export", ctx)}">Esporta registro</a> '
                f'<a class="button primary" href="{rel_url("/complaints", ctx, {"mode": "new"})}">+ Nuova voce</a>'
            )
            editing = None
            if mode == "edit" and edit_id.isdigit():
                editing = row("SELECT * FROM complaints WHERE id = ? AND platform_id = ?", (int(edit_id), pid))
            if mode == "new" or editing:
                e = editing
                def fv(k, *fb):
                    return esc(cv(e, k, *fb)) if e else ""
                action = "/complaints/update" if editing else "/complaints/create"
                hid = f'<input type="hidden" name="id" value="{editing["id"]}">' if editing else ""
                cls_opts = option_values(["Reclamo", "Richiesta di informazioni"], cv(e, "classificazione") if e else "Reclamo")
                stato_opts = option_values(["pendente", "chiuso", "accolto", "respinto"], (e["status"] if e else "pendente"))
                esb_opts = option_values(["No", "Si - autorizz. organo"], cv(e, "esborso") if e else "No")
                modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal">
    <div class="section-head"><h2>{'Modifica voce' if editing else 'Nuova voce'} - Registro reclami</h2><a class="modal-close" href="{rel_url('/complaints', ctx)}">x</a></div>
    <form class="form-grid" method="post" action="{action}">
      {hidden_ctx(ctx)}{hid}
      <label>N. / Prot.<input name="protocollo" value="{fv('protocollo')}"></label>
      <label>Data ricezione<input name="received_at" type="date" value="{(e['received_at'][:10] if e and e['received_at'] else today_iso())}" required></label>
      <label>Reclamante<input name="complainant" value="{fv('complainant')}" required></label>
      <label>Classificazione<select name="classificazione">{cls_opts}</select></label>
      <label class="full-span">Oggetto, motivi e danno lamentato<textarea name="motivi_danno" rows="2" required>{fv('motivi_danno','object')}</textarea></label>
      <label>Comunic. ricevibilita (&le;10 gg lav.)<input name="ricevibilita_date" type="date" value="{(e['ricevibilita_date'][:10] if e and cv(e,'ricevibilita_date') else '')}"></label>
      <label>Data riscontro (&le;30 gg)<input name="riscontro_date" type="date" value="{(e['riscontro_date'][:10] if e and cv(e,'riscontro_date') else '')}"></label>
      <label class="full-span">Misure adottate per il riscontro<textarea name="misure" rows="2">{fv('misure','outcome')}</textarea></label>
      <label>Esborso<select name="esborso">{esb_opts}</select></label>
      <label>Stato<select name="status">{stato_opts}</select></label>
      <label>Canale<select name="channel">{option_values(['Email','Portale','PEC','Telefono','Altro'], (e['channel'] if e else 'Email'))}</select></label>
      <label>Responsabile<select name="owner_user_id">{option_rows(users, (e['owner_user_id'] if e else ctx['user_id']))}</select></label>
      <div class="form-actions left"><button class="button primary" type="submit">{'Salva modifiche' if editing else 'Registra voce'}</button><a class="button ghost" href="{rel_url('/complaints', ctx)}">Annulla</a></div>
    </form>
  </section>
</div>"""
        body = f"""
<section class="panel">
  <div class="section-head"><h2>Registro dei reclami</h2><div class="header-badges">{action_button}</div></div>
  <p class="muted">Responsabile: <strong>{esc(resp_name)}</strong>{(' &middot; ' + esc(resp_func)) if resp_func else ' <span class="badge warning">assegna la funzione Reclami in Compagine</span>'} <span class="muted">(dall'organigramma)</span>. Base: Allegato 16 - art. 7 §3 ECSP - Reg. del. (UE) 2022/2117. Canale: help@pariterequity.com. Tempistiche: ricevibilita &le;10 gg lavorativi; riscontro scritto &le;30 gg solari; chiusura dopo 180 gg senza contestazioni.</p>
</section>
<section class="metric-grid">
  <div class="metric"><span>Voci a registro</span><strong>{len(complaints)}</strong></div>
  <div class="metric"><span>Aperti / pendenti</span><strong>{open_count}</strong></div>
  <div class="metric"><span>Chiusi</span><strong>{closed_count}</strong></div>
  <div class="metric"><span>Oltre 30 giorni</span><strong>{overdue_count}</strong></div>
</section>
<section class="panel">
  <table class="data-table compact">
    <thead><tr><th>N./Prot.</th><th>Ricezione</th><th>Reclamante</th><th>Classif.</th><th>Oggetto, motivi e danno</th><th>Ricevib. &le;10gg</th><th>Riscontro &le;30gg</th><th>Misure</th><th>Esborso</th><th>Stato</th><th></th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</section>
<section class="panel info-panel">
  <div class="section-head"><h2>Cosa si fa in questa pagina</h2></div>
  <p>Qui si tiene il <strong>registro dei reclami</strong>. Quando arriva un reclamo (in forma scritta, anche via help@pariterequity.com) si <strong>annota una nuova voce</strong> entro il giorno successivo: reclamante, oggetto/motivi/danno, classificazione e stato.</p>
  <p>Le scadenze da rispettare: comunicare la <strong>ricevibilita' entro 10 giorni lavorativi</strong> e dare <strong>riscontro scritto entro 30 giorni</strong>. Se la chiusura comporta un esborso, va autorizzata dall'organo competente. Con <strong>"Esporta registro"</strong> si genera il documento ufficiale (o CSV) conforme all'Allegato 16.</p>
</section>
{modal}
"""
        self.render("Reclami", body, "complaints")

    def _send_register_html(self, title, subtitle, intestazione, headers, data_rows, note):
        head = "".join(f"<th>{esc(h)}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in r) + "</tr>" for r in data_rows) \
            or f'<tr><td colspan="{len(headers)}">Registro vuoto.</td></tr>'
        note_html = "".join(f"<p class='muted'>{esc(n)}</p>" for n in note)
        doc = f"""<!doctype html><html lang="it"><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>body{{font-family:Georgia,serif;color:#1f1b17;margin:24px;}}h1{{font-size:19px;margin:0 0 2px}}
.sub{{font-family:Consolas,monospace;font-size:11px;color:#5f5a55;margin:0 0 4px}}
table{{width:100%;border-collapse:collapse;font-size:11px;margin:10px 0}}th,td{{border:1px solid #c9c2b6;padding:5px 6px;text-align:left;vertical-align:top}}
th{{background:#efeae0}}.muted{{color:#6f6a64;font-size:11px}}</style></head><body>
<h1>{esc(title)}</h1><p class="sub">{esc(subtitle)}</p>
<p>{esc(intestazione)}</p>
<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
{note_html}
<p class="muted">Documento generato dalla compliance suite il {esc(now_iso())} - conforme al modello M12 (Allegati 14 e 16).</p>
</body></html>"""
        self.send_html(doc)

    def _send_csv(self, filename, headers, data_rows):
        out = io.StringIO()
        import csv as _csv
        w = _csv.writer(out, delimiter=";")
        w.writerow(headers)
        for r in data_rows:
            w.writerow(r)
        data = out.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def export_conflicts_register(self):
        ctx = self.get_ctx()
        fmt = self.get_query_param("format")
        conflicts = rows("SELECT * FROM conflicts WHERE platform_id = ? ORDER BY opened_at, id", (ctx["platform_id"],))
        headers = ["N.", "Data", "Soggetti coinvolti", "Natura e fonte del conflitto", "Rilevato / segnalato da",
                   "Valutazione del Consigliere Incaricato", "Misura adottata", "Esito", "Atti collegati"]
        def val(c, k, *fb):
            v = c[k] if k in c.keys() and c[k] else ""
            for f in fb:
                v = v or (c[f] if f in c.keys() and c[f] else "")
            return v
        data_rows = [[
            val(c, "reg_no") or i, nice_date(c["opened_at"]), val(c, "soggetti", "subject"), val(c, "natura_fonte", "description"),
            val(c, "rilevato_da"), val(c, "valutazione"), val(c, "misura", "mitigation"), val(c, "esito", "status"), val(c, "atti_collegati"),
        ] for i, c in enumerate(conflicts, start=1)]
        if fmt == "csv":
            self._send_csv("Registro_conflitti_interesse.csv", headers, data_rows)
            return
        self._send_register_html(
            "Registro dei conflitti di interesse", "Pariter Equity S.r.l. - modello M12 conforme all'Allegato 14",
            "Responsabile della tenuta: Consigliere Incaricato delle funzioni di controllo (Stefania Monotoni). "
            "Base: Allegato 14 - Reg. del. (UE) 2022/2111 - art. 8 ECSP.",
            headers, data_rows,
            ["Tipi di misura (Allegato 14): astensione del Soggetto Rilevante in conflitto; delibera di insussistenza di "
             "circostanze anomale; informativa ai clienti con disclaimer; in ultima istanza non ammissione dell'iniziativa.",
             "La Mappatura delle fattispecie e' documento distinto dal Registro, aggiornato almeno annualmente e validato dal CdA."],
        )

    def export_complaints_register(self):
        ctx = self.get_ctx()
        fmt = self.get_query_param("format")
        complaints = rows("SELECT * FROM complaints WHERE platform_id = ? ORDER BY received_at, id", (ctx["platform_id"],))
        headers = ["N. / Prot.", "Data ricezione", "Reclamante", "Classificazione", "Oggetto, motivi e danno lamentato",
                   "Comunic. ricevibilita (<=10 gg lav.)", "Data riscontro (<=30 gg)", "Misure adottate per il riscontro",
                   "Esborso", "Stato"]
        def val(c, k, *fb):
            v = c[k] if k in c.keys() and c[k] else ""
            for f in fb:
                v = v or (c[f] if f in c.keys() and c[f] else "")
            return v
        data_rows = [[
            val(c, "protocollo") or i, nice_date(c["received_at"]), c["complainant"], val(c, "classificazione") or "Reclamo",
            val(c, "motivi_danno", "object"),
            nice_date(c["ricevibilita_date"]) if val(c, "ricevibilita_date") else "",
            nice_date(c["riscontro_date"]) if val(c, "riscontro_date") else "",
            val(c, "misure", "outcome"), val(c, "esborso") or "No", c["status"],
        ] for i, c in enumerate(complaints, start=1)]
        if fmt == "csv":
            self._send_csv("Registro_reclami.csv", headers, data_rows)
            return
        self._send_register_html(
            "Registro dei reclami", "Pariter Equity S.r.l. - modello M12 conforme all'Allegato 16",
            "Responsabile: Responsabile Reclami (Fabio Malerba). Base: Allegato 16 - art. 7 §3 ECSP - Reg. del. (UE) 2022/2117. "
            "Tenuto in forma elettronica; annotazione entro il giorno successivo alla ricezione.",
            headers, data_rows,
            ["Tempistiche (Allegato 16): ricevibilita entro 10 giorni lavorativi; riscontro scritto entro 30 giorni solari; "
             "il reclamo si considera chiuso decorsi 180 giorni dalla risposta senza contestazioni.",
             "Campi obbligatori art. 7 §3 ECSP: data di ricezione; estremi essenziali (motivi e danno); data di evasione; "
             "misure adottate; stato (pendente/chiuso/accolto/respinto)."],
        )

    def page_proponents(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        params = parse_qs(urlparse(self.path).query)
        mode = (params.get("mode") or [""])[0]
        proponents = rows(
            """
            SELECT p.*, COUNT(DISTINCT d.id) AS deal_count, COUNT(DISTINCT doc.id) AS doc_count
            FROM proponents p
            LEFT JOIN deals d ON d.proponent_id = p.id
            LEFT JOIN documents doc ON doc.proponent_id = p.id
            WHERE p.platform_id = ?
            GROUP BY p.id
            ORDER BY p.name
            """,
            (pid,),
        )
        api_linked = sum(1 for p in proponents if (p["source_system"] or "").startswith("adapter:"))
        active_pipeline = sum(1 for p in proponents if p["crm_status"] in {"Attivo", "Prioritario", "In istruttoria"})
        missing_docs = sum(1 for p in proponents if p["onboarding_status"] in {"Documenti da raccogliere", "Integrazione richiesta"})
        create = f'<a class="button primary" href="{rel_url("/proponents", ctx, {"mode": "new"})}">+ Nuovo proponente</a>'
        create_modal = ""
        if mode == "new" and user_can(ctx["user"], "manage_proponents"):
            create_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal">
    <div class="section-head">
      <h2>Nuovo proponente</h2>
      <a class="modal-close" href="{rel_url('/proponents', ctx)}">x</a>
    </div>
    <form class="form-grid" method="post" action="/proponents/create">
      {hidden_ctx(ctx)}
      <label>Ragione sociale<input name="name" required></label>
      <label>Forma giuridica<input name="legal_form"></label>
      <label>Codice fiscale / VAT<input name="tax_id"></label>
      <label>Email referente<input name="contact_email" type="email"></label>
      <label>Telefono<input name="phone"></label>
      <label>Sito web<input name="website"></label>
      <label>Settore<input name="sector" placeholder="es. MedTech, Industria sostenibile"></label>
      <label>Titolari effettivi<textarea name="beneficial_owners" rows="3"></textarea></label>
      <label>Esposizione<input name="exposure" type="number" min="0" step="1000" value="0"></label>
      <label>Score interno<select name="internal_score"><option>Da valutare</option><option>A</option><option>A-</option><option>B+</option><option>B</option><option>C</option></select></label>
      <label>Stato CRM<select name="crm_status"><option>In istruttoria</option><option>Prioritario</option><option>Attivo</option><option>In pausa</option><option>Archiviato</option></select></label>
      <label>Onboarding<select name="onboarding_status"><option>Documenti da raccogliere</option><option>Documentazione in verifica</option><option>Integrazione richiesta</option><option>Comitato tecnico</option><option>Pronto per CdA</option><option>Completo</option></select></label>
      <label>Owner interno<input name="owner_name" placeholder="Responsabile dossier"></label>
      <label>Fonte dato<select name="source_system"><option>Manuale</option><option>adapter:future-platform-api</option><option>Import CSV</option><option>Backoffice</option></select></label>
      <label>ID esterno<input name="external_proponent_id" placeholder="ID piattaforma sorgente"></label>
      <label class="full-span">Prossima azione<input name="next_action" placeholder="es. richiedere business plan aggiornato"></label>
      <label>Note<textarea name="notes" rows="3"></textarea></label>
      <div class="form-actions left"><button class="button primary" type="submit">Crea proponente</button><a class="button ghost" href="{rel_url('/proponents', ctx)}">Annulla</a></div>
    </form>
  </section>
</div>"""
        body_rows = "".join(
            f"""<tr>
              <td><a href="{rel_url('/proponents/' + str(p['id']), ctx)}"><strong>{esc(p['name'])}</strong></a></td>
              <td>{esc(p['sector'] or '-')}<br><span class="muted">{esc(p['legal_form'])}</span></td>
              <td><span class="badge {badge_class(p['crm_status'])}">{esc(p['crm_status'])}</span><br><span class="muted">{esc(p['owner_name'] or '-')}</span></td>
              <td>{esc(p['internal_score'])}</td>
              <td>{money(p['exposure'])}</td>
              <td>{p['deal_count']}</td>
              <td>{p['doc_count']}</td>
              <td>{esc(p['source_system'] or 'Manuale')}<br><span class="muted">{esc(nice_date(p['last_synced_at']))}</span></td>
            </tr>"""
            for p in proponents
        )
        body = f"""
<section class="metric-grid">
  <div class="metric"><span>Proponenti</span><strong>{len(proponents)}</strong></div>
  <div class="metric"><span>Pipeline attiva</span><strong>{active_pipeline}</strong></div>
  <div class="metric"><span>Documenti aperti</span><strong>{missing_docs}</strong></div>
  <div class="metric"><span>Da API</span><strong>{api_linked}</strong></div>
</section>
<section class="panel">
  <div class="section-head"><h2>Dossier proponenti</h2>{create}</div>
  <table class="data-table roomy">
    <thead><tr><th>Proponente</th><th>Settore</th><th>CRM / owner</th><th>Score</th><th>Esposizione</th><th>Deal</th><th>Doc.</th><th>Fonte</th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
</section>
{create_modal}
"""
        self.render("Proponenti", body, "proponents")

    def page_proponent_new(self):
        ctx = self.get_ctx()
        body = f"""
<section class="panel narrow">
  <div class="section-head"><h2>Nuovo proponente</h2></div>
  <form class="form-grid" method="post" action="/proponents/create">
    {hidden_ctx(ctx)}
    <label>Ragione sociale<input name="name" required></label>
    <label>Forma giuridica<input name="legal_form"></label>
    <label>Codice fiscale / VAT<input name="tax_id"></label>
    <label>Email referente<input name="contact_email" type="email"></label>
    <label>Telefono<input name="phone"></label>
    <label>Sito web<input name="website"></label>
    <label>Settore<input name="sector" placeholder="es. MedTech, Industria sostenibile"></label>
    <label>Titolari effettivi<textarea name="beneficial_owners" rows="3"></textarea></label>
    <label>Esposizione<input name="exposure" type="number" min="0" step="1000" value="0"></label>
    <label>Score interno<select name="internal_score"><option>Da valutare</option><option>A</option><option>A-</option><option>B+</option><option>B</option><option>C</option></select></label>
    <label>Stato CRM<select name="crm_status"><option>In istruttoria</option><option>Prioritario</option><option>Attivo</option><option>In pausa</option><option>Archiviato</option></select></label>
    <label>Onboarding<select name="onboarding_status"><option>Documenti da raccogliere</option><option>Documentazione in verifica</option><option>Integrazione richiesta</option><option>Comitato tecnico</option><option>Pronto per CdA</option><option>Completo</option></select></label>
    <label>Owner interno<input name="owner_name" placeholder="Responsabile dossier"></label>
    <label>Fonte dato<select name="source_system"><option>Manuale</option><option>adapter:future-platform-api</option><option>Import CSV</option><option>Backoffice</option></select></label>
    <label>ID esterno<input name="external_proponent_id" placeholder="ID piattaforma sorgente"></label>
    <label class="full-span">Prossima azione<input name="next_action" placeholder="es. richiedere business plan aggiornato"></label>
    <label>Note<textarea name="notes" rows="3"></textarea></label>
    <div class="form-actions"><button class="button primary" type="submit">Crea proponente</button></div>
  </form>
</section>
"""
        self.render("Nuovo proponente", body, "proponents")

    def page_proponent_detail(self, proponent_id):
        ctx = self.get_ctx()
        params = parse_qs(urlparse(self.path).query)
        mode = (params.get("mode") or [""])[0]
        prop = row("SELECT * FROM proponents WHERE id = ? AND platform_id = ?", (proponent_id, ctx["platform_id"]))
        if not prop:
            self.not_found()
            return
        deals = rows("SELECT * FROM deals WHERE proponent_id = ? ORDER BY updated_at DESC", (proponent_id,))
        docs = rows("SELECT * FROM documents WHERE proponent_id = ? ORDER BY created_at DESC", (proponent_id,))
        audit = rows(
            """
            SELECT * FROM audit_log
            WHERE platform_id = ? AND (
              (entity_type = 'proponent' AND entity_id = ?)
              OR (entity_type = 'deal' AND entity_id IN (SELECT id FROM deals WHERE proponent_id = ?))
            )
            ORDER BY created_at DESC
            LIMIT 8
            """,
            (ctx["platform_id"], proponent_id, proponent_id),
        )
        total_target = sum(float(d["funding_target"] or 0) for d in deals)
        active_deals = sum(1 for d in deals if d["phase"] not in {"concluso", "respinta", "archiviato"})
        doc_categories = {d["category"] for d in docs}
        required_docs = ["Visura camerale", "Bilanci", "Business plan", "KYC", "KIIS", "Contratto"]
        missing_docs = [item for item in required_docs if not any(item.lower().split()[0] in cat.lower() or item.lower().split()[0] in d["title"].lower() for cat in doc_categories for d in docs)]
        data_source_label = prop["source_system"] or "Manuale"
        crm_items = [
            ("Stato CRM", prop["crm_status"], "Priorita commerciale e operativa del dossier."),
            ("Onboarding", prop["onboarding_status"], "Avanzamento raccolta e verifica documentale."),
            ("Score interno", prop["internal_score"], "Valutazione sintetica rischio/opportunita."),
            ("Fonte", data_source_label, "Origine dato: API piattaforma, backoffice o manuale."),
        ]
        crm_html = "".join(
            f"""<div class="crm-check">
              <span>{esc(label)}</span>
              <strong><span class="badge {badge_class(status)}">{esc(status)}</span></strong>
              <small>{esc(note)}</small>
            </div>"""
            for label, status, note in crm_items
        )
        deal_rows = "".join(
            f"""<tr><td><a href="{rel_url('/deals/' + str(d['id']), ctx)}">{esc(d['title'])}</a></td><td>{esc(status_for_phase(d['phase']))}</td><td>{esc(phase_label(d['phase']))}</td><td>{money(d['funding_target'])}</td></tr>"""
            for d in deals
        ) or '<tr><td colspan="4" class="empty-row">Nessun deal collegato.</td></tr>'
        doc_rows = "".join(
            f"""<tr><td>{esc(d['category'])}</td><td><a href="{rel_url('/documents/' + str(d['id']) + '/download', ctx)}">{esc(d['title'])}</a></td><td>{esc(nice_date(d['created_at']))}</td></tr>"""
            for d in docs
        ) or '<tr><td colspan="3" class="empty-row">Nessun documento caricato.</td></tr>'
        timeline_items = [
            (prop["created_at"], "Creazione proponente", f"Profilo creato con score {prop['internal_score']}."),
        ]
        for d in deals:
            timeline_items.append((d["updated_at"], f"Deal: {d['title']}", f"{phase_label(d['phase'])} - {money(d['funding_target'])}"))
        for event in audit:
            timeline_items.append((event["created_at"], event["action"], event["details"]))
        timeline_items = sorted(timeline_items, key=lambda item: item[0] or "", reverse=True)[:10]
        timeline_html = "".join(
            f"""<li>
              <span>{esc(nice_date(moment))}</span>
              <strong>{esc(title)}</strong>
              <small>{esc(detail or '-')}</small>
            </li>"""
            for moment, title, detail in timeline_items
        )
        actions = []
        if prop["onboarding_status"] in {"Documenti da raccogliere", "Integrazione richiesta"}:
            actions.append("Richiedere o caricare la documentazione mancante prima di avanzare il dossier.")
        if missing_docs:
            actions.append(f"Documenti da verificare: {', '.join(missing_docs[:4])}.")
        if prop["next_action"]:
            actions.append(prop["next_action"])
        if not deals:
            actions.append("Creare o collegare un deal per trasformare il proponente in pipeline operativa.")
        if not actions:
            actions.append("Mantenere monitoraggio periodico su documenti, scoring e stato del deal.")
        action_html = "".join(f"<li>{esc(item)}</li>" for item in actions)

        edit_button = ""
        upload_button = ""
        edit_modal = ""
        upload_modal = ""
        if user_can(ctx["user"], "manage_proponents"):
            edit_button = f'<a class="button primary" href="{rel_url("/proponents/" + str(proponent_id), ctx, {"mode": "edit"})}">Modifica proponente</a>'
        if user_can(ctx["user"], "upload_document"):
            upload_button = f'<a class="button ghost" href="{rel_url("/proponents/" + str(proponent_id), ctx, {"mode": "upload"})}">+ Documento</a>'
        if mode == "edit" and user_can(ctx["user"], "manage_proponents"):
            edit_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal">
    <div class="section-head">
      <h2>Modifica proponente</h2>
      <a class="modal-close" href="{rel_url('/proponents/' + str(proponent_id), ctx)}">x</a>
    </div>
    <form class="form-grid" method="post" action="/proponents/{proponent_id}/update">
      {hidden_ctx(ctx)}
      <label>Ragione sociale<input name="name" value="{esc(prop['name'])}" required></label>
      <label>Forma giuridica<input name="legal_form" value="{esc(prop['legal_form'])}"></label>
      <label>Codice fiscale / VAT<input name="tax_id" value="{esc(prop['tax_id'])}"></label>
      <label>Email referente<input name="contact_email" type="email" value="{esc(prop['contact_email'])}"></label>
      <label>Telefono<input name="phone" value="{esc(prop['phone'])}"></label>
      <label>Sito web<input name="website" value="{esc(prop['website'])}"></label>
      <label>Settore<input name="sector" value="{esc(prop['sector'])}"></label>
      <label>Esposizione<input name="exposure" type="number" min="0" step="1000" value="{esc(prop['exposure'])}"></label>
      <label>Score interno<select name="internal_score">{option_values(['Da valutare', 'A', 'A-', 'B+', 'B', 'C'], prop['internal_score'])}</select></label>
      <label>Stato CRM<select name="crm_status">{option_values(['In istruttoria', 'Prioritario', 'Attivo', 'In pausa', 'Archiviato'], prop['crm_status'])}</select></label>
      <label>Onboarding<select name="onboarding_status">{option_values(['Documenti da raccogliere', 'Documentazione in verifica', 'Integrazione richiesta', 'Comitato tecnico', 'Pronto per CdA', 'Completo'], prop['onboarding_status'])}</select></label>
      <label>Owner interno<input name="owner_name" value="{esc(prop['owner_name'])}"></label>
      <label>Fonte dato<select name="source_system">{option_values(['Manuale', 'adapter:future-platform-api', 'Import CSV', 'Backoffice'], prop['source_system'])}</select></label>
      <label>ID esterno<input name="external_proponent_id" value="{esc(prop['external_proponent_id'])}"></label>
      <label class="full-span">Titolari effettivi<textarea name="beneficial_owners" rows="3">{esc(prop['beneficial_owners'])}</textarea></label>
      <label class="full-span">Prossima azione<input name="next_action" value="{esc(prop['next_action'])}"></label>
      <label class="full-span">Note override manuale<textarea name="manual_override_notes" rows="3">{esc(prop['manual_override_notes'])}</textarea></label>
      <label class="full-span">Note CRM / istruttoria<textarea name="notes" rows="3">{esc(prop['notes'])}</textarea></label>
      <div class="form-actions left"><button class="button primary" type="submit">Salva modifiche</button><a class="button ghost" href="{rel_url('/proponents/' + str(proponent_id), ctx)}">Annulla</a></div>
    </form>
  </section>
</div>"""
        if mode == "upload" and user_can(ctx["user"], "upload_document"):
            upload_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal entity-modal">
    <div class="section-head">
      <h2>Carica documento proponente</h2>
      <a class="modal-close" href="{rel_url('/proponents/' + str(proponent_id), ctx)}">x</a>
    </div>
    <form class="form-grid" method="post" action="/documents/upload" enctype="multipart/form-data">
      {hidden_ctx(ctx)}
      <input type="hidden" name="entity_kind" value="proponent">
      <input type="hidden" name="proponent_id" value="{proponent_id}">
      <input type="hidden" name="origin" value="Proponente">
      <label>Tipo documento<input name="category" list="proponent-document-types" value="Documentazione societaria"></label>
      <label>Titolo<input name="title" value="Documento {esc(prop['name'])}"></label>
      <label class="full-span">File<input name="file" type="file" required></label>
      <datalist id="proponent-document-types">
        <option value="Visura camerale"><option value="Statuto"><option value="Atto societario"><option value="Bilancio"><option value="Business plan"><option value="KYC / AML"><option value="KIIS"><option value="Contratto"><option value="Due diligence"><option value="Domanda autorizzazione ECSP">
      </datalist>
      <div class="form-actions left"><button class="button primary" type="submit">Carica documento</button><a class="button ghost" href="{rel_url('/proponents/' + str(proponent_id), ctx)}">Annulla</a></div>
    </form>
  </section>
</div>"""
        body = f"""
<section class="deal-header">
  <div>
    <p class="eyebrow">CRM proponente - {esc(prop['sector'] or prop['legal_form'])}</p>
    <h2>{esc(prop['name'])}</h2>
    <p class="muted">{esc(prop['contact_email'] or 'email non indicata')} - {esc(prop['phone'] or 'telefono non indicato')}</p>
  </div>
  <div class="header-badges">
    <span class="badge neutral">{esc(prop['internal_score'])}</span>
    <span class="badge {badge_class(prop['crm_status'])}">{esc(prop['crm_status'])}</span>
    {edit_button}
    {upload_button}
    <a class="button ghost" href="{rel_url('/proponents', ctx)}">Torna ai proponenti</a>
  </div>
</section>
<section class="metric-grid">
  <div class="metric"><span>Deal collegati</span><strong>{len(deals)}</strong></div>
  <div class="metric"><span>Pipeline attiva</span><strong>{active_deals}</strong></div>
  <div class="metric"><span>Target totale</span><strong>{money(total_target)}</strong></div>
  <div class="metric"><span>Documenti</span><strong>{len(docs)}</strong></div>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Anagrafica e relazione</h2><span class="panel-kicker">{esc(prop['owner_name'] or 'owner da assegnare')}</span></div>
    <dl class="definition-list">
      <dt>Forma giuridica</dt><dd>{esc(prop['legal_form'])}</dd>
      <dt>Codice fiscale / VAT</dt><dd>{esc(prop['tax_id'])}</dd>
      <dt>Referente</dt><dd>{esc(prop['contact_email'])}</dd>
      <dt>Telefono</dt><dd>{esc(prop['phone'] or '-')}</dd>
      <dt>Sito</dt><dd>{esc(prop['website'] or '-')}</dd>
      <dt>Settore</dt><dd>{esc(prop['sector'] or '-')}</dd>
      <dt>Titolari effettivi</dt><dd>{esc(prop['beneficial_owners'])}</dd>
      <dt>Esposizione</dt><dd>{money(prop['exposure'])}</dd>
      <dt>Note</dt><dd>{esc(prop['notes'])}</dd>
    </dl>
  </div>
  <div class="panel">
    <div class="section-head"><h2>CRM e istruttoria</h2><span class="panel-kicker">stato dossier</span></div>
    <div class="crm-check-grid">{crm_html}</div>
  </div>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Dati piattaforma e override</h2><span class="panel-kicker">API + manuale</span></div>
    <dl class="definition-list">
      <dt>Fonte</dt><dd>{esc(data_source_label)}</dd>
      <dt>ID esterno</dt><dd>{esc(prop['external_proponent_id'] or '-')}</dd>
      <dt>Ultimo sync</dt><dd>{esc(nice_date(prop['last_synced_at']))}</dd>
      <dt>Override</dt><dd>{esc(prop['manual_override_notes'] or 'Nessuna correzione manuale registrata.')}</dd>
      <dt>Prossima azione</dt><dd>{esc(prop['next_action'] or '-')}</dd>
    </dl>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Azioni operative</h2><span class="panel-kicker">CRM</span></div>
    <ul class="plain-list">{action_html}</ul>
  </div>
</section>
<section class="panel">
  <div class="section-head"><h2>Deal collegati</h2></div>
  <table class="data-table compact"><thead><tr><th>Deal</th><th>Stato</th><th>Fase</th><th>Target</th></tr></thead><tbody>{deal_rows}</tbody></table>
</section>
<section class="panel">
  <div class="section-head"><h2>Documenti proponente</h2>{upload_button}</div>
  <table class="data-table compact"><thead><tr><th>Categoria</th><th>Titolo</th><th>Data</th></tr></thead><tbody>{doc_rows}</tbody></table>
</section>
<section class="panel">
  <div class="section-head"><h2>Timeline dossier</h2><span class="panel-kicker">audit</span></div>
  <ol class="crm-timeline">{timeline_html}</ol>
</section>
{edit_modal}
{upload_modal}
"""
        self.render(prop["name"], body, "proponents")

    def page_communications(self):
        ctx = self.get_ctx()
        params = parse_qs(urlparse(self.path).query)
        active_tab = (params.get("tab") or ["scadenzario"])[0]
        if active_tab not in {"scadenzario", "generatore", "obblighi", "fonti"}:
            active_tab = "scadenzario"
        selected_id = (params.get("comm") or [COMMUNICATION_WORKFLOWS[0]["id"]])[0]
        selected = next((item for item in COMMUNICATION_WORKFLOWS if item["id"] == selected_id), COMMUNICATION_WORKFLOWS[0])
        output_id = int((params.get("output") or ["0"])[0] or 0)
        edit_output = (params.get("edit") or [""])[0] == "1"
        selected_output = None
        if output_id:
            selected_output = row(
                """
                SELECT co.*, doc.title AS document_title
                FROM communication_outputs co
                LEFT JOIN documents doc ON doc.id = co.document_id
                WHERE co.id = ? AND co.platform_id = ? AND co.workflow_id = ?
                """,
                (output_id, ctx["platform_id"], selected["id"]),
            )
        if selected_output and not self.communication_output_is_editable(selected_output["status"]):
            edit_output = False
        tabs = [
            ("scadenzario", "Scadenzario"),
            ("generatore", "Generatore"),
            ("obblighi", "Obblighi"),
            ("fonti", "Fonti ufficiali"),
        ]
        tab_html = "".join(
            f'<a class="subtab {"active" if key == active_tab else ""}" href="{rel_url("/communications", ctx, {"tab": key, "comm": selected["id"]})}">{label}</a>'
            for key, label in tabs
        )
        if active_tab == "generatore":
            content = self.communication_generator_view(ctx, selected, selected_output, edit_output)
        elif active_tab == "obblighi":
            content = self.communication_obligations_view(ctx)
        elif active_tab == "fonti":
            content = self.communication_sources_view(ctx)
        else:
            content = self.communication_schedule_view(ctx, selected)
        body = f"""
<p class="page-copy">Registro operativo delle comunicazioni: scadenze, stato, storico e generazione guidata dei file da inviare o archiviare.</p>
<nav class="subtabs">{tab_html}</nav>
{content}
"""
        self.render("Comunicazioni", body, "communications")

    def communication_workflow_by_id(self, workflow_id):
        return next((item for item in COMMUNICATION_WORKFLOWS if item["id"] == workflow_id), COMMUNICATION_WORKFLOWS[0])

    def communication_status_meta(self, status, due_date):
        normalized = (status or "").lower()
        if "respinta" in normalized:
            return "status-rejected", status
        if "approvata" in normalized or "conclusa" in normalized:
            return "status-done", status
        if "inviata" in normalized:
            return "status-sent", status
        if "validata" in normalized or "da inviare" in normalized:
            return "status-ready", status
        if "bozza" in normalized:
            return "status-draft", status
        if due_date:
            try:
                days = (date.fromisoformat(due_date) - date.today()).days
                if days < 0:
                    return "status-urgent", f"Scaduta da {-days} gg"
                if days <= 10:
                    return "status-urgent", f"Urgente - {days} gg"
                if days <= 30:
                    return "status-soon", f"Vicino - {days} gg"
                return "status-due", f"Da fare - {days} gg"
            except ValueError:
                pass
        return "status-due", status or "Da fare"

    def communication_output_is_editable(self, status):
        normalized = (status or "").lower()
        return not any(final in normalized for final in ("inviata", "approvata", "respinta", "conclusa"))

    def communication_output_rows(self, ctx, workflow_id=None, limit=20):
        where = "WHERE co.platform_id = ?"
        args = [ctx["platform_id"]]
        if workflow_id:
            where += " AND co.workflow_id = ?"
            args.append(workflow_id)
        return rows(
            f"""
            SELECT co.*, doc.title AS document_title, doc.storage_path
            FROM communication_outputs co
            LEFT JOIN documents doc ON doc.id = co.document_id
            {where}
            ORDER BY co.created_at DESC
            LIMIT ?
            """,
            (*args, limit),
        )

    def communication_latest_output_by_workflow(self, ctx):
        latest = {}
        for item in self.communication_output_rows(ctx, None, 200):
            latest.setdefault(item["workflow_id"], item)
        return latest

    def communication_output_status_form(self, ctx, output, workflow_id):
        statuses = ["Bozza generata", "Validata - da inviare", "Inviata", "Approvata", "Respinta"]
        options = option_values(statuses, output["status"])
        return f"""
        <form class="inline-form comm-output-state" method="post" action="/communications/output-status">
          {hidden_ctx(ctx)}
          <input type="hidden" name="output_id" value="{output['id']}">
          <input type="hidden" name="workflow_id" value="{esc(workflow_id)}">
          <select name="status">{options}</select>
          <button class="button secondary" type="submit">Aggiorna</button>
        </form>"""

    def communication_output_table(self, ctx, item):
        outputs = self.communication_output_rows(ctx, item["id"], 12)
        rows_html = []
        for output in outputs:
            status_class, status_label = self.communication_status_meta(output["status"], "")
            document_title = output["document_title"] or "Documento rimosso / scollegato"
            download = (
                f'<a class="button ghost" href="{rel_url("/documents/" + str(output["document_id"]) + "/download", ctx)}">Scarica</a>'
                if output["document_id"]
                else '<span class="muted">Nessun file</span>'
            )
            open_link = rel_url("/communications", ctx, {"tab": "generatore", "comm": item["id"], "output": output["id"]})
            edit_link = (
                f'<a class="button primary" href="{rel_url("/communications", ctx, {"tab": "generatore", "comm": item["id"], "output": output["id"], "edit": "1"})}">Modifica</a>'
                if self.communication_output_is_editable(output["status"])
                else ""
            )
            rows_html.append(
                f"""<tr>
                  <td>
                    <strong><a href="{open_link}">{esc(document_title)}</a></strong>
                    <span class="archive-file">Creato {esc(nice_date(output['created_at']))} - aggiornato {esc(nice_date(output['updated_at']))}</span>
                  </td>
                  <td><span class="comm-status {status_class}">{esc(status_label)}</span></td>
                  <td>{esc(output['reviewer'] or '-')}</td>
                  <td>{self.communication_output_status_form(ctx, output, item['id'])}</td>
                  <td>
                    <div class="inline-actions">
                      <a class="button secondary" href="{open_link}">Vedi</a>
                      {edit_link}
                      {download}
                      <form method="post" action="/communications/output-delete">
                        {hidden_ctx(ctx)}
                        <input type="hidden" name="output_id" value="{output['id']}">
                        <input type="hidden" name="workflow_id" value="{esc(item['id'])}">
                        <button class="button danger-button" type="submit">Rimuovi</button>
                      </form>
                    </div>
                  </td>
                </tr>"""
            )
        if not rows_html:
            rows_html.append('<tr><td colspan="5" class="empty-row">Nessuna bozza ancora generata per questa comunicazione.</td></tr>')
        return f"""
<section class="panel communication-output-panel">
  <div class="section-head">
    <div><h2>Bozze e storico della comunicazione</h2><span class="muted">Qui rientrano le bozze appena generate, la validazione interna, l'invio e l'esito finale.</span></div>
    <span class="panel-kicker">flusso documento</span>
  </div>
  <ol class="communication-flow">
    <li><span>1</span><strong>Bozza generata</strong><small>Scaricabile e rimovibile.</small></li>
    <li><span>2</span><strong>Validata</strong><small>Pronta da inviare.</small></li>
    <li><span>3</span><strong>Inviata</strong><small>In attesa esito/ricevuta.</small></li>
    <li><span>4</span><strong>Approvata o respinta</strong><small>Resta nello storico della comunicazione.</small></li>
  </ol>
  <div class="table-scroll">
    <table class="data-table compact communication-output-table">
      <thead><tr><th>Output</th><th>Stato</th><th>Validatore</th><th>Aggiorna stato</th><th>Azioni</th></tr></thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
  </div>
</section>
"""

    def communication_schedule_view(self, ctx, selected):
        latest_outputs = self.communication_latest_output_by_workflow(ctx)
        enriched = []
        for item in COMMUNICATION_SCHEDULE:
            copy_item = dict(item)
            latest = latest_outputs.get(item["workflow_id"])
            if latest:
                copy_item["status"] = latest["status"]
                copy_item["note"] = f"{item['note']} Ultimo output: {latest['document_title'] or 'documento rimosso'}."
                copy_item["output_id"] = latest["id"]
                copy_item["document_id"] = latest["document_id"]
            enriched.append(copy_item)
        active_statuses = {"Da fare", "Bozza generata", "Validata - da inviare", "Inviata", "Respinta"}
        entries = enriched
        active_entries = [item for item in enriched if item["status"] in active_statuses]
        completed = [item for item in enriched if item["status"] not in active_statuses]
        schedule_rows = "".join(self.communication_schedule_row(ctx, item) for item in entries)
        history_rows = "".join(self.communication_history_row(ctx, item) for item in completed)
        generated = self.communication_output_rows(ctx, None, 6)
        draft_rows = "".join(
            f"""<tr>
              <td><strong><a href="{rel_url('/communications', ctx, {'tab': 'generatore', 'comm': doc['workflow_id'], 'output': doc['id']})}">{esc(doc['document_title'] or 'Documento rimosso')}</a></strong></td>
              <td><span class="comm-status {self.communication_status_meta(doc['status'], '')[0]}">{esc(doc['status'])}</span></td>
              <td>{esc(nice_date(doc['created_at']))}</td>
            </tr>"""
            for doc in generated
        ) or '<tr><td colspan="3" class="empty-row">Nessuna bozza generata.</td></tr>'
        return f"""
<section class="metric-grid communication-metrics">
  <div class="metric"><span>Da fare</span><strong>{len(active_entries)}</strong></div>
  <div class="metric"><span>Inviate</span><strong>{sum(1 for item in completed if item['status'] == 'Inviata')}</strong></div>
  <div class="metric"><span>Approvate / concluse</span><strong>{sum(1 for item in completed if item['status'] in {'Approvata', 'Conclusa'})}</strong></div>
  <div class="metric"><span>Generatori attivi</span><strong>{len(COMMUNICATION_WORKFLOWS)}</strong></div>
</section>
<section class="panel">
  <div class="section-head">
    <div><h2>Scadenzario comunicazioni</h2><span class="muted">Le comunicazioni da fare cambiano colore con l'avvicinarsi della scadenza.</span></div>
    <span class="panel-kicker">clic su una riga per compilarla</span>
  </div>
  <div class="table-scroll">
    <table class="data-table roomy communication-register-table">
      <thead><tr><th>Comunicazione</th><th>Periodo</th><th>Scadenza</th><th>Stato</th><th>Owner</th><th>Azione</th></tr></thead>
      <tbody>{schedule_rows}</tbody>
    </table>
  </div>
</section>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Storico comunicazioni</h2><span class="panel-kicker">inviate, approvate, concluse</span></div>
    <table class="data-table compact">
      <thead><tr><th>Comunicazione</th><th>Periodo</th><th>Scadenza</th><th>Stato</th></tr></thead>
      <tbody>{history_rows}</tbody>
    </table>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Bozze generate</h2><span class="panel-kicker">archivio Comunicazioni</span></div>
    <table class="data-table compact">
      <thead><tr><th>Documento</th><th>Stato</th><th>Data</th></tr></thead>
      <tbody>{draft_rows}</tbody>
    </table>
  </div>
</section>
"""

    def communication_schedule_row(self, ctx, item):
        workflow = self.communication_workflow_by_id(item["workflow_id"])
        status_class, status_label = self.communication_status_meta(item["status"], item["due_date"])
        due = nice_date(item["due_date"]) if item["due_date"] else "Evento / prima dell'avvio"
        open_params = {"tab": "generatore", "comm": workflow["id"]}
        if item.get("output_id"):
            open_params["output"] = item["output_id"]
        action_html = self.communication_schedule_actions(ctx, item, workflow, open_params)
        return f"""<tr>
          <td><strong>{esc(workflow['title'])}</strong><br><span class="muted">{esc(item['note'])}</span></td>
          <td>{esc(item['period'])}</td>
          <td>{esc(due)}</td>
          <td><span class="comm-status {status_class}">{esc(status_label)}</span></td>
          <td>{esc(item['owner'])}</td>
          <td><div class="inline-actions">{action_html}</div></td>
        </tr>"""

    def communication_schedule_actions(self, ctx, item, workflow, open_params):
        status = item.get("status") or "Da fare"
        if not item.get("output_id"):
            label = "Genera" if status == "Da fare" else "Vedi"
            button_class = "primary" if status == "Da fare" else "secondary"
            return f'<a class="button {button_class}" href="{rel_url("/communications", ctx, {"tab": "generatore", "comm": workflow["id"]})}">{label}</a>'
        open_url = rel_url("/communications", ctx, open_params)
        download = (
            f'<a class="button ghost" href="{rel_url("/documents/" + str(item["document_id"]) + "/download", ctx)}">Scarica</a>'
            if item.get("document_id")
            else ""
        )
        if self.communication_output_is_editable(status):
            edit_params = dict(open_params)
            edit_params["edit"] = "1"
            return (
                f'<a class="button secondary" href="{open_url}">Vedi</a>'
                f'<a class="button primary" href="{rel_url("/communications", ctx, edit_params)}">Modifica</a>'
                f'{download}'
            )
        return f'<a class="button secondary" href="{open_url}">Vedi</a>{download}'

    def communication_history_row(self, ctx, item):
        workflow = self.communication_workflow_by_id(item["workflow_id"])
        status_class, status_label = self.communication_status_meta(item["status"], item["due_date"])
        due = nice_date(item["due_date"]) if item["due_date"] else "-"
        open_params = {"tab": "generatore", "comm": workflow["id"]}
        if item.get("output_id"):
            open_params["output"] = item["output_id"]
        return f"""<tr>
          <td><strong><a href="{rel_url('/communications', ctx, open_params)}">{esc(workflow['title'])}</a></strong></td>
          <td>{esc(item['period'])}</td>
          <td>{esc(due)}</td>
          <td><span class="comm-status {status_class}">{esc(status_label)}</span></td>
        </tr>"""

    def communication_generator_view(self, ctx, selected, selected_output=None, edit_output=False):
        picker_rows = "".join(self.communication_generator_picker_row(ctx, item, selected["id"]) for item in COMMUNICATION_WORKFLOWS)
        return f"""
<section class="communication-generator-shell">
  <details class="panel communication-picker">
    <summary>
      <div>
        <span class="panel-kicker">seleziona comunicazione</span>
        <strong>{esc(selected['title'])}</strong>
        <small>{esc(selected['authority'])} - {esc(selected['channel'])}</small>
      </div>
      <span class="picker-trigger">Cambia</span>
    </summary>
    <div class="record-list">{picker_rows}</div>
  </details>
  <div class="communication-generator-main">{self.communication_workflow_form(ctx, selected, selected_output, edit_output)}</div>
</section>
"""

    def communication_generator_picker_row(self, ctx, item, selected_id):
        active = " active" if item["id"] == selected_id else ""
        return f"""<a class="record-item{active}" href="{rel_url('/communications', ctx, {'tab': 'generatore', 'comm': item['id']})}">
          <strong>{esc(item['title'])}</strong>
          <span>{esc(item['authority'])} - {esc(item['channel'])}</span>
          <small>{esc(item['frequency'])}</small>
        </a>"""

    def communication_documents(self, ctx):
        docs = rows(
            """
            SELECT id, title, category, origin
            FROM documents
            WHERE platform_id = ? AND generated = 0 AND origin != 'Comunicazioni'
            ORDER BY created_at DESC
            """,
            (ctx["platform_id"],),
        )
        person_docs = rows(
            """
            SELECT document_id AS id, title, document_type AS category, 'Persona' AS origin
            FROM person_documents
            WHERE platform_id = ?
            ORDER BY created_at DESC
            """,
            (ctx["platform_id"],),
        )
        supplier_docs = rows(
            """
            SELECT document_id AS id, title, contract_type AS category, 'Fornitore' AS origin
            FROM supplier_contracts
            WHERE platform_id = ?
            ORDER BY created_at DESC
            """,
            (ctx["platform_id"],),
        )
        return list(docs) + list(person_docs) + list(supplier_docs)

    def communication_doc_match(self, docs, requested):
        needle = re.sub(r"[^a-z0-9]+", " ", (requested or "").lower()).strip()
        if not needle:
            return None
        parts = [part for part in needle.split() if len(part) > 2]
        for doc in docs:
            hay = re.sub(
                r"[^a-z0-9]+",
                " ",
                f"{doc['title']} {doc['category']} {doc['origin']}".lower(),
            )
            if needle in hay:
                return doc
            if len(parts) == 1 and parts[0] in hay:
                return doc
            if len(parts) > 1 and all(part in hay for part in parts):
                return doc
        return None

    def communication_data_snapshot(self, ctx):
        pid = ctx["platform_id"]
        first_deal = row(
            """
            SELECT d.*, p.name AS proponent_name
            FROM deals d
            JOIN proponents p ON p.id = d.proponent_id
            WHERE d.platform_id = ?
            ORDER BY d.updated_at DESC
            LIMIT 1
            """,
            (pid,),
        )
        return {
            "deal_count": row("SELECT COUNT(*) AS c FROM deals WHERE platform_id = ?", (pid,))["c"],
            "active_deal_count": row("SELECT COUNT(*) AS c FROM deals WHERE platform_id = ? AND phase NOT IN ('concluso','archiviato','respinta')", (pid,))["c"],
            "total_target": row("SELECT COALESCE(SUM(funding_target), 0) AS c FROM deals WHERE platform_id = ?", (pid,))["c"],
            "investor_count": row("SELECT COUNT(*) AS c FROM investors WHERE platform_id = ?", (pid,))["c"],
            "sophisticated_count": row("SELECT COUNT(*) AS c FROM investors WHERE platform_id = ? AND LOWER(investor_type) LIKE '%sofistic%'", (pid,))["c"],
            "supplier_count": row("SELECT COUNT(*) AS c FROM suppliers WHERE platform_id = ?", (pid,))["c"],
            "first_deal": first_deal,
        }

    def communication_field_suggestion(self, ctx, item, field, docs, snapshot):
        label = field[0]
        key = label.lower()
        first_deal = snapshot["first_deal"]
        if "periodo" in key:
            if "30 giugno" in item["reference"].lower():
                return "30/06/2026", "Dati calendario", "prefilled"
            if "anno" in item["reference"].lower():
                return "2025", "Dati calendario", "prefilled"
        if "data riferimento" in key:
            if "31 dicembre" in item["reference"].lower():
                return "31/12/2025", "Dati calendario", "prefilled"
        if "anno" in key:
            return "2025", "Dati calendario", "prefilled"
        if "numero offerte" in key or "offerte concluse" in key:
            return str(snapshot["active_deal_count"] or snapshot["deal_count"]), "Deal/API piattaforma", "prefilled"
        if "numero investitori" in key:
            return str(snapshot["investor_count"]), "Investitori/API piattaforma", "prefilled"
        if "investitori sofisticati" in key:
            return str(snapshot["sophisticated_count"]), "CRM investitori", "prefilled"
        if "investitori non sofisticati" in key:
            return str(max(0, snapshot["investor_count"] - snapshot["sophisticated_count"])), "CRM investitori", "prefilled"
        if "totale raccolto" in key or "importo raccolto" in key:
            return str(int(snapshot["total_target"] or 0)), "Deal/API piattaforma", "prefilled"
        if first_deal and "deal" in key:
            return first_deal["title"], "Deal", "prefilled"
        if first_deal and "titolare progetto" in key:
            return first_deal["proponent_name"], "Proponente", "prefilled"
        if first_deal and "importo obiettivo" in key:
            return str(int(first_deal["funding_target"] or 0)), "Deal", "prefilled"
        if "contratti" in key or "outsourcing" in key:
            return str(snapshot["supplier_count"]), "Fornitori e contratti", "prefilled"
        if "copertura assicurativa" in key:
            match = self.communication_doc_match(docs, "Polizza assicurativa")
            if match:
                return f"Da documento: {match['title']}", "Documento rilevato", "prefilled"
        manual_keywords = ["note", "descrizione", "impatto", "warning", "rischi", "variazioni", "misure", "azioni", "causa"]
        if any(word in key for word in manual_keywords):
            return "", "Inserimento manuale", "manual"
        return "", "Da completare", "empty"

    def communication_attachment_rows(self, ctx, item, docs):
        cards = []
        for required in item["required_docs"]:
            match = self.communication_doc_match(docs, required)
            if match:
                link = rel_url("/documents/" + str(match["id"]) + "/download", ctx) if match["id"] else "#"
                cards.append(
                    f"""<li class="attachment-item found">
                      <span class="attachment-dot"></span>
                      <div><strong>{esc(required)}</strong><small>Rilevato: <a href="{link}">{esc(match['title'])}</a></small></div>
                    </li>"""
                )
            else:
                cards.append(
                    f"""<li class="attachment-item missing">
                      <span class="attachment-dot"></span>
                      <div><strong>{esc(required)}</strong><small>Mancante o da collegare</small></div>
                    </li>"""
                )
        return "".join(cards)

    def communication_send_instructions(self, item):
        channel = item["channel"].lower()
        if "infostat" in channel:
            return [
                "Accedere al portale INFOSTAT con il profilo abilitato della piattaforma.",
                f"Selezionare il flusso coerente con: {item['title']}.",
                "Caricare o compilare i dati finali, allegare il fascicolo evidenze e salvare la ricevuta.",
                "Archiviare ricevuta, file finale e log validazione nella sezione Documenti / Comunicazioni.",
            ]
        if "sicrowd" in channel:
            return [
                "Preparare il file dati nel formato richiesto dal portale SICROWD.",
                "Allegare KIIS o reporting richiesto e verificare quadrature su offerta, proponente e investitori.",
                "Inviare dal canale SICROWD e archiviare ricevuta e versione finale.",
            ]
        if "pec" in channel:
            return [
                "Preparare testo firmabile, allegati e oggetto della comunicazione.",
                "Inviare dalla PEC societaria all'indirizzo dell'Autorita competente indicato dalla richiesta o dalla procedura.",
                "Archiviare PEC inviata, ricevuta accettazione/consegna e allegati.",
            ]
        return [
            "Validare campi e allegati.",
            "Generare la bozza finale e farla approvare dal responsabile.",
            "Inviare tramite il canale previsto e archiviare evidenze e ricevute.",
        ]

    def communication_obligations_view(self, ctx):
        communication_rows = "".join(self.communication_row(ctx, item) for item in COMMUNICATION_CATALOG)
        immediate_count = sum(1 for item in COMMUNICATION_CATALOG if "Senza indugio" in item["deadline"] or "10 giorni" in item["deadline"])
        return f"""
<section class="metric-grid">
  <div class="metric"><span>Obblighi mappati</span><strong>{len(COMMUNICATION_CATALOG)}</strong></div>
  <div class="metric"><span>Event-driven critici</span><strong>{immediate_count}</strong></div>
  <div class="metric"><span>Flussi CONSOB</span><strong>{sum(1 for item in COMMUNICATION_CATALOG if 'CONSOB' in item['recipient'])}</strong></div>
  <div class="metric"><span>Flussi Banca d'Italia</span><strong>{sum(1 for item in COMMUNICATION_CATALOG if "Banca d'Italia" in item['recipient'])}</strong></div>
</section>
<section class="panel table-scroll">
  <div class="section-head">
    <h2>Comunicazioni obbligatorie</h2>
    <span class="source-chip">Fonti ufficiali</span>
  </div>
  <p class="muted">Elenco normativo operativo per ECSP. Il registro scadenze usa questi obblighi come base e li trasforma in attivita, stati, output e archivio.</p>
  <table class="data-table roomy communications-table">
    <thead><tr><th>Comunicazione</th><th>Destinatario</th><th>Quando</th><th>Termine</th><th>Fonte</th><th>Template</th></tr></thead>
    <tbody>{communication_rows}</tbody>
  </table>
</section>
"""

    def communication_sources_view(self, ctx):
        template_rows = "".join(self.template_row(ctx, item) for item in OFFICIAL_TEMPLATES)
        downloaded = sum(1 for item in OFFICIAL_TEMPLATES if (TEMPLATE_DIR / item["filename"]).exists())
        return f"""
<section class="metric-grid">
  <div class="metric"><span>Fonti censite</span><strong>{len(OFFICIAL_TEMPLATES)}</strong></div>
  <div class="metric"><span>File disponibili</span><strong>{downloaded}</strong></div>
  <div class="metric"><span>Autorita</span><strong>CONSOB / BDI</strong></div>
  <div class="metric"><span>Uso</span><strong>Template</strong></div>
</section>
<section class="panel">
  <div class="section-head"><h2>Template e fonti ufficiali</h2><span class="source-chip">CONSOB / Banca d'Italia / UE</span></div>
  <table class="data-table compact">
    <thead><tr><th>Documento</th><th>Autorita</th><th>Stato</th><th>File</th></tr></thead>
    <tbody>{template_rows}</tbody>
  </table>
</section>
<section class="panel">
  <div class="section-head"><h2>Note operative</h2></div>
  <ul class="plain-list">
    <li><strong>Excel SICROWD:</strong> le tavole KIIS e reporting annuale sono trattate come formato di portale. La suite prepara dati, controlli, allegati e fascicolo evidenze.</li>
    <li><strong>Outsourcing Banca d'Italia:</strong> il provvedimento ufficiale e' in archivio; gli allegati tecnici vanno agganciati quando disponibili o caricati manualmente.</li>
    <li><strong>Output finale:</strong> ogni generazione deve conservare template usato, versione, responsabile, allegati, controlli e ricevuta di invio.</li>
  </ul>
</section>
"""

    def communication_workflow_form(self, ctx, item, selected_output=None, edit_output=False):
        docs_available = self.communication_documents(ctx)
        snapshot = self.communication_data_snapshot(ctx)
        saved_payload = {}
        saved_fields = {}
        if selected_output and selected_output["payload_json"]:
            try:
                saved_payload = json.loads(selected_output["payload_json"])
                saved_fields = {
                    field.get("label"): field.get("value", "")
                    for field in saved_payload.get("fields", [])
                    if isinstance(field, dict)
                }
            except (TypeError, ValueError):
                saved_payload = {}
                saved_fields = {}
        readonly_output = bool(selected_output and not edit_output)
        fields = "".join(
            self.communication_field_input(
                i,
                field,
                self.communication_field_suggestion(ctx, item, field, docs_available, snapshot),
                saved_fields.get(field[0]) if selected_output else None,
                readonly_output,
            )
            for i, field in enumerate(item["fields"])
        )
        attachment_rows = self.communication_attachment_rows(ctx, item, docs_available)
        prefill = "".join(f"<li>{esc(src)}</li>" for src in item["prefill"])
        instructions = "".join(f"<li>{esc(step)}</li>" for step in self.communication_send_instructions(item))
        template = item["template"]
        template_link = (
            f'<a href="{rel_url("/official-templates/" + template, ctx)}">Apri template/fonte</a>'
            if template
            else '<span class="muted">Template ufficiale non disponibile in locale: generazione da schema interno.</span>'
        )
        reviewer_value = saved_payload.get("reviewer") if selected_output else ctx["user"]["name"]
        manual_notes_value = saved_payload.get("manual_notes") if selected_output else ""
        readonly_attr = " readonly" if readonly_output else ""
        view_banner = ""
        form_actions = f"""
        <button class="button primary" type="submit">Genera bozza output</button>
        <a class="button ghost" href="{rel_url('/documents', ctx, {'origin': 'Comunicazioni'})}">Vedi archivio</a>
        """
        if selected_output:
            status_class, status_label = self.communication_status_meta(selected_output["status"], "")
            document_title = selected_output["document_title"] if "document_title" in selected_output.keys() else "Output comunicazione"
            can_edit_selected = self.communication_output_is_editable(selected_output["status"])
            if readonly_output:
                locked_action = (
                    f'<a class="button primary" href="{rel_url("/communications", ctx, {"tab": "generatore", "comm": item["id"], "output": selected_output["id"], "edit": "1"})}">Modifica / rigenera</a>'
                    if can_edit_selected
                    else '<span class="badge neutral">Versione chiusa</span>'
                )
                form_actions = (
                    f"""
        <a class="button primary" href="{rel_url('/communications', ctx, {'tab': 'generatore', 'comm': item['id'], 'output': selected_output['id'], 'edit': '1'})}">Modifica / rigenera</a>
        <a class="button ghost" href="{rel_url('/communications', ctx, {'tab': 'generatore', 'comm': item['id']})}">Nuova bozza pulita</a>
        """
                    if can_edit_selected
                    else f"""
        <a class="button ghost" href="{rel_url('/documents/' + str(selected_output['document_id']) + '/download', ctx) if selected_output['document_id'] else '#'}">Scarica</a>
        <a class="button secondary" href="{rel_url('/communications', ctx, {'tab': 'generatore', 'comm': item['id']})}">Nuova comunicazione</a>
        """
                )
                view_banner = f"""
    <div class="communication-opened-output">
      <div>
        <span class="panel-kicker">comunicazione aperta dallo storico</span>
        <strong>{esc(document_title or 'Output comunicazione')}</strong>
        <small>Stato: <span class="comm-status {status_class}">{esc(status_label)}</span> - versione fissata, non modificabile.</small>
      </div>
      {locked_action}
    </div>"""
            else:
                view_banner = f"""
    <div class="communication-opened-output is-editing">
      <div>
        <span class="panel-kicker">modifica da pratica esistente</span>
        <strong>Stai lavorando su una copia modificabile.</strong>
        <small>La rigenerazione crea una nuova bozza nello storico; la versione precedente resta archiviata finche non viene rimossa.</small>
      </div>
      <a class="button ghost" href="{rel_url('/communications', ctx, {'tab': 'generatore', 'comm': item['id'], 'output': selected_output['id']})}">Annulla modifica</a>
    </div>"""
        return f"""
<section class="panel generator-summary">
  <div class="section-head">
    <div>
      <p class="eyebrow">{esc(item['authority'])} - {esc(item['channel'])}</p>
      <h2>{esc(item['title'])}</h2>
    </div>
    <span class="badge {badge_class(item['deadline'])}">{esc(item['deadline'])}</span>
  </div>
  <div class="generator-summary-grid">
    <div><span>Periodicita</span><strong>{esc(item['frequency'])}</strong></div>
    <div><span>Riferimento</span><strong>{esc(item['reference'])}</strong></div>
    <div><span>Output</span><strong>{esc(item['output'])}</strong></div>
    <div><span>Fonte ufficiale</span><strong>{esc(item['source'])}</strong></div>
  </div>
</section>
<div class="communication-workbench refined-generator">
  <div class="panel generator-form-panel">
    <div class="section-head">
      <div><h2>Compilazione</h2><span class="muted">Ogni campo resta modificabile. I valori suggeriti vengono tracciati come precompilati.</span></div>
      <span class="panel-kicker">campi editabili</span>
    </div>
    {view_banner}
    <form class="form-grid top-gap" method="post" action="/communications/generate">
      {hidden_ctx(ctx)}
      <input type="hidden" name="workflow_id" value="{esc(item['id'])}">
      <input type="hidden" name="source_output_id" value="{esc(selected_output['id'] if selected_output else '')}">
      {fields}
      <label class="comm-field full-span is-prefilled">
        <span class="field-head"><span>Responsabile validazione</span><em>precompilato</em></span>
        <input name="reviewer" value="{esc(reviewer_value)}" data-field-state="prefilled"{readonly_attr}>
        <small>Fonte: utente corrente</small>
      </label>
      <label class="comm-field full-span is-manual">
        <span class="field-head"><span>Note operative e rettifiche manuali</span><em>manuale</em></span>
        <textarea name="manual_notes" rows="4" placeholder="Annota dati mancanti, assunzioni, fonti da verificare, differenze rispetto al template ufficiale." data-field-state="manual"{readonly_attr}>{esc(manual_notes_value)}</textarea>
        <small>Usa questo campo per rettifiche e decisioni non ricavate dai dati/documenti.</small>
      </label>
      <div class="form-actions left">
        {form_actions}
      </div>
    </form>
  </div>
  <aside class="panel generator-control-panel">
    <div class="section-head"><h2>Controlli output</h2><span class="panel-kicker">prima di generare</span></div>
    <div class="prefill-grid">
      <div><span>Precompilazione suggerita</span><ul class="plain-list">{prefill}</ul></div>
      <div><span>Allegati richiesti</span><ul class="attachment-list">{attachment_rows}</ul></div>
    </div>
    <div class="output-preview">
      <p class="panel-kicker">Output generato</p>
      <strong>{esc(item['output'])}</strong>
      <span>La bozza salvata in Documenti mantiene: campi inseriti, fonti, template, responsabile, warning e timestamp.</span>
      {template_link}
    </div>
  </aside>
</div>
{self.communication_output_table(ctx, item)}
<section class="panel send-instructions">
  <div class="section-head"><h2>Istruzioni invio</h2><span class="panel-kicker">{esc(item['channel'])}</span></div>
  <ol>{instructions}</ol>
</section>
"""

    def communication_field_input(self, index, field, suggestion, saved_value=None, readonly=False):
        label, kind, placeholder = field
        name = f"field_{index}"
        value, source, state = suggestion
        if saved_value is not None:
            value = saved_value
            source = "Versione salvata"
            state = "prefilled" if saved_value else "empty"
        state_class = {
            "prefilled": "is-prefilled",
            "manual": "is-manual",
            "empty": "is-empty",
        }.get(state, "is-empty")
        badge = {
            "prefilled": "precompilato",
            "manual": "manuale",
            "empty": "vuoto",
        }.get(state, "vuoto")
        readonly_attr = " readonly" if readonly else ""
        if kind == "textarea":
            control = f'<textarea name="{name}" rows="4" placeholder="{esc(placeholder)}" data-field-state="{esc(state)}"{readonly_attr}>{esc(value)}</textarea>'
        else:
            input_type = "date" if kind == "date" else "number" if kind == "number" else "text"
            control = f'<input name="{name}" type="{input_type}" placeholder="{esc(placeholder)}" value="{esc(value)}" data-field-state="{esc(state)}"{readonly_attr}>'
        return f"""<label class="comm-field {state_class}">
          <span class="field-head"><span>{esc(label)}</span><em>{badge}</em></span>
          {control}
          <small>Fonte: {esc(source)}</small>
        </label>"""

    def communication_generator_panel(self, ctx):
        pid = ctx["platform_id"]
        docs = rows(
            """
            SELECT category, origin, COUNT(*) AS count
            FROM documents
            WHERE platform_id = ?
            GROUP BY category, origin
            ORDER BY count DESC, category
            """,
            (pid,),
        )
        active_deals = row(
            "SELECT COUNT(*) AS c FROM deals WHERE platform_id = ? AND phase NOT IN ('concluso','archiviato')",
            (pid,),
        )["c"]
        proponents = row("SELECT COUNT(*) AS c FROM proponents WHERE platform_id = ?", (pid,))["c"]
        doc_sources = "".join(
            f"""<li>
              <span>{esc(d['origin'])} / {esc(d['category'])}</span>
              <strong>{d['count']}</strong>
            </li>"""
            for d in docs[:6]
        )
        if not doc_sources:
            doc_sources = '<li><span>Nessun documento caricato</span><strong>0</strong></li>'
        obligation_options = "".join(
            f'<option value="{esc(item["title"])}" {"selected" if item["title"] == "KIIS offerta art. 23" else ""}>{esc(item["title"])}</option>'
            for item in COMMUNICATION_CATALOG
        )
        field_rows = "".join(
            [
                self.field_mapping_row("Identificativo offerta", "Deal", "external_offer_id / progressivo interno", "Da validare"),
                self.field_mapping_row("Nome progetto", "Deal", "title", "Pronto"),
                self.field_mapping_row("Titolare progetto", "Proponente", "name, legal_form, tax_id", "Pronto"),
                self.field_mapping_row("Importo obiettivo", "Deal", "funding_target", "Pronto"),
                self.field_mapping_row("Statuto e diritti investitori", "Documenti", "statuto / delibere / patti", "Da estrarre"),
                self.field_mapping_row("Costi e commissioni", "Contratti", "contratto proponente / fee schedule", "Da estrarre"),
                self.field_mapping_row("Rischi specifici", "Business plan / due diligence", "documenti istruttoria", "Da revisionare"),
                self.field_mapping_row("Conflitti di interesse", "Registro conflitti", "registro + compagine", "Da validare"),
            ]
        )
        return f"""
<section class="panel generator-panel">
  <div class="section-head">
    <div>
      <p class="eyebrow">Design architetturale</p>
      <h2>Generatore file finale</h2>
    </div>
    <span class="badge warning">Prototipo UI</span>
  </div>
  <div class="generator-layout">
    <div class="generator-control">
      <label>Comunicazione da generare
        <select>{obligation_options}</select>
      </label>
      <label>Output finale
        <select>
          <option>Excel SICROWD + fascicolo evidenze</option>
          <option>DOCX domanda autorizzazione</option>
          <option>PDF comunicazione firmabile</option>
          <option>ZIP documenti e ricevute</option>
        </select>
      </label>
      <div class="generator-actions">
        <button class="button primary disabled-button" type="button" aria-disabled="true">Genera bozza file</button>
        <button class="button secondary disabled-button" type="button" aria-disabled="true">Valida campi mancanti</button>
      </div>
      <p class="muted">In questa fase i comandi mostrano il disegno del flusso: la generazione reale arrivera' quando collegheremo parser documentale, template engine e controlli di firma/invio.</p>
    </div>
    <div class="source-stack">
      <div class="source-card">
        <span>Dati strutturati disponibili</span>
        <strong>{active_deals} deal attivi</strong>
        <small>{proponents} proponenti in anagrafica</small>
      </div>
      <div class="source-card">
        <span>Documenti candidati</span>
        <ul class="source-list">{doc_sources}</ul>
      </div>
    </div>
  </div>
  <ol class="generation-flow">
    <li class="done"><span>1</span><strong>Seleziona obbligo</strong><small>Template, destinatario, scadenza</small></li>
    <li class="current"><span>2</span><strong>Raccogli dati</strong><small>Deal, proponenti, compagine, documenti</small></li>
    <li><span>3</span><strong>Estrai evidenze</strong><small>Contratti, statuti, verbali, allegati</small></li>
    <li><span>4</span><strong>Valida</strong><small>Campi mancanti, fonte, reviewer</small></li>
    <li><span>5</span><strong>Genera finale</strong><small>File + audit + ricevuta</small></li>
  </ol>
  <div class="workspace-grid generator-grid">
    <div class="panel inset-panel">
      <div class="section-head"><h2>Mappa dati proposta</h2><span class="source-chip">KIIS art. 23 esempio</span></div>
      <table class="data-table compact">
        <thead><tr><th>Campo</th><th>Fonte</th><th>Punto di prelievo</th><th>Stato</th></tr></thead>
        <tbody>{field_rows}</tbody>
      </table>
    </div>
    <div class="panel inset-panel">
      <div class="section-head"><h2>Regole di generazione</h2></div>
      <ul class="plain-list">
        <li><strong>Ogni campo mantiene la fonte:</strong> dato strutturato, documento, pagina, estratto e utente validatore.</li>
        <li><strong>I documenti caricati diventano fonti interrogabili:</strong> contratti, patti, statuti, verbali, business plan, KYC e KIIS vengono indicizzati e collegati al fascicolo.</li>
        <li><strong>Il file finale non nasce mai “al buio”:</strong> prima si vede una bozza con campi mancanti, conflitti e warning normativi.</li>
        <li><strong>La generazione produce evidenza:</strong> file finale, versione template, hash, allegati usati, log validazioni e ricevuta di trasmissione quando disponibile.</li>
      </ul>
    </div>
  </div>
</section>
"""

    def field_mapping_row(self, field, source, pointer, state):
        return f"""<tr>
          <td>{esc(field)}</td>
          <td>{esc(source)}</td>
          <td>{esc(pointer)}</td>
          <td><span class="badge {badge_class(state)}">{esc(state)}</span></td>
        </tr>"""

    def communication_row(self, ctx, item):
        template = item["template"]
        if template:
            template_html = f'<a href="{rel_url("/official-templates/" + template, ctx)}">{esc(item["status"])}</a>'
        else:
            template_html = f'<span class="badge {badge_class(item["status"])}">{esc(item["status"])}</span>'
        return f"""<tr>
          <td><strong>{esc(item['title'])}</strong><br><span class="muted">{esc(item['payload'])}</span></td>
          <td>{esc(item['recipient'])}</td>
          <td>{esc(item['trigger'])}</td>
          <td><span class="badge {badge_class(item['deadline'])}">{esc(item['deadline'])}</span></td>
          <td>{esc(item['source'])}</td>
          <td>{template_html}</td>
        </tr>"""

    def template_row(self, ctx, item):
        path = TEMPLATE_DIR / item["filename"]
        local = (
            f'<a href="{rel_url("/official-templates/" + item["filename"], ctx)}">Apri</a>'
            if path.exists()
            else '<span class="badge warning">Non presente</span>'
        )
        return f"""<tr>
          <td><strong>{esc(item['title'])}</strong><br><span class="muted">{esc(item['note'])}</span></td>
          <td>{esc(item['authority'])}</td>
          <td><span class="badge {badge_class(item['status'])}">{esc(item['status'])}</span></td>
          <td>{local}<br><a href="{esc(item['source_url'])}" target="_blank" rel="noreferrer">Fonte</a></td>
        </tr>"""

    def page_documents(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        params = parse_qs(urlparse(self.path).query)
        mode = (params.get("mode") or [""])[0]
        q = (params.get("q") or [""])[0].strip()
        origin_filter = (params.get("origin") or [""])[0]
        category_filter = (params.get("category") or [""])[0]
        context_filter = (params.get("context") or [""])[0]

        origin_counts_raw = rows(
            "SELECT origin AS name, COUNT(*) AS count FROM documents WHERE platform_id = ? GROUP BY origin ORDER BY origin",
            (pid,),
        )
        category_counts_raw = rows(
            """
            SELECT label AS name, SUM(count) AS count
            FROM (
              SELECT category AS label, COUNT(*) AS count FROM documents WHERE platform_id = ? GROUP BY category
              UNION ALL
              SELECT document_type AS label, COUNT(*) AS count FROM person_documents WHERE platform_id = ? GROUP BY document_type
              UNION ALL
              SELECT contract_type AS label, COUNT(*) AS count FROM supplier_contracts WHERE platform_id = ? GROUP BY contract_type
            )
            WHERE label != ''
            GROUP BY label
            ORDER BY label
            """,
            (pid, pid, pid),
        )
        origin_taxonomy = [
            "Archivio",
            "Compagine",
            "CdA",
            "Proponente",
            "Deal",
            "Persona",
            "Fornitore",
            "Contratto fornitore",
            "Governance",
            "Autorita",
            "Comunicazioni",
            "Altro",
        ]
        category_taxonomy = [
            "Documentazione",
            "Atto societario",
            "Statuto",
            "Bilancio",
            "Situazione contabile",
            "Relazione revisore",
            "Prospetto requisiti prudenziali",
            "Polizza assicurativa",
            "Domanda autorizzazione ECSP",
            "Aggiornamento autorizzazione",
            "Delibera CdA",
            "Verbale CdA",
            "Verbale assemblea soci",
            "Documento interno",
            "Allegato o template",
            "Visura camerale",
            "Patti parasociali",
            "Lettera di incarico",
            "Contratto amministratore",
            "Contratto fornitore",
            "Outsourcing",
            "Data processing agreement",
            "SLA",
            "NDA",
            "Procura",
            "Delega",
            "KIIS",
            "Business plan",
            "KYC / AML",
            "Comunicazione CONSOB",
            "Comunicazione Banca d'Italia",
            "Altro",
        ]

        def merge_taxonomy(count_rows, base):
            counts = {item["name"]: item["count"] for item in count_rows}
            ordered = [{"name": name, "count": counts.pop(name, 0)} for name in base]
            ordered.extend({"name": name, "count": count} for name, count in sorted(counts.items()))
            return ordered

        origins = merge_taxonomy(origin_counts_raw, origin_taxonomy)
        categories = merge_taxonomy(category_counts_raw, category_taxonomy)
        deals = rows("SELECT id, title AS name FROM deals WHERE platform_id = ? ORDER BY title", (pid,))
        props = rows("SELECT id, name FROM proponents WHERE platform_id = ? ORDER BY name", (pid,))
        suppliers = rows("SELECT id, name FROM suppliers WHERE platform_id = ? ORDER BY name", (pid,))
        people = rows(
            """
            SELECT name, MAX(role) AS role
            FROM (
              SELECT name, role FROM committee_members WHERE platform_id = ?
              UNION ALL
              SELECT name, role FROM users WHERE active = 1
              UNION ALL
              SELECT person_name AS name, role FROM person_agreements WHERE platform_id = ?
              UNION ALL
              SELECT person_name AS name, role FROM person_documents WHERE platform_id = ?
            )
            WHERE name != ''
            GROUP BY name
            ORDER BY name
            """,
            (pid, pid, pid),
        )

        where = ["doc.platform_id = ?"]
        query_params = [pid]
        if q:
            needle = f"%{q}%"
            where.append(
                """(
                  doc.title LIKE ? OR doc.category LIKE ? OR doc.origin LIKE ? OR doc.filename LIKE ?
                  OR IFNULL(d.title, '') LIKE ? OR IFNULL(p.name, '') LIKE ?
                  OR IFNULL(pd.person_name, '') LIKE ? OR IFNULL(s.name, '') LIKE ?
                )"""
            )
            query_params.extend([needle] * 8)
        if origin_filter:
            where.append("doc.origin = ?")
            query_params.append(origin_filter)
        if category_filter:
            where.append("(doc.category = ? OR IFNULL(pd.document_type, '') = ? OR IFNULL(sc.contract_type, '') = ?)")
            query_params.extend([category_filter, category_filter, category_filter])
        if context_filter and ":" in context_filter:
            kind, value = context_filter.split(":", 1)
            if kind == "deal" and value.isdigit():
                where.append("doc.deal_id = ?")
                query_params.append(int(value))
            elif kind == "proponent" and value.isdigit():
                where.append("doc.proponent_id = ?")
                query_params.append(int(value))
            elif kind == "supplier" and value.isdigit():
                where.append("s.id = ?")
                query_params.append(int(value))
            elif kind == "person":
                where.append("pd.person_name = ?")
                query_params.append(value)

        docs = rows(
            f"""
            SELECT doc.*, d.title AS deal_title, p.name AS proponent_name,
                   pd.person_name, pd.role AS person_role, pd.document_type AS person_document_type,
                   s.id AS supplier_id, s.name AS supplier_name,
                   sc.contract_type, sc.status AS contract_status
            FROM documents doc
            LEFT JOIN deals d ON d.id = doc.deal_id
            LEFT JOIN proponents p ON p.id = doc.proponent_id
            LEFT JOIN person_documents pd ON pd.document_id = doc.id
            LEFT JOIN supplier_contracts sc ON sc.document_id = doc.id
            LEFT JOIN suppliers s ON s.id = sc.supplier_id
            WHERE {" AND ".join(where)}
            ORDER BY doc.created_at DESC
            """,
            tuple(query_params),
        )
        person_doc_count = row("SELECT COUNT(*) AS c FROM person_documents WHERE platform_id = ?", (pid,))["c"]
        supplier_contract_count = row("SELECT COUNT(*) AS c FROM supplier_contracts WHERE platform_id = ?", (pid,))["c"]
        compagine_doc_count = row("SELECT COUNT(*) AS c FROM documents WHERE platform_id = ? AND origin = 'Compagine'", (pid,))["c"]
        deal_doc_count = row("SELECT COUNT(*) AS c FROM documents WHERE platform_id = ? AND deal_id IS NOT NULL", (pid,))["c"]
        proponent_doc_count = row("SELECT COUNT(*) AS c FROM documents WHERE platform_id = ? AND proponent_id IS NOT NULL", (pid,))["c"]
        general_doc_count = row(
            """
            SELECT COUNT(*) AS c
            FROM documents
            WHERE platform_id = ? AND deal_id IS NULL AND proponent_id IS NULL
            """,
            (pid,),
        )["c"]
        archive_counts = {
            "Persone": person_doc_count,
            "Fornitori": supplier_contract_count,
            "Societari": compagine_doc_count,
            "Deal": deal_doc_count,
            "Proponenti": proponent_doc_count,
            "Generale": general_doc_count,
        }

        def filtered_url(extra=None):
            clean = {}
            for key, value in {"q": q, "origin": origin_filter, "category": category_filter, "context": context_filter}.items():
                if value:
                    clean[key] = value
            if extra:
                clean.update(extra)
            clean = {key: value for key, value in clean.items() if value}
            return rel_url("/documents", ctx, clean)

        def option_from_counts(items, selected, all_label):
            opts = [f'<option value="">{esc(all_label)}</option>']
            for item in items:
                value = item["name"]
                sel = " selected" if value == selected else ""
                opts.append(f'<option value="{esc(value)}"{sel}>{esc(value)} ({item["count"]})</option>')
            return "".join(opts)

        context_entries = []
        for person in people:
            role_label = ROLE_LABELS.get(person["role"], person["role"])
            label = f"Persona - {person['name']}{(' - ' + role_label) if role_label else ''}"
            context_entries.append({"value": f"person:{person['name']}", "label": label})
        for supplier in suppliers:
            context_entries.append({"value": f"supplier:{supplier['id']}", "label": f"Fornitore - {supplier['name']}"})
        for prop in props:
            context_entries.append({"value": f"proponent:{prop['id']}", "label": f"Proponente - {prop['name']}"})
        for deal in deals:
            context_entries.append({"value": f"deal:{deal['id']}", "label": f"Deal - {deal['name']}"})
        selected_context_label = next((item["label"] for item in context_entries if item["value"] == context_filter), "")

        def context_datalist():
            chunks = ['<datalist id="document-context-options">']
            for item in context_entries:
                chunks.append(f'<option value="{esc(item["label"])}" data-value="{esc(item["value"])}"></option>')
            chunks.append("</datalist>")
            return "".join(chunks)

        def chip_links(items, filter_name, names=None, limit=8):
            selected = set(names or [item["name"] for item in items])
            selected_items = [item for item in items if item["name"] in selected]
            if not selected_items:
                return '<span class="classification-empty">Nessuna voce</span>'
            chips = []
            for item in selected_items[:limit]:
                active = " active" if (filter_name == "origin" and origin_filter == item["name"]) or (filter_name == "category" and category_filter == item["name"]) else ""
                chips.append(
                    f'<a class="classification-chip{active}" href="{filtered_url({filter_name: item["name"]})}"><span>{esc(item["name"])}</span><strong>{item["count"]}</strong></a>'
                )
            return "".join(chips)

        def context_quick_links():
            rows_html = []
            for label, count in archive_counts.items():
                rows_html.append(f'<div class="archive-count"><span>{esc(label)}</span><strong>{count}</strong></div>')
            return "".join(rows_html)

        def context_options():
            chunks = [f'<option value="">Tutti i collegamenti</option>']
            chunks.append('<optgroup label="Persone">')
            for person in people:
                value = f"person:{person['name']}"
                sel = " selected" if context_filter == value else ""
                role_label = ROLE_LABELS.get(person["role"], person["role"])
                label = f"{person['name']} - {role_label}" if role_label else person["name"]
                chunks.append(f'<option value="{esc(value)}"{sel}>{esc(label)}</option>')
            chunks.append("</optgroup><optgroup label=\"Fornitori\">")
            for supplier in suppliers:
                value = f"supplier:{supplier['id']}"
                sel = " selected" if context_filter == value else ""
                chunks.append(f'<option value="{esc(value)}"{sel}>{esc(supplier["name"])}</option>')
            chunks.append("</optgroup><optgroup label=\"Proponenti\">")
            for prop in props:
                value = f"proponent:{prop['id']}"
                sel = " selected" if context_filter == value else ""
                chunks.append(f'<option value="{esc(value)}"{sel}>{esc(prop["name"])}</option>')
            chunks.append("</optgroup><optgroup label=\"Deal\">")
            for deal in deals:
                value = f"deal:{deal['id']}"
                sel = " selected" if context_filter == value else ""
                chunks.append(f'<option value="{esc(value)}"{sel}>{esc(deal["name"])}</option>')
            chunks.append("</optgroup>")
            return "".join(chunks)

        def taxonomy_links(items, filter_name):
            if not items:
                return '<li><span>Nessuna voce</span><strong>0</strong></li>'
            links = []
            for item in items[:8]:
                url = filtered_url({filter_name: item["name"]})
                links.append(f'<li><a href="{url}">{esc(item["name"])}</a><strong>{item["count"]}</strong></li>')
            return "".join(links)

        def document_context(doc):
            if doc["person_name"]:
                role = f" - {doc['person_role']}" if doc["person_role"] else ""
                return "Persona", f"{doc['person_name']}{role}"
            if doc["supplier_name"]:
                ctype = f" - {doc['contract_type']}" if doc["contract_type"] else ""
                return "Fornitore", f"{doc['supplier_name']}{ctype}"
            if doc["deal_title"]:
                return "Deal", doc["deal_title"]
            if doc["proponent_name"]:
                return "Proponente", doc["proponent_name"]
            return "Archivio", "-"

        rows_html = ""
        for doc in docs:
            ctx_label, ctx_value = document_context(doc)
            source = "Generato" if doc["generated"] else "Caricato"
            rows_html += f"""<tr>
              <td>
                <strong><a href="{rel_url('/documents/' + str(doc['id']) + '/download', ctx)}">{esc(doc['title'])}</a></strong>
                <span class="archive-file">{esc(doc['filename'])}</span>
              </td>
              <td><span class="source-chip">{esc(doc['origin'])}</span><span class="source-chip">{esc(doc['category'])}</span></td>
              <td><strong>{esc(ctx_label)}</strong><br><span class="muted">{esc(ctx_value)}</span></td>
              <td>{esc(doc['proponent_name'] or '-')}</td>
              <td><span class="badge {badge_class(source)}">{source}</span></td>
              <td>{esc(nice_date(doc['created_at']))}</td>
            </tr>"""
        if not rows_html:
            rows_html = '<tr><td colspan="6" class="empty-row">Nessun documento trovato con questi filtri.</td></tr>'

        people_options = "".join(
            f'<option value="{esc(person["name"] + "||" + (ROLE_LABELS.get(person["role"], person["role"]) or ""))}">{esc(person["name"])}{(" - " + esc(ROLE_LABELS.get(person["role"], person["role"]))) if person["role"] else ""}</option>'
            for person in people
        )
        common_types = category_taxonomy
        type_datalist = "".join(f'<option value="{esc(value)}"></option>' for value in common_types)
        upload_modal = ""
        if mode == "upload" and user_can(ctx["user"], "upload_document"):
            upload_modal = f"""
<div class="modal-backdrop">
  <section class="person-modal archive-modal">
    <div class="section-head">
      <h2>Nuovo documento</h2>
      <a class="modal-close" href="{filtered_url()}">x</a>
    </div>
    <form class="form-grid" method="post" action="/documents/upload" enctype="multipart/form-data">
      {hidden_ctx(ctx)}
      <label>Classe archivio
        <select name="entity_kind">
          <option value="">Archivio generale</option>
          <option value="person">Persona / incarico</option>
          <option value="supplier">Fornitore / contratto</option>
          <option value="proponent">Proponente</option>
          <option value="deal">Deal / offerta</option>
        </select>
      </label>
      <label>Origine<select name="origin"><option{" selected" if origin_filter == "Archivio" or not origin_filter else ""}>Archivio</option><option{" selected" if origin_filter == "Compagine" else ""}>Compagine</option><option{" selected" if origin_filter == "CdA" else ""}>CdA</option><option{" selected" if origin_filter == "Proponente" else ""}>Proponente</option><option{" selected" if origin_filter == "Deal" else ""}>Deal</option><option{" selected" if origin_filter == "Persona" else ""}>Persona</option><option{" selected" if origin_filter == "Fornitore" else ""}>Fornitore</option><option{" selected" if origin_filter == "Contratto fornitore" else ""}>Contratto fornitore</option><option{" selected" if origin_filter == "Governance" else ""}>Governance</option><option{" selected" if origin_filter == "Autorita" else ""}>Autorita</option><option{" selected" if origin_filter == "Comunicazioni" else ""}>Comunicazioni</option><option{" selected" if origin_filter == "Altro" else ""}>Altro</option></select></label>
      <label>Tipo documento<input name="category" list="document-type-options" value="{esc(category_filter or 'Documentazione')}"></label>
      <datalist id="document-type-options">{type_datalist}</datalist>
      <label>Titolo<input name="title" required></label>
      <label>Persona collegata<select name="person_ref"><option value="">-</option>{people_options}</select></label>
      <label>Nuova persona<input name="person_name" placeholder="nominativo non ancora censito"></label>
      <label>Ruolo persona<input name="person_role" placeholder="es. delegato, consulente, sindaco"></label>
      <label>Fornitore<select name="supplier_id"><option value="">-</option>{option_rows(suppliers, '')}</select></label>
      <label>Nuovo fornitore<input name="supplier_name" placeholder="es. provider AML"></label>
      <label>Area servizio<input name="service_area" placeholder="es. KYC, IT, legale"></label>
      <label>Deal<select name="deal_id"><option value="">-</option>{option_rows(deals, '')}</select></label>
      <label>Proponente<select name="proponent_id"><option value="">-</option>{option_rows(props, '')}</select></label>
      <label>Controparte<input name="counterparty" placeholder="societa, fornitore, persona"></label>
      <label>Scadenza / revisione<input name="expires_at" type="date"></label>
      <label class="full-span">Note archivio<textarea name="notes" rows="3" placeholder="Ambito, poteri, vincoli, rinnovo, fonte dati..."></textarea></label>
      <label class="full-span">File<input name="file" type="file" required></label>
      <div class="form-actions left"><button class="button primary" type="submit">Carica documento</button><a class="button ghost" href="{filtered_url()}">Annulla</a></div>
    </form>
  </section>
</div>"""

        action_button = ""
        if user_can(ctx["user"], "upload_document"):
            action_button = f'<a class="button primary" href="{filtered_url({"mode": "upload"})}">+ Nuovo documento</a>'

        body = f"""
<p class="page-copy">Tutti i file della suite - caricati qui o nelle altre sezioni - confluiscono in un indice filtrabile per origine, tipo documento, soggetto collegato e contesto operativo.</p>
<form class="archive-toolbar" method="get" action="/documents">
  {hidden_ctx(ctx)}
  <label>Cerca<input name="q" value="{esc(q)}" placeholder="titolo, persona, fornitore, deal..."></label>
  <label>Origine<select name="origin">{option_from_counts(origins, origin_filter, "Tutte")}</select></label>
  <label>Tipo documento<select name="category">{option_from_counts(categories, category_filter, "Tutti")}</select></label>
  <label>Collegato a<input name="context_search" value="{esc(selected_context_label)}" list="document-context-options" placeholder="cerca persona, fornitore, proponente, deal" data-context-combobox></label>
  <input type="hidden" name="context" value="{esc(context_filter)}" data-context-value>
  {context_datalist()}
  <button class="button ghost" type="submit">Cerca</button>
  <a class="button secondary" href="{rel_url('/documents', ctx)}">Reset</a>
  {action_button}
</form>
<section class="panel archive-classification">
  <div class="section-head"><h2>Classificazione archivio</h2><span class="panel-kicker">filtri rapidi</span></div>
  <div class="classification-grid">
    <div>
      <p class="panel-kicker">Origini operative</p>
      <div class="classification-chips">{chip_links(origins, "origin", ["CdA", "Proponente", "Deal", "Compagine", "Persona", "Fornitore", "Autorita"])}</div>
    </div>
    <div>
      <p class="panel-kicker">Documenti societari</p>
      <div class="classification-chips">{chip_links(categories, "category", ["Statuto", "Atto societario", "Domanda autorizzazione ECSP", "Delibera CdA", "Verbale CdA", "Patti parasociali"])}</div>
    </div>
    <div>
      <p class="panel-kicker">Documenti operativi</p>
      <div class="classification-chips">{chip_links(categories, "category", ["Contratto fornitore", "Outsourcing", "Data processing agreement", "KYC / AML", "KIIS", "Business plan"])}</div>
    </div>
    <div>
      <p class="panel-kicker">Copertura</p>
      <div class="archive-count-grid">{context_quick_links()}</div>
    </div>
  </div>
</section>
<section class="panel archive-panel">
  <div class="section-head"><h2>Archivio aggregato</h2><span class="panel-kicker">{len(docs)} documenti</span></div>
  <div class="table-scroll">
    <table class="data-table roomy document-index-table">
      <thead><tr><th>Documento</th><th>Classificazione</th><th>Collegamento</th><th>Proponente</th><th>Fonte</th><th>Data</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
</section>
{upload_modal}
"""
        self.render("Documenti", body, "documents")

    def page_architecture(self):
        doc_path = BASE_DIR / "docs" / "ARCHITECTURE.md"
        content = doc_path.read_text(encoding="utf-8") if doc_path.exists() else "Documento non trovato."
        body = f'<section class="panel doc-panel"><pre>{esc(content)}</pre></section>'
        self.render("Architettura", body, "architecture")

    def page_assistant(self):
        ctx = self.get_ctx()
        pid = ctx["platform_id"]
        open_deals = row(
            "SELECT COUNT(*) AS c FROM deals WHERE platform_id = ? AND phase NOT IN ('pubblicato','raccolta_in_corso','concluso','respinta','archiviato')",
            (pid,),
        )["c"]
        open_complaints = row("SELECT COUNT(*) AS c FROM complaints WHERE platform_id = ? AND status != 'Chiuso'", (pid,))["c"]
        open_conflicts = row("SELECT COUNT(*) AS c FROM conflicts WHERE platform_id = ? AND status IN ('Aperto','In analisi')", (pid,))["c"]
        body = f"""
<p class="page-copy">Assistente operativo che leggerà stato del sistema, fascicoli, documenti indicizzati e quadro normativo. In questa fase è una superficie di design: nessuna risposta automatica viene ancora prodotta.</p>
<section class="workspace-grid">
  <div class="panel">
    <div class="section-head"><h2>Contesto disponibile</h2><span class="panel-kicker">Read-only</span></div>
    <div class="compact-metrics">
      <div><span>Deal in preparazione</span><strong>{open_deals}</strong></div>
      <div><span>Reclami aperti</span><strong>{open_complaints}</strong></div>
      <div><span>Conflitti aperti</span><strong>{open_conflicts}</strong></div>
      <div><span>Documenti</span><strong>{row("SELECT COUNT(*) AS c FROM documents WHERE platform_id = ?", (pid,))["c"]}</strong></div>
    </div>
  </div>
  <div class="panel">
    <div class="section-head"><h2>Bozza interazione</h2><span class="panel-kicker">Conferma umana</span></div>
    <form class="stacked-form">
      <label>Domanda<textarea rows="5" placeholder="Chiedi stato deal, adempimenti, documenti mancanti..."></textarea></label>
      <button class="button primary disabled-button" type="button">Interroga assistente</button>
    </form>
    <p class="muted top-gap">Azioni dispositive, invii e generazioni finali dovranno sempre richiedere conferma e produrre audit.</p>
  </div>
</section>
"""
        self.render("Assistente IA", body, "assistant")

    def post_deal_create(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "create_deal"):
            self.redirect("/deals", ctx, "Ruolo non abilitato.")
            return
        with connect() as conn:
            now = now_iso()
            cur = conn.execute(
                """
                INSERT INTO deals(platform_id, proponent_id, title, funding_target, phase, technical_reviewer_id,
                                  covi_reviewer_id, contract_required, kiis_state, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'appena_caricato', ?, ?, ?, 'Bozza', ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    int(form["proponent_id"]),
                    form["title"].strip(),
                    float(form.get("funding_target") or 0),
                    int(form.get("technical_reviewer_id") or 0) or None,
                    int(form.get("covi_reviewer_id") or 0) or None,
                    int(form.get("contract_required") or 0),
                    ctx["user_id"],
                    now,
                    now,
                ),
            )
            deal_id = cur.lastrowid
            for category, label, required in REQUIREMENT_SEED:
                conn.execute(
                    "INSERT INTO deal_requirements(deal_id, kind, category, label, required) VALUES (?, 'onboarding', ?, ?, ?)",
                    (deal_id, category, label, required),
                )
            for area in VERIFICATION_SEED:
                conn.execute("INSERT INTO verifications(deal_id, area, owner_user_id) VALUES (?, ?, ?)", (deal_id, area, ctx["user_id"]))
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "deal", deal_id, "Creazione deal", "Fascicolo aperto in stato Appena caricato.")
            conn.commit()
        self.redirect(f"/deals/{deal_id}", ctx, "Deal creato.")

    def post_requirement(self, deal_id, form):
        ctx = self.ctx_from_form(form)
        deal = fetch_deal(deal_id)
        if not user_can(ctx["user"], "edit_requirement"):
            self.redirect(f"/deals/{deal_id}", ctx, "Ruolo non abilitato.")
            return
        completed = int(form.get("completed") or 0)
        req_id = int(form["requirement_id"])
        with connect() as conn:
            conn.execute(
                """
                UPDATE deal_requirements
                SET completed = ?, completed_at = ?, completed_by = ?
                WHERE id = ? AND deal_id = ?
                """,
                (completed, now_iso() if completed else "", ctx["user_id"] if completed else None, req_id, deal_id),
            )
            log_audit(conn, deal["platform_id"], ctx["user_id"], "deal", deal_id, "Aggiornamento requisito", f"Requisito #{req_id}: {'completato' if completed else 'riaperto'}.")
            conn.commit()
        self.redirect(f"/deals/{deal_id}", ctx, "Requisito aggiornato.")

    def post_verification(self, deal_id, form):
        ctx = self.ctx_from_form(form)
        deal = fetch_deal(deal_id)
        if not user_can(ctx["user"], "verify"):
            self.redirect(f"/deals/{deal_id}", ctx, "Ruolo non abilitato.")
            return
        status = form.get("status") or "pending"
        result = form.get("result") or ""
        verification_id = int(form["verification_id"])
        with connect() as conn:
            conn.execute(
                """
                UPDATE verifications
                SET status = ?, result = ?, owner_user_id = ?, completed_at = ?
                WHERE id = ? AND deal_id = ?
                """,
                (status, result, ctx["user_id"], now_iso() if status in {"ok", "issue"} else "", verification_id, deal_id),
            )
            log_audit(conn, deal["platform_id"], ctx["user_id"], "deal", deal_id, "Aggiornamento verifica", f"Verifica #{verification_id}: {status}.")
            conn.commit()
        self.redirect(f"/deals/{deal_id}", ctx, "Verifica aggiornata.")

    def post_opinion(self, deal_id, form):
        ctx = self.ctx_from_form(form)
        deal = fetch_deal(deal_id)
        committee = form["committee"]
        permission = "technical_opinion" if committee == "Comitato Tecnico" else "covi_opinion"
        if not user_can(ctx["user"], permission):
            self.redirect(f"/deals/{deal_id}", ctx, "Ruolo non abilitato.")
            return
        expected_phase = "comitato_tecnico" if committee == "Comitato Tecnico" else "covi"
        if deal["phase"] != expected_phase:
            self.redirect(f"/deals/{deal_id}", ctx, "Il deal non e in questa fase.")
            return
        reviewer = row("SELECT * FROM committee_members WHERE id = ?", (int(form["reviewer_member_id"]),))
        outcome = form["outcome"]
        summary = form["summary"]
        content = build_opinion_html(deal, committee, reviewer, outcome, summary)
        filename = f"parere-{committee.lower().replace(' ', '-')}-{deal_id}.html"
        with connect() as conn:
            doc_id = generated_document(
                conn,
                deal["platform_id"],
                deal_id,
                deal["proponent_id"],
                committee,
                "Parere",
                f"Parere {committee} - {deal['title']}",
                filename,
                content,
                ctx["user_id"],
            )
            conn.execute(
                """
                INSERT INTO committee_opinions(deal_id, committee, reviewer_member_id, outcome, summary, generated_document_id, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (deal_id, committee, reviewer["id"] if reviewer else None, outcome, summary, doc_id, ctx["user_id"], now_iso()),
            )
            next_phase = "covi" if committee == "Comitato Tecnico" else "cda"
            update_deal_phase(conn, deal, next_phase, ctx["user_id"], f"Documento {committee} generato e trasmesso alla fase successiva.")
            conn.commit()
        self.redirect(f"/deals/{deal_id}", ctx, f"Parere {committee} generato.")

    def post_board_decision(self, deal_id, form):
        ctx = self.ctx_from_form(form)
        deal = fetch_deal(deal_id)
        if not user_can(ctx["user"], "board_decision"):
            self.redirect(f"/deals/{deal_id}", ctx, "Ruolo non abilitato.")
            return
        if deal["phase"] != "cda":
            self.redirect(f"/deals/{deal_id}", ctx, "Il deal non e in fase CdA.")
            return
        outcome = form["outcome"]
        notes = form["notes"]
        integration_required = 1 if outcome == "Approvato con integrazioni" else 0
        content = build_board_html(deal, outcome, notes, integration_required)
        with connect() as conn:
            doc_id = generated_document(
                conn,
                deal["platform_id"],
                deal_id,
                deal["proponent_id"],
                "CdA",
                "Delibera",
                f"Delibera CdA - {deal['title']}",
                f"delibera-cda-{deal_id}.html",
                content,
                ctx["user_id"],
            )
            conn.execute(
                """
                INSERT INTO board_decisions(deal_id, outcome, notes, integration_required, generated_document_id, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (deal_id, outcome, notes, integration_required, doc_id, ctx["user_id"], now_iso()),
            )
            if outcome == "Non approvato":
                target = "respinta"
                details = "CdA non favorevole; fascicolo respinto."
            elif integration_required:
                target = "integrazione_documenti"
                details = "CdA approva con integrazioni documentali."
                conn.execute(
                    """
                    INSERT INTO deal_requirements(deal_id, kind, category, label, required, due_date)
                    VALUES (?, 'integration', 'Integrazione CdA', ?, 1, ?)
                    """,
                    (deal_id, notes[:180] or "Integrazione richiesta dal CdA", (date.today() + timedelta(days=10)).isoformat()),
                )
            else:
                target = "contratto" if deal["contract_required"] else "pre_pubblicazione"
                details = "CdA approva; fascicolo avanzato."
            update_deal_phase(conn, deal, target, ctx["user_id"], details)
            conn.commit()
        self.redirect(f"/deals/{deal_id}", ctx, "Delibera registrata.")

    def post_transition(self, deal_id, form):
        ctx = self.ctx_from_form(form)
        deal = fetch_deal(deal_id)
        target = form["target_phase"]
        if not user_can(ctx["user"], "finalize") and ctx["user"]["role"] not in {"compliance", "legal", "operator"}:
            self.redirect(f"/deals/{deal_id}", ctx, "Ruolo non abilitato.")
            return
        notice = self.validate_transition(deal, target)
        if notice:
            self.redirect(f"/deals/{deal_id}", ctx, notice)
            return
        with connect() as conn:
            update_deal_phase(conn, deal, target, ctx["user_id"], f"Avanzamento manuale da {phase_label(deal['phase'])} a {phase_label(target)}.")
            if target == "pubblicato":
                conn.execute("UPDATE deals SET kiis_state = 'Definitivo' WHERE id = ?", (deal_id,))
            conn.commit()
        self.redirect(f"/deals/{deal_id}", ctx, f"Fase aggiornata: {phase_label(target)}.")

    def validate_transition(self, deal, target):
        allowed = {
            "appena_caricato": {"istruttoria_documentazione"},
            "istruttoria_documentazione": {"verifiche"},
            "verifiche": {"comitato_tecnico"},
            "integrazione_documenti": {"contratto", "pre_pubblicazione"},
            "contratto": {"pre_pubblicazione"},
            "pre_pubblicazione": {"pubblicato"},
        }
        if target not in allowed.get(deal["phase"], set()):
            return "Transizione non ammessa."
        if target == "verifiche":
            missing = row(
                "SELECT COUNT(*) AS c FROM deal_requirements WHERE deal_id = ? AND kind = 'onboarding' AND required = 1 AND completed = 0",
                (deal["id"],),
            )["c"]
            if missing:
                return f"Documentazione incompleta: {missing} elementi aperti."
        if target == "comitato_tecnico":
            pending = row(
                "SELECT COUNT(*) AS c FROM verifications WHERE deal_id = ? AND status != 'ok'",
                (deal["id"],),
            )["c"]
            if pending:
                return f"Verifiche non completate: {pending} elementi non OK."
        if deal["phase"] == "integrazione_documenti":
            open_items = row(
                "SELECT COUNT(*) AS c FROM deal_requirements WHERE deal_id = ? AND kind = 'integration' AND required = 1 AND completed = 0",
                (deal["id"],),
            )["c"]
            if open_items:
                return f"Integrazioni aperte: {open_items}."
        if target == "pubblicato":
            kiis = row(
                "SELECT COUNT(*) AS c FROM documents WHERE deal_id = ? AND category = 'KIIS'",
                (deal["id"],),
            )["c"]
            if not kiis:
                return "Caricare il KIIS definitivo prima della pubblicazione."
        return ""

    def post_deal_upload(self, deal_id, form, files):
        ctx = self.ctx_from_form(form)
        deal = fetch_deal(deal_id)
        if not user_can(ctx["user"], "upload_document"):
            self.redirect(f"/deals/{deal_id}", ctx, "Ruolo non abilitato.")
            return
        file_item = files.get("file")
        if not file_item:
            self.redirect(f"/deals/{deal_id}", ctx, "File mancante.")
            return
        with connect() as conn:
            doc_id = save_uploaded_document(
                conn,
                file_item,
                deal["platform_id"],
                deal_id,
                deal["proponent_id"],
                "Deal",
                form.get("category") or "Documentazione",
                form.get("title") or file_item.filename,
                ctx["user_id"],
            )
            if (form.get("category") or "").upper() == "KIIS":
                conn.execute("UPDATE deals SET kiis_state = 'Definitivo', updated_at = ? WHERE id = ?", (now_iso(), deal_id))
            log_audit(conn, deal["platform_id"], ctx["user_id"], "deal", deal_id, "Caricamento documento", f"Documento #{doc_id}: {form.get('title') or file_item.filename}.")
            conn.commit()
        self.redirect(f"/deals/{deal_id}", ctx, "Documento caricato.")

    def post_generate_report(self, deal_id, form):
        ctx = self.ctx_from_form(form)
        deal = fetch_deal(deal_id)
        content = build_iter_report(deal_id)
        with connect() as conn:
            doc_id = generated_document(
                conn,
                deal["platform_id"],
                deal_id,
                deal["proponent_id"],
                "Deal",
                "Report iter",
                f"Report iter - {deal['title']}",
                f"report-iter-{deal_id}.html",
                content,
                ctx["user_id"],
            )
            log_audit(conn, deal["platform_id"], ctx["user_id"], "deal", deal_id, "Generazione report iter", f"Documento #{doc_id} generato.")
            conn.commit()
        self.redirect(f"/deals/{deal_id}", ctx, "Report iter salvato nel fascicolo.")

    def post_proponent_create(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_proponents"):
            self.redirect("/proponents", ctx, "Ruolo non abilitato.")
            return
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO proponents(
                    platform_id, name, legal_form, tax_id, contact_email, phone, website, sector,
                    beneficial_owners, exposure, internal_score, crm_status, onboarding_status,
                    owner_name, source_system, external_proponent_id, last_synced_at, next_action, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    form["name"].strip(),
                    form.get("legal_form", ""),
                    form.get("tax_id", ""),
                    form.get("contact_email", ""),
                    form.get("phone", "").strip(),
                    form.get("website", "").strip(),
                    form.get("sector", "").strip(),
                    form.get("beneficial_owners", ""),
                    float(form.get("exposure") or 0),
                    form.get("internal_score", "Da valutare"),
                    form.get("crm_status", "In istruttoria"),
                    form.get("onboarding_status", "Documenti da raccogliere"),
                    form.get("owner_name", "").strip(),
                    form.get("source_system", "Manuale"),
                    form.get("external_proponent_id", "").strip(),
                    now_iso() if form.get("source_system", "Manuale").startswith("adapter:") else "",
                    form.get("next_action", "").strip(),
                    form.get("notes", ""),
                    now_iso(),
                ),
            )
            proponent_id = cur.lastrowid
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "proponent", proponent_id, "Creazione proponente", form["name"].strip())
            conn.commit()
        self.redirect(f"/proponents/{proponent_id}", ctx, "Proponente creato.")

    def post_proponent_update(self, proponent_id, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_proponents"):
            self.redirect(f"/proponents/{proponent_id}", ctx, "Ruolo non abilitato.")
            return
        with connect() as conn:
            prop = conn.execute(
                "SELECT * FROM proponents WHERE id = ? AND platform_id = ?",
                (proponent_id, ctx["platform_id"]),
            ).fetchone()
            if not prop:
                self.redirect("/proponents", ctx, "Proponente non trovato.")
                return
            source_system = form.get("source_system", "Manuale")
            last_synced_at = prop["last_synced_at"]
            if source_system != prop["source_system"] and source_system.startswith("adapter:"):
                last_synced_at = now_iso()
            conn.execute(
                """
                UPDATE proponents
                SET name = ?,
                    legal_form = ?,
                    tax_id = ?,
                    contact_email = ?,
                    phone = ?,
                    website = ?,
                    sector = ?,
                    beneficial_owners = ?,
                    exposure = ?,
                    internal_score = ?,
                    crm_status = ?,
                    onboarding_status = ?,
                    owner_name = ?,
                    source_system = ?,
                    external_proponent_id = ?,
                    last_synced_at = ?,
                    manual_override_notes = ?,
                    next_action = ?,
                    notes = ?
                WHERE id = ? AND platform_id = ?
                """,
                (
                    form["name"].strip(),
                    form.get("legal_form", ""),
                    form.get("tax_id", ""),
                    form.get("contact_email", ""),
                    form.get("phone", "").strip(),
                    form.get("website", "").strip(),
                    form.get("sector", "").strip(),
                    form.get("beneficial_owners", ""),
                    float(form.get("exposure") or 0),
                    form.get("internal_score", "Da valutare"),
                    form.get("crm_status", "In istruttoria"),
                    form.get("onboarding_status", "Documenti da raccogliere"),
                    form.get("owner_name", "").strip(),
                    source_system,
                    form.get("external_proponent_id", "").strip(),
                    last_synced_at,
                    form.get("manual_override_notes", "").strip(),
                    form.get("next_action", "").strip(),
                    form.get("notes", ""),
                    proponent_id,
                    ctx["platform_id"],
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "proponent", proponent_id, "Aggiornamento proponente", form["name"].strip())
            conn.commit()
        self.redirect(f"/proponents/{proponent_id}", ctx, "Proponente aggiornato.")

    def post_committee_create(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine"):
            self.redirect("/compagine", ctx, "Ruolo non abilitato.")
            return
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO committee_members(platform_id, committee, name, role, email, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (ctx["platform_id"], form["committee"], form["name"], form["role"], form["email"]),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "committee_member", cur.lastrowid, "Aggiornamento compagine", f"Aggiunto {form['name']} a {form['committee']}.")
            conn.commit()
        self.redirect("/compagine", ctx, "Membro aggiunto.")

    def post_shareholder_create(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine"):
            self.redirect("/compagine", ctx, "Ruolo non abilitato.")
            return
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO shareholders(
                    platform_id, name, subject_type, legal_form, tax_id, contact_email, phone,
                    address, stake_percent, beneficial_owners, requisites_status, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    form["name"].strip(),
                    form.get("subject_type", "Societa / ente"),
                    form.get("legal_form", "").strip(),
                    form.get("tax_id", "").strip(),
                    form.get("contact_email", "").strip(),
                    form.get("phone", "").strip(),
                    form.get("address", "").strip(),
                    float(form.get("stake_percent") or 0),
                    form.get("beneficial_owners", "").strip(),
                    form.get("requisites_status", "Da verificare"),
                    form.get("status", "Attivo"),
                    form.get("notes", "").strip(),
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "shareholder", cur.lastrowid, "Aggiornamento partecipogramma", form["name"])
            conn.commit()
            shareholder_id = cur.lastrowid
        self.redirect("/compagine", ctx, "Partecipante aggiunto.", {"shareholder": shareholder_id})

    def post_shareholder_update(self, shareholder_id, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine"):
            self.redirect("/compagine", ctx, "Ruolo non abilitato.")
            return
        with connect() as conn:
            shareholder = conn.execute(
                "SELECT * FROM shareholders WHERE id = ? AND platform_id = ?",
                (shareholder_id, ctx["platform_id"]),
            ).fetchone()
            if not shareholder:
                self.redirect("/compagine", ctx, "Partecipante non trovato.")
                return
            conn.execute(
                """
                UPDATE shareholders
                SET name = ?,
                    subject_type = ?,
                    legal_form = ?,
                    tax_id = ?,
                    contact_email = ?,
                    phone = ?,
                    address = ?,
                    stake_percent = ?,
                    beneficial_owners = ?,
                    requisites_status = ?,
                    status = ?,
                    notes = ?
                WHERE id = ? AND platform_id = ?
                """,
                (
                    form.get("name", shareholder["name"]).strip(),
                    form.get("subject_type", shareholder["subject_type"]),
                    form.get("legal_form", "").strip(),
                    form.get("tax_id", "").strip(),
                    form.get("contact_email", "").strip(),
                    form.get("phone", "").strip(),
                    form.get("address", "").strip(),
                    float(form.get("stake_percent") or 0),
                    form.get("beneficial_owners", "").strip(),
                    form.get("requisites_status", "Da verificare"),
                    form.get("status", "Attivo"),
                    form.get("notes", "").strip(),
                    shareholder_id,
                    ctx["platform_id"],
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "shareholder", shareholder_id, "Aggiornamento partecipante qualificato", form.get("name", shareholder["name"]))
            conn.commit()
        self.redirect("/compagine", ctx, "Partecipante aggiornato.", {"shareholder": shareholder_id})

    def post_shareholder_document_upload(self, shareholder_id, form, files):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine") and not user_can(ctx["user"], "upload_document"):
            self.redirect("/compagine", ctx, "Ruolo non abilitato.")
            return
        file_item = files.get("file")
        if file_item is None or not getattr(file_item, "filename", ""):
            self.redirect("/compagine", ctx, "File mancante.", {"shareholder": shareholder_id})
            return
        with connect() as conn:
            shareholder = conn.execute(
                "SELECT * FROM shareholders WHERE id = ? AND platform_id = ?",
                (shareholder_id, ctx["platform_id"]),
            ).fetchone()
            if not shareholder:
                self.redirect("/compagine", ctx, "Partecipante non trovato.")
                return
            doc_type = form.get("document_type") or "Documento societario"
            title = (form.get("title") or file_item.filename).strip()
            doc_id = save_uploaded_document(
                conn,
                file_item,
                ctx["platform_id"],
                None,
                None,
                "Partecipante qualificato",
                doc_type,
                title,
                ctx["user_id"],
            )
            cur = conn.execute(
                """
                INSERT INTO shareholder_documents(
                    platform_id, shareholder_id, document_type, title, notes,
                    issued_at, expires_at, document_id, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    shareholder_id,
                    doc_type,
                    title,
                    form.get("notes", "").strip(),
                    form.get("issued_at", ""),
                    form.get("expires_at", ""),
                    doc_id,
                    ctx["user_id"],
                    now_iso(),
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "shareholder_document", cur.lastrowid, "Caricamento documento partecipante", f"{shareholder['name']}: {title}")
            conn.commit()
        self.redirect("/compagine", ctx, "Documento partecipante caricato.", {"shareholder": shareholder_id})

    def org_kind_for_area(self, area):
        normalized = (area or "").lower()
        if "governance" in normalized:
            return "governance"
        if "controllo" in normalized:
            return "control"
        if "outsourcing" in normalized:
            return "outsourcing"
        if "comitato tecnico" in normalized:
            return "committee"
        if "advisory" in normalized:
            return "advisory"
        if "operativa" in normalized:
            return "operational"
        return "function"

    def post_org_function_save(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine"):
            self.redirect("/compagine", ctx, "Ruolo non abilitato.")
            return
        area = (form.get("group_name") or form.get("new_function_group") or "Funzioni responsabili").strip()
        function_name = (form.get("block_title") or form.get("new_function_title") or "").strip()
        note = (form.get("block_note") or form.get("notes") or "").strip()
        if not function_name:
            self.redirect("/compagine", ctx, "Titolo funzione mancante.")
            return
        with connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM org_functions
                WHERE platform_id = ? AND function_name = ?
                """,
                (ctx["platform_id"], function_name),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE org_functions
                    SET area = ?, kind = ?, note = ?, active = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (area, self.org_kind_for_area(area), note, now_iso(), existing["id"]),
                )
                function_id = existing["id"]
            else:
                cur = conn.execute(
                    """
                    INSERT INTO org_functions(platform_id, area, function_name, kind, note, active, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (ctx["platform_id"], area, function_name, self.org_kind_for_area(area), note, ctx["user_id"], now_iso(), now_iso()),
                )
                function_id = cur.lastrowid
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "org_function", function_id, "Salvataggio funzione organigramma", function_name)
            conn.commit()
        self.redirect("/compagine", ctx, "Funzione aggiunta all'organigramma.")

    def post_org_assignment_save(self, form, files):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine"):
            self.redirect("/compagine", ctx, "Ruolo non abilitato.")
            return
        function_name = (form.get("function_name") or "").strip()
        area = (form.get("function_area") or "").strip()
        if function_name == "__new__":
            function_name = (form.get("new_function_title") or "").strip()
            area = (form.get("new_function_group") or area or "Funzioni responsabili").strip()
        if not function_name:
            self.redirect("/compagine", ctx, "Seleziona o crea una funzione.")
            return
        if not area:
            area = "Funzioni responsabili"

        mode = form.get("assignment_mode") or "existing"
        subject_name = (form.get("subject_name") or "").strip()
        subject_type = (form.get("subject_type") or "").strip()
        if not subject_name and mode == "existing":
            subject_name = (form.get("existing_subject") or "").strip()
            subject_type = (form.get("existing_subject_type") or subject_type or "Persona fisica").strip()
        if not subject_name:
            self.redirect("/compagine", ctx, "Seleziona o inserisci un soggetto.")
            return
        if not subject_type:
            subject_type = "Persona fisica"

        role = (form.get("role") or "").strip()
        start_date = form.get("start_date", "")
        end_date = form.get("end_date", "")
        notes = (form.get("notes") or "").strip()
        linked_document = form.get("linked_document") or ""
        document_id = None
        linked_document_title = "" if linked_document in {"", "__upload__"} else linked_document

        with connect() as conn:
            function_exists = conn.execute(
                "SELECT id FROM org_functions WHERE platform_id = ? AND function_name = ?",
                (ctx["platform_id"], function_name),
            ).fetchone()
            if not function_exists and (form.get("function_name") == "__new__" or form.get("new_function_title")):
                conn.execute(
                    """
                    INSERT INTO org_functions(platform_id, area, function_name, kind, note, active, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (ctx["platform_id"], area, function_name, self.org_kind_for_area(area), notes, ctx["user_id"], now_iso(), now_iso()),
                )

            file_item = files.get("new_document_file")
            if linked_document == "__upload__" and file_item is not None and getattr(file_item, "filename", ""):
                doc_type = form.get("new_document_type") or "Contratto"
                linked_document_title = (form.get("new_document_title") or file_item.filename or f"{doc_type} - {subject_name}").strip()
                document_id = save_uploaded_document(
                    conn,
                    file_item,
                    ctx["platform_id"],
                    None,
                    None,
                    "Compagine",
                    doc_type,
                    linked_document_title,
                    ctx["user_id"],
                )
                conn.execute(
                    """
                    INSERT INTO person_documents(
                        platform_id, person_name, role, document_type, counterparty, title, notes,
                        signed_at, expires_at, document_id, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ctx["platform_id"],
                        subject_name,
                        function_name,
                        doc_type,
                        "",
                        linked_document_title,
                        notes,
                        start_date,
                        end_date,
                        document_id,
                        ctx["user_id"],
                        now_iso(),
                    ),
                )
            elif linked_document_title:
                doc = conn.execute(
                    """
                    SELECT id FROM documents
                    WHERE platform_id = ? AND title = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (ctx["platform_id"], linked_document_title),
                ).fetchone()
                if doc:
                    document_id = doc["id"]
                else:
                    contract = conn.execute(
                        """
                        SELECT document_id FROM supplier_contracts
                        WHERE platform_id = ? AND title = ?
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (ctx["platform_id"], linked_document_title),
                    ).fetchone()
                    if contract and contract["document_id"]:
                        document_id = contract["document_id"]

            existing = conn.execute(
                """
                SELECT id FROM org_assignments
                WHERE platform_id = ? AND subject_name = ? AND function_name = ?
                """,
                (ctx["platform_id"], subject_name, function_name),
            ).fetchone()
            if existing:
                assignment_id = existing["id"]
                conn.execute(
                    """
                    UPDATE org_assignments
                    SET subject_type = ?, area = ?, role = ?, start_date = ?, end_date = ?,
                        linked_document_title = ?, document_id = ?, status = 'Attivo',
                        notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        subject_type,
                        area,
                        role,
                        start_date,
                        end_date,
                        linked_document_title,
                        document_id,
                        notes,
                        now_iso(),
                        assignment_id,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO org_assignments(
                        platform_id, subject_name, subject_type, function_name, area, role,
                        start_date, end_date, linked_document_title, document_id, status,
                        notes, created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Attivo', ?, ?, ?, ?)
                    """,
                    (
                        ctx["platform_id"],
                        subject_name,
                        subject_type,
                        function_name,
                        area,
                        role,
                        start_date,
                        end_date,
                        linked_document_title,
                        document_id,
                        notes,
                        ctx["user_id"],
                        now_iso(),
                        now_iso(),
                    ),
                )
                assignment_id = cur.lastrowid
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "org_assignment", assignment_id, "Salvataggio assegnazione organigramma", f"{subject_name} - {function_name}")
            conn.commit()

        self.redirect("/compagine", ctx, "Collegamento funzione salvato.", {"person": subject_name, "role": function_name})

    def post_org_assignment_delete(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine"):
            self.redirect("/compagine", ctx, "Ruolo non abilitato.")
            return
        subject_name = (form.get("subject_name") or "").strip()
        function_name = (form.get("function_name") or "").strip()
        status = form.get("status") or "Rimosso"
        if status not in {"Archiviato", "Rimosso"}:
            status = "Rimosso"
        if not subject_name or not function_name:
            self.redirect("/compagine", ctx, "Collegamento non valido.")
            return
        with connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM org_assignments
                WHERE platform_id = ? AND subject_name = ? AND function_name = ?
                """,
                (ctx["platform_id"], subject_name, function_name),
            ).fetchone()
            if existing:
                assignment_id = existing["id"]
                conn.execute(
                    """
                    UPDATE org_assignments
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, now_iso(), assignment_id),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO org_assignments(
                        platform_id, subject_name, subject_type, function_name, area, status,
                        created_by, created_at, updated_at
                    ) VALUES (?, ?, 'Persona fisica', ?, '', ?, ?, ?, ?)
                    """,
                    (ctx["platform_id"], subject_name, function_name, status, ctx["user_id"], now_iso(), now_iso()),
                )
                assignment_id = cur.lastrowid
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "org_assignment", assignment_id, status, f"{subject_name} - {function_name}")
            conn.commit()
        self.redirect("/compagine", ctx, f"Funzione {status.lower()}.", {"person": subject_name, "role": function_name})

    def post_board_meeting_create(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_governance"):
            self.redirect("/governance", ctx, "Ruolo non abilitato.")
            return
        title = (form.get("title") or "CdA - (data da definire)").strip()
        meeting_date = form.get("meeting_date") or today_iso()
        agenda = form.get("agenda", "")
        meta = []
        if form.get("meeting_time"):
            meta.append(f"Ora: {form.get('meeting_time')}")
        if form.get("meeting_mode"):
            meta.append(f"Modalita: {form.get('meeting_mode')}")
        if form.get("meeting_place"):
            meta.append(f"Luogo: {form.get('meeting_place')}")
        if meta:
            agenda = (agenda + "\n\n" if agenda else "") + " / ".join(meta)
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO board_meetings(platform_id, title, meeting_date, meeting_link, agenda, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    title,
                    meeting_date,
                    form.get("meeting_link", ""),
                    agenda,
                    form.get("status", "Pianificata"),
                    now_iso(),
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "board_meeting", cur.lastrowid, "Creazione seduta CdA", title)
            conn.commit()
        target_tab = "sedute" if "seduta" in title.lower() or form.get("meeting_type") else "convocazioni"
        params = {"platform": ctx["platform_id"], "user": ctx["user_id"], "tab": target_tab, "notice": "Elemento CdA creato."}
        self.send_response(303)
        self.send_header("Location", f"/governance?{urlencode(params)}")
        self.end_headers()

    def post_investor_create(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_investors"):
            self.redirect("/investors", ctx, "Ruolo non abilitato.")
            return
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO investors(
                    platform_id, name, email, phone, investor_type, total_invested, onboarding_status,
                    entry_test_status, loss_simulation_status, threshold_status, reflection_status,
                    crm_status, preferred_categories, risk_profile, preferred_ticket_min, preferred_ticket_max,
                    preferred_channel, recurrence_status, source_system, external_investor_id, last_synced_at,
                    crm_notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    form["name"].strip(),
                    form["email"].strip(),
                    form.get("phone", "").strip(),
                    form.get("investor_type", "Non sofisticato"),
                    float(form.get("total_invested") or 0),
                    form.get("onboarding_status", "Da completare"),
                    form.get("entry_test_status", "Da completare"),
                    form.get("loss_simulation_status", "Da completare"),
                    form.get("threshold_status", "Da verificare"),
                    form.get("reflection_status", "Non applicabile"),
                    form.get("crm_status", "Attivo"),
                    form.get("preferred_categories", "").strip(),
                    form.get("risk_profile", "Da profilare"),
                    float(form.get("preferred_ticket_min") or 0),
                    float(form.get("preferred_ticket_max") or 0),
                    form.get("preferred_channel", "Email"),
                    form.get("recurrence_status", "Da valutare"),
                    form.get("source_system", "Manuale"),
                    form.get("external_investor_id", "").strip(),
                    now_iso() if form.get("source_system", "Manuale").startswith("adapter:") else "",
                    form.get("crm_notes", "").strip(),
                    now_iso(),
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "investor", cur.lastrowid, "Creazione investitore", form["name"].strip())
            conn.commit()
        self.redirect("/investors", ctx, "Investitore creato.")

    def post_investor_update(self, investor_id, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_investors"):
            self.redirect(f"/investors/{investor_id}", ctx, "Ruolo non abilitato.")
            return
        with connect() as conn:
            investor = conn.execute(
                "SELECT * FROM investors WHERE id = ? AND platform_id = ?",
                (investor_id, ctx["platform_id"]),
            ).fetchone()
            if not investor:
                self.redirect("/investors", ctx, "Investitore non trovato.")
                return
            source_system = form.get("source_system", "Manuale")
            last_synced_at = investor["last_synced_at"]
            if source_system != investor["source_system"] and source_system.startswith("adapter:"):
                last_synced_at = now_iso()
            conn.execute(
                """
                UPDATE investors
                SET name = ?,
                    email = ?,
                    phone = ?,
                    investor_type = ?,
                    total_invested = ?,
                    onboarding_status = ?,
                    entry_test_status = ?,
                    loss_simulation_status = ?,
                    threshold_status = ?,
                    reflection_status = ?,
                    crm_status = ?,
                    preferred_categories = ?,
                    risk_profile = ?,
                    preferred_ticket_min = ?,
                    preferred_ticket_max = ?,
                    preferred_channel = ?,
                    recurrence_status = ?,
                    source_system = ?,
                    external_investor_id = ?,
                    last_synced_at = ?,
                    manual_override_notes = ?,
                    crm_notes = ?
                WHERE id = ? AND platform_id = ?
                """,
                (
                    form["name"].strip(),
                    form["email"].strip(),
                    form.get("phone", "").strip(),
                    form.get("investor_type", "Non sofisticato"),
                    float(form.get("total_invested") or 0),
                    form.get("onboarding_status", "Da completare"),
                    form.get("entry_test_status", "Da completare"),
                    form.get("loss_simulation_status", "Da completare"),
                    form.get("threshold_status", "Da verificare"),
                    form.get("reflection_status", "Non applicabile"),
                    form.get("crm_status", "Attivo"),
                    form.get("preferred_categories", "").strip(),
                    form.get("risk_profile", "Da profilare"),
                    float(form.get("preferred_ticket_min") or 0),
                    float(form.get("preferred_ticket_max") or 0),
                    form.get("preferred_channel", "Email"),
                    form.get("recurrence_status", "Da valutare"),
                    source_system,
                    form.get("external_investor_id", "").strip(),
                    last_synced_at,
                    form.get("manual_override_notes", "").strip(),
                    form.get("crm_notes", "").strip(),
                    investor_id,
                    ctx["platform_id"],
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "investor", investor_id, "Aggiornamento investitore", form["name"].strip())
            conn.commit()
        self.redirect(f"/investors/{investor_id}", ctx, "Investitore aggiornato.")

    def _conflict_fields(self, form):
        def g(name):
            v = form.get(name, "")
            if v.strip().lower() == "altro":
                return form.get(name + "_altro", "").strip() or v
            return v
        nominativo = form.get("nominativo", "").strip()
        tipo = g("tipo_soggetto").strip()
        soggetti = f"{nominativo} ({tipo})" if (nominativo and tipo) else (nominativo or tipo)
        note = form.get("note", "").strip()
        valutazione = f"{form.get('fondatezza', 'fondato')}; {form.get('gestibilita', 'gestibile')}"
        if note:
            valutazione += f" - {note}"
        esito = form.get("esito", "in lavorazione")
        status = "Chiuso" if esito in {"gestito", "non ammesso"} else ("In analisi" if esito == "in monitoraggio" else "Aperto")
        return {
            "reg_no": form.get("reg_no", ""),
            "opened_at": form.get("opened_at") or today_iso(),
            "soggetti": soggetti,
            "natura_fonte": g("natura_fonte"),
            "rilevato_da": g("rilevato_da"),
            "valutazione": valutazione,
            "misura": g("misura"),
            "esito": esito,
            "atti_collegati": g("atti_collegati"),
            "status": status,
        }

    def post_conflict_create(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_registers"):
            self.redirect("/conflicts", ctx, "Ruolo non abilitato.")
            return
        f = self._conflict_fields(form)
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO conflicts(platform_id, subject, related_party, deal_id, description, mitigation, status, opened_at, closed_at,
                       reg_no, soggetti, natura_fonte, rilevato_da, valutazione, misura, esito, atti_collegati)
                   VALUES (?, ?, '', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ctx["platform_id"], f["soggetti"], f["natura_fonte"], f["misura"], f["status"],
                 f["opened_at"], today_iso() if f["status"] == "Chiuso" else "",
                 f["reg_no"], f["soggetti"], f["natura_fonte"], f["rilevato_da"],
                 f["valutazione"], f["misura"], f["esito"], f["atti_collegati"]),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "conflict", cur.lastrowid, "Registro conflitti", f["soggetti"])
            conn.commit()
        self.redirect("/conflicts", ctx, "Voce registrata nel registro conflitti.")

    def post_conflict_update(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_registers"):
            self.redirect("/conflicts", ctx, "Ruolo non abilitato.")
            return
        cid = int(form["id"])
        f = self._conflict_fields(form)
        with connect() as conn:
            conn.execute(
                """UPDATE conflicts SET reg_no=?, opened_at=?, soggetti=?, subject=?, natura_fonte=?, description=?,
                       rilevato_da=?, valutazione=?, misura=?, mitigation=?, esito=?, status=?, atti_collegati=?
                   WHERE id=? AND platform_id=?""",
                (f["reg_no"], f["opened_at"], f["soggetti"], f["soggetti"], f["natura_fonte"], f["natura_fonte"],
                 f["rilevato_da"], f["valutazione"], f["misura"], f["misura"], f["esito"], f["status"], f["atti_collegati"],
                 cid, ctx["platform_id"]),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "conflict", cid, "Registro conflitti", "modifica")
            conn.commit()
        self.redirect("/conflicts", ctx, "Voce aggiornata.")

    def _complaint_fields(self, form):
        return {
            "protocollo": form.get("protocollo", ""),
            "received_at": form.get("received_at") or today_iso(),
            "complainant": form.get("complainant", "").strip(),
            "classificazione": form.get("classificazione", "Reclamo"),
            "object": form.get("motivi_danno", "").strip(),
            "motivi_danno": form.get("motivi_danno", "").strip(),
            "ricevibilita_date": form.get("ricevibilita_date", ""),
            "riscontro_date": form.get("riscontro_date", ""),
            "misure": form.get("misure", ""),
            "outcome": form.get("misure", ""),
            "esborso": form.get("esborso", "No"),
            "status": form.get("status", "pendente"),
            "channel": form.get("channel", "Email"),
            "owner_user_id": int(form.get("owner_user_id") or self.ctx_from_form(form)["user_id"]),
        }

    def post_complaint_create(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_registers"):
            self.redirect("/complaints", ctx, "Ruolo non abilitato.")
            return
        f = self._complaint_fields(form)
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO complaints(platform_id, received_at, complainant, channel, object, status, outcome, owner_user_id,
                       protocollo, classificazione, motivi_danno, ricevibilita_date, riscontro_date, misure, esborso)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ctx["platform_id"], f["received_at"], f["complainant"], f["channel"], f["object"], f["status"], f["outcome"],
                 f["owner_user_id"], f["protocollo"], f["classificazione"], f["motivi_danno"], f["ricevibilita_date"],
                 f["riscontro_date"], f["misure"], f["esborso"]),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "complaint", cur.lastrowid, "Registro reclami", f["complainant"])
            conn.commit()
        self.redirect("/complaints", ctx, "Voce registrata nel registro reclami.")

    def post_complaint_update(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_registers"):
            self.redirect("/complaints", ctx, "Ruolo non abilitato.")
            return
        f = self._complaint_fields(form)
        cid = int(form["id"])
        with connect() as conn:
            conn.execute(
                """UPDATE complaints SET received_at=?, complainant=?, channel=?, object=?, status=?, outcome=?, owner_user_id=?,
                       protocollo=?, classificazione=?, motivi_danno=?, ricevibilita_date=?, riscontro_date=?, misure=?, esborso=?
                   WHERE id=? AND platform_id=?""",
                (f["received_at"], f["complainant"], f["channel"], f["object"], f["status"], f["outcome"], f["owner_user_id"],
                 f["protocollo"], f["classificazione"], f["motivi_danno"], f["ricevibilita_date"], f["riscontro_date"],
                 f["misure"], f["esborso"], cid, ctx["platform_id"]),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "complaint", cid, "Registro reclami", "modifica")
            conn.commit()
        self.redirect("/complaints", ctx, "Voce aggiornata.")

    def post_person_document_upload(self, form, files):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine") and not user_can(ctx["user"], "upload_document"):
            self.redirect("/compagine", ctx, "Ruolo non abilitato.")
            return
        file_item = files.get("file")
        person_name = (form.get("person_name") or "").strip()
        role_name = form.get("role", "")
        if not file_item or not person_name:
            self.redirect("/compagine", ctx, "Persona o file mancante.")
            return
        doc_type = form.get("document_type") or "Accordo"
        title = form.get("title") or f"{doc_type} - {person_name}"
        with connect() as conn:
            doc_id = save_uploaded_document(
                conn,
                file_item,
                ctx["platform_id"],
                None,
                None,
                "Compagine",
                doc_type,
                title,
                ctx["user_id"],
            )
            cur = conn.execute(
                """
                INSERT INTO person_documents(
                    platform_id, person_name, role, document_type, counterparty, title, notes,
                    signed_at, expires_at, document_id, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    person_name,
                    role_name,
                    doc_type,
                    form.get("counterparty", ""),
                    title,
                    form.get("notes", ""),
                    form.get("signed_at", ""),
                    form.get("expires_at", ""),
                    doc_id,
                    ctx["user_id"],
                    now_iso(),
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "person_document", cur.lastrowid, "Caricamento documento persona", f"{person_name}: {title}")
            conn.commit()
        params = {"platform": ctx["platform_id"], "user": ctx["user_id"], "person": person_name, "role": role_name, "notice": "Documento collegato alla persona."}
        self.send_response(303)
        self.send_header("Location", f"/compagine?{urlencode(params)}")
        self.end_headers()

    def post_supplier_contract_upload(self, form, files):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_compagine") and not user_can(ctx["user"], "upload_document"):
            self.redirect("/compagine", ctx, "Ruolo non abilitato.")
            return
        title = (form.get("title") or "").strip()
        if not title:
            self.redirect("/compagine", ctx, "Titolo contratto mancante.")
            return
        supplier_id = int(form["supplier_id"]) if form.get("supplier_id") else None
        supplier_name = (form.get("supplier_name") or "").strip()
        service_area = form.get("service_area", "")
        doc_id = None
        with connect() as conn:
            if not supplier_id:
                if not supplier_name:
                    self.redirect("/compagine", ctx, "Seleziona o inserisci un fornitore.")
                    return
                cur = conn.execute(
                    """
                    INSERT INTO suppliers(platform_id, name, service_area, owner_role, status, notes, created_at)
                    VALUES (?, ?, ?, ?, 'Attivo', '', ?)
                    """,
                    (ctx["platform_id"], supplier_name, service_area, ctx["user"]["role"], now_iso()),
                )
                supplier_id = cur.lastrowid
            else:
                supplier = conn.execute(
                    "SELECT id FROM suppliers WHERE id = ? AND platform_id = ?",
                    (supplier_id, ctx["platform_id"]),
                ).fetchone()
                if not supplier:
                    self.redirect("/compagine", ctx, "Fornitore non valido per questa piattaforma.")
                    return
            if supplier_id and service_area:
                conn.execute(
                    "UPDATE suppliers SET service_area = COALESCE(NULLIF(service_area, ''), ?) WHERE id = ? AND platform_id = ?",
                    (service_area, supplier_id, ctx["platform_id"]),
                )
            file_item = files.get("file")
            if file_item:
                doc_id = save_uploaded_document(
                    conn,
                    file_item,
                    ctx["platform_id"],
                    None,
                    None,
                    "Compagine",
                    form.get("contract_type") or "Contratto fornitore",
                    title,
                    ctx["user_id"],
                )
            cur = conn.execute(
                """
                INSERT INTO supplier_contracts(
                    platform_id, supplier_id, contract_type, title, counterparty, value,
                    start_date, end_date, renewal_notice, status, document_id, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    supplier_id,
                    form.get("contract_type") or "Contratto fornitore",
                    title,
                    form.get("counterparty", ""),
                    float(form.get("value") or 0),
                    form.get("start_date", ""),
                    form.get("end_date", ""),
                    form.get("renewal_notice", ""),
                    form.get("status", "Attivo"),
                    doc_id,
                    ctx["user_id"],
                    now_iso(),
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "supplier_contract", cur.lastrowid, "Registrazione contratto fornitore", title)
            conn.commit()
        self.redirect("/compagine", ctx, "Contratto fornitore registrato.")

    def post_finance_cost_save(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_finance"):
            self.redirect("/finance", ctx, "Ruolo non abilitato.")
            return
        try:
            cost_id = int(form.get("cost_id") or 0)
        except ValueError:
            cost_id = 0
        title = (form.get("title") or "").strip()
        if not title:
            self.redirect("/finance", ctx, "Titolo costo mancante.")
            return
        try:
            amount = float(form.get("amount") or 0)
        except ValueError:
            amount = 0
        periodicity = form.get("periodicity") or "Annuale"
        if periodicity not in {"Mensile", "Trimestrale", "Semestrale", "Annuale", "Una tantum"}:
            periodicity = "Annuale"
        status = form.get("status") or "Attivo"
        if status not in {"Attivo", "Da pagare", "Pagato", "Stimato", "Archiviato"}:
            status = "Attivo"
        with connect() as conn:
            existing = conn.execute(
                "SELECT id FROM finance_costs WHERE id = ? AND platform_id = ?",
                (cost_id, ctx["platform_id"]),
            ).fetchone() if cost_id else None
            if existing:
                conn.execute(
                    """
                    UPDATE finance_costs
                    SET title = ?, category = ?, amount = ?, periodicity = ?, due_date = ?,
                        status = ?, notes = ?, updated_at = ?
                    WHERE id = ? AND platform_id = ?
                    """,
                    (
                        title,
                        form.get("category") or "Altro costo",
                        amount,
                        periodicity,
                        form.get("due_date", ""),
                        status,
                        (form.get("notes") or "").strip(),
                        now_iso(),
                        cost_id,
                        ctx["platform_id"],
                    ),
                )
                finance_cost_id = cost_id
                action = "Aggiornamento costo finance"
                notice = "Costo aggiornato."
            else:
                cur = conn.execute(
                    """
                    INSERT INTO finance_costs(
                        platform_id, title, category, amount, periodicity, due_date,
                        status, source, notes, created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Manuale', ?, ?, ?, ?)
                    """,
                    (
                        ctx["platform_id"],
                        title,
                        form.get("category") or "Altro costo",
                        amount,
                        periodicity,
                        form.get("due_date", ""),
                        status,
                        (form.get("notes") or "").strip(),
                        ctx["user_id"],
                        now_iso(),
                        now_iso(),
                    ),
                )
                finance_cost_id = cur.lastrowid
                action = "Nuovo costo finance"
                notice = "Costo aggiunto al quadro finance."
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "finance_cost", finance_cost_id, action, title)
            conn.commit()
        self.redirect("/finance", ctx, notice)

    def post_finance_contract_update(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_finance"):
            self.redirect("/finance", ctx, "Ruolo non abilitato.")
            return
        try:
            contract_id = int(form.get("contract_id") or 0)
        except ValueError:
            contract_id = 0
        title = (form.get("title") or "").strip()
        if not contract_id or not title:
            self.redirect("/finance", ctx, "Contratto non valido.")
            return
        try:
            value = float(form.get("value") or 0)
        except ValueError:
            value = 0
        status = form.get("status") or "Attivo"
        if status not in {"Attivo", "In rinnovo", "Da firmare", "Scaduto", "Chiuso", "Archiviato"}:
            status = "Attivo"
        with connect() as conn:
            contract = conn.execute(
                """
                SELECT sc.*, s.name AS supplier_name
                FROM supplier_contracts sc
                JOIN suppliers s ON s.id = sc.supplier_id
                WHERE sc.id = ? AND sc.platform_id = ?
                """,
                (contract_id, ctx["platform_id"]),
            ).fetchone()
            if not contract:
                self.redirect("/finance", ctx, "Contratto non trovato.")
                return
            conn.execute(
                """
                UPDATE supplier_contracts
                SET title = ?, contract_type = ?, value = ?, start_date = ?, end_date = ?,
                    renewal_notice = ?, status = ?
                WHERE id = ? AND platform_id = ?
                """,
                (
                    title,
                    form.get("contract_type") or "Contratto fornitore",
                    value,
                    form.get("start_date", ""),
                    form.get("end_date", ""),
                    (form.get("renewal_notice") or "").strip(),
                    status,
                    contract_id,
                    ctx["platform_id"],
                ),
            )
            service_area = (form.get("service_area") or "").strip()
            if service_area:
                conn.execute(
                    "UPDATE suppliers SET service_area = ? WHERE id = ? AND platform_id = ?",
                    (service_area, contract["supplier_id"], ctx["platform_id"]),
                )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "supplier_contract", contract_id, "Aggiornamento costo contratto", title)
            conn.commit()
        self.redirect("/finance", ctx, "Costo da contratto aggiornato.")

    def post_finance_contract_to_manual(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_finance"):
            self.redirect("/finance", ctx, "Ruolo non abilitato.")
            return
        try:
            contract_id = int(form.get("contract_id") or 0)
        except ValueError:
            contract_id = 0
        with connect() as conn:
            contract = conn.execute(
                """
                SELECT sc.*, s.name AS supplier_name, s.service_area
                FROM supplier_contracts sc
                JOIN suppliers s ON s.id = sc.supplier_id
                WHERE sc.id = ? AND sc.platform_id = ?
                """,
                (contract_id, ctx["platform_id"]),
            ).fetchone()
            if not contract:
                self.redirect("/finance", ctx, "Contratto non trovato.")
                return
            existing = conn.execute(
                """
                SELECT id FROM finance_costs
                WHERE platform_id = ? AND linked_contract_id = ?
                """,
                (ctx["platform_id"], contract_id),
            ).fetchone()
            if existing:
                self.redirect("/finance", ctx, "Costo manuale gia' presente.", {"mode": "cost", "cost_id": existing["id"]})
                return
            cur = conn.execute(
                """
                INSERT INTO finance_costs(
                    platform_id, title, category, amount, periodicity, due_date,
                    status, source, notes, linked_contract_id, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'Annuale', ?, ?, 'Manuale da contratto', ?, ?, ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    contract["title"],
                    contract["service_area"] or contract["contract_type"] or "Fornitore",
                    float(contract["value"] or 0),
                    contract["end_date"],
                    contract["status"],
                    f"Origine contratto: {contract['supplier_name']} - {contract['contract_type']}",
                    contract_id,
                    ctx["user_id"],
                    now_iso(),
                    now_iso(),
                ),
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "finance_cost", cur.lastrowid, "Costo manuale da contratto", contract["title"])
            conn.commit()
            cost_id = cur.lastrowid
        self.redirect("/finance", ctx, "Costo copiato tra le voci manuali.", {"mode": "cost", "cost_id": cost_id})

    def post_campaign_update(self, form):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "manage_finance"):
            self.redirect("/finance", ctx, "Ruolo non abilitato.")
            return
        try:
            update_id = int(form.get("update_id") or 0)
        except ValueError:
            update_id = 0
        try:
            deal_id = int(form.get("deal_id") or 0)
        except ValueError:
            deal_id = 0
        try:
            funding_target = float(form.get("funding_target") or 0)
        except ValueError:
            funding_target = 0
        try:
            platform_fee_percent = float(form.get("platform_fee_percent") or 5)
        except ValueError:
            platform_fee_percent = 5
        raised_raw = (form.get("raised_amount") or "").strip()
        try:
            raised_amount = float(raised_raw) if raised_raw else 0
        except ValueError:
            raised_amount = 0
        try:
            investors_count = int(form.get("investors_count") or 0)
        except ValueError:
            investors_count = 0
        status = form.get("status") or "Rilevazione"
        if status not in {"Rilevazione", "In crescita", "Sotto target", "Target raggiunto", "Chiusa"}:
            status = "Rilevazione"
        as_of_date = form.get("as_of_date") or today_iso()
        with connect() as conn:
            deal = conn.execute(
                "SELECT id, title FROM deals WHERE id = ? AND platform_id = ?",
                (deal_id, ctx["platform_id"]),
            ).fetchone()
            if not deal:
                self.redirect("/finance", ctx, "Campagna non valida.")
                return
            if funding_target >= 0:
                conn.execute(
                    "UPDATE deals SET funding_target = ?, platform_fee_percent = ?, updated_at = ? WHERE id = ? AND platform_id = ?",
                    (funding_target, platform_fee_percent, today_iso(), deal_id, ctx["platform_id"]),
                )
            existing = conn.execute(
                "SELECT id FROM campaign_updates WHERE id = ? AND platform_id = ?",
                (update_id, ctx["platform_id"]),
            ).fetchone() if update_id else None
            if existing:
                conn.execute(
                    """
                    UPDATE campaign_updates
                    SET deal_id = ?, as_of_date = ?, raised_amount = ?, investors_count = ?,
                        status = ?, notes = ?
                    WHERE id = ? AND platform_id = ?
                    """,
                    (
                        deal_id,
                        as_of_date,
                        raised_amount,
                        investors_count,
                        status,
                        (form.get("notes") or "").strip(),
                        update_id,
                        ctx["platform_id"],
                    ),
                )
                campaign_update_id = update_id
                action = "Aggiornamento rilevazione campagna"
                notice = "Andamento campagna aggiornato."
            else:
                cur = conn.execute(
                    """
                    INSERT INTO campaign_updates(
                        platform_id, deal_id, as_of_date, raised_amount, investors_count,
                        status, source, notes, created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'Manuale', ?, ?, ?)
                    """,
                    (
                        ctx["platform_id"],
                        deal_id,
                        as_of_date,
                        raised_amount,
                        investors_count,
                        status,
                        (form.get("notes") or "").strip(),
                        ctx["user_id"],
                        now_iso(),
                    ),
                )
                campaign_update_id = cur.lastrowid
                action = "Rilevazione andamento campagna"
                notice = "Andamento campagna registrato."
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "campaign_update", campaign_update_id, action, deal["title"])
            conn.commit()
        self.redirect("/finance", ctx, notice)

    def post_communication_generate(self, form):
        ctx = self.ctx_from_form(form)
        workflow_id = form.get("workflow_id") or COMMUNICATION_WORKFLOWS[0]["id"]
        item = next((w for w in COMMUNICATION_WORKFLOWS if w["id"] == workflow_id), COMMUNICATION_WORKFLOWS[0])
        field_rows = []
        for index, field in enumerate(item["fields"]):
            field_rows.append((field[0], form.get(f"field_{index}", "")))
        payload = {
            "workflow_id": item["id"],
            "source_output_id": form.get("source_output_id", ""),
            "fields": [{"label": label, "value": value} for label, value in field_rows],
            "reviewer": form.get("reviewer", ctx["user"]["name"]),
            "manual_notes": form.get("manual_notes", ""),
            "generated_at": now_iso(),
        }
        missing_cell = '<span class="missing">Da completare</span>'
        field_html = "".join(
            f"<tr><th>{esc(label)}</th><td>{esc(value) if value else missing_cell}</td></tr>"
            for label, value in field_rows
        )
        required_docs = "".join(f"<li>{esc(doc)}</li>" for doc in item["required_docs"])
        prefill = "".join(f"<li>{esc(src)}</li>" for src in item["prefill"])
        content = f"""<!doctype html>
<html lang="it">
<head><meta charset="utf-8"><title>{esc(item['title'])}</title>
<style>
body{{font-family: Georgia, 'Times New Roman', serif; margin:42px; color:#1f2528;}}
h1{{font-size:24px; margin-bottom:4px;}} h2{{font-size:16px; margin-top:28px;}}
.meta{{color:#666; font-family: Arial, sans-serif; font-size:12px;}}
table{{border-collapse:collapse; width:100%; margin-top:12px;}}
th,td{{border:1px solid #d8d1c7; padding:9px; text-align:left; vertical-align:top;}}
th{{width:32%; background:#f7f4ef;}}
.missing{{color:#a33; font-style:italic;}}
li{{margin:5px 0;}}
</style></head>
<body>
<p class="meta">Bozza generata dalla compliance suite - {esc(now_iso())}</p>
<h1>{esc(item['title'])}</h1>
<p><strong>Autorita:</strong> {esc(item['authority'])}<br>
<strong>Canale:</strong> {esc(item['channel'])}<br>
<strong>Scadenza:</strong> {esc(item['deadline'])}<br>
<strong>Riferimento:</strong> {esc(item['reference'])}<br>
<strong>Output atteso:</strong> {esc(item['output'])}</p>
<h2>Campi compilati</h2>
<table>{field_html}</table>
<h2>Precompilazione suggerita</h2>
<ul>{prefill}</ul>
<h2>Allegati richiesti</h2>
<ul>{required_docs}</ul>
<h2>Note operative</h2>
<p>{esc(form.get('manual_notes', '')) or 'Nessuna nota.'}</p>
<p class="meta">Fonte normativa/template: {esc(item['source'])}</p>
<p class="meta">Responsabile validazione: {esc(form.get('reviewer', ctx['user']['name']))}</p>
</body></html>"""
        with connect() as conn:
            doc_id = generated_document(
                conn,
                ctx["platform_id"],
                None,
                None,
                "Comunicazioni",
                "Comunicazione autorita",
                f"Bozza - {item['title']}",
                f"{item['id']}.html",
                content,
                ctx["user_id"],
            )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "communication", doc_id, "Generazione bozza comunicazione", item["title"])
            cur = conn.execute(
                """
                INSERT INTO communication_outputs(platform_id, workflow_id, period, status, document_id, reviewer, notes, payload_json, created_by, created_at, updated_at)
                VALUES (?, ?, ?, 'Bozza generata', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ctx["platform_id"],
                    item["id"],
                    field_rows[0][1] if field_rows else item["reference"],
                    doc_id,
                    form.get("reviewer", ctx["user"]["name"]),
                    form.get("manual_notes", ""),
                    json.dumps(payload, ensure_ascii=True),
                    ctx["user_id"],
                    now_iso(),
                    now_iso(),
                ),
            )
            output_id = cur.lastrowid
            conn.commit()
        self.redirect("/communications", ctx, "Bozza comunicazione generata e censita nel flusso.", {"tab": "generatore", "comm": item["id"], "output": output_id})

    def post_communication_output_status(self, form):
        ctx = self.ctx_from_form(form)
        output_id = int(form.get("output_id", 0) or 0)
        workflow_id = form.get("workflow_id") or COMMUNICATION_WORKFLOWS[0]["id"]
        allowed = {"Bozza generata", "Validata - da inviare", "Inviata", "Approvata", "Respinta"}
        status = form.get("status") or "Bozza generata"
        if status not in allowed:
            status = "Bozza generata"
        with connect() as conn:
            output = row("SELECT * FROM communication_outputs WHERE id = ? AND platform_id = ?", (output_id, ctx["platform_id"]))
            if output:
                conn.execute(
                    "UPDATE communication_outputs SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now_iso(), output_id),
                )
                log_audit(conn, ctx["platform_id"], ctx["user_id"], "communication", output_id, f"Stato output: {status}", workflow_id)
                conn.commit()
        self.redirect("/communications", ctx, "Stato comunicazione aggiornato.", {"tab": "generatore", "comm": workflow_id})

    def post_communication_output_delete(self, form):
        ctx = self.ctx_from_form(form)
        output_id = int(form.get("output_id", 0) or 0)
        workflow_id = form.get("workflow_id") or COMMUNICATION_WORKFLOWS[0]["id"]
        with connect() as conn:
            output = row(
                """
                SELECT co.*, doc.storage_path
                FROM communication_outputs co
                LEFT JOIN documents doc ON doc.id = co.document_id
                WHERE co.id = ? AND co.platform_id = ?
                """,
                (output_id, ctx["platform_id"]),
            )
            if output:
                if output["document_id"]:
                    conn.execute("DELETE FROM documents WHERE id = ? AND platform_id = ?", (output["document_id"], ctx["platform_id"]))
                    if output["storage_path"]:
                        file_path = (BASE_DIR / output["storage_path"]).resolve()
                        if str(file_path).startswith(str(BASE_DIR.resolve())) and file_path.exists():
                            file_path.unlink()
                conn.execute("DELETE FROM communication_outputs WHERE id = ?", (output_id,))
                log_audit(conn, ctx["platform_id"], ctx["user_id"], "communication", output_id, "Rimozione output comunicazione", workflow_id)
                conn.commit()
        self.redirect("/communications", ctx, "Output comunicazione rimosso.", {"tab": "generatore", "comm": workflow_id})

    def post_document_upload(self, form, files):
        ctx = self.ctx_from_form(form)
        if not user_can(ctx["user"], "upload_document"):
            self.redirect("/documents", ctx, "Ruolo non abilitato.")
            return
        file_item = files.get("file")
        if file_item is None or not getattr(file_item, "filename", ""):
            self.redirect("/documents", ctx, "File mancante.")
            return
        deal_id = int(form["deal_id"]) if form.get("deal_id") else None
        proponent_id = int(form["proponent_id"]) if form.get("proponent_id") else None
        if deal_id and not proponent_id:
            d = fetch_deal(deal_id)
            proponent_id = d["proponent_id"] if d else None
        entity_kind = form.get("entity_kind") or ""
        category = form.get("category") or "Documentazione"
        title = form.get("title") or file_item.filename
        origin = form.get("origin") or "Archivio"
        if entity_kind == "person":
            origin = "Persona"
        elif entity_kind == "supplier":
            origin = "Fornitore"
        elif entity_kind == "deal":
            origin = "Deal"
        elif entity_kind == "proponent":
            origin = "Proponente"
        doc_date = (form.get("doc_date") or "").strip()
        description = (form.get("description") or "").strip()
        with connect() as conn:
            doc_id = save_uploaded_document(
                conn,
                file_item,
                ctx["platform_id"],
                deal_id,
                proponent_id,
                origin,
                category,
                title,
                ctx["user_id"],
                doc_date,
                description,
            )
            if entity_kind == "person":
                person_ref = form.get("person_ref") or ""
                person_name = (form.get("person_name") or "").strip()
                person_role = (form.get("person_role") or "").strip()
                if person_ref:
                    parts = person_ref.split("||", 1)
                    person_name = parts[0].strip()
                    if len(parts) > 1 and not person_role:
                        person_role = parts[1].strip()
                if person_name:
                    conn.execute(
                        """
                        INSERT INTO person_documents(
                            platform_id, person_name, role, document_type, counterparty, title, notes,
                            signed_at, expires_at, document_id, created_by, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ctx["platform_id"],
                            person_name,
                            person_role,
                            category,
                            form.get("counterparty", ""),
                            title,
                            form.get("notes", ""),
                            "",
                            form.get("expires_at", ""),
                            doc_id,
                            ctx["user_id"],
                            now_iso(),
                        ),
                    )
            elif entity_kind == "supplier":
                supplier_id = int(form["supplier_id"]) if form.get("supplier_id") else None
                supplier_name = (form.get("supplier_name") or "").strip()
                service_area = form.get("service_area", "")
                if not supplier_id and supplier_name:
                    cur = conn.execute(
                        """
                        INSERT INTO suppliers(platform_id, name, service_area, owner_role, status, notes, created_at)
                        VALUES (?, ?, ?, ?, 'Attivo', '', ?)
                        """,
                        (ctx["platform_id"], supplier_name, service_area, ctx["user"]["role"], now_iso()),
                    )
                    supplier_id = cur.lastrowid
                if supplier_id:
                    if service_area:
                        conn.execute(
                            "UPDATE suppliers SET service_area = COALESCE(NULLIF(service_area, ''), ?) WHERE id = ? AND platform_id = ?",
                            (service_area, supplier_id, ctx["platform_id"]),
                        )
                    conn.execute(
                        """
                        INSERT INTO supplier_contracts(
                            platform_id, supplier_id, contract_type, title, counterparty, value,
                            start_date, end_date, renewal_notice, status, document_id, created_by, created_at
                        ) VALUES (?, ?, ?, ?, ?, 0, '', ?, ?, 'Da classificare', ?, ?, ?)
                        """,
                        (
                            ctx["platform_id"],
                            supplier_id,
                            category,
                            title,
                            form.get("counterparty", ""),
                            form.get("expires_at", ""),
                            form.get("notes", ""),
                            doc_id,
                            ctx["user_id"],
                            now_iso(),
                        ),
                    )
            log_audit(conn, ctx["platform_id"], ctx["user_id"], "document", doc_id, "Caricamento documento", title)
            conn.commit()
        if entity_kind == "proponent" and proponent_id:
            self.redirect(f"/proponents/{proponent_id}", ctx, "Documento caricato.")
        elif entity_kind == "deal" and deal_id:
            self.redirect(f"/deals/{deal_id}", ctx, "Documento caricato.")
        elif entity_kind in {"person", "supplier"} or origin == "Compagine":
            self.redirect("/compagine", ctx, "Documento caricato.")
        else:
            self.redirect("/documents", ctx, "Documento caricato.")

    def download_document(self, document_id):
        doc = row("SELECT * FROM documents WHERE id = ?", (document_id,))
        if not doc:
            self.not_found()
            return
        file_path, ok = resolve_storage_path(doc["storage_path"])
        if not ok:
            self.not_found()
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(doc["filename"])[0] or "application/octet-stream")
        self.send_header("Content-Disposition", f'inline; filename="{sanitize_filename(doc["filename"])}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def download_official_template(self, path):
        filename = sanitize_filename(path.removeprefix("/official-templates/"))
        allowed = {item["filename"] for item in OFFICIAL_TEMPLATES}
        if filename not in allowed:
            self.not_found()
            return
        file_path = (TEMPLATE_DIR / filename).resolve()
        if not str(file_path).startswith(str(TEMPLATE_DIR.resolve())) or not file_path.exists():
            self.not_found()
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(filename)[0] or "application/octet-stream")
        self.send_header("Content-Disposition", f'inline; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def not_found(self):
        self.send_html("<h1>404</h1><p>Pagina non trovata.</p>", status=404)

    def error_page(self, exc):
        self.send_html(f"<h1>Errore</h1><pre>{esc(type(exc).__name__)}: {esc(exc)}</pre>", status=500)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


def main():
    init_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8772
    server = ThreadingHTTPServer(("127.0.0.1", port), App)
    print(f"OmniCrowd running at http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
