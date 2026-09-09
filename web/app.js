const THEME_STORAGE_KEY = "paper-daily-theme-v2";
const READING_STORAGE_KEY = "paper-daily-reading-state-v1";

const DEFAULT_THEME = "pink";
const THEMES = new Set(["pink", "light", "dark", "eye"]);
const READING_STATES = new Set(["unread", "read", "favorite"]);

const state = {
  datasets: {
    daily: null,
    conference: null,
  },

  theme: DEFAULT_THEME,

  reading: {
    // key -> { status: "read" | "favorite", updated_at: ISO string }
    papers: {},
  },

  filters: {
    query: "",
    topic: "all",
    level: "all",
    collection: "daily",
    view: "daily",
    date: "",
    reading: "unread",
  },

  loadError: "",
};


const nodes = {
  updatedAt: document.querySelector("#updatedAt"),

  paperCount: document.querySelector("#paperCount"),
  weekCount: document.querySelector("#weekCount"),
  monthCount: document.querySelector("#monthCount"),
  topScore: document.querySelector("#topScore"),

  unreadCount: document.querySelector("#unreadCount"),
  readCount: document.querySelector("#readCount"),
  favoriteCount: document.querySelector("#favoriteCount"),

  resultCount: document.querySelector("#resultCount"),
  viewTitle: document.querySelector("#viewTitle"),
  listTitle: document.querySelector("#listTitle"),
  scopeLabel: document.querySelector("#scopeLabel"),

  paperList: document.querySelector("#paperList"),
  emptyState: document.querySelector("#emptyState"),
  emptyStateTitle: document.querySelector("#emptyStateTitle"),
  emptyStateText: document.querySelector("#emptyStateText"),

  topicFilter: document.querySelector("#topicFilter"),
  levelFilter: document.querySelector("#levelFilter"),
  dateFilter: document.querySelector("#dateFilter"),
  searchInput: document.querySelector("#searchInput"),

  themeOptions: document.querySelectorAll("[data-theme-option]"),
  collectionTabs: document.querySelectorAll("[data-collection]"),
  tabs: document.querySelectorAll(".tab"),
  readingTabs: document.querySelectorAll("[data-reading-state]"),

  template: document.querySelector("#paperTemplate"),
};


/* =========================================================
   Data
   ========================================================= */

function emptyDataset() {
  return {
    generated_at_iso: new Date().toISOString(),
    papers: [],
    topics: [],
    stats: {},
  };
}


function activeData() {
  return (
    state.datasets[state.filters.collection] ||
    state.datasets.daily ||
    emptyDataset()
  );
}


/* =========================================================
   Theme
   ========================================================= */

function storedTheme() {
  try {
    const theme = localStorage.getItem(THEME_STORAGE_KEY);
    return THEMES.has(theme) ? theme : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}


function applyTheme(theme) {
  state.theme = THEMES.has(theme) ? theme : DEFAULT_THEME;
  document.body.dataset.theme = state.theme;

  for (const option of nodes.themeOptions) {
    const active = option.dataset.themeOption === state.theme;

    option.classList.toggle("active", active);
    option.setAttribute("aria-checked", String(active));
  }

  try {
    localStorage.setItem(THEME_STORAGE_KEY, state.theme);
  } catch {
    // localStorage can be unavailable in restrictive/private browser modes.
  }
}


/* =========================================================
   Local reading state
   ========================================================= */

/**
 * Reading state is intentionally local-only.
 *
 * Nothing is uploaded to GitHub or another service.
 *
 * Stored shape:
 * {
 *   version: 1,
 *   papers: {
 *     "arxiv:2609.12345": {
 *       status: "read",
 *       updated_at: "2026-09-09T..."
 *     }
 *   }
 * }
 *
 * Missing key = unread.
 */
function loadReadingState() {
  const empty = { papers: {} };

  try {
    const raw = localStorage.getItem(READING_STORAGE_KEY);
    if (!raw) return empty;

    const parsed = JSON.parse(raw);
    const source =
      parsed &&
      typeof parsed === "object" &&
      parsed.papers &&
      typeof parsed.papers === "object"
        ? parsed.papers
        : parsed;

    if (!source || typeof source !== "object") {
      return empty;
    }

    const papers = {};

    for (const [key, value] of Object.entries(source)) {
      if (!key) continue;

      // Compatibility with a possible old direct map:
      // { "paper-key": "read" }
      if (typeof value === "string") {
        if (value === "read" || value === "favorite") {
          papers[key] = {
            status: value,
            updated_at: "",
          };
        }

        continue;
      }

      if (!value || typeof value !== "object") {
        continue;
      }

      const status = String(value.status || "").toLowerCase();

      if (status !== "read" && status !== "favorite") {
        continue;
      }

      papers[key] = {
        status,
        updated_at: String(value.updated_at || ""),
      };
    }

    return { papers };
  } catch {
    return empty;
  }
}


function saveReadingState() {
  try {
    localStorage.setItem(
      READING_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        updated_at: new Date().toISOString(),
        papers: state.reading.papers,
      })
    );
  } catch {
    // The site still works without persistence if localStorage is unavailable.
  }
}


