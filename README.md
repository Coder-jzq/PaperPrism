# Paper Daily

一个面向科研人员的自动论文雷达：每天自动抓取最新论文，按照你的研究兴趣进行相关性筛选，并使用 LLM 生成中英双语技术速览，再通过 GitHub Pages 提供一个可阅读、可筛选、可标记 **未读 / 已读 / 精选** 的个人论文工作台。

项目基于 **GitHub Actions + GitHub Pages** 运行，不需要单独部署服务器。

---

## ✨ 主要功能

### 1. 多来源论文抓取

目前支持：

- `arxiv`
- `openalex`
- `crossref`
- `semantic_scholar`
- `google_scholar_serpapi`
- `feed` / RSS / Atom

推荐默认启用：

```json
{
  "sources": [
    { "type": "arxiv", "name": "arXiv" },
    { "type": "openalex", "name": "OpenAlex" },
    { "type": "crossref", "name": "Crossref" }
  ]
}
```

多个来源彼此独立。某一个来源遇到超时、429、503 或临时错误时，不会直接导致整个采集流程失败。

这对 GitHub Actions 很重要，因为 arXiv 偶尔会对共享出口 IP 返回 `429 Too Many Requests`。启用 OpenAlex 和 Crossref 后，即使 arXiv 暂时不可用，系统仍然可以继续获取候选论文。

---

### 2. 顶会论文独立收集

“每日新论文”和“顶会精品”使用独立数据文件：

```text
web/data/papers.json
web/data/conference_papers.json
```

会议题录通过 DBLP 获取，并可在 `Research Interests` Issue 中自定义会议。例如：

- ICML
- NeurIPS
- ICLR
- AAAI
- ACM MM
- ACL
- EMNLP
- CVPR
- ICCV
- ICASSP
- Interspeech

对于缺少摘要的会议论文，系统会继续尝试通过：

```text
arXiv → OpenAlex → Crossref
```

按标题补充摘要、PDF、分类和其他元数据。

如果没有可靠摘要，默认不会让 LLM 仅凭标题猜测论文创新点。

---

### 3. 基于研究兴趣的自动筛选

研究兴趣通过 GitHub Issue 配置。

每篇候选论文会根据标题、摘要、关键词、分类和研究方向计算相关性，并保留最匹配的研究主题。

一个方向示例：

```json
{
  "id": "multimodal_dialogue_understanding",
  "name": "Multimodal Dialogue Understanding",
  "description": "关注多模态对话理解、跨模态交互以及长上下文建模。",
  "keywords": [
    "multimodal dialogue",
    "multimodal conversation",
    "dialogue understanding",
    "spoken dialogue"
  ],
  "arxiv_categories": ["cs.CL", "cs.AI", "cs.MM"]
}
```

推荐每个方向配置约 5～15 个高质量英文关键词。

---

### 4. LLM 中英双语技术分析

新版不再只生成中文摘要，而是生成结构化双语分析：

```json
{
  "ai_analysis": {
    "schema_version": 2,
    "title": {
      "original": "Original English Paper Title",
      "zh": "论文中文标题"
    },
    "problem": [
      {
        "original": "One English sentence.",
        "zh": "对应的一句中文。"
      }
    ],
    "method": [],
    "innovation": [],
    "evidence": [],
    "limitations": [],
    "why_relevant": []
  }
}
```

分析包含：

| Section | 内容 |
| --- | --- |
| `title` | 英文原始标题 + 中文翻译 |
| `problem` | 论文解决的问题 |
| `method` | 核心方法与技术流程 |
| `innovation` | 主要创新 |
| `evidence` | 摘要中可核验的实验、理论或系统证据 |
| `limitations` | 明确局限或需要阅读全文确认的内容 |
| `why_relevant` | 为什么与当前研究方向相关 |

网页展示形式为：

```text
English sentence
对应中文

English sentence
对应中文
```

LLM Prompt 会要求模型不要虚构摘要里不存在的实验、数据集、指标或模型组件。

为了兼容旧缓存，新版仍保留：

```text
chinese_summary
```

---

### 5. 未读 / 已读 / 精选

新版网页加入三个阅读状态：

```text
未读
已读
精选
```

新论文默认进入 **未读**。

