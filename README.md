# GPT-SoVITS Auto

把「在 GPT-SoVITS WebUI 里选模型、填台词、点生成、另存」的流程自动化：

- 读取 `.md` / `.txt` 台词文档；
- 按角色解析台词，自动切换 GPT / SoVITS 权重和参考音频；
- 逐条调用 GPT-SoVITS 的推理接口生成 WAV；
- 按 `角色/项目/编号_台词.wav` 保存；
- 支持断点续跑、失败重试、报告和清单；
- 提供 Windows 图形界面（双击 `run_ui.bat`），可以选文档、预览、生成、管理音色；
- 提供 `voices scan/add/remove`，方便接入自己的音色；
- **只读** GPT-SoVITS 安装目录，不修改其中任何文件。

> **本仓库不附带任何音色模型、GPT/SoVITS 权重或参考音频。**
> 你需要自己准备合法取得的权重和参考音频，再用 `voices add` 添加。
> 文档和示例中的 A/B/C/D 只是占位角色名，不对应任何特定作品或角色。

- 工作区：本目录
- 安装目录（只读）：由 `config\settings.yaml` 的 `install_root` 决定（默认是工作区旁边的 `GPT-SoVITS-v2pro-20250604-nvidia50` 或 `GPT-SoVITS`）
- 输出目录：由 `output.root` 决定（默认是工作区旁边的 `语音output`）

## 界面预览

> 截图使用演示路径 `X:\...` 和占位音色 A/B/C/D，不是真实本机路径或真实角色。

![生成语音页](docs/screenshots/generate.png)

![设置](docs/screenshots/settings.png)

---

## 1. 为什么不是“自动点击 WebUI”

当前运行的 TTS WebUI（`inference_webui_fast.py`，端口 9872）没有暴露“生成”接口；它的生成按钮和预设操作会写入安装目录里的 `presets\history.json`、`presets\ui_config.json`、`weight.json` 等文件。自动点击 WebUI 既会破坏只读约束，也很不稳定。

本脚本改为调用 GPT-SoVITS 自带的 `api_v2.py`。它和 WebUI 使用同一套 `TTS_infer_pack` 推理代码、同一批权重和参考音频，只是把“选模型/填台词/点生成”换成 HTTP 请求：

1. `GET /set_sovits_weights`：切换 SoVITS；
2. `GET /set_gpt_weights`：切换 GPT；
3. `GET /set_refer_audio`：切换参考音频；
4. `POST /tts`：填入台词并拿回 WAV 字节。

API 服务由脚本自动启动在 `127.0.0.1:9880`，生成结束后自动停止（可用 `--keep-server` 保留）。

> 重要：`api_v2.py` 在切换权重时会重写它的 `-c` 配置文件。脚本传入的是工作区里的副本 `config\tts_infer.local.yaml`，不是安装目录里的 `GPT_SoVITS\configs\tts_infer.yaml`。

---

## 2. 环境要求

- Windows 10/11；
- 可用的 GPT-SoVITS 安装目录（需要 `api_v2.py`、`runtime\python.exe`、权重目录和 `presets`）；
- 你自己的 GPT/SoVITS 权重、参考音频和参考文本；
- 安装目录的 runtime 已带 `requests`、`PyYAML`；图形界面需要 Tkinter（GPT-SoVITS 自带 runtime 已包含）。

本仓库不下载、不分发任何模型或参考音频。`voices scan` 只会扫描你本机已有的权重和预设。

---

## 3. 目录结构