function normalizedArxivId(value) {
  return String(value || "")
    .trim()
    .replace(/^arxiv:/i, "")
    .replace(/^https?:\/\/arxiv\.org\/(?:abs|pdf)\//i, "")
    .replace(/\.pdf$/i, "")
    .replace(/v\d+$/i, "");
}


function normalizedDoi(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
    .replace(/^doi:/i, "");
}


/**
 * Try to use the most stable identity available so a paper keeps the same
 * local reading state after later metadata enrichment.
 */
function paperStateKey(paper) {
  const explicitArxiv =
    paper.arxiv_id ||
    (
      String(paper.source || "").toLowerCase().includes("arxiv")
        ? paper.id
        : ""
    );

  const arxivId = normalizedArxivId(explicitArxiv);

  if (arxivId && /^\d{4}\.\d{4,5}$/i.test(arxivId)) {
    return `arxiv:${arxivId}`;
  }

  const doi =
    normalizedDoi(
      paper.conference?.doi ||
      (
        String(paper.id || "").toLowerCase().startsWith("crossref:")
          ? String(paper.id).slice("crossref:".length)
          : ""
      ) ||
      (
        String(paper.paper_url || "").toLowerCase().includes("doi.org/")
          ? paper.paper_url
          : ""
      )
    );

  if (doi) {
    return `doi:${doi}`;
  }

  const id = String(paper.id || "").trim();

  if (id) {
    return `id:${id}`;
  }

  const url = String(paper.paper_url || "").trim();

  if (url) {
    return `url:${url}`;
  }

  const title = String(paper.title || "paper")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();

  return `title:${title}`;
}


function readingRecordByKey(key) {
  const record = state.reading.papers[key];

  if (!record || typeof record !== "object") {
    return null;
  }

  if (record.status !== "read" && record.status !== "favorite") {
    return null;
  }

  return record;
}


function readingStatusByKey(key) {
  return readingRecordByKey(key)?.status || "unread";
}


function readingStatusOf(paper) {
  return readingStatusByKey(paperStateKey(paper));
}


function readingUpdatedAtOf(paper) {
  return readingRecordByKey(paperStateKey(paper))?.updated_at || "";
}


function setReadingStatusByKey(key, status) {
  if (!key || !READING_STATES.has(status)) {
    return;
  }

  if (status === "unread") {
    delete state.reading.papers[key];
  } else {
    state.reading.papers[key] = {
      status,
      updated_at: new Date().toISOString(),
    };
  }

  saveReadingState();
}


/* =========================================================
   Date helpers
   ========================================================= */

function parseDate(value) {
  if (!value) return null;

  const date = new Date(value);

  return Number.isNaN(date.getTime())
    ? null
    : date;
}


function formatDate(value) {
  const date = parseDate(value);

  if (!date) {
    return value
      ? String(value).slice(0, 10)
      : "-";
  }

  return date.toLocaleDateString(
    "zh-CN",
    {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }
  );
}


function dateKey(value) {
  const date = parseDate(value);

  if (!date) return "";

  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");

  return `${year}-${month}-${day}`;
}


function collectionTime(paper) {
  return (
    paper.last_seen_at ||
    paper.first_seen_at ||
    paper.published ||
    paper.updated ||
    ""
  );
}


function startOfDay(date) {
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    date.getDate()
  );
}


