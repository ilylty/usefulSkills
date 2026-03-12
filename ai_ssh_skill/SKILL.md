---
name: ai-ssh
description: >
  Paramiko-based SSH remote executor CLI for AI agents. Use this skill whenever
  the user wants to connect to Linux servers via SSH, run remote commands,
  use sudo, read or write remote files, upload/download files, collect basic
  system info, or debug server issues. Prefer this skill for concrete remote
  operations over giving only theoretical shell advice.
compatibility:
  tools:
    - bash
---

# Overview

This skill teaches you how to use the `ai-ssh` CLI implemented in this
workspace (the `cli.py` script in `./scripts`) to
perform real SSH operations for the user.

The CLI is built on Paramiko and exposes high-level subcommands that:

- Connect and validate SSH access
- Run non-interactive commands (`exec`)
- Run commands via `sudo` (`sudo`)
- Upload and download files via SFTP (`upload` / `download`)
- Read and write remote text files (`read` / `write`)
- Run PTY-based commands using an interactive shell with markers (`shell-run`)
- Collect basic system information (`stat`)

Additional capabilities:

- Watch systemd services (`service-watch`, `service-fail-summary`)
- Run long commands as background jobs (`job-run`, `job-status`)
- Observe network throughput (`net-watch`)
- Watch process churn (`proc-watch`)
- Validate and write JSON configs (`write-json`)
- Basic remote file operations (`file-exists`, `file-ls`, `file-mkdir`, `file-mv`, `file-rm`, `file-cp`)
- Persistent shell sessions via tmux (`shell-open`, `shell-send`, `shell-close`)

All subcommands return structured JSON, which you should parse to decide
follow-up actions.

Always use this skill (via `bash` tool) when the user wants actual SSH
interaction with a server, not just example shell commands.


# When to Use This Skill

Use this skill whenever the user asks to:

- "SSH 登录", "连到服务器", "远程执行命令" 等
- Run commands like `hostname`, `df -h`, `systemctl status xxx` on a remote host
- Restart or inspect services with `sudo` on a server
- Upload deployment scripts or configuration files to a remote machine
- Download logs/configs from a server for analysis
- Read or modify remote config files (e.g. `/etc/nginx/nginx.conf`)
- Collect system info (OS, disk usage, uptime) from a remote host
- Test whether SSH credentials work

Prefer this over giving only theoretical command snippets when:

- The user has already provided host/IP, port, username, and authentication
  details (password or key) or references the test server in this workspace.
- The task clearly requires actually running commands on a remote machine.

Do **not** use this skill when:

- The user only wants to learn shell/SSH concepts without touching real hosts.
- No credentials/host info are available and the user does not want you to
  run anything.
- The task is purely local to this repo (e.g. editing code here) with no
  remote interaction.


# Environment and Path

- Working directory: `./scripts`
- CLI entrypoint: `cli.py` in that directory
- Invoke the CLI with `bash` like:

  ```bash
  python cli.py <subcommand> [options]
  ```

Always set `workdir` to `./scripts` when using the
`bash` tool so `python cli.py` runs in the right place.


# Authentication and Common Arguments

Most subcommands share common SSH args:

- `--host` (string, required): target hostname or IP
- `--port` (int, default 22)
- `--user` / `--username` (string, required)
- `--password` (string, optional)
- `--key` (path, optional): private key path
- `--passphrase` (string, optional): key passphrase
- `--timeout` (seconds, default 30): connection/command timeout

Pick the auth method based on what the user provided. Do **not** invent
passwords or keys; only use values explicitly given or stored in local files
the user mentions.

Example (using the sample test server from this workspace):

```bash
python cli.py connect --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx
```

The result is JSON, e.g.:

```json
{
  "success": true,
  "host": "xxx.xxx.xxx.xxx",
  "port": 22
}
```

You should check `success` before proceeding.


# JSON Results Pattern

Most execution-style commands return a JSON object like:

```json
{
  "success": true,
  "stdout": "...",
  "stderr": "...",
  "exit_code": 0,
  "timed_out": false,
  "interrupted": false,
  "duration_ms": 921,
  "host": "1.2.3.4",
  "command": "df -h"
}
```



# Subcommands and How to Use Them

## connect — Test SSH Connection

Use to quickly verify that credentials work.

Example:

```bash
python cli.py connect --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx
```

Behavior:

- Returns `success: true` if a connection can be established and closed.
- Use before running more expensive operations when credentials are new.


