# usefulSkills

`usefulSkills` 是一个用于收集和维护 OpenCode/Claude 技能（skills）的仓库，面向「可复用、可落地」的任务能力沉淀。

当前仓库已包含两个可直接使用的技能：`drissionpage-browser-control` 与 `ai_ssh_skill`。

## 仓库目标

- 沉淀高质量、可复用的技能模板与文档。
- 让常见任务可以通过 skill 快速触发并稳定执行。
- 通过触发评测与文档索引，提升技能可维护性。

## 当前目录结构

```text
.
├─ ai_ssh_skill/
│  ├─ SKILL.md
│  └─ scripts/
│     ├─ cli.py
│     ├─ executor.py
│     ├─ file_ops.py
│     ├─ guard.py
│     ├─ sftp_client.py
│     ├─ shell.py
│     ├─ ssh_client.py
│     └─ sudo.py
├─ drissionpage-browser-control/
│  ├─ SKILL.md
│  ├─ references.md
│  ├─ trigger_evals.json
│  ├─ trigger_evals_ascii.json
│  └─ browser_control_md/
│     ├─ browser_control_intro.md
│     ├─ browser_control_connect_browser.md
│     ├─ browser_control_browser_options.md
│     ├─ ...
│     └─ browser_control_waiting.md
└─ LICENSE
```

## 已收录技能

### ai_ssh_skill

适用场景：

- 通过 SSH 连接 Linux 服务器，执行远程命令。
- 支持 sudo、SCP/SFTP 文件上传下载、系统信息采集。
- 适合服务器运维、排障、批量命令执行场景。

技能入口文件：`ai_ssh_skill/SKILL.md`

### drissionpage-browser-control

适用场景：

- 使用 Python + DrissionPage 自动化网页操作。
- 控制 Chromium 标签页/窗口、元素交互、iframe 流程。
- 处理上传下载、抓取网络请求、监听控制台、截图录屏。
- 将 Selenium/Playwright 脚本迁移到 DrissionPage。

技能入口文件：`drissionpage-browser-control/SKILL.md`

文档索引：`drissionpage-browser-control/references.md`

## 使用方式

根据你的运行环境，将技能目录放入对应 skills 路径（例如 OpenCode 本地 skills 目录）后即可被系统按描述自动触发。

如果你在维护技能，建议：

1. 先更新 `SKILL.md` 的触发描述与工作流。
2. 再补充 `browser_control_md/` 对应专题文档。
3. 最后更新触发评测文件并进行回归检查。

## 许可证

本仓库采用 BSD 2-Clause License，详见 `LICENSE`。
