import type { HelpSlug } from "./index";
import type { HelpDocument } from "./types";

export const helpEn: Record<HelpSlug, HelpDocument> = {
  "run-on-your-computer": {
  "title": "Run VortoOne on your computer",
  "summary": "Use the installation package and connection instructions supplied with the current VortoOne Desktop release. This build does not provide a hosted cloud service or a one-command server installer.",
  "sections": []
},
  "workspace-computer": {
  "title": "The workspace's computer",
  "summary": "Use the installation package and connection instructions supplied with the current VortoOne Desktop release. This build does not provide a hosted cloud service or a one-command server installer.",
  "sections": []
},
  computers: {
    title: "Your computers and phones",
    summary:
      "Bring a Mac, a PC, a Linux computer, a server or a phone, and your agents work on it. On VortoOne Cloud and on a server of your own alike.",
    sections: [
      {
        id: "app",
        heading: "From the VortoOne app",
        blocks: [
          "Install the app on the computer, open it and sign in to your workspace. It asks **Let <workspace>'s agents use this Mac?** (this PC on Windows, this computer on Linux). Choose who can use it and press **Allow**. That's all.",
          "Already signed in? Open **Preferences\u00a0›\u00a0General** and press **Allow** on the workspace's row in the first card. Not there? Press **Add a workspace…** at the bottom of that card and sign in.",
        ],
      },
      {
        id: "web",
        heading: "From the website",
        blocks: [
          "In **Settings\u00a0›\u00a0Devices**, press **Connect a device**:",
          {
            list: [
              "**This Mac** or **This PC**: opens the app, which asks you first.",
              "**Another computer**: install the app there, sign in as yourself and press **Allow**. It shows up here by itself.",
              "**A server or VPS**: run one line in its terminal, then let it join. The line works once, for 10 minutes.",
            ],
          },
          "The app connects in one click only to an address with https, such as a Tailscale one ending in `.ts.net`. On a plain http address, use the terminal line.",
        ],
      },
      {
        id: "phones",
        heading: "Phones",
        blocks: [
          "An Android phone connects through any computer that's already connected: by cable, or over Wi-Fi on Android 14 or newer with the May 2026 security update. An iPhone connects through a Mac that has the app and Xcode, and the app sets up the rest. Both start from **Connect a device**.",
        ],
      },
      {
        id: "who",
        heading: "Who can use it",
        blocks: [
          "A new device is for you alone: **Only me** means you and the agents working for you. Open it to everyone in the workspace, or to some people and roles, when you allow it or later on its page in **Settings\u00a0›\u00a0Devices**.",
        ],
      },
      {
        id: "open",
        heading: "While the app is open",
        blocks: [
          "Agents work on a computer only while the app runs there and the computer is awake. When the app asks, leave **Keep it ready: open VortoOne at login** checked. A computer that's off or asleep shows **Offline**, and agents wait for it to come back.",
        ],
      },
      {
        id: "pausing",
        heading: "Pausing and disconnecting",
        blocks: [
          "On the computer, the VortoOne icon by the clock pauses the agents of every workspace, or of just one. In **Preferences\u00a0›\u00a0General**, each workspace's switch pauses just that one, and **Leave**, in its **⋯** menu, disconnects it. Nothing new starts while paused, and what was running finishes. Disconnecting keeps the agents' folders on disk.",
          "From the website, **Remove** on the device's page disconnects it.",
        ],
      },
      {
        id: "organizations",
        heading: "Several workspaces, one computer",
        blocks: [
          "Sign in to each workspace in the same app and press **Allow** for each one. Workspaces on VortoOne Cloud and on your own servers can share a computer (app 0.6.1 and later). Each has its own agents and folders there, and disconnecting one doesn't touch the others.",
          "A server the app runs on this computer is connected already and needs nothing. To also lend that computer to a workspace somewhere else, see [Lend it to another workspace too](/help/run-on-your-computer#another-workspace).",
          "Using the app on a laptop only as a window onto your workspace? When it asks to let agents use the laptop, **Not now** keeps it a plain window.",
          "A server connected with the terminal line works for one workspace. Connecting it from another workspace moves the server to that one, and the first no longer reaches it. For several workspaces on one computer, use the app.",
        ],
      },
      {
        id: "line",
        heading: "When agents wait in line",
        blocks: [
          "Each piece of an agent's work uses the computer's memory. When free memory runs short, the next one waits in line until another ends. People go first. The number of CPUs doesn't change this.",
          "A computer with 4 GB of memory runs two or three at once, one with 8 GB about nine. A Mac or a PC runs up to eight. Browsers and desktops the agents keep open use memory too. For a shorter line, use a computer with more memory, or connect another and split the agents between them in **Settings\u00a0›\u00a0Devices**. Workspaces that share a computer share its memory.",
        ],
      },
      {
        id: "folders",
        heading: "Folders",
        blocks: [
          "Each agent always has its own folder, in `~/vortoone/<workspace>`. More folders are allowed on the computer itself, never from the website: in the app, **Preferences\u00a0›\u00a0Folders**, or with `vortoone-runner folders allow` on a server. Each workspace allows its own folders: what you allow for one only counts for its agents. Removing a folder deletes nothing. It only takes the access away.",
        ],
      },
      {
        id: "reach",
        heading: "What agents can reach",
        blocks: [
          "Agents run as you, the computer's user. Their file tools stay in their folders, but a command can read anything you can:",
          {
            list: [
              "your files and sign-ins",
              "other agents' folders, other workspaces' included, and the logins their browsers keep",
              "the token that connects this computer",
              "on a computer that also runs VortoOne, its database and keys in `.vortoone`",
            ],
          },
          "VortoOne refuses commands that name its keys, tokens and sign-ins, but that's a rule, not a wall. **Commands** are on by default. Turn them off in the app's ask before you press **Allow**, or later in **Preferences\u00a0›\u00a0Tools**, where each workspace has its own switch (app 0.8.1 and later) and the app asks before turning one back on. Claude Code and Codex run their own commands in the agent's folder, under their own rules, even with Commands off.",
          "For a real wall, use a Linux server. On Debian 13, installed with `--system`, each agent is a user of its own. On Ubuntu, agents share one user, `vortoone`: they see each other's folders, not yours or the server's. Or give the agents a computer of their own.",
        ],
      },
    ],
  },
  import: {
    title: "Bring your agents",
    summary:
      "Bring agents from another app, or from another VortoOne workspace, with their instructions, skills, routines and memory.",
    sections: [
      {
        id: "comes",
        heading: "What comes along",
        blocks: [
          {
            list: [
              "Agents, with their name, role, instructions and look.",
              "Skills, on for the agents that had them.",
              "Public channels, with the agents in them.",
              "Routines, active and on their schedule. Pause them in **Routines** if you want.",
              "Memory: what each agent remembers.",
              "Each agent's own files (OpenBot.run's files folder, an OpenClaw workspace), in a folder with its name in the **Drive**. Keys, logins and system files stay behind, and nothing already there is replaced.",
            ],
          },
        ],
      },
      {
        id: "never",
        heading: "What never comes along",
        blocks: [
          "Keys, passwords, logins and chat history never come along: the agents think with the workspace's AI. Some things have no equivalent here yet, or should never travel in a file. You can recreate them later.",
          "Files and folders are read in your browser. A GitHub link goes through the workspace's server. Key and login files are never opened, and nothing changes until you confirm.",
        ],
      },
      {
        id: "mcp",
        heading: "Connect with MCP",
        blocks: [
          "MCP lets your AI app reach this workspace, so it creates the agents here itself, with their instructions, skills and routines. Choose your app, pick **Connect with MCP**, then:",
          {
            list: [
              "Connect the address the step shows, in that app's own steps. [How to connect, app by app](/help/apps-de-ia#connect)",
              "On the sign-in page, pick **Create and edit agents, routines and skills**.",
              "Paste the request into the app. The agents show up on the step as they arrive.",
            ],
          },
          "When an app can't connect, choose **From a file** or **Prompt only**, whichever the step shows. [From another app](/help/import#other-apps) covers the request.",
        ],
      },
      {
        id: "other-apps",
        heading: "From another app",
        blocks: [
          "Choose **Grok Bot**, **ChatGPT dots** or **Another app**. Copy the request, paste it in that app, then bring back what it returns: drop the file, or paste the whole reply. Where each app puts it:",
          {
            list: [
              "**Grok Bot**: in the chat with your bot. It replies with the JSON: copy the whole reply.",
              "**ChatGPT dots**: in a new chat. Download the file it makes.",
              "**Claude**: in a new conversation, or in your agents' project. Download the file it makes.",
              "**Manus**: in a new task. The file is in the task's results.",
              "**Rakazo**: send it to your agent. It replies with the JSON: copy the whole reply.",
              "**OpenBot**: no request needed. Drag the tenant folder (agents.yaml, agents/, model.yaml, skills.yaml) or paste its GitHub link.",
            ],
          },
        ],
      },
      {
        id: "export",
        heading: "Export a workspace",
        blocks: [
          "**Settings\u00a0›\u00a0Import and export\u00a0›\u00a0Export** downloads a .json file, to take your agents to another workspace or to keep a copy. In the file:",
          {
            list: [
              "Agents, with their instructions, model and look",
              "Skills, with the whole SKILL.md",
              "Public channels, with their agents",
              "Scheduled routines (they arrive active on the other side)",
              "The memory everyone in the workspace can see",
            ],
          },
          "Never in it:",
          {
            list: [
              "API keys and passwords",
              "Connectors and logins",
              "Computers",
              "Conversations, messages and private channels",
              "Usage, costs and activity history",
            ],
          },
          "To use the file, open **Settings\u00a0›\u00a0Import and export** in the other workspace and drop the .json.",
        ],
      },
    ],
  },
  wiki: {
    title: "The wiki",
    summary: "What the company knows, in pages linked to each other.",
    sections: [
      {
        id: "links",
        heading: "Links",
        blocks: ["Type [[ to link another page. Backlinks and the graph show how pages connect."],
      },
      {
        id: "agents",
        heading: "How agents use it",
        blocks: ["Agents read the wiki when they answer. They write in it only when someone asks."],
      },
      {
        id: "obsidian",
        heading: "Bring an Obsidian vault",
        blocks: [
          "Drop the vault's folder, or a .zip of it, in the setup's Context step or in the wiki's own import. [[Links]], [[note|alias]] and [[note#section]] keep working. Tags come from the frontmatter and the text. Attachments go to the Drive, and embedded images and files become cards on the page. Callouts stay callouts. Dataview queries come in as text: ask an agent to build the table. Canvases and Obsidian's settings (.obsidian) stay out.",
        ],
      },
    ],
  },
  memory: {
    title: "Memory",
    summary: "What each agent learns from conversations and the team's corrections.",
    sections: [
      {
        id: "learns",
        heading: "How agents learn",
        blocks: [
          "Agents learn from conversations and from the team's corrections. What an agent knows enters its context when relevant. On its **Memory** tab, edit, pin or archive what's wrong or stale.",
        ],
      },
      {
        id: "permissions",
        heading: "Memory grants no permission",
        blocks: ["Nothing in memory lets an agent do more. What it can do stays in its **Tools and access**."],
      },
      {
        id: "proposals",
        heading: "Proposed changes",
        blocks: [
          "When a lesson repeats, the agent may propose changing its own instructions or the workspace's context. Nothing changes until someone accepts, and the text can be adjusted first. The previous version stays in the history. A refused proposal is kept as a lesson, so it isn't proposed again.",
        ],
      },
    ],
  },
  approvals: {
    title: "Approvals",
    summary: "Sensitive actions wait for a person, once or for good.",
    sections: [
      {
        id: "what",
        heading: "What asks first",
        blocks: [
          "When an agent wants to merge, deploy, roll back, send something outside or delete data, it asks first. That is **Only what is risky**, the level every agent starts at. On the agent's page, under **Approvals**, **Always ask** makes it ask before changing anything, and **Never ask** makes it ask for nothing, merge, deploy, payments and routines included. The request shows up in **Approvals** and in the conversation, with the exact action and why.",
        ],
      },
      {
        id: "always",
        heading: "Once, or always",
        blocks: [
          "Approving covers one exact action, with its values. If the agent changes anything, it asks again.",
          "**Always approve** lets the agent do that kind of action from now on, with any values, without asking. On a routine's request, **Always in this routine** does the same for that routine only. **Always approve** is there for any kind of action, merge and deploy too, while the agent is at **Only what is risky**. **Always in this routine** is not offered for merge, deploy, roll back and the like. To undo it, go to **Tools and access** on the agent's page, or to the routine's settings.",
        ],
      },
      {
        id: "who",
        heading: "Who approves",
        blocks: [
          {
            list: [
              "The agent's owner, for its requests.",
              "Anyone whose role has **Approve agents' actions**, for every agent.",
              "The people an agent names when it asks, in place of its owner.",
              "Whoever covers someone in **Who approves for me**, for what is asked of that person. Merge, deploy and other risky actions only when that's turned on.",
            ],
          },
          "An action in someone's own mailbox waits for that person alone. New access for an agent waits for those who manage agents.",
          "Lodi, your right hand, can also approve for you the new agents other agents propose and changes to an agent's model, budget and channels, only when you asked for them. Who covers you and what Lodi approves are chosen in **Settings\u00a0›\u00a0You\u00a0›\u00a0Approvals**.",
        ],
      },
      {
        id: "refusing",
        heading: "Refusing",
        blocks: ["Say why, if you want: the agent reads it, adjusts its plan and keeps it as a lesson."],
      },
      {
        id: "meetings",
        heading: "Meetings",
        blocks: ["Agents only suggest times. Invitations to people always go through whoever asked."],
      },
    ],
  },
  "who-answers": {
    title: "Who answers a message",
    summary: "A mention picks the agent. Without one, VortoOne picks for you, in this order.",
    sections: [
      {
        id: "order",
        heading: "How a message finds its agent",
        blocks: [
          "A message that @mentions an agent goes to that agent. A message that mentions nobody is read by the first of these the workspace has:",
          {
            list: [
              "**Jev**, a small, fast model made for decisions like this one.",
              "**A small model on one of the workspace's API keys**, such as Claude Haiku 5.5 or GPT-6 Luna.",
              "**Your own Claude or ChatGPT plan**, for the messages you write, on a server you run yourself. It reads them on the computer where it's signed in, and takes a few seconds.",
              "**Simple rules**: a reply to an agent goes to it, and so does a follow-up right after you talked with it, or a message in a channel where you are alone with one agent.",
            ],
          },
          "When none of these fits, no agent answers. Mention an agent to call it.",
        ],
      },
      {
        id: "jev",
        heading: "Jev",
        blocks: [
          "When VortoOne hosts your workspace, Jev is included. On a server you run yourself, it uses the owner's TypeSafe key, about US$ 0.0001 a message: [Set up Jev](/help/jev#setup).",
        ],
      },
      {
        id: "plans",
        heading: "Why your plan picks only for your messages",
        blocks: ["Claude and ChatGPT plans are personal, so your plan picks only for the messages you write."],
      },
      {
        id: "see",
        heading: "Seeing who picked",
        blocks: ["Open the check under a message. Its last line says who picked the agents."],
      },
    ],
  },
  jev: {
    title: "Jev",
    summary:
      "A fast model by TypeSafe that makes quick decisions for your agents. On your own server it's optional, with a key of your own.",
    sections: [
      {
        id: "what",
        heading: "What it does",
        blocks: [
          {
            list: [
              "**Picks who answers.** A message nobody @mentioned goes to the right agent in under a second, even when your agents think with a plan and you have no API key.",
              "**Keeps memory tidy.** It spots a repeat or a newer fact even in other words: the repeat merges with what the agent knew, and the newer fact replaces the old one. Once a day it tidies the rest.",
              "**Sees the screen.** On their browser and desktop, agents find buttons and fields from a description, look closer at small ones, and check on the screen's image that a step worked.",
              "**Reads less.** Each turn loads only the tools the agent will likely need, so your AI reads less and costs less.",
            ],
          },
        ],
      },
      {
        id: "without",
        heading: "Without it",
        blocks: [
          "Everything keeps working, more slowly and with less care.",
          {
            list: [
              "**Who answers:** a small model on one of your API keys picks, such as Claude Haiku 5.5 or GPT-6 Luna. Without a key, each person's own Claude or ChatGPT plan picks for the messages they write, in a few seconds. Otherwise a reply or a follow-up still reaches its agent. Mention an agent to call it. [How agents are picked](/help/who-answers)",
              "**Memory:** repeats in the same words still merge. Repeats and updates in other words stay apart.",
              "**Screen:** agents use the numbers shown on the screen and exact names. Nothing is read from images.",
            ],
          },
        ],
      },
      {
        id: "setup",
        heading: "Set it up",
        blocks: [
          {
            list: [
              "Open https://console.typesafe.ai and sign in with Google or your email. A new account is made the same way.",
              "Add credit in the console. Jev is prepaid.",
              "Open https://console.typesafe.ai/keys, create a key and copy it right away.",
              "In VortoOne, open **Settings\u00a0›\u00a0AI and usage** and paste it under **Jev**. Only the person who set up this server sees it.",
            ],
          },
          "On a server you run with Docker, you can put the key in `.env` as `TYPESAFE_API_KEY` instead. That one wins over the one in Settings.",
        ],
      },
      {
        id: "cost",
        heading: "What it costs",
        blocks: [
          {
            list: [
              "US$ 0.042 per million tokens Jev reads. Its answers are free. Picking who answers reads about 2,500 tokens, so about US$ 0.0001. A screenshot is about 3,900 tokens.",
              "Credits you buy last 12 months. Turn on automatic refills in the console so Jev doesn't run out.",
              "**Settings\u00a0›\u00a0AI and usage** shows how many decisions Jev made this month, and what they cost.",
            ],
          },
        ],
      },
      {
        id: "data",
        heading: "Your data",
        blocks: [
          "What Jev reads to decide:",
          {
            list: [
              "The channel's name and description, the message and the few before it",
              "Your agents' names and roles, the start of their instructions and the tools they can use",
              "The memory items it compares",
              "An agent's task and the start of its tool results",
              "A mission's goal and the commands an agent wants to run for it",
              "The tools a connected app offers",
              "The page or screen an agent is using, screenshots included",
            ],
          },
          "TypeSafe runs Jev in the United States and doesn't train its models on what you send. It keeps data as long as it needs to run the service. Zero retention is available on its enterprise plans.",
          "TypeSafe's privacy policy: https://typesafe.ai/legal/privacy-policy. Its data processing agreement: https://typesafe.ai/legal/data-processing.",
        ],
      },
      {
        id: "problems",
        heading: "When it stops",
        blocks: [
          {
            list: [
              "**Key not accepted:** create a new key and paste it in **Settings\u00a0›\u00a0AI and usage**.",
              "**Out of credit:** add credit in the console. Jev tries again within 30 minutes, or click **Check again**.",
              "**Busy or unreachable:** TypeSafe may limit requests per second, or stop answering for a while. Jev picks up again by itself. Your limits: https://console.typesafe.ai/rate-limits. For higher ones, write to sales@typesafe.ai.",
            ],
          },
          "Meanwhile, agents work as in [Without it](/help/jev#without).",
        ],
      },
    ],
  },
  "apps-de-ia": {
    title: "AI apps",
    summary:
      "Ask VortoOne's agents for tasks from Claude, Cursor or another MCP app, without leaving it, and get the answer right there.",
    sections: [
      {
        id: "what",
        heading: "How it works",
        blocks: [
          "VortoOne is an MCP server. You paste its address into your AI app, sign in with your account and pick a workspace. From then on, the app can list agents and channels, send a task to an agent as you, wait for the reply and read recent tasks.",
          'For example, in the middle of a scene in Unity or Blender, ask Claude: "Ask the marketing agent for a launch blurb". The message shows in VortoOne as yours, marked **via Claude**, and the agent\'s reply comes back to Claude.',
          "The address is in **Settings\u00a0›\u00a0AI apps**. It ends in `/mcp`.",
        ],
      },
      {
        id: "connect",
        heading: "Connect an app",
        blocks: [
          {
            list: [
              "**Claude** (claude.ai, Claude Desktop and phone): in **Customize\u00a0›\u00a0Connectors**, click **+ Add**, then **Add custom connector**, name it VortoOne, paste the address and click **Continue**. Choose **Sign in now**, click **Add** and authorize. On Team and Enterprise plans, the Claude organization's Owner adds the connector in Organization settings\u00a0›\u00a0Connectors, and each person clicks **Connect** in Customize\u00a0›\u00a0Connectors. Claude's free plan has room for one custom connector.",
              "**Claude Code:** run `claude mcp add --transport http --scope user VortoOne <address>`, then type `/mcp`, pick VortoOne and **Authenticate**. For the current folder only, drop `--scope user`.",
              "**Cursor:** **Add to Cursor** in **Settings\u00a0›\u00a0AI apps**, or the address in `~/.cursor/mcp.json`. Cursor asks you to sign in the first time you use it.",
              '**VS Code:** **Add to VS Code**, or the address in `.vscode/mcp.json` with `"type": "http"`.',
              "**Other apps:** ChatGPT, Gemini, Grok, Perplexity, Codex, Zed, JetBrains and more each have their own steps in **Connect an app**, with your address filled in.",
            ],
          },
          "When you sign in, VortoOne shows which app asked for access and where it returns to. Continue only if you just started the connection.",
        ],
      },
      {
        id: "tools",
        heading: "What the app can do",
        blocks: [
          {
            list: [
              "`send_to_agent`: sends a message from you to an agent, in your private conversation with it or in a channel, and waits for the reply. Only with **Read and send tasks to agents** access.",
              "`wait_for_reply`: keeps waiting for a task's reply.",
              "`list_agents`: the agents you see.",
              "`list_channels`: the channels you read.",
              "`read_conversation`: recent messages of a channel, a thread or a task.",
              "`list_recent_tasks`: the tasks you sent from apps and what agents are doing now.",
            ],
          },
          "The app acts as you and sees only what you see. It never approves agents' actions: when an agent stops to ask for approval, the reply says so with a link, and a person decides in VortoOne. It doesn't change settings, members or permissions either.",
        ],
      },
      {
        id: "workspace",
        heading: "One workspace per connection",
        blocks: [
          "Each connection covers the workspace picked when signing in. To use another workspace, connect the app again and pick the other one.",
          "In **Settings\u00a0›\u00a0AI apps** you see the connected apps, when each was used, and disconnect any of them. People who manage members see the whole workspace's apps and can disconnect them too. Every message sent from an app shows in the Audit.",
        ],
      },
      {
        id: "tokens",
        heading: "Personal tokens",
        blocks: [
          "For scripts and apps without OAuth sign-in, create a personal token in **Settings\u00a0›\u00a0AI apps** and send it in the `Authorization: Bearer` header. The token is shown once, works only in the workspace it was created in, and can expire in 30 days, 90 days, 1 year or never.",
          "On a server without https, away from its own computer, use a personal token: OAuth sign-in needs https.",
        ],
      },
      {
        id: "limits",
        heading: "Limits",
        blocks: [
          {
            list: [
              "Up to 120 requests a minute and 5,000 a day per connection.",
              "Up to 20 messages a minute and 500 a day per person in each workspace.",
              "A wait lasts up to 45 seconds. When the agent is still working, the app waits again.",
            ],
          },
        ],
      },
      {
        id: "safety",
        heading: "Other people's text",
        blocks: [
          "Replies carry text written by agents and people in VortoOne. The app gets that text marked as content, not instructions, but a text may still try to talk the app into something else. Check what your app is about to do before you confirm.",
        ],
      },
      {
        id: "problems",
        heading: "When it won't connect",
        blocks: [
          {
            list: [
              "**Request expired:** the sign-in page lasts 30 minutes and works once. Go back to the app and connect again.",
              "**The app asks to sign in again:** the connection was disconnected, the password changed or you left the workspace. Sign in again from the app.",
              "**Cursor stuck on an old sign-in:** quit Cursor, delete the `~/.mcp-auth` folder and connect again.",
            ],
          },
        ],
      },
    ],
  },
  "install-on-a-server": {
  "title": "Install VortoOne on a server",
  "summary": "Use the installation package and connection instructions supplied with the current VortoOne Desktop release. This build does not provide a hosted cloud service or a one-command server installer.",
  "sections": []
},
};
