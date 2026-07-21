const OUTPUT_BASE_URL = "output/";
const RUNS_INDEX_URL = `${OUTPUT_BASE_URL}runs.json`;
const PAGE_SIZE = 50;
const TOP_DOMAINS = 8;
const THEMES = ["auto", "light", "dark"];

const state = {
  runs: [],
  results: [],
  summary: null,
  errors: null,
  errorsNote: "",
  search: "",
  query: "all",
  status: "all",
  sort: "rank",
  visibleCount: PAGE_SIZE,
  filtered: [],
  tab: "results",
  theme: "auto",
};

const els = {
  dropZone: document.getElementById("drop-zone"),
  fileInput: document.getElementById("file-input"),
  runSelect: document.getElementById("run-select"),
  themeToggle: document.getElementById("theme-toggle"),
  sourceLabel: document.getElementById("source-label"),
  dashboard: document.getElementById("dashboard"),
  summary: document.getElementById("summary"),
  tabs: document.querySelectorAll(".tabs [role='tab']"),
  errorBadge: document.getElementById("error-badge"),
  search: document.getElementById("search"),
  queryFilter: document.getElementById("query-filter"),
  statusFilter: document.getElementById("status-filter"),
  sort: document.getElementById("sort"),
  resultCount: document.getElementById("result-count"),
  results: document.getElementById("results"),
  pager: document.getElementById("pager"),
  showMore: document.getElementById("show-more"),
  showAll: document.getElementById("show-all"),
  charts: document.getElementById("charts"),
  errorsContent: document.getElementById("errors-content"),
  tooltip: document.getElementById("tooltip"),
};

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function formatCompact(value) {
  const n = Number(value || 0);
  if (Math.abs(n) < 10000) return n.toLocaleString();
  return n.toLocaleString(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  });
}

function formatBytes(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function decodeEntities(value) {
  if (!value || !value.includes("&")) return value;
  return (
    new DOMParser().parseFromString(value, "text/html").documentElement
      .textContent || value
  );
}

function safeHref(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
  } catch {}
  return null;
}

function hostnameOf(url) {
  try {
    return new URL(url).hostname || "(unknown)";
  } catch {
    return "(unknown)";
  }
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showLoadError(message) {
  console.error(message);
  els.dropZone.hidden = false;
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function skipReasonOf(item) {
  const err = item.error || "";
  const httpMatch = err.match(/^HTTP \d+/);
  if (httpMatch) return httpMatch[0];
  if (err.startsWith("Not HTML")) return "Not HTML";
  if (err.startsWith("Blocked domain")) return "Blocked domain";
  if (err.startsWith("No readable text")) return "No readable text";
  if (err.startsWith("Empty response")) return "Empty response";
  if (/timeout|timed out/i.test(err)) return "Timeout";
  if (/connection|refused|reset|max retries/i.test(err))
    return "Connection error";
  return err ? "Other error" : "Unknown";
}

function parsePayload(payload) {
  const rows = Array.isArray(payload) ? payload : payload && payload.results;
  if (!Array.isArray(rows)) {
    throw new Error(
      "Expected a scrape_results JSON file with a 'results' array.",
    );
  }

  const results = rows.filter((row) => row && typeof row === "object");

  for (const item of results) {
    item.title = decodeEntities(item.title);
    item.snippet = decodeEntities(item.snippet);
    item.page_title = decodeEntities(item.page_title);
    item.meta_description = decodeEntities(item.meta_description);
    item._domain = hostnameOf(item.link || item.final_url || "");
    item._reason = item.status === "skipped" ? skipReasonOf(item) : "";
    item._hay = [
      item.title,
      item.page_title,
      item.link,
      item.final_url,
      item.snippet,
      item.meta_description,
      item.query,
      item._domain,
    ]
      .join("\n")
      .toLowerCase();
    item._haytext = (item.text || "").toLowerCase();
  }

  return {
    results,
    summary: Array.isArray(payload) ? null : payload.summary || null,
  };
}

function loadData(payload, sourceName, options = {}) {
  const { results, summary } = parsePayload(payload);

  state.results = results;
  state.summary = summary;
  state.errors = options.errors ?? null;
  state.errorsNote =
    options.errorsNote || "The search-errors file could not be loaded.";
  state.visibleCount = PAGE_SIZE;

  els.sourceLabel.textContent = sourceName;
  els.dropZone.hidden = true;
  els.dashboard.hidden = false;

  renderSummary();
  populateQueryFilter();
  applyFilters();
  renderCharts();
  renderErrors();
}

function loadFromFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      loadData(JSON.parse(reader.result), file.name, {
        errors: null,
        errorsNote:
          "Search errors are available when browsing runs served over HTTP.",
      });
      markManualSource(file.name);
    } catch (err) {
      showLoadError(`Could not read ${file.name}: ${err.message}`);
    }
  };
  reader.onerror = () => showLoadError(`Could not read ${file.name}.`);
  reader.readAsText(file);
}

