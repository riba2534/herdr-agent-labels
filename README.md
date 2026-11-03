<h1 align="center">herdr-agent-labels</h1>

<p align="center">
  <strong>一眼分清每个 Agent 是谁、在干什么、从什么时候开始</strong>
  <br />
  给 Herdr 侧边栏的 Agent 加上 Claude / Codex 图标、易读的状态说明和状态时间，正在跑的、跑完没看的、闲置很久的一目了然。
</p>

<p align="center">
  <a href="https://herdr.dev/"><img src="https://img.shields.io/badge/Herdr-0.9.1%2B-7C3AED?style=for-the-badge" alt="Herdr 0.9.1+" /></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.9+" />
  <a href="https://www.nerdfonts.com/"><img src="https://img.shields.io/badge/Nerd%20Fonts-3.5%2B-1F6FEB?style=for-the-badge" alt="Nerd Fonts 3.5+" /></a>
  <a href="https://github.com/riba2534/herdr-agent-labels/stargazers"><img src="https://img.shields.io/github/stars/riba2534/herdr-agent-labels?style=for-the-badge&color=f5a623" alt="GitHub Stars" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-0F766E?style=for-the-badge" alt="MIT License" /></a>
</p>

<p align="center">
  <a href="#安装">安装</a> ·
  <a href="#配置侧边栏">配置侧边栏</a> ·
  <a href="#状态时间">状态时间</a> ·
  <a href="#多台机器">多台机器</a> ·
  <a href="#自定义">自定义</a> ·
  <a href="#工作原理">工作原理</a> ·
  <a href="#开发">开发</a> ·
  <a href="#常见问题">常见问题</a>
</p>

---

Herdr 的侧边栏默认用颜色圆点表示 Agent 状态：运行中、已完成、等待确认都是实心圆点，只靠颜色区分；Claude Code 和 Codex 也只显示一个英文名。同时开十几个 Agent、分布在几台机器上时，很难一眼看出哪些还在跑、哪些已经跑完、哪些已经闲置好几天。herdr-agent-labels 是一个 Herdr 插件，给每个 Agent 挂上对应的图标、一行状态说明和进入这个状态的时间，配合 Herdr 自带的按状态排序，同一状态的 Agent 自然排在一起。

**只改显示，不碰状态。** 插件只写入 Herdr 的展示元数据，不影响 Agent 的状态判定、通知和 `agent wait`。

```
agents                              priority
✓ ✳ · homebig · hpc-mail
  已完成·未查看 · 13:05 起
◐ ✳ · homebig · herdrx
  运行中 · 12:40 起
◐ ◎ · macminim6 · viral-bot · agent-1
  运行中 · 13:12 起
○ ✳ · devcn · copa · 微信转发
  空闲 · 11-02 23:10 起
○ ✳ · macminim6 · persona
  空闲 · 10-30 08:15 起
```

<p align="center"><sub>终端里 ✳ 显示为 Claude 星芒图标，◎ 显示为 OpenAI 花结图标（Nerd Fonts），这里用普通字符代替。</sub></p>

## 你可以做什么

| 功能 | 说明 |
|---|---|
| Agent 图标 | Claude Code 显示 Claude 星芒，Codex 显示 OpenAI 花结；其他 Agent 显示它的名字，也可以在配置里指定图标 |
| 状态说明 | 运行中、已完成·未查看、待你确认、空闲、状态未知；按系统语言自动选择中文或英文 |
| 状态时间 | 进入当前状态的时间：运行中显示开始时间，已完成和空闲显示完成时间；当天的只显示时间，更早的显示日期和时间 |
| 自动生效 | 新开的 Agent 被 Herdr 识别后立即挂上标识；Herdr 重启后自动补上 |
| 多台机器 | 每台运行 Herdr 的机器各装一份，Saved SSH machines 里的 Agent 同样显示 |
| 一键还原 | 「清除 Agent 标识」动作删除插件写入的全部显示内容 |

## 安装

需要 Herdr 0.9.1 或更新版本，以及系统自带的 `python3`（3.9 或更新）。插件只用 Python 标准库，不需要安装依赖。

