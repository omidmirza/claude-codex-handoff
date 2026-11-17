#!/usr/bin/env python3
"""Move a chat between Claude Code and Codex, both ways, and keep the copy in sync.

  handoff.py to-codex  [ID] [--new] [--cwd DIR]   Claude Code chat -> Codex thread (Codex's own importer)
  handoff.py to-claude [ID] [--new] [--cwd DIR]   Codex thread -> Claude Code chat
  handoff.py list      [--cwd DIR]                 recent chats of both agents in this folder

ID is a Claude session id or a Codex thread id. Without it: the chat you are in (inside Claude
Code), or the newest chat of the other agent in the folder. Each handoff copies the chat you run it
from; run it again later and the same copy gets the new messages. --new makes a fresh copy instead.
Codex only updates a copy that nobody has continued in Codex; a Claude chat that is open in Claude
Code is left alone and a fresh copy is written. To Codex, tool calls come as short text notes
(Codex's importer); to Claude Code only the messages come. Files are already on disk.
Needs Python 3.9+ and the codex CLI (0.166 or later) on the PATH.
"""
import argparse
import datetime as dt
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from pathlib import Path

HOME = Path.home()
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
STATE = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local" / "state") / "handoff"
LEDGER = STATE / "ledger.json"
# Text that tools inject into a chat, not something a person typed.
META = ("<environment_context>", "# AGENTS.md instructions", "<user_instructions>", "<heartbeat>",
        "<app-context>", "<permissions instructions>", "<turn_aborted>", "<command-name>",
        "<command-message>", "<local-command-", "<system-reminder>", "Caveat: The messages below")
IMPORT_MARK = "<EXTERNAL SESSION IMPORTED>"
TOOL_NOTE = ("[external_agent_tool_call", "[external_agent_tool_result")  # Codex's notes for Claude's tool calls


def die(msg):
    sys.exit(f"handoff: {msg}")


def home(p):
    return str(p).replace(str(HOME), "~", 1)


def here(path):
    return os.path.realpath(path)


# ---------- Codex: talk to its app server (JSON-RPC over stdio) ----------

