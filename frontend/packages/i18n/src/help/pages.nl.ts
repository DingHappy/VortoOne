import type { HelpSlug } from "./index";
import type { HelpDocument } from "./types";

/** The help pages in nl: a page not here shows in English. */
export const helpNl: Partial<Record<HelpSlug, HelpDocument>> = {
  "run-on-your-computer": {
  "title": "VortoOne op je eigen computer draaien",
  "summary": "Gebruik het installatiepakket en de verbindingsinstructies van de huidige VortoOne Desktop-versie. Deze versie biedt geen gehoste clouddienst of serverinstallatie met één opdracht.",
  "sections": []
},
  "workspace-computer": {
  "title": "De computer van de werkruimte",
  "summary": "Gebruik het installatiepakket en de verbindingsinstructies van de huidige VortoOne Desktop-versie. Deze versie biedt geen gehoste clouddienst of serverinstallatie met één opdracht.",
  "sections": []
},
  computers: {
    title: "Je computers en telefoons",
    summary:
      "Breng een Mac, een pc, een Linux-computer, een server of een telefoon mee, en je agents werken erop. Op VortoOne Cloud net zo goed als op een eigen server.",
    sections: [
      {
        id: "app",
        heading: "Vanuit de VortoOne-app",
        blocks: [
          "Installeer de app op de computer, open hem en meld je aan bij je werkruimte. Hij vraagt **Agents van <workspace> deze Mac laten gebruiken?** (deze pc op Windows, deze computer op Linux). Kies wie hem mag gebruiken en druk op **Toestaan**. Dat is alles.",
          "Al aangemeld? Open **Voorkeuren\u00a0›\u00a0Algemeen** en druk op **Toestaan** in de rij van de werkruimte, in de eerste kaart. Staat hij er niet? Druk op **Werkruimte toevoegen…** onderaan die kaart en meld je aan.",
        ],
      },
      {
        id: "web",
        heading: "Vanaf de website",
        blocks: [
          "Druk in **Instellingen › Apparaten** op **Apparaat koppelen**:",
          {
            list: [
              "**Deze Mac** of **Deze pc**: opent de app, die het je eerst vraagt.",
              "**Een andere computer**: installeer daar de app, meld je aan met je eigen account en druk op **Toestaan**. Hij verschijnt hier vanzelf.",
              "**Een server of VPS**: voer één regel uit in de terminal ervan en laat hem dan toe. De regel werkt één keer, 10 minuten lang.",
            ],
          },
          "De app maakt met één klik alleen verbinding met een adres met https, zoals een Tailscale-adres dat eindigt op `.ts.net`. Bij een gewoon http-adres gebruik je de regel voor de terminal.",
        ],
      },
      {
        id: "phones",
        heading: "Telefoons",
        blocks: [
          "Een Android-telefoon maakt verbinding via elke computer die al gekoppeld is: met een kabel, of via wifi vanaf Android 14 met de beveiligingsupdate van mei 2026. Een iPhone maakt verbinding via een Mac met de app en Xcode, en de app regelt de rest. Allebei begin je bij **Apparaat koppelen**.",
        ],
      },
      {
        id: "who",
        heading: "Wie hem mag gebruiken",
        blocks: [
          "Een nieuw apparaat is alleen voor jou: **Alleen ik** betekent jij en de agents die voor jou werken. Open het voor iedereen in de werkruimte, of voor bepaalde mensen en rollen, als je het toestaat of later op de pagina ervan in **Instellingen › Apparaten**.",
        ],
      },
      {
        id: "open",
        heading: "Zolang de app open is",
        blocks: [
          "Agents werken alleen op een computer zolang de app daar draait en de computer wakker is. Als de app het vraagt, laat **Klaar houden: VortoOne openen bij inloggen** aangevinkt. Een computer die uit staat of slaapt, toont **Offline**, en agents wachten tot hij terug is.",
        ],
      },
      {
        id: "pausing",
        heading: "Pauzeren en loskoppelen",
        blocks: [
          "Op de computer pauzeert het VortoOne-icoon bij de klok de agents van alle werkruimtes, of van één. In **Voorkeuren\u00a0›\u00a0Algemeen** pauzeert de schakelaar van elke werkruimte alleen die ene, en **Verlaten**, in het **⋯**-menu ervan, koppelt hem los. Tijdens een pauze start er niets nieuws, en wat al liep, maakt het af. Na het loskoppelen blijven de mappen van de agents op de schijf staan.",
          "Op de website koppel je een apparaat los met **Verwijderen** op de pagina ervan.",
        ],
      },
      {
        id: "organizations",
        heading: "Meerdere werkruimtes, één computer",
        blocks: [
          "Meld je in dezelfde app aan bij elke werkruimte en druk voor elk op **Toestaan**. Werkruimtes op VortoOne Cloud en op je eigen servers kunnen een computer delen (app 0.6.1 en nieuwer). Elk heeft er eigen agents en mappen, en als je er een loskoppelt, blijven de andere ongemoeid.",
          "Een server die de app op deze computer draait, is al gekoppeld en heeft niets nodig. Om die computer ook uit te lenen aan een werkruimte ergens anders, zie [Leen hem ook uit aan een andere werkruimte](/help/run-on-your-computer#another-workspace).",
          "Gebruik je de app op een laptop alleen als venster op je werkruimte? Als hij vraagt of agents de laptop mogen gebruiken, houdt **Niet nu** het bij een gewoon venster.",
          "Een server die met de terminalregel is verbonden, werkt voor één workspace. Verbind je hem vanuit een andere workspace, dan gaat de server naar die workspace en bereikt de eerste hem niet meer. Gebruik de app voor meerdere workspaces op één computer.",
        ],
      },
      {
        id: "line",
        heading: "Als agenten in de rij wachten",
        blocks: [
          "Elk werk van een agent gebruikt geheugen van de computer. Als er weinig vrij geheugen is, wacht het volgende in de rij tot een ander klaar is. Mensen gaan voor. Het aantal CPU's verandert dit niet.",
          "Een computer met 4 GB geheugen draait twee of drie tegelijk, een met 8 GB ongeveer negen. Een Mac of pc draait er tot acht. Browsers en bureaubladen die agenten open laten, gebruiken ook geheugen. Voor een kortere rij gebruik je een computer met meer geheugen, of verbind je er nog een en verdeel je de agenten over beide. Workspaces die een computer delen, delen het geheugen ervan.",
        ],
      },
      {
        id: "folders",
        heading: "Mappen",
        blocks: [
          "Elke agent heeft altijd een eigen map, in `~/vortoone/<workspace>`. Meer mappen geef je vrij op de computer zelf, nooit via de website: in de app via **Voorkeuren › Mappen**, of met `vortoone-runner folders allow` op een server. Elke werkruimte geeft zijn eigen mappen vrij: wat je voor de ene vrijgeeft, geldt alleen voor de agents daarvan. Een map weghalen verwijdert niets. Het neemt alleen de toegang weg.",
        ],
      },
      {
        id: "reach",
        heading: "Waar agents bij kunnen",
        blocks: [
          "Agents draaien als jij, de gebruiker van de computer. Hun bestandstools blijven in hun mappen, maar een opdracht kan alles lezen wat jij kunt lezen:",
          {
            list: [
              "je bestanden en logins",
              "de mappen van andere agents, ook die van andere werkruimtes, en de logins die hun browsers bewaren",
              "het token dat deze computer koppelt",
              "op een computer die ook VortoOne draait, de database en sleutels ervan in `.vortoone`",
            ],
          },
          "VortoOne weigert opdrachten die zijn sleutels, tokens en aanmeldingen noemen, maar dat is een regel, geen muur. **Opdrachten** staan standaard aan. Zet ze uit in de vraag van de app voordat je op **Toestaan** drukt, of later in **Voorkeuren\u00a0›\u00a0Tools**, waar elke werkruimte een eigen schakelaar heeft (app 0.8.1 en nieuwer) en de app het eerst vraagt voordat er een weer aangaat. Claude Code en Codex voeren hun eigen opdrachten uit in de map van de agent, volgens hun eigen regels, ook als Opdrachten uit staan.",
          "Voor een echte muur gebruik je een Linux-server. Op Debian 13, geïnstalleerd met `--system`, is elke agent een eigen gebruiker. Op Ubuntu delen agents één gebruiker, `vortoone`: ze zien elkaars mappen, niet die van jou of van de server. Of geef de agents een eigen computer.",
        ],
      },
    ],
  },
  import: {
    title: "Je agents meenemen",
    summary:
      "Neem agents mee uit een andere app, of uit een andere VortoOne-werkruimte, met hun instructies, skills, routines en geheugen.",
    sections: [
      {
        id: "comes",
        heading: "Wat er meekomt",
        blocks: [
          {
            list: [
              "Agents, met hun naam, rol, instructies en uiterlijk.",
              "Skills, aan voor de agents die ze hadden.",
              "Openbare kanalen, met de agents erin.",
              "Routines, actief en volgens hun schema. Pauzeer ze in **Routines** als je wilt.",
              "Geheugen: wat elke agent onthoudt.",
              "De eigen bestanden van elke agent (de bestandenmap van OpenBot.run, een OpenClaw-werkruimte), in een map met zijn naam in de **Drive**. Sleutels, logins en systeembestanden blijven achter, en niets wat er al staat wordt vervangen.",
            ],
          },
        ],
      },
      {
        id: "never",
        heading: "Wat nooit meekomt",
        blocks: [
          "Sleutels, wachtwoorden, logins en chatgeschiedenis komen nooit mee: de agents denken met de AI van de werkruimte. Sommige dingen hebben hier nog geen tegenhanger, of horen nooit in een bestand te reizen. Je kunt ze later opnieuw aanmaken.",
          "Bestanden en mappen worden in je browser gelezen. Een GitHub-link gaat via de server van de werkruimte. Sleutel- en loginbestanden worden nooit geopend, en er verandert niets tot je bevestigt.",
        ],
      },
      {
        id: "mcp",
        heading: "Verbinden met MCP",
        blocks: [
          "Met MCP kan je AI-app bij deze werkruimte, zodat die de agents hier zelf aanmaakt, met hun instructies, skills en routines. Kies je app, selecteer **Verbinden met MCP** en dan:",
          {
            list: [
              "Koppel het adres dat de stap toont, met de stappen van de app zelf. [Zo koppel je het, per app](/help/apps-de-ia#connect)",
              "Kies op de inlogpagina **Agents, routines en skills maken en bewerken**.",
              "Plak het verzoek in de app. De agents verschijnen in de stap zodra ze binnenkomen.",
            ],
          },
          "Kan een app niet koppelen, kies dan **Uit een bestand** of **Alleen het verzoek**, afhankelijk van wat de stap toont. [Uit een andere app](/help/import#other-apps) legt het verzoek uit.",
        ],
      },
      {
        id: "other-apps",
        heading: "Uit een andere app",
        blocks: [
          "Kies **Grok Bot**, **ChatGPT dots** of **Een andere app**. Kopieer het verzoek, plak het in die app en breng terug wat die teruggeeft: sleep het bestand erin, of plak het hele antwoord. Waar elke app het neerzet:",
          {
            list: [
              "**Grok Bot**: in de chat met je bot. Hij antwoordt met de JSON: kopieer het hele antwoord.",
              "**ChatGPT dots**: in een nieuwe chat. Download het bestand dat hij maakt.",
              "**Claude**: in een nieuw gesprek, of in het project van je agents. Download het bestand dat hij maakt.",
              "**Manus**: in een nieuwe taak. Het bestand staat in de resultaten van de taak.",
              "**Rakazo**: stuur het naar je agent. Hij antwoordt met de JSON: kopieer het hele antwoord.",
              "**OpenBot**: geen verzoek nodig. Sleep de tenantmap (agents.yaml, agents/, model.yaml, skills.yaml) erin of plak de GitHub-link ervan.",
            ],
          },
        ],
      },
      {
        id: "export",
        heading: "Een werkruimte exporteren",
        blocks: [
          "**Instellingen › Importeren en exporteren › Exporteren** downloadt een .json-bestand, om je agents mee te nemen naar een andere werkruimte of om een kopie te bewaren. In het bestand:",
          {
            list: [
              "Agents, met hun instructies, model en uiterlijk",
              "Skills, met de hele SKILL.md",
              "Openbare kanalen, met hun agents",
              "Geplande routines (ze komen actief aan de andere kant aan)",
              "Het geheugen dat iedereen in de werkruimte kan zien",
            ],
          },
          "Nooit erin:",
          {
            list: [
              "API-sleutels en wachtwoorden",
              "Connectors en logins",
              "Computers",
              "Gesprekken, berichten en privékanalen",
              "Gebruik, kosten en activiteitengeschiedenis",
            ],
          },
          "Om het bestand te gebruiken, open je **Instellingen › Importeren en exporteren** in de andere werkruimte en sleep je de .json erin.",
        ],
      },
    ],
  },
  wiki: {
    title: "De wiki",
    summary: "Wat het bedrijf weet, in pagina's die naar elkaar linken.",
    sections: [
      {
        id: "links",
        heading: "Links",
        blocks: [
          "Typ [[ om naar een andere pagina te linken. Backlinks en de graaf laten zien hoe pagina's samenhangen.",
        ],
      },
      {
        id: "agents",
        heading: "Hoe agents hem gebruiken",
        blocks: ["Agents lezen de wiki als ze antwoorden. Ze schrijven er alleen in als iemand daarom vraagt."],
      },
      {
        id: "obsidian",
        heading: "Een Obsidian-vault meenemen",
        blocks: [
          "Sleep de map van de vault, of een .zip ervan, in de stap Context van de setup of in de eigen import van de wiki. [[Links]], [[notitie|alias]] en [[notitie#sectie]] blijven werken. Tags komen uit de frontmatter en de tekst. Bijlagen gaan naar de Drive, en ingesloten afbeeldingen en bestanden worden kaarten op de pagina. Callouts blijven callouts. Dataview-query's komen binnen als tekst: vraag een agent om de tabel te bouwen. Canvassen en de instellingen van Obsidian (.obsidian) blijven buiten.",
        ],
      },
    ],
  },
  memory: {
    title: "Geheugen",
    summary: "Wat elke agent leert van gesprekken en van de correcties van het team.",
    sections: [
      {
        id: "learns",
        heading: "Hoe agents leren",
        blocks: [
          "Agents leren van gesprekken en van de correcties van het team. Wat een agent weet, komt in zijn context als het relevant is. Op het tabblad **Geheugen** bewerk, pin of archiveer je wat niet klopt of verouderd is.",
        ],
      },
      {
        id: "permissions",
        heading: "Geheugen geeft geen toestemming",
        blocks: ["Niets in het geheugen laat een agent meer doen. Wat hij mag, blijft in zijn **Tools en toegang**."],
      },
      {
        id: "proposals",
        heading: "Voorgestelde wijzigingen",
        blocks: [
          "Als een les terugkomt, kan de agent voorstellen zijn eigen instructies of de context van de werkruimte aan te passen. Er verandert niets tot iemand het accepteert, en de tekst kan eerst worden bijgewerkt. De vorige versie blijft in de geschiedenis. Een geweigerd voorstel wordt bewaard als les, zodat het niet opnieuw wordt voorgesteld.",
        ],
      },
    ],
  },
  approvals: {
    title: "Goedkeuringen",
    summary: "Gevoelige acties wachten op een mens, één keer of voorgoed.",
    sections: [
      {
        id: "what",
        heading: "Wat eerst vraagt",
        blocks: [
          "Als een agent wil mergen, deployen, terugdraaien, iets naar buiten sturen of gegevens verwijderen, vraagt hij het eerst. Dat is het niveau **Alleen wat riskant is**, waarmee elke agent begint. Op de pagina van de agent, onder **Goedkeuringen**, laat **Altijd vragen** hem vragen voordat er iets verandert, en met **Nooit vragen** vraagt hij niets, ook niet bij merge, deploy, betalingen en routines. Het verzoek verschijnt in **Goedkeuringen** en in het gesprek, met de precieze actie en de reden.",
        ],
      },
      {
        id: "always",
        heading: "Eén keer, of altijd",
        blocks: [
          "Goedkeuren geldt voor één precieze actie, met haar waarden. Verandert de agent iets, dan vraagt hij het opnieuw.",
          "**Altijd goedkeuren** laat de agent dat soort actie vanaf nu doen, met elke waarde, zonder te vragen. Bij het verzoek van een routine doet **Altijd in deze routine** hetzelfde, alleen voor die routine. **Altijd goedkeuren** wordt aangeboden voor elk soort actie, ook mergen en deployen, zolang de agent op het niveau **Alleen wat riskant is** staat. **Altijd in deze routine** wordt niet aangeboden voor mergen, deployen, terugdraaien en dergelijke. Ongedaan maken doe je in **Tools en toegang** op de pagina van de agent, of in de instellingen van de routine.",
        ],
      },
      {
        id: "who",
        heading: "Wie goedkeurt",
        blocks: [
          {
            list: [
              "De eigenaar van de agent, voor zijn verzoeken.",
              "Iedereen met een rol met **Acties van agents goedkeuren**, voor elke agent.",
              "De mensen die een agent noemt als hij vraagt, in plaats van zijn eigenaar.",
              "Wie iemand vervangt in **Wie keurt voor mij goed**, voor wat aan die persoon wordt gevraagd. Mergen, deployen en andere risicovolle acties alleen als dat aanstaat.",
            ],
          },
          "Een actie in iemands eigen mailbox wacht alleen op die persoon. Nieuwe toegang voor een agent wacht op wie agents beheert.",
          "Lodi, je rechterhand, kan ook voor jou nieuwe agents goedkeuren die andere agents voorstellen, en wijzigingen in het model, budget en de kanalen van een agent, alleen als jij erom vroeg. Wie jou vervangt en wat Lodi goedkeurt, kies je in **Instellingen\u00a0›\u00a0Jij\u00a0›\u00a0Goedkeuringen**.",
        ],
      },
      {
        id: "refusing",
        heading: "Weigeren",
        blocks: ["Zeg waarom, als je wilt: de agent leest het, past zijn plan aan en bewaart het als les."],
      },
      {
        id: "meetings",
        heading: "Afspraken",
        blocks: ["Agents stellen alleen tijden voor. Uitnodigingen aan mensen gaan altijd via degene die erom vroeg."],
      },
    ],
  },
  "who-answers": {
    title: "Wie een bericht beantwoordt",
    summary: "Een vermelding kiest de agent. Zonder vermelding kiest VortoOne voor je, in deze volgorde.",
    sections: [
      {
        id: "order",
        heading: "Hoe een bericht zijn agent vindt",
        blocks: [
          "Een bericht dat een agent @vermeldt, gaat naar die agent. Een bericht zonder vermelding wordt gelezen door de eerste van deze die de werkruimte heeft:",
          {
            list: [
              "**Jev**, een klein, snel model gemaakt voor beslissingen zoals deze.",
              "**Een klein model op een van de API-sleutels van de werkruimte**, zoals Claude Haiku 5.5 of GPT-6 Luna.",
              "**Je eigen Claude- of ChatGPT-abonnement**, voor de berichten die jij schrijft, op een server die je zelf beheert. Het leest ze op de computer waar het is ingelogd, en dat duurt een paar seconden.",
              "**Eenvoudige regels**: een antwoord aan een agent gaat naar die agent, net als een vervolgbericht vlak nadat je met hem sprak, of een bericht in een kanaal waar je alleen bent met één agent.",
            ],
          },
          "Past geen van deze, dan antwoordt er geen agent. Vermeld een agent om hem op te roepen.",
        ],
      },
      {
        id: "jev",
        heading: "Jev",
        blocks: [
          "Als VortoOne je werkruimte host, zit Jev erbij inbegrepen. Op een server die je zelf beheert, gebruikt hij de TypeSafe-sleutel van de eigenaar, ongeveer US$ 0,0001 per bericht: [Jev instellen](/help/jev#setup).",
        ],
      },
      {
        id: "plans",
        heading: "Waarom je abonnement alleen voor jouw berichten kiest",
        blocks: [
          "Abonnementen van Claude en ChatGPT zijn persoonlijk, dus je abonnement kiest alleen voor de berichten die jij schrijft.",
        ],
      },
      {
        id: "see",
        heading: "Zien wie er koos",
        blocks: ["Open het vinkje onder een bericht. De laatste regel zegt wie de agents koos."],
      },
    ],
  },
  jev: {
    title: "Jev",
    summary:
      "Een snel model van TypeSafe dat snelle beslissingen neemt voor je agents. Op je eigen server is het optioneel, met een eigen sleutel.",
    sections: [
      {
        id: "what",
        heading: "Wat het doet",
        blocks: [
          {
            list: [
              "**Kiest wie antwoordt.** Een bericht waarin niemand is @vermeld, gaat in minder dan een seconde naar de juiste agent, ook als je agents denken met een abonnement en je geen API-sleutel hebt.",
              "**Houdt het geheugen netjes.** Het herkent een herhaling of een nieuwer feit, ook in andere woorden: de herhaling gaat op in wat de agent al wist, en het nieuwere feit vervangt het oude. Eén keer per dag ruimt het de rest op.",
              "**Ziet het scherm.** In hun browser en op hun bureaublad vinden agents knoppen en velden aan de hand van een beschrijving, kijken ze beter naar kleine, en controleren ze op de afbeelding van het scherm of een stap gelukt is.",
              "**Leest minder.** Elke beurt laadt alleen de tools die de agent waarschijnlijk nodig heeft, zodat je AI minder leest en minder kost.",
            ],
          },
        ],
      },
      {
        id: "without",
        heading: "Zonder Jev",
        blocks: [
          "Alles blijft werken, alleen trager en minder zorgvuldig.",
          {
            list: [
              "**Wie antwoordt:** een klein model op een van je API-sleutels kiest, zoals Claude Haiku 5.5 of GPT-6 Luna. Zonder sleutel kiest het eigen Claude- of ChatGPT-abonnement van elke persoon voor de berichten die die persoon schrijft, in een paar seconden. Anders komt een antwoord of een vervolgbericht nog steeds bij zijn agent aan. Vermeld een agent om hem op te roepen. [Hoe agents worden gekozen](/help/who-answers)",
              "**Geheugen:** herhalingen in dezelfde woorden worden nog steeds samengevoegd. Herhalingen en updates in andere woorden blijven apart.",
              "**Scherm:** agents gebruiken de nummers op het scherm en exacte namen. Er wordt niets uit afbeeldingen gelezen.",
            ],
          },
        ],
      },
      {
        id: "setup",
        heading: "Instellen",
        blocks: [
          {
            list: [
              "Open https://console.typesafe.ai en log in met Google of je e-mailadres. Een nieuw account maak je op dezelfde manier.",
              "Zet tegoed in de console. Jev is prepaid.",
              "Open https://console.typesafe.ai/keys, maak een sleutel aan en kopieer hem meteen.",
              "Open in VortoOne **Instellingen › AI en gebruik** en plak hem onder **Jev**. Alleen degene die deze server heeft ingericht, ziet hem.",
            ],
          },
          "Op een server die je met Docker draait, kun je de sleutel ook in `.env` zetten als `TYPESAFE_API_KEY`. Die gaat voor op de sleutel in Instellingen.",
        ],
      },
      {
        id: "cost",
        heading: "Wat het kost",
        blocks: [
          {
            list: [
              "US$ 0,042 per miljoen tokens die Jev leest. Zijn antwoorden zijn gratis. Kiezen wie antwoordt leest ongeveer 2.500 tokens, dus ongeveer US$ 0,0001. Een screenshot is ongeveer 3.900 tokens.",
              "Tegoed dat je koopt, blijft 12 maanden geldig. Zet automatisch opwaarderen aan in de console, zodat Jev niet zonder komt te zitten.",
              "**Instellingen › AI en gebruik** laat zien hoeveel beslissingen Jev deze maand nam, en wat ze kostten.",
            ],
          },
        ],
      },
      {
        id: "data",
        heading: "Je gegevens",
        blocks: [
          "Wat Jev leest om te beslissen:",
          {
            list: [
              "De naam en beschrijving van het kanaal, het bericht en de paar berichten ervoor",
              "De namen en rollen van je agents, het begin van hun instructies en de tools die ze kunnen gebruiken",
              "De geheugenitems die het vergelijkt",
              "De taak van een agent en het begin van de resultaten van zijn tools",
              "Het doel van een missie en de opdrachten die een agent ervoor wil uitvoeren",
              "De tools die een gekoppelde app aanbiedt",
              "De pagina of het scherm dat een agent gebruikt, schermafbeeldingen inbegrepen",
            ],
          },
          "TypeSafe draait Jev in de Verenigde Staten en traint zijn modellen niet op wat je stuurt. Het bewaart gegevens zo lang als nodig is om de dienst te leveren. Met de enterprise-abonnementen kun je ervoor kiezen dat er niets wordt bewaard.",
          "Het privacybeleid van TypeSafe: https://typesafe.ai/legal/privacy-policy. De verwerkersovereenkomst: https://typesafe.ai/legal/data-processing.",
        ],
      },
      {
        id: "problems",
        heading: "Als het stopt",
        blocks: [
          {
            list: [
              "**Sleutel niet geaccepteerd:** maak een nieuwe sleutel aan en plak hem in **Instellingen › AI en gebruik**.",
              "**Tegoed op:** zet tegoed in de console. Jev probeert het binnen 30 minuten opnieuw, of klik op **Opnieuw controleren**.",
              "**Druk of onbereikbaar:** TypeSafe kan het aantal verzoeken per seconde beperken, of een tijdje niet antwoorden. Jev pakt het vanzelf weer op. Je limieten: https://console.typesafe.ai/rate-limits. Voor hogere limieten schrijf je naar sales@typesafe.ai.",
            ],
          },
          "Intussen werken agents zoals in [Zonder Jev](/help/jev#without).",
        ],
      },
    ],
  },
  "apps-de-ia": {
    title: "AI-apps",
    summary:
      "Vraag de agents van VortoOne om taken vanuit Claude, Cursor of een andere MCP-app, zonder die te verlaten, en krijg het antwoord daar.",
    sections: [
      {
        id: "what",
        heading: "Hoe het werkt",
        blocks: [
          "VortoOne is een MCP-server. Je plakt het adres in je AI-app, logt in met je account en kiest een werkruimte. Vanaf dan kan de app agents en kanalen tonen, namens jou een taak naar een agent sturen, op het antwoord wachten en recente taken lezen.",
          "Bijvoorbeeld midden in een scène in Unity of Blender vraag je Claude: ‘Vraag de marketingagent om een tekstje voor de lancering’. Het bericht verschijnt in VortoOne als het jouwe, gemarkeerd met **via Claude**, en het antwoord van de agent komt terug in Claude.",
          "Het adres staat in **Instellingen › AI-apps**. Het eindigt op `/mcp`.",
        ],
      },
      {
        id: "connect",
        heading: "Een app koppelen",
        blocks: [
          {
            list: [
              "**Claude** (claude.ai, Claude Desktop en telefoon): klik in **Customize › Connectors** op **+ Add** en dan op **Add custom connector**, noem hem VortoOne, plak het adres en klik op **Continue**. Kies **Sign in now**, klik op **Add** en geef toestemming. In de abonnementen Team en Enterprise voegt de Owner van de Claude-organisatie de connector toe in Organization settings › Connectors, en klikt elke persoon op **Connect** in Customize › Connectors. Het gratis abonnement van Claude heeft ruimte voor één eigen connector.",
              "**Claude Code:** voer `claude mcp add --transport http --scope user VortoOne <address>` uit, typ dan `/mcp`, kies VortoOne en **Authenticate**. Alleen voor de huidige map laat je `--scope user` weg.",
              "**Cursor:** **Toevoegen aan Cursor** in **Instellingen › AI-apps**, of het adres in `~/.cursor/mcp.json`. Cursor vraagt je in te loggen als je hem voor het eerst gebruikt.",
              '**VS Code:** **Toevoegen aan VS Code**, of het adres in `.vscode/mcp.json` met `"type": "http"`.',
              "**Andere apps:** ChatGPT, Gemini, Grok, Perplexity, Codex, Zed, JetBrains en meer hebben elk hun eigen stappen in **App koppelen**, met je adres al ingevuld.",
            ],
          },
          "Bij het inloggen laat VortoOne zien welke app om toegang vroeg en waar hij naar terugkeert. Ga alleen verder als je de koppeling net zelf bent gestart.",
        ],
      },
      {
        id: "tools",
        heading: "Wat de app kan",
        blocks: [
          {
            list: [
              "`send_to_agent`: stuurt een bericht van jou naar een agent, in je privégesprek met hem of in een kanaal, en wacht op het antwoord. Alleen met toegang **Lezen en taken naar agents sturen**.",
              "`wait_for_reply`: blijft wachten op het antwoord op een taak.",
              "`list_agents`: de agents die jij ziet.",
              "`list_channels`: de kanalen die jij leest.",
              "`read_conversation`: recente berichten van een kanaal, een thread of een taak.",
              "`list_recent_tasks`: de taken die je vanuit apps stuurde en waar agents nu mee bezig zijn.",
            ],
          },
          "De app handelt namens jou en ziet alleen wat jij ziet. Hij keurt nooit acties van agents goed: als een agent stopt om goedkeuring te vragen, zegt het antwoord dat met een link, en beslist een mens in VortoOne. Hij verandert ook geen instellingen, leden of rechten.",
        ],
      },
      {
        id: "workspace",
        heading: "Eén werkruimte per koppeling",
        blocks: [
          "Elke koppeling geldt voor de werkruimte die je bij het inloggen koos. Wil je een andere werkruimte gebruiken, koppel de app dan opnieuw en kies de andere.",
          "In **Instellingen › AI-apps** zie je de gekoppelde apps en wanneer elke app is gebruikt, en kun je ze ontkoppelen. Wie leden beheert, ziet de apps van de hele werkruimte en kan ze ook ontkoppelen. Elk bericht dat vanuit een app wordt gestuurd, staat in de audit.",
        ],
      },
      {
        id: "tokens",
        heading: "Persoonlijke tokens",
        blocks: [
          "Voor scripts en apps zonder inloggen via OAuth maak je een persoonlijk token aan in **Instellingen › AI-apps** en stuur je het mee in de header `Authorization: Bearer`. Het token wordt één keer getoond, werkt alleen in de werkruimte waarin het is aangemaakt, en kan verlopen na 30 dagen, 90 dagen, 1 jaar of nooit.",
          "Op een server zonder https, buiten de eigen computer, gebruik je een persoonlijk token: inloggen via OAuth vereist https.",
        ],
      },
      {
        id: "limits",
        heading: "Limieten",
        blocks: [
          {
            list: [
              "Tot 120 verzoeken per minuut en 5.000 per dag per koppeling.",
              "Tot 20 berichten per minuut en 500 per dag per persoon in elke werkruimte.",
              "Wachten duurt maximaal 45 seconden. Is de agent nog bezig, dan wacht de app opnieuw.",
            ],
          },
        ],
      },
      {
        id: "safety",
        heading: "Tekst van anderen",
        blocks: [
          "Antwoorden bevatten tekst die agents en mensen in VortoOne hebben geschreven. De app krijgt die tekst gemarkeerd als inhoud, niet als instructies, maar een tekst kan toch proberen de app iets anders te laten doen. Controleer wat je app gaat doen voordat je bevestigt.",
        ],
      },
      {
        id: "problems",
        heading: "Als koppelen niet lukt",
        blocks: [
          {
            list: [
              "**Verzoek verlopen:** de inlogpagina blijft 30 minuten geldig en werkt één keer. Ga terug naar de app en koppel opnieuw.",
              "**De app vraagt je opnieuw in te loggen:** de koppeling is verbroken, het wachtwoord is gewijzigd of je hebt de werkruimte verlaten. Log opnieuw in vanuit de app.",
              "**Cursor blijft hangen op een oude login:** sluit Cursor af, verwijder de map `~/.mcp-auth` en koppel opnieuw.",
            ],
          },
        ],
      },
    ],
  },
  "install-on-a-server": {
  "title": "VortoOne op een server installeren",
  "summary": "Gebruik het installatiepakket en de verbindingsinstructies van de huidige VortoOne Desktop-versie. Deze versie biedt geen gehoste clouddienst of serverinstallatie met één opdracht.",
  "sections": []
},
};