```text
GPT-SoVITS-Auto\
├─ run.bat                     # Windows 命令行入口（自动找 runtime）
├─ run_ui.bat                  # 双击打开图形界面
├─ run.ps1                     # PowerShell 入口
├─ .gitattributes              # .bat/.ps1 保持 CRLF
├─ add_voice.bat               # 双击添加音色（命令行向导）
├─ ui_main.py                  # 图形界面入口
├─ gptsovits_auto.py           # CLI
├─ SKILL.md                    # 给 agent 读的脚本文案格式规范
├─ auto_tts\                   # 解析、音色、服务、生成、清单等模块
│  ├─ ui.py                    # 主窗口（生成/设置/帮助）
│  ├─ ui_voices.py             # 音色库和添加音色对话框
│  ├─ ui_theme.py              # 颜色、字体、控件样式
│  ├─ ui_dnd.py                # Windows 文件拖入（原生 WM_DROPFILES）
│  ├─ voice_editor.py          # 扫描/添加/删除音色
│  ├─ bootstrap.py             # 初始化与 settings 更新
│  └─ ...
├─ config\
│  ├─ settings.yaml            # 本地配置（安装路径、API、输出、默认参数）
│  ├─ settings.example.yaml    # 开源模板
│  └─ tts_infer.local.yaml     # 每次启动 API 时自动生成的工作区配置
├─ voices\
│  ├─ voices.yaml              # 角色 → 预设/权重/参考音频映射（默认空）
│  ├─ voices.example.yaml      # 开源模板
│  └─ backups\                 # voices add/remove 自动备份
├─ examples\
│  └─ example.md               # 可直接试跑的 A/B/C/D 示例文档
├─ docs\screenshots\           # README 界面截图（演示路径）
├─ tests\                      # 单元测试
├─ state\                      # 断点续跑清单、服务标记
├─ logs\                       # 运行日志、API 日志、CSV 报告
├─ cache\pycache\              # Python 字节码缓存（不写安装目录）
└─ tmp\                        # 临时文件
```

---

## 4. 快速开始

### 4.1 图形界面（推荐新手）

双击工作区里的 `run_ui.bat`。它会自动查找 `GPT_SOVITS_HOME`、旁边的 `GPT-SoVITS-v2pro-20250604-nvidia50`、旁边的 `GPT-SoVITS` 或 PATH 里的 `pythonw`。

第一次打开：

1. 如果顶部显示红色“环境未就绪”，点“设置”，选择 GPT-SoVITS 安装目录和输出目录，点“保存并重新检测”。
2. 打开“音色库”，点“手动添加”或“从预设添加”，先添加至少一个音色（例如 A）。
3. 打开“生成语音”，选择 `.md` / `.txt` 文档；也可以点“载入示例”。
4. 表格会显示每句的角色、音色、输出文件。点“开始生成”。
5. 生成中可点“停止”；当前这一句结束后安全停下。
6. 完成后点“打开输出目录”。

也可以直接把 `.md` / `.txt` / `.markdown` 文件拖进窗口，软件会自动切到“生成语音”并解析预览：

- 文件拖入只接受文本台本文档；音频、图片、压缩包会被忽略并提示。
- 拖入的文档仍按普通文档处理，不会复制或修改原文件。

**随机多录**：在预览表格里选中一句，设置“随机多录”的条数（1–9），点“生成多条”。
软件会用同一个音色、同一句台词、每次不同的随机种子生成多条，并在预览表里紧挨着原句
插入带 `↳` 标记的子行。文件名在原名后加 `_随机01`、`_随机02`……，方便在同一目录里区分；
随机多录不会写断点续跑清单，重复测试不会影响正式生成记录。

“音色库”页可以直接从你安装目录里的预设添加，也可以手动选择 GPT/SoVITS 权重和参考音频。每次添加/删除都会自动备份 `voices\voices.yaml`。

> `run_ui.bat` 使用 `pythonw.exe`，没有黑色控制台。如果窗口没有出现，查看 `logs\ui-crash.log`，或先在终端执行 `run.bat --version` 检查运行时。

### 4.2 命令行

在本工作区目录下打开终端：

```bat
run.bat init
run.bat check
run.bat voices scan
```

如果 GPT-SoVITS 安装在非默认位置，先执行一次：

```bat
run.bat init --install "D:\GPT-SoVITS"
```

`check` 会检查安装目录、运行时、音色、显存和 API 状态。音色数量取决于你自己的 `voices.yaml`，默认是 0。