function runLabel(run) {
  const date = run.generated_at ? new Date(run.generated_at) : null;
  const when =
    date && !Number.isNaN(date.getTime())
      ? date.toLocaleString()
      : run.id || run.dir;
  const total = run.summary?.total_results;
  return total != null ? `${when} - ${formatNumber(total)} results` : when;
}

function populateRunSelect(runs) {
  state.runs = runs;
  els.runSelect.textContent = "";

  runs.forEach((run, index) => {
    const option = el("option", "", runLabel(run));
    option.value = String(index);
    els.runSelect.appendChild(option);
  });

  els.runSelect.hidden = false;
}

function markManualSource(name) {
  if (els.runSelect.hidden) return;

  let option = els.runSelect.querySelector('option[value="manual"]');
  if (!option) {
    option = el("option");
    option.value = "manual";
    els.runSelect.prepend(option);
  }
  option.textContent = `Loaded file: ${name}`;
  els.runSelect.value = "manual";
}

function errorsFileFor(run) {
  if (run.errors) return run.errors;
  if (run.manifest && run.manifest.startsWith("scrape_results_")) {
    return run.manifest.replace("scrape_results_", "search_errors_");
  }
  return null;
}

async function selectRun(run) {
  const base = `${OUTPUT_BASE_URL}${encodeURIComponent(run.dir)}/`;
  const manifestUrl = base + encodeURIComponent(run.manifest);

  els.dashboard.classList.add("loading");
  try {
    const payload = await fetchJson(manifestUrl);

    let errors = null;
    const errorsFile = errorsFileFor(run);
    if (errorsFile) {
      try {
        const parsed = await fetchJson(base + encodeURIComponent(errorsFile));
        if (Array.isArray(parsed)) errors = parsed;
      } catch {}
    }

    loadData(payload, `${run.dir}/${run.manifest}`, { errors });
  } catch (err) {
    showLoadError(`Could not load ${run.dir}/${run.manifest}: ${err.message}`);
  } finally {
    els.dashboard.classList.remove("loading");
  }
}

async function tryAutoLoad() {
  if (!window.location.protocol.startsWith("http")) return;

  try {
    const index = await fetchJson(RUNS_INDEX_URL);
    const runs = (index.runs || []).filter((run) => run.dir && run.manifest);
    if (runs.length) {
      populateRunSelect(runs);
      await selectRun(runs[0]);
    }
  } catch {}
}

function addStat(value, label, detail) {
  const stat = el("div", "stat");
  stat.appendChild(el("div", "stat-value", value));
  if (detail) stat.appendChild(el("div", "stat-detail", detail));
  stat.appendChild(el("div", "stat-label", label));
  els.summary.appendChild(stat);
}

