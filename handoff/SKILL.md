---
name: handoff
description: Move the current chat between Claude Code and Codex, both ways, and keep the copy in sync. Use when the user says "continue this in Codex", "hand this to Claude", "bring my Codex chat here", "move this chat to the other agent", "sync the chat", or types /handoff or $handoff.
---

# Handoff

Copies the chat you are in to the other agent, in the same project folder. Run it again later and
the same copy gets the new messages. To Codex (Codex's own importer): the messages, plus tool calls
as short text notes with up to 4,000 characters of each output; thinking is dropped. To Claude Code:
only the messages. The files are already on disk.

Run the script in this skill's `scripts/` folder, from the project folder:

| You are in | The user wants | Run |
|---|---|---|
| Claude Code | this chat in Codex | `python3 <this skill's folder>/scripts/handoff.py to-codex` |
| Codex | this chat in Claude Code | `python3 <this skill's folder>/scripts/handoff.py to-claude` |
| either | the chats of both agents here | `python3 <this skill's folder>/scripts/handoff.py list` |

Add an id to pick another chat: a Claude Code session id for `to-codex`, a Codex thread id for
`to-claude`. Without one, `to-codex` takes the chat you are in and `to-claude` the newest Codex
thread in the folder.

Then tell the user in a few short lines what the script printed: the chat it copied, and the lines
that open it, exactly as printed (each starts with `cd`, so it works from any folder). A chat sent
to Codex also shows in the Codex app's list. A chat sent to Claude Code opens with one of the two
printed lines, pasted into Terminal: one for Claude Code in Terminal, one for the Claude app.

If `to-codex` says Codex left its copy as it is, someone continued that copy in Codex, and Codex
then stops adding messages to it. Offer the two choices it printed: continue in that Codex chat,
or send a fresh copy with `--new`. If `to-claude` says the Claude chat is open, it wrote a fresh
copy instead; pass on the new id.

- The script talks to the local `codex app-server` and writes `~/.claude/projects/` and
  `~/.local/state/handoff/`. If a sandbox blocks that, ask the user to approve running it outside
  the sandbox.
- Never edit chat files by hand.