## exec — Non-interactive Command Execution

Use for most simple commands that do not require a TTY or interactive input.

Key options:

- `--cmd` (required): shell command to execute
- `--cwd`: remote working directory
- `--env KEY=VALUE` (repeatable): environment variables
- `--allow-dangerous`: bypass dangerous-command guard (only with explicit user
  approval)

Example:

```bash
python cli.py exec --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx --cmd "df -h"
```

Guidelines:

- Prefer `exec` for `hostname`, `uptime`, `df -h`, `systemctl status ...`,
  reading simple files (`cat`), etc.; it is best suited to short, one-off
  utility commands like `df -h`、`systemctl status` 等。
- For long-running scripts or commands with very large output, prefer
  `shell-run`（运行时间较长、输出较多的脚本推荐用 `shell-run`，`exec` 更适合一次性的工具命令，如
  `df -h`、`systemctl status` 等）。
- If the command obviously needs a TTY or interactive conversation (e.g.
  `top`, bare `sudo` that prompts), prefer `shell-run` instead.
- The guard rejects extremely dangerous commands (like `rm -rf /`). Only add
  `--allow-dangerous` if the user explicitly requests that specific command
  and understands the risk.


## sudo — Run Command via sudo

Use when the user explicitly wants to run a root-level command and provides a
password that has sudo rights.

Key options:

- `--cmd` (required): command to run with sudo
- `--sudo-password`: password for sudo (defaults to `--password` if omitted)
- `--allow-dangerous`: same guard bypass as `exec`

Example:

```bash
python cli.py sudo --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx --cmd "systemctl restart nginx"
```

Guidelines:

- Use only when the remote action really needs root.
- Make sure the user knows what will be restarted/changed before running.


## upload — Upload Local File via SFTP

Use when you need to place a local file on the remote host (e.g. deployment
script, config, binary).

Key options:

- `--local` (required): local path
- `--remote` (required): remote path
- `--overwrite`: allow overwriting existing remote file

Example:

```bash
python cli.py upload --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx --local ./app.py --remote /tmp/app.py
```

The JSON result includes `bytes`, `local`, `remote`, `host`.


## download — Download Remote File via SFTP

Use when you need a remote file locally (logs, configs, etc.).

Key options:

- `--remote` (required): remote path
- `--local` (required): local path
- `--overwrite`: allow overwriting existing local file

Example:

```bash
python cli.py download --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx --remote /var/log/syslog --local ./syslog
```


## read — Read Remote Text File

Use when the user wants to inspect the contents of a remote text file.

Example:

```bash
python cli.py read --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx --path /etc/nginx/nginx.conf
```

Result includes `content` field with the full file contents. Summarize or
quote relevant sections instead of dumping enormous files.


## write — Write/Append Remote Text File

Use when editing or creating remote text files.

Key options:

- `--path` (required)
- `--content`: content to write (if omitted, read from stdin)
- `--append`: append instead of overwriting
- `--backup`: move existing file to `path.bak` before writing

Examples:

Overwrite with explicit content:

```bash
python cli.py write --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx --path /tmp/test.txt --content "hello"
```

Or write from stdin:

```bash
echo "new config" | python cli.py write --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx --path /etc/myapp/config.conf --backup
```

When modifying config files, prefer `--backup` and explain to the user what
changed.


## shell-run — PTY Shell Command with Marker

Use when a command behaves differently under a TTY, or when `sudo` inside a
PTY is more reliable. This internally uses `invoke_shell` and a unique marker
to detect completion.

Key options:

- `--cmd` (required)
- `--sudo-password` (optional): sudo password for commands that prompt
- `--allow-dangerous`: guard bypass

Example:

```bash
python cli.py shell-run --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx --cmd "sudo systemctl status ssh --no-pager" --sudo-password rootxxx
```

Guidelines:

- Prefer `exec` when possible; use `shell-run` for commands that need PTY or
  more complex interaction.
- The result format is similar to `exec`, with `output` being the cleaned
  text after removing the marker.


## stat — Basic System Information

Use when the user wants a quick view of system status on the remote host.

Example:

```bash
python cli.py stat --host xxx.xxx.xxx.xxx --port 22 --user temp_usr --password xxx
```

Result:

```json
{
  "success": true,
  "host": "xxx.xxx.xxx.xxx",
  "uname": { ... },
  "os_release": { ... },
  "uptime": { ... },
  "disk": { ... }
}
```