function renderSummary() {
  els.summary.textContent = "";

  const results = state.results;
  const summary = state.summary || {};
  const total = summary.total_results ?? results.length;
  const scraped =
    summary.scraped ?? results.filter((r) => r.status === "scraped").length;
  const skipped =
    summary.skipped ?? results.filter((r) => r.status === "skipped").length;
  const words =
    summary.total_words ??
    results.reduce((sum, r) => sum + (r.word_count || 0), 0);
  const domains = new Set(results.map((r) => r._domain)).size;
  const rate = total ? Math.round((scraped / total) * 100) : 0;

  addStat(formatNumber(total), "Results");
  addStat(formatNumber(scraped), "Scraped", `${rate}% of results`);
  addStat(formatNumber(skipped), "Skipped");
  addStat(formatCompact(words), "Words extracted");
  addStat(formatNumber(domains), "Unique domains");

  if (summary.generated_at) {
    const generated = new Date(summary.generated_at);
    if (!Number.isNaN(generated.getTime())) {
      addStat(
        generated.toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
        }),
        "Generated",
        generated.toLocaleTimeString(undefined, {
          hour: "numeric",
          minute: "2-digit",
        }),
      );
    }
  }
}

function setTab(name) {
  state.tab = name;
  for (const tab of els.tabs) {
    const active = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", String(active));
    const panel = document.getElementById(`panel-${tab.dataset.tab}`);
    if (panel) panel.hidden = !active;
  }
}

function applyTheme(theme) {
  state.theme = THEMES.includes(theme) ? theme : "auto";
  if (state.theme === "auto") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = state.theme;
  }
  const label = state.theme[0].toUpperCase() + state.theme.slice(1);
  els.themeToggle.textContent = `Theme: ${label}`;
  try {
    localStorage.setItem("wcf-theme", state.theme);
  } catch {}
}

function populateQueryFilter() {
  const queries = [
    ...new Set(state.results.map((r) => r.query).filter(Boolean)),
  ];

  els.queryFilter.textContent = "";

  const allOption = el("option", "", "All queries");
  allOption.value = "all";
  els.queryFilter.appendChild(allOption);

  for (const query of queries) {
    const option = el("option", "", query);
    option.value = query;
    els.queryFilter.appendChild(option);
  }

  state.query = "all";
  els.queryFilter.value = "all";
  state.status = "all";
  els.statusFilter.value = "all";
  state.search = "";
  els.search.value = "";
}

function matchesSearch(item, needle) {
  if (item._hay.includes(needle)) return true;
  return needle.length >= 3 && item._haytext.includes(needle);
}

function applyFilters() {
  const needle = state.search.trim().toLowerCase();

  const items = state.results.filter((item) => {
    if (state.query !== "all" && item.query !== state.query) return false;
    if (state.status !== "all" && item.status !== state.status) return false;
    if (needle && !matchesSearch(item, needle)) return false;
    return true;
  });

  if (state.sort === "rank") {
    items.sort((a, b) => (a.search_rank || 0) - (b.search_rank || 0));
  } else if (state.sort === "words") {
    items.sort((a, b) => (b.word_count || 0) - (a.word_count || 0));
  } else if (state.sort === "title") {
    items.sort((a, b) =>
      (a.page_title || a.title || "").localeCompare(
        b.page_title || b.title || "",
      ),
    );
  } else if (state.sort === "domain") {
    items.sort((a, b) => a._domain.localeCompare(b._domain));
  }

  state.filtered = items;
  renderResults();
}

function addMetaBadge(container, text, className) {
  container.appendChild(
    el("span", className ? `badge ${className}` : "", text),
  );
}

function addDetail(dl, term, value) {
  if (value === undefined || value === null || value === "") return;
  dl.appendChild(el("dt", "", term));
  dl.appendChild(el("dd", "", String(value)));
}