状态切换逻辑：

```text
未读 → 已读 / 精选
已读 → 精选 / 未读
精选 → 已读 / 未读
```

三个状态互斥，一篇论文在任意时刻只属于一个状态。

---

### 6. 阅读状态只保存在浏览器

阅读状态使用：

```text
localStorage
```

Key：

```text
paper-daily-reading-state-v1
```

这意味着：

- 每个访问者都有自己独立的阅读记录；
- 多个人可以同时使用同一个公开页面；
- 不需要数据库；
- 不需要 GitHub Token；
- 不会互相覆盖状态。

> 阅读状态只存在当前浏览器，不支持跨设备同步。

---

### 7. 多主题网页界面

目前支持：

```text
粉白
淡色
深色
护眼
```

主题选择同样保存在浏览器：

```text
paper-daily-theme-v2
```

---

# 🚀 快速开始

## 第 1 步：Fork 项目

把项目 Fork 到自己的 GitHub 账号，例如：

```text
https://github.com/你的用户名/paper-daily
```

---

## 第 2 步：开启 GitHub Pages

进入：

```text
Settings → Pages → Build and deployment → Source
```

选择：

```text
GitHub Actions
```

运行成功后，网页地址通常为：

```text
https://你的用户名.github.io/paper-daily/
```

例如：

```text
https://coder-jzq.github.io/paper-daily/
```

---

## 第 3 步：配置 Research Interests

进入 `Issues`，创建或编辑一个标题严格为：

```text
Research Interests
```

的 Issue。

推荐将整个配置放进 JSON 代码块中。

不要重复创建多个同名 Issue，保留一个并持续编辑即可。

当 Issue 被 `opened / edited / reopened` 时，可以触发论文更新 workflow。

推荐的多来源配置：

```json
{
  "sources": [
    { "type": "arxiv", "name": "arXiv" },
    { "type": "openalex", "name": "OpenAlex" },
    { "type": "crossref", "name": "Crossref" }
  ],
  "topics": [
    {
      "id": "multimodal_dialogue_understanding",
      "name": "Multimodal Dialogue Understanding",
      "description": "关注多模态对话理解、跨模态交互以及长上下文建模。",
      "keywords": [
        "multimodal dialogue",
        "multimodal conversation",
        "dialogue understanding",
        "spoken dialogue"
      ],
      "arxiv_categories": ["cs.CL", "cs.AI", "cs.MM"]
    }
  ]
}
```

---

## 第 4 步：配置顶会

会议源通过 `conference_sources` 配置，例如：

```json
{
  "conference_sources": {
    "include_default_venues": false,
    "venues": [
      {
        "id": "icml",
        "name": "ICML",
        "group": "machine learning",
        "dblp_toc_patterns": ["db/conf/icml/icml{year}.bht"]
      },
      {
        "id": "nips",
        "name": "NeurIPS",
        "group": "machine learning",
        "dblp_toc_patterns": ["db/conf/nips/neurips{year}.bht"]
      },
      {
        "id": "iclr",
        "name": "ICLR",
        "group": "machine learning",
        "dblp_toc_patterns": ["db/conf/iclr/iclr{year}.bht"]
      }
    ]
  }
}
```

如果不显式配置 `years`，程序可以按照当前年份动态处理最近会议年份，例如 2026 年运行时覆盖 2026 和 2025。

---

## 第 5 步：配置 OpenAlex / Crossref 联系邮箱

进入：

```text
Settings → Secrets and variables → Actions → Variables
```

添加：

| Name | 示例 |
| --- | --- |
| `CONTACT_EMAIL` | `you@example.com` |
| `OPENALEX_EMAIL` | `you@example.com` |
| `CROSSREF_EMAIL` | `you@example.com` |

三个变量可以使用同一个邮箱。

---

## 第 6 步：配置 LLM

不配置 LLM 也可以抓论文；配置后可以生成完整双语技术分析。

进入：

```text
Settings → Secrets and variables → Actions → Secrets → New repository secret
```

OpenAI-compatible 服务添加：

| Name | 内容 |
| --- | --- |
| `LLM_API_KEY` | 你的 API Key |

然后在 Variables 中添加：