添加音色（向导）：

```bat
run.bat voices add
```

添加完成后，用示例文档预览：

```bat
run.bat parse --doc examples\example.md
```

正式生成：

```bat
run.bat run --doc examples\example.md --project 示例项目
```

示例使用 A/B/C/D 四个占位角色；请先在 `voices.yaml` 里添加同名音色，或把你的音色加别名 A/B/C/D。

输出示例（取决于你的 `output_dir`）：

```text
<输出根目录>\A\示例项目\0001_雨好像停了，我们还要继续等吗？.wav
<输出根目录>\B\示例项目\0002_你们看，云缝里透出阳光了。.wav
<输出根目录>\C\示例项目\0001_那家咖啡店已经开门了。.wav
<输出根目录>\D\示例项目\0425_我请客，庆祝今天没有迟到。.wav
```

用自己的文档：

```bat
run.bat parse --doc "D:\素材\我的台词.md"
run.bat run   --doc "D:\素材\我的台词.md" --project "我的项目"
```

> `run.bat` 和 `run.ps1` 会依次查找：环境变量 `GPT_SOVITS_HOME`、本目录旁边的 `GPT-SoVITS-v2pro-20250604-nvidia50`、本目录旁边的 `GPT-SoVITS`、PATH 中的 `python`。移动安装目录后执行 `run.bat init --install "新路径"` 即可，不需要改脚本。Linux/macOS 也可以用 `python gptsovits_auto.py ...`。

---

## 5. 台词文档格式

支持 UTF-8（含 BOM）和 GB18030。一行一条台词；空行、`#` 注释、`//` 注释、代码块会被跳过。

如果你让其他 agent 帮你写台词文档，直接让它读根目录的 `SKILL.md`；它包含完整的
格式、编号、角色、清洗和转换规则，不依赖本 README 的上下文。

### 5.1 最常用的写法

```markdown
A：雨好像停了，我们还要继续等吗？
B：你们看，云缝里透出阳光了。
C：那家咖啡店已经开门了。
D：我请客，庆祝今天没有迟到。
```

### 5.2 编号与时间码

```markdown
[0001] A：雨好像停了，我们还要继续等吗？
[0002] B：你们看，云缝里透出阳光了。
[04:25] C：那家咖啡店已经开门了。
[09:02-07:43] D：这一句会使用 0902_0743 作为编号。
```

- 显式编号优先：`[0001]` → `0001_...wav`；
- 其次时间码：`[04:25]` → `0425_...wav`，`[09:02-07:43]` → `0902_0743_...wav`；
- 都没有时，用文档内序号：`0001_...wav`。

### 5.3 竖线、标题、列表、引用

```markdown
0001 | A | 雨好像停了。
A | 我们还要继续等吗？
```

```markdown
## A
雨好像停了。
- 我们还要继续等吗？
> 再等五分钟吧。

## B
你们看，云缝里透出阳光了。
```

`## 角色` 之后，没有写角色名的行会归给该角色。标题不是已配置角色时，会清空当前角色，不会误归给上一个角色。

### 5.4 frontmatter

```markdown
---
project: 雨后的公交站
default_voice: A
text_lang: zh
---

[0001] A：雨好像停了，我们还要继续等吗？
```

- `project`：项目文件夹名；命令行 `--project` 优先；
- `default_voice`：没有角色名的行使用该角色；
- `text_lang`：覆盖本文件的默认语言（仅当音色没有显式 `text_lang` 时）。

### 5.5 清洗规则

- 去掉 Markdown 粗体、链接、图片、HTML 标签、零宽字符；
- 行首的已知情绪标签（如 `【开心】`、`（生气）`）会去掉，不会读出来；
- 保留中文全角标点；
- 空台词、超长台词、未知角色会报错并给出行号。

### 5.6 不支持的格式

