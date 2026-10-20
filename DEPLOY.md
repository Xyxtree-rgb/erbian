# 耳辩 · 云端部署手册（修正版）

> 配套文件：`D:\耳辩H5\erbian_deploy.zip`（48 个文件 / 26.4 MB，已排除所有垃圾文件）

---

## 一、先看：原教程有 4 个坑，照着做会失败

**坑 1｜文件清单漏了必需的 4 个文件 → 后端一启动就崩**
教程让你传 `index.html、app.py、routes、assets、story.json、story_team.json、requirements.txt`。
漏掉了 `knowledge_base.json`（后端启动时立刻读取它，没有就直接报错退出）、`config.py`、`services/`、`manifest.json + sw.js`。
→ 解决：不用你挑文件了，直接用打好的 zip 包，该有的都在，垃圾一个没有。

**坑 2｜前端写死了 `127.0.0.1:5001` → 公网上 AI 裁判 100% 失败**
不管服务器在哪，浏览器里 `127.0.0.1` 永远指"访问者自己那台电脑"。
所以你在服务器上部署成功、页面能打开，但点"提交发言"必定报错。
→ 已改：`index.html` / `mobile.html` / `knowledge.html` 全部换成相对路径 `/api/judge`。

**坑 3｜根本不需要第二个 8000 端口的 `http.server`**
Flask 本身就在托管整个前端（`/` → index.html、`/assets/*`、`/knowledge-base`、`/story.json`、`/sw.js`）。
按教程跑两个服务反而制造了跨域问题：前端来自 `:8000`，后端在 `:5001`，CORS 还得放行。
→ 现在只跑一个服务、一个端口，同源，跨域问题直接消失。

**坑 4｜`pip install` 那条命令漏了 `requests`**
`services/policy.py` 里 `import requests`（政策库抓取），少装它，点"国务院政策辩题"就 500。
另外 `requirements.txt` 里的 `pywebview` 是桌面版用的，在没装图形界面的 Linux 服务器上会安装失败，把整条 pip 命令带崩。
→ 已单独准备 `requirements-server.txt`（去掉了 pywebview，补齐了 requests / aiohttp）。

**额外修的｜`debug=True` 不能上公网**
原代码 `app.run(debug=True)`，等于把 Werkzeug 调试器挂到公网，任何人能远程执行代码。
→ 已改为由环境变量 `FLASK_DEBUG` 控制，默认关闭。

---

## 二、当前服务器实测状态（重要）

| 检测项 | 结果 |
|---|---|
| ping 47.122.109.160 | ✅ 通，44ms（机器活着） |
| TCP 22（SSH） | ❌ 超时 |
| TCP 5001 | ❌ 不通 |
| TCP 8000 | ❌ 不通 |

**结论：机器开着，但防火墙把端口全挡了。** 所以第一步不是上传，是去控制台放行端口——否则 VS Code 第二步就会卡住，而且看起来像网络问题。

---

## 三、第一步：放行防火墙

阿里云控制台 → 轻量应用服务器 → 点你的实例 → **防火墙** → 添加规则：

| 应用类型 | 协议 | 端口范围 | 用途 |
|---|---|---|---|
| 自定义 | TCP | `22` | SSH，VS Code 连接用，**必加** |
| 自定义 | TCP | `80` | 网页访问（推荐，链接不用带端口） |
| 自定义 | TCP | `5001` | 备用：不想用 80 端口时勾这个 |

保存后不用重启。

---

## 四、第二步：上传

**方式 A（推荐，一个文件拖动）**

1. VS Code → 按 `F1` → 输入 `Remote-SSH: Connect to Host` → 选 `47.122.109.160`
2. 连上后，左侧资源管理器打开你的家目录（root 用户是 `/root`，admin 用户是 `/home/admin`）
3. 把本地 `D:\耳辩H5\erbian_deploy.zip` 拖进左侧窗口，等底部进度条走完
4. 按 `Ctrl + ~` 开终端，执行：

```bash
cd ~
unzip -o erbian_deploy.zip
cd erbian
ls
```

正常情况下能看到 `app.py config.py routes services index.html assets ...`

**方式 B（本地 PowerShell 一条命令）**

```powershell
scp "D:\耳辩H5\erbian_deploy.zip" admin@47.122.109.160:~/
```

> 注意：你的 `~/.ssh/config` 里写的是 `User admin`，但教程里用的是 `root`。以你实际能登录的为准，两边路径不同（root → `/root`，admin → `/home/admin`）。

---

## 五、第三步：装环境

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv unzip

cd ~/erbian
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-server.txt -i https://mirrors.aliyun.com/pypi/simple/
```

装完验证一下（三个都不报错就对了）：

```bash
python -c "import flask, flask_cors, openai, edge_tts, requests; print('依赖OK')"
```

---

## 六、第四步：配 API Key

```bash
cd ~/erbian
nano .env
```

写一行（换成你的真实密钥）：

```
DEEPSEEK_API_KEY=sk-你的真实密钥
```

`Ctrl + O` → 回车保存 → `Ctrl + X` 退出。

> 密钥我没有打进 zip 包（`.env` 已排除），必须手动建，这样不会随压缩包外泄。

---

## 七、第五步：启动

**推荐（80 端口，访问地址最干净）**

```bash
cd ~/erbian
sudo PORT=80 bash start.sh
```

看到健康检查返回 `{"service":"耳辩 AI 裁判","status":"ok"}` 就成功了。
然后浏览器打开：**http://47.122.109.160**

**备用（5001 端口）**

```bash
cd ~/erbian
bash start.sh
```

访问：**http://47.122.109.160:5001**

脚本会自动杀掉旧进程、后台运行、写日志到 `server.log`，SSH 断开也不会停。

---

## 八、别再用 8000

教程第五步让你访问 `http://47.122.109.160:8000` —— **不要用**。
那是给 `python3 -m http.server 8000` 准备的，现在前端由 Flask 直接托管，8000 端口既不需要开，也不需要跑。

