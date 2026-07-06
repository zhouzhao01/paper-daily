const THEME_STORAGE_KEY = "paper-daily-theme";
const THEMES = new Set(["dark", "light", "eye"]);
const FALLBACK_REPO = "zhouzhao01/paper-daily";

const state = {
  datasets: {
    library: null,
    conference: null,
  },
  theme: "dark",
  filters: {
    query: "",
    topic: "all",
    level: "all",
    track: "all",
    collection: "library",
    view: "all",
    date: "",
  },
};

const nodes = {
  updatedAt: document.querySelector("#updatedAt"),
  paperCount: document.querySelector("#paperCount"),
  weekCount: document.querySelector("#weekCount"),
  monthCount: document.querySelector("#monthCount"),
  topScore: document.querySelector("#topScore"),
  topScoreLabel: document.querySelector("#topScoreLabel"),
  resultCount: document.querySelector("#resultCount"),
  viewTitle: document.querySelector("#viewTitle"),
  listTitle: document.querySelector("#listTitle"),
  scopeLabel: document.querySelector("#scopeLabel"),
  paperList: document.querySelector("#paperList"),
  topicFilter: document.querySelector("#topicFilter"),
  levelFilter: document.querySelector("#levelFilter"),
  trackFilter: document.querySelector("#trackFilter"),
  dateFilter: document.querySelector("#dateFilter"),
  searchInput: document.querySelector("#searchInput"),
  configLink: document.querySelector("#configLink"),
  actionsLink: document.querySelector("#actionsLink"),
  themeOptions: document.querySelectorAll("[data-theme-option]"),
  collectionTabs: document.querySelectorAll("[data-collection]"),
  tabs: document.querySelectorAll(".tab"),
  libraryViews: document.querySelector("#libraryViews"),
  conferenceViews: document.querySelector("#conferenceViews"),
  trackField: document.querySelector("#trackField"),
  dateField: document.querySelector("#dateField"),
  levelField: document.querySelector("#levelField"),
  template: document.querySelector("#paperTemplate"),
};

function isLibrary() {
  return state.filters.collection === "library";
}

function emptyLibrary() {
  return { version: 1, updated_at_iso: new Date().toISOString(), topics: [], papers: [] };
}

function emptyConference() {
  return { generated_at_iso: new Date().toISOString(), topics: [], papers: [], stats: {} };
}

function activeData() {
  return state.datasets[state.filters.collection] || (isLibrary() ? emptyLibrary() : emptyConference());
}

function repoSlug() {
  const host = window.location.hostname || "";
  const match = host.match(/^([^.]+)\.github\.io$/);
  if (match) {
    const owner = match[1];
    const segment = (window.location.pathname || "").split("/").filter(Boolean)[0];
    return `${owner}/${segment || "paper-daily"}`;
  }
  return FALLBACK_REPO;
}

function applyRepoLinks() {
  const slug = repoSlug();
  if (nodes.configLink) nodes.configLink.href = `https://github.com/${slug}/blob/main/config/interests.json`;
  if (nodes.actionsLink) nodes.actionsLink.href = `https://github.com/${slug}/actions`;
}

function storedTheme() {
  try {
    const theme = localStorage.getItem(THEME_STORAGE_KEY);
    return THEMES.has(theme) ? theme : "dark";
  } catch {
    return "dark";
  }
}