- 不解析 SRT / ASS 字幕；
- 不自动按句号拆成多条 WAV（一条输入 = 一个 WAV，内部由 `cut5` 自动切分）；
- 不会自动生成参考音频或 prompt 文本。

---

## 6. 音色配置

`voices\voices.yaml` 默认是空的（`voices: {}`）。本仓库不附带任何音色，需要你自己添加。

文档里的角色名必须能在 `voices.yaml` 的 `key` 或 `aliases` 中找到；例如你用 A/B/C/D 写台词，就给对应音色起名 A/B/C/D。

### 6.1 用向导添加音色

先扫描安装目录、`presets` 和 `extra_model_roots`：

```bat
run.bat voices scan
```

再启动向导：

```bat
run.bat voices add
```

向导会先让你选择“预设音色”或“手动音色”：

- **预设音色**：从你自己安装目录的 `presets` 里选一个，模型、参考音频、prompt 文本和语言会自动读取，只需填角色名、输出目录和别名；
- **手动音色**：从扫描到的 GPT/SoVITS 配对里选，或直接粘贴权重路径，再填参考音频和参考文本；适合模型包、LoRA、自己训练的模型。

非交互式（适合脚本/批处理）：

```bat
run.bat voices add --key A --preset "你的预设名" --output-dir A --alias 角色A --yes
```

写入前会先做一次完整校验（模型版本、参考音频、语言、别名冲突），通过后才备份并原子写入。备份在 `voices\backups\`，默认保留最近 20 份。

### 6.2 手动音色的字段

参考音频、参考文本、语言必须互相对应，否则生成质量会明显变差或直接报错。手动音色的向导等价于下面这段 YAML（也可以直接编辑 `voices\voices.yaml`）：

```yaml
A:
  gpt: ..\models\A\A-e10.ckpt
  sovits: ..\models\A\A_e10_s120.pth
  ref_audio: ..\models\A\ref_A.wav
  prompt_text: 参考音频里说的那句话。
  prompt_lang: zh
  text_lang: zh
  output_dir: A
```

非交互式等价命令：

```bat
run.bat voices add --key A ^
  --gpt "..\models\A\A-e10.ckpt" ^
  --sovits "..\models\A\A_e10_s120.pth" ^
  --ref-audio "..\models\A\ref_A.wav" ^
  --prompt-text "参考音频里说的那句话。" ^
  --prompt-lang zh --text-lang zh --output-dir A --yes
