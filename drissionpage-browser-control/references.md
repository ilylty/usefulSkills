# DrissionPage 浏览器控制文档索引

本索引对应源码目录：`./browser_control_md/`

## 快速选路
- 入门总览与对象关系：`browser_control_intro.md`
- 连接/接管浏览器：`browser_control_connect_browser.md`
- 启动参数与浏览器配置：`browser_control_browser_options.md`
- 浏览器对象与标签页管理：`browser_control_browser_object.md`、`browser_control_tabs.md`
- 页面对象类型选择：`browser_control_pages.md`
- 页面访问与页面级操作：`browser_control_visit.md`、`browser_control_page_operation.md`
- 元素交互与元素信息：`browser_control_ele_operation.md`、`browser_control_get_ele_info.md`
- 页面信息与状态：`browser_control_get_page_info.md`
- 智能等待：`browser_control_waiting.md`
- iframe：`browser_control_iframe.md`
- 网络监听与抓包：`browser_control_listener.md`
- 控制台消息监听：`browser_control_console.md`
- 复合动作（鼠标键盘拖拽）：`browser_control_actions.md`
- 截图与录屏：`browser_control_screen.md`
- 文件上传下载：`browser_control_upload.md`
- 模式切换（d/s）：`browser_control_mode_change.md`

## 按任务找文档

### 1) 建立连接与环境准备
- `browser_control_connect_browser.md`
  - Chromium 初始化参数
  - 指定端口/地址
  - 接管已打开浏览器
  - 多浏览器共存与 `auto_port()`
- `browser_control_browser_options.md`
  - `set_argument()` / `remove_argument()`
  - 端口、路径、用户目录、代理、下载路径
  - headless/incognito/new_env 等

### 2) 页面与标签页生命周期
- `browser_control_browser_object.md`
  - `get_tab()` / `get_tabs()` / `latest_tab`
  - `new_tab()` / `activate_tab()` / `close_tabs()`
- `browser_control_tabs.md`
  - Tab 对象相关管理能力
- `browser_control_visit.md`
  - 页面访问流程与导航
- `browser_control_page_operation.md`
  - `get()` / `back()` / `forward()` / `refresh()`
  - 页面脚本执行、窗口设置、缓存与 cookie 操作

### 3) 元素定位、交互与数据读取
- `browser_control_ele_operation.md`
  - 点击、输入、拖拽、hover、JS 执行、滚动
  - 上传/下载触发相关点击流程
- `browser_control_get_ele_info.md`
  - `text` / `attrs` / `attr()` / `property()`
  - 元素位置尺寸、xpath/css path、状态信息

### 4) 等待、稳定性与异常场景
- `browser_control_waiting.md`
  - `wait.load_start()` / `wait.doc_loaded()`
  - `wait.eles_loaded()` / `wait.ele_displayed()`
  - `wait.new_tab()` / 下载等待

### 5) 复杂页面能力
- `browser_control_iframe.md`
  - `get_frame()` / `get_frames()`
  - 跨 iframe 与 iframe 内查找
- `browser_control_listener.md`
  - `listen.start()` / `listen.wait()` / `listen.steps()`
  - `DataPacket`、请求响应读取
- `browser_control_console.md`
  - `console.start()` / `console.wait()` / `console.messages`

### 6) 产出与调试
- `browser_control_get_page_info.md`
  - 页面 html/json/title、状态与窗口信息
- `browser_control_screen.md`
  - 页面截图、元素截图、录屏
- `browser_control_upload.md`
  - 上传与下载相关操作

### 7) 特殊能力
- `browser_control_mode_change.md`
  - `mode` / `change_mode()` / cookies 在 session 与浏览器间转换
- `browser_control_actions.md`
  - 鼠标、键盘、拖入文件文本、组合动作
