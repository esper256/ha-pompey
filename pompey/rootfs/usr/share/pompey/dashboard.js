/* Native observations, explicit release inspection and validated app preferences. */
(() => {
  const element = id => document.getElementById(id);
  let overview = null;
  let busy = false;
  let latestHealth = null;
  let overviewFailed = false;
  const tabs = [...element("dashboard-tabs").querySelectorAll('[role="tab"]')];
  let activePanel = "overview-panel";
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
    const tab = tabs.find(item => item.getAttribute("aria-controls") === id);
    tab.focus({preventScroll: true});
    tab.scrollIntoView({block: "nearest"});
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
    metric("Indexers", indexers ? indexers.healthy + " / " + indexers.enabled + " available" : "—", "Enabled sources", data.indexers, "overview-panel", indexers && indexers.healthy < indexers.enabled);
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
    if (!setup.seerrAvailable) row(table, "Seerr", "Connection unavailable", true);
    else if (!setup.initialized) row(table, "Seerr", "Open Search and requests to finish setup", true);
    if (setup.initialized && setup.seerrAvailable) for (const name of ["radarr", "sonarr"]) {
      if (!setup.requestConnections?.[name]) row(table, "Seerr → " + labels[name], "Check Seerr connections", true);
    }
    for (const name of ["radarr", "sonarr"]) {
      if (!setup.sources[name]) row(table, labels[name] + " sources", setup.sources[name] === null ? "Connection unavailable" : "No synchronized sources", true);
    }
    for (const source of setup.indexerIssues || []) row(table, source.name, "Indexer retry after " + new Date(source.retryAfter).toLocaleTimeString(), true);
    if (overview.summary.indexers.value?.enabled === 0) row(table, "Indexers", "Add a source in Manage indexers", true);
    const server = setup.mediaServer;
    if (server?.configured && server.reachable === false) row(table, server.type === 1 ? "Plex" : server.type === 3 ? "Emby" : "Jellyfin", "Library discovery unavailable", true);
    element("setup-panel").hidden = !table.children.length;
    element("setup-title").textContent = setup.seerrAvailable && !setup.initialized ? "Finish setup" : "Connections need attention";
    element("setup-note").textContent = server?.configured && server.reachable === false
      ? "Media-server discovery is unavailable. Imports can still complete; check the server connection in Seerr if media is missing from your player."
      : "Review request connections in Seerr and sources in Manage indexers. Automatic retries continue in the background.";
  }
  function renderStatus() {
    if (!overview) return;
    element("bundle-version").textContent = "Pompey " + overview.versions.pompey;
    const versions = element("application-versions");
    versions.replaceChildren();
    for (const [name, version] of Object.entries(overview.versions.applications)) {
      if (!["radarr", "sonarr", "prowlarr", "qbittorrent-nox", "seerr"].includes(name.toLowerCase())) continue;
      const service = name === "qbittorrent-nox" ? "qbittorrent" : name.toLowerCase();
      const down = latestHealth?.services?.[service] === false;
      const item = document.createElement("div");
      item.append(text("strong", labels[service] || name, down ? "service-down" : ""), text("small", version.replace(/^release-/, "").split("_v")[0] + (down ? " · unavailable" : "")));
      versions.append(item);
    }
    const jobs = element("maintenance-status");
    jobs.replaceChildren();
    for (const [name, job] of Object.entries(latestHealth?.jobs || {})) {
      row(jobs, labels[name] || name, job.failures ? "Retrying · " + age(job.lastSuccess) : job.overdue ? "Delayed · " + age(job.lastSuccess) : age(job.lastSuccess), job.failures > 0 || job.overdue);
    }
  }
  let preferencesLoaded = false;
  let preferencesDirty = false;
  let preferencesBusy = false;
  const preferencesForm = element("preferences");
  const preferencesResult = element("preferences-result");
  const preferenceFields = [...preferencesForm.querySelectorAll("input, select")];
  preferenceFields.forEach(field => { field.disabled = true; });
  async function refreshPreferences() {
    if (preferencesBusy || preferencesDirty) return;
    preferencesBusy = true;
    try {
      const data = await diagnosticRequest("/preferences");
      if (!preferencesDirty) {
        element("seeding-policy").value = data.values.after_download;
        element("download-slots").value = data.values.simultaneous_downloads;
        element("dual-audio").checked = data.values.prefer_dual_audio;
        preferencesResult.textContent = data.pending ? "Saved. Waiting for configuration to apply; this normally takes about a minute." : "Preferences applied.";
        preferencesResult.classList.remove("check-warning");
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
    preferencesDirty = true;
    element("save-preferences").disabled = !preferencesLoaded || preferencesBusy;
    preferencesResult.textContent = "Unsaved changes";
  });
  preferencesForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (!preferencesLoaded || preferencesBusy) return;
    preferencesBusy = true;
    element("save-preferences").disabled = true;
    preferenceFields.forEach(field => { field.disabled = true; });
    try {
      const data = await diagnosticRequest("/preferences", {
        after_download: element("seeding-policy").value,
        simultaneous_downloads: Number(element("download-slots").value),
        prefer_dual_audio: element("dual-audio").checked
      });
      preferencesDirty = false;
      preferencesResult.textContent = data.pending ? "Saved. Waiting for configuration to apply; this normally takes about a minute." : "Preferences applied.";
      preferencesResult.classList.remove("check-warning");
    } catch (error) {
      preferencesResult.textContent = error.message;
      preferencesResult.classList.add("check-warning");
      element("save-preferences").disabled = false;
    } finally {
      preferenceFields.forEach(field => { field.disabled = false; });
      preferencesBusy = false;
    }
  });
  function nativeLink(service, container) {
    if (!debugConsoles.hidden && ["radarr", "sonarr", "qbittorrent"].includes(service)) {
      const a = text("a", "Open " + (labels[service] || service), "native-link");
      a.href = ingressBase() + "debug/" + service + "/";
      a.target = "_blank"; a.rel = "noopener";
      container.append(a);
    }
  }
  function searchButton(kind, id, container) {
    const button = text("button", "Inspect releases");
    button.addEventListener("click", async () => {
      button.disabled = true;
      container.querySelector(".release-results")?.remove();
      const results = document.createElement("div");
      results.className = "release-results";
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
  function transfer(download) {
    if (!download) return "";
    const parts = [];
    if (Number.isFinite(download.progress)) parts.push(Math.round(download.progress * 100) + "%");
    if (download.speed > 0) parts.push(fmtBytes(download.speed) + "/s");
    if (Number.isFinite(download.connectedSeeds)) parts.push(download.connectedSeeds + " seeds");
    if (Number.isFinite(download.connectedPeers)) parts.push(download.connectedPeers + " peers");
    return parts.join(" · ");
  }
  let selectedWorkflow = null;
  async function refreshRequests() {
    const button = element("inspect");
    if (button.disabled) return;
    button.disabled = true;
    try {
      const data = await diagnosticRequest("");
      diagnosis.replaceChildren();
      for (const notice of data.attention || []) diagnosis.append(text("p", notice, "check-warning"));
      const items = data.workflow || [];
      if (!items.length) diagnosis.append(text("p", "No active downloads or missing monitored media reported. Open Seerr for request history."));
      if (items.length) {
        const table = document.createElement("table");
        table.className = "workflow-table";
        table.setAttribute("aria-label", "Downstream request workflow");
        const head = document.createElement("thead");
        const headings = document.createElement("tr");
        for (const name of ["Title / application", "Current activity"]) {
          const th = text("th", name); th.scope = "col"; headings.append(th);
        }
        head.append(headings); table.append(head);
        const body = document.createElement("tbody"); table.append(body);
        const detail = document.createElement("section"); detail.className = "workflow-detail";
        detail.setAttribute("aria-label", "Workflow inspection");
        const select = (item, tr, control) => {
          selectedWorkflow = [item.kind, item.id, item.title].join(":");
          for (const row of body.children) {
            row.classList.toggle("selected", row === tr);
            row.querySelector("button").setAttribute("aria-pressed", String(row === tr));
          }
          control.setAttribute("aria-controls", "workflow-inspection");
          detail.id = "workflow-inspection";
          detail.replaceChildren(text("h3", item.title), text("p", (labels[item.kind] || item.kind) + " · " + (item.available ? "Observed at " : "Last observation at ") + (item.updated ? new Date(item.updated * 1000).toLocaleTimeString() : "unknown time"), "evidence"));
          if (!item.available) detail.append(text("p", "Last known: " + (item.lastKnownPhase || "Unavailable"), "check-warning"));
          for (const message of item.messages || []) detail.append(text("p", message, "check-warning"));
          const download = item.download;
          if (download) {
            if (item.available && Number.isFinite(download.progress)) {
              const meter = document.createElement("progress"); meter.max = 1; meter.value = download.progress;
              meter.setAttribute("aria-label", "Download progress"); detail.append(meter);
            }
            detail.append(text("p", transfer(download)));
            const noConnections = download.connectedSeeds === 0 && download.connectedPeers === 0;
            if (item.available && ["stalledDL", "metaDL", "forcedMetaDL"].includes(download.state)) detail.append(text("p", noConnections
              ? "No seeds or peers are connected. The tracker may list sources that cannot currently be reached."
              : "Peers are connected, but no data is arriving. They may not have the remaining pieces."));
            if (item.available && download.state === "stoppedDL") detail.append(text("p", "The download is stopped. Check for excluded files, an error, or a manual stop before resuming it."));
          }
          if (item.release) detail.append(text("p", item.release, "evidence"));
          if (download) detail.append(text("p", "qBittorrent: " + (download.state || "state unavailable"), "evidence"));
          if (item.nextAction) detail.append(text("p", item.nextAction));
          if (debugConsoles.hidden && download && ["stoppedDL", "pausedDL", "error", "missingFiles"].includes(download.state)) detail.append(text("p", "Enable advanced interfaces in Home Assistant’s app configuration to inspect or resume this torrent."));
          if (item.available && ["radarr", "sonarr"].includes(item.kind) && item.id && !item.messages?.length) searchButton(item.kind, item.id, detail);
          nativeLink(item.kind, detail);
        };
        const controls = [];
        for (const item of items) {
          const tr = document.createElement("tr");
          const title = document.createElement("td");
          const control = text("button", item.title, "title-button");
          control.setAttribute("aria-label", "Inspect " + item.title);
          title.append(control, text("small", labels[item.kind] || item.kind));
          const activity = document.createElement("td");
          activity.append(text("span", item.phase, "phase" + (item.attention || !item.available ? " warning" : "")));
          if (item.download) activity.append(text("small", transfer(item.download)));
          else if (item.messages?.length) activity.append(text("small", item.messages[0]));
          if (!item.available) activity.append(text("small", "Last known · " + age(item.updated)));
          tr.append(title, activity); body.append(tr);
          control.addEventListener("click", () => select(item, tr, control));
          controls.push({item, tr, control});
        }
        diagnosis.append(table, detail);
        const selected = controls.find(({item}) => [item.kind, item.id, item.title].join(":") === selectedWorkflow) || controls[0];
        select(selected.item, selected.tr, selected.control);
      }
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
      if (activePanel === "config-panel") refreshPreferences();
    } catch (_) {
      overviewFailed = true;
      if (overview) renderOverview();
      else { element("ecosystem").hidden = false; element("overview-note").hidden = false; element("overview-note").textContent = "Overview unavailable. Check application status."; }
    } finally { busy = false; }
  }
  element("inspect").addEventListener("click", refreshRequests);
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectPanel(tab.getAttribute("aria-controls")));
    tab.addEventListener("keydown", event => {
      const next = {ArrowLeft: (index + tabs.length - 1) % tabs.length,
                    ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault();
      openPanel(tabs[next].getAttribute("aria-controls"));
    });
  });
  element("setup-recheck").addEventListener("click", refreshOverview);
  refreshOverview();
  setInterval(refreshOverview, 15000);
})();
