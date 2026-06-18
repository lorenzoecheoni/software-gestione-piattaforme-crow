# ECSP Compliance Suite - Context for Claude

This file is the working context for continuing the project in Claude or Claude Code after exporting the repository to GitHub.

## Project Goal

Build an internal compliance and operations suite for ECSP crowdfunding platforms under Reg. (EU) 2020/1503.

The app is not meant to be a marketing site. It is an operational cockpit for:

- platform corporate structure and organigram;
- governance and board workflows;
- deal onboarding and approval;
- proponents and investors CRM;
- conflicts of interest and complaints;
- official regulatory communications;
- document archive and AI context;
- finance, costs, contracts and campaign economics.

Core product principle:

Every operational datum must be connectable to documents, people/subjects, functions, contracts, deadlines, communications and AI context.

## Current Technical Shape

This is a local Python prototype:

- main app: `app.py`;
- styling: `static/styles.css`;
- optional static JS: `static/app.js`;
- SQLite database: `data/ecsp_suite.db`;
- generated/uploaded files: `uploads/`;
- official communication templates: `templates/official/`;
- docs: `docs/`.

The app uses Python standard-library HTTP handling with SQLite. There is no framework yet.

Run locally:

```bash
cd outputs/ecsp-compliance-suite
python3 app.py 8772
```

Then open:

```text
http://127.0.0.1:8772
```

In the current local Codex environment the Python runtime used was:

```bash
/Users/lorenzo/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 app.py 8772
```

Before finishing changes, always run:

```bash
python3 -c "compile(open('app.py', encoding='utf-8').read(), 'app.py', 'exec')"
```

If the local server is running, restart it after Python changes.

## GitHub Export Notes

Recommended files/directories to commit:

- `app.py`
- `README.md`
- `CLAUDE.md`
- `CONTESTO_PROGETTO_ECSP.md`
- `docs/`
- `static/`
- `templates/`

Usually do not commit:

- `__pycache__/`
- `data/ecsp_suite.db` unless a demo database snapshot is intentionally wanted;
- `uploads/` unless demo generated documents are intentionally wanted;
- old root-level `ecsp_suite.sqlite3` if present, because the active DB is `data/ecsp_suite.db`.

Suggested `.gitignore` if missing:

```gitignore
__pycache__/
*.pyc
.DS_Store
data/*.db
uploads/
ecsp_suite.sqlite3
```

## Current Navigation Order

The menu order requested by the user is:

1. Dashboard
2. Compagine
3. Finance
4. Governance
5. Deal
6. Proponenti
7. Investitori
8. Conflitti d'int.
9. Reclami
10. Comunicazioni
11. Documenti
12. Assistente IA

Do not move Finance back before Compagine.

## UX Direction

The interface must feel like a sober professional operating system:

- dense but readable;
- no landing page;
- no decorative cards or marketing sections;
- compact tables and CRM-like detail pages;
- modals for quick inserts/edits;
- avoid long forms at the bottom of pages;
- use small buttons and clear operational labels;
- no circular chips around names in the organigram; use comma-separated names;
- avoid explanations in the UI unless they are operationally necessary.

## Key Domain Model

The important conceptual model is:

```text
subject -> function/service -> contract/document -> deadline -> control/communication/AI context
```

Terms:

- `subject` can be either a natural person or a company/entity.
- A subject can cover one or many functions.
- A company can cover a function or outsourced service.
- A contract can govern one or many functions/services.
- A function belongs to an area/quadrant of the organigram.
- Documents are general sources for AI context, controls, scadenziario and communications. Do not add manual "use for communications" flags.

## Compagine

The Compagine section is central. It must rationalize:

- organigram;
- anagrafiche;
- qualified shareholders/participogram;
- corporate documents;
- authorization documents;
- financial statements;
- person/company documents;
- supplier contracts;
- outsourced services;
- deadlines.

### Organigram

Main areas/quadrants:

- Governance
- Funzioni responsabili
- Area di controllo
- Servizi in outsourcing
- Comitato tecnico progetti
- Advisory Committee
- Area operativa

Important user correction:

- `CoVi` was a misunderstanding.
- What was called CoVi in some old labels is usually `Comitato Tecnico`.
- `Advisory Committee` is separate and comes later in the deal approval flow.

Each quadrant has a plus button to add a new block/function inside that quadrant.

Each block has:

- plus button to add an existing/new subject to that function;
- minus button to remove/archive the block;
- if empty, show `da censire`.