```

字段说明：

- `gpt` / `sovits`：可以写绝对路径，也可以写安装目录下的相对路径，或只写文件名让脚本在 `GPT_weights*` / `SoVITS_weights*` 里查找；
- `ref_audio`：主参考音频；`aux_refs` 可选，写一个列表；
- `prompt_text`：参考音频对应的文字。v3/v4 音色必须提供；
- `prompt_lang`：`zh` / `ja` / `en` / `ko` / `yue`；
- `text_lang`：要合成的台词语言，同上；
- `version`：可选，通常从 SoVITS 文件头自动识别（v1/v2/v3/v4/v2Pro/v2ProPlus）；
- `output_dir`：输出到 `<输出根目录>\<output_dir>\<项目>\`，输出根目录默认 `..\语音output`；
- `speed_factor` / `seed`：可选，覆盖全局默认值。

> 没有参考音频和 prompt 文本的权重不能直接使用。`voices scan` 会把它列出来并标记为未配置；请先准备合法的参考音频和逐字对应的文本。

### 6.3 查看、删除和备份

```bat
run.bat voices list                 # 已配置音色
run.bat voices scan                 # 未配置的模型/预设/模型包
run.bat voices remove --key A       # 删除一个音色（会先备份）
```

`voices remove` 不会删除最后一条音色；删除和添加都会在 `voices\backups\` 留一份带时间戳的 YAML。所有写入都只发生在工作区，不会改安装目录的 `presets`。

---

## 7. 命令行参考

### 7.1 命令

| 命令 | 作用 |
|---|---|
| `run_ui.bat` | 打开图形界面（推荐新手） |
| `run.bat init` | 初始化/补齐默认文件；`--install/--output/--models` 可改路径；已有配置默认不覆盖 |
| `run.bat check` | 检查路径、音色、显存、API |
| `run.bat voices list` | 列出音色、别名、prompt、参考音频 |
| `run.bat voices scan` | 扫描安装目录、预设和模型包，列出已配置/未配置模型；`--json` 便于脚本处理 |
| `run.bat voices add` | 向导添加音色；`--key/--preset/--gpt/... --yes` 可非交互 |
| `run.bat voices remove --key 名字` | 删除音色（先备份） |
| `run.bat parse --doc ...` | 只解析并预览，不生成 |
| `run.bat run --doc ...` | 解析并生成 |
| `run.bat server start` | 启动 API 并保留 |
| `run.bat server status` | 查看 API 状态 |
| `run.bat server stop` | 停止本工作区启动的 API |

示例：

```bat
run.bat init --install "D:\GPT-SoVITS" --output "D:\voice_output" --models "D:\models"
run.bat voices add --key A --preset "你的预设名" --output-dir A --alias 角色A --yes
run.bat voices add --key B --gpt "..." --sovits "..." --ref-audio "..." --prompt-text "..." --prompt-lang zh --text-lang zh --yes
run.bat voices add --key 测试 --preset "你的预设名" --dry-run   # 只打印，不写文件
```

`voices add` 的 `--version` 通常不用填，脚本会从 SoVITS 文件头识别；`--force` 用于覆盖同名音色（仍会备份）。

### 7.2 `run` 常用参数

| 参数 | 说明 |
|---|---|
| `--doc PATH` | 必填，`.md` / `.txt` |
| `--project NAME` | 项目子目录；默认 frontmatter 或文档文件名 |
| `--out-root PATH` | 覆盖输出根目录 |
| `--dry-run` | 只预览，不启动 API |
| `--limit N` | 最多生成 N 条 |
| `--start N` | 从第 N 条开始（1 起） |
| `--order doc` | 按文档顺序；默认 `voice`，按音色分组 |
| `--only-voice NAME` | 只生成指定角色 |
| `--default-voice NAME` | 无角色行的默认角色 |
| `--overwrite` | 重新生成已有音频 |
| `--no-resume` | 关闭断点续跑 |
| `--fail-fast` | 第一条失败就停止 |
| `--server-url URL` | 复用外部 API，例如 `http://127.0.0.1:9880` |
| `--no-server` | 不自动启动 API，只复用现有服务 |
| `--keep-server` | 生成完保留 API |
| `--skip-gpu-check` | 跳过显存预检 |
| `--debug`（全局） | 放在子命令前，例如 `run.bat --debug run ...`，异常时打印完整堆栈 |

### 7.3 退出码

| 代码 | 含义 |
|---|---|
| 0 | 全部成功（或全部已跳过） |
| 1 | 部分失败/部分未执行 |
| 2 | 配置、文档解析或参数错误 |
| 3 | API 服务启动/请求失败 |
| 130 | 用户 Ctrl+C |

---

## 8. 输出、清单、断点续跑

### 8.1 输出

```text
<输出根目录>\<voice_dir>\<project>\<id>_<text>.wav
```

默认输出根目录是工作区旁边的 `..\语音output`，可在 `config\settings.yaml` 的 `output.root` 或 `--out-root` 修改。

- 文件名中的 `<>:"/\|?*` 会替换为 `_`；
- 保留中文全角标点；
- 文本部分默认截断到 80 字符，完整台词在清单里；
- 同目录重名自动加 `_2`、`_3`；
- 已存在且不属于本清单的文件**绝不覆盖**。

### 8.2 清单与报告

- 清单：`state\<project>-<文档sha8>.json`
- 报告：`logs\<project>-<时间戳>.csv`
- 运行日志：`logs\run-<project>-<时间戳>.log`
- API 日志：`logs\api-<时间戳>.log`

