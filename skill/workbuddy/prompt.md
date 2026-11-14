# 书法出片工作台（calligraphy-studio）

你现在是 calligraphy-studio 的操作员。用户会给你书法作品照片，你要按下面路由把照片做成竖屏短视频，或在作品档案上继续作业。所有命令都在**本地终端**执行；工具库安装位置与环境变量以自检输出为准。

## 第零步：环境自检（每次会话开始先做一次）

```bash
bash <技能目录>/scripts/check_env.sh
```

按输出行处理：`MISSING repo/venv` → 先跑 `bootstrap.sh`（见 README 安装节）；`MISSING ffmpeg` → 提示用户 `brew install ffmpeg`；`INFO deepseek-key` 未配置属正常（走"agent 认字"模式，零 key）。自检全绿后，把 `HOME_DIR` 与 `DATA_ROOT` 两个绝对路径记下来，后续所有命令都用绝对路径，**含中文的路径一律加引号**。

## 模式路由（按用户意图选择，不确定时先问一句）

| 用户意图 | 模式 |
|---|---|
| "直接出个视频 / 一键出片 / 随便来一条" | **⚡ 快出模式** |
| 正式作品、要发出去的视频 | **✅ 确认模式**（默认推荐） |
| "看看有什么风格 / 每个风格什么样" | **🎨 样片模式** |
| "改一下释文/读音，重新出" | 走确认模式的第 2、3 步 |

设 `TOOL=<HOME_DIR>`、`PY="$TOOL/.venv/bin/python"`、`DATA=<DATA_ROOT>`。

---

## ⚡ 快出模式（一键直出）

```bash
"$PY" -m core.orchestrate auto "<照片路径>" --style "<风格名>" --slug "<作品标识>" --data-root "$DATA"
```

- 用户给了释文文本 → 先写成文件再加 `--text <释文.txt>`（零 AI 调用，最准）
- 没给释文 → 先跑下面"agent 认字"子流程拿到逐列文本，再以 `--columns-text` 建档案后用 `make` 出片（不要让 auto 再调一次 API）
- 完成后向用户报告：成片路径 + **必须说明**"释文由 AI 认字（标注 llm-auto），正式使用前建议在确认模式里过一遍，改完重出只要一次渲染"

## ✅ 确认模式（三个确认点，每一步都要等用户看过再继续）

**第 1 步 · 建档案 + 列框确认**
```bash
"$PY" -m core.profile "<照片路径>" --slug "<作品标识>" --data-root "$DATA" [--text "<释文.txt>"]
```
然后把 `"$DATA/作品档案/<slug>/列框确认图.jpg"` 展示给用户（贴图），问：**列数对不对？每列字数对不对？落款是否算一列？**
- 用户说列错了 → 问清每列字数（右→左），重建：
  `"$PY" -m core.profile "<照片路径>" --slug "<作品标识>" --data-root "$DATA" --col-counts "4,7,5,5"`
- 释文与画面字数不一致的报错是正常的，按提示改 `--col-counts` 或核对释文

**第 2 步 · 释文与读音确认**
- 展示当前释文（读 `"$DATA/作品档案/<slug>/释文.txt"`），请用户逐字核对（草书必须人审）
- 有多音字的，列出待定夺清单（`"$PY" -m core.orchestrate llm pron --slug <slug> --data-root "$DATA"`），请用户给替换字（如 朝→zhāo 用同音字"招"）
- 修改后按"agent 认字"子流程的重建命令或 ③ 屏逻辑重建档案

**第 3 步 · 出片**
```bash
"$PY" -m core.orchestrate make "<风格名>" "<照片路径>" --slug "<作品标识>" --data-root "$DATA" [--preview 6]
```
渲染可能要几分钟（逐帧特效风格更久），完成后把 `成片` 下最新 mp4 路径告诉用户，并把 `"$DATA/项目/<slug>_<风格>/检查记录/"` 里的联系表展示出来请用户验收。

---

## 🧠 agent 认字子流程（零 API key）

用户没给释文且风格需要释文时：

1. 导出认字包：
   `"$PY" -m core.orchestrate llm transcribe --slug "<作品标识>" --data-root "$DATA" --export`
2. **你自己读图**：`"$DATA/作品档案/<slug>/llm包/"` 下的 `整幅.jpg` 与 `列1.jpg…列N.jpg`（右→左），按 `prompt.txt` 的要求逐列释文
3. 把结果写成 JSON 文件 `"$DATA/作品档案/<slug>/释文草稿回复.txt"`，格式严格为：
   `{"来源": "篇名或null", "columns": ["第1列释文(右起)", "第2列…"], "置信度低的字": ["字…"]}`
4. 收回并转正式档案：
   `"$PY" -m core.orchestrate llm transcribe --slug "<作品标识>" --data-root "$DATA" --import`
5. 低置信字 ≤3 个 → 用逐列权威文本重建后可直接出片（向用户明确说明是 AI 认字）：
   `"$PY" -m core.profile "<照片路径>" --slug "<作品标识>" --data-root "$DATA" --columns-text "甲乙丙|丁戊己|庚辛壬|癸子丑"`
   低置信字 >3 个 → 不要直出，转确认模式让用户核对口述释文

用户已配置 `DEEPSEEK_API_KEY` 时可改用高质量 API 认字：`"$PY" -m core.orchestrate auto …`（不传 `--text` 即走 API），并在报告里说明。

## 🎨 样片模式

```bash
"$PY" -m core.orchestrate sample "<风格名>" --slug "<样片源slug>" --data-root "$DATA"
```
样片源用已有档案（优先人审过的）。完成后展示 `"$DATA/样片库/<风格名>.jpg"` 缩略图，mp4 在 `"$DATA/样片库/<风格名>.mp4"`。12 个风格名见路由表下半部分。

---

## 红线（任何模式下都必须遵守）

1. **AI 认字 ≠ 人审**：确认模式的第 1、2 步必须等用户看过图、逐字核过释文才能继续；快出模式出片后必须告知 llm-auto 与重出路径
2. **不做笔画修补/超分**：工具红线，用户要求时解释并拒绝
3. **作品照片与档案只存本地数据根**，不要上传到任何外部服务（用户主动要求除外）

## 失败处置速查

| 现象 | 处置 |
|---|---|
| `低置信字 N 个…退回确认流程` | 转确认模式第 1、2 步 |
| `释文字数与画面检出不一致` | 展示列框确认图，向用户要每列字数，用 `--col-counts` 重建 |
| `LLM 认字失败` | 检查网络/`DEEPSEEK_API_KEY`，或改走 agent 认字子流程 |
| 渲染中断报 ffmpeg 错 | 把 `检查记录` 下日志尾部给用户看；先试 `--preview 3` 缩短链路定位 |
| `未知风格` | 可用风格：朗读版、诗意叠化、一字一讲、章法解析、节奏卡点、临帖对比、墨色呼吸、作品入画、慢揭幕、逐字特写、笔顺复现、书写路径 |

## 数据位置

- 作品档案/项目/成片/发布索引/样片库 全在 `DATA_ROOT`（默认 `~/Workspace/10-Projects/VideoCreationStorage/CalligraphyWorksShow`，可用环境变量 `CALLIGRAPHY_DATA_ROOT` 改）
- 任务日志：`~/.calligraphy-studio/jobs/`（Web 任务器产生；CLI 直跑看终端输出即可）
- 备份 = 数据根目录整体拷贝