function buildDetails(item) {
  const dl = el("dl", "card-details");
  addDetail(dl, "Search page", item.page_number);
  addDetail(dl, "Search rank", item.search_rank);
  addDetail(dl, "Rank on page", item.source_rank);
  addDetail(dl, "HTTP status", item.http_status);
  addDetail(dl, "Content type", item.content_type);
  addDetail(
    dl,
    "Content length",
    item.content_length != null ? formatBytes(item.content_length) : "",
  );
  addDetail(
    dl,
    "Words / characters",
    item.word_count || item.char_count
      ? `${formatNumber(item.word_count)} / ${formatNumber(item.char_count)}`
      : "",
  );
  if (item.fetched_at) {
    const fetched = new Date(item.fetched_at);
    if (!Number.isNaN(fetched.getTime())) {
      addDetail(dl, "Fetched", fetched.toLocaleString());
    }
  }
  if (item.final_url && item.final_url !== item.link) {
    addDetail(dl, "Final URL", item.final_url);
  }
  if (item.sha256) addDetail(dl, "SHA-256", item.sha256.slice(0, 16) + "…");
  addDetail(dl, "Saved as", item.saved_as);
  return dl;
}

function addToggle(actions, body, label, build) {
  const toggle = el("button", "ghost-button", `Show ${label}`);
  toggle.type = "button";
  actions.appendChild(toggle);

  let block = null;
  toggle.addEventListener("click", () => {
    if (!block) {
      block = build();
      body.appendChild(block);
      toggle.textContent = `Hide ${label}`;
    } else {
      block.hidden = !block.hidden;
      toggle.textContent = block.hidden ? `Show ${label}` : `Hide ${label}`;
    }
  });
}

function buildCard(item) {
  const card = el("li", "card");
  card.appendChild(el("div", "card-rank", `#${item.search_rank || "?"}`));

  const body = el("div", "card-body");
  card.appendChild(body);

  const url = item.link || item.final_url || "";
  const href = safeHref(url);
  const titleText = item.page_title || item.title || url || "(untitled)";

  const title = el("h2", "card-title");
  if (href) {
    const link = el("a", "", titleText);
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    title.appendChild(link);
  } else {
    title.textContent = titleText;
  }
  body.appendChild(title);

  if (url) {
    const urlLine = el("p", "card-url", url);
    urlLine.title =
      item.final_url && item.final_url !== url
        ? `Final URL: ${item.final_url}`
        : url;
    body.appendChild(urlLine);
  }

  const description = item.meta_description || item.snippet;
  if (description) body.appendChild(el("p", "card-desc", description));

  const meta = el("div", "card-meta");
  body.appendChild(meta);

  const status = item.status || "unknown";
  addMetaBadge(meta, status.replace("_", " "), `badge-${status}`);
  if (item.query) addMetaBadge(meta, item.query, "badge-query");
  if (url) meta.appendChild(el("span", "", item._domain));
  if (item.status === "scraped") {
    meta.appendChild(el("span", "", `${formatNumber(item.word_count)} words`));
  }
  if (item.http_status) {
    meta.appendChild(el("span", "", `HTTP ${item.http_status}`));
  }
  if (item.error && item.error !== `HTTP ${item.http_status}`) {
    meta.appendChild(el("span", "card-error", item.error));
  }

  const actions = el("div", "card-actions");
  body.appendChild(actions);
  if (item.text) {
    addToggle(actions, body, "extracted text", () =>
      el("pre", "page-text", item.text),
    );
  }
  addToggle(actions, body, "details", () => buildDetails(item));

  return card;
}

function renderResults() {
  const items = state.filtered;
  const visible = items.slice(0, state.visibleCount);
  const filteredNote =
    items.length !== state.results.length
      ? ` matching (${formatNumber(state.results.length)} total)`
      : "";

  els.resultCount.textContent = items.length
    ? `Showing ${formatNumber(visible.length)} of ${formatNumber(
        items.length,
      )} results${filteredNote}`
    : "No results match the current filters.";

  els.results.textContent = "";
  const fragment = document.createDocumentFragment();
  for (const item of visible) {
    fragment.appendChild(buildCard(item));
  }
  els.results.appendChild(fragment);

  els.pager.hidden = visible.length >= items.length;
}