只有 3 个地址是对的：

| 地址 | 内容 |
|---|---|
| `http://47.122.109.160/` | 主游戏（飞行棋） |
| `http://47.122.109.160/knowledge-base` | 知识库页面 |
| `http://47.122.109.160/mobile` | 手机版（新加的路由） |

---

## 九、验收清单

```bash
# 1. 服务活着吗
curl http://127.0.0.1/api/health

# 2. 素材能取到吗（应该返回 200）
curl -o /dev/null -w "%{http_code}\n" http://127.0.0.1/assets/keys/start.png

# 3. AI 裁判能调用 DeepSeek 吗
curl -X POST http://127.0.0.1/api/judge \
  -H "Content-Type: application/json" \
  -d '{"input":"这个条款明显违反了消费者权益保护法","case_law":"《消费者权益保护法》第二十六条"}'
```

第 3 条能返回带 `valid` / `damage` / `reason` 的 JSON，说明 DeepSeek 链路完全打通。

---

## 十、报错对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| VS Code 连不上、一直转圈 | 22 端口没放行 | 回第三步加防火墙规则 |
| `Address already in use` | 5001 被旧进程占着 | `pkill -f "python app.py"` 再启动 |
| 页面能开，点提交转圈后失败 | 前端地址没改（旧文件覆盖回来了） | 确认传的是新 zip，`grep -c 127.0.0.1 index.html` 应为 0 |
| `未配置 DEEPSEEK_API_KEY` | `.env` 没建或位置不对 | `.env` 必须在 `~/erbian/` 下 |
| `ModuleNotFoundError: requests` | 用了原来的 requirements.txt | 改用 `requirements-server.txt` |
| 浏览器打开是空白页 | 端口没放行，或用了 8000 | 换 80 端口重新放行 |
| `permission denied` 绑 80 端口 | 非 root 用户 | 命令前加 `sudo` |
| 手机版 404 | 旧版本没有 `/mobile` 路由 | 用新包（已加） |

---

## 十一、这次改了哪些文件

| 文件 | 改动 |
|---|---|
| `index.html` | 2 处 `/api/judge`、1 处知识库抽屉改为相对路径 |
| `mobile.html` | 同上 2 处 |
| `knowledge.html` | `/api/knowledge`、`/api/law` 改为相对路径 |
| `app.py` | CORS 放开到公网；`debug` 改由 `FLASK_DEBUG` 控制；端口改由 `PORT` 控制 |
| `routes/api.py` | 新增 `/mobile` 路由 |
| `requirements-server.txt` | 新增（服务器专用依赖） |
| `start.sh` | 新增（一键后台启动 + 健康检查） |

本地已验证：`/`、`/api/health`、`/knowledge-base`、`/mobile`、`/assets/keys/start.png` 全部 200。

**没有删你的 `build` / `dist` / `__pycache__`** —— 打包时排除就够了，`dist` 里那个 exe 以后还能用，删了不可逆。

---

## 十二、实际部署记录（2026-10-17 18:20 完成）

| 项目 | 实际值 |
|---|---|
| 服务器 | 47.122.109.160 |
| 登录用户 | `admin`（不是 root） |
| 项目路径 | `/home/admin/erbian` |
| 运行端口 | `80` |
| 启动命令 | `sudo PORT=80 bash start.sh` |
| 访问地址 | **http://47.122.109.160** |
| 数据库 | 无（JSON 文件） |
| Key 位置 | `/home/admin/erbian/.env`（scp 传入，未进压缩包） |

### 外网实测结果

| 检查项 | 结果 |
|---|---|
| `GET /` | 200，194494 字节（与本地 index.html 完全一致） |
| `GET /api/health` | `{"status":"ok"}` |
| `GET /assets/keys/start.png` | 200，1148803 字节 |
| `GET /knowledge-base` | 200，68515 字节 |
| `POST /api/judge` | **0.8 秒返回** `damage=15, valid=true`（DeepSeek 链路通） |

### 日常运维命令

```bash
cd ~/erbian

tail -f server.log            # 实时看日志（Ctrl+C 退出）
pgrep -f "python app.py"      # 看服务进程在不在
pkill -f "python app.py"      # 停服务
sudo PORT=80 bash start.sh    # 重启（会自动杀旧进程）
```

### 两个必须记住的事

1. **服务器重启后服务不会自动起来。** `nohup` 只在当前系统会话里保活，机器一重启就没了。重新执行：
   ```bash
   cd ~/erbian && sudo PORT=80 bash start.sh
   ```
   想彻底解决可以配 systemd 开机自启，演示前不必折腾。

2. **更新代码的正确姿势**：
   ```bash
   # 本地 PowerShell
   scp "D:\耳辩H5\erbian_deploy.zip" admin@47.122.109.160:/home/admin/
   # 服务器终端
   cd ~ && unzip -o erbian_deploy.zip && cd erbian && sudo PORT=80 bash start.sh
   ```
   注意 `unzip -o` 会覆盖同名文件，`.env` 在 `~/erbian/` 里不受影响（zip 包里没有它）。

### 安全收尾（演示结束后再做）

现在那个防火墙模板放行的是 `1/65535` 全 TCP 端口，等于服务器所有端口都裸奔在公网。
演示结束后进控制台，把模板改成只留两条：

| 协议 | 端口 | 来源 |
|---|---|---|
| TCP | `22` | 你自己的公网 IP（不要 0.0.0.0/0） |
| TCP | `80` | `0.0.0.0/0` |
