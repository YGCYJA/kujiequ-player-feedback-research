# 库街区玩家反馈研究

## 已完成的近月研究案例

**[阅读角色与优化意见报告](research/2026-09-25_2026-10-25/README.md)** ｜ [汇总数据](research/2026-09-25_2026-10-25/summary.json) ｜ [可下载后离线打开的HTML报告](research/2026-09-25_2026-10-25/index.html)

研究窗口为 **2026-09-25 00:00 至 2026-10-25 16:54:46（北京时间，右端不含）**。从14个选定的官方版本反馈、问题说明及角色演示/展示/PV帖子采集2,296条顶层评论。日期筛选纳入2,271条，排除13条窗口外和12条日期不明记录；长度等清理后分析 **2,249条**。

这是一份指定来源的近月评论案例，**不是库街区全平台近月全量数据**。14帖均在本次设定范围内读到接口终止；每帖600条上限未触发。帖子选择仍会影响结果，不能据此估计全体玩家投诉比例。

全部纳入日期为日精度，年份依据“官方栏目首次发布时间至采集时刻”的唯一日期候选推定。栏目发布时间被假设为公开评论下界，并不是底层帖子创建时间的官方保证。没有用评论ID编码伪造精确评论时间，日期精度及排除数量在报告中列明。

源码、方法、汇总和去身份意见概述可公开；逐条评论、作者标识和本地匿名化盐保留在本地。意见复核使用AI辅助语境阅读，不冒充真人访谈或独立双人编码。

一个围绕**鸣潮角色反馈与优化问题**的 Python 项目：公开帖子发现 → 评论与楼中楼采集 → 清理与审计 → 主题提及统计 → 语境复核 → 中文 HTML 报告。本次案例采用AI辅助复核，可继续补充人工核验。

