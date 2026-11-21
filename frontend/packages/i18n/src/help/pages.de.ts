import type { HelpSlug } from "./index";
import type { HelpDocument } from "./types";

/** The help pages in de: a page not here shows in English. */
export const helpDe: Partial<Record<HelpSlug, HelpDocument>> = {
  "run-on-your-computer": {
  "title": "VortoOne auf deinem Computer ausführen",
  "summary": "Verwende das Installationspaket und die Verbindungsanleitung der aktuellen VortoOne Desktop-Version. Diese Version bietet weder einen gehosteten Cloud-Dienst noch eine Serverinstallation mit einem Befehl.",
  "sections": []
},
  "workspace-computer": {
  "title": "Der Computer des Workspace",
  "summary": "Verwende das Installationspaket und die Verbindungsanleitung der aktuellen VortoOne Desktop-Version. Diese Version bietet weder einen gehosteten Cloud-Dienst noch eine Serverinstallation mit einem Befehl.",
  "sections": []
},
  computers: {
    title: "Deine Computer und Handys",
    summary:
      "Bring einen Mac, einen PC, einen Linux-Computer, einen Server oder ein Handy mit, und deine Agenten arbeiten darauf. Auf VortoOne Cloud genauso wie auf einem eigenen Server.",
    sections: [
      {
        id: "app",
        heading: "Mit der VortoOne-App",
        blocks: [
          "Installiere die App auf dem Computer, öffne sie und melde dich bei deinem Workspace an. Sie fragt **Agenten von <workspace> diesen Mac nutzen lassen?** (diesen PC unter Windows, diesen Computer unter Linux). Wähle, wer ihn nutzen darf, und drück **Erlauben**. Das ist alles.",
          "Schon angemeldet? Öffne **Einstellungen\u00a0›\u00a0Allgemein** und drück in der ersten Karte **Erlauben** in der Zeile des Workspace. Nicht dabei? Drück **Workspace hinzufügen…** unten in dieser Karte und melde dich an.",
        ],
      },
      {
        id: "web",
        heading: "Über die Website",
        blocks: [
          "Drück unter **Einstellungen › Geräte** auf **Gerät verbinden**:",
          {
            list: [
              "**Dieser Mac** oder **Dieser PC**: öffnet die App, die dich vorher fragt.",
              "**Ein anderer Computer**: Installiere dort die App, melde dich mit deinem Konto an und drück **Erlauben**. Er erscheint hier von selbst.",
              "**Ein Server oder VPS**: Führe eine Zeile in seinem Terminal aus und lass ihn dann beitreten. Die Zeile funktioniert einmal, 10 Minuten lang.",
            ],
          },
          "Mit einem Klick verbindet sich die App nur mit einer Adresse mit https, etwa einer von Tailscale, die auf `.ts.net` endet. Bei einer einfachen http-Adresse nimm die Zeile fürs Terminal.",
        ],
      },
      {
        id: "phones",
        heading: "Handys",
        blocks: [
          "Ein Android-Handy verbindet sich über jeden Computer, der schon verbunden ist: per Kabel oder über WLAN ab Android 14 mit dem Sicherheitsupdate vom Mai 2026. Ein iPhone verbindet sich über einen Mac mit der App und Xcode, den Rest richtet die App ein. Beides beginnt bei **Gerät verbinden**.",
        ],
      },
      {
        id: "who",
        heading: "Wer es nutzen darf",
        blocks: [
          "Ein neues Gerät ist nur für dich: **Nur ich** heißt du und die Agenten, die für dich arbeiten. Öffne es für alle im Workspace oder für bestimmte Personen und Rollen, gleich beim Erlauben oder später auf seiner Seite unter **Einstellungen › Geräte**.",
        ],
      },
      {
        id: "open",
        heading: "Solange die App offen ist",
        blocks: [
          "Agenten arbeiten auf einem Computer nur, solange die App dort läuft und der Computer wach ist. Wenn die App fragt, lass **Bereithalten: VortoOne bei der Anmeldung öffnen** angehakt. Ein Computer, der aus ist oder schläft, zeigt **Offline**, und die Agenten warten, bis er wieder da ist.",
        ],
      },
      {
        id: "pausing",
        heading: "Pausieren und trennen",
        blocks: [
          "Auf dem Computer pausiert das VortoOne-Symbol neben der Uhr die Agenten aller Workspaces oder nur eines Workspace. Unter **Einstellungen\u00a0›\u00a0Allgemein** pausiert der Schalter eines Workspace nur diesen einen, und **Trennen** in seinem **⋯**-Menü trennt ihn. Während der Pause startet nichts Neues, und was gerade läuft, wird noch fertig. Beim Trennen bleiben die Ordner der Agenten auf der Festplatte.",
          "Auf der Website trennst du ein Gerät mit **Entfernen** auf seiner Seite.",
        ],
      },
      {
        id: "organizations",
        heading: "Mehrere Workspaces, ein Computer",
        blocks: [
          "Melde dich in derselben App bei jedem Workspace an und drück für jeden **Erlauben**. Workspaces auf VortoOne Cloud und auf deinen eigenen Servern können sich einen Computer teilen (ab App-Version 0.6.1). Jeder hat dort eigene Agenten und Ordner, und wenn du einen trennst, bleiben die anderen unberührt.",
          "Ein Server, den die App auf diesem Computer ausführt, ist schon verbunden und braucht nichts. Um den Computer auch einem Workspace an anderer Stelle zu leihen, sieh dir [Auch einem anderen Workspace leihen](/help/run-on-your-computer#another-workspace) an.",
          "Nutzt du die App auf einem Laptop nur als Fenster zu deinem Workspace? Wenn sie fragt, ob Agenten den Laptop nutzen dürfen, bleibt sie mit **Nicht jetzt** ein einfaches Fenster.",
          "Ein Server, der mit der Terminal-Zeile verbunden wurde, arbeitet für einen Workspace. Wird er von einem anderen Workspace aus verbunden, wechselt der Server zu diesem, und der erste erreicht ihn nicht mehr. Für mehrere Workspaces auf einem Computer nutze die App.",
        ],
      },
      {
        id: "line",
        heading: "Wenn Agenten in der Warteschlange stehen",
        blocks: [
          "Jede Arbeit eines Agenten braucht Arbeitsspeicher des Computers. Wird der freie Speicher knapp, wartet die nächste in der Warteschlange, bis eine andere endet. Menschen kommen zuerst dran. Die Zahl der CPUs ändert daran nichts.",
          "Ein Computer mit 4 GB Arbeitsspeicher führt zwei oder drei Arbeiten gleichzeitig aus, einer mit 8 GB etwa neun. Ein Mac oder PC führt bis zu acht aus. Browser und Desktops, die Agenten offen lassen, brauchen ebenfalls Speicher. Für eine kürzere Warteschlange nutze einen Computer mit mehr Arbeitsspeicher oder verbinde einen weiteren und verteile die Agenten auf beide. Workspaces, die sich einen Computer teilen, teilen sich seinen Speicher.",
        ],
      },
      {
        id: "folders",
        heading: "Ordner",
        blocks: [
          "Jeder Agent hat immer einen eigenen Ordner, in `~/vortoone/<workspace>`. Weitere Ordner gibst du nur auf dem Computer selbst frei, nie über die Website: in der App unter **Einstellungen\u00a0›\u00a0Ordner** oder auf einem Server mit `vortoone-runner folders allow`. Jeder Workspace gibt seine eigenen Ordner frei: Was du für einen freigibst, gilt nur für dessen Agenten. Einen Ordner zu entfernen löscht nichts. Es nimmt nur den Zugriff weg.",
        ],
      },
      {
        id: "reach",
        heading: "Was Agenten erreichen können",
        blocks: [
          "Agenten laufen als du, der Benutzer des Computers. Ihre Datei-Tools bleiben in ihren Ordnern, aber ein Befehl kann alles lesen, was du lesen kannst:",
          {
            list: [
              "deine Dateien und Anmeldungen",
              "die Ordner anderer Agenten, auch die anderer Workspaces, und die Logins, die ihre Browser speichern",
              "das Token, das diesen Computer verbindet",
              "auf einem Computer, der auch VortoOne ausführt, dessen Datenbank und Schlüssel in `.vortoone`",
            ],
          },
          "VortoOne lehnt Befehle ab, die seine Schlüssel, Tokens und Anmeldungen nennen, aber das ist eine Regel, keine Mauer. **Befehle** sind von Anfang an eingeschaltet. Schalte sie in der Frage der App aus, bevor du **Erlauben** drückst, oder später unter **Einstellungen\u00a0›\u00a0Tools**, wo jeder Workspace einen eigenen Schalter hat (ab App-Version 0.8.1) und die App nachfragt, bevor sie einen wieder einschaltet. Claude Code und Codex führen ihre eigenen Befehle im Ordner des Agenten aus, nach ihren eigenen Regeln, auch wenn Befehle aus sind.",
          "Für eine echte Mauer nimm einen Linux-Server. Unter Debian 13, installiert mit `--system`, ist jeder Agent ein eigener Benutzer. Unter Ubuntu teilen sich die Agenten einen Benutzer, `vortoone`: Sie sehen die Ordner der anderen, aber nicht deine oder die des Servers. Oder gib den Agenten einen eigenen Computer.",
        ],
      },
    ],
  },
  import: {
    title: "Bring deine Agenten mit",
    summary:
      "Hol Agenten aus einer anderen App oder einem anderen VortoOne-Workspace, mit ihren Anweisungen, Skills, Routinen und ihrem Gedächtnis.",
    sections: [
      {
        id: "comes",
        heading: "Was mitkommt",
        blocks: [
          {
            list: [
              "Agenten, mit Name, Rolle, Anweisungen und Aussehen.",
              "Skills, eingeschaltet für die Agenten, die sie hatten.",
              "Öffentliche Kanäle, mit den Agenten darin.",
              "Routinen, aktiv und nach ihrem Zeitplan. Pausiere sie unter **Routinen**, wenn du willst.",
              "Gedächtnis: was sich jeder Agent merkt.",
              "Die eigenen Dateien jedes Agenten (der Dateiordner von OpenBot.run, ein OpenClaw-Workspace), in einem Ordner mit seinem Namen im **Drive**. Schlüssel, Logins und Systemdateien bleiben zurück, und nichts, was schon da ist, wird ersetzt.",
            ],
          },
        ],
      },
      {
        id: "never",
        heading: "Was nie mitkommt",
        blocks: [
          "Schlüssel, Passwörter, Logins und Chatverläufe kommen nie mit: Die Agenten denken mit der KI des Workspace. Manches hat hier noch keine Entsprechung oder sollte nie in einer Datei reisen. Du kannst es später neu anlegen.",
          "Dateien und Ordner werden in deinem Browser gelesen. Ein GitHub-Link geht über den Server des Workspace. Schlüssel- und Login-Dateien werden nie geöffnet, und nichts ändert sich, bevor du bestätigst.",
        ],
      },
      {
        id: "mcp",
        heading: "Mit MCP verbinden",
        blocks: [
          "MCP lässt deine KI-App auf diesen Workspace zugreifen, damit sie die Agenten hier selbst anlegt, mit ihren Anweisungen, Skills und Routinen. Wähle deine App, nimm **Mit MCP verbinden** und dann:",
          {
            list: [
              "Verbinde die Adresse, die der Schritt zeigt, mit den Schritten der App selbst. [So verbindest du jede App](/help/apps-de-ia#connect)",
              "Wähle auf der Anmeldeseite **Agenten, Routinen und Skills erstellen und bearbeiten**.",
              "Füge die Anfrage in die App ein. Die Agenten erscheinen im Schritt, sobald sie ankommen.",
            ],
          },
          "Wenn sich eine App nicht verbinden lässt, wähle **Aus einer Datei** oder **Nur die Anfrage**, je nachdem, was der Schritt zeigt. [Aus einer anderen App](/help/import#other-apps) erklärt die Anfrage.",
        ],
      },
      {
        id: "other-apps",
        heading: "Aus einer anderen App",
        blocks: [
          "Wähle **Grok Bot**, **ChatGPT dots** oder **Andere App**. Kopiere die Anfrage, füge sie in dieser App ein und bring dann zurück, was sie liefert: Zieh die Datei hinein oder füge die ganze Antwort ein. Wo das in jeder App geht:",
          {
            list: [
              "**Grok Bot**: im Chat mit deinem Bot. Er antwortet mit dem JSON: Kopiere die ganze Antwort.",
              "**ChatGPT dots**: in einem neuen Chat. Lade die Datei herunter, die es erstellt.",
              "**Claude**: in einer neuen Unterhaltung oder im Projekt deiner Agenten. Lade die Datei herunter, die es erstellt.",
              "**Manus**: in einer neuen Aufgabe. Die Datei steht in den Ergebnissen der Aufgabe.",
              "**Rakazo**: Schick sie an deinen Agenten. Er antwortet mit dem JSON: Kopiere die ganze Antwort.",
              "**OpenBot**: keine Anfrage nötig. Zieh den Tenant-Ordner hinein (agents.yaml, agents/, model.yaml, skills.yaml) oder füge seinen GitHub-Link ein.",
            ],
          },
        ],
      },
      {
        id: "export",
        heading: "Einen Workspace exportieren",
        blocks: [
          "**Einstellungen\u00a0›\u00a0Import und Export\u00a0›\u00a0Export** lädt eine .json-Datei herunter, um deine Agenten in einen anderen Workspace mitzunehmen oder eine Kopie zu behalten. In der Datei:",
          {
            list: [
              "Agenten, mit Anweisungen, Modell und Aussehen",
              "Skills, mit der ganzen SKILL.md",
              "Öffentliche Kanäle, mit ihren Agenten",
              "Geplante Routinen (sie kommen auf der anderen Seite aktiv an)",
              "Das Gedächtnis, das alle im Workspace sehen können",
            ],
          },
          "Nie darin:",
          {
            list: [
              "API-Schlüssel und Passwörter",
              "Konnektoren und Logins",
              "Computer",
              "Unterhaltungen, Nachrichten und private Kanäle",
              "Nutzung, Kosten und Aktivitätsverlauf",
            ],
          },
          "Um die Datei zu nutzen, öffne im anderen Workspace **Einstellungen\u00a0›\u00a0Import und Export** und zieh die .json hinein.",
        ],
      },
    ],
  },
  wiki: {
    title: "Das Wiki",
    summary: "Was das Unternehmen weiß, in Seiten, die miteinander verlinkt sind.",
    sections: [
      {
        id: "links",
        heading: "Links",
        blocks: [
          "Tippe [[, um eine andere Seite zu verlinken. Rückverweise und der Graph zeigen, wie Seiten zusammenhängen.",
        ],
      },
      {
        id: "agents",
        heading: "Wie Agenten es nutzen",
        blocks: ["Agenten lesen das Wiki, wenn sie antworten. Sie schreiben nur darin, wenn jemand darum bittet."],
      },
      {
        id: "obsidian",
        heading: "Einen Obsidian-Vault mitbringen",
        blocks: [
          "Zieh den Ordner des Vaults oder eine .zip davon in den Schritt Kontext der Einrichtung oder in den Import des Wikis. [[Links]], [[notiz|alias]] und [[notiz#abschnitt]] funktionieren weiter. Tags kommen aus dem Frontmatter und dem Text. Anhänge landen im Drive, und eingebettete Bilder und Dateien werden zu Karten auf der Seite. Callouts bleiben Callouts. Dataview-Abfragen kommen als Text an: Bitte einen Agenten, die Tabelle zu bauen. Canvases und die Einstellungen von Obsidian (.obsidian) bleiben draußen.",
        ],
      },
    ],
  },
  memory: {
    title: "Gedächtnis",
    summary: "Was jeder Agent aus Unterhaltungen und den Korrekturen des Teams lernt.",
    sections: [
      {
        id: "learns",
        heading: "Wie Agenten lernen",
        blocks: [
          "Agenten lernen aus Unterhaltungen und aus den Korrekturen des Teams. Was ein Agent weiß, kommt in seinen Kontext, wenn es relevant ist. Im Tab **Gedächtnis** kannst du bearbeiten, anheften oder archivieren, was falsch oder veraltet ist.",
        ],
      },
      {
        id: "permissions",
        heading: "Das Gedächtnis gibt keine Berechtigungen",
        blocks: [
          "Nichts im Gedächtnis erlaubt einem Agenten mehr. Was er darf, steht weiter unter **Tools und Zugriff**.",
        ],
      },
      {
        id: "proposals",
        heading: "Vorgeschlagene Änderungen",
        blocks: [
          "Wenn sich eine Lektion wiederholt, kann der Agent vorschlagen, seine eigenen Anweisungen oder den Kontext des Workspace zu ändern. Nichts ändert sich, bevor jemand zustimmt, und der Text lässt sich vorher anpassen. Die vorige Version bleibt im Verlauf. Ein abgelehnter Vorschlag wird als Lektion behalten, damit er nicht noch einmal kommt.",
        ],
      },
    ],
  },
  approvals: {
    title: "Freigaben",
    summary: "Heikle Aktionen warten auf einen Menschen, einmal oder für immer.",
    sections: [
      {
        id: "what",
        heading: "Was vorher fragt",
        blocks: [
          "Wenn ein Agent mergen, deployen, zurückrollen, etwas nach außen senden oder Daten löschen will, fragt er vorher. Das ist die Stufe **Nur Riskantes**, mit der jeder Agent startet. Auf der Seite des Agenten, unter **Freigaben**, lässt **Immer fragen** ihn vor jeder Änderung fragen, und mit **Nie fragen** fragt er gar nicht, auch nicht bei Merge, Deploy, Zahlungen und Routinen. Die Anfrage erscheint unter **Freigaben** und in der Unterhaltung, mit der genauen Aktion und dem Grund.",
        ],
      },
      {
        id: "always",
        heading: "Einmal oder immer",
        blocks: [
          "Eine Freigabe gilt für genau eine Aktion, mit ihren Werten. Ändert der Agent irgendetwas, fragt er erneut.",
          "**Immer freigeben** lässt den Agenten diese Art von Aktion ab jetzt mit beliebigen Werten tun, ohne zu fragen. Bei der Anfrage einer Routine macht **Immer in dieser Routine** dasselbe nur für diese Routine. **Immer freigeben** gibt es für jede Art von Aktion, auch für Merge und Deploy, solange der Agent auf der Stufe **Nur Riskantes** steht. **Immer in dieser Routine** gibt es nicht für Merge, Deploy, Rollback und Ähnliches. Rückgängig machen kannst du es unter **Tools und Zugriff** auf der Seite des Agenten oder in den Einstellungen der Routine.",
        ],
      },
      {
        id: "who",
        heading: "Wer freigibt",
        blocks: [
          {
            list: [
              "Der Inhaber des Agenten, für seine Anfragen.",
              "Alle, deren Rolle **Aktionen von Agenten freigeben** hat, für jeden Agenten.",
              "Die Personen, die ein Agent beim Fragen nennt, anstelle seines Inhabers.",
              "Wer jemanden unter **Wer für mich freigibt** vertritt, für das, worum diese Person gebeten wird. Merge, Deploy und andere riskante Aktionen nur, wenn das eingeschaltet ist.",
            ],
          },
          "Eine Aktion im eigenen Postfach von jemandem wartet nur auf diese Person. Neuer Zugriff für einen Agenten wartet auf die, die Agenten verwalten.",
          "Lodi, deine rechte Hand, kann außerdem für dich neue Agenten freigeben, die andere Agenten vorschlagen, und Änderungen an Modell, Budget und Kanälen eines Agenten, nur wenn du darum gebeten hast. Wer dich vertritt und was Lodi freigibt, legst du unter **Einstellungen\u00a0›\u00a0Du\u00a0›\u00a0Freigaben** fest.",
        ],
      },
      {
        id: "refusing",
        heading: "Ablehnen",
        blocks: ["Sag, warum, wenn du willst: Der Agent liest es, passt seinen Plan an und behält es als Lektion."],
      },
      {
        id: "meetings",
        heading: "Meetings",
        blocks: [
          "Agenten schlagen nur Zeiten vor. Einladungen an Menschen gehen immer über die Person, die gefragt hat.",
        ],
      },
    ],
  },
  "who-answers": {
    title: "Wer auf eine Nachricht antwortet",
    summary: "Eine Erwähnung wählt den Agenten. Ohne sie wählt VortoOne für dich, in dieser Reihenfolge.",
    sections: [
      {
        id: "order",
        heading: "Wie eine Nachricht ihren Agenten findet",
        blocks: [
          "Eine Nachricht, die einen Agenten mit @ erwähnt, geht an diesen Agenten. Eine Nachricht ohne Erwähnung wird von der ersten dieser Optionen gelesen, die der Workspace hat:",
          {
            list: [
              "**Jev**, ein kleines, schnelles Modell für genau solche Entscheidungen.",
              "**Ein kleines Modell über einen der API-Schlüssel des Workspace**, etwa Claude Haiku 5.5 oder GPT-6 Luna.",
              "**Dein eigenes Claude- oder ChatGPT-Abo**, für die Nachrichten, die du schreibst, auf einem Server, den du selbst betreibst. Es liest sie auf dem Computer, auf dem es angemeldet ist, und braucht ein paar Sekunden.",
              "**Einfache Regeln**: Eine Antwort an einen Agenten geht an ihn, ebenso eine Nachricht direkt nach eurer Unterhaltung oder eine Nachricht in einem Kanal, in dem du mit nur einem Agenten bist.",
            ],
          },
          "Wenn nichts davon passt, antwortet kein Agent. Erwähne einen Agenten, um ihn zu rufen.",
        ],
      },
      {
        id: "jev",
        heading: "Jev",
        blocks: [
          "Wenn VortoOne deinen Workspace hostet, ist Jev inklusive. Auf einem Server, den du selbst betreibst, nutzt es den TypeSafe-Schlüssel des Inhabers, etwa US$ 0,0001 pro Nachricht: [Jev einrichten](/help/jev#setup).",
        ],
      },
      {
        id: "plans",
        heading: "Warum dein Abo nur für deine Nachrichten wählt",
        blocks: [
          "Claude- und ChatGPT-Abos sind persönlich, deshalb wählt dein Abo nur für die Nachrichten, die du schreibst.",
        ],
      },
      {
        id: "see",
        heading: "Sehen, wer gewählt hat",
        blocks: ["Öffne den Haken unter einer Nachricht. Die letzte Zeile sagt, wer die Agenten gewählt hat."],
      },
    ],
  },
  jev: {
    title: "Jev",
    summary:
      "Ein schnelles Modell von TypeSafe, das schnelle Entscheidungen für deine Agenten trifft. Auf deinem eigenen Server ist es optional, mit eigenem Schlüssel.",
    sections: [
      {
        id: "what",
        heading: "Was es tut",
        blocks: [
          {
            list: [
              "**Wählt, wer antwortet.** Eine Nachricht ohne @-Erwähnung geht in unter einer Sekunde an den richtigen Agenten, auch wenn deine Agenten mit einem Abo denken und du keinen API-Schlüssel hast.",
              "**Hält das Gedächtnis ordentlich.** Es erkennt eine Wiederholung oder eine neuere Tatsache auch in anderen Worten: Die Wiederholung verschmilzt mit dem, was der Agent wusste, und die neuere Tatsache ersetzt die alte. Einmal am Tag räumt es den Rest auf.",
              "**Sieht den Bildschirm.** In ihrem Browser und auf ihrem Desktop finden Agenten Buttons und Felder anhand einer Beschreibung, schauen bei kleinen genauer hin und prüfen am Bild des Bildschirms, ob ein Schritt geklappt hat.",
              "**Liest weniger.** Jede Runde lädt nur die Tools, die der Agent wahrscheinlich braucht, also liest deine KI weniger und kostet weniger.",
            ],
          },
        ],
      },
      {
        id: "without",
        heading: "Ohne Jev",
        blocks: [
          "Alles funktioniert weiter, nur langsamer und weniger sorgfältig.",
          {
            list: [
              "**Wer antwortet:** Ein kleines Modell über einen deiner API-Schlüssel wählt, etwa Claude Haiku 5.5 oder GPT-6 Luna. Ohne Schlüssel wählt das eigene Claude- oder ChatGPT-Abo jeder Person für die Nachrichten, die sie schreibt, in ein paar Sekunden. Sonst erreicht eine Antwort oder Folgenachricht trotzdem ihren Agenten. Erwähne einen Agenten, um ihn zu rufen. [Wie Agenten gewählt werden](/help/who-answers)",
              "**Gedächtnis:** Wiederholungen in denselben Worten verschmelzen weiter. Wiederholungen und Neuerungen in anderen Worten bleiben getrennt.",
              "**Bildschirm:** Agenten nutzen die Nummern auf dem Bildschirm und exakte Namen. Aus Bildern wird nichts gelesen.",
            ],
          },
        ],
      },
      {
        id: "setup",
        heading: "Einrichten",
        blocks: [
          {
            list: [
              "Öffne https://console.typesafe.ai und melde dich mit Google oder deiner E-Mail-Adresse an. Ein neues Konto entsteht auf demselben Weg.",
              "Lade in der Konsole Guthaben auf. Jev ist Prepaid.",
              "Öffne https://console.typesafe.ai/keys, erstelle einen Schlüssel und kopiere ihn sofort.",
              "Öffne in VortoOne **Einstellungen\u00a0›\u00a0KI und Verbrauch** und füge ihn unter **Jev** ein. Nur die Person, die diesen Server eingerichtet hat, sieht ihn.",
            ],
          },
          "Auf einem Server, den du mit Docker betreibst, kannst du den Schlüssel stattdessen als `TYPESAFE_API_KEY` in `.env` eintragen. Dieser hat Vorrang vor dem in den Einstellungen.",
        ],
      },
      {
        id: "cost",
        heading: "Was es kostet",
        blocks: [
          {
            list: [
              "US$ 0,042 pro Million Tokens, die Jev liest. Seine Antworten sind kostenlos. Zu wählen, wer antwortet, liest etwa 2.500 Tokens, also etwa US$ 0,0001. Ein Screenshot sind etwa 3.900 Tokens.",
              "Gekauftes Guthaben gilt 12 Monate. Schalte in der Konsole das automatische Aufladen ein, damit Jev das Guthaben nicht ausgeht.",
              "**Einstellungen\u00a0›\u00a0KI und Verbrauch** zeigt, wie viele Entscheidungen Jev diesen Monat getroffen hat und was sie gekostet haben.",
            ],
          },
        ],
      },
      {
        id: "data",
        heading: "Deine Daten",
        blocks: [
          "Was Jev für eine Entscheidung liest:",
          {
            list: [
              "Name und Beschreibung des Kanals, die Nachricht und die paar davor",
              "Namen und Rollen deiner Agenten, den Anfang ihrer Anweisungen und die Tools, die sie nutzen können",
              "Die Gedächtniseinträge, die es vergleicht",
              "Die Aufgabe eines Agenten und den Anfang seiner Tool-Ergebnisse",
              "Das Ziel einer Mission und die Befehle, die ein Agent dafür ausführen will",
              "Die Tools, die eine verbundene App anbietet",
              "Die Seite oder den Bildschirm, den ein Agent gerade nutzt, Screenshots eingeschlossen",
            ],
          },
          "TypeSafe betreibt Jev in den USA und trainiert seine Modelle nicht mit dem, was du sendest. Daten werden so lange aufbewahrt, wie es für den Dienst nötig ist. Null Aufbewahrung gibt es in den Enterprise-Tarifen.",
          "Die Datenschutzerklärung von TypeSafe: https://typesafe.ai/legal/privacy-policy. Der Auftragsverarbeitungsvertrag: https://typesafe.ai/legal/data-processing.",
        ],
      },
      {
        id: "problems",
        heading: "Wenn es stoppt",
        blocks: [
          {
            list: [
              "**Schlüssel nicht akzeptiert:** Erstelle einen neuen Schlüssel und füge ihn unter **Einstellungen\u00a0›\u00a0KI und Verbrauch** ein.",
              "**Kein Guthaben mehr:** Lade in der Konsole Guthaben auf. Jev versucht es innerhalb von 30 Minuten erneut, oder klick auf **Erneut prüfen**.",
              "**Ausgelastet oder nicht erreichbar:** TypeSafe kann Anfragen pro Sekunde begrenzen oder eine Weile nicht antworten. Jev macht von selbst weiter. Deine Limits: https://console.typesafe.ai/rate-limits. Für höhere schreib an sales@typesafe.ai.",
            ],
          },
          "In der Zwischenzeit arbeiten Agenten wie unter [Ohne Jev](/help/jev#without).",
        ],
      },
    ],
  },
  "apps-de-ia": {
    title: "KI-Apps",
    summary:
      "Gib den Agenten von VortoOne Aufgaben aus Claude, Cursor oder einer anderen MCP-App, ohne sie zu verlassen, und bekomm die Antwort direkt dort.",
    sections: [
      {
        id: "what",
        heading: "So funktioniert es",
        blocks: [
          "VortoOne ist ein MCP-Server. Du fügst seine Adresse in deine KI-App ein, meldest dich mit deinem Konto an und wählst einen Workspace. Ab dann kann die App Agenten und Kanäle auflisten, in deinem Namen einem Agenten eine Aufgabe schicken, auf die Antwort warten und letzte Aufgaben lesen.",
          "Zum Beispiel mitten in einer Szene in Unity oder Blender fragst du Claude: „Bitte den Marketing-Agenten um einen Text zum Launch“. Die Nachricht erscheint in VortoOne als deine, markiert mit **über Claude**, und die Antwort des Agenten kommt zurück zu Claude.",
          "Die Adresse steht unter **Einstellungen\u00a0›\u00a0KI-Apps**. Sie endet auf `/mcp`.",
        ],
      },
      {
        id: "connect",
        heading: "Eine App verbinden",
        blocks: [
          {
            list: [
              "**Claude** (claude.ai, Claude Desktop und Handy): Klick unter **Customize\u00a0›\u00a0Connectors** auf **+ Add**, dann auf **Add custom connector**, nenne ihn VortoOne, füge die Adresse ein und klick auf **Continue**. Wähle **Sign in now**, klick auf **Add** und autorisiere. In den Team- und Enterprise-Abos fügt der Owner der Claude-Organisation den Konnektor unter Organization settings\u00a0›\u00a0Connectors hinzu, und jede Person klickt unter Customize\u00a0›\u00a0Connectors auf **Connect**. Das kostenlose Claude-Abo bietet Platz für einen eigenen Konnektor.",
              "**Claude Code:** Führe `claude mcp add --transport http --scope user VortoOne <address>` aus, tippe dann `/mcp`, wähle VortoOne und **Authenticate**. Nur für den aktuellen Ordner lässt du `--scope user` weg.",
              "**Cursor:** **Zu Cursor hinzufügen** unter **Einstellungen\u00a0›\u00a0KI-Apps** oder die Adresse in `~/.cursor/mcp.json`. Cursor bittet dich bei der ersten Nutzung, dich anzumelden.",
              '**VS Code:** **Zu VS Code hinzufügen** oder die Adresse in `.vscode/mcp.json` mit `"type": "http"`.',
              "**Andere Apps:** ChatGPT, Gemini, Grok, Perplexity, Codex, Zed, JetBrains und weitere haben jeweils eigene Schritte unter **App verbinden**, mit deiner Adresse schon eingetragen.",
            ],
          },
          "Bei der Anmeldung zeigt VortoOne, welche App Zugriff möchte und wohin sie zurückkehrt. Mach nur weiter, wenn du die Verbindung gerade selbst gestartet hast.",
        ],
      },
      {
        id: "tools",
        heading: "Was die App kann",
        blocks: [
          {
            list: [
              "`send_to_agent`: schickt eine Nachricht von dir an einen Agenten, in eurer privaten Unterhaltung oder in einem Kanal, und wartet auf die Antwort. Nur mit dem Zugriff **Lesen und Agenten Aufgaben schicken**.",
              "`wait_for_reply`: wartet weiter auf die Antwort zu einer Aufgabe.",
              "`list_agents`: die Agenten, die du siehst.",
              "`list_channels`: die Kanäle, die du liest.",
              "`read_conversation`: letzte Nachrichten eines Kanals, eines Threads oder einer Aufgabe.",
              "`list_recent_tasks`: die Aufgaben, die du aus Apps geschickt hast, und was Agenten gerade tun.",
            ],
          },
          "Die App handelt in deinem Namen und sieht nur, was du siehst. Sie gibt nie Aktionen von Agenten frei: Wenn ein Agent anhält, um eine Freigabe zu erbitten, sagt die Antwort das mit einem Link, und ein Mensch entscheidet in VortoOne. Einstellungen, Mitglieder oder Berechtigungen ändert sie auch nicht.",
        ],
      },
      {
        id: "workspace",
        heading: "Ein Workspace pro Verbindung",
        blocks: [
          "Jede Verbindung gilt für den Workspace, der bei der Anmeldung gewählt wurde. Für einen anderen Workspace verbindest du die App erneut und wählst den anderen.",
          "Unter **Einstellungen\u00a0›\u00a0KI-Apps** siehst du die verbundenen Apps und wann jede zuletzt genutzt wurde, und du kannst jede davon trennen. Wer Mitglieder verwaltet, sieht die Apps des ganzen Workspace und kann sie ebenfalls trennen. Jede Nachricht, die aus einer App gesendet wird, erscheint im Audit.",
        ],
      },
      {
        id: "tokens",
        heading: "Persönliche Tokens",
        blocks: [
          "Für Skripte und Apps ohne OAuth-Anmeldung erstellst du unter **Einstellungen\u00a0›\u00a0KI-Apps** ein persönliches Token und sendest es im Header `Authorization: Bearer`. Das Token wird einmal angezeigt, funktioniert nur in dem Workspace, in dem es erstellt wurde, und kann nach 30 Tagen, 90 Tagen, 1 Jahr oder nie ablaufen.",
          "Auf einem Server ohne https, von einem anderen Computer aus, nutze ein persönliches Token: Die OAuth-Anmeldung braucht https.",
        ],
      },
      {
        id: "limits",
        heading: "Limits",
        blocks: [
          {
            list: [
              "Bis zu 120 Anfragen pro Minute und 5.000 pro Tag und Verbindung.",
              "Bis zu 20 Nachrichten pro Minute und 500 pro Tag und Person in jedem Workspace.",
              "Ein Warten dauert bis zu 45 Sekunden. Arbeitet der Agent noch, wartet die App erneut.",
            ],
          },
        ],
      },
      {
        id: "safety",
        heading: "Text von anderen",
        blocks: [
          "Antworten enthalten Text, den Agenten und Menschen in VortoOne geschrieben haben. Die App bekommt diesen Text als Inhalt markiert, nicht als Anweisungen, aber ein Text kann trotzdem versuchen, die App zu etwas anderem zu überreden. Prüfe, was deine App gleich tun will, bevor du bestätigst.",
        ],
      },
      {
        id: "problems",
        heading: "Wenn die Verbindung nicht klappt",
        blocks: [
          {
            list: [
              "**Anfrage abgelaufen:** Die Anmeldeseite gilt 30 Minuten und funktioniert einmal. Geh zurück in die App und verbinde erneut.",
              "**Die App will eine neue Anmeldung:** Die Verbindung wurde getrennt, das Passwort geändert oder du hast den Workspace verlassen. Melde dich aus der App erneut an.",
              "**Cursor hängt an einer alten Anmeldung:** Beende Cursor, lösche den Ordner `~/.mcp-auth` und verbinde erneut.",
            ],
          },
        ],
      },
    ],
  },
  "install-on-a-server": {
  "title": "VortoOne auf einem Server installieren",
  "summary": "Verwende das Installationspaket und die Verbindungsanleitung der aktuellen VortoOne Desktop-Version. Diese Version bietet weder einen gehosteten Cloud-Dienst noch eine Serverinstallation mit einem Befehl.",
  "sections": []
},
};