function startOfWeek(date) {
  const day = startOfDay(date);
  const offset = (day.getDay() + 6) % 7;

  day.setDate(day.getDate() - offset);

  return day;
}


function endOfWeek(date) {
  const end = startOfWeek(date);

  end.setDate(end.getDate() + 7);

  return end;
}


function startOfMonth(date) {
  return new Date(
    date.getFullYear(),
    date.getMonth(),
    1
  );
}


function endOfMonth(date) {
  return new Date(
    date.getFullYear(),
    date.getMonth() + 1,
    1
  );
}


function inRange(value, start, end) {
  const date = parseDate(value);

  return Boolean(
    date &&
    date >= start &&
    date < end
  );
}


function selectedDate() {
  return (
    parseDate(`${state.filters.date}T12:00:00`) ||
    new Date()
  );
}


/* =========================================================
   Paper matching / search
   ========================================================= */

function scoreOf(paper) {
  return Number(paper.best_match?.score || 0);
}


function levelOf(paper) {
  return String(
    paper.best_match?.level || "low"
  ).toLowerCase();
}


function pairOriginal(pair) {
  if (!pair || typeof pair !== "object") return "";

  return String(
    pair.original ||
    pair.en ||
    pair.english ||
    pair.source ||
    ""
  );
}


function pairChinese(pair) {
  if (!pair || typeof pair !== "object") return "";

  return String(
    pair.zh ||
    pair.cn ||
    pair.chinese ||
    pair.translation ||
    ""
  );
}


function analysisSectionPairs(paper, field) {
  const value = paper.ai_analysis?.[field];

  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === "string") {
          return {
            original: "",
            zh: item,
          };
        }

        return {
          original: pairOriginal(item),
          zh: pairChinese(item),
        };
      })
      .filter((pair) => pair.original || pair.zh);
  }

  if (value && typeof value === "object") {
    const pair = {
      original: pairOriginal(value),
      zh: pairChinese(value),
    };

    return pair.original || pair.zh
      ? [pair]
      : [];
  }

  if (typeof value === "string" && value.trim()) {
    return [{
      original: "",
      zh: value,
    }];
  }

  const legacy = paper.chinese_summary?.[field];

  if (typeof legacy === "string" && legacy.trim()) {
    return [{
      original: "",
      zh: legacy,
    }];
  }

  return [];
}


function analysisTitle(paper) {
  const title = paper.ai_analysis?.title;

  if (title && typeof title === "object") {
    return {
      original:
        pairOriginal(title) ||
        String(paper.title || ""),
      zh: pairChinese(title),
    };
  }

  return {
    original: String(paper.title || ""),
    zh: "",
  };
}


function analysisSearchText(paper) {
  const parts = [];
  const title = analysisTitle(paper);

  parts.push(title.original, title.zh);

  for (const field of [
    "problem",
    "method",
    "innovation",
    "evidence",
    "limitations",
    "why_relevant",
  ]) {
    for (const pair of analysisSectionPairs(paper, field)) {
      parts.push(pair.original, pair.zh);
    }
  }

  return parts.join(" ");
}


/**
 * Search range:
 * - English title / Chinese title
 * - abstract
 * - authors
 * - categories
 * - relevance reason
 * - bilingual AI analysis
 */