同一文档再次运行时，已生成且 WAV 校验通过的条目会跳过：

```text
断点续跑：跳过 1 条已生成且校验通过的音频。
```

想强制重做：

```bat
run.bat run --doc ... --no-resume
run.bat run --doc ... --overwrite
```

---

## 9. 只读保证

脚本只读安装目录，具体做法：

1. **配置副本**：`api_v2.py` 的 `-c` 指向 `config\tts_infer.local.yaml`；切换权重时 `TTS.py` 的 `save_configs()` 只会重写这个工作区副本。
2. **不调用 WebUI 写接口**：不访问 `save_preset`、`save_generated_audio_fast`、`save_ui_config` 等会写 `presets` / `weight.json` 的接口。
3. **不写字节码**：API 以 `-B` 启动，并设置 `pycache_prefix` 指向工作区；父进程也用 `-B`。
4. **写入守卫**：`PathGuard` 只允许写工作区和输出根目录；安装目录、配置路径、参考音频路径都在禁止写入范围内。
5. **输出不在安装目录**：默认输出工作区旁边的 `..\语音output`；`--out-root` 若指向安装目录会直接报错。

已核对以下文件在生成前后 SHA256 不变：

- `GPT_SoVITS\configs\tts_infer.yaml`
- `presets\history.json`
- `presets\ui_config.json`

并且安装目录 `GPT_SoVITS` 下的 `__pycache__` 数量保持不变（均为原有）。

---

## 10. 显存与 WebUI

- 脚本会在 `127.0.0.1:9880` 启动自己的 API；当前 WebUI 的 TTS 服务在 9872，两者可以同时存在。
- 实测加载一个 v2ProPlus 音色约占 2.7 GB 显存；当前 12 GB 显卡 + WebUI 常驻时，脚本默认要求启动前至少 4000 MiB 空闲显存。
- 如果提示显存低于阈值：
  1. 关闭 WebUI 的 TTS 推理页（9872）或主 WebUI（9874）后重试；
  2. 或加 `--skip-gpu-check`（有 OOM 风险）。
- 不要同时跑两个 `run.bat run`：它们会争用 9880 端口和显存。
- 生成中途 OOM 会停止后续条目并保留已完成清单；关闭 WebUI 后重新执行同一命令即可续跑。

---

## 11. 常见问题

**`未知角色“XXX”（请在 voices.yaml 中配置，或添加 aliases）`**  
运行 `run.bat voices add` 添加角色，或给现有音色加别名；先 `run.bat voices list` 确认。

**`音色配置缺少 voices: 映射` / `音色配置文件不存在`**
先执行 `run.bat init`；首次使用 `voices.yaml` 是空的，需要 `voices add`。

**API 返回 400 / `tts failed`**  
看 `logs\api-*.log` 和 `logs\run-*.log`。常见原因：参考音频不存在、`prompt_text` 与参考音频不匹配、`text_lang` / `prompt_lang` 不被该版本支持、v3/v4 缺少 `prompt_text`。

**API 启动超时**  
看 `logs\api-*.log` 末尾；确认 9880 没被别的程序占用；首次加载模型可能需要 1–2 分钟。

**`run.bat` 提示 `GPT-SoVITS runtime not found`**  
把安装目录设为环境变量（当前窗口或系统变量）：

```bat
set GPT_SOVITS_HOME=D:\GPT-SoVITS
run.bat init --install "%GPT_SOVITS_HOME%"
```

也可以把安装目录放到工作区旁边并命名为 `GPT-SoVITS`，或确保 PATH 里有 Python 3.9+ 和 `requests`/`PyYAML`。

**端口 9880 被占用**  
先 `run.bat server stop`；如果是别的 `api_v2.py`，用 `--server-url http://127.0.0.1:9880` 明确复用，或关闭它。

**文档编码乱码**  
优先保存为 UTF-8；脚本也会尝试 GB18030。报错时会显示行号。

---

## 12. 已验证内容