| Name | 示例 |
| --- | --- |
| `LLM_BASE_URL` | `https://your-provider.example.com` |
| `LLM_MODEL` | `gpt-5.6` |

也可以使用：

```text
OPENAI_API_KEY
DEEPSEEK_API_KEY
```

Key 优先级：

```text
LLM_API_KEY → OPENAI_API_KEY → DEEPSEEK_API_KEY
```

采集器支持 OpenAI-compatible Responses API，并保留 Chat Completions 兼容逻辑。

### 安全提醒

不要把 API Key 写进：

```text
README.md
Research Interests Issue
web/app.js
web/index.html
scripts/collect_papers.py
```

API Key 必须放在 GitHub Actions Secrets 中。

---

## 第 7 步：第一次手动运行

进入：

```text
Actions → Paper Daily → Run workflow
```

推荐第一次使用：

```text
lookback_days = 7
clear_cache = false
```

然后点击 `Run workflow`。

---

# ♻️ clear_cache 是什么？

## `clear_cache = false`

日常推荐。

```text
恢复历史 cache
↓
抓取新论文
↓
与旧论文合并
↓
筛选
↓
对新的合适论文调用 LLM
↓
生成网页数据
```

优点是即使某一个论文源临时失败，也不会轻易把历史论文清空。

## `clear_cache = true`

表示本次忽略历史论文缓存并重新抓取：

```text
清空论文 cache
↓
重新请求 arXiv / OpenAlex / Crossref / DBLP
↓
重新筛选
↓
重新调用 LLM
↓
重新生成 papers.json
```

适合：

- 大幅修改 Research Interests 后重建；
- 想重新生成双语分析；
- 旧缓存质量较差；
- 调整筛选规则后重新测试。

注意：如果本次运行期间 arXiv 出现 429、DBLP 出现 500，重新抓到的论文可能比原来少。

因此日常建议：

```text
clear_cache = false
```

---

# ⏰ 自动更新时间

默认 workflow：

```yaml
schedule:
  - cron: "0 22 * * *"
```

GitHub Actions 使用 UTC，因此对应：

```text
UTC          22:00
北京时间      次日 06:00
日本时间      次日 07:00
```

即默认每天北京时间早上 **06:00** 自动更新。

GitHub Actions 的定时任务可能有几分钟延迟，这是正常现象。

---

# 📦 数据保存方式

论文数据会：

```text
GitHub Actions
↓
生成 web/data/*.json
↓
GitHub Pages artifact
↓
部署网页
```

主要文件：

```text
web/data/papers.json
web/data/conference_papers.json
```

同时 workflow 可以使用 GitHub Actions cache 保存历史数据，以便下一次增量更新。

“每日新论文”和“顶会精品”独立裁剪，不会互相挤占存储上限。

---

# 🧠 阅读状态与论文数据是分开的

论文数据：

```text
GitHub Actions → papers.json → 所有访问者共享
```

阅读状态：

```text
Browser → localStorage → 每个访问者独立
```

因此每天更新论文数据不会自动清除浏览器中的已读 / 精选记录。

---

# 🔎 网页筛选

论文集合：

```text
每日新论文
顶会精品
```

阅读状态：

```text
未读
已读
精选
```

时间范围：

```text
全部
当日
本周
本月
```

还支持：

```text
日期
研究方向
匹配等级
关键词搜索
```

搜索范围包括英文标题、中文标题、作者、摘要、分类、匹配原因、AI 英文分析和 AI 中文分析。

---

# 📊 常用参数

| Name | 默认值 | 说明 |
| --- | ---: | --- |
| `MIN_PAPER_SCORE` | `0.08` | 有摘要论文最低相关性 |
| `MIN_TITLE_ONLY_SCORE` | `0.18` | 只有标题时最低相关性 |
| `MIN_CONFERENCE_SCORE` | `0.18` | 顶会题录最低相关性 |
| `MAX_NEW_PAPERS` | `50` | 单次最多新增每日论文 |
| `MAX_STORED_PAPERS` | `50` | 每日论文最多保留数量 |
| `MAX_NEW_CONFERENCE_PAPERS` | `50` | 单次最多新增会议论文 |
| `MAX_STORED_CONFERENCE_PAPERS` | `300` | 顶会论文最多保留数量 |
| `MAX_SUMMARIES` | `20` | 每次最多调用 LLM 分析的论文数 |
| `MIN_DAILY_PAPERS` | `8` | 当日不足时尝试回填到的最低数量 |
| `DAILY_BACKFILL_DAYS` | `14` | 每日不足时最多回看多少天 |

