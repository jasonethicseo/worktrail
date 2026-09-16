# Worktrail

[한국어](README.ko.md)

**A record of what an AI coding agent did, kept so the next session does not have to guess from the code.**

Worktrail is an MCP server and a small local window. While Claude Code or Codex works in your repository, it records the work as *threads*: what the thread is for, what was decided and what it was decided from, what is still open, and what comes next. The next session, another agent, or you picks up from that record instead of re-reading the diff.

- **Demo** (read-only, a fictional team): https://casebook-api.syncflo.cloud/demo/en/ · [Korean](https://casebook-api.syncflo.cloud/demo/)
- **Request access** to the hosted server: https://casebook-api.syncflo.cloud/join (Google sign-in; a handful of seats, first come)
- **Feedback**: [GitHub Issues](https://github.com/jasonethicseo/worktrail/issues)

## What gets recorded

Every thread keeps four kinds of things apart, because they are overturned by different things:

- **Focus** — what this thread is for and what would finish it.
- **Decisions and constraints** — choices the work follows from now on, with the reason and who made them (you or the agent). They are superseded, never edited.
- **Evidence** — what the machine or you actually said, byte-exact: a test result, an error, a diff. Never paraphrased.
- **Notes** — what the agent observed and concluded at each step, and **next**: whose turn it is and what happens next.

The screen shows a thread as three lines a person can read without opening anything: how far it got, what is next, and why. The rules behind that are enforced by the server rather than by the agent's memory: a thread needs a topic, a *next* needs an owner, the first line of everything is a title (at most 120 columns, no record numbers in it), and evidence is cited by number instead of being pasted into prose.

## Hosts

- **Claude Code** — with session hooks, your open threads appear when a session starts.
- **Codex CLI** — registered with `codex mcp add`; say "show my open Worktrail threads" once when you start.
- **claude.ai** — as a connector, signed in with OAuth (hosted server only).

Works on **macOS, Linux and WSL**. Native Windows is not supported yet: the installer and the launcher are `sh` scripts.

## Install

The easy way is the hosted server. Sign in at https://casebook-api.syncflo.cloud/join, read the data notice, and paste the one install line it gives you into a terminal. That line installs the client, registers the MCP server with whichever agents it finds, and installs the hooks. Afterwards `casebook-ui` opens the window and `casebook-login` signs you in from a terminal.

Two things worth knowing:

- The MCP server is registered under the name **`casebook`**. That is its original name, kept so that nothing already installed breaks. Say "casebook" to your agent.
- The installer asks where records live: on the hosted server, or only on your machine. During the test period the hosted server is recommended — fixes reach you without reinstalling, and the author can see what breaks. What is stored, who can read it, and how to delete it is written on the sign-up page.

### From source (records stay on your machine)

```bash
git clone https://github.com/jasonethicseo/worktrail.git && cd worktrail
python3 -m venv .venv && .venv/bin/pip install -e ".[mcp]"
mkdir -p ~/.casebook && echo local > ~/.casebook/mode      # records in a local sqlite file, no server

# Claude Code
claude mcp add casebook -s user \
  -e CASEBOOK_DB=$PWD/casebook.db -e CASEBOOK_MCP_EMAIL=you@example.com \
  -- $PWD/.venv/bin/python -m casebook.adapters.mcp_proxy
sh tools/hooks/install_claude_hooks.sh          # optional: session-start and post-commit hooks

# Codex CLI: the same, as `codex mcp add casebook --env CASEBOOK_DB=... --env CASEBOOK_MCP_EMAIL=... -- ...`

.venv/bin/python -m casebook.adapters.ui_server  # the window
```

## Layout

- `casebook/core` — the record: threads, turns, evidence, decisions, the first-line rules.
- `casebook/adapters` — the doors: `mcp_server` (stdio and HTTP), `mcp_proxy` (thin client), `http_api`, `ui_server` (the window), `device_login`, `client_dist` (what the install line delivers).
- `web/worktrail` — the screen. `web/join` — the sign-up page.
- `tools/hooks` — Claude Code and git hooks. `tools/demo_*` — the demo's fictional records.
- `tests` — run with `.venv/bin/python -m pytest -q`.

## License

[Functional Source License 1.1, Apache 2.0 Future License](LICENSE) (FSL-1.1-ALv2, the license Sentry uses). You may use, copy, modify and redistribute it for any purpose except a competing product or service. Two years after each version is released, that version becomes Apache 2.0. GitHub lists it as "Other" because it is source-available rather than open source.
