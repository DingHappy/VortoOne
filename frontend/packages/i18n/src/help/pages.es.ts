import type { HelpSlug } from "./index";
import type { HelpDocument } from "./types";

/** The help pages in es: a page not here shows in English. */
export const helpEs: Partial<Record<HelpSlug, HelpDocument>> = {
  "run-on-your-computer": {
  "title": "Usa VortoOne en tu computadora",
  "summary": "Usa el instalador y las instrucciones de conexión de la versión actual de VortoOne Desktop. Esta versión no ofrece un servicio de nube alojado ni instalación de servidor con un comando.",
  "sections": []
},
  "workspace-computer": {
  "title": "La computadora del espacio de trabajo",
  "summary": "Usa el instalador y las instrucciones de conexión de la versión actual de VortoOne Desktop. Esta versión no ofrece un servicio de nube alojado ni instalación de servidor con un comando.",
  "sections": []
},
  computers: {
    title: "Tus computadoras y teléfonos",
    summary:
      "Trae una Mac, una PC, una computadora con Linux, un servidor o un teléfono, y tus agentes trabajan en él. Tanto en VortoOne Cloud como en un servidor propio.",
    sections: [
      {
        id: "app",
        heading: "Desde la app de VortoOne",
        blocks: [
          "Instala la app en la computadora, ábrela e inicia sesión en tu espacio de trabajo. Te pregunta **¿Dejar que los agentes de <workspace> usen esta Mac?** (esta PC en Windows, esta computadora en Linux). Elige quién puede usarla y pulsa **Permitir**. Eso es todo.",
          "¿Ya iniciaste sesión? Abre **Preferencias\u00a0›\u00a0General** y pulsa **Permitir** en la fila del espacio de trabajo, en la primera tarjeta. ¿No aparece? Pulsa **Agregar un espacio de trabajo…** al final de esa tarjeta e inicia sesión.",
        ],
      },
      {
        id: "web",
        heading: "Desde el sitio web",
        blocks: [
          "En **Configuración\u00a0›\u00a0Dispositivos**, pulsa **Conectar un dispositivo**:",
          {
            list: [
              "**Esta Mac** o **Esta PC**: abre la app, que te pregunta primero.",
              "**Otra computadora**: instala la app allí, inicia sesión con tu cuenta y pulsa **Permitir**. Aparece aquí sola.",
              "**Un servidor o VPS**: ejecuta una línea en su terminal y luego déjalo unirse. La línea funciona una vez, durante 10 minutos.",
            ],
          },
          "La app se conecta con un clic solo a una dirección con https, como una de Tailscale que termina en `.ts.net`. En una dirección http simple, usa la línea de terminal.",
        ],
      },
      {
        id: "phones",
        heading: "Teléfonos",
        blocks: [
          "Un teléfono Android se conecta a través de cualquier computadora ya conectada: por cable, o por Wi-Fi en Android 14 o posterior con la actualización de seguridad de mayo de 2026. Un iPhone se conecta a través de una Mac con la app y Xcode, y la app prepara el resto. Ambos empiezan en **Conectar un dispositivo**.",
        ],
      },
      {
        id: "who",
        heading: "Quién puede usarlo",
        blocks: [
          "Un dispositivo nuevo es solo para ti: **Solo yo** significa tú y los agentes que trabajan para ti. Ábrelo a todo el espacio de trabajo, o a algunas personas y roles, al permitirlo o más adelante en su página de **Configuración\u00a0›\u00a0Dispositivos**.",
        ],
      },
      {
        id: "open",
        heading: "Mientras la app está abierta",
        blocks: [
          "Los agentes trabajan en una computadora solo mientras la app funciona allí y la computadora está despierta. Cuando la app pregunte, deja marcada **Tenerla lista: abrir VortoOne al iniciar sesión**. Una computadora apagada o en reposo aparece como **Sin conexión**, y los agentes esperan a que vuelva.",
        ],
      },
      {
        id: "pausing",
        heading: "Pausar y desconectar",
        blocks: [
          "En la computadora, el ícono de VortoOne junto al reloj pausa a los agentes de todos los espacios de trabajo, o de uno solo. En **Preferencias\u00a0›\u00a0General**, el interruptor de cada espacio de trabajo pausa solo ese, y **Salir**, en su menú **⋯**, lo desconecta. No empieza nada nuevo mientras está en pausa, y lo que estaba en marcha termina. Al desconectar, las carpetas de los agentes se quedan en el disco.",
          "Desde el sitio web, **Quitar** en la página del dispositivo lo desconecta.",
        ],
      },
      {
        id: "organizations",
        heading: "Varios espacios de trabajo, una computadora",
        blocks: [
          "Inicia sesión en cada espacio de trabajo en la misma app y pulsa **Permitir** para cada uno. Los espacios de trabajo en VortoOne Cloud y en tus propios servidores pueden compartir una computadora (app 0.6.1 y posteriores). Cada uno tiene allí sus propios agentes y carpetas, y desconectar uno no afecta a los demás.",
          "Un servidor que la app ejecuta en esta computadora ya está conectado y no necesita nada. Para prestar también esa computadora a un espacio de trabajo de otro lugar, consulta [Préstala también a otro espacio de trabajo](/help/run-on-your-computer#another-workspace).",
          "¿Usas la app en una laptop solo como ventana a tu espacio de trabajo? Cuando te pida dejar que los agentes usen la laptop, **Ahora no** la deja como una simple ventana.",
          "Un servidor conectado con la línea de la terminal trabaja para un solo workspace. Conectarlo desde otro workspace pasa el servidor a ese otro, y el primero deja de alcanzarlo. Para varios workspaces en una computadora, usa la app.",
        ],
      },
      {
        id: "line",
        heading: "Cuando los agentes esperan en la fila",
        blocks: [
          "Cada trabajo de un agente usa la memoria de la computadora. Cuando queda poca memoria libre, el siguiente espera en la fila hasta que otro termine. Las personas van primero. El número de CPU no cambia esto.",
          "Una computadora con 4 GB de memoria ejecuta dos o tres trabajos a la vez, una con 8 GB unos nueve. Un Mac o un PC, hasta ocho. Los navegadores y escritorios que los agentes dejan abiertos también usan memoria. Para una fila más corta, usa una computadora con más memoria, o conecta otra y reparte los agentes entre ellas. Los workspaces que comparten una computadora comparten su memoria.",
        ],
      },
      {
        id: "folders",
        heading: "Carpetas",
        blocks: [
          "Cada agente siempre tiene su propia carpeta, en `~/vortoone/<workspace>`. Se pueden permitir más carpetas en la propia computadora, nunca desde el sitio web: en la app, en **Preferencias › Carpetas**, o con `vortoone-runner folders allow` en un servidor. Cada espacio de trabajo permite sus propias carpetas: lo que permites para uno solo vale para sus agentes. Quitar una carpeta no borra nada. Solo retira el acceso.",
        ],
      },
      {
        id: "reach",
        heading: "A qué pueden llegar los agentes",
        blocks: [
          "Los agentes se ejecutan como tú, el usuario de la computadora. Sus herramientas de archivos se quedan en sus carpetas, pero un comando puede leer todo lo que tú puedes leer:",
          {
            list: [
              "tus archivos y tus sesiones iniciadas",
              "las carpetas de otros agentes, incluidas las de otros espacios de trabajo, y las sesiones que guardan sus navegadores",
              "el token que conecta esta computadora",
              "en una computadora que también ejecuta VortoOne, su base de datos y sus claves en `.vortoone`",
            ],
          },
          "VortoOne rechaza los comandos que nombran sus claves, tokens e inicios de sesión, pero es una regla, no un muro. Los **Comandos** vienen activados. Desactívalos en la pregunta de la app antes de pulsar **Permitir**, o más tarde en **Preferencias\u00a0›\u00a0Herramientas**, donde cada espacio de trabajo tiene su propio interruptor (app 0.8.1 y posteriores) y la app pregunta antes de volver a activar uno. Claude Code y Codex ejecutan sus propios comandos en la carpeta del agente, con sus propias reglas, incluso con los comandos desactivados.",
          "Para un muro de verdad, usa un servidor Linux. En Debian 13, instalado con `--system`, cada agente es un usuario propio. En Ubuntu, los agentes comparten un usuario, `vortoone`: ven las carpetas de los demás, no las tuyas ni las del servidor. O dales a los agentes una computadora propia.",
        ],
      },
    ],
  },
  import: {
    title: "Trae a tus agentes",
    summary:
      "Trae agentes de otra app, o de otro espacio de trabajo de VortoOne, con sus instrucciones, habilidades, rutinas y memoria.",
    sections: [
      {
        id: "comes",
        heading: "Qué se trae",
        blocks: [
          {
            list: [
              "Los agentes, con su nombre, rol, instrucciones y apariencia.",
              "Las habilidades, activadas para los agentes que las tenían.",
              "Los canales públicos, con los agentes que están en ellos.",
              "Las rutinas, activas y en su horario. Pausa las que quieras en **Rutinas**.",
              "La memoria: lo que recuerda cada agente.",
              "Los archivos propios de cada agente (la carpeta de archivos de OpenBot.run, un workspace de OpenClaw), en una carpeta con su nombre en el **Drive**. Las claves, los inicios de sesión y los archivos del sistema se quedan fuera, y no se reemplaza nada de lo que ya estaba.",
            ],
          },
        ],
      },
      {
        id: "never",
        heading: "Qué nunca se trae",
        blocks: [
          "Las claves, las contraseñas, los inicios de sesión y el historial de chat nunca se traen: los agentes piensan con la IA del espacio de trabajo. Algunas cosas todavía no tienen equivalente aquí, o nunca deberían viajar en un archivo. Puedes volver a crearlas después.",
          "Los archivos y las carpetas se leen en tu navegador. Un enlace de GitHub pasa por el servidor del espacio de trabajo. Los archivos de claves e inicios de sesión nunca se abren, y no cambia nada hasta que confirmes.",
        ],
      },
      {
        id: "mcp",
        heading: "Conectar con MCP",
        blocks: [
          "MCP permite que tu app de IA acceda a este espacio de trabajo, así que crea los agentes aquí por sí sola, con sus instrucciones, habilidades y rutinas. Elige tu app, marca **Conectar con MCP** y luego:",
          {
            list: [
              "Conecta la dirección que muestra el paso, con los pasos de la propia app. [Cómo conectar, app por app](/help/apps-de-ia#connect)",
              "En la página de inicio de sesión, elige **Crear y editar agentes, rutinas y habilidades**.",
              "Pega el pedido en la app. Los agentes aparecen en el paso a medida que llegan.",
            ],
          },
          "Si una app no se puede conectar, elige **Desde un archivo** o **Solo el pedido**, lo que muestre el paso. [Desde otra app](/help/import#other-apps) explica el pedido.",
        ],
      },
      {
        id: "other-apps",
        heading: "Desde otra app",
        blocks: [
          "Elige **Grok Bot**, **ChatGPT dots** u **Otra app**. Copia la solicitud, pégala en esa app y luego trae lo que te devuelva: suelta el archivo o pega la respuesta completa. Dónde lo pone cada app:",
          {
            list: [
              "**Grok Bot**: en el chat con tu bot. Responde con el JSON: copia la respuesta completa.",
              "**ChatGPT dots**: en un chat nuevo. Descarga el archivo que genera.",
              "**Claude**: en una conversación nueva, o en el proyecto de tus agentes. Descarga el archivo que genera.",
              "**Manus**: en una tarea nueva. El archivo está en los resultados de la tarea.",
              "**Rakazo**: envíasela a tu agente. Responde con el JSON: copia la respuesta completa.",
              "**OpenBot**: no hace falta una solicitud. Arrastra la carpeta del tenant (agents.yaml, agents/, model.yaml, skills.yaml) o pega su enlace de GitHub.",
            ],
          },
        ],
      },
      {
        id: "export",
        heading: "Exportar un espacio de trabajo",
        blocks: [
          "**Configuración › Importar y exportar › Exportar** descarga un archivo .json, para llevar a tus agentes a otro espacio de trabajo o guardar una copia. En el archivo:",
          {
            list: [
              "Los agentes, con sus instrucciones, modelo y apariencia",
              "Las habilidades, con el SKILL.md completo",
              "Los canales públicos, con sus agentes",
              "Las rutinas programadas (llegan activas al otro lado)",
              "La memoria que ve todo el espacio de trabajo",
            ],
          },
          "Nunca incluye:",
          {
            list: [
              "Claves de API y contraseñas",
              "Conectores e inicios de sesión",
              "Computadoras",
              "Conversaciones, mensajes y canales privados",
              "Uso, costos e historial de actividad",
            ],
          },
          "Para usar el archivo, abre **Configuración › Importar y exportar** en el otro espacio de trabajo y suelta el .json.",
        ],
      },
    ],
  },
  wiki: {
    title: "La wiki",
    summary: "Lo que sabe la empresa, en páginas enlazadas entre sí.",
    sections: [
      {
        id: "links",
        heading: "Enlaces",
        blocks: [
          "Escribe [[ para enlazar otra página. Los enlaces entrantes y el grafo muestran cómo se conectan las páginas.",
        ],
      },
      {
        id: "agents",
        heading: "Cómo la usan los agentes",
        blocks: ["Los agentes leen la wiki cuando responden. Solo escriben en ella cuando alguien se lo pide."],
      },
      {
        id: "obsidian",
        heading: "Trae una bóveda de Obsidian",
        blocks: [
          "Suelta la carpeta de la bóveda, o un .zip de ella, en el paso Contexto de la configuración o en la importación de la propia wiki. Los [[enlaces]], [[nota|alias]] y [[nota#sección]] siguen funcionando. Las etiquetas salen del frontmatter y del texto. Los adjuntos van al Drive, y las imágenes y archivos incrustados se convierten en tarjetas en la página. Los callouts siguen siendo callouts. Las consultas de Dataview llegan como texto: pídele a un agente que arme la tabla. Los lienzos y la configuración de Obsidian (.obsidian) se quedan fuera.",
        ],
      },
    ],
  },
  memory: {
    title: "Memoria",
    summary: "Lo que cada agente aprende de las conversaciones y de las correcciones del equipo.",
    sections: [
      {
        id: "learns",
        heading: "Cómo aprenden los agentes",
        blocks: [
          "Los agentes aprenden de las conversaciones y de las correcciones del equipo. Lo que sabe un agente entra en su contexto cuando es relevante. En su pestaña **Memoria**, edita, fija o archiva lo que esté mal o desactualizado.",
        ],
      },
      {
        id: "permissions",
        heading: "La memoria no da permisos",
        blocks: [
          "Nada en la memoria le permite a un agente hacer más. Lo que puede hacer sigue en sus **Herramientas y acceso**.",
        ],
      },
      {
        id: "proposals",
        heading: "Cambios propuestos",
        blocks: [
          "Cuando una lección se repite, el agente puede proponer cambiar sus propias instrucciones o el contexto del espacio de trabajo. No cambia nada hasta que alguien acepte, y antes se puede ajustar el texto. La versión anterior queda en el historial. Una propuesta rechazada se guarda como lección, para que no se vuelva a proponer.",
        ],
      },
    ],
  },
  approvals: {
    title: "Aprobaciones",
    summary: "Las acciones delicadas esperan a una persona, una vez o para siempre.",
    sections: [
      {
        id: "what",
        heading: "Qué pide permiso antes",
        blocks: [
          "Cuando un agente quiere hacer un merge, un deploy, revertir algo, enviar algo hacia afuera o borrar datos, primero pregunta. Ese es el nivel **Solo lo arriesgado**, con el que empieza todo agente. En la página del agente, en **Aprobaciones**, **Preguntar siempre** hace que pregunte antes de cambiar cualquier cosa, y **No preguntar nunca** hace que no pregunte nada, tampoco en merge, deploy, pagos y rutinas. La solicitud aparece en **Aprobaciones** y en la conversación, con la acción exacta y el motivo.",
        ],
      },
      {
        id: "always",
        heading: "Una vez, o siempre",
        blocks: [
          "Aprobar cubre una acción exacta, con sus valores. Si el agente cambia algo, vuelve a preguntar.",
          "**Aprobar siempre** deja que el agente haga ese tipo de acción de ahora en adelante, con cualquier valor, sin preguntar. En la solicitud de una rutina, **Siempre en esta rutina** hace lo mismo solo para esa rutina. **Aprobar siempre** aparece para cualquier tipo de acción, también merge y deploy, mientras el agente esté en **Solo lo arriesgado**. **Siempre en esta rutina** no aparece para merge, deploy, rollback y similares. Para deshacerlo, ve a **Herramientas y acceso** en la página del agente, o a los ajustes de la rutina.",
        ],
      },
      {
        id: "who",
        heading: "Quién aprueba",
        blocks: [
          {
            list: [
              "El responsable del agente, en sus solicitudes.",
              "Quien tenga un rol con **Aprobar acciones de los agentes**, en todos los agentes.",
              "Las personas que el agente nombra al pedir, en lugar de su responsable.",
              "Quien cubre a alguien en **Quién aprueba por mí**, en lo que se le pide a esa persona. Merge, deploy y otras acciones de riesgo solo cuando eso está activado.",
            ],
          },
          "Una acción en el correo de alguien espera solo a esa persona. Un acceso nuevo para un agente espera a quien gestiona agentes.",
          "Lodi, tu mano derecha, también puede aprobar por ti los agentes nuevos que proponen otros agentes y los cambios de modelo, presupuesto y canales de un agente, solo cuando tú lo pediste. Quién te cubre y lo que aprueba Lodi se eligen en **Configuración\u00a0›\u00a0Tú\u00a0›\u00a0Aprobaciones**.",
        ],
      },
      {
        id: "refusing",
        heading: "Rechazar",
        blocks: ["Di por qué, si quieres: el agente lo lee, ajusta su plan y lo guarda como lección."],
      },
      {
        id: "meetings",
        heading: "Reuniones",
        blocks: ["Los agentes solo sugieren horarios. Las invitaciones a personas siempre pasan por quien lo pidió."],
      },
    ],
  },
  "who-answers": {
    title: "Quién responde un mensaje",
    summary: "Una mención elige al agente. Sin ella, VortoOne elige por ti, en este orden.",
    sections: [
      {
        id: "order",
        heading: "Cómo un mensaje encuentra a su agente",
        blocks: [
          "Un mensaje que @menciona a un agente va a ese agente. Un mensaje que no menciona a nadie lo lee el primero de estos que tenga el espacio de trabajo:",
          {
            list: [
              "**Jev**, un modelo pequeño y rápido hecho para decisiones como esta.",
              "**Un modelo pequeño con una de las claves de API del espacio de trabajo**, como Claude Haiku 5.5 o GPT-6 Luna.",
              "**Tu propio plan de Claude o ChatGPT**, para los mensajes que escribes tú, en un servidor que administras tú. Los lee en la computadora donde tiene la sesión iniciada, y tarda unos segundos.",
              "**Reglas simples**: una respuesta a un agente va a ese agente, igual que un mensaje justo después de hablar con él, o un mensaje en un canal donde estás a solas con un agente.",
            ],
          },
          "Cuando nada de esto aplica, ningún agente responde. Menciona a un agente para llamarlo.",
        ],
      },
      {
        id: "jev",
        heading: "Jev",
        blocks: [
          "Cuando VortoOne aloja tu espacio de trabajo, Jev viene incluido. En un servidor que administras tú, usa la clave de TypeSafe del propietario, unos US$ 0.0001 por mensaje: [Configura Jev](/help/jev#setup).",
        ],
      },
      {
        id: "plans",
        heading: "Por qué tu plan elige solo para tus mensajes",
        blocks: [
          "Los planes de Claude y ChatGPT son personales, así que tu plan elige solo para los mensajes que escribes tú.",
        ],
      },
      {
        id: "see",
        heading: "Ver quién eligió",
        blocks: [
          "Abre la marca de verificación debajo de un mensaje. Su última línea dice quién eligió a los agentes.",
        ],
      },
    ],
  },
  jev: {
    title: "Jev",
    summary:
      "Un modelo veloz de TypeSafe que toma decisiones rápidas para tus agentes. En tu propio servidor es opcional, con una clave tuya.",
    sections: [
      {
        id: "what",
        heading: "Qué hace",
        blocks: [
          {
            list: [
              "**Elige quién responde.** Un mensaje que no @menciona a nadie llega al agente correcto en menos de un segundo, incluso cuando tus agentes piensan con un plan y no tienes clave de API.",
              "**Mantiene la memoria ordenada.** Detecta una repetición o un dato más reciente aunque esté dicho con otras palabras: la repetición se une a lo que el agente ya sabía, y el dato más reciente reemplaza al anterior. Una vez al día ordena el resto.",
              "**Ve la pantalla.** En su navegador y su escritorio, los agentes encuentran botones y campos a partir de una descripción, miran más de cerca los pequeños y comprueban en la imagen de la pantalla que un paso funcionó.",
              "**Lee menos.** Cada turno carga solo las herramientas que el agente probablemente necesite, así tu IA lee menos y cuesta menos.",
            ],
          },
        ],
      },
      {
        id: "without",
        heading: "Sin Jev",
        blocks: [
          "Todo sigue funcionando, más lento y con menos cuidado.",
          {
            list: [
              "**Quién responde:** elige un modelo pequeño con una de tus claves de API, como Claude Haiku 5.5 o GPT-6 Luna. Sin clave, el propio plan de Claude o ChatGPT de cada persona elige para los mensajes que escribe, en unos segundos. Si no, una respuesta o un mensaje de seguimiento igual llega a su agente. Menciona a un agente para llamarlo. [Cómo se elige a los agentes](/help/who-answers)",
              "**Memoria:** las repeticiones con las mismas palabras se siguen uniendo. Las repeticiones y actualizaciones con otras palabras quedan separadas.",
              "**Pantalla:** los agentes usan los números que aparecen en la pantalla y los nombres exactos. No se lee nada de las imágenes.",
            ],
          },
        ],
      },
      {
        id: "setup",
        heading: "Configúralo",
        blocks: [
          {
            list: [
              "Abre https://console.typesafe.ai e inicia sesión con Google o tu correo. Una cuenta nueva se crea de la misma forma.",
              "Agrega saldo en la consola. Jev es prepago.",
              "Abre https://console.typesafe.ai/keys, crea una clave y cópiala de inmediato.",
              "En VortoOne, abre **Configuración › IA y uso** y pégala en **Jev**. Solo la persona que configuró este servidor la ve.",
            ],
          },
          "En un servidor que ejecutas con Docker, también puedes poner la clave en `.env` como `TYPESAFE_API_KEY`. Esa tiene prioridad sobre la de Configuración.",
        ],
      },
      {
        id: "cost",
        heading: "Cuánto cuesta",
        blocks: [
          {
            list: [
              "US$ 0.042 por millón de tokens que lee Jev. Sus respuestas son gratis. Elegir quién responde lee unos 2500 tokens, es decir, unos US$ 0.0001. Una captura de pantalla son unos 3900 tokens.",
              "El saldo que compras dura 12 meses. Activa las recargas automáticas en la consola para que Jev no se quede sin saldo.",
              "**Configuración › IA y uso** muestra cuántas decisiones tomó Jev este mes y cuánto costaron.",
            ],
          },
        ],
      },
      {
        id: "data",
        heading: "Tus datos",
        blocks: [
          "Lo que Jev lee para decidir:",
          {
            list: [
              "El nombre y la descripción del canal, el mensaje y los pocos anteriores",
              "Los nombres y roles de tus agentes, el comienzo de sus instrucciones y las herramientas que pueden usar",
              "Los elementos de memoria que compara",
              "La tarea de un agente y el comienzo de los resultados de sus herramientas",
              "El objetivo de una misión y los comandos que un agente quiere ejecutar para ella",
              "Las herramientas que ofrece una app conectada",
              "La página o pantalla que usa un agente, capturas de pantalla incluidas",
            ],
          },
          "TypeSafe ejecuta Jev en Estados Unidos y no entrena sus modelos con lo que envías. Guarda los datos mientras los necesite para prestar el servicio. Sus planes empresariales ofrecen retención cero.",
          "La política de privacidad de TypeSafe: https://typesafe.ai/legal/privacy-policy. Su acuerdo de tratamiento de datos: https://typesafe.ai/legal/data-processing.",
        ],
      },
      {
        id: "problems",
        heading: "Cuando deja de funcionar",
        blocks: [
          {
            list: [
              "**Clave no aceptada:** crea una clave nueva y pégala en **Configuración › IA y uso**.",
              "**Sin saldo:** agrega saldo en la consola. Jev vuelve a intentarlo en menos de 30 minutos, o haz clic en **Comprobar de nuevo**.",
              "**Ocupado o inaccesible:** TypeSafe puede limitar las solicitudes por segundo, o dejar de responder por un rato. Jev se recupera solo. Tus límites: https://console.typesafe.ai/rate-limits. Para límites más altos, escribe a sales@typesafe.ai.",
            ],
          },
          "Mientras tanto, los agentes trabajan como en [Sin Jev](/help/jev#without).",
        ],
      },
    ],
  },
  "apps-de-ia": {
    title: "Apps de IA",
    summary:
      "Pide tareas a los agentes de VortoOne desde Claude, Cursor u otra app MCP, sin salir de ella, y recibe la respuesta ahí mismo.",
    sections: [
      {
        id: "what",
        heading: "Cómo funciona",
        blocks: [
          "VortoOne es un servidor MCP. Pegas su dirección en tu app de IA, inicias sesión con tu cuenta y eliges un espacio de trabajo. A partir de ahí, la app puede listar agentes y canales, enviar una tarea a un agente en tu nombre, esperar la respuesta y leer las tareas recientes.",
          'Por ejemplo, en medio de una escena en Unity o Blender, pídele a Claude: "Pídele al agente de marketing un texto para el lanzamiento". El mensaje aparece en VortoOne como tuyo, marcado **vía Claude**, y la respuesta del agente vuelve a Claude.',
          "La dirección está en **Configuración › Apps de IA**. Termina en `/mcp`.",
        ],
      },
      {
        id: "connect",
        heading: "Conecta una app",
        blocks: [
          {
            list: [
              "**Claude** (claude.ai, Claude Desktop y el teléfono): en **Personalizar › Conectores**, haz clic en **+ Agregar**, luego en **Agregar conector personalizado**, llámalo VortoOne, pega la dirección y haz clic en **Continuar**. Elige **Iniciar sesión ahora**, haz clic en **Agregar** y autoriza. En los planes Team y Enterprise, el propietario de la organización de Claude agrega el conector en Configuración de la organización › Conectores, y cada persona hace clic en **Conectar** en Personalizar › Conectores. El plan gratuito de Claude tiene lugar para un conector personalizado.",
              "**Claude Code:** ejecuta `claude mcp add --transport http --scope user agentes <address>`, luego escribe `/mcp`, elige agentes y **Authenticate**. Solo para la carpeta actual, quita `--scope user`.",
              "**Cursor:** **Agregar a Cursor** en **Configuración › Apps de IA**, o la dirección en `~/.cursor/mcp.json`. Cursor te pide iniciar sesión la primera vez que lo usas.",
              '**VS Code:** **Agregar a VS Code**, o la dirección en `.vscode/mcp.json` con `"type": "http"`.',
              "**Otras apps:** ChatGPT, Gemini, Grok, Perplexity, Codex, Zed, JetBrains y más tienen cada una sus pasos en **Conectar una app**, con tu dirección ya puesta.",
            ],
          },
          "Cuando inicias sesión, VortoOne muestra qué app pidió acceso y adónde vuelve. Continúa solo si acabas de iniciar la conexión.",
        ],
      },
      {
        id: "tools",
        heading: "Qué puede hacer la app",
        blocks: [
          {
            list: [
              "`send_to_agent`: envía un mensaje tuyo a un agente, en tu conversación privada con él o en un canal, y espera la respuesta. Solo con el acceso **Leer y enviar tareas a los agentes**.",
              "`wait_for_reply`: sigue esperando la respuesta de una tarea.",
              "`list_agents`: los agentes que ves.",
              "`list_channels`: los canales que lees.",
              "`read_conversation`: los mensajes recientes de un canal, un hilo o una tarea.",
              "`list_recent_tasks`: las tareas que enviaste desde apps y lo que los agentes están haciendo ahora.",
            ],
          },
          "La app actúa en tu nombre y solo ve lo que tú ves. Nunca aprueba las acciones de los agentes: cuando un agente se detiene para pedir aprobación, la respuesta lo dice con un enlace, y una persona decide en VortoOne. Tampoco cambia la configuración, los miembros ni los permisos.",
        ],
      },
      {
        id: "workspace",
        heading: "Un espacio de trabajo por conexión",
        blocks: [
          "Cada conexión cubre el espacio de trabajo que se eligió al iniciar sesión. Para usar otro espacio de trabajo, vuelve a conectar la app y elige el otro.",
          "En **Configuración › Apps de IA** ves las apps conectadas, cuándo se usó cada una, y puedes desconectar cualquiera. Quienes administran los miembros ven las apps de todo el espacio de trabajo y también pueden desconectarlas. Cada mensaje enviado desde una app aparece en la Auditoría.",
        ],
      },
      {
        id: "tokens",
        heading: "Tokens personales",
        blocks: [
          "Para scripts y apps sin inicio de sesión con OAuth, crea un token personal en **Configuración › Apps de IA** y envíalo en el encabezado `Authorization: Bearer`. El token se muestra una sola vez, solo funciona en el espacio de trabajo donde se creó, y puede vencer en 30 días, 90 días, 1 año o nunca.",
          "En un servidor sin https, lejos de su propia computadora, usa un token personal: el inicio de sesión con OAuth necesita https.",
        ],
      },
      {
        id: "limits",
        heading: "Límites",
        blocks: [
          {
            list: [
              "Hasta 120 solicitudes por minuto y 5000 por día por conexión.",
              "Hasta 20 mensajes por minuto y 500 por día por persona en cada espacio de trabajo.",
              "Una espera dura hasta 45 segundos. Si el agente sigue trabajando, la app vuelve a esperar.",
            ],
          },
        ],
      },
      {
        id: "safety",
        heading: "Texto de otras personas",
        blocks: [
          "Las respuestas traen texto escrito por agentes y personas en VortoOne. La app recibe ese texto marcado como contenido, no como instrucciones, pero un texto aun así puede intentar convencer a la app de hacer otra cosa. Revisa lo que tu app está por hacer antes de confirmar.",
        ],
      },
      {
        id: "problems",
        heading: "Cuando no se conecta",
        blocks: [
          {
            list: [
              "**Solicitud vencida:** la página de inicio de sesión dura 30 minutos y funciona una sola vez. Vuelve a la app y conéctala de nuevo.",
              "**La app pide iniciar sesión otra vez:** se desconectó la conexión, cambió la contraseña o saliste del espacio de trabajo. Vuelve a iniciar sesión desde la app.",
              "**Cursor se quedó con un inicio de sesión viejo:** cierra Cursor, borra la carpeta `~/.mcp-auth` y conéctalo de nuevo.",
            ],
          },
        ],
      },
    ],
  },
  "install-on-a-server": {
  "title": "Instala VortoOne en un servidor",
  "summary": "Usa el instalador y las instrucciones de conexión de la versión actual de VortoOne Desktop. Esta versión no ofrece un servicio de nube alojado ni instalación de servidor con un comando.",
  "sections": []
},
};
