/* Read-only dashboard. Native applications continue to own requests and imports. */
(() => {
  const element = id => document.getElementById(id);
  let overview = null;
  let busy = false;
  let latestHealth = null;
  let overviewFailed = false;
  const labels = {radarr: "Radarr", sonarr: "Sonarr", prowlarr: "Prowlarr", qbittorrent: "qBittorrent", seerr: "Seerr", configuration: "Configuration", requests: "Requests", downloads: "Downloads", routing: "Library routing"};
  const text = (tag, value, className = "") => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  function age(updated) {
    if (!updated) return "Never checked";
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - updated));
    return seconds < 60 ? seconds + "s ago" : Math.floor(seconds / 60) + "m ago";
  }
  function row(table, name, value, warning = false) {
    const tr = document.createElement("tr");
    tr.append(text("td", name), text("td", value, warning ? "check-warning" : ""));
    table.append(tr);
  }
  async function openPanel(id) {
    const panel = element(id);
    panel.open = true;
    panel.scrollIntoView({behavior: "smooth", block: "nearest"});
    panel.querySelector("summary").focus();
    if (id === "setup-panel") renderSetup();
  }
  function metric(title, value, detail, source, target, warning = false) {
    const button = document.createElement("button");
    button.className = "metric" + (warning ? " warning" : "");
    const stale = overviewFailed || !source?.available || Date.now() / 1000 - (source.updated || 0) > 45;
    if (stale) button.classList.add("stale");
    button.append(text("span", title), text("strong", value), text("small", detail));
    if (stale) button.append(text("small", source?.updated ? "Last known · " + age(source.updated) : "Unavailable"));
    button.addEventListener("click", () => openPanel(target));
    element("ecosystem-counts").append(button);
  }
  function renderOverview() {
    const data = overview.summary;
    element("ecosystem").hidden = false;
    element("ecosystem-counts").replaceChildren();
    const torrents = data.torrents.value;
    metric("Torrents", torrents ? torrents.downloading + " downloading" : "—", torrents ? ["seeding", "stalled", "paused", "checking", "other"].filter(k => torrents[k]).map(k => torrents[k] + " " + k).join(" · ") : "", data.torrents, "diagnostic-panel");
    metric("Monitored", (data.shows.value ?? "—") + " shows", (data.movies.value ?? "—") + " movies", {available:data.shows.available && data.movies.available, updated:Math.min(data.shows.updated || 0, data.movies.updated || 0)}, "diagnostic-panel");
    const indexers = data.indexers.value;
    metric("Indexers", indexers ? indexers.healthy + " / " + indexers.enabled + " available" : "—", "Enabled sources", data.indexers, "setup-panel", indexers && indexers.healthy < indexers.enabled);
    const requests = data.requests.value;
    metric("Requests", requests ? requests.open + " open" : "—", requests ? [requests.approval && requests.approval + " awaiting approval", requests.processing && requests.processing + " in progress", requests.failed && requests.failed + " failed"].filter(Boolean).join(" · ") || "No outstanding requests" : "", data.requests, "diagnostic-panel", requests?.failed > 0);
    element("overview-note").hidden = !overviewFailed;
    element("overview-note").textContent = "Overview unavailable; showing last known counts.";
    renderSetup();
    renderStatus();
    const storage = overview.storage || {};
    window.pompeyStorageBlocked = storage.ok === false;
    element("storage-alert").hidden = storage.ok !== false;
    element("storage-reason").textContent = (storage.reason || "Storage could not be checked.") + (storage.path ? " " + storage.path : "");
    element("storage-action").textContent = storage.phase === "paused"
      ? "Downloads and imports are paused. Restore the share or free space; access is checked automatically. Existing files are retained."
      : storage.phase === "attention" ? "Application shutdown could not be confirmed. Review the Home Assistant log."
      : "Stopping affected applications while storage is unavailable. Local setup and request interfaces remain accessible.";
  }
  function renderSetup() {
    if (!overview) return;
    const table = element("setup-checks");
    table.replaceChildren();
    const setup = overview.setup;
    row(table, "Storage", overview.storage?.ok === true ? "Writable" : overview.storage?.ok === false ? "Needs attention" : "Unavailable", overview.storage?.ok === false);
    row(table, "Request setup", !setup.seerrAvailable ? "Unavailable" : setup.initialized ? "Complete" : "Not configured");
    if (setup.initialized) for (const name of ["radarr", "sonarr"]) row(table, "Seerr → " + labels[name], !setup.seerrAvailable ? "Unavailable" : setup.requestConnections?.[name] ? "Configured" : "Check Seerr connections", setup.seerrAvailable && !setup.requestConnections?.[name]);
    for (const name of ["radarr", "sonarr"]) row(table, labels[name] + " sources", setup.sources[name] === null ? "Unavailable" : setup.sources[name] ? "Configured" : "No synchronized sources", setup.sources[name] === false);
    const indexers = overview.summary.indexers;
    row(table, "Enabled indexers", indexers.available && indexers.value ? indexers.value.healthy + " / " + indexers.value.enabled + " available" : "Unavailable", indexers.available && indexers.value?.healthy < indexers.value?.enabled);
    for (const source of setup.indexerIssues || []) row(table, source.name, "Retry after " + new Date(source.retryAfter).toLocaleTimeString(), true);
    const server = setup.mediaServer;
    const name = server?.type === 1 ? "Plex" : server?.type === 3 ? "Emby" : "Jellyfin";
    if (!setup.seerrAvailable) row(table, "Media-server discovery", "Status unavailable");
    else if (server?.configured) {
      row(table, name + " discovery (optional)", server.reachable === false ? "Connection unavailable" : server.selectedLibraries ? server.selectedLibraries + " libraries selected" : "No libraries selected", server.reachable === false);
    } else row(table, "Media-server discovery", "Not configured · optional");
    element("setup-note").textContent = "Seerr and Prowlarr open on separate local-network ports. Additional networks can be allowed in Pompey's Local network access setting.";
    if (setup.indexerIssues?.length) {
      const link = text("a", "Manage indexers", "native-link");
      link.href = "http://" + location.hostname + ":" + lastPorts.sources + "/";
      link.target = "_blank"; link.rel = "noopener";
      element("setup-note").append(document.createElement("br"), link);
    }
  }
  function renderStatus() {
    if (!overview) return;
    element("bundle-version").textContent = "Pompey " + overview.versions.pompey + (overview.versions.build ? " · " + overview.versions.build.slice(0, 12) : "");
    const table = element("application-versions");
    table.replaceChildren();
    for (const [name, version] of Object.entries(overview.versions.applications)) {
      const service = name === "qbittorrent-nox" ? "qbittorrent" : name.toLowerCase();
      const down = latestHealth?.services?.[service] === false;
      row(table, name, version + (down ? " · unavailable" : ""), down);
    }
    element("quality-resources").textContent = Object.entries(overview.versions.resources).map(([name, commit]) => name + ": " + commit).join(" · ");
    const jobs = element("maintenance-status");
    jobs.replaceChildren();
    for (const [name, job] of Object.entries(latestHealth?.jobs || {})) {
      row(jobs, labels[name] || name, job.failures ? "Retrying · " + age(job.lastSuccess) : job.overdue ? "Delayed · " + age(job.lastSuccess) : age(job.lastSuccess), job.failures > 0 || job.overdue);
    }
  }
  function nativeLink(service, container) {
    if (!debugConsoles.hidden && ["radarr", "sonarr", "qbittorrent"].includes(service)) {
      const a = text("a", "Open " + (labels[service] || service), "native-link");
      a.href = ingressBase() + "debug/" + service + "/";
      a.target = "_blank"; a.rel = "noopener";
      container.append(a);
    }
  }
  function searchButton(kind, id, container) {
    const button = text("button", "Search releases");
    button.addEventListener("click", async () => {
      button.disabled = true;
      const results = document.createElement("div");
      container.append(results);
      try {
        const result = await diagnosticRequest("/search", {kind, id});
        results.append(text("p", result.status + " · " + new Date(result.updated * 1000).toLocaleString()));
        if (!result.releases?.length) results.append(text("p", "The configured indexers returned no releases."));
        for (const release of result.releases || []) results.append(text("p", release.title + ": " + (release.rejections?.join("; ") || "No rejection reasons reported") + "; seeders: " + (release.reportedSeeders ?? "unknown")));
      } catch (error) { results.append(text("p", error.message)); }
      finally { button.disabled = false; }
    });
    container.append(button);
  }
  async function refreshRequests() {
    const button = element("inspect");
    button.disabled = true;
    diagnosis.replaceChildren(text("p", "Loading requests…"));
    try {
      const data = await diagnosticRequest("");
      diagnosis.replaceChildren();
      for (const notice of data.attention || []) diagnosis.append(text("p", notice, "check-warning"));
      const linked = new Set();
      const history = document.createElement("details");
      history.append(text("summary", "Completed and declined requests"));
      for (const request of data.requests || []) {
        const card = document.createElement("details"); card.className = "request-card";
        const summary = text("summary", request.title);
        summary.setAttribute("aria-label", request.title + ": " + request.phase);
        summary.append(text("span", request.phase, "request-phase"));
        card.append(summary);
        if (request.available) card.append(text("p", (request.observedIn || ["Seerr"]).join(" · ") + " · " + age(request.updated)));
        if (request.nextAction) card.append(text("p", request.nextAction));
        for (const message of request.messages || []) card.append(text("p", message, "check-warning"));
        if (!request.available && request.updated) card.append(text("p", "Last known: " + request.lastKnownPhase + " · " + age(request.updated)));
        if (request.arrLinked) {
          linked.add(request.kind + ":" + request.arrId);
          searchButton(request.kind, request.arrId, card);
          nativeLink(request.kind, card);
        }
        if (["Completed", "Declined"].includes(request.state) || ["Available according to Seerr", "Imported to library"].includes(request.phase)) history.append(card);
        else diagnosis.append(card);
      }
      if (history.children.length > 1) diagnosis.append(history);
      const extra = document.createElement("details"); extra.append(text("summary", "Other titles and downloads"));
      for (const title of data.titles || []) if (!linked.has(title.kind + ":" + title.id)) {
        const card = document.createElement("div"); card.className = "request-card";
        card.append(text("p", title.title + (title.monitored ? " · monitored" : " · monitoring disabled")));
        searchButton(title.kind, title.id, card); nativeLink(title.kind, card); extra.append(card);
      }
      for (const download of (data.downloads || []).filter(row => row.service === "downloader")) extra.append(text("p", download.title + " · " + download.state));
      if (extra.children.length > 1) diagnosis.append(extra);
      if (!(data.requests || []).length && extra.children.length === 1) diagnosis.append(text("p", "No requests or downloads to show."));
      if (data.requestReview) {
        diagnosis.append(text("p", data.requestReview, "check-warning"));
        const accept = text("button", "Accept current request history");
        accept.addEventListener("click", async () => {
          accept.disabled = true;
          try { diagnosis.append(text("p", (await diagnosticRequest("/rebaseline", {confirm:true})).message)); }
          catch (error) { diagnosis.append(text("p", error.message)); accept.disabled = false; }
        });
        diagnosis.append(text("p", "Existing monitoring is preserved. Accepting uses the current request history for future monitoring changes."), accept);
      }
    } catch (error) { diagnosis.replaceChildren(text("p", error.message)); }
    finally { button.disabled = false; }
  }
  async function refreshOverview() {
    if (busy || location.protocol === "file:") return;
    busy = true;
    try {
      const response = await timedFetch(ingressBase() + "diagnostics/overview", {cache:"no-store"}, 12000);
      if (!response.ok) throw new Error("Overview unavailable");
      overview = await response.json();
      overviewFailed = false;
      const healthResponse = await timedFetch(ingressBase() + "health.json", {cache:"no-store"});
      latestHealth = healthResponse.ok ? await healthResponse.json() : null;
      renderOverview();
    } catch (_) {
      overviewFailed = true;
      if (overview) renderOverview();
      else { element("ecosystem").hidden = false; element("overview-note").hidden = false; element("overview-note").textContent = "Overview unavailable. Check application status."; }
    } finally { busy = false; }
  }
  element("inspect").addEventListener("click", refreshRequests);
  element("diagnostic-panel").addEventListener("toggle", () => {
    if (element("diagnostic-panel").open) refreshRequests();
  });
  element("setup-recheck").addEventListener("click", refreshOverview);
  refreshOverview();
  setInterval(refreshOverview, 15000);
})();
