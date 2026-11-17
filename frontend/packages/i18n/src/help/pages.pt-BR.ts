import type { HelpSlug } from "./index";
import type { HelpDocument } from "./types";

export const helpPtBR: Record<HelpSlug, HelpDocument> = {
  "run-on-your-computer": {
  "title": "Rodar o VortoOne no seu computador",
  "summary": "Use o instalador e as instruções de conexão da versão atual do VortoOne Desktop. Esta versão não oferece serviço de nuvem hospedado nem instalação de servidor com um comando.",
  "sections": []
},
  "workspace-computer": {
  "title": "O computador do workspace",
  "summary": "Use o instalador e as instruções de conexão da versão atual do VortoOne Desktop. Esta versão não oferece serviço de nuvem hospedado nem instalação de servidor com um comando.",
  "sections": []
},
  computers: {
    title: "Seus computadores e celulares",
    summary:
      "Traga um Mac, um PC, um computador com Linux, um servidor ou um celular, e os agentes trabalham nele. Tanto no VortoOne Cloud quanto num servidor próprio.",
    sections: [
      {
        id: "app",
        heading: "Pelo app do VortoOne",
        blocks: [
          "Instale o app no computador, abra e entre no seu workspace. Ele pergunta **Deixar os agentes de <workspace> usarem este Mac?** (este PC no Windows, este computador no Linux). Escolha quem pode usar e aperte **Permitir**. Só isso.",
          "Já entrou? Abra **Preferências\u00a0›\u00a0Geral** e aperte **Permitir** na linha do workspace, no primeiro cartão. Não está lá? Aperte **Adicionar um workspace…** no fim desse cartão e entre.",
        ],
      },
      {
        id: "web",
        heading: "Pelo site",
        blocks: [
          "Em **Configurações\u00a0›\u00a0Dispositivos**, aperte **Conectar um dispositivo**:",
          {
            list: [
              "**Este Mac** ou **Este PC**: abre o app, que pergunta antes.",
              "**Outro computador**: instale o app nele, entre com a sua conta e aperte **Permitir**. Ele aparece aqui sozinho.",
              "**Um servidor ou VPS**: rode uma linha no terminal dele e depois deixe ele entrar. A linha vale uma vez, por 10 minutos.",
            ],
          },
          "O app se conecta com um clique só a um endereço com https, como um do Tailscale terminado em `.ts.net`. Num endereço http simples, use a linha do terminal.",
        ],
      },
      {
        id: "phones",
        heading: "Celulares",
        blocks: [
          "Um celular Android se conecta por qualquer computador já conectado: por cabo ou, no Android 14 ou mais novo com a atualização de segurança de maio de 2026, pelo Wi-Fi. Um iPhone se conecta por um Mac com o app e o Xcode, e o app prepara o resto. Os dois começam em **Conectar um dispositivo**.",
        ],
      },
      {
        id: "who",
        heading: "Quem pode usar",
        blocks: [
          "Um dispositivo novo é só seu: **Só eu** quer dizer você e os agentes que trabalham para você. Abra para todo o workspace, ou para algumas pessoas e cargos, na hora de permitir ou depois, na página dele em **Configurações\u00a0›\u00a0Dispositivos**.",
        ],
      },
      {
        id: "open",
        heading: "Enquanto o app está aberto",
        blocks: [
          "Os agentes trabalham num computador só enquanto o app roda nele e o computador está acordado. Quando o app perguntar, deixe marcado **Deixar pronto: abrir o VortoOne ao iniciar a sessão**. Um computador desligado ou em repouso aparece como **Offline**, e os agentes esperam ele voltar.",
        ],
      },
      {
        id: "pausing",
        heading: "Pausar e desconectar",
        blocks: [
          "No computador, o ícone do VortoOne perto do relógio pausa os agentes de todos os workspaces, ou de um só. Em **Preferências\u00a0›\u00a0Geral**, a chave de cada workspace pausa só aquele, e **Sair**, no menu **⋯** dele, desconecta. Nada novo começa enquanto está pausado, e o que já rodava termina. Desconectar mantém as pastas dos agentes no disco.",
          "Pelo site, **Remover** na página do dispositivo desconecta ele.",
        ],
      },
      {
        id: "organizations",
        heading: "Vários workspaces, um computador",
        blocks: [
          "Entre em cada workspace no mesmo app e aperte **Permitir** para cada um. Workspaces no VortoOne Cloud e em servidores próprios podem dividir um computador (app 0.6.1 ou mais novo). Cada um tem ali os próprios agentes e pastas, e desconectar um não mexe nos outros.",
          "Um servidor que o app roda no próprio computador já está conectado e não precisa de nada. Para emprestar esse computador também a um workspace de outro lugar, veja [Emprestar para outro workspace também](/help/run-on-your-computer#another-workspace).",
          "Usa o app num notebook só como uma janela para o workspace? Quando ele pedir para deixar os agentes usarem o notebook, **Agora não** mantém só a janela.",
          "Um servidor conectado pela linha do terminal atende um workspace só. Conectar ele a partir de outro workspace passa o servidor para esse outro, e o primeiro deixa de alcançá-lo. Para vários workspaces num computador, use o app.",
        ],
      },
      {
        id: "line",
        heading: "Quando os agentes esperam na fila",
        blocks: [
          "Cada trabalho de um agente usa a memória do computador. Quando a memória livre fica curta, o próximo espera na fila até outro terminar. Pessoas vão primeiro. O número de CPUs não muda isso.",
          "Um computador com 4 GB de memória roda dois ou três trabalhos ao mesmo tempo, um com 8 GB cerca de nove. Um Mac ou um PC roda até oito. Navegadores e áreas de trabalho que os agentes deixam abertos também usam memória. Para uma fila menor, use um computador com mais memória, ou conecte outro e divida os agentes entre eles em **Configurações\u00a0›\u00a0Dispositivos**. Workspaces que dividem um computador dividem a memória dele.",
        ],
      },
      {
        id: "folders",
        heading: "Pastas",
        blocks: [
          "Cada agente sempre tem a própria pasta, em `~/agentes/<workspace>`. Pastas a mais se liberam no próprio computador, nunca pelo site: no app, em **Preferências\u00a0›\u00a0Pastas**, ou com `agentes-runner folders allow` num servidor. Cada workspace libera as próprias pastas: o que você libera para um vale só para os agentes dele. Remover uma pasta não apaga nada. Só tira o acesso.",
        ],
      },
      {
        id: "reach",
        heading: "O que os agentes alcançam",
        blocks: [
          "Os agentes rodam como você, o usuário do computador. As ferramentas de arquivo deles ficam nas pastas deles, mas um comando lê tudo o que você lê:",
          {
            list: [
              "os seus arquivos e logins",
              "as pastas de outros agentes, inclusive as de outros workspaces, e os logins que os navegadores deles guardam",
              "o token que conecta o computador",
              "num computador que também roda o VortoOne, o banco de dados e as chaves dele em `.agentes`",
            ],
          },
          "O VortoOne recusa comandos que citam as chaves, os tokens e os logins, mas isso é uma regra, não uma parede. Os **Comandos** já vêm ligados. Desligue na pergunta do app antes de apertar **Permitir**, ou depois em **Preferências\u00a0›\u00a0Ferramentas**, onde cada workspace tem a própria chave (app 0.8.1 ou mais novo) e o app pergunta antes de ligar de novo. O Claude Code e o Codex rodam os próprios comandos na pasta do agente, com as próprias regras, mesmo com Comandos desligado.",
          "Para uma parede de verdade, use um servidor Linux. No Debian 13, instalado com `--system`, cada agente é um usuário só dele. No Ubuntu, os agentes dividem um usuário, `agentes`: eles veem as pastas uns dos outros, não as suas nem as do servidor. Ou dê aos agentes um computador só deles.",
        ],
      },
    ],
  },
  import: {
    title: "Trazer os seus agentes",
    summary:
      "Traga agentes de outro app, ou de outro workspace do VortoOne, com instruções, skills, rotinas e memória.",
    sections: [
      {
        id: "comes",
        heading: "O que vem junto",
        blocks: [
          {
            list: [
              "Agentes, com nome, função, instruções e aparência.",
              "Skills, ligadas para os agentes que as tinham.",
              "Canais públicos, com os agentes dentro.",
              "Rotinas, ativas e no horário delas. Dá para pausar em **Rotinas** quando quiser.",
              "Memória: o que cada agente lembra.",
              "Os arquivos de cada agente (a pasta files do OpenBot.run, o workspace do OpenClaw), numa pasta com o nome dele no **Drive**. Chaves, logins e arquivos do sistema ficam de fora, e o que já estiver lá não é substituído.",
            ],
          },
        ],
      },
      {
        id: "never",
        heading: "O que nunca vem",
        blocks: [
          "Chaves, senhas, logins e histórico de conversa nunca vêm: os agentes pensam com a IA do workspace. Algumas coisas ainda não têm equivalente aqui, ou nunca devem viajar num arquivo. Dá para recriar depois.",
          "Arquivos e pastas são lidos no seu navegador. Um link do GitHub passa pelo servidor do workspace. Arquivos de chaves e de login nunca são abertos, e nada muda até você confirmar.",
        ],
      },
      {
        id: "mcp",
        heading: "Conectar com MCP",
        blocks: [
          "O MCP deixa o seu app de IA acessar este workspace, e ele cria os agentes aqui sozinho, com instruções, skills e rotinas. Escolha o seu app, marque **Conectar com MCP** e então:",
          {
            list: [
              "Conecte o endereço que o passo mostra, nos passos do próprio app. [Como conectar, app por app](/help/apps-de-ia#connect)",
              "Na página de entrada, escolha **Criar e editar agentes, rotinas e skills**.",
              "Cole o pedido no app. Os agentes aparecem no passo conforme chegam.",
            ],
          },
          "Quando um app não conecta, escolha **Por arquivo** ou **Só o pedido**, o que o passo mostrar. [De outro app](/help/import#other-apps) explica o pedido.",
        ],
      },
      {
        id: "other-apps",
        heading: "De outro app",
        blocks: [
          "Escolha **Grok Bot**, **ChatGPT dots** ou **Outro app**. Copie o pedido, cole nesse app e traga o que ele devolver: solte o arquivo ou cole a resposta inteira. Onde cada app entrega:",
          {
            list: [
              "**Grok Bot**: na conversa com o seu bot. Ele responde com o JSON: copie a resposta inteira.",
              "**ChatGPT dots**: num chat novo. Baixe o arquivo que ele criar.",
              "**Claude**: numa conversa nova, ou no projeto dos seus agentes. Baixe o arquivo que ele criar.",
              "**Manus**: numa tarefa nova. O arquivo fica nos resultados da tarefa.",
              "**Rakazo**: mande para o seu agente. Ele responde com o JSON: copie a resposta inteira.",
              "**OpenBot**: sem pedido. Arraste a pasta do tenant (agents.yaml, agents/, model.yaml, skills.yaml) ou cole o link do GitHub dela.",
            ],
          },
        ],
      },
      {
        id: "export",
        heading: "Exportar um workspace",
        blocks: [
          "**Configurações\u00a0›\u00a0Importar e exportar\u00a0›\u00a0Exportar** baixa um arquivo .json, para levar os agentes a outro workspace ou guardar uma cópia. Vai no arquivo:",
          {
            list: [
              "Agentes, com instruções, modelo e aparência",
              "Skills, com o SKILL.md inteiro",
              "Canais públicos, com os agentes de cada um",
              "Rotinas agendadas (chegam ativas do outro lado)",
              "A memória que todo mundo no workspace pode ver",
            ],
          },
          "Nunca vai:",
          {
            list: [
              "Chaves de API e senhas",
              "Conectores e logins",
              "Computadores",
              "Conversas, mensagens e canais privados",
              "Uso, custos e histórico de ações",
            ],
          },
          "Para usar o arquivo, abra **Configurações\u00a0›\u00a0Importar e exportar** no outro workspace e solte o .json.",
        ],
      },
    ],
  },
  wiki: {
    title: "A wiki",
    summary: "O que a empresa sabe, em páginas ligadas umas às outras.",
    sections: [
      {
        id: "links",
        heading: "Links",
        blocks: ["Digite [[ para ligar outra página. Os backlinks e o grafo mostram como as páginas se conectam."],
      },
      {
        id: "agents",
        heading: "Como os agentes usam",
        blocks: ["Os agentes leem a wiki quando respondem. Escrevem nela só quando alguém pede."],
      },
      {
        id: "obsidian",
        heading: "Trazer uma vault do Obsidian",
        blocks: [
          "Solte a pasta da vault, ou um .zip dela, no passo Contexto da configuração ou na importação da própria wiki. [[Links]], [[nota|apelido]] e [[nota#seção]] continuam funcionando. As tags vêm do frontmatter e do texto. Os anexos vão para o Drive, e imagens e arquivos incorporados viram cards na página. Callouts continuam callouts. Consultas do Dataview chegam como texto: peça a um agente para montar a tabela. Canvases e os ajustes do Obsidian (.obsidian) ficam de fora.",
        ],
      },
    ],
  },
  memory: {
    title: "Memória",
    summary: "O que cada agente aprende com as conversas e as correções do time.",
    sections: [
      {
        id: "learns",
        heading: "Como os agentes aprendem",
        blocks: [
          "Os agentes aprendem com as conversas e com as correções do time. O que um agente sabe entra no contexto dele quando for relevante. Na aba **Memória**, edite, fixe ou arquive o que estiver errado ou velho.",
        ],
      },
      {
        id: "permissions",
        heading: "Memória não dá permissão",
        blocks: [
          "Nada na memória deixa um agente fazer mais. O que ele pode fazer continua em **Ferramentas e acesso**.",
        ],
      },
      {
        id: "proposals",
        heading: "Mudanças propostas",
        blocks: [
          "Quando uma lição se repete, o agente pode propor mudar as próprias instruções ou o contexto do workspace. Nada muda sem alguém aceitar, e dá para ajustar o texto antes. A versão anterior fica no histórico. Uma proposta recusada vira lição, para não ser proposta de novo.",
        ],
      },
    ],
  },
  approvals: {
    title: "Aprovações",
    summary: "Ações sensíveis esperam uma pessoa, uma vez ou para sempre.",
    sections: [
      {
        id: "what",
        heading: "O que pede antes",
        blocks: [
          "Quando um agente quiser fazer merge, deploy, rollback, mandar algo para fora ou apagar dados, ele pede antes. Esse é o nível **Só o arriscado**, em que todo agente começa. Na página do agente, em **Aprovações**, **Sempre perguntar** faz ele pedir antes de mudar qualquer coisa, e **Nunca perguntar** faz ele não pedir nada, nem em merge, deploy, pagamentos e rotinas. O pedido aparece em **Aprovações** e na conversa, com a ação exata e o porquê.",
        ],
      },
      {
        id: "always",
        heading: "Uma vez, ou sempre",
        blocks: [
          "Aprovar vale para uma ação exata, com os valores dela. Se o agente mudar qualquer coisa, ele pede de novo.",
          "**Aprovar sempre** deixa o agente fazer esse tipo de ação daqui em diante, com quaisquer valores, sem pedir. No pedido de uma rotina, **Sempre nesta rotina** faz o mesmo só para ela. **Aprovar sempre** aparece para qualquer tipo de ação, merge e deploy também, enquanto o agente estiver em **Só o arriscado**. **Sempre nesta rotina** não aparece para merge, deploy, rollback e afins. Para desfazer, vá em **Ferramentas e acesso** na página do agente, ou nos ajustes da rotina.",
        ],
      },
      {
        id: "who",
        heading: "Quem aprova",
        blocks: [
          {
            list: [
              "Quem é responsável pelo agente, nos pedidos dele.",
              "Quem tem um cargo com **Aprovar ações dos agentes**, em todos os agentes.",
              "As pessoas que o agente nomeia ao pedir, no lugar do responsável.",
              "Quem cobre alguém em **Quem aprova por mim**, no que é pedido a essa pessoa. Merge, deploy e outras ações de risco só quando isso está ligado.",
            ],
          },
          "Uma ação na caixa de e-mail de alguém espera só essa pessoa. Um acesso novo para um agente espera quem gerencia agentes.",
          "O Lodi, seu braço direito, também pode aprovar por você os agentes novos que outros agentes propõem e mudanças de modelo, orçamento e canais de um agente, só quando foi você quem pediu. Quem cobre você e o que o Lodi aprova ficam em **Configurações\u00a0›\u00a0Você\u00a0›\u00a0Aprovações**.",
        ],
      },
      {
        id: "refusing",
        heading: "Recusar",
        blocks: ["Diga o porquê, se quiser: o agente lê, ajusta o plano e guarda como lição."],
      },
      {
        id: "meetings",
        heading: "Reuniões",
        blocks: ["Agentes só propõem horários. Convites para pessoas sempre passam por quem pediu."],
      },
    ],
  },
  "who-answers": {
    title: "Quem responde uma mensagem",
    summary: "Uma menção escolhe o agente. Sem ela, o VortoOne escolhe por você, nesta ordem.",
    sections: [
      {
        id: "order",
        heading: "Como uma mensagem encontra o seu agente",
        blocks: [
          "Uma mensagem que @menciona um agente vai para esse agente. Uma mensagem que não menciona ninguém é lida pelo primeiro destes que o workspace tiver:",
          {
            list: [
              "**Jev**, um modelo pequeno e rápido, feito para decisões como esta.",
              "**Um modelo pequeno numa das chaves de API do workspace**, como o Claude Haiku 5.5 ou o GPT-6 Luna.",
              "**O seu próprio plano do Claude ou do ChatGPT**, para as mensagens que você escreve, num servidor que você mesmo roda. Ele as lê no computador onde está conectado e leva alguns segundos.",
              "**Regras simples**: uma resposta a um agente vai para ele, e também uma continuação logo depois de você falar com ele, ou uma mensagem num canal onde você está sozinho com um agente.",
            ],
          },
          "Quando nada disso serve, nenhum agente responde. Mencione um agente para chamá-lo.",
        ],
      },
      {
        id: "jev",
        heading: "Jev",
        blocks: [
          "Quando o VortoOne hospeda o seu workspace, o Jev está incluído. Num servidor que você mesmo roda, ele usa a chave da TypeSafe do dono, cerca de US$ 0,0001 por mensagem: [Configurar o Jev](/help/jev#setup).",
        ],
      },
      {
        id: "plans",
        heading: "Por que o seu plano só escolhe nas suas mensagens",
        blocks: [
          "Os planos do Claude e do ChatGPT são pessoais, então o seu plano só escolhe nas mensagens que você escreve.",
        ],
      },
      {
        id: "see",
        heading: "Ver quem escolheu",
        blocks: ["Abra o visto embaixo de uma mensagem. A última linha diz quem escolheu os agentes."],
      },
    ],
  },
  jev: {
    title: "Jev",
    summary:
      "Um modelo rápido da TypeSafe que toma decisões rápidas pelos seus agentes. No seu próprio servidor ele é opcional, com uma chave sua.",
    sections: [
      {
        id: "what",
        heading: "O que ele faz",
        blocks: [
          {
            list: [
              "**Escolhe quem responde.** Uma mensagem sem @menção vai para o agente certo em menos de um segundo, mesmo quando os agentes pensam com um plano e você não tem chave de API.",
              "**Organiza a memória.** Ele percebe uma repetição ou um fato novo mesmo com outras palavras: a repetição se junta ao que o agente já sabia, e o fato novo substitui o antigo. Uma vez por dia ele organiza o resto.",
              "**Enxerga a tela.** No navegador e no desktop, os agentes acham botões e campos pela descrição, olham de perto os pequenos e conferem na imagem da tela se um passo deu certo.",
              "**Lê menos.** A cada vez, o agente carrega só as ferramentas de que deve precisar. A sua IA lê menos e custa menos.",
            ],
          },
        ],
      },
      {
        id: "without",
        heading: "Sem ele",
        blocks: [
          "Tudo continua funcionando, mais devagar e com menos cuidado.",
          {
            list: [
              "**Quem responde:** um modelo pequeno numa das suas chaves de API escolhe, como o Claude Haiku 5.5 ou o GPT-6 Luna. Sem chave, o plano do Claude ou do ChatGPT de cada pessoa escolhe nas mensagens que ela escreve, em alguns segundos. Fora isso, uma resposta ou uma continuação ainda chega ao agente. Mencione um agente para chamá-lo. [Como os agentes são escolhidos](/help/who-answers)",
              "**Memória:** repetições com as mesmas palavras ainda se juntam. Repetições e novidades ditas de outro jeito ficam separadas.",
              "**Tela:** os agentes usam os números mostrados na tela e os nomes exatos. Nada é lido de imagens.",
            ],
          },
        ],
      },
      {
        id: "setup",
        heading: "Como configurar",
        blocks: [
          {
            list: [
              "Abra https://console.typesafe.ai e entre com o Google ou o seu e-mail. Uma conta nova é criada do mesmo jeito.",
              "Adicione crédito no console. O Jev é pré-pago.",
              "Abra https://console.typesafe.ai/keys, crie uma chave e copie na hora.",
              "No VortoOne, abra **Configurações\u00a0›\u00a0IA e uso** e cole em **Jev**. Só quem configurou este servidor vê essa parte.",
            ],
          },
          "Num servidor que você roda com Docker, dá para colocar a chave no `.env` como `TYPESAFE_API_KEY`. Essa vale mais que a das Configurações.",
        ],
      },
      {
        id: "cost",
        heading: "Quanto custa",
        blocks: [
          {
            list: [
              "US$ 0,042 por milhão de tokens que o Jev lê. As respostas são grátis. Escolher quem responde lê cerca de 2.500 tokens, ou seja, cerca de US$ 0,0001. Uma captura de tela tem cerca de 3.900 tokens.",
              "O crédito comprado vale por 12 meses. Ligue a recarga automática no console para o Jev não ficar sem crédito.",
              "**Configurações\u00a0›\u00a0IA e uso** mostra quantas decisões o Jev tomou no mês e quanto custaram.",
            ],
          },
        ],
      },
      {
        id: "data",
        heading: "Os seus dados",
        blocks: [
          "O que o Jev lê para decidir:",
          {
            list: [
              "O nome e a descrição do canal, a mensagem e as poucas antes dela",
              "Os nomes e funções dos agentes, o começo das instruções deles e as ferramentas que eles podem usar",
              "Os itens de memória que ele compara",
              "A tarefa de um agente e o começo dos resultados das ferramentas",
              "O objetivo de uma missão e os comandos que um agente quer rodar nela",
              "As ferramentas que um app conectado oferece",
              "A página ou a tela que um agente está usando, inclusive capturas",
            ],
          },
          "A TypeSafe roda o Jev nos Estados Unidos e não treina os modelos dela com o que você envia. Ela guarda os dados pelo tempo necessário para operar o serviço. A TypeSafe oferece retenção zero nos planos enterprise.",
          "Política de privacidade da TypeSafe: https://typesafe.ai/legal/privacy-policy. Contrato de tratamento de dados: https://typesafe.ai/legal/data-processing.",
        ],
      },
      {
        id: "problems",
        heading: "Quando ele para",
        blocks: [
          {
            list: [
              "**Chave recusada:** crie uma chave nova e cole em **Configurações\u00a0›\u00a0IA e uso**.",
              "**Sem crédito:** adicione crédito no console. O Jev tenta de novo em até 30 minutos, ou clique em **Verificar de novo**.",
              "**Ocupado ou fora do ar:** a TypeSafe pode limitar os pedidos por segundo, ou parar de responder por um tempo. O Jev volta sozinho. Os seus limites: https://console.typesafe.ai/rate-limits. Para limites maiores, escreva para sales@typesafe.ai.",
            ],
          },
          "Enquanto isso, os agentes trabalham como em [Sem ele](/help/jev#without).",
        ],
      },
    ],
  },
  "apps-de-ia": {
    title: "Apps de IA",
    summary:
      "Peça tarefas aos agentes do VortoOne pelo Claude, Cursor ou outro app com MCP, sem sair dele, e receba a resposta ali mesmo.",
    sections: [
      {
        id: "what",
        heading: "Como funciona",
        blocks: [
          "O VortoOne é um servidor MCP. Você cola o endereço dele no seu app de IA, entra com a sua conta e escolhe um workspace. Daí em diante, o app pode listar os agentes e os canais, mandar uma tarefa a um agente em seu nome, esperar a resposta e ler as tarefas recentes.",
          'Por exemplo, no meio de uma cena no Unity ou no Blender, peça ao Claude: "Peça ao agente de marketing um texto para o lançamento". A mensagem aparece no VortoOne como sua, marcada com **via Claude**, e a resposta do agente volta para o Claude.',
          "O endereço está em **Configurações\u00a0›\u00a0Apps de IA**. Ele termina em `/mcp`.",
        ],
      },
      {
        id: "connect",
        heading: "Conectar um app",
        blocks: [
          {
            list: [
              "**Claude** (claude.ai, Claude Desktop e celular): em **Personalizar\u00a0›\u00a0Conectores** (Customize\u00a0›\u00a0Connectors), clique em **+ Adicionar** e em **Adicionar conector personalizado** (Add custom connector), dê o nome VortoOne, cole o endereço e clique em **Continuar**. Escolha **Entrar agora** (Sign in now), clique em **Adicionar** e autorize. Em planos Team e Enterprise, o dono da organização no Claude adiciona o conector em Configurações da organização\u00a0›\u00a0Conectores, e cada pessoa clica em **Conectar** em Personalizar\u00a0›\u00a0Conectores. No plano grátis do Claude, cabe um conector personalizado.",
              "**Claude Code:** rode `claude mcp add --transport http --scope user agentes <endereço>`, depois digite `/mcp`, escolha agentes e **Authenticate**. Para só a pasta atual, tire `--scope user`.",
              "**Cursor:** **Adicionar ao Cursor** em **Configurações\u00a0›\u00a0Apps de IA**, ou o endereço em `~/.cursor/mcp.json`. O Cursor pede para entrar na primeira vez que usar.",
              '**VS Code:** **Adicionar ao VS Code**, ou o endereço em `.vscode/mcp.json` com `"type": "http"`.',
              "**Outros apps:** ChatGPT, Gemini, Grok, Perplexity, Codex, Zed, JetBrains e outros têm cada um seus passos em **Conectar um app**, com o seu endereço já preenchido.",
            ],
          },
          "Na hora de entrar, o VortoOne mostra qual app pediu o acesso e para onde ele volta. Continue só se você acabou de começar a conexão.",
        ],
      },
      {
        id: "tools",
        heading: "O que o app pode fazer",
        blocks: [
          {
            list: [
              "`send_to_agent`: manda uma mensagem sua a um agente, na conversa privada com ele ou num canal, e espera a resposta. Só com o acesso **Ler e mandar tarefas aos agentes**.",
              "`wait_for_reply`: continua esperando a resposta de uma tarefa.",
              "`list_agents`: os agentes que você vê.",
              "`list_channels`: os canais que você lê.",
              "`read_conversation`: as mensagens recentes de um canal, de uma thread ou de uma tarefa.",
              "`list_recent_tasks`: as tarefas que você mandou pelos apps e o que os agentes estão fazendo agora.",
            ],
          },
          "O app age como você e vê só o que você vê. Ele nunca aprova ações dos agentes: quando um agente para e pede aprovação, a resposta diz isso e traz o link, e uma pessoa decide no VortoOne. Ele também não muda configurações, membros ou permissões.",
        ],
      },
      {
        id: "workspace",
        heading: "Um workspace por conexão",
        blocks: [
          "Cada conexão vale para o workspace escolhido ao entrar. Para usar outro workspace, conecte o app de novo e escolha o outro.",
          "Em **Configurações\u00a0›\u00a0Apps de IA** você vê os apps conectados, quando cada um foi usado, e desconecta qualquer um. Quem gerencia os membros vê os apps de todo o workspace e também pode desconectar. Cada mensagem mandada por um app aparece na Auditoria.",
        ],
      },
      {
        id: "tokens",
        heading: "Tokens pessoais",
        blocks: [
          "Para scripts e apps sem login OAuth, crie um token pessoal em **Configurações\u00a0›\u00a0Apps de IA** e mande no cabeçalho `Authorization: Bearer`. O token aparece uma vez só, vale só no workspace onde foi criado e pode ter validade de 30 dias, 90 dias, 1 ano ou nenhuma.",
          "Num servidor sem https, fora do próprio computador, use um token pessoal: o login OAuth pede https.",
        ],
      },
      {
        id: "limits",
        heading: "Limites",
        blocks: [
          {
            list: [
              "Até 120 pedidos por minuto e 5.000 por dia em cada conexão.",
              "Até 20 mensagens por minuto e 500 por dia de cada pessoa em cada workspace.",
              "Uma espera dura até 45 segundos. Quando o agente ainda está trabalhando, o app espera de novo.",
            ],
          },
        ],
      },
      {
        id: "safety",
        heading: "Textos de outras pessoas",
        blocks: [
          "As respostas trazem textos escritos por agentes e por pessoas no VortoOne. O app recebe esses textos marcados como conteúdo, não como instruções, mas um texto pode tentar convencer o app a fazer outra coisa. Confira o que o seu app vai fazer antes de confirmar.",
        ],
      },
      {
        id: "problems",
        heading: "Quando não conecta",
        blocks: [
          {
            list: [
              "**Pedido expirado:** a página de entrada vale 30 minutos e serve uma vez. Volte ao app e conecte de novo.",
              "**O app pede para entrar de novo:** a conexão foi desconectada, a senha mudou ou você saiu do workspace. Entre de novo pelo app.",
              "**Cursor preso num login antigo:** feche o Cursor, apague a pasta `~/.mcp-auth` e conecte de novo.",
            ],
          },
        ],
      },
    ],
  },
  "install-on-a-server": {
  "title": "Instalar o VortoOne num servidor",
  "summary": "Use o instalador e as instruções de conexão da versão atual do VortoOne Desktop. Esta versão não oferece serviço de nuvem hospedado nem instalação de servidor com um comando.",
  "sections": []
},
};
