---
tags:
  - ai-generated
---
# Move a chat between Claude Code and Codex

You are chatting with Claude Code and want to go on in Codex, or the other way round.
Type one word and the same chat opens in the other app. Nothing to explain again.

- In Claude Code: type `/handoff` (or say "continue this chat in Codex").
- In Codex: type `$handoff` (or say "bring this chat back to Claude Code").
- A chat sent to Codex shows up in the Codex app's list. On the way back, Codex prints two lines: paste
  the one for your app into Terminal (one opens Claude Code in Terminal, the other the Claude app).

## Set it up (once)

1. Open Terminal (press ⌘+Space, type Terminal, press Return).
2. Paste these two lines and press Return:
   ```bash
   git clone https://github.com/omidmirza/claude-codex-handoff.git
   bash claude-codex-handoff/install.sh
   ```
   Have the folder already? Type `bash ` (with a space), drag `install.sh` into Terminal, press Return.
3. Start a new chat in Claude Code or Codex.

You need a Mac with Claude Code and Codex (CLI 0.166 or later) installed. If a window asks to install
developer tools, click Install, then paste the lines again.
For one project only: `bash claude-codex-handoff/install.sh --project /path/to/project`.

## Two real tests

Dinner:
1. Claude Code: "Dinner for 4. Anna is vegetarian. Suggest a menu." Then: "Continue this chat in Codex."
2. Codex: "Who is vegetarian?" It answered: "Anna is vegetarian; the main course is wild mushroom risotto."
3. Back in Claude Code: "What did Codex say about Anna?" It answered with Codex's reply.

Nobody told Codex about Anna. It knew because the whole chat came with it.

Cruise control:
1. Claude Code: a PI cruise controller for a 1000 kg car, at most 4000 N, settling within 10 s.
   Claude wrote `cruise.py`: Kp 1500, Ki 400, settles in 6.6 s. Then: "Continue this chat in Codex."
2. Codex: "What if the car is twice as heavy? Is the goal still met?" It ran it: 12.2 s, goal missed.
3. `$handoff`, then the printed line in Terminal. Back in Claude Code: "What did Codex find?" It knew.

## Good to know

- Only the chat is copied. Your files stay where they are; both apps work in the same folder.
- Went on chatting? Type it again and the other app gets the new messages.
- Codex also has its own one-way import: Codex app, Settings, Import, then Import next to Claude Code.
- The chat formats belong to Claude Code and Codex and can change with an update. If a handoff fails
  after an update, it says what happened.

## What is in here

- `install.sh`: puts the `handoff` folder where Claude Code looks (`.claude/skills`) and a link to it
  where Codex looks (`.agents/skills`), so both apps use the same copy.
- `handoff/SKILL.md`: tells the app when and how to hand a chat over (a "skill").
- `handoff/scripts/handoff.py`: does the copying. To Codex it uses Codex's own import; to Claude Code it
  writes a new chat. Run again, it adds only the new messages.