---

## arXiv 参数

| Name | 默认值 | 说明 |
| --- | --- | --- |
| `ARXIV_QUERY_MODE` | `keyword` | 默认关键词检索 |
| `ARXIV_SORT_BY` | `lastUpdatedDate` | 按最近更新时间排序 |
| `ARXIV_EXPAND_CATEGORY_SEARCH` | `false` | 是否额外分类扩展 |
| `ARXIV_CATEGORY_MAX_RESULTS` | `10` | 分类扩展最大结果数 |
| `ARXIV_RETRY_THROTTLED` | `false` | 429/503 时是否等待重试 |
| `ARXIV_RETRIES` | `4` | 临时错误最大尝试次数 |

为了降低 arXiv 429 风险，推荐：

```text
ARXIV_QUERY_MODE=keyword
ARXIV_EXPAND_CATEGORY_SEARCH=false
```

同时启用 OpenAlex 和 Crossref。

---

## DBLP / 会议参数

| Name | 默认值 | 说明 |
| --- | ---: | --- |
| `DBLP_DELAY_SECONDS` | `5` | 不同会议源之间等待时间 |
| `DBLP_PATTERN_DELAY_SECONDS` | `3` | 同会议多个 pattern 间等待时间 |
| `DBLP_RETRIES` | `3` | 临时错误最大尝试次数 |
| `MAX_PER_CONFERENCE` | `1000` | 单个 TOC 最大读取数量 |
| `MAX_CONFERENCE_ABSTRACT_ENRICHMENTS` | `50` | 每次最多补摘要的会议论文数 |
| `CONFERENCE_ABSTRACT_SOURCES` | `arxiv,openalex,crossref` | 摘要补全来源顺序 |
| `CONFERENCE_ABSTRACT_DELAY_SECONDS` | `3` | 标题补摘要请求间隔 |
| `CONFERENCE_ABSTRACT_SEARCH_RESULTS` | `5` | 每个来源返回的标题候选数 |

---

## LLM 参数

| Name | 说明 |
| --- | --- |
| `LLM_API_KEY` | OpenAI-compatible API Key |
| `OPENAI_API_KEY` | OpenAI API Key |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `LLM_BASE_URL` | 自定义 API Base URL |
| `LLM_MODEL` | 模型名称 |
| `LLM_CONCURRENCY` | 并发 LLM 请求数 |
| `LLM_TIMEOUT_SECONDS` | 单请求超时时间 |
| `LLM_MAX_OUTPUT_TOKENS` | 可选最大输出 token |
| `LLM_REASONING_EFFORT` | 支持时可指定推理强度 |
| `LLM_SUMMARIZE_CONFERENCE` | 是否分析有摘要的会议论文 |
| `LLM_SUMMARIZE_TITLE_ONLY` | 是否允许仅凭标题调用 LLM |

推荐：

```text
LLM_SUMMARIZE_CONFERENCE=true
LLM_SUMMARIZE_TITLE_ONLY=false
```

---

# 🌐 自定义 RSS / Atom

如果期刊、会议、实验室或机构提供 RSS / Atom，可以直接接入：

```json
{
  "sources": [
    {
      "type": "feed",
      "name": "Custom Paper Feed",
      "url": "https://example.com/papers.xml"
    }
  ]
}
```

当前采集器不建议直接抓需要 JavaScript 渲染、验证码、登录、复杂分页的普通网页。

优先使用官方 API、RSS 或 Atom。

---

# 📁 项目结构

```text
paper-daily/
├── .github/
│   └── workflows/
│       └── paper-daily.yml
├── config/
├── scripts/
│   └── collect_papers.py
├── tests/
├── web/
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   └── data/
│       ├── papers.json
│       └── conference_papers.json
├── requirements.txt
└── README.md
```

---

# 🔄 整体工作流程

