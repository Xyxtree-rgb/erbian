# 项目交接文档 · 耳辩

## 一句话
面向青年干部的轻量化辩论训练 + 普法学习工具，产品名「耳辩」（在你耳边，与尔同辩）。黑客松项目，时间紧。

## 当前文件结构
C:\Users\hp\Desktop\啥子文件\app
├── app.py Flask 应用工厂、蓝图注册与启动
├── config.py 资源路径与环境密钥加载
├── routes/
│   └── api.py 页面托管及 API 路由
├── services/
│   ├── deepseek.py AI 裁判与 System Prompt
│   └── policy.py 国务院政策抓取与辩题生成
├── desktop.py pywebview 桌面窗口入口
├── knowledge_base.json 117条案例 + laws法条数组
├── .env DEEPSEEK_API_KEY
├── index.html 主游戏页面（飞行棋）
├── knowledge.html 知识库页面
├── mobile.html 手机版
├── manifest.json PWA配置
├── sw.js Service Worker
├── dist/
│ └── erbian-backend.exe PyInstaller打包的后端
└── assets/
├── keys/ 7张果冻键帽图（AI生成，start/debate/knowledge/mystery/trap/item/finish）
├── arrival.png
└── icon-192/512/1024.png
## 三大功能模块现状

### 1. 飞行棋模式（已上线）
- 20格棋盘，果冻键帽风格
- 三种子模式：单人匹配（AI代打桃弁）、双人对抗、阵营战2v2
- 事件类型：辩题、知识卡、陷阱、道具、终点
- 辩题触发 → AI裁判（DeepSeek）判分 → 加分/连击
- 本局内辩题不重复（usedDebates Set）
- 手记碎片系统（渡口叙事，6片，贯穿整局）

### 2. 案件模式（代码已写，未接入）
- 数据结构：CASES 数组，每个案件含 rounds、每轮含 violations（关键词+damage）
- 玩法：看对方证词 → 玩家输入 → 关键词匹配命中违法点 → 扣血
- 内置3个案件：入职第一天、产假风波、年终奖离职
- 需接入 openModeSelect 的 mode-grid

### 3. 知识库（已上线）
- 117条真实案例（9大分类：消费纠纷/生态环保/动物保护/产品质量/食品安全/劳动争议/知识产权/网络治理/医疗美容）
- laws 数组存法条原文
- 抽屉式加载（iframe），支持 category 和 q 两种URL参数
- 支持法条模式（mode=law）

## 技术栈
- 前端：纯HTML/CSS/JS，单文件，无框架
- 后端：Flask + flask-cors + openai SDK（指向DeepSeek）
- 桌面：pywebview
- 打包：PyInstaller（--onefile）
- 音效：Web Audio API合成，无外部音频文件

## 关键决策（别再推翻）
- 用JSON不用MySQL（数据量小，开发快）
- AI裁判用 DeepSeek API（deepseek-chat，不是deepseek-v4-flash）
- 桌面化用 pywebview（不用Electron，轻量）
- 角色名：**青洏**、**桃弁**（不是"青蓝""粉色"）
- 知识库直接读JSON文件，后端内存查询

## 后端关键接口
- GET  /                     → index.html
- GET  /knowledge-base       → knowledge.html
- GET  /api/knowledge        → 117条案例（支持?category=&q=）
- GET  /api/knowledge?source=policies → 国务院政策生成的辩题
- GET  /api/law?q=刑法       → 法条查询
- POST /api/judge            → AI裁判，body: {input, case_law}
- GET  /api/health           → 健康检查
- GET  /assets/<path>        → 静态资源

## 用户偏好（非常重要，照着做）

### 审美
- 喜欢**扁平插画几何风 + 果冻/玻璃质感**
- 不喜欢纸质感、不喜欢emoji当图标
- 配色：低饱和灰调 + 彩虹渐变高光（`--iri: linear-gradient(100deg,#7ac8ff,#c9a7ff,#ff9ed2,#ffd9a0,#8ef0d2)`）
- 字体：Space Grotesk + Noto Sans SC + Zhi Mang Xing（手写体）

### 内容风格
- 讨厌说教，喜欢**小说式留白**（给场景、给细节，不给结论）
- 喜欢**带梗、带调侃、不严肃**
- 拒绝"法律是温暖的"这类金句总结
- 案例和故事要真实、有人味

### 沟通方式
- 中文交流
- **每次都要完整可运行的代码**，不要片段、不要"其余不变"
- 对技术细节不熟，VSCode、终端、PyInstaller、npm 这些都要**手把手一步步说**
- 每一步都要截图验证
- 遇到报错先看终端输出，再定位

### 交互习惯
- 说"哪个方案"时用字母/数字选（A/B/C）
- 不喜欢反复确认，喜欢直接给方案然后微调
- 提需求喜欢用"我希望它是…"开头

## 已知问题/待办

### 高优先级
- [ ] 案件模式接入 openModeSelect
- [ ] AI回合提示（单人模式：AI思考时禁用骰子 + 顶部显示"桃弁 思考中"）
- [ ] 结果页文案去掉渡口味（"老码头""碎片4/5"改为"本局共X次辩题，Y次有效发言"）
- [ ] FRAGMENTS 数组改成法律常识（不再讲粽子故事）

### 中优先级
- [ ] 打包最终 exe（`--windowed --add-data` 一堆）
- [ ] 知识库扩充到200+条
- [ ] 案件模式数据扩充

### 低优先级
- [ ] AI裁判提示词调严（现在太容易给分）
- [ ] 移动端适配微调

## 常见坑（踩过的）
1. **PowerShell里`pip`不认** → 用 `python -m pip`
2. **PyInstaller打包后资源找不到** → 用 `sys._MEIPASS`，见 app.py 的 `resource_path()` 函数
3. **Flask路由必须在 `app = Flask(__name__)` 之后**，否则 NameError
4. **5001端口被残留进程占用** → `taskkill /F /IM erbian-backend.exe`（要管理员权限）
5. **pywebview 的窗口关掉后 Flask 也跟着停** → 想单独测接口要另开 `python app.py`
6. **函数被吞进别的函数体**（showAIDebate 的闭合括号问题）→ 用 `typeof 函数名` 在 Console 验证
7. **重复定义变量**（`const data` 定义两次）→ VSCode 会报 "Cannot redeclare"

## 重要的代码片段位置

- AI裁判弹窗：index.html 里 `async function showAIDebate(pIdx, d)`
- 掷骰子：`async function rollDice()`
- 落格事件：`async function onLandTile(pIdx)`
- 结局：`function endGame()`
- 本次小记：`function showJournal()`（未接入按钮）
- 案件模式：搜索 `const CASES = [`（未接入）
- 知识库抽屉：`function openDrawer(opts)` / `closeDrawer()`
- 后端AI裁判：app.py 里 `@app.route('/api/judge')`

## 下一步起点

**接入案件模式**（代码已写好，只需接入 mode-grid 和点击逻辑）。

---

**文档版本**：v1.0
**最后更新**：2026-10-14