function positionTooltip(x, y) {
  const tip = els.tooltip;
  const rect = tip.getBoundingClientRect();
  let left = x + 14;
  let top = y + 14;
  if (left + rect.width > window.innerWidth - 8) {
    left = Math.max(8, x - rect.width - 14);
  }
  if (top + rect.height > window.innerHeight - 8) {
    top = Math.max(8, y - rect.height - 14);
  }
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function showTooltip(title, rows, x, y) {
  const tip = els.tooltip;
  tip.textContent = "";
  tip.appendChild(el("div", "tooltip-title", title));

  for (const row of rows) {
    const line = el("div", "tooltip-row");
    if (row.color) {
      const key = el("span", "tooltip-key");
      key.style.background = row.color;
      line.appendChild(key);
    }
    line.appendChild(el("span", "tooltip-value", row.value));
    line.appendChild(el("span", "", row.label));
    tip.appendChild(line);
  }

  tip.hidden = false;
  positionTooltip(x, y);
}

function hideTooltip() {
  els.tooltip.hidden = true;
}

function seriesColor(cls) {
  const styles = getComputedStyle(document.documentElement);
  return cls === "seg-accent"
    ? styles.getPropertyValue("--accent").trim()
    : styles.getPropertyValue("--series-muted").trim();
}

function buildBarChart({ title, series, rows, tableOnlyRows = [] }) {
  const card = el("section", "chart-card");
  card.appendChild(el("h3", "chart-title", title));

  if (series.length > 1) {
    const legend = el("div", "chart-legend");
    for (const s of series) {
      const item = el("span", "legend-item");
      const swatch = el("span", `legend-swatch ${s.cls}`);
      swatch.classList.add("bar-seg");
      swatch.style.height = "10px";
      item.appendChild(swatch);
      item.appendChild(el("span", "", s.label));
      legend.appendChild(item);
    }
    card.appendChild(legend);
  }

  const max = Math.max(
    1,
    ...rows.map((row) => row.values.reduce((a, b) => a + b, 0)),
  );

  const rowsBox = el("div", "chart-rows");
  for (const row of rows) {
    const total = row.values.reduce((a, b) => a + b, 0);
    const bar = el("div", "bar-row");
    bar.tabIndex = 0;

    const parts = series
      .map((s, i) => ({ s, value: row.values[i] }))
      .filter((p) => p.value > 0);
    bar.setAttribute(
      "aria-label",
      `${row.label}: ` +
        (parts.length
          ? parts.map((p) => `${formatNumber(p.value)} ${p.s.label}`).join(", ")
          : "0"),
    );

    const label = el("span", "bar-label", row.label);
    label.title = row.label;
    bar.appendChild(label);

    const track = el("div", "bar-track");
    parts.forEach((part, i) => {
      const seg = el("span", `bar-seg ${part.s.cls}`);
      seg.style.flex = `0 1 ${(part.value / max) * 100}%`;
      seg.style.minWidth = "3px";
      if (i === parts.length - 1) seg.classList.add("seg-end");
      track.appendChild(seg);
    });
    bar.appendChild(track);

    bar.appendChild(el("span", "bar-value", formatNumber(total)));

    const tooltipRows = () =>
      series
        .map((s, i) => ({
          value: formatNumber(row.values[i]),
          label: s.label,
          color: seriesColor(s.cls),
        }))
        .concat(
          series.length > 1
            ? [{ value: formatNumber(total), label: "total" }]
            : [],
        );

    bar.addEventListener("pointermove", (event) =>
      showTooltip(row.label, tooltipRows(), event.clientX, event.clientY),
    );
    bar.addEventListener("pointerleave", hideTooltip);
    bar.addEventListener("focus", () => {
      const rect = bar.getBoundingClientRect();
      showTooltip(row.label, tooltipRows(), rect.left, rect.bottom);
    });
    bar.addEventListener("blur", hideTooltip);

    rowsBox.appendChild(bar);
  }
  card.appendChild(rowsBox);

  const details = el("details", "chart-table");
  details.appendChild(el("summary", "", "View as table"));
  const scroll = el("div", "table-scroll");
  const table = el("table", "data-table");
  const thead = el("thead");
  const headRow = el("tr");
  headRow.appendChild(el("th", "", "Category"));
  for (const s of series) headRow.appendChild(el("th", "num", s.label));
  if (series.length > 1) headRow.appendChild(el("th", "num", "Total"));
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = el("tbody");
  for (const row of rows.concat(tableOnlyRows)) {
    const tr = el("tr");
    tr.appendChild(el("td", "", row.label));
    row.values.forEach((v) => tr.appendChild(el("td", "num", formatNumber(v))));
    if (series.length > 1) {
      tr.appendChild(
        el("td", "num", formatNumber(row.values.reduce((a, b) => a + b, 0))),
      );
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  scroll.appendChild(table);
  details.appendChild(scroll);
  card.appendChild(details);

  return card;
}

function buildDomainsChart(domains, series, splitOf, expanded) {
  const shown = expanded ? domains : domains.slice(0, TOP_DOMAINS);
  const rest = expanded ? [] : domains.slice(TOP_DOMAINS);

  const card = buildBarChart({
    title: "Top Domains",
    series,
    rows: shown.map(([domain, items]) => ({
      label: domain,
      values: splitOf(items),
    })),
    tableOnlyRows: rest.length
      ? [
          {
            label: `All other domains (${formatNumber(rest.length)})`,
            values: splitOf(rest.flatMap(([, items]) => items)),
          },
        ]
      : [],
  });

  if (domains.length > TOP_DOMAINS) {
    const footer = el("div", "chart-footer");
    const toggle = el(
      "button",
      "ghost-button",
      expanded
        ? `See top ${TOP_DOMAINS}`
        : `See all ${formatNumber(domains.length)} domains`,
    );
    toggle.type = "button";
    toggle.addEventListener("click", () => {
      card.replaceWith(buildDomainsChart(domains, series, splitOf, !expanded));
    });
    footer.appendChild(toggle);
    card.insertBefore(footer, card.querySelector("details.chart-table"));
  }

  return card;
}

function renderCharts() {
  els.charts.textContent = "";
  const results = state.results;

  if (!results.length) {
    els.charts.appendChild(
      el("p", "empty-state", "This run has no results to chart."),
    );
    return;
  }

  const anyNotScraped = results.some((r) => r.status === "not_scraped");
  const outcomeSeries = [
    { key: "scraped", label: "Scraped", cls: "seg-accent" },
    {
      key: "rest",
      label: anyNotScraped ? "Not scraped" : "Skipped",
      cls: "seg-muted",
    },
  ];
  const splitOf = (items) => {
    const scraped = items.filter((r) => r.status === "scraped").length;
    return [scraped, items.length - scraped];
  };

  const queries = [...new Set(results.map((r) => r.query).filter(Boolean))];
  if (queries.length > 1) {
    els.charts.appendChild(
      buildBarChart({
        title: "Outcome By Query",
        series: outcomeSeries,
        rows: queries.map((query) => ({
          label: query,
          values: splitOf(results.filter((r) => r.query === query)),
        })),
      }),
    );
  }

  const byDomain = new Map();
  for (const item of results) {
    if (!byDomain.has(item._domain)) byDomain.set(item._domain, []);
    byDomain.get(item._domain).push(item);
  }
  const domains = [...byDomain.entries()].sort(
    (a, b) => b[1].length - a[1].length,
  );
  els.charts.appendChild(
    buildDomainsChart(domains, outcomeSeries, splitOf, false),
  );

  const skipped = results.filter((r) => r.status === "skipped");
  if (skipped.length) {
    const byReason = new Map();
    for (const item of skipped) {
      byReason.set(item._reason, (byReason.get(item._reason) || 0) + 1);
    }
    const reasons = [...byReason.entries()].sort((a, b) => b[1] - a[1]);
    els.charts.appendChild(
      buildBarChart({
        title: "Skip Reasons",
        series: [{ key: "count", label: "Skipped results", cls: "seg-muted" }],
        rows: reasons.map(([reason, count]) => ({
          label: reason,
          values: [count],
        })),
      }),
    );
  }
}

function renderErrors() {
  const box = els.errorsContent;
  box.textContent = "";

  const errors = state.errors;
  const count = errors
    ? errors.length
    : (state.summary?.search_error_count ?? 0);
  els.errorBadge.textContent = formatNumber(count);
  els.errorBadge.classList.toggle("has-errors", count > 0);

  if (errors === null) {
    box.appendChild(el("p", "empty-state", state.errorsNote));
    return;
  }

  if (!errors.length) {
    box.appendChild(
      el(
        "p",
        "empty-state",
        "No search errors; every Google CSE request succeeded.",
      ),
    );
    return;
  }

  const scroll = el("div", "table-scroll");
  const table = el("table", "data-table");
  const thead = el("thead");
  const headRow = el("tr");
  for (const heading of ["Query", "Page", "Type", "HTTP", "Message"]) {
    headRow.appendChild(
      el("th", heading === "Page" || heading === "HTTP" ? "num" : "", heading),
    );
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const err of errors) {
    if (!err || typeof err !== "object") continue;
    const tr = el("tr");
    tr.appendChild(el("td", "", err.query ?? ""));
    tr.appendChild(el("td", "num", err.page_number ?? ""));
    tr.appendChild(el("td", "", err.error_type ?? ""));
    tr.appendChild(el("td", "num", err.http_status ?? ""));
    tr.appendChild(el("td", "", err.message ?? ""));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  scroll.appendChild(table);
  box.appendChild(scroll);
}

function init() {
  const params = new URLSearchParams(window.location.search);

  let theme = params.get("theme");
  if (!THEMES.includes(theme)) {
    try {
      theme = localStorage.getItem("wcf-theme");
    } catch {
      theme = null;
    }
  }
  applyTheme(theme || "auto");

  els.themeToggle.addEventListener("click", () => {
    const next = THEMES[(THEMES.indexOf(state.theme) + 1) % THEMES.length];
    applyTheme(next);
  });

  for (const tab of els.tabs) {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
  }
  const requestedTab = params.get("tab");
  if (["results", "overview", "errors"].includes(requestedTab)) {
    setTab(requestedTab);
  }

  els.runSelect.addEventListener("change", () => {
    const run = state.runs[Number(els.runSelect.value)];
    if (run) selectRun(run);
  });

  els.fileInput.addEventListener("change", () => {
    if (els.fileInput.files.length) {
      loadFromFile(els.fileInput.files[0]);
      els.fileInput.value = "";
    }
  });

  for (const eventName of ["dragenter", "dragover"]) {
    document.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropZone.classList.add("drag-over");
    });
  }
  document.addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) els.dropZone.classList.remove("drag-over");
  });
  document.addEventListener("drop", (event) => {
    event.preventDefault();
    els.dropZone.classList.remove("drag-over");
    if (event.dataTransfer.files.length) {
      loadFromFile(event.dataTransfer.files[0]);
    }
  });

  els.search.addEventListener(
    "input",
    debounce(() => {
      state.search = els.search.value;
      state.visibleCount = PAGE_SIZE;
      applyFilters();
    }, 150),
  );

  for (const [element, key] of [
    [els.queryFilter, "query"],
    [els.statusFilter, "status"],
    [els.sort, "sort"],
  ]) {
    element.addEventListener("change", () => {
      state[key] = element.value;
      state.visibleCount = PAGE_SIZE;
      applyFilters();
    });
  }

  els.showMore.addEventListener("click", () => {
    state.visibleCount += PAGE_SIZE;
    renderResults();
  });
  els.showAll.addEventListener("click", () => {
    state.visibleCount = state.filtered.length;
    renderResults();
  });

  tryAutoLoad();
}

init();