Adding a subject to a block must allow:

- choose existing anagrafica or create new subject;
- subject type: natural person or company/entity;
- role/responsibility;
- start date;
- end date/deadline;
- document/contract: none, existing document, or new upload;
- notes.

Changes from organigram and anagrafica must reflect each other.

### Lista anagrafica

The user wants this like investors/proponents CRM:

- table/list first;
- click subject opens a full detail page style, not only a modal;
- show all linked functions, contracts, documents and deadlines;
- actions: modify data, add function, connect document, connect contract.

Existing demo subject:

- `Mario Rossi`, used to verify demo function/document flows.

### Partecipogramma / Shareholders

Qualified shareholders must be editable:

- add subjects;
- change percentages;
- click company/person;
- upload documents such as statute, company registry extract, cap table, beneficial owners, shareholder data and honorability requirements.

Implemented tables include:

- `shareholders`
- `shareholder_documents`

## Finance

Finance was recently added and should stay after Compagine in the nav.

Purpose:

- total structural costs;
- costs detected from contracts;
- manual costs;
- supplier/service cost deadlines;
- campaign progress;
- platform fee retained on campaigns;
- estimated revenue and break-even gap.

Important user correction:

- Do not split the top summary between "manual costs" and "contract costs".
- The summary should show total structural costs, tracked campaign amount, estimated revenue from fee and break-even gap.
- For campaign economics, the percentage is the fee retained by the platform.
- Default platform fee is `5%`, but it can vary by campaign.

Current finance behavior:

- costs from supplier contracts are shown in `Costi e scadenze`;
- contract rows must show the contract/document as reachable;
- contract rows have `Modifica`;
- contract rows have `Manuale`, which creates a manual finance cost linked to the contract;
- when a contract is converted/copied to manual, Finance should avoid double-counting it as both contract and manual cost;
- manual costs remain editable in a modal;
- campaign modal includes target, raised amount, fee retained %, investors, date, status and notes;
- estimated revenue is `raised_amount * platform_fee_percent / 100`.

Relevant tables:

- `finance_costs`
- `supplier_contracts`
- `campaign_updates`
- `deals.platform_fee_percent`

Relevant handlers:

- `page_finance`
- `post_finance_cost_save`
- `post_finance_contract_update`
- `post_finance_contract_to_manual`
- `post_campaign_update`

## Communications

The Communications page must be a full operational system, not a static list.

Required structure:

- Scadenzario
- Generatore
- Storico
- Obblighi/template/fonti ufficiali

State logic:

- da fare: orange;
- closer to deadline: progressively red;
- bozza generata;
- validata / da inviare;
- inviata: yellow;
- approvata/conclusa: green;
- respinta: retained in history and can be reopened/archived.

Rules:

- only official sources should be shown: CONSOB, Banca d'Italia, EU regulation.
- Pariter files supplied by the user are examples, not official sources to display as source.
- generated drafts can be modified or removed while draft/validated.
- sent/approved/rejected outputs are read-only except upload/remove attachments as applicable.

Generator requirements:

- prefill from existing structured data and indexed documents where possible;
- distinguish prefilled, manual and empty fields visually;
- required attachments get green dot if found and grey dot if missing;
- show sending instructions and destination/channel.

Relevant constants/tables:

- `COMMUNICATION_WORKFLOWS`
- `OFFICIAL_TEMPLATES`
- `communication_outputs`

Relevant handlers:

- `page_communications`
- `post_communication_generate`
- `post_communication_output_status`
- `post_communication_output_delete`

## Documents

The Documents page is a sophisticated archive and AI-context generator.

It must not be a simple upload form at the bottom.

Requirements:

- upload via button + popup/modal;
- search and filters;
- taxonomy by origin/category/entity/type;
- ability to find all documents linked to a person, company, supplier, proponent, investor, deal or function;
- document origin examples: Compagine, CdA, Deal, Proponente, Investitore, Reclamo, Conflitto, Comunicazioni, Fornitore;
- document type examples: statute, company act, authorization application, authorization attachments, financial statements, contract, delegation, proxy, due diligence, KIIS, minutes, opinion.

Relevant table:

- `documents`

Upload handlers include:

- `post_document_upload`
- `post_person_document_upload`
- `post_supplier_contract_upload`
- `post_shareholder_document_upload`

## Governance

Governance includes:

- CdA convocations;
- meeting links/email;
- board sessions;
- minutes and deliberations;
- members;
- automatic agenda/context from deals in approval.