function textIncludes(paper, query) {
  if (!query) return true;

  const haystack = [
    paper.title,
    paper.summary,

    (paper.authors || []).join(" "),
    (paper.categories || []).join(" "),

    paper.best_match?.reason,
    paper.best_match?.llm_reason,

    analysisSearchText(paper),

    // Explicit legacy fields for old cached papers.
    paper.chinese_summary?.problem,
    paper.chinese_summary?.method,
    paper.chinese_summary?.innovation,
    paper.chinese_summary?.evidence,
    paper.chinese_summary?.limitations,
    paper.chinese_summary?.why_relevant,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  return haystack.includes(query.toLowerCase());
}


function matchesBaseFilters(paper) {
  if (!textIncludes(paper, state.filters.query)) {
    return false;
  }

  if (
    state.filters.topic !== "all" &&
    paper.best_match?.topic_id !== state.filters.topic
  ) {
    return false;
  }

  if (
    state.filters.level !== "all" &&
    levelOf(paper) !== state.filters.level
  ) {
    return false;
  }

  return true;
}


function matchesView(paper) {
  if (state.filters.view === "all") {
    return true;
  }

  const date = selectedDate();
  const collectedAt = collectionTime(paper);

  if (state.filters.view === "daily") {
    return dateKey(collectedAt) === state.filters.date;
  }

  if (state.filters.view === "week") {
    return inRange(
      collectedAt,
      startOfWeek(date),
      endOfWeek(date)
    );
  }

  if (state.filters.view === "month") {
    return inRange(
      collectedAt,
      startOfMonth(date),
      endOfMonth(date)
    );
  }

  return true;
}


function matchesReadingState(paper) {
  return readingStatusOf(paper) === state.filters.reading;
}


function comparePapers(a, b) {
  // Read/favorite lists feel better when the most recently acted-on paper
  // appears first. Unread papers stay relevance-first.
  if (state.filters.reading !== "unread") {
    const stateOrder =
      String(readingUpdatedAtOf(b)).localeCompare(
        String(readingUpdatedAtOf(a))
      );

    if (stateOrder !== 0) {
      return stateOrder;
    }
  }

  return (
    scoreOf(b) - scoreOf(a) ||
    String(b.published || "").localeCompare(
      String(a.published || "")
    )
  );
}


function filteredPapers() {
  return (activeData().papers || [])
    .filter(
      (paper) =>
        matchesReadingState(paper) &&
        matchesBaseFilters(paper) &&
        matchesView(paper)
    )
    .sort(comparePapers);
}


/* =========================================================
   DOM helpers
   ========================================================= */

function setText(parent, selector, text, fallback = "暂无") {
  const element = parent.querySelector(selector);

  if (!element) return;

  element.textContent =
    text === null ||
    text === undefined ||
    text === ""
      ? fallback
      : String(text);
}


function safeFilename(paper) {
  const title = String(
    paper.title ||
    paper.id ||
    "paper"
  )
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);

  return `${title || "paper"}.pdf`;
}


function renderBilingualPairs(container, pairs) {
  if (!container) return;

  container.textContent = "";

  if (!pairs.length) {
    const empty = document.createElement("p");

    empty.className = "bilingual-empty";
    empty.textContent = "暂无可靠分析";

    container.appendChild(empty);
    return;
  }

  for (const pair of pairs) {
    const block = document.createElement("div");

    block.className = "bilingual-pair";

    if (pair.original) {
      const original = document.createElement("p");

      original.className = "bilingual-original";
      original.lang = "en";
      original.textContent = pair.original;

      block.appendChild(original);
    }

    if (pair.zh) {
      const chinese = document.createElement("p");

      chinese.className = "bilingual-zh";
      chinese.lang = "zh-CN";
      chinese.textContent = pair.zh;

      block.appendChild(chinese);
    }

    container.appendChild(block);
  }
}


function showElement(element, visible) {
  if (!element) return;

  element.hidden = !visible;
}


/* =========================================================
   Reading state controls
   ========================================================= */

function configureReadingButtons(node, paper) {
  const status = readingStatusOf(paper);

  const readButton = node.querySelector('[data-action="read"]');
  const favoriteButton = node.querySelector('[data-action="favorite"]');
  const unreadButton = node.querySelector('[data-action="unread"]');

  node.dataset.paperKey = paperStateKey(paper);
  node.dataset.readingState = status;

  // unread:
  //   [已读] [精选]
  //
  // read:
  //   [精选] [设为未读]
  //
  // favorite:
  //   [已读] [设为未读]
  showElement(readButton, status !== "read");
  showElement(favoriteButton, status !== "favorite");
  showElement(unreadButton, status !== "unread");

  if (readButton) {
    readButton.setAttribute(
      "aria-label",
      status === "favorite"
        ? "从精选转为已读"
        : "标记为已读"
    );

    readButton.title =
      status === "favorite"
        ? "从精选转为已读"
        : "标记为已读";
  }

  if (favoriteButton) {
    favoriteButton.setAttribute(
      "aria-label",
      status === "read"
        ? "从已读加入精选"
        : "加入精选"
    );

    favoriteButton.title =
      status === "read"
        ? "从已读加入精选"
        : "加入精选";
  }

  if (unreadButton) {
    unreadButton.setAttribute(
      "aria-label",
      status === "favorite"
        ? "取消精选并恢复为未读"
        : "取消已读并恢复为未读"
    );

    unreadButton.title =
      status === "favorite"
        ? "取消精选并恢复为未读"
        : "取消已读并恢复为未读";
  }
}


