# Contesto progetto ECSP Compliance Suite

## Obiettivo

La suite deve essere un sistema operativo per piattaforme ECSP: compliance, governance, deal, investitori, proponenti, documenti, comunicazioni obbligatorie e contesto IA.

Il principio centrale e' che ogni dato operativo deve poter essere collegato a documenti, persone/soggetti, funzioni, contratti, scadenze e comunicazioni. L'IA dovra' usare questo archivio strutturato per precompilare output, segnalazioni, fascicoli e controlli.

## Logica documenti

La pagina Documenti non e' solo upload file: e' archivio sofisticato e generatore di contesto.

Ogni documento deve avere tassonomie e collegamenti:
- origine: compagine, CdA, deal, proponente, investitore, reclamo, conflitto, comunicazione, fornitore;
- tipo documento: statuto, atto societario, domanda autorizzazione, allegato autorizzazione, bilancio, contratto, delega, procura, due diligence, KIIS, verbale, parere;
- soggetto collegato: persona fisica, societa/ente, proponente, investitore, fornitore;
- funzione o area collegata;
- scadenza, stato, fonte, eventuale uso per comunicazioni obbligatorie.

I nuovi documenti devono aprirsi in popup/modale, non con form lunghi in basso pagina.

## Compagine: logica corretta

La Compagine deve razionalizzare organigramma, anagrafiche, accordi, contratti, documenti e scadenze.

Non bisogna ragionare come "persona vs fornitore". Il concetto giusto e' "soggetto":
- Persona fisica;
- Societa / ente.

Qualunque soggetto puo' coprire una funzione o un servizio. Una societa puo' stare in un box operativo, una persona puo' stare in piu' funzioni, un contratto puo' coprire piu' funzioni.

La relazione centrale deve essere unica:

`soggetto -> funzione organigramma -> documento/accordo -> scadenza -> uso per controlli/comunicazioni`

Questa relazione deve essere modificabile sia dall'organigramma sia dalla scheda anagrafica del soggetto. Se si collega una funzione da una parte, l'altra vista deve mostrare lo stesso collegamento. La funzione va scelta dalla lista dei box presenti nell'organigramma; solo se manca si usa `Nuova funzione`, indicando il quadrante in cui dovra' comparire.

Ogni relazione funzione-soggetto deve poter indicare:
- ruolo/responsabilita;
- data inizio;
- data fine o scadenza;
- documento/contratto collegato, con tre casi: nessun documento, nuovo documento da caricare, documento esistente gia' presente in archivio;
- note operative.

Non deve esserci un campo manuale "uso per comunicazioni": i documenti collegati servono automaticamente come fonte generale per contesto IA, scadenzario, controlli e comunicazioni obbligatorie.
Non serve nemmeno uno "stato collegamento" manuale nel form rapido: il presidio si controlla da date, scadenze, documento collegato e presenza/assenza delle informazioni.

## Organigramma operativo

L'organigramma operativo e' una mappa per quadranti/gruppi:
- Governance;
- Funzioni responsabili;
- Area di controllo;
- Servizi in outsourcing;
- Comitato tecnico progetti;
- Advisory Committee;
- Area operativa.

Ogni quadrante ha un `+` nel titolo:
- serve ad aggiungere un nuovo blocco/funzione dentro quel quadrante;
- se il `+` viene premuto da un quadrante specifico, il blocco puo' essere creato solo li' e il quadrante deve restare bloccato nel popup;
- se il blocco non ha soggetti collegati, deve mostrare `da censire`;
- il nuovo blocco diventa subito una funzione selezionabile nei form di collegamento anagrafica/funzione;
- il popup deve chiedere quadrante, titolo blocco, tipo blocco, stato, descrizione/presidio.

Ogni box/blocco ha un `+` interno:
- serve ad aggiungere rapidamente un soggetto dentro quel box;
- il popup deve permettere sia scelta da anagrafica esistente sia creazione nuovo soggetto;
- deve indicare ruolo, data inizio, data fine/scadenza, documento collegato, note.

Ogni box ha anche un `-` in basso:
- serve a rimuovere/archiviare il blocco;
- deve chiedere conferma prima dell'azione.

I nomi dentro i box devono essere testo semplice separato da virgole, non chip circolari.

## Lista anagrafica

La Lista anagrafica deve essere simile alle pagine CRM investitori/proponenti:
- tabella leggibile;
- click sul soggetto apre pagina dettaglio, non popup;
- mostra tipo soggetto, funzioni collegate, accordi/contratti, documenti, scadenze, stato.

Scheda singolo soggetto:
- dati anagrafici prima di tutto;
- funzioni collegate con data inizio/fine;
- documenti collegati alla funzione;
- accordi/contratti collegati e relative scadenze;
- azioni operative: modifica anagrafica, aggiungi funzione, collega documento, collega accordo;
- i pulsanti modifica/archivia/elimina funzione devono funzionare almeno come popup/confirm in fase design.

Dato demo attuale:
- Mario Rossi e' inserito come persona demo in `Presidio demo architettura`;
- serve a verificare funzioni, documenti, accordi, popup e azioni operative.

## Comunicazioni

La pagina Comunicazioni deve avere:
- scadenzario con stati: da fare, bozza, validata/da inviare, inviata, approvata, respinta;
- colori: arancione per da fare, rosso progressivo vicino a scadenza, giallo per inviata, verde per approvata/conclusa;
- generatore comunicazioni collegato allo scadenzario;
- storico comunicazioni;
- obblighi/template/fonti ufficiali in sotto-voce separata.

Le fonti devono essere solo ufficiali: CONSOB, Banca d'Italia, normativa UE. Documenti forniti da Pariter servono come esempio operativo, non come fonte da mostrare.

Il generatore deve:
- precompilare dai documenti e dati gia' presenti;
- distinguere dati precompilati, manuali e vuoti;
- mostrare allegati richiesti con pallino verde se trovati e grigio se mancanti;
- permettere modifica solo finche' bozza/validata, non quando inviata o approvata;
- permettere download, rimozione allegati e consultazione storico.

## UX generale

Lo stile deve restare sobrio, denso e operativo:
- niente landing page;
- niente card decorative inutili;
- modali per inserimenti rapidi;
- tabelle e schede CRM per liste complesse;
- pulsanti chiari e piccoli;
- usare testo tecnico, non spiegazioni marketing in pagina.

Quando la logica non e' ancora backend, e' accettabile implementare flussi demo purche' siano coerenti con l'architettura futura.