class Codex:
    def __init__(self):
        try:
            self.p = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, bufsize=1)
        except FileNotFoundError:
            die("the codex command is not on the PATH")
        self.q, self.n, self.notes, self.err = queue.Queue(), 0, [], []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()
        self.call("initialize", {"clientInfo": {"name": "handoff", "version": "1.1"},
                                 "capabilities": {"experimentalApi": True}})
        self._send({"method": "initialized"})

    def _pump(self):
        for line in self.p.stdout:
            self.q.put(line)
        self.q.put(None)  # the server closed its output

    def _drain(self):
        for line in self.p.stderr:
            self.err = (self.err + [line.rstrip()])[-20:]

    def _fail(self, what):
        time.sleep(0.2)
        code = self.p.poll()
        tail = "\n".join(x for x in self.err[-5:] if x.strip())
        die(what + (f" (codex app-server exited with code {code})" if code is not None else "")
            + (f":\n{tail}" if tail else ""))

    def _send(self, msg):
        try:
            self.p.stdin.write(json.dumps(msg) + "\n")
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            self._fail("codex app-server stopped")

    def _next(self, deadline):
        while True:
            try:
                line = self.q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                if time.time() >= deadline:
                    return None
                continue
            if line is None:
                self.q.put(None)
                return None
            try:
                return json.loads(line)
            except ValueError:
                continue

    def call(self, method, params, timeout=120):
        self.n += 1
        rid = self.n
        self._send({"id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while True:
            msg = self._next(deadline)
            if msg is None:
                self._fail(f"no answer from codex app-server for {method}")
            if msg.get("id") == rid:
                if "error" in msg:
                    die(f"{method}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result") or {}
            self.notes.append(msg)

    def wait(self, method, match, timeout=300):
        for msg in self.notes:
            if msg.get("method") == method and match(msg.get("params") or {}):
                return msg["params"]
        deadline = time.time() + timeout
        while True:
            msg = self._next(deadline)
            if msg is None:
                self._fail(f"codex did not finish ({method})")
            if msg.get("method") == method and match(msg.get("params") or {}):
                return msg["params"]

    def close(self):
        self.p.terminate()

    def threads(self, cwd, limit=10):
        res = self.call("thread/list", {"cwd": cwd, "limit": limit, "sortKey": "updated_at", "sortDirection": "desc",
                                        "sourceKinds": ["cli", "vscode", "exec", "appServer"]})
        return res.get("data", [])

    def turns(self, tid):
        out, cursor = [], None
        while True:
            res = self.call("thread/turns/list", {"threadId": tid, "itemsView": "full", "sortDirection": "asc",
                                                  "limit": 100, "cursor": cursor})
            out += res.get("data", [])
            cursor = res.get("nextCursor")
            if not cursor:
                return out


def item_text(item):
    if isinstance(item.get("text"), str):
        return item["text"].strip()
    parts = []
    for c in item.get("content") or []:
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            parts.append(c["text"])
        elif isinstance(c, dict) and "image" in str(c.get("type", "")).lower():
            parts.append("[image]")
    return "\n".join(parts).strip()


def imported(turn):
    """True for a turn Codex's importer brought over from Claude Code."""
    return str(turn.get("id", "")).startswith("external-import-turn-")


def running(turn):
    return turn.get("status") == "inProgress" or not (imported(turn) or turn.get("completedAt"))


def slash_command(text):
    """'<command-name>/handoff</command-name>...<command-args>x</command-args>' -> '/handoff x'."""
    name = re.search(r"<command-name>\s*(/?[^<\s]+)\s*</command-name>", text)
    if not name:
        return None
    args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
    cmd = name.group(1) if name.group(1).startswith("/") else "/" + name.group(1)
    return (cmd + " " + (args.group(1).strip() if args else "")).strip()


def codex_blocks(turn):
    """The visible messages of one Codex turn as [role, text] pairs. Replies Codex wrote are marked; replies
    that came from Claude Code through Codex's importer are not, and their tool notes are left out."""
    out = []
    for it in turn.get("items") or []:
        kind = it.get("type")
        if kind not in ("userMessage", "agentMessage"):
            continue
        text = item_text(it)
        if kind == "userMessage" and "<command-name>" in text:
            text = slash_command(text) or ""
        if not text or text == IMPORT_MARK or (kind == "userMessage" and text.startswith(META)):
            continue
        if kind == "agentMessage" and imported(turn) and text.startswith(TOOL_NOTE):
            continue
        out.append(["user" if kind == "userMessage" else "assistant", text])
    if not imported(turn):
        out = [[r, "**Codex:** " + t if r == "assistant" else t] for r, t in out]
    return out


# ---------- Claude Code: session files ----------

def claude_cwd(cwd):
    """The folder as Claude Code records it: the real path, NFC-normalised."""
    return unicodedata.normalize("NFC", here(cwd))


def project_dir(cwd):
    """Claude Code's folder for a project: every non-ASCII-alphanumeric UTF-16 unit becomes '-';
    names over 200 characters are cut and get '-' plus a base-36 hash of the path."""
    path = claude_cwd(cwd)
    raw = path.encode("utf-16-le")
    units = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]
    name = "".join(chr(u) if 48 <= u <= 57 or 65 <= u <= 90 or 97 <= u <= 122 else "-" for u in units)
    if len(name) > 200:
        h = 0
        for u in units:
            h = (h * 31 + u) & 0xFFFFFFFF
        h = abs(h - (1 << 32) if h >= 1 << 31 else h)
        digits = ""
        while True:
            h, r = divmod(h, 36)
            digits = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + digits
            if not h:
                break
        name = name[:200] + "-" + digits
    return CLAUDE / "projects" / name


def read_jsonl(path):
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass  # a line still being written
    return out


def claude_sessions(cwd):
    d = project_dir(cwd)
    files = [p for p in d.glob("*.jsonl")] if d.is_dir() else []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def find_claude(sid):
    hits = list((CLAUDE / "projects").glob(f"*/{sid}.jsonl"))
    if not hits:
        die(f"no Claude Code chat with id {sid}")
    return hits[0]


def claude_title(entries):
    """The name Claude Code shows: its custom title, else its AI title, else a summary, else the first prompt."""
    custom = ai = summary = None
    for e in entries:
        if e.get("type") == "custom-title" and e.get("customTitle"):
            custom = e["customTitle"]
        elif e.get("type") == "ai-title" and e.get("aiTitle"):
            ai = e["aiTitle"]
        elif e.get("type") == "summary" and e.get("summary"):
            summary = e["summary"]
    if custom or ai or summary:
        return custom or ai or summary
    for e in entries:
        m = e.get("message") or {}
        if e.get("type") == "user" and isinstance(m.get("content"), str) and not e.get("isMeta"):
            t = m["content"].strip()
            if t and not t.startswith(META):
                return t.splitlines()[0][:70]
    return "Claude Code chat"


def resume_leaf(entries):
    """The row Claude Code resumes from, so new rows continue the chat instead of starting a side branch:
    its last checkpoint ('last-prompt'), or the last row when that row descends from the checkpoint."""
    rows = {e["uuid"]: e for e in entries if e.get("uuid")}

    def chat_row(e):
        kind = e.get("type")
        return (kind in ("user", "assistant", "attachment", "system") and not e.get("isSidechain")
                and not (kind == "system" and e.get("subtype") == "file_snapshot")
                and not (kind == "attachment" and (e.get("attachment") or {}).get("type") == "fork_briefing"))

    last = next((e["uuid"] for e in reversed(entries) if e.get("uuid") and chat_row(e)), None)
    mark = next((e for e in reversed(entries) if e.get("type") == "last-prompt" and "leafUuid" in e), None)
    if mark is None:
        return last
    leaf = mark.get("leafUuid")
    if leaf is None:
        return None if mark.get("explicit") else last
    if leaf not in rows or rows[leaf].get("isSidechain"):
        return last
    node, seen = last, set()
    while node and node not in seen:  # does the last row descend from the checkpoint?
        if node == leaf:
            return last
        seen.add(node)
        node = (rows.get(node) or {}).get("parentUuid")
    return leaf


def claude_live(sid):
    """True when this chat is the one running the script, or is open in a running Claude Code process."""
    if os.environ.get("CLAUDE_CODE_SESSION_ID") == sid:
        return True
    for f in (CLAUDE / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text())
            if d.get("sessionId") != sid:
                continue
            pid = int(d.get("pid"))
            os.kill(pid, 0)
        except PermissionError:
            return True
        except (OSError, ValueError, TypeError):
            continue
        started = d.get("procStart")
        if started:  # rule out a reused pid
            now = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout
            if now.split() and now.split() != started.split():
                continue
        return True
    return False


def claude_version():
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20).stdout
        return out.split()[0]
    except Exception:
        return "2.1.0"


