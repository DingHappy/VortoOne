import type { HelpSlug } from "./index";
import type { HelpDocument } from "./types";

/** The help pages in it: a page not here shows in English. */
export const helpIt: Partial<Record<HelpSlug, HelpDocument>> = {
  "run-on-your-computer": {
  "title": "Usa VortoOne sul tuo computer",
  "summary": "Usa il pacchetto di installazione e le istruzioni di connessione della versione attuale di VortoOne Desktop. Questa versione non offre un servizio cloud ospitato né un installer del server con un solo comando.",
  "sections": []
},
  "workspace-computer": {
  "title": "Il computer dello spazio di lavoro",
  "summary": "Usa il pacchetto di installazione e le istruzioni di connessione della versione attuale di VortoOne Desktop. Questa versione non offre un servizio cloud ospitato né un installer del server con un solo comando.",
  "sections": []
},
  computers: {
    title: "I tuoi computer e telefoni",
    summary:
      "Porta un Mac, un PC, un computer Linux, un server o un telefono, e i tuoi agenti ci lavorano. Su VortoOne Cloud come su un server tutto tuo.",
    sections: [
      {
        id: "app",
        heading: "Dall’app VortoOne",
        blocks: [
          "Installa l’app sul computer, aprila e accedi al tuo spazio di lavoro. Ti chiede **Lasciare che gli agenti di <workspace> usino questo Mac?** (questo PC su Windows, questo computer su Linux). Scegli chi può usarlo e premi **Consenti**. Tutto qui.",
          "Hai già fatto l’accesso? Apri **Preferenze\u00a0›\u00a0Generali** e premi **Consenti** nella riga dello spazio di lavoro, nella prima scheda. Non c’è? Premi **Aggiungi uno spazio di lavoro…** in fondo a quella scheda e accedi.",
        ],
      },
      {
        id: "web",
        heading: "Dal sito",
        blocks: [
          "In **Impostazioni › Dispositivi**, premi **Collega un dispositivo**:",
          {
            list: [
              "**Questo Mac** o **Questo PC**: apre l’app, che prima ti chiede conferma.",
              "**Un altro computer**: installa l’app lì, accedi con il tuo account e premi **Consenti**. Compare qui da solo.",
              "**Un server o VPS**: esegui una riga nel suo terminale, poi fallo entrare. La riga funziona una volta, per 10 minuti.",
            ],
          },
          "L’app si collega con un clic solo a un indirizzo con https, come uno di Tailscale che finisce con `.ts.net`. Con un semplice indirizzo http, usa la riga per il terminale.",
        ],
      },
      {
        id: "phones",
        heading: "Telefoni",
        blocks: [
          "Un telefono Android si collega tramite qualsiasi computer già collegato: con il cavo, o via Wi-Fi da Android 14 in poi con l’aggiornamento di sicurezza di maggio 2026. Un iPhone si collega tramite un Mac con l’app e Xcode, e al resto pensa l’app. Entrambi partono da **Collega un dispositivo**.",
        ],
      },
      {
        id: "who",
        heading: "Chi può usarlo",
        blocks: [
          "Un nuovo dispositivo è solo per te: **Solo io** vuol dire tu e gli agenti che lavorano per te. Aprilo a tutti nello spazio di lavoro, o ad alcune persone e ruoli, quando lo consenti o più tardi nella sua pagina in **Impostazioni › Dispositivi**.",
        ],
      },
      {
        id: "open",
        heading: "Mentre l’app è aperta",
        blocks: [
          "Gli agenti lavorano su un computer solo mentre l’app gira lì e il computer è sveglio. Quando l’app lo chiede, lascia spuntato **Tienilo pronto: apri VortoOne all'accesso**. Un computer spento o in stop mostra **Offline**, e gli agenti aspettano che torni.",
        ],
      },
      {
        id: "pausing",
        heading: "Mettere in pausa e scollegare",
        blocks: [
          "Sul computer, l’icona di VortoOne vicino all’orologio mette in pausa gli agenti di tutti gli spazi di lavoro, o di uno solo. In **Preferenze\u00a0›\u00a0Generali**, l’interruttore di ogni spazio di lavoro mette in pausa solo quello, e **Esci**, nel suo menu **⋯**, lo scollega. In pausa non parte niente di nuovo, e quello che era in corso finisce. Scollegando, le cartelle degli agenti restano sul disco.",
          "Dal sito, **Rimuovi** nella pagina del dispositivo lo scollega.",
        ],
      },
      {
        id: "organizations",
        heading: "Più spazi di lavoro, un solo computer",
        blocks: [
          "Accedi a ogni spazio di lavoro nella stessa app e premi **Consenti** per ognuno. Spazi di lavoro su VortoOne Cloud e sui tuoi server possono condividere un computer (app 0.6.1 e successive). Ognuno ha lì i suoi agenti e le sue cartelle, e scollegarne uno non tocca gli altri.",
          "Un server che l’app fa girare su questo computer è già collegato e non serve altro. Per prestare quel computer anche a uno spazio di lavoro altrove, vedi [Prestalo anche a un altro spazio di lavoro](/help/run-on-your-computer#another-workspace).",
          "Usi l’app su un portatile solo come finestra sul tuo spazio di lavoro? Quando chiede di lasciare che gli agenti usino il portatile, **Non ora** la lascia una semplice finestra.",
          "Un server connesso con la riga del terminale lavora per un solo workspace. Connetterlo da un altro workspace passa il server a quello, e il primo non lo raggiunge più. Per più workspace su un computer, usa l'app.",
        ],
      },
      {
        id: "line",
        heading: "Quando gli agenti aspettano in coda",
        blocks: [
          "Ogni lavoro di un agente usa la memoria del computer. Quando la memoria libera scarseggia, il successivo aspetta in coda finché un altro non finisce. Le persone passano per prime. Il numero di CPU non cambia nulla.",
          "Un computer con 4 GB di memoria esegue due o tre lavori alla volta, uno con 8 GB circa nove. Un Mac o un PC ne esegue fino a otto. Anche i browser e i desktop che gli agenti lasciano aperti usano memoria. Per una coda più corta, usa un computer con più memoria, oppure connettine un altro e dividi gli agenti tra i due. I workspace che condividono un computer ne condividono la memoria.",
        ],
      },
      {
        id: "folders",
        heading: "Cartelle",
        blocks: [
          "Ogni agente ha sempre una cartella sua, in `~/agentes/<workspace>`. Altre cartelle si autorizzano sul computer stesso, mai dal sito: nell’app, in **Preferenze › Cartelle**, oppure con `agentes-runner folders allow` su un server. Ogni spazio di lavoro autorizza le sue cartelle: quello che autorizzi per uno vale solo per i suoi agenti. Rimuovere una cartella non elimina nulla. Toglie solo l’accesso.",
        ],
      },
      {
        id: "reach",
        heading: "Cosa possono raggiungere gli agenti",
        blocks: [
          "Gli agenti girano come te, l’utente del computer. I loro strumenti per i file restano nelle loro cartelle, ma un comando può leggere tutto quello che puoi leggere tu:",
          {
            list: [
              "i tuoi file e i tuoi accessi",
              "le cartelle degli altri agenti, anche di altri spazi di lavoro, e gli accessi salvati nei loro browser",
              "il token che collega questo computer",
              "su un computer che fa girare anche VortoOne, il suo database e le sue chiavi in `.agentes`",
            ],
          },
          "VortoOne rifiuta i comandi che nominano le sue chiavi, i token e gli accessi salvati, ma è una regola, non un muro. I **Comandi** sono attivi fin dall’inizio. Disattivali nella domanda dell’app prima di premere **Consenti**, o più tardi in **Preferenze\u00a0›\u00a0Strumenti**, dove ogni spazio di lavoro ha il suo interruttore (app 0.8.1 e successive) e l’app chiede conferma prima di riattivarne uno. Claude Code e Codex eseguono i propri comandi nella cartella dell’agente, con le proprie regole, anche con i comandi disattivati.",
          "Per un vero muro, usa un server Linux. Su Debian 13, installato con `--system`, ogni agente è un utente a sé. Su Ubuntu, gli agenti condividono un solo utente, `agentes`: vedono le cartelle degli altri, non le tue né quelle del server. Oppure dai agli agenti un computer tutto loro.",
        ],
      },
    ],
  },
  import: {
    title: "Porta i tuoi agenti",
    summary:
      "Porta qui agenti da un’altra app, o da un altro spazio di lavoro VortoOne, con istruzioni, skill, routine e memoria.",
    sections: [
      {
        id: "comes",
        heading: "Cosa arriva",
        blocks: [
          {
            list: [
              "Gli agenti, con nome, ruolo, istruzioni e aspetto.",
              "Le skill, attive per gli agenti che le avevano.",
              "I canali pubblici, con i loro agenti.",
              "Le routine, attive e con i loro orari. Se vuoi, mettile in pausa in **Routine**.",
              "La memoria: quello che ricorda ogni agente.",
              "I file di ogni agente (la cartella dei file di OpenBot.run, uno spazio di lavoro OpenClaw), in una cartella con il suo nome nel **Drive**. Chiavi, accessi e file di sistema restano fuori, e niente di quello che c’è già viene sostituito.",
            ],
          },
        ],
      },
      {
        id: "never",
        heading: "Cosa non arriva mai",
        blocks: [
          "Chiavi, password, accessi e cronologia delle chat non arrivano mai: gli agenti ragionano con l’IA dello spazio di lavoro. Alcune cose qui non hanno ancora un equivalente, o non dovrebbero mai viaggiare in un file. Puoi ricrearle dopo.",
          "File e cartelle vengono letti nel tuo browser. Un link GitHub passa dal server dello spazio di lavoro. I file di chiavi e accessi non vengono mai aperti, e non cambia nulla finché non confermi.",
        ],
      },
      {
        id: "mcp",
        heading: "Connetti con MCP",
        blocks: [
          "MCP permette alla tua app di IA di accedere a questo spazio di lavoro, così crea gli agenti qui da sola, con le loro istruzioni, skill e routine. Scegli la tua app, seleziona **Connetti con MCP** e poi:",
          {
            list: [
              "Collega l’indirizzo che mostra il passaggio, con i passaggi dell’app stessa. [Come collegarla, app per app](/help/apps-de-ia#connect)",
              "Nella pagina di accesso, scegli **Creare e modificare agenti, routine e skill**.",
              "Incolla la richiesta nell’app. Gli agenti compaiono nel passaggio man mano che arrivano.",
            ],
          },
          "Se un’app non si collega, scegli **Da un file** o **Solo la richiesta**, a seconda di cosa mostra il passaggio. [Da un’altra app](/help/import#other-apps) spiega la richiesta.",
        ],
      },
      {
        id: "other-apps",
        heading: "Da un’altra app",
        blocks: [
          "Scegli **Grok Bot**, **ChatGPT dots** o **Un’altra app**. Copia la richiesta, incollala in quell’app, poi riporta qui quello che restituisce: trascina il file, o incolla tutta la risposta. Dove la mette ogni app:",
          {
            list: [
              "**Grok Bot**: nella chat con il tuo bot. Risponde con il JSON: copia tutta la risposta.",
              "**ChatGPT dots**: in una nuova chat. Scarica il file che crea.",
              "**Claude**: in una nuova conversazione, o nel progetto dei tuoi agenti. Scarica il file che crea.",
              "**Manus**: in una nuova attività. Il file è nei risultati dell’attività.",
              "**Rakazo**: mandala al tuo agente. Risponde con il JSON: copia tutta la risposta.",
              "**OpenBot**: nessuna richiesta necessaria. Trascina la cartella del tenant (agents.yaml, agents/, model.yaml, skills.yaml) o incolla il suo link GitHub.",
            ],
          },
        ],
      },
      {
        id: "export",
        heading: "Esporta uno spazio di lavoro",
        blocks: [
          "**Impostazioni › Importa ed esporta › Esporta** scarica un file .json, per portare i tuoi agenti in un altro spazio di lavoro o per tenerne una copia. Nel file:",
          {
            list: [
              "Gli agenti, con istruzioni, modello e aspetto",
              "Le skill, con tutto lo SKILL.md",
              "I canali pubblici, con i loro agenti",
              "Le routine programmate (dall’altra parte arrivano attive)",
              "La memoria che tutti nello spazio di lavoro possono vedere",
            ],
          },
          "Mai nel file:",
          {
            list: [
              "Chiavi API e password",
              "Connettori e accessi",
              "Computer",
              "Conversazioni, messaggi e canali privati",
              "Utilizzo, costi e cronologia delle attività",
            ],
          },
          "Per usare il file, apri **Impostazioni › Importa ed esporta** nell’altro spazio di lavoro e trascina lì il .json.",
        ],
      },
    ],
  },
  wiki: {
    title: "Il wiki",
    summary: "Quello che l’azienda sa, in pagine collegate tra loro.",
    sections: [
      {
        id: "links",
        heading: "Link",
        blocks: [
          "Scrivi [[ per collegare un’altra pagina. I backlink e il grafo mostrano come si collegano le pagine.",
        ],
      },
      {
        id: "agents",
        heading: "Come lo usano gli agenti",
        blocks: ["Gli agenti leggono il wiki quando rispondono. Ci scrivono solo quando qualcuno lo chiede."],
      },
      {
        id: "obsidian",
        heading: "Porta un vault di Obsidian",
        blocks: [
          "Trascina la cartella del vault, o un suo .zip, nel passo Contesto della configurazione o nell’importazione del wiki. [[Link]], [[nota|alias]] e [[nota#sezione]] continuano a funzionare. I tag vengono dal frontmatter e dal testo. Gli allegati vanno nel Drive, e immagini e file incorporati diventano schede nella pagina. I callout restano callout. Le query di Dataview arrivano come testo: chiedi a un agente di costruire la tabella. I canvas e le impostazioni di Obsidian (.obsidian) restano fuori.",
        ],
      },
    ],
  },
  memory: {
    title: "Memoria",
    summary: "Quello che ogni agente impara dalle conversazioni e dalle correzioni del team.",
    sections: [
      {
        id: "learns",
        heading: "Come imparano gli agenti",
        blocks: [
          "Gli agenti imparano dalle conversazioni e dalle correzioni del team. Quello che un agente sa entra nel suo contesto quando serve. Nella sua scheda **Memoria**, modifica, fissa o archivia quello che è sbagliato o superato.",
        ],
      },
      {
        id: "permissions",
        heading: "La memoria non dà permessi",
        blocks: [
          "Niente nella memoria permette a un agente di fare di più. Quello che può fare resta in **Strumenti e accesso**.",
        ],
      },
      {
        id: "proposals",
        heading: "Modifiche proposte",
        blocks: [
          "Quando una lezione si ripete, l’agente può proporre di cambiare le sue istruzioni o il contesto dello spazio di lavoro. Non cambia nulla finché qualcuno non accetta, e prima si può ritoccare il testo. La versione precedente resta nella cronologia. Una proposta rifiutata resta come lezione, così non viene riproposta.",
        ],
      },
    ],
  },
  approvals: {
    title: "Approvazioni",
    summary: "Le azioni delicate aspettano una persona, una volta o per sempre.",
    sections: [
      {
        id: "what",
        heading: "Cosa chiede prima",
        blocks: [
          "Quando un agente vuole fare un merge, un deploy, un rollback, mandare qualcosa all’esterno o eliminare dati, chiede prima. È il livello **Solo ciò che è rischioso**, quello da cui parte ogni agente. Nella pagina dell’agente, in **Approvazioni**, **Chiedere sempre** gli fa chiedere prima di cambiare qualsiasi cosa, e con **Non chiedere mai** non chiede nulla, nemmeno per merge, deploy, pagamenti e routine. La richiesta compare in **Approvazioni** e nella conversazione, con l’azione esatta e il motivo.",
        ],
      },
      {
        id: "always",
        heading: "Una volta, o sempre",
        blocks: [
          "Approvare copre un’azione esatta, con i suoi valori. Se l’agente cambia qualcosa, chiede di nuovo.",
          "**Approva sempre** lascia che l’agente faccia quel tipo di azione da ora in poi, con qualsiasi valore, senza chiedere. Nella richiesta di una routine, **Sempre in questa routine** fa lo stesso solo per quella routine. **Approva sempre** compare per qualsiasi tipo di azione, anche merge e deploy, finché l’agente è al livello **Solo ciò che è rischioso**. **Sempre in questa routine** non compare per merge, deploy, rollback e simili. Per annullare, vai in **Strumenti e accesso** nella pagina dell’agente, o nelle impostazioni della routine.",
        ],
      },
      {
        id: "who",
        heading: "Chi approva",
        blocks: [
          {
            list: [
              "Il proprietario dell’agente, per le sue richieste.",
              "Chiunque abbia un ruolo con **Approvare le azioni degli agenti**, per tutti gli agenti.",
              "Le persone che l’agente nomina quando chiede, al posto del proprietario.",
              "Chi copre qualcuno in **Chi approva per me**, per ciò che viene chiesto a quella persona. Merge, deploy e altre azioni a rischio solo quando è attivo.",
            ],
          },
          "Un’azione nella casella di posta di qualcuno aspetta solo quella persona. Un nuovo accesso per un agente aspetta chi gestisce gli agenti.",
          "Lodi, il tuo braccio destro, può anche approvare per te i nuovi agenti proposti da altri agenti e le modifiche a modello, budget e canali di un agente, solo quando l’hai chiesto tu. Chi ti copre e cosa approva Lodi si scelgono in **Impostazioni\u00a0›\u00a0Tu\u00a0›\u00a0Approvazioni**.",
        ],
      },
      {
        id: "refusing",
        heading: "Rifiutare",
        blocks: ["Se vuoi, spiega il motivo: l’agente lo legge, cambia piano e lo tiene come lezione."],
      },
      {
        id: "meetings",
        heading: "Riunioni",
        blocks: [
          "Gli agenti suggeriscono solo gli orari. Gli inviti alle persone passano sempre da chi li ha chiesti.",
        ],
      },
    ],
  },
  "who-answers": {
    title: "Chi risponde a un messaggio",
    summary: "Una menzione sceglie l’agente. Senza menzione, VortoOne sceglie per te, in questo ordine.",
    sections: [
      {
        id: "order",
        heading: "Come un messaggio trova il suo agente",
        blocks: [
          "Un messaggio che menziona un agente con @ va a quell’agente. Un messaggio che non menziona nessuno viene letto dal primo di questi che lo spazio di lavoro ha:",
          {
            list: [
              "**Jev**, un modello piccolo e veloce fatto per decisioni come questa.",
              "**Un modello piccolo su una delle chiavi API dello spazio di lavoro**, come Claude Haiku 5.5 o GPT-6 Luna.",
              "**Il tuo piano Claude o ChatGPT**, per i messaggi che scrivi tu, su un server che gestisci tu. Li legge sul computer dove hai fatto l’accesso, e ci mette qualche secondo.",
              "**Regole semplici**: una risposta a un agente va a lui, e così un seguito subito dopo che ci hai parlato, o un messaggio in un canale dove sei solo con un agente.",
            ],
          },
          "Quando nessuno di questi va bene, non risponde nessun agente. Menziona un agente per chiamarlo.",
        ],
      },
      {
        id: "jev",
        heading: "Jev",
        blocks: [
          "Quando è VortoOne a ospitare il tuo spazio di lavoro, Jev è incluso. Su un server che gestisci tu, usa la chiave TypeSafe del proprietario, circa 0,0001 US$ a messaggio: [Configura Jev](/help/jev#setup).",
        ],
      },
      {
        id: "plans",
        heading: "Perché il tuo piano sceglie solo per i tuoi messaggi",
        blocks: [
          "I piani Claude e ChatGPT sono personali, quindi il tuo piano sceglie solo per i messaggi che scrivi tu.",
        ],
      },
      {
        id: "see",
        heading: "Vedere chi ha scelto",
        blocks: ["Apri la spunta sotto un messaggio. L’ultima riga dice chi ha scelto gli agenti."],
      },
    ],
  },
  jev: {
    title: "Jev",
    summary:
      "Un modello veloce di TypeSafe che prende decisioni rapide per i tuoi agenti. Sul tuo server è facoltativo, con una chiave tua.",
    sections: [
      {
        id: "what",
        heading: "Cosa fa",
        blocks: [
          {
            list: [
              "**Sceglie chi risponde.** Un messaggio senza menzioni arriva all’agente giusto in meno di un secondo, anche quando i tuoi agenti ragionano con un piano e non hai una chiave API.",
              "**Tiene in ordine la memoria.** Riconosce un doppione o un fatto più recente anche se detto con altre parole: il doppione si unisce a quello che l’agente sapeva, e il fatto più recente sostituisce quello vecchio. Una volta al giorno sistema il resto.",
              "**Vede lo schermo.** Nel loro browser e desktop, gli agenti trovano pulsanti e campi da una descrizione, guardano più da vicino quelli piccoli e controllano sull’immagine dello schermo che un passo sia riuscito.",
              "**Legge meno.** Ogni turno carica solo gli strumenti che probabilmente serviranno all’agente, così la tua IA legge meno e costa meno.",
            ],
          },
        ],
      },
      {
        id: "without",
        heading: "Senza Jev",
        blocks: [
          "Tutto continua a funzionare, più lentamente e con meno cura.",
          {
            list: [
              "**Chi risponde:** sceglie un modello piccolo su una delle tue chiavi API, come Claude Haiku 5.5 o GPT-6 Luna. Senza chiave, il piano Claude o ChatGPT di ogni persona sceglie per i messaggi che scrive, in qualche secondo. Altrimenti una risposta o un seguito arriva comunque al suo agente. Menziona un agente per chiamarlo. [Come vengono scelti gli agenti](/help/who-answers)",
              "**Memoria:** i doppioni con le stesse parole si uniscono comunque. Doppioni e aggiornamenti con parole diverse restano separati.",
              "**Schermo:** gli agenti usano i numeri mostrati sullo schermo e i nomi esatti. Dalle immagini non si legge nulla.",
            ],
          },
        ],
      },
      {
        id: "setup",
        heading: "Configuralo",
        blocks: [
          {
            list: [
              "Apri https://console.typesafe.ai e accedi con Google o con la tua email. Un nuovo account si crea allo stesso modo.",
              "Aggiungi credito nella console. Jev è prepagato.",
              "Apri https://console.typesafe.ai/keys, crea una chiave e copiala subito.",
              "In VortoOne, apri **Impostazioni › IA e consumi** e incollala sotto **Jev**. La vede solo chi ha configurato questo server.",
            ],
          },
          "Su un server che gestisci con Docker, puoi invece mettere la chiave in `.env` come `TYPESAFE_API_KEY`. Questa ha la precedenza su quella in Impostazioni.",
        ],
      },
      {
        id: "cost",
        heading: "Quanto costa",
        blocks: [
          {
            list: [
              "0,042 US$ per milione di token letti da Jev. Le sue risposte sono gratuite. Scegliere chi risponde legge circa 2.500 token, quindi circa 0,0001 US$. Uno screenshot è circa 3.900 token.",
              "Il credito che acquisti dura 12 mesi. Attiva le ricariche automatiche nella console così Jev non resta a secco.",
              "**Impostazioni › IA e consumi** mostra quante decisioni ha preso Jev questo mese, e quanto sono costate.",
            ],
          },
        ],
      },
      {
        id: "data",
        heading: "I tuoi dati",
        blocks: [
          "Cosa legge Jev per decidere:",
          {
            list: [
              "Il nome e la descrizione del canale, il messaggio e i pochi precedenti",
              "I nomi e i ruoli dei tuoi agenti, l’inizio delle loro istruzioni e gli strumenti che possono usare",
              "Gli elementi di memoria che confronta",
              "L’attività di un agente e l’inizio dei risultati dei suoi strumenti",
              "L’obiettivo di una missione e i comandi che un agente vuole eseguire per portarla avanti",
              "Gli strumenti offerti da un’app collegata",
              "La pagina o lo schermo che un agente sta usando, screenshot inclusi",
            ],
          },
          "TypeSafe fa girare Jev negli Stati Uniti e non addestra i suoi modelli su quello che invii. Conserva i dati per il tempo necessario a far funzionare il servizio. La conservazione zero è disponibile nei suoi piani enterprise.",
          "Informativa sulla privacy di TypeSafe: https://typesafe.ai/legal/privacy-policy. Il suo accordo sul trattamento dei dati: https://typesafe.ai/legal/data-processing.",
        ],
      },
      {
        id: "problems",
        heading: "Quando si ferma",
        blocks: [
          {
            list: [
              "**Chiave non accettata:** crea una nuova chiave e incollala in **Impostazioni › IA e consumi**.",
              "**Credito esaurito:** aggiungi credito nella console. Jev riprova entro 30 minuti, oppure fai clic su **Controlla di nuovo**.",
              "**Occupato o irraggiungibile:** TypeSafe può limitare le richieste al secondo, o smettere di rispondere per un po’. Jev riparte da solo. I tuoi limiti: https://console.typesafe.ai/rate-limits. Per limiti più alti, scrivi a sales@typesafe.ai.",
            ],
          },
          "Nel frattempo, gli agenti lavorano come in [Senza Jev](/help/jev#without).",
        ],
      },
    ],
  },
  "apps-de-ia": {
    title: "App IA",
    summary:
      "Chiedi attività agli agenti di VortoOne da Claude, Cursor o un’altra app MCP, senza uscirne, e ricevi la risposta lì.",
    sections: [
      {
        id: "what",
        heading: "Come funziona",
        blocks: [
          "VortoOne è un server MCP. Incolli il suo indirizzo nella tua app IA, accedi con il tuo account e scegli uno spazio di lavoro. Da lì in poi, l’app può elencare agenti e canali, mandare un’attività a un agente a nome tuo, aspettare la risposta e leggere le attività recenti.",
          "Per esempio, nel mezzo di una scena in Unity o Blender, chiedi a Claude: “Chiedi all’agente marketing un testo per il lancio”. Il messaggio compare in VortoOne come tuo, con l’etichetta **via Claude**, e la risposta dell’agente torna a Claude.",
          "L’indirizzo è in **Impostazioni › App IA**. Finisce con `/mcp`.",
        ],
      },
      {
        id: "connect",
        heading: "Collega un’app",
        blocks: [
          {
            list: [
              "**Claude** (claude.ai, Claude Desktop e telefono): in **Personalizza › Connettori** (Customize › Connectors), fai clic su **+ Aggiungi**, poi su **Aggiungi connettore personalizzato** (Add custom connector), chiamalo VortoOne, incolla l’indirizzo e fai clic su **Continua**. Scegli **Accedi ora** (Sign in now), fai clic su **Aggiungi** e autorizza. Nei piani Team ed Enterprise, il Proprietario dell’organizzazione Claude aggiunge il connettore in Impostazioni dell’organizzazione › Connettori, e ogni persona fa clic su **Connetti** in Personalizza › Connettori. Il piano gratuito di Claude ha posto per un connettore personalizzato.",
              "**Claude Code:** esegui `claude mcp add --transport http --scope user agentes <indirizzo>`, poi scrivi `/mcp`, scegli agentes e **Authenticate**. Solo per la cartella attuale, togli `--scope user`.",
              "**Cursor:** **Aggiungi a Cursor** in **Impostazioni › App IA**, oppure l’indirizzo in `~/.cursor/mcp.json`. Cursor ti chiede di accedere la prima volta che lo usi.",
              '**VS Code:** **Aggiungi a VS Code**, oppure l’indirizzo in `.vscode/mcp.json` con `"type": "http"`.',
              "**Altre app:** ChatGPT, Gemini, Grok, Perplexity, Codex, Zed, JetBrains e altre hanno ciascuna i propri passaggi in **Collega un'app**, con il tuo indirizzo già inserito.",
            ],
          },
          "Quando accedi, VortoOne mostra quale app ha chiesto l’accesso e dove torna. Continua solo se hai appena avviato tu il collegamento.",
        ],
      },
      {
        id: "tools",
        heading: "Cosa può fare l’app",
        blocks: [
          {
            list: [
              "`send_to_agent`: manda un tuo messaggio a un agente, nella tua conversazione privata con lui o in un canale, e aspetta la risposta. Solo con l’accesso **Leggere e mandare attività agli agenti**.",
              "`wait_for_reply`: continua ad aspettare la risposta a un’attività.",
              "`list_agents`: gli agenti che vedi.",
              "`list_channels`: i canali che leggi.",
              "`read_conversation`: i messaggi recenti di un canale, di un thread o di un’attività.",
              "`list_recent_tasks`: le attività che hai mandato dalle app e quello che gli agenti stanno facendo ora.",
            ],
          },
          "L’app agisce a nome tuo e vede solo quello che vedi tu. Non approva mai le azioni degli agenti: quando un agente si ferma per chiedere un’approvazione, la risposta lo dice con un link, e una persona decide in VortoOne. Non cambia nemmeno impostazioni, membri o permessi.",
        ],
      },
      {
        id: "workspace",
        heading: "Uno spazio di lavoro per collegamento",
        blocks: [
          "Ogni collegamento copre lo spazio di lavoro scelto al momento dell’accesso. Per usare un altro spazio di lavoro, collega di nuovo l’app e scegli l’altro.",
          "In **Impostazioni › App IA** vedi le app collegate, quando è stata usata ognuna, e puoi scollegarne qualsiasi. Chi gestisce i membri vede le app di tutto lo spazio di lavoro e può scollegarle anche lui. Ogni messaggio mandato da un’app compare nell’Audit.",
        ],
      },
      {
        id: "tokens",
        heading: "Token personali",
        blocks: [
          "Per script e app senza accesso OAuth, crea un token personale in **Impostazioni › App IA** e mandalo nell’intestazione `Authorization: Bearer`. Il token si vede una volta sola, funziona solo nello spazio di lavoro in cui è stato creato e può scadere dopo 30 giorni, 90 giorni, 1 anno o mai.",
          "Su un server senza https, lontano dal suo computer, usa un token personale: l’accesso OAuth ha bisogno di https.",
        ],
      },
      {
        id: "limits",
        heading: "Limiti",
        blocks: [
          {
            list: [
              "Fino a 120 richieste al minuto e 5.000 al giorno per collegamento.",
              "Fino a 20 messaggi al minuto e 500 al giorno per persona in ogni spazio di lavoro.",
              "Un’attesa dura fino a 45 secondi. Se l’agente sta ancora lavorando, l’app aspetta di nuovo.",
            ],
          },
        ],
      },
      {
        id: "safety",
        heading: "Testo scritto da altri",
        blocks: [
          "Le risposte contengono testo scritto da agenti e persone in VortoOne. L’app riceve quel testo segnato come contenuto, non come istruzioni, ma un testo può comunque provare a convincere l’app a fare altro. Controlla cosa sta per fare la tua app prima di confermare.",
        ],
      },
      {
        id: "problems",
        heading: "Quando non si collega",
        blocks: [
          {
            list: [
              "**Richiesta scaduta:** la pagina di accesso dura 30 minuti e funziona una volta sola. Torna all’app e collegati di nuovo.",
              "**L’app chiede di accedere di nuovo:** il collegamento è stato rimosso, la password è cambiata o sei uscito dallo spazio di lavoro. Accedi di nuovo dall’app.",
              "**Cursor bloccato su un vecchio accesso:** chiudi Cursor, elimina la cartella `~/.mcp-auth` e collegati di nuovo.",
            ],
          },
        ],
      },
    ],
  },
  "install-on-a-server": {
  "title": "Installa VortoOne su un server",
  "summary": "Usa il pacchetto di installazione e le istruzioni di connessione della versione attuale di VortoOne Desktop. Questa versione non offre un servizio cloud ospitato né un installer del server con un solo comando.",
  "sections": []
},
};