/* =========================================================
   Paper card rendering
   ========================================================= */

function renderPaper(paper) {
  const node =
    nodes.template.content
      .firstElementChild
      .cloneNode(true);

  const best = paper.best_match || {};
  const level = levelOf(paper);

  configureReadingButtons(node, paper);

  /* ---------- Match badge ---------- */

  const badge = node.querySelector(".match-badge");

  if (badge) {
    badge.textContent =
      `${level} ${scoreOf(paper).toFixed(2)}`;

    badge.classList.add(level);
  }


  /* ---------- Metadata ---------- */

  setText(
    node,
    ".paper-date",
    `发布 ${formatDate(paper.published)} · 收录 ${formatDate(collectionTime(paper))}`
  );

  setText(
    node,
    ".paper-source",
    paper.source || "paper"
  );


  /* ---------- Bilingual title ---------- */

  const title = analysisTitle(paper);

  setText(
    node,
    ".paper-title",
    title.original || paper.title
  );

  const titleZh = node.querySelector(".paper-title-zh");

  if (titleZh) {
    if (title.zh) {
      titleZh.textContent = title.zh;
      titleZh.hidden = false;
    } else {
      titleZh.textContent = "";
      titleZh.hidden = true;
    }
  }


  /* ---------- Authors ---------- */

  setText(
    node,
    ".paper-authors",
    (paper.authors || [])
      .slice(0, 8)
      .join(", "),
    "作者信息暂无"
  );


  /* ---------- Bilingual AI analysis ---------- */

  renderBilingualPairs(
    node.querySelector(".summary-problem"),
    analysisSectionPairs(paper, "problem")
  );

  renderBilingualPairs(
    node.querySelector(".summary-method"),
    analysisSectionPairs(paper, "method")
  );

  renderBilingualPairs(
    node.querySelector(".summary-innovation"),
    analysisSectionPairs(paper, "innovation")
  );

  renderBilingualPairs(
    node.querySelector(".summary-evidence"),
    analysisSectionPairs(paper, "evidence")
  );

  renderBilingualPairs(
    node.querySelector(".summary-limitations"),
    analysisSectionPairs(paper, "limitations")
  );

  renderBilingualPairs(
    node.querySelector(".summary-relevant"),
    analysisSectionPairs(paper, "why_relevant")
  );


  /* ---------- Match reason ---------- */

  setText(
    node,
    ".match-reason",
    `${best.topic_name || "未分类"}：${best.reason || ""}`
  );


  /* ---------- Categories ---------- */

  const tags = node.querySelector(".paper-tags");

  if (tags) {
    for (const category of (paper.categories || []).slice(0, 8)) {
      const tag = document.createElement("span");

      tag.className = "tag";
      tag.textContent = category;

      tags.appendChild(tag);
    }
  }


  /* ---------- Links ---------- */

  const absLink = node.querySelector(".abs-link");
  const pdfLink = node.querySelector(".pdf-link");
  const downloadLink = node.querySelector(".download-link");

  const paperUrl = String(paper.paper_url || "");
  const pdfUrl = String(paper.pdf_url || "");

  if (absLink) {
    if (paperUrl) {
      absLink.href = paperUrl;
      absLink.hidden = false;
    } else {
      absLink.hidden = true;
    }
  }

  if (pdfLink) {
    if (pdfUrl) {
      pdfLink.href = pdfUrl;
      pdfLink.hidden = false;
    } else {
      pdfLink.hidden = true;
    }
  }

  if (downloadLink) {
    if (pdfUrl) {
      downloadLink.href = pdfUrl;
      downloadLink.hidden = false;

      downloadLink.setAttribute(
        "download",
        safeFilename(paper)
      );

      downloadLink.setAttribute(
        "target",
        "_blank"
      );

      downloadLink.setAttribute(
        "rel",
        "noreferrer"
      );
    } else {
      downloadLink.hidden = true;
    }
  }

  return node;
}