def git_branch(cwd):
    try:
        return subprocess.run(["git", "-C", cwd, "branch", "--show-current"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return ""


def iso(t):
    if isinstance(t, (int, float)) and t > 0:
        return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------- ledgers: which copy belongs to which chat ----------

def load_ledger():
    try:
        return json.loads(LEDGER.read_text())
    except (OSError, ValueError):
        return {}


def save_ledger(data):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(LEDGER)


def codex_records():
    try:
        return json.loads((CODEX_HOME / "external_agent_session_imports.json").read_text()).get("records", [])
    except (OSError, ValueError):
        return []


def codex_imported(path):
    """The Codex thread that Codex's importer already made from this Claude chat, if any."""
    hits = [r for r in codex_records() if r.get("source_path") == str(path)]
    return hits[-1].get("imported_thread_id") if hits else None


def codex_import_title(tid):
    return next((r.get("title") for r in reversed(codex_records())
                 if r.get("imported_thread_id") == tid and r.get("title")), None)


def fork_claude(path, entries, title):
    """A copy of a Claude Code chat under a new id, in the same folder (what `claude --fork-session` does)."""
    sid = str(uuid.uuid4())
    out = path.parent / f"{sid}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for e in entries:
            if "sessionId" in e:
                e = dict(e, sessionId=sid)
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        f.write(json.dumps({"type": "custom-title", "customTitle": f"{title} (copy)", "sessionId": sid}) + "\n")
    return out, read_jsonl(out)


# ---------- the two directions ----------

def to_codex(args):
    cwd = here(args.cwd)
    if args.id:
        path = find_claude(args.id)
    elif os.environ.get("CLAUDE_CODE_SESSION_ID") and list((CLAUDE / "projects").glob(
            f"*/{os.environ['CLAUDE_CODE_SESSION_ID']}.jsonl")):
        path = find_claude(os.environ["CLAUDE_CODE_SESSION_ID"])  # the chat this runs in
    else:
        files = claude_sessions(cwd)
        if not files:
            die(f"no Claude Code chat in {home(cwd)}")
        path = files[0]
    entries = read_jsonl(path)
    cwd = next((e["cwd"] for e in entries if e.get("cwd")), cwd)
    title = claude_title(entries)
    if args.new:
        path, entries = fork_claude(path, entries, title)
    before = codex_imported(path)
    codex = Codex()
    item = {"itemType": "SESSIONS", "description": "Claude Code chat (handoff)", "cwd": None,
            "details": {"sessions": [{"path": str(path), "cwd": cwd, "title": title}]}}
    iid = codex.call("externalAgentConfig/import", {"migrationItems": [item]})["importId"]
    done = codex.wait("externalAgentConfig/import/completed", lambda p: p.get("importId") == iid)
    ok = [s for r in done.get("itemTypeResults", []) for s in r.get("successes", [])]
    bad = [f for r in done.get("itemTypeResults", []) for f in r.get("failures", [])]
    if not ok:
        codex.close()
        if bad:
            die("Codex did not import the chat: " + "; ".join(f.get("message", "?") for f in bad))
        if before:
            die(f"Codex left its copy (thread {before}) as it is. Codex adds new messages only to a copy "
                f"nobody has continued in Codex, and only while it is not open, busy or archived.\n"
                f"  Continue there:  codex resume {before}\n"
                f"  Or send a fresh copy:  handoff.py to-codex {path.stem} --new")
        die("Codex did not import the chat (no reason given)")
    tid = ok[0].get("target")
    if not codex.call("thread/read", {"threadId": tid}).get("thread", {}).get("name"):
        codex.call("thread/name/set", {"threadId": tid, "name": title})
    codex.close()
    led = load_ledger()
    again = before == tid or led.get(f"claude:{path.stem}", {}).get("codex_thread") == tid
    led[f"claude:{path.stem}"] = {"codex_thread": tid, "title": title, "updated": iso(time.time())}
    save_ledger(led)
    print(f"{'Updated' if again else 'Copied'} Claude Code chat \"{title}\" to Codex.")
    print(f"  Codex thread: {tid}")
    print(f"  Continue:     cd {shlex.quote(cwd)} && codex resume {tid}   (or open it in the Codex app)")


def to_claude(args):
    cwd = here(args.cwd)
    codex = Codex()
    if args.id:
        tid = args.id
    else:
        found = codex.threads(cwd)
        if not found:
            codex.close()
            die(f"no Codex thread in {home(cwd)}")
        tid = found[0]["id"]
    thread = codex.call("thread/read", {"threadId": tid}).get("thread", {})
    cwd = claude_cwd(thread.get("cwd") or cwd)
    turns = codex.turns(tid)
    codex.close()
    led = load_ledger()
    title = thread.get("name") or codex_import_title(tid) or next(
        (v.get("title") for v in led.values() if v.get("codex_thread") == tid and v.get("title")), None) or \
        ((thread.get("preview") or "Codex chat").splitlines() or ["Codex chat"])[0][:70]

    key = f"codex:{tid}"
    link = led.get(key)
    target = Path(link["claude_file"]) if link else None
    append = bool(target and target.exists() and not args.new)
    note = ""
    if append and claude_live(target.stem):
        append, note = False, (f"Claude chat {target.stem} is open in Claude Code, so it was left as it is; "
                               f"this is a fresh copy.")
    done_turns = set(link.get("turns", [])) if append else set()
    # Only the last turn can still be running; it waits for the next handoff (it is often the one asking for it).
    pending = turns[-1] if turns and running(turns[-1]) else None
    new = [t for t in turns if t is not pending and t.get("id") not in done_turns]
    blocks = []  # [role, text, turn id, start, end], same-role neighbours merged
    for t in new:
        for role, text in codex_blocks(t):
            if blocks and blocks[-1][0] == role:
                blocks[-1][1] += "\n\n" + text
                blocks[-1][4] = t.get("completedAt") or t.get("startedAt")
            else:
                blocks.append([role, text, t["id"], t.get("startedAt"), t.get("completedAt") or t.get("startedAt")])
    if not blocks:
        print(f"Nothing new in Codex thread {tid}." if append else f"Codex thread {tid} has no finished messages yet.")
        return

    version, branch = claude_version(), git_branch(cwd)
    if append:
        sid, path = target.stem, target
        parent = resume_leaf(read_jsonl(path))
        lead = "[More from the Codex chat, synced by handoff]\n\n"
    else:
        sid = str(uuid.uuid4())
        path = project_dir(cwd) / f"{sid}.jsonl"
        parent = None
        lead = (f"[Handoff from Codex, thread {tid}. The replies marked Codex were written by Codex. Only the "
                f"messages came over; tool calls and their output stayed behind. Files they changed are on disk.]\n\n")

    lines = []
    if not append:
        lines.append({"type": "custom-title", "customTitle": f"{title} (from Codex)", "sessionId": sid})
    first_user = True
    for role, text, turn_id, start, end in blocks:
        if role == "user" and first_user:
            text, first_user = lead + text, False
        uid = str(uuid.uuid4())
        row = {"parentUuid": parent, "isSidechain": False, "userType": "external", "cwd": cwd, "sessionId": sid,
               "version": version, "gitBranch": branch, "uuid": uid,
               "timestamp": iso(start if role == "user" else end),
               "handoff": {"from": "codex", "thread": tid, "turn": turn_id}}
        if role == "user":
            row.update({"type": "user", "message": {"role": "user", "content": text}})
        else:
            row.update({"type": "assistant", "message": {
                "id": f"msg_handoff_{uid.replace('-', '')[:24]}", "type": "message", "role": "assistant",
                "model": "codex", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}})
        lines.append(row)
        parent = uid
    lines.append({"type": "last-prompt", "leafUuid": parent, "sessionId": sid})  # where Claude resumes
    if args.dry_run:
        print(f"Would {'append' if append else 'write'} {len(new)} turn(s), {len(blocks)} message(s) to {home(path)}")
        if note:
            print(f"  {note}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in lines:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    led[key] = {"claude_session": sid, "claude_file": str(path), "title": title,
                "turns": sorted(done_turns | {t["id"] for t in new}), "updated": iso(time.time())}
    save_ledger(led)
    n = len(new)
    print(f"{'Updated' if append else 'Copied'} Codex thread \"{title}\" to Claude Code "
          f"({n} turn{'s' if n != 1 else ''}).")
    if note:
        print(f"  {note}")
    print(f"  Claude chat: {sid}")
    print(f"  Open it in Terminal:   cd {shlex.quote(cwd)} && claude --resume {sid}")
    print(f"  Or in the Claude app:  cd {shlex.quote(cwd)} && claude --desktop --resume {sid}")


def list_chats(args):
    cwd = here(args.cwd)
    led = load_ledger()
    print(f"Claude Code chats in {home(cwd)}:")
    for p in claude_sessions(cwd)[:5]:
        link = led.get(f"claude:{p.stem}", {}).get("codex_thread")
        when = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d %b %H:%M")
        print(f"  {p.stem}  {when}  {claude_title(read_jsonl(p))[:50]}" + (f"  -> Codex {link}" if link else ""))
    codex = Codex()
    print(f"Codex threads in {home(cwd)}:")
    for t in codex.threads(cwd, 5):
        link = led.get(f"codex:{t['id']}", {}).get("claude_session")
        name = t.get("name") or ((t.get("preview") or "").splitlines() or [""])[0]
        when = dt.datetime.fromtimestamp(t.get("updatedAt") or 0).strftime("%d %b %H:%M")
        print(f"  {t['id']}  {when}  {name[:50]}" + (f"  -> Claude {link}" if link else ""))
    codex.close()


def main():
    ap = argparse.ArgumentParser(prog="handoff", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("to-codex", to_codex), ("to-claude", to_claude), ("list", list_chats)):
        sp = sub.add_parser(name)
        if name != "list":
            sp.add_argument("id", nargs="?", help="chat id (default: the current or newest chat)")
        sp.add_argument("--cwd", default=os.getcwd(), help="project folder (default: here)")
        if name == "to-claude":
            sp.add_argument("--new", action="store_true", help="write a fresh Claude chat instead of updating")
            sp.add_argument("--dry-run", action="store_true", help="show what would be written")
        if name == "to-codex":
            sp.add_argument("--new", action="store_true", help="send a fresh copy (a fork of the Claude chat)")
        sp.set_defaults(fn=fn)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