终端字体需要 [Nerd Fonts](https://www.nerdfonts.com/) **3.5.0 或更新版本**，Claude 和 OpenAI 图标是 3.5.0 才加入的。macOS 可以用 Homebrew 安装，再把终端字体设为 `Hack Nerd Font Mono`：

```bash
brew install --cask font-hack-nerd-font
```

安装插件，并给已经在运行的 Agent 补上标识：

```bash
herdr plugin install riba2534/herdr-agent-labels
herdr plugin action invoke riba2534.agent-labels.apply
```

安装时 Herdr 会展示插件会执行的命令，确认后注册。之后新开的 Agent 会自动挂上标识，不需要再手动执行。

更新：Herdr 0.9.5 及以上运行 `herdr plugin update riba2534.agent-labels`；更早的版本重新执行一次 `herdr plugin install`。

## 配置侧边栏

插件只提供图标和文案，侧边栏怎么排列由你自己的 `config.toml` 决定。把下面的配置加进 Herdr 配置文件（默认 `~/.config/herdr/config.toml`），已有 `[ui]` 段的话合并进去：

```toml
[ui]
status_indicators = "symbols"
agent_panel_sort = "priority"

[ui.sidebar.agents]
rows = [
  ["state_icon", { token = "$logo", rules = [{ equals = "\uEC82", fg = "#D97757" }, { equals = "\uEC81", fg = "#E6E6E6" }] }, "machine", "workspace", "tab"],
  [{ token = "state_text", rules = [{ equals = "运行中", fg = "#f9e2af", bold = true }, { equals = "已完成·未查看", fg = "#94e2d5", bold = true }, { equals = "待你确认", fg = "#f38ba8", bold = true }, { equals = "Running", fg = "#f9e2af", bold = true }, { equals = "Done · unread", fg = "#94e2d5", bold = true }, { equals = "Needs you", fg = "#f38ba8", bold = true }, { equals = "working", fg = "#f9e2af", bold = true }, { equals = "done", fg = "#94e2d5", bold = true }, { equals = "blocked", fg = "#f38ba8", bold = true }] }, { token = "$since", fg = "#89b4fa", dim = false }],
]
```

保存后运行 `herdr config check` 检查格式，再在 Herdr 里执行 reload config 生效。Herdr 的配置解析只支持 TOML 1.0，行内表不能换行，所以规则写在同一行。

| 配置 | 作用 |
|---|---|
| `status_indicators = "symbols"` | 状态图标换成形状不同的符号，不再只靠颜色区分 |
| `agent_panel_sort = "priority"` | 按状态排序，同一状态的 Agent 排在一起 |
| 第 1 行 | 状态图标、Agent 图标、机器、工作区、标签页；Claude 图标橙色，OpenAI 图标白色 |
| 第 2 行 | 状态说明和状态时间；运行中、已完成·未查看、待你确认分别用黄、青、红色加粗，时间用蓝色 |

各状态的显示：

| Herdr 状态 | 图标 | 中文 | English |
|---|---|---|---|
| `blocked` | × | 待你确认 | Needs you |
| `done` | ✓ | 已完成·未查看 | Done · unread |
| `working` | ◐ | 运行中 | Running |
| `idle` | ○ | 空闲 | Idle |
| `unknown` | · | 状态未知 | Unknown |

想在第 3 行显示 Claude 的会话标题，在 `rows` 里加一行 `["terminal_title_stripped"]`。

## 状态时间

Herdr 不提供状态变化的时间，插件在每台机器上自己记录：

- **状态变化时记下时间。** 运行中显示开始时间，跑完后显示完成时间。
- **看过不改时间。** 点开一个「已完成·未查看」的 Agent 后它变成「空闲」，时间仍然是它跑完的时间，而不是你查看的时间。
- **补上已有 Agent 的时间。** 插件安装或 Herdr 重启时，已经在运行的 Claude Code 会话从会话记录推算：运行中的取你最后一条指令的时间，其他状态取会话记录最后写入的时间。Codex 等其他 Agent 等到下一次状态变化后显示。
- **格式。** 当天的只显示时间 `13:05 起`，更早的显示日期和时间 `11-02 23:10 起`；英文为 `since 13:05`、`since 11-02 23:10`。

时间是写入侧边栏的文字，不会自己走动。是否显示日期在这台机器上有 Agent 状态变化时重新判断，一台机器整晚没有任何变化的话，第二天早上它显示的可能还是不带日期的前一天时间，直到有 Agent 开始干活。记录保存在 Herdr 的插件状态目录里，同一台机器上的多个 Herdr 会话各自记录。

## 多台机器

侧边栏由你本机的 Herdr 客户端绘制，使用本机的 `config.toml`；但图标和状态说明是写在 Agent 所在的那台 Herdr server 上的。所以使用 [Saved SSH machines](https://herdr.dev/docs/connecting-machines/) 时，**每台运行 Agent 的机器都要安装插件**，侧边栏配置只需要改本机。

`herdr --machine` 不转发插件安装，需要登录到每台机器执行：

```bash
ssh workbox 'herdr plugin install riba2534/herdr-agent-labels --yes && herdr plugin action invoke riba2534.agent-labels.apply'
```

远程机器不需要安装 Nerd Fonts，字体只和你本机的终端有关。没装插件的机器上的 Agent 显示 Herdr 默认的英文状态，没有图标；上面的配置同样会给这些英文状态上色。

## 自定义

插件配置文件是插件配置目录下的 `config.json`，目录位置用下面的命令查看：

```bash
herdr plugin config-dir riba2534.agent-labels
```

```json
{
  "locale": "zh-CN",
  "logos": {
    "claude": "\uEC82",
    "codex": "\uEC81",
    "gemini": "✦"
  },
  "labels": {
    "done": "跑完了"
  }
}
```

| 字段 | 说明 |
|---|---|
| `locale` | `auto`（默认，按 `LC_ALL`、`LC_MESSAGES`、`LANG` 的顺序判断，以 `zh` 开头用中文）、`zh-CN` 或 `en` |
| `logos` | 按 Herdr 的 Agent 种类覆盖图标；设为空字符串表示不显示 |
| `labels` | 按状态覆盖文案，键为 `working`、`done`、`idle`、`blocked`、`unknown` |

修改后执行一次 `herdr plugin action invoke riba2534.agent-labels.apply`。侧边栏的颜色规则按文字匹配，改了图标或文案，记得同步修改 `config.toml` 里对应的 `equals`。

## 工作原理

```
Herdr server ── 识别到 Agent / 状态变化 ──▶ agent_labels.py ── pane report-metadata ──▶ $logo + $since + 各状态文案
                                                                                               │
Herdr 客户端侧边栏 ◀── config.toml 的行布局引用 $logo、state_text 和 $since ◀─────────────────────┘
```

- 插件通过 `herdr pane report-metadata` 给每个 Agent 写入展示元数据：`logo` 和 `since` 两个自定义字段，以及五种状态各自的文案（`state_labels`）。
- 状态文案一次写好，Herdr 按 Agent 当前的状态挑选显示；时间由状态变化事件更新。插件没有常驻进程，每次事件执行一次就退出。
- 三个触发点：Herdr 启动后的 `startup` 钩子给已有 Agent 补上标识和时间；`pane.agent_detected` 事件在新 Agent 出现或种类变化时更新，Agent 退出时清除；`pane.agent_status_changed` 事件记录状态变化的时间，只写 `since` 字段。
- 状态时间记录在插件状态目录下的 `since-<server>.json`，按 Herdr server 区分，多个钩子同时运行时用文件锁串行。
- 展示元数据不参与状态判定，Agent 的状态、通知、`agent wait` 和排序都由 Herdr 自己决定。

```
herdr-plugin.toml   插件清单：启动钩子、两个事件钩子、两个动作
agent_labels.py     写入与清除 Agent 标识，记录状态时间
tests/              单元测试
```

## 开发

```bash
git clone https://github.com/riba2534/herdr-agent-labels.git
cd herdr-agent-labels
herdr plugin link "$PWD"                                   # 用本地目录注册插件
herdr plugin action invoke riba2534.agent-labels.apply     # 给当前 Agent 写入标识
herdr plugin log list --plugin riba2534.agent-labels       # 查看插件运行记录
python3 -m unittest discover tests                         # 单元测试
```

`plugin link` 不会触发启动钩子，修改代码后手动执行一次 `apply` 查看效果。用 `herdr agent get <pane_id>` 可以看到插件写入的 `tokens` 和 `state_labels`。

## 常见问题

**图标显示成方框或空白？** 终端字体需要是 Nerd Fonts 3.5.0 或更新版本。刚安装或升级字体后，完全退出终端再打开；运行中的终端可能还在使用旧的字体缓存。

**为什么「已完成·未查看」排在「运行中」上面？** 这是 Herdr `priority` 排序的固定顺序：待你确认 → 已完成·未查看 → 运行中 → 空闲。插件不改变排序。

**侧边栏的 agents、priority 能显示成中文吗？** 不能。这些是 Herdr 内置的界面文字，插件和配置都改不了。

**会影响 `agent wait`、通知或自动化脚本吗？** 不会。插件写入的是展示元数据，Herdr 的状态判定、等待和通知都不读取它。

**远程机器上的 Agent 没有图标？** 那台机器没有安装插件，或者插件安装前 Agent 就已经在运行。登录那台机器安装插件，再执行一次 `apply` 动作。

**状态时间不准？** 插件安装前就在运行的 Agent，时间是从 Claude Code 会话记录推算的；Codex 等其他 Agent 要等下一次状态变化。如果某个 Agent 的时间明显不对，执行一次「清除 Agent 标识」再执行「重新设置 Agent 标识」，会重新推算。

**怎么卸载？** 先执行「清除 Agent 标识」，再卸载插件，最后删掉 `config.toml` 里加的侧边栏配置：

```bash
herdr plugin action invoke riba2534.agent-labels.clear
herdr plugin uninstall riba2534.agent-labels
```

## 许可

[MIT](LICENSE)