/* =========================================================
   Page labels
   ========================================================= */

function viewLabels() {
  const date = selectedDate();
  const dayLabel = formatDate(date.toISOString());

  const weekStart = formatDate(
    startOfWeek(date).toISOString()
  );

  const weekEndDate = endOfWeek(date);
  weekEndDate.setDate(weekEndDate.getDate() - 1);

  const weekEnd = formatDate(
    weekEndDate.toISOString()
  );

  const monthLabel =
    `${date.getFullYear()} 年 ${String(
      date.getMonth() + 1
    ).padStart(2, "0")} 月`;

  return {
    all: [
      state.filters.collection === "conference"
        ? "全部顶会论文"
        : "全部论文",
      "全部已收录论文",
    ],

    daily: [
      "当日论文",
      dayLabel,
    ],

    week: [
      "本周论文",
      `${weekStart} - ${weekEnd}`,
    ],

    month: [
      "月度论文",
      monthLabel,
    ],
  };
}


function readingLabel() {
  return {
    unread: {
      title: "未读论文",
      short: "未读",
    },
    read: {
      title: "已读论文",
      short: "已读",
    },
    favorite: {
      title: "精选论文",
      short: "精选",
    },
  }[state.filters.reading];
}


function updateHeadings(papers) {
  const labels =
    viewLabels()[state.filters.view] ||
    viewLabels().all;

  const reading = readingLabel();

  if (nodes.viewTitle) {
    nodes.viewTitle.textContent = reading.title;
  }

  if (nodes.listTitle) {
    nodes.listTitle.textContent =
      `${reading.short} · ${labels[0]}`;
  }

  if (nodes.scopeLabel) {
    nodes.scopeLabel.textContent = labels[1];
  }

  if (nodes.resultCount) {
    nodes.resultCount.textContent =
      `${papers.length} 篇`;
  }
}


/* =========================================================
   Reading-state counters / empty state
   ========================================================= */

function updateReadingCounts() {
  const papers = activeData().papers || [];

  const counts = {
    unread: 0,
    read: 0,
    favorite: 0,
  };

  for (const paper of papers) {
    const status = readingStatusOf(paper);

    if (Object.hasOwn(counts, status)) {
      counts[status] += 1;
    }
  }

  if (nodes.unreadCount) {
    nodes.unreadCount.textContent = String(counts.unread);
  }

  if (nodes.readCount) {
    nodes.readCount.textContent = String(counts.read);
  }

  if (nodes.favoriteCount) {
    nodes.favoriteCount.textContent = String(counts.favorite);
  }
}


function updateReadingTabs() {
  for (const tab of nodes.readingTabs) {
    const active =
      tab.dataset.readingState ===
      state.filters.reading;

    tab.classList.toggle("active", active);
    tab.setAttribute(
      "aria-selected",
      String(active)
    );
  }
}


function updateEmptyState(visible) {
  if (!nodes.emptyState) return;

  nodes.emptyState.hidden = !visible;

  if (!visible) {
    return;
  }

  const config = {
    unread: {
      title: "当前没有未读论文",
      text: "新的论文收录后会自动进入未读列表。",
    },
    read: {
      title: "还没有已读论文",
      text: "在未读页点击“已读”，论文会移动到这里。",
    },
    favorite: {
      title: "还没有精选论文",
      text: "在论文卡片上点击“精选”，值得重点关注的论文会收集到这里。",
    },
  }[state.filters.reading];

  if (nodes.emptyStateTitle) {
    nodes.emptyStateTitle.textContent =
      config.title;
  }

  if (nodes.emptyStateText) {
    nodes.emptyStateText.textContent =
      config.text;
  }
}


/* =========================================================
   Main list rendering
   ========================================================= */

function render() {
  const papers = filteredPapers();

  updateReadingCounts();
  updateReadingTabs();
  updateHeadings(papers);

  nodes.paperList.textContent = "";
  updateEmptyState(!papers.length);

  if (!papers.length) {
    return;
  }

  const fragment =
    document.createDocumentFragment();

  for (const paper of papers) {
    fragment.appendChild(
      renderPaper(paper)
    );
  }

  nodes.paperList.appendChild(fragment);
}