- 45 个单元测试全部通过（解析、命名、配置、音色、服务配置、WAV 校验、音色编辑器/发现/备份、随机多录命名、`SKILL.md` 和通用示例）。
- 生成流程已用 4 句示例和本机音色映射实际验证（A/B/C/D 为占位名，仓库不包含这些音色）；验证了：
  - API 自动启动/停止；
  - 权重切换；
  - 中文台词和中文路径；
  - 多参考音频音色；
  - WAV 头校验和时长读取；
  - 断点续跑跳过；
  - 安装目录关键文件哈希不变、无新增 `.pyc`。
- 持久服务 `server start` / `status` / `stop` 已验证。
- `voices scan` 已验证：会按你本机的权重和预设列出配对、未配置模型和模型包（数量取决于你的安装目录）。
- `voices add` 的预设/手动 dry-run、实际添加/删除、重复键保护和自动备份已验证。
- `run.bat --version`、`run.bat check`、`init --install` 的路径更新逻辑已验证；`run.bat`/`add_voice.bat` 使用纯 ASCII + CRLF，避免 cmd.exe 解析中文注释出错。
- 图形界面已验证：主窗口、生成页解析、音色库、添加预设音色、手动添加音色、设置页、帮助页；从界面生成 4 句示例成功，模型切换和断点续跑正常；`run_ui.bat` 能启动 `pythonw` 进程。
- 拖入 `.md` 文件已验证：会切到“生成语音”并自动解析；Windows 原生 `WM_DROPFILES` 不依赖 TkDND，注册失败时“浏览…”仍可用。
- 随机多录已验证：对同一句生成 2 条，预览表在原句下插入 `↳1`/`↳2` 两行，文件名为 `原名_随机01.wav` / `_随机02.wav`，状态显示“随机多录完成 2 / 失败 0”。
- `SKILL.md` 已验证包含 frontmatter、A/B/C/D 示例、编号/时间码、角色写法和 agent 检查清单；`examples/example.md` 只使用通用角色和场景。

---

## 13. 限制与后续可扩展

- 一条台词生成一个 WAV；不解析 SRT/ASS。
- 不自动生成参考音频、prompt 文本或情感标签。
- 不做文本语言自动识别；请在 `voices.yaml` 里写 `text_lang`。
- 不自动关闭或控制 WebUI。
- 图形界面一次只处理一个文档；还没有“任务队列/多文档批量”界面，批量多文档仍建议用命令行。
- 图形界面主要在 Windows + 自带 runtime 上验证；Linux/macOS 可用 `python ui_main.py`，但需要自行安装 Tkinter。
- 需要更多音色时，用 `run.bat voices scan` 找到模型，再 `run.bat voices add` 或界面“音色库 → 手动添加”；没有参考音频的模型无法直接使用。
- 参考音频仍建议人工试听确认；脚本不会自动生成参考音频、prompt 文本或情感标签。

---

## 14. 开源发布

仓库已包含 `.gitignore`、`requirements.txt`、MIT `LICENSE`、`config/settings.example.yaml`、`voices/voices.example.yaml`、`README.en.md`、`CONTRIBUTING.md`。发布前注意：

- 不要提交本地 `config/settings.yaml`、`config/tts_infer.local.yaml`、`voices/voices.yaml`、`voices/backups/`、`state/`、`logs/`、`cache/`、`tmp/`；`.gitignore` 已覆盖。
- 不要把自己的绝对路径写进示例文件；示例使用 `..\GPT-SoVITS`、`..\语音output` 这类相对路径。
- **不要分发音色模型、权重、参考音频或角色相关的截图/文案**；使用者应自行下载和配置。
- GPT-SoVITS 上游是 MIT；本项目的 `LICENSE` 也使用 MIT。若你希望换成 Apache-2.0/GPL，发布前替换 `LICENSE` 和 README 中的许可证说明。
- 示例中的 A/B/C/D 只是占位角色名；请勿把它们改成受版权保护的角色名或作品名。