The user wanted:

- ability to send email to interested parties from there;
- create Meet/Zoom link;
- sections/forms must be navigable;
- creation of new session should show the detail form on the right.

This is currently prototype/design-first; do not overbuild external email/Meet integration unless requested.

## Deals

Deals are the core workflow:

- create deal;
- collect documentation;
- due diligence/checklist;
- Comitato Tecnico review;
- Advisory Committee review when applicable;
- CdA/Board approval;
- contract;
- pre-publication;
- publication/campaign;
- audit trail/report.

For future work, link deal progress to Finance campaign metrics and investor CRM.

## Investors CRM

Investors should become a serious CRM:

- clickable investor;
- full history;
- investments by deal;
- amount invested;
- registration date;
- sophisticated/non-sophisticated;
- phone number;
- recurrence;
- preferred deal types;
- matching map;
- statistics and solicitation logic;
- data imported via API but manually editable.

Relevant handlers:

- `page_investors`
- `page_investor_detail`
- `post_investor_create`
- `post_investor_update`

## Proponents CRM

Proponents should mirror a complete CRM:

- profile details;
- editable fields;
- documents;
- onboarding;
- scoring;
- API/import data plus manual override;
- linked deals and due diligence documents.

Relevant handlers:

- `page_proponents`
- `page_proponent_detail`
- `post_proponent_create`
- `post_proponent_update`

## Current User Preferences And Corrections

Keep these in mind:

- Finance after Compagine.
- Finance top summary must not split manual/contract costs.
- Campaign percentage in Finance is fee retained by the platform, default 5%.
- Contract cost rows must show the contract as reachable/modifiable and allow manual conversion.
- No visible "use for communications" checkbox on documents; documents serve all contexts.
- In organigram, names should be comma-separated text, not circular chips.
- Subject can be person or company; do not hard-code person vs supplier as separate worlds.
- Adding function/subject must match existing organigram functions and anagrafiche.
- Popups/modals for quick additions, not long bottom forms.
- Use official regulatory sources in Communications.
- Avoid showing Pariter as source; user files are examples.

## Important Routes

Core GET routes:

- `/`
- `/compagine`
- `/finance`
- `/governance`
- `/deals`
- `/proponents`
- `/investors`
- `/conflicts`
- `/complaints`
- `/communications`
- `/documents`
- `/assistant`

Finance POST routes:

- `/finance/cost-save`
- `/finance/contract-update`
- `/finance/contract-to-manual`
- `/finance/campaign-update`

Compagine POST routes:

- `/compagine/function-save`
- `/compagine/assignment-save`
- `/compagine/assignment-delete`
- `/shareholders/create`
- `/shareholders/{id}/update`
- `/shareholders/{id}/document-upload`
- `/person-documents/upload`
- `/supplier-contracts/upload`

Communications POST routes:

- `/communications/generate`
- `/communications/output-status`
- `/communications/output-delete`

## Data And Demo Platforms

Demo platforms:

- `Pariter Equity`
- `ISI Crowd`

Demo users:

- Alessia Ricci: compliance
- Marco Bianchi: legal
- Giulia Ferri: technical committee
- Paolo Conti: old CoVi/advisory demo role
- Elena Martini: board
- Sara De Luca: operator

The selector in the header simulates user/platform switching.

## Implementation Rules For Future Agents

When continuing this repo:

1. Read `app.py` before changing behavior.
2. Keep edits focused; this is a prototype with many user-driven design decisions.
3. Do not introduce a framework unless explicitly requested.
4. Preserve the sober, dense UI style in `static/styles.css`.
5. Prefer modals for quick creates/edits.
6. Avoid duplicate concepts; link data rather than copying when possible.
7. After Python edits, compile the app.
8. Restart the server and verify the affected page.
9. Do not overwrite user/demo data unless the user asks.

Minimal validation checklist:

```bash
python3 -c "compile(open('app.py', encoding='utf-8').read(), 'app.py', 'exec')"
curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8772/finance?platform=2&user=1'
```

Expected HTTP status is `200`.

## Known Technical Debt

- The app is a single large `app.py`; eventually split into modules/templates.
- No real authentication/authorization beyond role simulation.
- No real email/Meet/Zoom integration yet.
- No external API adapters yet; current fields model where adapters will feed data.
- SQLite is fine for prototype; production would need migrations and stronger storage.
- File uploads/generated docs are local.
- Some flows are intentionally design-first and not fully complete.