/* =========================================================
   Filters
   ========================================================= */

function hydrateTopicFilter() {
  const previous = state.filters.topic;

  nodes.topicFilter.innerHTML =
    '<option value="all">全部方向</option>';

  for (const topic of activeData().topics || []) {
    const option =
      document.createElement("option");

    option.value = topic.id;
    option.textContent = topic.name;

    nodes.topicFilter.appendChild(option);
  }

  const valid = Array.from(nodes.topicFilter.options)
    .some((option) => option.value === previous);

  state.filters.topic = valid ? previous : "all";
  nodes.topicFilter.value = state.filters.topic;
}


function hydrateDateFilter() {
  const data = activeData();

  const dates = [
    ...new Set(
      (data.papers || [])
        .map(
          (paper) =>
            dateKey(collectionTime(paper))
        )
        .filter(Boolean)
    ),
  ].sort().reverse();

  const fallback =
    dateKey(
      data.generated_at_iso ||
      new Date().toISOString()
    ) ||
    dateKey(new Date().toISOString());

  const options =
    dates.length
      ? dates
      : [fallback];

  const previous = state.filters.date;

  state.filters.date =
    options.includes(previous)
      ? previous
      : options[0];

  nodes.dateFilter.textContent = "";

  for (const key of options) {
    const option =
      document.createElement("option");

    option.value = key;
    option.textContent =
      formatDate(`${key}T12:00:00`);

    nodes.dateFilter.appendChild(option);
  }

  nodes.dateFilter.value = state.filters.date;
}


/* =========================================================
   Statistics
   ========================================================= */

function updateStats() {
  const papers = activeData().papers || [];
  const date = selectedDate();

  const weekPapers =
    papers.filter(
      (paper) =>
        inRange(
          collectionTime(paper),
          startOfWeek(date),
          endOfWeek(date)
        )
    );

  const monthPapers =
    papers.filter(
      (paper) =>
        inRange(
          collectionTime(paper),
          startOfMonth(date),
          endOfMonth(date)
        )
    );

  const top =
    papers.reduce(
      (max, paper) =>
        Math.max(max, scoreOf(paper)),
      0
    );

  nodes.paperCount.textContent =
    String(papers.length);

  nodes.weekCount.textContent =
    String(weekPapers.length);

  nodes.monthCount.textContent =
    String(monthPapers.length);

  nodes.topScore.textContent =
    top.toFixed(2);

  updateReadingCounts();
}


/* =========================================================
   Tab-state helpers
   ========================================================= */

function updateCollectionTabs() {
  for (const item of nodes.collectionTabs) {
    const active =
      item.dataset.collection ===
      state.filters.collection;

    item.classList.toggle("active", active);

    item.setAttribute(
      "aria-selected",
      String(active)
    );
  }
}


function updateViewTabs() {
  for (const item of nodes.tabs) {
    const active =
      item.dataset.view ===
      state.filters.view;

    item.classList.toggle("active", active);

    item.setAttribute(
      "aria-selected",
      String(active)
    );
  }
}


/* =========================================================
   Events
   ========================================================= */