function applyTheme(theme) {
  state.theme = THEMES.has(theme) ? theme : "dark";
  document.body.dataset.theme = state.theme;
  for (const option of nodes.themeOptions) {
    const active = option.dataset.themeOption === state.theme;
    option.classList.toggle("active", active);
    option.setAttribute("aria-checked", String(active));
  }
  try {
    localStorage.setItem(THEME_STORAGE_KEY, state.theme);
  } catch {
    // localStorage may be blocked in privacy-focused browser modes.
  }
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value) {
  const date = parseDate(value);
  if (!date) return value ? String(value).slice(0, 10) : "-";
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
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
  return paper.last_seen_at || paper.first_seen_at || paper.published || paper.updated || "";
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
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
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function endOfMonth(date) {
  return new Date(date.getFullYear(), date.getMonth() + 1, 1);
}

function inRange(value, start, end) {
  const date = parseDate(value);
  return Boolean(date && date >= start && date < end);
}

function selectedDate() {
  return parseDate(`${state.filters.date}T12:00:00`) || new Date();
}

// ---- Conference (legacy schema) helpers ----

function scoreOf(paper) {
  return Number(paper.best_match?.score || 0);
}

function levelOf(paper) {
  return String(paper.best_match?.level || "low").toLowerCase();
}

function textIncludes(paper, query) {
  if (!query) return true;
  const haystack = [
    paper.title,
    paper.summary,
    (paper.authors || []).join(" "),
    (paper.categories || []).join(" "),
    paper.best_match?.reason,
    paper.chinese_summary?.innovation,
    paper.chinese_summary?.evidence,
    paper.chinese_summary?.limitations,
    paper.chinese_summary?.why_relevant,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function matchesBaseFilters(paper) {
  if (!textIncludes(paper, state.filters.query)) return false;
  if (state.filters.topic !== "all" && paper.best_match?.topic_id !== state.filters.topic) return false;
  if (state.filters.level !== "all" && levelOf(paper) !== state.filters.level) return false;
  return true;
}

function matchesView(paper) {
  if (state.filters.view === "all") return true;
  const date = selectedDate();
  const collectedAt = collectionTime(paper);
  if (state.filters.view === "daily") return dateKey(collectedAt) === state.filters.date;
  if (state.filters.view === "week") return inRange(collectedAt, startOfWeek(date), endOfWeek(date));
  if (state.filters.view === "month") return inRange(collectedAt, startOfMonth(date), endOfMonth(date));
  if (state.filters.view === "highlights") {
    return inRange(collectedAt, startOfWeek(date), endOfWeek(date)) && scoreOf(paper) >= 0.42;
  }
  return true;
}

function filteredConferencePapers() {
  return (activeData().papers || [])
    .filter((paper) => matchesBaseFilters(paper) && matchesView(paper))
    .sort((a, b) => scoreOf(b) - scoreOf(a) || String(b.published || "").localeCompare(String(a.published || "")));
}

// ---- Library (new schema) helpers ----

function libraryScore(paper) {
  return Number(paper.score || 0);
}

function addedAt(paper) {
  return `${paper.date_added || ""}T12:00:00`;
}

function textIncludesLibrary(paper, query) {
  if (!query) return true;
  const summary = paper.chinese_summary || {};
  const haystack = [
    paper.title,
    paper.summary,
    (paper.authors || []).join(" "),
    (paper.categories || []).join(" "),
    paper.justification,
    paper.topic_name,
    summary.problem,
    summary.method,
    summary.innovation,
    summary.evidence,
    summary.limitations,
    summary.why_relevant,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function matchesLibraryFilters(paper) {
  if (!textIncludesLibrary(paper, state.filters.query)) return false;
  if (state.filters.topic !== "all" && paper.topic_id !== state.filters.topic) return false;
  if (state.filters.track !== "all" && paper.track !== state.filters.track) return false;
  return true;
}

function matchesLibraryView(paper) {
  if (state.filters.view === "all") return true;
  const today = new Date();
  if (state.filters.view === "week") return inRange(addedAt(paper), startOfWeek(today), endOfWeek(today));
  if (state.filters.view === "month") return inRange(addedAt(paper), startOfMonth(today), endOfMonth(today));
  return true;
}

function filteredLibraryPapers() {
  return (activeData().papers || [])
    .filter((paper) => matchesLibraryFilters(paper) && matchesLibraryView(paper))
    .sort(
      (a, b) =>
        String(b.date_added || "").localeCompare(String(a.date_added || "")) || libraryScore(b) - libraryScore(a)
    );
}

function filteredPapers() {
  return isLibrary() ? filteredLibraryPapers() : filteredConferencePapers();
}

function setText(parent, selector, text) {
  parent.querySelector(selector).textContent = text || "暂无";
}

function safeFilename(paper) {
  const title = String(paper.title || paper.id || "paper")
    .replace(/[\\/:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
  return `${title || "paper"}.pdf`;
}

function applyLinks(node, paper) {
  const absLink = node.querySelector(".abs-link");
  const pdfLink = node.querySelector(".pdf-link");
  const downloadLink = node.querySelector(".download-link");
  const pdfUrl = paper.pdf_url || paper.paper_url || "#";
  absLink.href = paper.paper_url || "#";
  pdfLink.href = pdfUrl;
  downloadLink.href = pdfUrl;
  downloadLink.setAttribute("download", safeFilename(paper));
  downloadLink.setAttribute("target", "_blank");
  downloadLink.setAttribute("rel", "noreferrer");
}

function fillTags(node, paper) {
  const tags = node.querySelector(".paper-tags");
  for (const category of (paper.categories || []).slice(0, 8)) {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = category;
    tags.appendChild(tag);
  }
}

function renderConferencePaper(paper) {
  const node = nodes.template.content.firstElementChild.cloneNode(true);
  const best = paper.best_match || {};
  const summary = paper.chinese_summary || {};
  const badge = node.querySelector(".match-badge");
  const level = levelOf(paper);

  badge.textContent = `${level} ${scoreOf(paper).toFixed(2)}`;
  badge.classList.add(level);

  setText(node, ".paper-date", `发布 ${formatDate(paper.published)} · 收录 ${formatDate(collectionTime(paper))}`);
  setText(node, ".paper-source", paper.source || "paper");
  setText(node, ".paper-title", paper.title);
  setText(node, ".paper-authors", (paper.authors || []).slice(0, 8).join(", "));
  setText(node, ".summary-problem", summary.problem);
  setText(node, ".summary-method", summary.method);
  setText(node, ".summary-innovation", summary.innovation);
  setText(node, ".summary-evidence", summary.evidence);
  setText(node, ".summary-limitations", summary.limitations);
  setText(node, ".summary-relevant", summary.why_relevant);
  setText(node, ".match-reason", `${best.topic_name || "未分类"}：${best.reason || ""}`);

  fillTags(node, paper);
  applyLinks(node, paper);
  return node;
}

function renderLibraryPaper(paper) {
  const node = nodes.template.content.firstElementChild.cloneNode(true);
  const summary = paper.chinese_summary;
  const frontier = paper.track === "frontier";

  node.querySelector(".match-badge").classList.add("is-hidden");

  const trackBadge = node.querySelector(".track-badge");
  trackBadge.textContent = frontier ? "前沿" : "经典";
  trackBadge.classList.add(frontier ? "frontier" : "foundation");
  trackBadge.classList.remove("is-hidden");

  const scoreBadge = node.querySelector(".score-badge");
  scoreBadge.textContent = libraryScore(paper).toFixed(1);
  scoreBadge.classList.remove("is-hidden");

  const metaParts = [`发布 ${formatDate(paper.published)}`, `收录 ${formatDate(paper.date_added)}`];
  if (typeof paper.citation_count === "number") metaParts.push(`引用 ${paper.citation_count}`);
  node.querySelector(".paper-date").textContent = metaParts.join(" · ");
  node.querySelector(".paper-source").classList.add("is-hidden");

  setText(node, ".paper-title", paper.title);
  setText(node, ".paper-authors", (paper.authors || []).slice(0, 8).join(", "));

  const justification = node.querySelector(".paper-justification");
  if (paper.justification) {
    justification.textContent = `入选理由：${paper.justification}`;
    justification.classList.remove("is-hidden");
  }

  const grid = node.querySelector(".summary-grid");
  if (summary) {
    setText(node, ".summary-problem", summary.problem);
    setText(node, ".summary-method", summary.method);
    setText(node, ".summary-innovation", summary.innovation);
    setText(node, ".summary-evidence", summary.evidence);
    setText(node, ".summary-limitations", summary.limitations);
    setText(node, ".summary-relevant", summary.why_relevant);
  } else {
    grid.classList.add("is-hidden");
  }

  node.querySelector(".match-reason").textContent = paper.topic_name || "";

  fillTags(node, paper);
  applyLinks(node, paper);
  return node;
}

function renderPaper(paper) {
  return isLibrary() ? renderLibraryPaper(paper) : renderConferencePaper(paper);
}

function viewLabels() {
  const date = selectedDate();
  const dayLabel = formatDate(date.toISOString());
  const weekStart = formatDate(startOfWeek(date).toISOString());
  const weekEndDate = endOfWeek(date);
  weekEndDate.setDate(weekEndDate.getDate() - 1);
  const weekEnd = formatDate(weekEndDate.toISOString());
  const monthLabel = `${date.getFullYear()} 年 ${String(date.getMonth() + 1).padStart(2, "0")} 月`;
  return {
    all: ["顶会精品", "全部已收录论文"],
    daily: ["当日论文", dayLabel],
    week: ["本周论文", `${weekStart} - ${weekEnd}`],
    month: ["月度论文", monthLabel],
    highlights: ["本周精选", `${weekStart} - ${weekEnd}`],
  };
}

function libraryHeadings() {
  const today = new Date();
  if (state.filters.view === "week") {
    const weekStart = formatDate(startOfWeek(today).toISOString());
    const weekEndDate = endOfWeek(today);
    weekEndDate.setDate(weekEndDate.getDate() - 1);
    return ["本周新增", `${weekStart} - ${formatDate(weekEndDate.toISOString())}`];
  }
  if (state.filters.view === "month") {
    return ["本月新增", `${today.getFullYear()} 年 ${String(today.getMonth() + 1).padStart(2, "0")} 月`];
  }
  return ["精选文库", "全部精选论文"];
}

function updateHeadings(papers) {
  const labels = isLibrary() ? libraryHeadings() : viewLabels()[state.filters.view];
  nodes.viewTitle.textContent = labels[0];
  nodes.listTitle.textContent = labels[0];
  nodes.scopeLabel.textContent = labels[1];
  nodes.resultCount.textContent = `${papers.length} 篇`;
}

function render() {
  const papers = filteredPapers();
  updateHeadings(papers);
  nodes.paperList.textContent = "";

  if (!papers.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const total = (activeData().papers || []).length;
    if (isLibrary() && total === 0) {
      empty.textContent = "文库为空——每日任务运行后这里会逐步积累精选论文。";
    } else {
      empty.textContent = "当前筛选条件下没有论文。";
    }
    nodes.paperList.appendChild(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const paper of papers) fragment.appendChild(renderPaper(paper));
  nodes.paperList.appendChild(fragment);
}

function hydrateTopicFilter() {
  nodes.topicFilter.innerHTML = '<option value="all">全部方向</option>';
  for (const topic of activeData().topics || []) {
    const option = document.createElement("option");
    option.value = topic.id;
    option.textContent = topic.name;
    nodes.topicFilter.appendChild(option);
  }
  nodes.topicFilter.value = "all";
}

function hydrateDateFilter() {
  const data = activeData();
  const dates = [...new Set((data.papers || []).map((paper) => dateKey(collectionTime(paper))).filter(Boolean))].sort().reverse();
  const fallback = dateKey(data.generated_at_iso || new Date().toISOString());
  const options = dates.length ? dates : [fallback];
  state.filters.date = options[0];
  nodes.dateFilter.textContent = "";
  for (const key of options) {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = formatDate(`${key}T12:00:00`);
    nodes.dateFilter.appendChild(option);
  }
}

function updateStats() {
  const papers = activeData().papers || [];
  if (isLibrary()) {
    const today = new Date();
    const weekPapers = papers.filter((paper) => inRange(addedAt(paper), startOfWeek(today), endOfWeek(today)));
    const monthPapers = papers.filter((paper) => inRange(addedAt(paper), startOfMonth(today), endOfMonth(today)));
    const top = papers.reduce((max, paper) => Math.max(max, libraryScore(paper)), 0);
    nodes.paperCount.textContent = String(papers.length);
    nodes.weekCount.textContent = String(weekPapers.length);
    nodes.monthCount.textContent = String(monthPapers.length);
    nodes.topScore.textContent = top.toFixed(1);
    nodes.topScoreLabel.textContent = "最高评分";
    return;
  }
  const date = selectedDate();
  const weekPapers = papers.filter((paper) => inRange(collectionTime(paper), startOfWeek(date), endOfWeek(date)));
  const monthPapers = papers.filter((paper) => inRange(collectionTime(paper), startOfMonth(date), endOfMonth(date)));
  const top = papers.reduce((max, paper) => Math.max(max, scoreOf(paper)), 0);
  nodes.paperCount.textContent = String(papers.length);
  nodes.weekCount.textContent = String(weekPapers.length);
  nodes.monthCount.textContent = String(monthPapers.length);
  nodes.topScore.textContent = top.toFixed(2);
  nodes.topScoreLabel.textContent = "最高匹配";
}

function applyCollectionVisibility() {
  const lib = isLibrary();
  nodes.libraryViews.classList.toggle("is-hidden", !lib);
  nodes.conferenceViews.classList.toggle("is-hidden", lib);
  nodes.trackField.classList.toggle("is-hidden", !lib);
  nodes.dateField.classList.toggle("is-hidden", lib);
  nodes.levelField.classList.toggle("is-hidden", lib);
}

function syncViewTabs() {
  for (const item of nodes.tabs) {
    item.classList.toggle("active", item.dataset.view === state.filters.view);
  }
}

function bindEvents() {
  for (const option of nodes.themeOptions) {
    option.addEventListener("click", () => {
      applyTheme(option.dataset.themeOption);
    });
  }
  nodes.searchInput.addEventListener("input", (event) => {
    state.filters.query = event.target.value.trim();
    render();
  });
  nodes.topicFilter.addEventListener("change", (event) => {
    state.filters.topic = event.target.value;
    render();
  });
  nodes.levelFilter.addEventListener("change", (event) => {
    state.filters.level = event.target.value;
    render();
  });
  nodes.trackFilter.addEventListener("change", (event) => {
    state.filters.track = event.target.value;
    render();
  });
  for (const tab of nodes.collectionTabs) {
    tab.addEventListener("click", () => {
      state.filters.collection = tab.dataset.collection;
      state.filters.view = "all";
      state.filters.topic = "all";
      state.filters.track = "all";
      state.filters.level = "all";
      nodes.levelFilter.value = "all";
      nodes.trackFilter.value = "all";
      for (const item of nodes.collectionTabs) item.classList.toggle("active", item === tab);
      applyCollectionVisibility();
      syncViewTabs();
      hydrateTopicFilter();
      if (!isLibrary()) hydrateDateFilter();
      updateStats();
      updateUpdatedAt();
      render();
    });
  }
  nodes.dateFilter.addEventListener("change", (event) => {
    state.filters.date = event.target.value;
    updateStats();
    render();
  });
  for (const tab of nodes.tabs) {
    tab.addEventListener("click", () => {
      state.filters.view = tab.dataset.view;
      syncViewTabs();
      render();
    });
  }
}

async function loadOptionalData(path, fallbackFactory) {
  try {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) return fallbackFactory();
    return await response.json();
  } catch {
    return fallbackFactory();
  }
}

function updateUpdatedAt(message = "") {
  if (message) {
    nodes.updatedAt.textContent = message;
    return;
  }
  const data = activeData();
  if (isLibrary()) {
    const count = (data.papers || []).length;
    nodes.updatedAt.textContent = `精选文库 · 更新于 ${formatDate(data.updated_at_iso)} · 共 ${count} 篇`;
    return;
  }
  const stats = data.stats || {};
  const mode = stats.collection_mode === "incremental" ? "增量" : "初始化";
  nodes.updatedAt.textContent = `顶会精品 · 更新于 ${formatDate(data.generated_at_iso)} · ${mode} · ${stats.llm_enabled ? "LLM" : "基础"}`;
}

async function main() {
  applyTheme(storedTheme());
  applyRepoLinks();
  bindEvents();

  state.datasets.library = await loadOptionalData("./data/library.json", emptyLibrary);
  state.datasets.conference = await loadOptionalData("./data/conference_papers.json", emptyConference);

  applyCollectionVisibility();
  syncViewTabs();
  updateUpdatedAt();
  hydrateTopicFilter();
  updateStats();
  render();
}

main();
