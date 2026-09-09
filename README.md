# PaperPrism

> 🔍 An AI-powered personalized research paper radar and reading workspace.

**PaperPrism** 是一个面向科研人员的智能论文发现与阅读辅助系统。它能够根据用户配置的研究兴趣，自动从多个学术来源发现最新论文，进行相关性筛选，并使用大语言模型生成结构化的中英双语论文分析。同时，系统还能够自动从论文 PDF 中识别并截取模型框架图，最终通过 GitHub Pages 构建一个可持续更新的个人科研阅读工作台。

> [!IMPORTANT]
> **PaperPrism 是基于开源项目 [Paper Daily](https://github.com/Futuresxy/paper-daily) 进行二次开发和功能扩展的项目。**
>
> 我们在原项目自动论文收集与展示能力的基础上，进一步扩展了多源论文检索、个性化研究兴趣匹配、LLM 深度双语论文分析、任务定义、模型框架图自动提取、阅读状态管理以及顶会论文追踪等功能。
>
> 感谢原项目作者及贡献者提供的优秀开源基础。

---

## ✨ Features

PaperPrism 当前主要包含以下功能：

### 📡 Multi-source Paper Discovery

支持从多个学术来源自动发现论文：

- arXiv
- OpenAlex
- Crossref
- DBLP
- Semantic Scholar（可选）
- RSS / Atom Feed（可选）
- Google Scholar via SerpApi（可选）

推荐默认启用：

```text
arXiv
+
OpenAlex
+
Crossref
```

多个数据源之间相互补充。

当某个数据源出现：

```text
HTTP 429
HTTP 500
Timeout
Temporary unavailable
```

时，其他来源仍然可以继续工作，尽可能减少单一数据源故障带来的影响。

---

## 🎯 Personalized Research Interests

PaperPrism 可以根据用户自己的科研方向自动筛选论文。

研究兴趣通过 GitHub Issue：

```text
Research Interests
```

进行配置。

例如可以关注：

```text
Omni / Multimodal Foundation Models

Speech LLM / Spoken Dialogue

Emotion / Affective Intelligence

Empathy / Social Intelligence

Multimodal Dialogue Understanding

Reasoning + Agent + RAG

Multimodal / Speech Generation

Memory / Long-Context Intelligence

Retrieval-Augmented Intelligence

Graph Modeling / Structured Reasoning

Flow Matching / Generative Modeling

Reinforcement Learning / Preference Optimization

Embodied Intelligence / Multimodal Agents
```

系统会综合：

```text
Title
Abstract
Keywords
Categories
Research Interests
```

计算论文与个人研究方向之间的相关性。

---

# 🧠 AI-powered Deep Paper Analysis

PaperPrism 不仅推荐论文，还会进一步使用 LLM 对论文进行结构化分析。

新版分析结构包括：

```text
Task
Problem
Method
Model Figure
Innovation
Evidence
Limitations
Relevance
```

---

## 1. Task / 任务定义

首先介绍论文所属研究任务。

主要回答：

```text
这是什么研究方向？

研究任务是什么？

典型输入是什么？

模型需要输出什么？

该任务最终希望解决什么问题？
```

例如：

```text
Task

Speech Emotion Recognition aims to identify emotional states
expressed in speech signals.

语音情感识别旨在根据语音信号识别说话者表达的情绪状态。
```

这使得即使用户第一次接触某个研究方向，也可以快速理解论文的基本任务。

---

## 2. Problem / 问题

详细分析论文要解决的问题。

通常按照：

```text
Existing Research
        ↓
Existing Limitation
        ↓
Core Challenge
        ↓
Target Problem
```

进行组织。

相比简单摘要，PaperPrism 更强调：

```text
为什么这个问题值得研究？

已有方法为什么不够？

真正困难的地方是什么？

这篇论文具体想解决什么？
```

---

## 3. Method / 方法

Method 部分重点解析论文的技术流程。

例如：

```text
Input
  ↓
Representation
  ↓
Encoder / Backbone
  ↓
Core Module
  ↓
Interaction / Fusion
  ↓
Training Objective
  ↓
Inference
  ↓
Output
```

通常生成多个中英双语技术步骤：

```text
1. English technical description
   对应中文解释

2. English technical description
   对应中文解释

3. English technical description
   对应中文解释
```

避免将整个方法压缩成一句模糊描述。

---

## 4. Model Figure / 模型框架图

PaperPrism 可以自动从论文 PDF 中寻找最可能的模型框架图。

系统会分析：

```text
Figure Caption
+
Page Position
+
Architecture Keywords
+
Framework Keywords
```

重点寻找包含：

```text
architecture
framework
overview
pipeline
proposed model
proposed method
system
network
overall framework
```

等信息的 Figure。

同时会降低以下类型图片的优先级：

```text
results
comparison
performance
ablation
distribution
visualization
```

避免将实验结果图误认为模型架构图。

---

### Model Figure Extraction Pipeline

```text
Paper PDF
   ↓
PyMuPDF
   ↓
Read Figure Captions
   ↓
Figure Candidate Scoring
   ↓
Locate Figure Region
   ↓
Render PDF Page
   ↓
Crop Original Figure
   ↓
Save Figure Image
   ↓
GitHub Pages
```

PaperPrism **不会使用生成式模型重新绘制模型图**。

展示的模型图直接来自论文原始 PDF。

例如：

```text
MODEL FIGURE / 模型框架

[Original Paper Figure]

Figure 2: Overview of the proposed framework.

PDF Page 4
High Confidence
```

如果系统无法可靠判断某张图片是不是模型框架图：

```text
Confidence Too Low
        ↓
Do Not Display
```

宁可不显示，也不会随意选择实验图。

---

## 5. Innovation / 创新

Innovation 会将主要创新拆分为独立条目：

```text
Innovation 1

Innovation 2

Innovation 3
```

重点回答：

```text
相比已有方法，新在哪里？

提出了什么新的结构？

提出了什么新的训练机制？

提出了什么新的建模方式？

为什么这些设计可能有效？
```

---

## 6. Evidence / 证据

PaperPrism 会尽量提取摘要中能够可靠验证的实验或理论证据。

例如：

```text
Datasets

Evaluation Tasks

Performance Improvements

Experimental Findings

Ablation Evidence
```

如果摘要没有提供具体数据，则不会凭空生成实验结果。

---

## 7. Limitations / 局限

系统会区分：

```text
Explicit Limitations
```

和：

```text
Conservative Inference
```

不会将没有依据的猜测写成论文明确声明的局限。

---

## 8. Relevance / 研究相关性

PaperPrism 会解释：

```text
为什么这篇论文与你配置的研究方向相关？
```

例如：

```text
Multimodal Dialogue Understanding

Speech LLM

Emotion

RAG

Flow Matching
```

让推荐结果不仅有：

```text
Score = 0.82
```

还能够解释：

```text
为什么推荐给你。
```

---

# 🌐 Bilingual Paper Reading

PaperPrism 的 AI 分析采用：

```text
English
中文
```

逐句对应的双语形式。

例如：

```text
The model introduces a cross-modal interaction module.

模型引入了跨模态交互模块。


The module explicitly models dependencies between speech and text.

该模块显式建模语音与文本之间的依赖关系。
```

英文用于保留原始技术表达。

中文用于快速理解论文内容。

---

## AI Analysis Data Structure

新版论文分析数据大致为：

```json
{
  "ai_analysis": {
    "schema_version": 3,

    "title": {
      "original": "Original Paper Title",
      "zh": "论文中文标题"
    },

    "task_intro": [
      {
        "original": "...",
        "zh": "..."
      }
    ],

    "problem": [
      {
        "original": "...",
        "zh": "..."
      }
    ],

    "method": [
      {
        "original": "...",
        "zh": "..."
      }
    ],

    "innovation": [
      {
        "original": "...",
        "zh": "..."
      }
    ],

    "evidence": [],

    "limitations": [],

    "why_relevant": []
  }
}
```

---

# 📚 Conference Paper Tracking

除了每日新论文，PaperPrism 还支持独立追踪顶级会议论文。

当前可以配置：

```text
ICML
NeurIPS
ICLR
AAAI
ACM MM
ACL
EMNLP
CVPR
ICCV
ICASSP
Interspeech
```

会议题录主要通过：

```text
DBLP
```

获取。

如果会议论文缺少摘要，PaperPrism 会继续尝试：

```text
arXiv
↓
OpenAlex
↓
Crossref
```

补充：

```text
Abstract
PDF
DOI
Categories
Metadata
```

---

# 📖 Reading Workspace

PaperPrism 不只是论文列表，同时提供简单的个人阅读管理功能。

每篇论文可以被标记为：

```text
未读
已读
精选
```

---

## 未读

新论文默认进入：

```text
未读
```

可以：

```text
✓ 标记为已读

★ 加入精选
```

---

## 已读

阅读后的论文进入：

```text
已读
```

可以：

```text
★ 转为精选

↩ 恢复为未读
```

---

## 精选

特别值得关注的论文可以加入：

```text
精选
```

可以：

```text
✓ 转为已读

↩ 恢复为未读
```

---

# 💾 Local Reading State

阅读状态不会上传到服务器。

PaperPrism 使用浏览器：

```text
localStorage
```

保存：

```text
未读
已读
精选
主题
```

因此：

```text
GitHub Pages
        ↓
所有人看到相同论文数据

Browser A
        ↓
自己的阅读状态

Browser B
        ↓
自己的阅读状态
```

多个用户可以同时使用同一个公开 PaperPrism 页面，而不会互相影响。

需要注意：

> 当前阅读状态不会跨设备同步。

---

# 🎨 Themes

PaperPrism 当前支持四种界面主题：

```text
粉白
淡色
深色
护眼
```

默认主题：

```text
Pink Glassmorphism
+
Soft Neumorphism
```

---

# 🔎 Search & Filters

支持：

### Dataset

```text
每日新论文

顶会精品
```

### Reading State

```text
未读

已读

精选
```

### Time Range

```text
全部

当日

本周

本月
```

### Filters

```text
日期

研究方向

匹配等级

关键词搜索
```

搜索范围包括：

```text
English Title

Chinese Title

Authors

Abstract

Categories

Research Topic

Task

Problem

Method

Innovation

Evidence

Limitations

Relevance

Model Figure Caption
```

---

# ⚙️ Automated Workflow

PaperPrism 使用 GitHub Actions 自动运行。

默认：

```yaml
schedule:
  - cron: "0 22 * * *"
```

GitHub Actions 使用 UTC，因此：

```text
UTC        22:00

北京时间    次日 06:00

日本时间    次日 07:00
```

也就是说：

> PaperPrism 默认每天北京时间早上 06:00 自动更新论文。

---

## Complete Pipeline

```text
Research Interests
        ↓
GitHub Actions
        ↓

arXiv
OpenAlex
Crossref
DBLP
        ↓

Paper Deduplication
        ↓

Relevance Scoring
        ↓

Paper Selection
        ↓

LLM Deep Analysis
        ↓

Task
Problem
Method
Innovation
Evidence
Limitations
Relevance
        ↓

Paper PDF
        ↓

Model Figure Extraction
        ↓

papers.json
conference_papers.json
figures/
        ↓

GitHub Pages
        ↓

PaperPrism
        ↓

Unread / Read / Favorite
```

---

# 🚀 Quick Start

## 1. Fork

Fork this repository into your own GitHub account.

---

## 2. Enable GitHub Pages

进入：

```text
Settings
→ Pages
```

选择：

```text
Build and deployment
→ Source
→ GitHub Actions
```

---

## 3. Configure Research Interests

进入：

```text
Issues
```

创建一个标题严格为：

```text
Research Interests
```

的 Issue。

在 Issue 中配置 JSON，例如：

```json
{
  "sources": [
    {
      "type": "arxiv",
      "name": "arXiv"
    },
    {
      "type": "openalex",
      "name": "OpenAlex"
    },
    {
      "type": "crossref",
      "name": "Crossref"
    }
  ],

  "topics": [
    {
      "id": "multimodal_dialogue_understanding",

      "name": "Multimodal Dialogue Understanding",

      "description": "Multimodal dialogue understanding and reasoning.",

      "keywords": [
        "multimodal dialogue",
        "spoken dialogue",
        "multimodal conversation",
        "dialogue understanding"
      ],

      "arxiv_categories": [
        "cs.CL",
        "cs.AI",
        "cs.MM"
      ]
    }
  ]
}
```

---

# 🔑 LLM Configuration

进入：

```text
Settings
→ Secrets and variables
→ Actions
```

---

## Secrets

建议配置：

```text
LLM_API_KEY
```

不要将 API Key 写进公开代码。

---

## Variables

例如：

```text
LLM_BASE_URL
LLM_MODEL
LLM_API_MODE
```

OpenAI-compatible Responses API 可以配置为：

```text
LLM_API_MODE=responses
```

---

## Recommended LLM Settings

```text
MAX_SUMMARIES=100

LLM_CONCURRENCY=2

LLM_SUMMARIZE_CONFERENCE=true

LLM_SUMMARIZE_TITLE_ONLY=false
```

推荐保持：

```text
LLM_SUMMARIZE_TITLE_ONLY=false
```

因为如果论文只有标题而没有可靠摘要，直接让 LLM 推测：

```text
Problem
Method
Innovation
```

可能造成错误信息。

---

# 🖼 Model Figure Configuration

模型图功能依赖：

```text
PyMuPDF
```

`requirements.txt`：

```text
PyMuPDF>=1.24.0,<2.0.0
```

推荐配置：

```text
ENABLE_MODEL_FIGURE=true

MAX_MODEL_FIGURES_PER_RUN=100

PDF_TIMEOUT_SECONDS=45

PDF_MAX_BYTES=31457280

MODEL_FIGURE_MIN_SCORE=0.48

MODEL_FIGURE_MAX_PAGES=12

MODEL_FIGURE_DPI=180
```

---

# ♻️ Cache

PaperPrism 使用 GitHub Actions Cache 保存历史论文数据和模型图。

主要包括：

```text
.paper-cache/
├── papers.json
├── conference_papers.json
└── figures/
```

运行时会恢复到：

```text
web/data/
├── papers.json
├── conference_papers.json
└── figures/
```

---

## `clear_cache = false`

推荐日常使用。

```text
历史论文
+
新论文
        ↓
增量更新
```

优点：

```text
数据源临时失败时
不会轻易丢失历史数据
```

---

## `clear_cache = true`

表示重新初始化论文数据。

适合：

```text
重大版本升级

修改研究兴趣

重新生成 AI Analysis

重新生成 Model Figure
```

但如果此时：

```text
arXiv → 429

OpenAlex → 429

DBLP → 500
```

重新抓取的数据可能暂时减少。

因此：

> 日常自动运行建议使用 `clear_cache=false`。

---

# 📂 Project Structure

```text
PaperPrism/
├── .github/
│   └── workflows/
│       └── paper-daily.yml
│
├── config/
│
├── scripts/
│   └── collect_papers.py
│
├── tests/
│
├── web/
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   │
│   └── data/
│       ├── papers.json
│       ├── conference_papers.json
│       │
│       └── figures/
│
├── requirements.txt
└── README.md
```

---

# 🧪 Local Preview

在仓库根目录执行：

```bash
python -m http.server 8000 --directory web
```

然后打开：

```text
http://localhost:8000
```

不建议直接双击：

```text
web/index.html
```

因为浏览器本地文件安全策略可能导致：

```javascript
fetch("./data/papers.json")
```

失败。

---

# ⚠️ Notes

## arXiv / OpenAlex 429

GitHub Actions 使用共享云服务器 IP。

因此：

```text
arXiv
OpenAlex
```

偶尔可能返回：

```text
HTTP 429 Too Many Requests
```

这通常并不代表代码存在错误。

PaperPrism 使用多数据源策略降低这一问题的影响。

---

## Why do some papers not have deep AI analysis?

可能原因：

```text
没有可靠 Abstract

超过 MAX_SUMMARIES

LLM 单次请求失败

数据源只提供题录信息
```

为了降低幻觉风险，PaperPrism 默认不会仅根据标题推测论文完整方法和创新。

---

## Why do some papers not have a model figure?

模型图不会强制生成。

可能原因：

```text
论文没有明显 Architecture Figure

PDF 无法访问

Figure Caption 不够明确

模型图置信度不足

论文属于理论研究

PDF 页面结构较复杂
```

PaperPrism 的原则是：

> 宁可不展示模型图，也不随意将实验结果图识别成模型架构图。

---

# 🔐 Security

不要把任何 API Key 写入：

```text
README.md

index.html

app.js

styles.css

collect_papers.py

Research Interests Issue
```

应该使用：

```text
GitHub Actions Secrets
```

保存敏感信息。

---

# 🙏 Acknowledgements

PaperPrism 并不是从零开始构建的项目。

本项目基于开源项目：

**Paper Daily**

https://github.com/Futuresxy/paper-daily

进行二次开发和功能扩展。

原项目提供了自动论文收集、GitHub Actions 工作流以及 GitHub Pages 展示等重要基础能力。

PaperPrism 在此基础上进一步加入和扩展了：

```text
多来源论文发现

个性化研究兴趣匹配

顶会论文追踪

LLM 中英双语深度论文分析

Task / Problem / Method / Innovation 结构化阅读

论文原始 Model Figure 自动识别与截取

Evidence / Limitations / Relevance 分析

未读 / 已读 / 精选阅读管理

多主题科研阅读工作台
```

在此感谢 **Paper Daily** 项目的原作者及所有贡献者。

如果你使用、修改或重新发布 PaperPrism，请同时遵循原项目及当前仓库所采用的开源许可证和相关要求。

---

# 📌 Current Status

PaperPrism currently supports:

```text
Multi-source Paper Discovery          ✅

Personalized Research Interests       ✅

Daily Paper Tracking                  ✅

Top Conference Tracking               ✅

LLM Bilingual Analysis                ✅

Task Definition                       ✅

Detailed Problem Analysis             ✅

Detailed Method Analysis              ✅

Detailed Innovation Analysis          ✅

Evidence & Limitations                ✅

Research Relevance Explanation        ✅

Automatic Model Figure Extraction     ✅

Unread / Read / Favorite              ✅

Browser-local Reading State           ✅

GitHub Actions Automation             ✅

GitHub Pages Deployment               ✅

Multiple UI Themes                    ✅
```

---

## PaperPrism

**Discover what matters. Understand papers faster.**