function bindEvents() {

  /* ---------- Theme ---------- */

  for (const option of nodes.themeOptions) {
    option.addEventListener(
      "click",
      () => {
        applyTheme(
          option.dataset.themeOption
        );
      }
    );
  }


  /* ---------- Search ---------- */

  nodes.searchInput.addEventListener(
    "input",
    (event) => {
      state.filters.query =
        event.target.value.trim();

      render();
    }
  );


  /* ---------- Topic ---------- */

  nodes.topicFilter.addEventListener(
    "change",
    (event) => {
      state.filters.topic =
        event.target.value;

      render();
    }
  );


  /* ---------- Level ---------- */

  nodes.levelFilter.addEventListener(
    "change",
    (event) => {
      state.filters.level =
        event.target.value;

      render();
    }
  );


  /* ---------- Reading state ---------- */

  for (const tab of nodes.readingTabs) {
    tab.addEventListener(
      "click",
      () => {
        const reading =
          tab.dataset.readingState;

        if (!READING_STATES.has(reading)) {
          return;
        }

        state.filters.reading = reading;

        updateReadingTabs();
        render();
      }
    );
  }


  /* ---------- Dataset ---------- */

  for (const tab of nodes.collectionTabs) {
    tab.addEventListener(
      "click",
      () => {
        const collection =
          tab.dataset.collection;

        if (!state.datasets[collection]) {
          return;
        }

        state.filters.collection =
          collection;

        state.filters.view =
          collection === "conference"
            ? "all"
            : "daily";

        state.filters.topic = "all";

        updateCollectionTabs();
        updateViewTabs();

        hydrateTopicFilter();
        hydrateDateFilter();

        updateStats();
        updateUpdatedAt();
        render();
      }
    );
  }


  /* ---------- Date ---------- */

  nodes.dateFilter.addEventListener(
    "change",
    (event) => {
      state.filters.date =
        event.target.value;

      updateStats();
      render();
    }
  );


  /* ---------- Time view ---------- */

  for (const tab of nodes.tabs) {
    tab.addEventListener(
      "click",
      () => {
        state.filters.view =
          tab.dataset.view;

        updateViewTabs();
        render();
      }
    );
  }


  /* ---------- Paper reading actions ---------- */

  nodes.paperList.addEventListener(
    "click",
    (event) => {
      const button =
        event.target.closest(
          "[data-action]"
        );

      if (!button) {
        return;
      }

      const card =
        button.closest(".paper-card");

      const paperKey =
        card?.dataset.paperKey;

      const action =
        button.dataset.action;

      if (
        !paperKey ||
        !READING_STATES.has(action)
      ) {
        return;
      }

      setReadingStatusByKey(
        paperKey,
        action
      );

      /*
       * The current card naturally disappears from the current tab:
       * unread -> read/favorite
       * read/favorite -> unread
       *
       * No server request is made.
       */
      render();
    }
  );
}


/* =========================================================
   Data loading
   ========================================================= */

async function loadData() {
  const response =
    await fetch(
      "./data/papers.json",
      {
        cache: "no-store",
      }
    );

  if (!response.ok) {
    throw new Error(
      `HTTP ${response.status}`
    );
  }

  return response.json();
}


async function loadOptionalData(path) {
  try {
    const response =
      await fetch(
        path,
        {
          cache: "no-store",
        }
      );

    if (!response.ok) {
      return emptyDataset();
    }

    return response.json();
  } catch {
    return emptyDataset();
  }
}


/* =========================================================
   Updated-at label
   ========================================================= */

function updateUpdatedAt(message = "") {
  if (message) {
    nodes.updatedAt.textContent =
      message;

    return;
  }

  if (state.loadError) {
    nodes.updatedAt.textContent =
      state.loadError;

    return;
  }

  const data = activeData();
  const stats = data.stats || {};

  const mode =
    stats.collection_mode === "incremental"
      ? "增量"
      : "初始化";

  const kind =
    state.filters.collection === "conference"
      ? "顶会精品"
      : "每日新论文";

  nodes.updatedAt.textContent =
    `${kind} · 更新于 ${formatDate(
      data.generated_at_iso
    )} · ${mode} · ${
      stats.llm_enabled
        ? "LLM"
        : "基础"
    }`;
}


/* =========================================================
   Initialization
   ========================================================= */

async function main() {

  /*
   * Theme and reading state are both local browser preferences.
   */
  applyTheme(storedTheme());
  state.reading = loadReadingState();

  bindEvents();

  try {
    state.datasets.daily =
      await loadData();

    state.datasets.conference =
      await loadOptionalData(
        "./data/conference_papers.json"
      );

    state.loadError = "";
  } catch (error) {
    state.datasets.daily =
      emptyDataset();

    state.datasets.conference =
      emptyDataset();

    state.loadError =
      `数据读取失败：${error.message}`;
  }

  hydrateTopicFilter();
  hydrateDateFilter();

  updateCollectionTabs();
  updateViewTabs();
  updateReadingTabs();

  updateStats();
  updateUpdatedAt();
  render();
}


/* =========================================================
   Start
   ========================================================= */

main();