```text
Research Interests Issue
          │
          ▼
GitHub Actions
          │
          ▼
arXiv / OpenAlex / Crossref / Feed
          │
          ├──────────────┐
          │              │
          ▼              ▼
   Daily Papers      DBLP Conferences
          │              │
          │              ▼
          │      arXiv / OpenAlex / Crossref
          │          补充会议摘要
          │              │
          └──────┬───────┘
                 ▼
          Relevance Scoring
                 │
                 ▼
                LLM
                 │
                 ▼
        Bilingual AI Analysis
                 │
                 ▼
 papers.json / conference_papers.json
                 │
                 ▼
            GitHub Pages
                 │
                 ▼
          未读 / 已读 / 精选
                 │
                 ▼
        Browser localStorage
```

---

# 🧪 本地预览

在仓库根目录执行：

```bash
python -m http.server 8000 --directory web
```

浏览器打开：

```text
http://localhost:8000
```

不要直接双击 `web/index.html`，因为浏览器的本地文件安全限制可能导致：

```javascript
fetch("./data/papers.json")
```

无法正常工作。

---

# 🛠 常见问题

## 1. 为什么运行后没有新论文？

可能原因：

- 最近几天没有论文达到阈值；
- arXiv 返回 429；
- OpenAlex / Crossref 临时失败；
- 关键词过严；
- `lookback_days` 太短；
- 当前是增量模式，确实没有新候选。

查看：

```text
Actions → Paper Daily → 对应运行 → Collect papers
```

---

## 2. arXiv 出现 429 怎么办？

通常不是代码错误，而是 GitHub Actions 共享出口 IP 被临时限流。

推荐：

```text
clear_cache=false
sources=arxiv,openalex,crossref
```

不要因为一次 429 就反复执行 `clear_cache=true`。

---

## 3. 为什么有些论文只有中文，没有英文双语分析？

通常是历史缓存中的旧论文。

旧版本只有：

```text
chinese_summary
```

新版网页会兼容显示。

新抓取并成功调用新版 LLM 的论文会拥有：

```text
ai_analysis
```

---

## 4. 为什么有些会议论文没有 AI Summary？

DBLP 主要提供题录，不一定有摘要。

系统会尝试通过 arXiv、OpenAlex、Crossref 补摘要。如果仍然找不到，为避免模型凭标题猜测，默认不会生成完整技术分析。

---

## 5. 为什么手机和电脑上的已读状态不同？

这是预期行为。

新版故意使用 `localStorage`，不同设备和不同浏览器拥有独立状态。

---

## 6. 清理浏览器缓存会发生什么？

如果清除了该站点的 localStorage，已读和精选记录会消失，论文重新视为未读。

论文数据本身不会受影响。

---

## 7. `clear_cache=true` 会清除已读和精选吗？

不会。

`clear_cache` 清理的是 GitHub Actions 的论文缓存；已读 / 精选属于浏览器 localStorage，两者彼此独立。

---

# ✅ 推荐日常配置

```text
GitHub Pages                 = GitHub Actions
lookback_days                = 7
clear_cache                  = false

sources                      = arxiv, openalex, crossref

MAX_NEW_PAPERS               = 50
MAX_STORED_PAPERS            = 50
MAX_NEW_CONFERENCE_PAPERS    = 50
MAX_STORED_CONFERENCE_PAPERS = 300
MAX_SUMMARIES                = 20

ARXIV_QUERY_MODE             = keyword
ARXIV_EXPAND_CATEGORY_SEARCH = false

LLM_CONCURRENCY              = 2
LLM_SUMMARIZE_CONFERENCE     = true
LLM_SUMMARIZE_TITLE_ONLY     = false
```

---

# 🎯 设计目标

Paper Daily 的目标不是简单做一个“论文列表”，而是把每天的信息流整理成一个真正可使用的科研阅读工作台：

```text
发现论文
   ↓
自动筛选
   ↓
双语快速理解
   ↓
人工阅读
   ↓
已读管理
   ↓
精选沉淀
```

论文数据对所有访问者共享，而个人阅读状态保存在各自浏览器中，因此一个公开的 Paper Daily 页面可以同时被多人使用，而不会互相影响。

---

## License

请遵循原项目仓库中的 License。
