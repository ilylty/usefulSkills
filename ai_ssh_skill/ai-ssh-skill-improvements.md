# ai-ssh Skill Improvement Notes (from LunaGC install session)

> Concrete pain points and a revised CLI design, including watch/observability. This version incorporates requested fixes: merge write/validate, add JSON examples, improve proc-watch semantics, add file ops, and add persistent shell session APIs.

## 1) High-Impact Pain Points Observed

### 1.1 Long-running commands time out without continuation
**What happened**
- `git clone` and `docker run` (image pull) exceeded default timeout.

**Why it's a problem**
- Unclear whether the task is still running, failed, or stuck.

**Suggested improvement**
- Add `job-run` and `job-status` subcommands (see section 2.3).

---

### 1.2 Hard to detect service restart loops (flapping)
**What happened**
- `lunagc.service` failed due to config issues and repeatedly restarted.
- Detected manually via `systemctl`/`journalctl` + htop PID churn.

**Suggested improvement**
- Add `service-watch` and `service-fail-summary` (see 2.1 / 2.2).

---

### 1.3 No network activity / download progress visibility
**What happened**
- To infer whether downloads were still in progress, network speed was watched manually.

**Suggested improvement**
- Add `net-watch` to report rx/tx rates and "active download" status (see 2.4).

---

### 1.4 Config file errors are opaque
**What happened**
- Server failed with a generic "config.json" load error, required manual regeneration.

**Suggested improvement**
- Merge validation into `write-json` and return validation results in the response (see 2.5).

---

### 1.5 Output truncation makes diagnostics slower
**What happened**
- `docker run` / `apt-get install` output was truncated, requiring manual tool output reads.

**Suggested improvement**
- Add `--output-file` / `--save-output` on all exec-like commands (see 2.6).

---

### 1.6 Lack of basic file ops
**What happened**
- Needed `exists`, `ls`, `mkdir`, `mv`, `rm`, `cp` and had to fall back to raw shell.

**Suggested improvement**
- Add `file-*` primitives (see 2.7).

---

### 1.7 No persistent interactive shell
**What happened**
- `shell-run` is unreliable for interactive flows.

**Suggested improvement**
- Add `shell-open`, `shell-send`, `shell-close` (see 2.8).

---

## 2) Proposed New Capabilities (Concrete CLI Design)

### 2.1 `service-watch` (restart loop detection)
**Goal**: Replace manual `htop` observation for PID churn and restart loops.

**Example**
```
python cli.py service-watch --unit lunagc --duration 60 --interval 2
```

**Proposed JSON**
```json
{
  "unit": "lunagc",
  "duration_s": 60,
  "interval_s": 2,
  "restart_count_start": 3,
  "restart_count_end": 8,
  "pids_seen": [6210, 6235, 6259, 6282],
  "status": "flapping",
  "last_exit_code": 1,
  "last_error": "config.json parse error"
}
```

**Implementation idea**
- Poll `systemctl show <unit>` (MainPID, NRestarts) + `journalctl -u <unit> -n 5`.

---

### 2.2 `service-fail-summary` (root-cause snapshot)
**Goal**: One command to return last failure + likely cause.

**Example**
```
python cli.py service-fail-summary --unit lunagc
```

**Proposed JSON**
```json
{
  "unit": "lunagc",
  "last_exit_code": 1,
  "last_restart_time": "2026-03-12T11:21:31Z",
  "tail": [
    "<ERROR:Grasscutter> There was an error while trying to load the configuration..."
  ],
  "hint": "config.json parse error or schema mismatch"
}
```

---

### 2.3 `job-run` + `job-status` (long task continuity)
**Goal**: handle `git clone`, `docker pull`, etc. without timeout ambiguity.

**Example**
```
python cli.py job-run --cmd "git clone ..." --log /tmp/clone.log
python cli.py job-status --id <job_id>
```

**Proposed JSON (job-run)**
```json
{
  "job_id": "job-20260312-001",
  "pid": 5330,
  "log": "/tmp/clone.log",
  "status": "running"
}
```

**Proposed JSON (job-status)**
```json
{
  "job_id": "job-20260312-001",
  "status": "running",
  "exit_code": null,
  "tail": ["Cloning into ...", "Receiving objects: 63%"]
}
```

---

