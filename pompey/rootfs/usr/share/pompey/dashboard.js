/* Read-only observations, native release inspection and managed preferences. */
(() => {
  const element = id => document.getElementById(id);
  const text = (tag, value, className = "") => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  const labels = {radarr: "Radarr", sonarr: "Sonarr", prowlarr: "Prowlarr", qbittorrent: "qBittorrent", seerr: "Seerr"};
  const age = updated => {
    if (!updated) return "Never checked";
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - updated));
    return seconds < 60 ? seconds + "s ago" : Math.floor(seconds / 60) + "m ago";
  };
  let accessHostRecorded = false;
  let accessHostBusy = false;
  async function rememberAccessHost() {
    if (accessHostRecorded || accessHostBusy || location.protocol === "file:") return;
    accessHostBusy = true;
    try {
      await diagnosticRequest("/access-host", {hostname: location.hostname});
      accessHostRecorded = true;
    } catch (_) { /* The next overview refresh retries a busy maintenance lock. */ }
    finally { accessHostBusy = false; }
  }

  // A report survives closing the dialog and failed subsequent collections.
  const reportDialog = element("report-dialog");
  const reportText = element("report-text");
  const reportStatus = element("report-status");
  let reportBusy = false;
  let reportOpener = null;
  function openReport(opener) {
    reportOpener = opener;
    reportDialog.showModal();
    element("report-title").focus();
  }
  reportDialog.addEventListener("close", () => reportOpener?.focus({preventScroll: true}));
  for (const id of ["close-report", "dismiss-report"]) element(id).addEventListener("click", () => reportDialog.close());
  async function collectReport(opener) {
    if (reportBusy) return;
    reportBusy = true;
    if (!reportDialog.open) openReport(opener);
    element("collect-report").disabled = true;
    element("recollect-report").hidden = true;
    reportDialog.setAttribute("aria-busy", "true");
    reportStatus.classList.remove("check-warning");
    reportStatus.textContent = "Collecting release sequences…";
    try {
      const response = await timedFetch(ingressBase() + "diagnostics/report", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: "{}", cache: "no-store"
      }, 65000);
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || "Report collection failed");
      }
      reportText.value = await response.text();
      reportText.hidden = false;
      reportStatus.textContent = "Report ready · " + new Date().toLocaleTimeString();
    } catch (error) {
      reportStatus.textContent = error.message + (reportText.value ? " The previous report is still available." : " Try collecting again.");
      reportStatus.classList.add("check-warning");
    } finally {
      reportBusy = false;
      reportDialog.removeAttribute("aria-busy");
      element("collect-report").disabled = false;
      element("recollect-report").hidden = false;
      element("copy-report").disabled = element("save-report").disabled = !reportText.value;
    }
  }
  element("collect-report").addEventListener("click", event => collectReport(event.currentTarget));
  element("recollect-report").addEventListener("click", () => collectReport(reportOpener));
  element("copy-report").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(reportText.value);
      reportStatus.classList.remove("check-warning");
      reportStatus.textContent = "Report copied.";
    } catch (_) {
      reportText.focus(); reportText.select();
      reportStatus.textContent = "Report selected. Use your device’s Copy command.";
    }
  });
  element("save-report").addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([reportText.value], {type: "text/plain;charset=utf-8"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = "pompey-diagnostics-" + new Date().toISOString().replace(/[:.]/g, "-") + ".txt";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  const recoveryDialog = element("recovery-dialog");
  let recoveryTarget = null;
  let recoveryItem = null;
  let recoveryOpener = null;
  let recoveryBusy = false;
  let recoveryGeneration = 0;
  let recoveryDownloadId = null;
  function closeRecovery() { if (!recoveryBusy) recoveryDialog.close(); }
  for (const id of ["close-recovery", "cancel-recovery"]) element(id).addEventListener("click", closeRecovery);
  recoveryDialog.addEventListener("cancel", event => { if (recoveryBusy) event.preventDefault(); });
  recoveryDialog.addEventListener("close", () => { recoveryGeneration++; recoveryOpener?.focus({preventScroll:true}); });
  async function prepareRecovery(downloadId) {
    const generation = ++recoveryGeneration;
    const item = recoveryItem;
    recoveryDownloadId = downloadId;
    recoveryTarget = null;
    element("confirm-recovery").disabled = true;
    element("refresh-recovery").hidden = true;
    element("recovery-target").replaceChildren();
    element("recovery-status").textContent = "Checking this release and its files…";
    try {
      const plan = await diagnosticRequest("/recovery/preview", {kind:item.kind, id:item.id, downloadId});
      if (generation !== recoveryGeneration || !recoveryDialog.open) return;
      recoveryTarget = plan;
      const container = element("recovery-target");
      container.append(text("strong", plan.release));
      for (const [label, files, field] of [["Download files",plan.downloadFiles,"name"],["Library files",plan.libraryFiles,"path"]]) {
        if (!files.length) continue;
        container.append(text("h3",label + " · " + files.length));
        const list = document.createElement("ul");
        for (const file of files) list.append(text("li",file[field]));
        container.append(list);
      }
      if (!plan.downloadFiles.length && !plan.libraryFiles.length) container.append(text("p","No files are currently linked to this release in Arr. Files you moved elsewhere will be kept. This will blocklist the recorded release and search again."));
      element("recovery-status").textContent = "Review the affected files before continuing.";
      element("confirm-recovery").disabled = false;
    } catch (error) {
      if (generation !== recoveryGeneration || !recoveryDialog.open) return;
      element("recovery-status").textContent = error.message;
      element("refresh-recovery").hidden = false;
    }
  }
  async function openRecovery(item, opener) {
    const generation = ++recoveryGeneration;
    recoveryItem = item; recoveryOpener = opener; recoveryTarget = null; recoveryDownloadId = null;
    element("recovery-target").replaceChildren();
    element("recovery-choice").hidden = element("recovery-choice-label").hidden = true;
    element("recovery-note").value = "";
    element("recovery-reason").value = item.phase === "Waiting for peers" ? "stalled" : "other";
    element("confirm-recovery").disabled = true;
    element("refresh-recovery").hidden = true;
    recoveryDialog.showModal(); element("recovery-title").focus();
    if (item.downloadId) return prepareRecovery(item.downloadId);
    element("recovery-status").textContent = "Finding the imported releases…";
    try {
      const data = await diagnosticRequest("/recovery/releases", {kind:item.kind,id:item.id});
      if (generation !== recoveryGeneration || !recoveryDialog.open) return;
      if (!data.choices.length) throw new Error("No current library files have identifiable release history. Use the advanced application interface to review them.");
      if (data.choices.length === 1) return prepareRecovery(data.choices[0].downloadId);
      const select = element("recovery-choice");
      const placeholder = text("option","Choose a release…"); placeholder.value = ""; select.replaceChildren(placeholder);
      for (const choice of data.choices) {
        const option = text("option",choice.release + " · " + choice.files.length + " file(s)"); option.value = choice.downloadId; select.append(option);
      }
      select.hidden = element("recovery-choice-label").hidden = false;
      element("recovery-status").textContent = "Choose the release to replace. A season pack can contain several episodes.";
    } catch (error) { if (generation === recoveryGeneration && recoveryDialog.open) element("recovery-status").textContent = error.message; }
  }
  element("recovery-choice").addEventListener("change", event => {
    if(event.target.value) return prepareRecovery(event.target.value);
    recoveryGeneration++; recoveryTarget = null; recoveryDownloadId = null;
    element("confirm-recovery").disabled = true; element("recovery-target").replaceChildren();
    element("recovery-status").textContent = "Choose the release to replace.";
  });
  element("refresh-recovery").addEventListener("click", () => prepareRecovery(recoveryDownloadId || recoveryItem.downloadId || element("recovery-choice").value));
  element("confirm-recovery").addEventListener("click", async () => {
    if (!recoveryTarget || recoveryBusy) return;
    recoveryBusy = true;
    element("confirm-recovery").disabled = true;
    element("cancel-recovery").disabled = element("close-recovery").disabled = element("recovery-choice").disabled = true;
    element("recovery-status").textContent = "Saving evidence, removing this release and asking Arr for a replacement…";
    try {
      const result = await diagnosticRequest("/recovery/reject", {kind:recoveryTarget.kind,id:recoveryTarget.id,
        downloadId:recoveryTarget.downloadId, token:recoveryTarget.token, confirmationId:recoveryTarget.confirmationId,
        confirm:true,reason:element("recovery-reason").value,note:element("recovery-note").value});
      recoveryDialog.close();
      await refreshRequests();
      element("request-status").textContent = result.message;
    } catch (error) {
      element("recovery-status").textContent = error.message;
      element("refresh-recovery").hidden = false;
    } finally {
      recoveryBusy = false;
      element("cancel-recovery").disabled = element("close-recovery").disabled = element("recovery-choice").disabled = false;
    }
  });
  function renderSequences() {
    const sequences = requestData?.sequences || [];
    element("sequence-history").hidden = !sequences.length;
    const container = element("sequence-list");
    // Preserve an open history disclosure during routine refreshes.
    const open = new Set([...container.querySelectorAll("details[open]")].map(n=>n.dataset.sequence));
    container.replaceChildren();
    for (const sequence of sequences) {
      const detail = document.createElement("details"); detail.dataset.sequence = sequence.sequenceId;
      detail.open = open.has(sequence.sequenceId);
      const summary = text("summary",sequence.title + (sequence.scope ? " · " + sequence.scope : "") + " · " + sequence.attempts.length + (sequence.attempts.length === 1 ? " attempt · " : " attempts · ") + sequence.status);
      if (sequence.kind === "sonarr" && !sequence.scope) summary.append(text("small",sequence.attempts.at(-1)?.release || "Episode details unavailable", "scope"));
      detail.append(summary);
      const list = document.createElement("ol");
      const events = sequence.attempts.map(attempt => ({at:attempt.at, label:(attempt.stage === "retry-requested" ? "Rejected: " : "Rejection requested: ") + attempt.release + " — " + attempt.reason.replaceAll("_"," ") + (attempt.note ? ": " + attempt.note : "") + (attempt.error ? " · " + attempt.error : "")}));
      const labels = {grabbed:"Selected",downloadFolderImported:"Imported"};
      const seen = new Set();
      for (const outcome of sequence.outcomes || []) {
        const label = labels[outcome.eventType];
        if (!label) continue;
        const key = outcome.eventType + ":" + outcome.downloadId;
        if(seen.has(key)) continue; seen.add(key);
        events.push({at:Date.parse(outcome.date)/1000,label:label + ": " + outcome.sourceTitle});
      }
      events.sort((a,b)=>a.at-b.at);
      for (const event of events) list.append(text("li",event.label));
      detail.append(list);
      if (sequence.status === "Needs attention") {
        const button = text("button","Try another","danger-button");
        button.addEventListener("click",()=>openRecovery({...sequence,downloadId:sequence.attempts.at(-1).downloadId},button));
        detail.append(button);
      }
      if (sequence.collectionError) detail.append(text("p","Current outcome unavailable. " + sequence.collectionError,"field-help"));
      container.append(detail);
    }
  }

  let overview = null;
  let overviewBusy = false;
  let overviewFailed = false;
  let health = null;
  const tabs = [...element("dashboard-tabs").querySelectorAll('[role="tab"]')];
  let activePanel = "overview-panel";
  function selectPanel(id) {
    const changed = activePanel !== id;
    activePanel = id;
    for (const tab of tabs) {
      const selected = tab.getAttribute("aria-controls") === id;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      element(tab.getAttribute("aria-controls")).hidden = !selected;
    }
    if (changed && id === "diagnostic-panel") refreshRequests();
    if (id === "config-panel") refreshPreferences();
  }
  function openPanel(id) {
    selectPanel(id);
    tabs.find(tab => tab.getAttribute("aria-controls") === id).focus({preventScroll: true});
  }
  function showAttention() {
    attentionOnly = true; importedOnly = false;
    setFilters();
  }
  function metric(title, value, detail, source, action = null, warning = false) {
    const node = document.createElement(action ? "button" : "div");
    node.className = "metric" + (warning ? " warning" : "");
    const stale = overviewFailed || !source?.available || Date.now() / 1000 - (source.updated || 0) > 45;
    if (stale) node.classList.add("stale");
    node.append(text("span", title), text("strong", value));
    if (detail) node.append(text("small", detail));
    if (stale) node.append(text("small", source?.updated ? "Last known · " + age(source.updated) : "Unavailable"));
    if (action) node.addEventListener("click", action);
    element("ecosystem-counts").append(node);
    return node;
  }
  function renderOverview() {
    const data = overview.summary;
    element("ecosystem").hidden = false;
    element("ecosystem-counts").replaceChildren();
    const torrents = data.torrents.value;
    metric("Downloads", torrents ? torrents.downloading + " downloading" : "—",
      torrents ? [torrents.stalled && torrents.stalled + " waiting for peers", torrents.seeding && torrents.seeding + " seeding", torrents.paused && torrents.paused + " stopped", torrents.checking && torrents.checking + " checking", torrents.other && torrents.other + " other"].filter(Boolean).join(" · ") : "",
      data.torrents, () => { attentionOnly = false; setFilters(); openPanel("diagnostic-panel"); });
    metric("Monitored", (data.shows.value ?? "—") + " shows · " + (data.movies.value ?? "—") + " movies", "",
      {available: data.shows.available && data.movies.available, updated: Math.min(data.shows.updated || 0, data.movies.updated || 0)});
    const indexers = data.indexers.value;
    const issues = (overview.setup.indexerIssues || []).length;
    const sourceMetric = metric("Indexers", indexers ? indexers.healthy + " of " + indexers.enabled + " available" : "—", "", data.indexers,
      issues || indexers?.enabled === 0 ? () => {
        const target = element("setup-panel");
        target.scrollIntoView({block: "nearest"}); target.tabIndex = -1; target.focus({preventScroll: true});
      } : null, indexers && indexers.healthy < indexers.enabled);
    if (issues) sourceMetric.append(text("small", "View " + issues + (issues === 1 ? " issue" : " issues"), "metric-action"));
    if (indexers?.enabled === 0) sourceMetric.append(text("small", "Add an indexer", "metric-action"));
    const requests = data.requests.value;
    metric("Requests", requests ? requests.open + " open" : "—", requests ? [requests.approval && requests.approval + " awaiting approval", requests.processing && requests.processing + " in progress", requests.failed && requests.failed + " failed"].filter(Boolean).join(" · ") : "", data.requests,
      () => { openSearchBtn.click(); }, requests?.failed > 0);
    element("overview-note").hidden = !overviewFailed;
    element("overview-note").textContent = "Overview unavailable; showing last known counts.";
    renderConnections();
    renderVersions();
    const storage = overview.storage || {};
    window.pompeyStorageBlocked = storage.ok === false;
    element("storage-alert").hidden = storage.ok !== false;
    element("storage-reason").textContent = (storage.reason || "Storage could not be checked.") + (storage.path ? " " + storage.path : "");
    element("storage-action").textContent = storage.phase === "paused"
      ? "Downloads and imports are paused. Restore the share or free space; access is checked automatically."
      : storage.phase === "attention" ? "Application shutdown could not be confirmed. Review the Home Assistant log."
      : "Stopping affected applications while storage is unavailable.";
  }
  function renderConnections() {
    const table = element("setup-checks"); table.replaceChildren();
    const row = (name, message) => {
      const tr = document.createElement("tr"); tr.append(text("td", name), text("td", message)); table.append(tr);
    };
    const setup = overview.setup;
    if (!setup.seerrAvailable) row("Seerr", "Connection unavailable");
    else if (!setup.initialized) row("Seerr", "Open Seerr to finish setup");
    if (setup.initialized && setup.seerrAvailable) for (const name of ["radarr", "sonarr"]) {
      if (!setup.requestConnections?.[name]) row("Seerr → " + labels[name], "Check this connection in Seerr");
    }
    for (const name of ["radarr", "sonarr"]) {
      if (!setup.sources[name]) row(labels[name], setup.sources[name] === null ? "Connection unavailable" : "No synchronized indexers");
    }
    for (const source of setup.indexerIssues || []) row(source.name, "Retry after " + new Date(source.retryAfter).toLocaleTimeString());
    if (overview.summary.indexers.value?.enabled === 0) row("Indexers", "Add a source in Manage indexers");
    const server = setup.mediaServer;
    if (server?.configured && server.reachable === false) row(server.type === 1 ? "Plex" : server.type === 3 ? "Emby" : "Jellyfin", "Library discovery unavailable; check the connection in Seerr");
    element("setup-panel").hidden = !table.children.length;
    element("setup-title").textContent = setup.seerrAvailable && !setup.initialized ? "Finish setup" : "Connections need attention";
    element("setup-note").textContent = setup.indexerIssues?.length && table.children.length === setup.indexerIssues.length
      ? "Prowlarr retries these indexers automatically."
      : "Check server connections in Seerr and sources in Manage indexers.";
  }
  function renderVersions() {
    element("bundle-version").textContent = "Pompey " + overview.versions.pompey;
    const versions = element("application-versions"); versions.replaceChildren();
    for (const [name, version] of Object.entries(overview.versions.applications)) {
      const service = name === "qbittorrent-nox" ? "qbittorrent" : name.toLowerCase();
      if (!labels[service]) continue;
      const down = health?.services?.[service] === false;
      const item = document.createElement("div");
      item.append(text("strong", labels[service], down ? "service-down" : ""), text("small", version.replace(/^release-/, "").split("_v")[0] + (down ? " · unavailable" : "")));
      versions.append(item);
    }
  }

  let preferencesLoaded = false;
  let preferencesDirty = false;
  let preferencesBusy = false;
  const preferencesForm = element("preferences");
  const preferencesResult = element("preferences-result");
  const preferenceFields = [...preferencesForm.querySelectorAll("input, select")];
  preferenceFields.forEach(field => { field.disabled = true; });
  function seedingHelp() {
    element("seeding-help").textContent = element("seeding-policy").value === "stop_sharing"
      ? "Download files are removed after a successful import."
      : "Completed download files remain until import and the seeding target are complete.";
  }
  function preferenceMessage(pending) {
    preferencesResult.classList.remove("check-warning");
    preferencesResult.textContent = pending ? "Saved · Applying in the background…" : "Saved";
  }
  async function refreshPreferences() {
    if (preferencesBusy || preferencesDirty) return;
    preferencesBusy = true;
    try {
      const data = await diagnosticRequest("/preferences");
      if (!preferencesDirty) {
        element("seeding-policy").value = data.values.after_download;
        element("download-slots").value = data.values.simultaneous_downloads;
        element("dual-audio").checked = data.values.prefer_dual_audio;
        preferenceMessage(data.pending); seedingHelp();
      }
      preferencesLoaded = true;
      preferenceFields.forEach(field => { field.disabled = false; });
    } catch (error) {
      preferencesResult.textContent = error.message;
      preferencesResult.classList.add("check-warning");
    } finally {
      preferencesBusy = false;
      element("save-preferences").disabled = !preferencesLoaded || !preferencesDirty;
    }
  }
  preferencesForm.addEventListener("input", () => {
    preferencesDirty = true; seedingHelp();
    element("save-preferences").disabled = !preferencesLoaded || preferencesBusy;
    preferencesResult.classList.remove("check-warning"); preferencesResult.textContent = "Unsaved changes";
  });
  preferencesForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!preferencesLoaded || preferencesBusy) return;
    preferencesBusy = true;
    const values = {after_download: element("seeding-policy").value, simultaneous_downloads: Number(element("download-slots").value), prefer_dual_audio: element("dual-audio").checked};
    element("save-preferences").disabled = true;
    preferenceFields.forEach(field => { field.disabled = true; });
    preferencesResult.textContent = "Saving…";
    try {
      const data = await diagnosticRequest("/preferences", values);
      preferencesDirty = false; preferenceMessage(data.pending);
    } catch (error) {
      preferencesResult.textContent = error.message; preferencesResult.classList.add("check-warning");
    } finally {
      preferenceFields.forEach(field => { field.disabled = false; });
      preferencesBusy = false;
      element("save-preferences").disabled = !preferencesDirty;
    }
  });

  function nativeLink(service, container) {
    if (!debugConsoles.hidden && ["radarr", "sonarr", "qbittorrent"].includes(service)) {
      const link = text("a", "Open " + labels[service] + " ↗", "button");
      link.href = ingressBase() + "debug/" + service + "/";
      link.target = "_blank"; link.rel = "noopener"; container.append(link);
    }
  }
  const releaseResults = new Map();
  const releaseBusy = new Set();
  function releaseKey(item) { return item.kind + ":" + item.id; }
  function renderReleases(container, record) {
    container.replaceChildren();
    if (!record) return;
    if (record.error) { container.append(text("p", record.error, "check-warning")); return; }
    container.append(text("p", "Search completed " + new Date(record.updated * 1000).toLocaleTimeString() + " · Inspection does not start a download."));
    if (!record.releases?.length) { container.append(text("p", "No releases returned by the configured indexers.")); return; }
    const table = document.createElement("table"); table.className = "release-table"; table.setAttribute("aria-label", "Release search results");
    const head = document.createElement("thead"); const headings = document.createElement("tr");
    for (const label of ["Release", "Seeds", "Result"]) { const th = text("th", label); th.scope = "col"; headings.append(th); }
    head.append(headings); table.append(head);
    const body = document.createElement("tbody"); body.setAttribute("role", "rowgroup"); table.append(body);
    for (const release of record.releases) {
      const tr = document.createElement("tr");
      const reason = release.rejections?.join("; ") || (release.approved === true ? "Accepted by Arr" : "No rejection reason reported");
      tr.append(text("td", release.title), text("td", release.reportedSeeders ?? "—"), text("td", reason)); body.append(tr);
    }
    container.append(table);
  }
  const components = new Map();
  let nextRowId = 0;
  let selectedWorkflow = null;
  let requestData = null;
  let requestsBusy = false;
  let attentionOnly = false;
  let importedOnly = false;
  let requestItems = new Map();
  let requestOrder = [];
  const table = document.createElement("table"); table.setAttribute("role", "table"); table.className = "workflow-table"; table.setAttribute("aria-label", "Release progress");
  const head = document.createElement("thead"); const headings = document.createElement("tr");
  for (const label of ["Title", "Status"]) { const th = text("th", label); th.scope = "col"; headings.append(th); }
  head.append(headings); table.append(head);
  const body = document.createElement("tbody"); body.setAttribute("role", "rowgroup"); table.append(body);
  const empty = text("p", "", "empty-state");
  diagnosis.append(table, empty);
  function phaseClass(item) {
    if (!item.available) return "stale";
    if (item.messages?.length || ["Download error", "Download stopped", "Import needs attention", "Unmatched download", "Request failed"].includes(item.phase)) return "warning";
    if (item.phase === "Downloading") return "active";
    if (/Waiting|Missing|Retrieving|Not available|No releases|All releases/.test(item.phase)) return "waiting";
    return "";
  }
  function requestKey(item) { return item.key || [item.kind, item.id, item.downloadId || item.release || item.title].join(":"); }
  function isAttention(item) { return item.attention || !item.available; }
  function makeComponent(key) {
    const row = document.createElement("tr"); row.setAttribute("role", "row"); row.className = "workflow-row";
    const title = document.createElement("td"); title.setAttribute("role", "cell");
    const button = text("button", "", "title-button"); button.type = "button";
    const scope = text("small", "", "scope"); title.append(button, scope);
    const status = document.createElement("td"); status.setAttribute("role", "cell"); row.append(title, status);
    const detailRow = document.createElement("tr"); detailRow.setAttribute("role", "row"); detailRow.className = "detail-row";
    const cell = document.createElement("td"); cell.setAttribute("role", "cell"); cell.colSpan = 2; cell.className = "detail-cell"; detailRow.append(cell);
    const detail = document.createElement("section"); detail.className = "workflow-detail";
    detail.id = "workflow-" + (components.size + 1) + "-" + String(++nextRowId);
    button.setAttribute("aria-controls", detail.id);
    const evidence = document.createElement("div");
    const actions = document.createElement("div"); actions.className = "actions";
    const results = document.createElement("div"); results.className = "release-results"; results.setAttribute("aria-live", "polite");
    const technical = document.createElement("details"); technical.className = "technical-details";
    const summary = text("summary", "Technical details");
    const fields = document.createElement("dl"); fields.className = "evidence-list"; technical.append(summary, fields);
    const primaryActions = document.createElement("div"); primaryActions.className = "actions";
    technical.append(actions, results);
    detail.append(evidence, primaryActions, technical); cell.append(detail);
    const component = {key, row, button, scope, status, detailRow, evidence, actions, primaryActions, results, fields, actionSignature: null};
    row.addEventListener("click", event => {
      if (event.target.closest("a")) return;
      selectedWorkflow = selectedWorkflow === key ? null : key;
      renderRequests();
    });
    return component;
  }
  function detailEvidence(component, item) {
    const {evidence, fields} = component;
    evidence.replaceChildren(); fields.replaceChildren();
    const addField = (name, value) => {
      if (value !== undefined && value !== null && value !== "") fields.append(text("dt", name), text("dd", value));
    };
    const download = item.download;
    if (item.library && item.delivered) evidence.append(text("p", "Successfully delivered to the library. Later moves or deletion do not restart automatic downloads. Use Try another to request a replacement."));
    if (item.library && item.delivered && !item.hasFile) evidence.append(text("p", "Arr no longer reports a file at its old location. Files moved outside Arr are retained when requesting a replacement.", "field-help"));
    const importing = ["Import needs attention", "Waiting for import", "Imported to library", "Imported"].includes(item.phase);
    if (download && item.available && Number.isFinite(download.progress) && !importing) {
      const progress = document.createElement("div"); progress.className = "transfer-progress";
      const meter = document.createElement("progress"); meter.max = 1; meter.value = download.progress; meter.setAttribute("aria-label", "Download progress");
      progress.append(meter, text("span", Math.round(download.progress * 100) + "%")); evidence.append(progress);
    }
    if (!item.available) evidence.append(text("p", "Current progress is unavailable. Last known: " + (item.lastKnownPhase || "unknown") + ". Restore the application connection before taking action.", "check-warning"));
    else if (item.messages?.length) {
      for (const message of [...new Set(item.messages)]) evidence.append(text("p", message, "check-warning"));
      if (item.phase === "Import needs attention") evidence.append(text("p", "The application retries import after the problem is resolved."));
    } else if (["stalledDL", "metaDL", "forcedMetaDL"].includes(download?.state)) {
      const noConnections = download.connectedSeeds === 0 && download.connectedPeers === 0;
      evidence.append(text("p", noConnections ? "No seeds or peers connected." : "Peers are connected, but no data is arriving."));
    } else if (["stoppedDL", "pausedDL"].includes(download?.state)) evidence.append(text("p", "Download stopped. Check skipped files and downloader errors before resuming."));
    else if (item.phase === "Downloading" && download) {
      const parts = [];
      if (download.speed > 0) parts.push(fmtBytes(download.speed) + "/s");
      if (download.eta > 0 && download.eta < 8640000) parts.push("About " + Math.max(1, Math.round(download.eta / 60)) + " min remaining");
      if (parts.length) evidence.append(text("p", parts.join(" · ")));
    } else if (item.nextAction && !item.library) evidence.append(text("p", item.nextAction));
    if (item.canCheckLibrary && item.path) evidence.append(text("p", "Expected library folder: " + item.path, "library-path"));
    if (debugConsoles.hidden && ["stoppedDL", "pausedDL", "error", "missingFiles"].includes(download?.state)) evidence.append(text("p", "Enable advanced interfaces in Home Assistant’s app settings to inspect this download."));
    addField("Application", labels[item.kind] || item.kind);
    addField(item.available ? "Observed" : "Last observed", item.updated ? new Date(item.updated * 1000).toLocaleString() : "Unavailable");
    addField("Release", item.release);
    addField("Delivered to", item.deliveryPath);
    addField("Download ID", item.downloadId);
    if (download) {
      addField("Downloader state", download.state);
      addField("Connected seeds", download.connectedSeeds);
      addField("Connected peers", download.connectedPeers);
      addField("Reported seeds", download.reportedSeeds);
      addField("Availability", download.availability);
      if (Number.isFinite(download.remainingBytes)) addField("Remaining", fmtBytes(download.remainingBytes));
      addField("Category", download.category || "Uncategorized");
      addField("Download location", download.path);
      if (Number.isFinite(download.size)) addField("Torrent size", fmtBytes(download.size));
      if (Number.isFinite(download.ratio)) addField("Upload ratio", download.ratio.toFixed(2));
      if (Number.isFinite(download.seedingSeconds)) addField("Time seeding", Math.floor(download.seedingSeconds / 3600) + "h " + Math.floor(download.seedingSeconds % 3600 / 60) + "m");
      if (download.completed > 0) addField("Download completed", new Date(download.completed * 1000).toLocaleString());
    }
    const canSearch = item.available && ["radarr", "sonarr"].includes(item.kind) && item.id && !importing;
    const signature = JSON.stringify([item.available, canSearch, item.kind, item.id, item.library, item.downloadId, item.fileId, !!download, item.canCheckLibrary, debugConsoles.hidden]);
    if (signature !== component.actionSignature) {
      component.actionSignature = signature; component.actions.replaceChildren(); component.primaryActions.replaceChildren(); component.searchButton = null;
      if(item.available && ["radarr","sonarr"].includes(item.kind) && item.id && (item.downloadId || item.library)) {
        const retry = text("button","Try another","danger-button");
        retry.addEventListener("click",()=>openRecovery(component.item,retry));
        component.primaryActions.append(retry);
      }
      if (item.available && item.canCheckLibrary) {
        const scan = text("button", "Check library");
        scan.addEventListener("click", async () => {
          scan.disabled = true;
          try {
            const result = await diagnosticRequest("/check-library", {kind:item.kind,id:item.id});
            component.notice = result.message;
          } catch (error) { component.notice = error.message; }
          finally { scan.disabled = false; detailEvidence(component, component.item); }
        });
        component.primaryActions.append(scan);
      }
      if (canSearch) {
        const inspect = text("button", "Inspect releases");
        component.searchButton = inspect;
        inspect.disabled = releaseBusy.has(releaseKey(item));
        inspect.addEventListener("click", async () => {
          const token = releaseKey(item); releaseBusy.add(token); inspect.disabled = true;
          component.results.replaceChildren(text("p", "Searching indexers…"));
          try { releaseResults.set(token, await diagnosticRequest("/search", {kind: item.kind, id: item.id})); }
          catch (error) { releaseResults.set(token, {error: error.message}); }
          finally {
            releaseBusy.delete(token); inspect.disabled = false;
            for (const entry of components.values()) if (releaseKey(entry.item) === token) {
              if (entry.searchButton) entry.searchButton.disabled = false;
              renderReleases(entry.results, releaseResults.get(token));
            }
          }
        });
        component.actions.append(inspect);
      }
      if (download) nativeLink("qbittorrent", component.actions);
      if (item.kind !== "qbittorrent") nativeLink(item.kind, component.actions);
    }
    if (component.notice) evidence.append(text("p", component.notice, "field-help"));
  }
  function renderRequests() {
    if (!requestData) return;
    const active = [...requestItems.values()].filter(r=>!r.library);
    element("count-all").textContent = active.length;
    element("count-attention").textContent = active.filter(isAttention).length;
    element("count-imported").textContent = [...requestItems.values()].filter(r=>r.library).length;
    const query = element("request-filter").value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const key of requestOrder) {
      const item = requestItems.get(key);
      const matches = (!attentionOnly || (!item.library && isAttention(item))) &&
        (query ? [item.title,item.scope,item.release].filter(Boolean).join(" ").toLocaleLowerCase().includes(query)
          : importedOnly === !!item.library);
      const shown = matches && visible < 100;
      if (matches) visible++;
      if (shown && !components.has(key)) {
        const component = makeComponent(key); components.set(key,component); body.append(component.row,component.detailRow);
        component.item = item; updateSummary(component,item);
      }
      const component = components.get(key);
      if(!component) continue;
      component.row.hidden = !shown;
      const selected = shown && selectedWorkflow === key;
      component.row.classList.toggle("selected",selected);
      component.button.setAttribute("aria-expanded",String(selected));
      component.detailRow.hidden = !selected;
      if(selected) detailEvidence(component,item);
    }
    table.hidden = !visible; empty.hidden = !!visible;
    empty.textContent = query || attentionOnly ? "No releases match this filter." : importedOnly
      ? "No delivered titles are reported." : "No active downloads or missing monitored media.";
    if(!element("request-status").classList.contains("check-warning") && visible>100) element("request-status").textContent = "Showing 100 of " + visible + " matches. Search to narrow the list.";
  }
  function setFilters() {
    element("filter-all").setAttribute("aria-pressed", String(!attentionOnly && !importedOnly));
    element("filter-attention").setAttribute("aria-pressed", String(attentionOnly));
    element("filter-imported").setAttribute("aria-pressed",String(importedOnly));
    renderRequests();
  }
  function updateSummary(component, item) {
        component.button.textContent = item.title; component.button.setAttribute("aria-label", "Details for " + item.title + (item.scope ? ", " + item.scope : ""));
        component.scope.textContent = item.scope || ""; component.scope.hidden = !item.scope;
        component.status.replaceChildren(text("span", item.phase, "phase " + phaseClass(item)));
        if (item.phase === "Downloading" && item.download) {
          const parts = [];
          if (Number.isFinite(item.download.progress)) parts.push(Math.round(item.download.progress * 100) + "%");
          if (item.download.speed > 0) parts.push(fmtBytes(item.download.speed) + "/s");
          if (parts.length) component.status.append(text("small", "· " + parts.join(" · "), "row-transfer"));
        }
        if (!item.available) component.status.append(text("small", "Last known · " + age(item.updated), "last-known"));
  }
  async function refreshRequests() {
    if (requestsBusy) return;
    requestsBusy = true;
    let restoreFocus = false;
    element("inspect").disabled = true;
    try {
      const data = await diagnosticRequest("");
      const library = (data.titles || []).filter(item=>item.hasFile || item.delivered).map(item=>({...item,library:true,
        key:"library:"+item.kind+":"+item.id, phase:item.delivered ? "Delivered" : "Imported", attention:false,
        scope:item.kind === "sonarr" ? (item.delivered ? item.delivered + " episode(s) delivered" : item.hasFile + " episode file(s)") : ""}));
      const items = new Map([...(data.workflow || []),...library].map(item=>[requestKey(item),item]));
      requestItems = items;
      // Keep existing rows in place. New observations must not shuffle a list
      // while a user is inspecting one of its downloads.
      const previousOrder = new Set(requestOrder);
      requestOrder = [...requestOrder.filter(key => items.has(key)), ...[...items.keys()].filter(key => !previousOrder.has(key))];
      for (const [key, component] of components) if (!items.has(key)) {
        const ownedFocus = component.row.contains(document.activeElement) || component.detailRow.contains(document.activeElement);
        component.row.remove(); component.detailRow.remove(); components.delete(key);
        if (selectedWorkflow === key) selectedWorkflow = null;
        restoreFocus ||= ownedFocus;
      }
      for (const [key, component] of components) {
        const item = items.get(key); component.item = item; updateSummary(component,item);
        if(!releaseBusy.has(releaseKey(item))) renderReleases(component.results,releaseResults.get(releaseKey(item)));
      }
      requestData = data;
      const maintenance = data.maintenance || {};
      const policy = {stop_sharing:"Stop seeding when complete",share_to_ratio:"Seed to a 1:1 ratio",share_one_day:"Seed for one day"}[maintenance.policy] || "Policy unavailable";
      const recent = maintenance.updated && Date.now()/1000 - maintenance.updated < 30;
      element("download-maintenance").textContent = policy + " · Checks every 30s · " + (!recent ? "Maintenance status unavailable" : maintenance.error ? "Download check failed: " + maintenance.error : maintenance.running ? "Checking downloads…" : "Last policy check " + age(maintenance.lastSuccess));
      renderSequences();
      const notices = element("request-notices"); notices.replaceChildren();
      for (const message of data.attention || []) notices.append(text("p", message, "notice"));
      if (data.requestReview) {
        const notice = document.createElement("div"); notice.className = "notice";
        notice.append(text("p", data.requestReview), text("p", "Accepting preserves existing monitoring and uses the current request history for future changes."));
        const accept = text("button", "Accept current request history");
        accept.addEventListener("click", async () => {
          accept.disabled = true;
          try { await diagnosticRequest("/rebaseline", {confirm: true}); await refreshRequests(); }
          catch (error) { notice.append(text("p", error.message)); accept.disabled = false; }
        });
        notice.append(accept); notices.append(notice);
      }
      element("request-status").textContent = "";
      element("request-status").classList.remove("check-warning");
      renderRequests();
    } catch (error) {
      element("request-status").textContent = error.message + (requestData ? " Showing the previous observation." : "");
      element("request-status").classList.add("check-warning");
      requestItems = new Map([...requestItems].map(([key,item])=>[key,{...item,
        lastKnownPhase:item.available ? item.phase : item.lastKnownPhase,available:false,phase:"Current progress unavailable"}]));
      for (const [key,component] of components) {
        component.item = requestItems.get(key); updateSummary(component,component.item);
      }
      renderRequests();
    } finally {
      requestsBusy = false; element("inspect").disabled = false;
      if (restoreFocus) element("inspect").focus({preventScroll: true});
    }
  }
  async function refreshOverview() {
    if (overviewBusy || location.protocol === "file:") return;
    overviewBusy = true; rememberAccessHost();
    try {
      const response = await timedFetch(ingressBase() + "diagnostics/overview", {cache: "no-store"}, 12000);
      if (!response.ok) throw new Error("Overview unavailable");
      overview = await response.json(); overviewFailed = false;
      const healthResponse = await timedFetch(ingressBase() + "health.json", {cache: "no-store"});
      health = healthResponse.ok ? await healthResponse.json() : null;
      renderOverview();
    } catch (_) {
      overviewFailed = true;
      if (overview) renderOverview();
      else { element("ecosystem").hidden = false; element("overview-note").hidden = false; element("overview-note").textContent = "Overview unavailable. Check application status."; }
    } finally { overviewBusy = false; }
    if (activePanel === "config-panel") refreshPreferences();
    if (activePanel === "diagnostic-panel") refreshRequests();
  }
  element("inspect").addEventListener("click", refreshRequests);
  element("check-downloads").addEventListener("click", async event => {
    const button = event.currentTarget;
    const result = element("download-check-result");
    button.disabled = true; result.hidden = false; result.classList.remove("check-warning");
    result.textContent = "Checking download policies and asking Arr to check imports and cleanup…";
    try {
      const response = await diagnosticRequest("/check-downloads", {});
      result.textContent = response.message;
      await refreshRequests();
    } catch (error) { result.textContent = error.message; result.classList.add("check-warning"); }
    finally { button.disabled = false; }
  });
  element("request-filter").addEventListener("input", renderRequests);
  element("filter-all").addEventListener("click", () => { attentionOnly = false; importedOnly = false; setFilters(); });
  element("filter-imported").addEventListener("click",()=>{importedOnly=true;attentionOnly=false;setFilters();});
  element("filter-attention").addEventListener("click", showAttention);
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectPanel(tab.getAttribute("aria-controls")));
    tab.addEventListener("keydown", event => {
      const next = {ArrowLeft: (index + tabs.length - 1) % tabs.length, ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault(); openPanel(tabs[next].getAttribute("aria-controls"));
    });
  });
  element("setup-recheck").addEventListener("click", refreshOverview);
  refreshOverview();
  setInterval(refreshOverview, 15000);
})();
