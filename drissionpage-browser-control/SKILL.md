---
name: drissionpage-browser-control
description: Use this skill for DrissionPage-based browser automation and control tasks. Trigger whenever the user asks to automate web pages with Python, control Chromium tabs/windows, interact with elements (click/input/scroll/drag), handle iframe flows, wait for dynamic content, capture network requests or console logs, manage uploads/downloads, connect to an existing browser session, configure ChromiumOptions, or take screenshots/screencasts. Also trigger when users ask to migrate Selenium/Playwright browser scripts to DrissionPage or request robust anti-flaky browser automation patterns. Do not trigger for API-only scraping (no browser), pure framework comparison, or non-browser desktop automation.
compatibility:
  tools: [Read, Glob, Grep, Bash]
---

# DrissionPage 浏览器控制技能

## 目标
把用户的网页自动化需求转换为可运行、稳定、可维护的 DrissionPage 方案。

## 先做什么
1. 先确认用户目标：要访问什么页面、做哪些交互、最终产出是什么（数据、截图、下载文件等）。
2. 再确认运行前提：是否接管已打开浏览器、是否需要登录态、是否要多标签页/iframe/下载目录。
3. 输出时优先给完整脚本，并附上关键步骤说明。

## 标准工作流
1. 创建/接管浏览器对象（`Chromium` + `ChromiumOptions`）。
2. 获取 Tab（通常 `browser.latest_tab` 或 `browser.new_tab()`）。
3. 访问页面（`tab.get(url)`）并等待关键状态。
4. 定位元素（先稳健定位，再交互）。
5. 执行交互（点击、输入、滚动、拖拽、JS、上传下载）。
6. 必要时处理标签页、iframe、网络监听、控制台、截图录屏。
7. 返回结果（脚本、提取数据、文件路径、排错建议）。

## 实战原则
- 少用硬编码 `sleep()`，优先 `wait.*` 智能等待。
- 定位策略优先稳定性：语义化属性/层级关系/可复用定位，而不是脆弱绝对路径。
- 多页场景显式处理 tab：新开、激活、关闭、等待新 tab。
- iframe 场景先确定 frame 边界，再在 frame 内定位。
- 出现波动时优先加“状态等待 + 重试”，不要只盲目拉长超时。

## 输出格式
默认输出包含：
1. 可直接运行的 Python 代码块；
2. 关键步骤说明（浏览器连接、定位、等待、交互、收尾）；
3. 若页面不稳定，附 2-4 条可操作的排错建议。

## 参考文档路由
按任务类型读取 `./browser_control_md/` 下对应文档（详见 `references.md`）：
- 入门流程：`browser_control_intro.md`
- 浏览器连接与配置：`browser_control_connect_browser.md`、`browser_control_browser_options.md`
- 浏览器与标签页对象：`browser_control_browser_object.md`、`browser_control_tabs.md`
- 页面访问与操作：`browser_control_visit.md`、`browser_control_page_operation.md`
- 元素操作与信息：`browser_control_ele_operation.md`、`browser_control_get_ele_info.md`
- 页面信息：`browser_control_get_page_info.md`
- 等待机制：`browser_control_waiting.md`
- iframe：`browser_control_iframe.md`
- 监听与控制台：`browser_control_listener.md`、`browser_control_console.md`
- 截图录屏：`browser_control_screen.md`
- 上传下载：`browser_control_upload.md`
- 模式切换：`browser_control_mode_change.md`
- 页面对象选择：`browser_control_pages.md`
- 复合动作：`browser_control_actions.md`

## 常用骨架
```python
from DrissionPage import Chromium

browser = Chromium()
tab = browser.latest_tab
tab.get('https://example.com')

# 等待关键元素出现
tab.wait.ele_displayed('css:input[name="q"]')

# 交互
tab.ele('css:input[name="q"]').input('DrissionPage')
tab.ele('css:button[type="submit"]').click()

# 等待结果并提取
tab.wait.eles_loaded('css:.result-item')
items = tab.eles('css:.result-item')
for i in items:
    print(i.text)
```

## 常见请求如何响应
- “接管已打开浏览器”：优先读取连接与端口配置文档，给出可接管方案。
- “元素点不到/输入失败”：先给稳定定位 + 等待 + 滚动到可视区方案。
- “点击后开新标签页”：使用新 tab 等待与 tab 切换流程。
- “要抓接口返回”：使用监听器流程，返回请求/响应字段提取代码。
- “要上传或下载文件”：按上传下载章节给出可复用脚本。

## 禁止事项
- 不要编造 DrissionPage API 名称或参数。
- 不要忽略异常与超时处理。
- 不要在复杂页面中只依赖固定时间 sleep。