### 2.4 `net-watch` (download activity)
**Goal**: formalize the "watch network speed" heuristic.

**Example**
```
python cli.py net-watch --iface auto --duration 60 --interval 1 --threshold-kbps 200
```

**Proposed JSON**
```json
{
  "iface": "eth0",
  "avg_rx_kbps": 820.4,
  "avg_tx_kbps": 22.1,
  "active_download": true,
  "samples": [820, 760, 910, 800]
}
```

---

### 2.5 `write-json` (write + validate in one step)
**Goal**: avoid config syntax errors by validating on write and reporting issues.

**Example**
```
python cli.py write-json --path /opt/lunagc/config.json --content @file.json --backup
```

**Proposed JSON**
```json
{
  "path": "/opt/lunagc/config.json",
  "written": true,
  "valid": true,
  "error": null,
  "backup_path": "/opt/lunagc/config.json.bak"
}
```

**Invalid JSON response**
```json
{
  "path": "/opt/lunagc/config.json",
  "written": false,
  "valid": false,
  "error": "JSON parse error at line 12: unexpected token",
  "backup_path": null
}
```

---

### 2.6 Universal output capture
**Goal**: no more silent truncation or missing tail logs.

**Suggestion**
- Add `--output-file /path` option to `exec`, `sudo`, `shell-run`.
- Always return `output_file` when truncation happens.

**Example JSON**
```json
{
  "success": true,
  "stdout": "(truncated)",
  "output_file": "/tmp/exec-20260312.log",
  "truncated": true
}
```

---

### 2.7 Basic file ops
**Goal**: avoid raw shell for routine file actions.

**Proposed subcommands**
- `file-exists --path /etc/nginx/nginx.conf`
- `file-ls --path /opt/lunagc --all --long`
- `file-mkdir --path /opt/lunagc/plugins --parents`
- `file-mv --src a --dst b --overwrite`
- `file-rm --path /opt/lunagc/tmp --recursive --force`
- `file-cp --src a --dst b --recursive --overwrite`

**Example JSON (file-ls)**
```json
{
  "path": "/opt/lunagc",
  "entries": [
    {"name": "config.json", "type": "file", "size": 5498},
    {"name": "resources", "type": "dir", "size": 4096}
  ]
}
```

---

### 2.8 Persistent interactive shell
**Goal**: make interactive flows reliable.

**Proposed subcommands**
- `shell-open` → returns session id
- `shell-send` → send commands to that session
- `shell-close` → close session and return exit state

**Example**
```
python cli.py shell-open --host ...
python cli.py shell-send --session <id> --cmd "sudo systemctl status ssh --no-pager"
python cli.py shell-close --session <id>
```

**Proposed JSON**
```json
{
  "session": "sh-20260312-001",
  "status": "open"
}
```

```json
{
  "session": "sh-20260312-001",
  "output": "...",
  "exit_code": 0
}
```

---

### 2.9 `proc-watch` (PID churn + command-lines)
**Goal**: detect unstable processes while avoiding false positives from old processes.

**Behavior requirements**
- Track only processes that *start during the watch window*.
- Return the **unique command lines** (full `cmdline` or truncated) for processes that match.
- Include an optional `--since` filter and `--min-lifetime-ms`.

**Example**
```
python cli.py proc-watch --pattern java --duration 60 --interval 1 --min-lifetime-ms 200
```

**Proposed JSON**
```json
{
  "pattern": "java",
  "window_s": 60,
  "spawn_count": 6,
  "pids": [6210, 6235, 6259, 6282],
  "avg_lifetime_s": 4.1,
  "status": "unstable",
  "commands": [
    "/usr/bin/java -jar /opt/lunagc/LunaGC-4.6.0.jar"
  ]
}
```

---

## 3) Small UX Fixes

- **Make `exec` return explicit reason** if `exit_code: null` (timeout vs connection drop).
- **Expose `cwd` and `env`** in response for traceability.
- **Add `--json` output for `stat` subfields** to simplify parsing.

---

## 4) Prioritized Roadmap

1. `service-watch` + `service-fail-summary`
2. `job-run` + `job-status`
3. `net-watch` + `proc-watch`
4. `write-json` (with validation result)
5. file ops primitives
6. persistent shell sessions
7. universal output capture