适合掌握基础 Python、希望积累游戏用户研究作品的学习者。项目参考了[原神「千星奇域」评论分析](https://github.com/746505972/Genshen-Impact-Thousand-Star-Wonderland-player-sentiment-analysis)的研究流程，采集器、清理和报告代码为本项目重新实现。这里的主题规则只识别“谈到了什么”，不会把关键词命中自动解释为负面反馈、满意度或真实需求。

## 先看结果

- `research/2026-09-25_2026-10-25/`：已完成的近月研究案例，只包含可公开的汇总与意见概述。
- `output/live-validation/report.html`：早期三个帖子的功能验证报告，只保存在本地。
- `output/demo/report.html`：运行演示后生成；全部为明确标记的模拟评论。
- `docs/research-plan.md`：如何选样、人工核验、设计访谈并写成研究报告。
- `docs/api-observations.md`：2026-10-25 的官方页面与接口核实记录。

报告可直接用本地浏览器打开，无需服务器、外部脚本或在线字体。源码压缩包不包含真实评论、作者匿名化盐文件或账号凭证。

## 环境准备（Windows）

需要 Python 3.10 或更新版本。在本文件所在文件夹打开终端：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

只依赖 `requests`；清理、统计和报告使用 Python 标准库，无需 API Key、付费模型、SPSS 或 GPU。以下命令使用 `.venv\Scripts\python.exe`，不需要修改 PowerShell 脚本执行策略。如果你使用已有的 Python 环境，可换成 `python`。

## 第一次运行：离线演示

```powershell
.venv\Scripts\python.exe run.py demo
```

打开 `output/demo/report.html`。这一步使用24条内置模拟评论，旨在检查环境和了解流程，**不能作为实际玩家研究成果**。

## 复现近月研究

采样计划在 `config/month-study.json`：预登记14个帖子、栏目元数据来源、日历窗口及采样上限。主要统计单位为顶层评论，避免热门楼中楼反复讨论同一问题放大比例；基础采集器仍支持楼中楼。

```powershell
.venv\Scripts\python.exe tools/run_month_study.py
.venv\Scripts\python.exe tools/finalize_month_study.py --findings config/reviewed-findings.json
```

重新请求时平台内容可能变化；不能保证与原快照的计数完全一致。已有本地采集目录时使用检查点，新的独立研究请复制并修改配置中的 `study_id` 和窗口。`reviewed-findings.json` 是本次语境核查后的去身份结论，不是自动情感模型输出；新增数据需要重新阅读证据后更新，不能沿用旧证据数量。

也可以独立执行日期筛选与公开导出：

```powershell
.venv\Scripts\python.exe run.py period --input data/my-study/comments.jsonl --output data/my-study/window/comments.jsonl --start 2026-09-25T00:00:00+08:00 --end 2026-10-25T17:00:00+08:00 --posts-metadata data/my-study/posts.json
.venv\Scripts\python.exe run.py pipeline --data data/my-study/window --output output/my-study
.venv\Scripts\python.exe run.py export-public --input-dir output/my-study --output-dir research/my-study
```

只给日期的 `--end` 表示包含该日，内部取次日零时；建议使用含时区的完整起止时间消除歧义。`period` 只在明确边界内恢复月日显示日期，不能判断的日期单列排除。公开导出不会复制逐条评论或原始清单。

## 采集真实公开评论

### 1. 选择帖子

`examples/posts.txt` 已列出三个官方候选帖：角色活动唤取、3.7版本已知问题、客户端资源分级说明。可以复制到自己的文本文件，增删链接，每行一个；`#` 开头为说明。

也可以发现近期官方公告，再人工选样：

```powershell
.venv\Scripts\python.exe run.py discover --pages 2 --keyword 角色 --keyword 优化 --keyword 已知问题 --output output/discovery
```

查看生成的 `posts.csv` 中的标题，再编辑 `posts.txt`，避免把抽奖、无关活动直接当成角色评价。`discover` 只筛选官方公告标题，**不是全社区全文搜索**；当前匿名全文搜索未验证可用，所以没有包装成已经实现的功能。社区讨论帖可手工从网页复制链接加入。

### 2. 小样本采集

```powershell
.venv\Scripts\python.exe run.py fetch --posts examples/posts.txt --output data/my-study --max-comments 100 --max-pages 20 --delay 2
```

或者只采一个帖子：

```powershell
.venv\Scripts\python.exe run.py fetch --post https://www.kurobbs.com/mc/post/1554788184571133952 --output data/optimization --max-comments 100
```

- 默认为最新排序；`--order 2` 使用平台默认排序。平台默认排序可能涉及热评，不等于随机抽样。
- 默认包含内联预览和单独分页取得的楼中楼。只研究顶层评论时增加 `--no-replies`，该范围会记录在清单中。
- `--max-comments` 是**每个帖子**的总评论上限，包含回复；`--max-pages` 是每帖本次运行的分页请求上限，也包含回复页。
- `--page-size` 默认20；平台实际返回数量可能不同。短页且 `hasNext=1` 时仍继续请求，不凭页长判断采完。
- 请求间隔默认2秒，至少1秒；单线程采集，网络/服务器错误有限重试。遇到登录、验证码或访问限制就停止，保留已完成进度，不自动登录或规避限制。

### 3. 中断后续采

使用相同帖子清单、排序、回复范围和每页大小，增加 `--resume`。需要扩大样本时，可以提高评论及页数上限：

```powershell
.venv\Scripts\python.exe run.py fetch --posts examples/posts.txt --output data/my-study --max-comments 200 --max-pages 20 --delay 2 --resume
```

采集后查看 `collection_manifest.json`：`limited` 表示触及人为上限，`failed` 表示未完成；`complete_within_scope` 只代表当次所选范围内接口已返回结束，不代表全社区、全历史或永久完整。续采沿用已记录页码；评论动态插入、删除及排序变化仍可能造成遗漏，按ID去重只能避免重复，不能证明没有漏采。

### 4. 一键清理、分析、出报告

```powershell
.venv\Scripts\python.exe run.py pipeline --data data/my-study --output output/my-study
```

需要按发布日期比较前后讨论分布，可添加分界日：

```powershell
.venv\Scripts\python.exe run.py pipeline --data data/my-study --output output/my-study --split-date 2026-10-21
```

当天计入后段，时区为中国标准时间 UTC+08:00。接口常将顶层评论时间显示成 `10-22` 或相对时间，程序不会擅自补年份；缺少完整日期的记录不参与时间图和分段统计，仍保留在主题分析中。因此报告会同时显示日期缺失量。

## 如何得到更贴近角色的分析

先按研究问题选相关帖子，尽量纳入角色体验与机制讨论，避免只采抽奖活动。分析默认保留样本中全部清理后评论，主题占比以该样本为分母。

如果确实需要正文包含某个角色名的子样本，可使用 `--include`，多次指定为 OR 关系：

```powershell
.venv\Scripts\python.exe run.py pipeline --data data/my-study --output output/character-subset --include 你研究的角色名 --include 对应别称
```

这样可能漏掉使用代词或只描述技能的评论，也会改变统计分母；筛选词和排除数量会记录在清洗清单中。报告中须说明这是一份条件筛选后的样本。

修改 `config/topics.json` 后重新运行分析，可以加入技能名、常见缩写、设备称呼。当前内置12类主题，覆盖角色机制、手感、养成、强度、角色表现，以及帧率、发热、闪退、网络、画面加载、设备适配和其他优化提及。

## 输出文件

| 文件 | 用途 |
| --- | --- |
| `data/.../comments.jsonl` | 保留原文的归一化采集记录，不是完整接口响应 |
| `data/.../checkpoint.json` | 评论页及回复页进度 |
| `data/.../collection_manifest.json` | 来源、采样参数、状态、上限与各帖完成情况 |
| `data/.../.author_salt` | 本地随机匿名化盐；续采需要，不公开 |
| `output/.../cleaned_comments.csv` | 清洗后评论，UTF-8 BOM，便于中文表格工具打开 |
| `output/.../cleaning_summary.json` | 去重、空值、长度筛选、关键词筛选、坏行等互斥审计计数 |
| `output/.../topics.csv` | 各主题提及数量及占全部样本的比例 |
| `output/.../daily_counts.csv` | 有完整日期样本的每日评论量 |
| `output/.../labeled_comments.csv` | 每条评论的规则主题及命中词 |
| `output/.../review_sample.csv` | 待人工核验的覆盖样本与标注列 |
| `output/.../summary.json` | 可检查的统计、分母、规则版本及研究局限 |
| `output/.../key_findings.md` | 统计摘要与后续核验提示 |
| `output/.../report.html` | 自包含中文报告和图表 |

标识符始终按字符串处理，避免19位ID的浮点精度丢失。Excel打开CSV可能自行把长数字转成科学计数法；需要保留ID时，用“从文本/CSV导入”并将ID列指定为文本。原始JSONL里的ID不会被改写。缺失的点赞、回复数保持空值，不当成零。CSV正文的公式前缀做转义处理。

清理仅按 `(post_id, comment_id)` 去重：不同玩家发布相同文本仍保留。不会仅凭词汇将用户标为水军，也不会自动剔除所有简短表达；筛选规则及保留范围都可核查。

## 研究结论的边界

- “不卡的”“卡顿已修复”和“卡顿严重”都可能命中帧率主题；**主题提及不是投诉数或满意度**。
- 同一条评论可以命中多个主题，各主题比例合计可能超过100%；组内评论去重计数。
- `review_sample.csv` 是覆盖不同规则结果的核验样本，不是随机抽样。请人工记录问题是否明确、主题是否正确以及场景证据。
- 官方帖、角色活动帖、主动发言者及评论回复都有选择偏差，不能据此推断全体玩家的态度。
- 角色和优化词典可能遗漏别称、隐喻、反讽。项目不提供尚未验证的分类准确率、情感分或“优化提升”结论。
- 真实数据与演示数据使用不同目录并保留来源标签，来源混合或冲突会明确标记。
- 默认不保存用户名、头像、IP属地或原始用户ID。作者标识用本地盐做HMAC；评论正文依旧可能含个人自述，分享报告前须人工检查。

本项目已完成一份AI辅助的近月评论研究案例。用于简历或面试前，请亲自复现流程、阅读证据并解释分析方法、发现与局限，如实说明AI辅助的范围和自己的实际工作；用户访谈与游戏体验验证是建议的后续研究，本次尚未开展。参见 `docs/research-plan.md`。

## 分步运行与测试

```powershell
.venv\Scripts\python.exe run.py clean --input data/my-study/comments.jsonl --output output/my-study/cleaned_comments.csv
.venv\Scripts\python.exe run.py analyze --input output/my-study/cleaned_comments.csv --output output/my-study
.venv\Scripts\python.exe run.py report --output output/my-study
.venv\Scripts\python.exe -m pip install "pytest>=8,<9"
.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local-temp
```

测试覆盖分页与短页、内联/分页回复去重、续采、ID保真、未知计数、访问限制停止、数据清洗、空集、主题统计分母、来源冲突、CSV与HTML转义；离线测试不请求库街区。当前环境的系统临时目录受限，因此示例将测试临时文件放在项目文件夹。

## 项目结构

```text
kujiequ-research/
├── run.py
├── src/kujiequ_research/
│   ├── client.py       # 匿名、限速、只读接口
│   ├── collector.py    # 分页、回复、续采与清单
│   ├── models.py       # 字段、富文本、日期与匿名化
│   ├── cleaning.py    # 清理与审计
│   ├── analysis.py    # 主题规则、统计和核验样本
│   ├── report.py      # 中文HTML与SVG图表
│   ├── demo.py        # 明确标记的模拟数据
│   └── cli.py         # 命令行入口
├── config/topics.json
├── examples/posts.txt
├── docs/
└── tests/
```

接口来自库街区网页当前使用的读取请求，不是平台承诺稳定的开放API。访问要求或字段变化时程序会停止并说明，避免把错误响应当成“没有评论”。当前接口观察与官方脚本链接见 `docs/api-observations.md`。