Each nested value is itself a structured result from `run_exec_command()`.
Summarize key points (OS, kernel, uptime, disk usage) for the user.


# Safety and Guardrails

- The CLI includes a `guard` to block clearly dangerous commands (e.g.
  `rm -rf /`, `mkfs`, `reboot`, `shutdown`, recursive `chmod 777 /`).
- Never bypass this guard unless the user explicitly asks to run that exact
  command and acknowledges the risk; only then add `--allow-dangerous`.
- Treat credentials (passwords, keys, IPs) as sensitive; do not echo them in
  summaries unless the user already sees them.
- Prefer read-only operations first (e.g. `stat`, `exec` with info commands)
  before making destructive changes.


# Typical Workflow Patterns

## 1. Diagnose Disk Full Issue

1. Use `connect` to verify SSH works.
2. Use `exec --cmd "df -h"` to inspect disk usage.
3. If logs are large, `read` or `download` specific log files the user cares
   about and summarize.
4. Suggest safe cleanup steps and execute them only with user approval.

## 2. Restart a Service via sudo

1. Confirm with the user which service to restart and the impact.
2. Use `sudo --cmd "systemctl restart <service>"`.
3. Use `exec --cmd "systemctl status <service> --no-pager"` or `shell-run`
   equivalent to verify.
4. Summarize status for the user.

## 3. Edit a Remote Config File

1. `read` the file and analyze content.
2. Propose specific edits and show a diff in the conversation.
3. Once the user agrees, apply changes via `write --backup`.
4. If needed, restart related services via `sudo` and confirm status.


# Audit Logging

The CLI writes a JSONL audit log to `ai_ssh_audit.log` (or the path specified
by `AI_SSH_AUDIT_LOG`). You usually do not need to inspect this file, but you
should remember that all actions are recorded for traceability.


# New Subcommands

## service-fail-summary — Root-cause snapshot for a systemd unit

Example:

```bash
python cli.py service-fail-summary --host <ip> --user <user> --password <pwd> --unit cron.service --tail-lines 20
```

Returns fields like:

- `restart_count`, `last_exit_code`, `tail[]`, `hint`


## service-watch — Detect restart loops (flapping)

Example:

```bash
python cli.py service-watch --host <ip> --user <user> --password <pwd> --unit myapp.service --duration 60 --interval 2
```

Returns `status: stable|flapping`, `pids_seen[]`, restart count deltas.


## job-run / job-status — Long task continuity

Example:

```bash
python cli.py job-run --host <ip> --user <user> --password <pwd> --cmd "git clone ..." --log /tmp/clone.log --exit-file /tmp/clone.exit
python cli.py job-status --host <ip> --user <user> --password <pwd> --job-id <job_id>
```


## net-watch — Download activity heuristic

Example:

```bash
python cli.py net-watch --host <ip> --user <user> --password <pwd> --iface auto --duration 60 --interval 1 --threshold-kbps 200
```


## proc-watch — PID churn for new processes in a window

Example:

```bash
python cli.py proc-watch --host <ip> --user <user> --password <pwd> --pattern java --duration 60 --interval 1 --min-lifetime-ms 200
```


## write-json — Write + validate in one step

Example:

```bash
python cli.py write-json --host <ip> --user <user> --password <pwd> --path /opt/app/config.json --content @./config.json --backup
```


## file-* — Basic remote file ops

Examples:

```bash
python cli.py file-exists --host <ip> --user <user> --password <pwd> --path /etc/os-release
python cli.py file-ls --host <ip> --user <user> --password <pwd> --path /tmp --all --long
python cli.py file-mkdir --host <ip> --user <user> --password <pwd> --path /tmp/mydir --parents
python cli.py file-cp --host <ip> --user <user> --password <pwd> --src /tmp/a --dst /tmp/b --recursive
python cli.py file-mv --host <ip> --user <user> --password <pwd> --src /tmp/a --dst /tmp/a2 --overwrite
python cli.py file-rm --host <ip> --user <user> --password <pwd> --path /tmp/mydir --recursive --force
```


## shell-open / shell-send / shell-close — Persistent shell via tmux

Example:

```bash
python cli.py shell-open --host <ip> --user <user> --password <pwd> --session sh-001 --cwd /tmp
python cli.py shell-send --host <ip> --user <user> --password <pwd> --session sh-001 --cmd "pwd"
python cli.py shell-close --host <ip> --user <user> --password <pwd> --session sh-001
```
