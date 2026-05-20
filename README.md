# Aura Link

The open-source local bridge that connects your machine to the [Aura](https://dev-aura.com) platform. Aura Link runs on your computer and lets Aura's AI agents read files, write code, and run commands in your actual project — securely, without uploading your codebase to the cloud.

## How it works

```
Aura Web (War Room) ──► Aura Backend ──► Aura Link (your machine) ──► your project files
```

Aura Link opens a WebSocket connection to the Aura backend. When you approve a task in the War Room, the agent's file writes and shell commands are forwarded here and executed locally. Nothing leaves your machine except what the agent explicitly needs to read.

## Installation

### Option 1 — pip (recommended)

```bash
pip install aura-link
```

### Option 2 — clone and install

```bash
git clone https://github.com/dev-aura-com/aura-link
cd aura-link
pip install -e .
```

Requires Python 3.11+.

## Usage

**Step 1 — log in**

```bash
aura-link login
```

This opens a browser tab. Click **Authorize** and the CLI stores your credentials automatically — no token copying required.

**Step 2 — connect your project**

```bash
cd /path/to/your/project
aura-link connect
```

The CLI connects to Aura and watches for instructions. Leave this running while you work in the War Room.

**Options**

```
aura-link connect --url https://your-backend    # custom backend URL
aura-link connect --dir /path/to/project        # explicit project directory
aura-link connect --quiet                        # suppress status messages
aura-link logout                                 # remove saved credentials
```

## Environment variables

| Variable      | Description                                         | Default                    |
| ------------- | --------------------------------------------------- | -------------------------- |
| `AURA_SERVER` | Backend API URL                                     | `https://api.dev-aura.com` |
| `AURA_TOKEN`  | JWT access token (alternative to `aura-link login`) | —                          |

## Security

- All file operations are **jailed to the project directory** — path traversal attempts are blocked.
- `.env` files, private keys (`*.pem`, `*.key`, `id_rsa`, etc.), and any file matched by your `.gitignore` are **never sent to the cloud**.
- Destructive shell commands (`rm -rf /`, `sudo`, `shutdown`, etc.) are blocked before execution.
- The source code is fully open — you can audit exactly what Aura Link does before running it.

## License

MIT
