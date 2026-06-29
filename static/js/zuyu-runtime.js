(function initZuyuRuntime(globalScope) {
  const root = globalScope;
  const hasDocument = typeof document !== "undefined";
  const hasWindow = typeof window !== "undefined";

  const state = {
    initialized: false,
    pendingRequests: 0,
    activeLayer: null,
    layerObserver: null,
    sessionId: null,
  };

  function deepClone(value) {
    if (typeof structuredClone === "function") return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
  }

  function randomId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
    return `zuyu_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
  }

  function safeText(value, fallback, maxLength) {
    const text = typeof value === "string" ? value.trim() : "";
    if (!text) return fallback;
    return text.slice(0, maxLength);
  }

  function safeNumber(value, fallback, options) {
    const opts = options || {};
    const num = Number(value);
    if (!Number.isFinite(num)) return fallback;
    if (typeof opts.min === "number" && num < opts.min) return fallback;
    if (typeof opts.max === "number" && num > opts.max) return fallback;
    return num;
  }

  function safeArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function safeObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function normalizeScheduleBlocks(value) {
    return safeArray(value)
      .map((item) => {
        const block = safeObject(item);
        const label = safeText(block.label, "", 120);
        const time = safeText(block.time, "", 16);
        if (!label || !time) return null;
        return {
          label,
          time,
          end: safeText(block.end, "", 16) || undefined,
          cat: safeText(block.cat, "routine", 30),
        };
      })
      .filter(Boolean);
  }

  function normalizeGymSections(value) {
    return safeArray(value)
      .map((item, index) => {
        const section = safeObject(item);
        const label = safeText(section.label, "", 120);
        if (!label) return null;
        const exercises = safeArray(section.exercises)
          .map((exercise, exerciseIndex) => {
            const ex = safeObject(exercise);
            const name = safeText(ex.name, "", 120);
            if (!name) return null;
            return {
              id: safeText(ex.id, `gx_${index}_${exerciseIndex}`, 40),
              name,
              sets: safeText(ex.sets, "", 40),
              reps: safeText(ex.reps, "", 40) || undefined,
              weight: safeText(ex.weight, "", 20) || undefined,
              notes: safeText(ex.notes, "", 400) || undefined,
              archived: !!ex.archived,
            };
          })
          .filter(Boolean);
        return {
          id: safeText(section.id, `gs_${index}`, 40),
          label,
          description: safeText(section.description, "", 200) || undefined,
          days: safeArray(section.days).map((day) => safeNumber(day, -1, { min: 0, max: 6 })).filter((day) => day >= 0),
          color: safeText(section.color, "#7c86f8", 32),
          exercises,
        };
      })
      .filter(Boolean);
  }

  function normalizeCalendarTasks(value) {
    return safeArray(value)
      .map((item, index) => {
        const task = safeObject(item);
        const text = safeText(task.text, "", 240);
        if (!text) return null;
        return {
          id: safeText(task.id, `ct_${index}`, 40),
          text,
          category: safeText(task.category, "personal", 20) === "work" ? "work" : "personal",
          done: Boolean(task.done),
          important: Boolean(task.important),
          subtasks: safeArray(task.subtasks)
            .map((subtask, subIndex) => {
              const sub = safeObject(subtask);
              const subText = safeText(sub.text, "", 180);
              if (!subText) return null;
              return {
                id: safeText(sub.id, `sub_${index}_${subIndex}`, 40),
                text: subText,
                done: Boolean(sub.done),
              };
            })
            .filter(Boolean),
        };
      })
      .filter(Boolean);
  }

  function normalizeMeals(value) {
    const input = safeObject(value);
    const output = {};
    ["breakfast", "lunch", "dinner"].forEach((slot) => {
      const text = safeText(input[slot], "", 200);
      if (text) output[slot] = text;
    });
    output.extras = safeArray(input.extras)
      .map((item, index) => {
        const extra = safeObject(item);
        const text = safeText(extra.text, "", 200);
        if (!text) return null;
        return { id: safeText(extra.id, `extra_${index}`, 40), text };
      })
      .filter(Boolean);
    return output;
  }

  function normalizeKanbanTasks(value) {
    return safeArray(value)
      .map((item, index) => {
        const task = safeObject(item);
        const title = safeText(task.title, "", 180);
        if (!title) return null;
        return {
          id: safeText(task.id, `kb_${index}`, 60),
          num: safeNumber(task.num, index + 1, { min: 0, max: 1000000 }),
          title,
          description: typeof task.description === "string" ? task.description.slice(0, 6000) : "",
          priority: ["low", "medium", "high", "urgent"].includes(task.priority) ? task.priority : "medium",
          status: safeText(task.status, "todo", 60),
          dueDate: safeText(task.dueDate, "", 10) || null,
          plannedDate: safeText(task.plannedDate, "", 10) || null,
          labels: safeArray(task.labels).map((label) => safeText(label, "", 40)).filter(Boolean),
          board: safeText(task.board, "personal", 20) === "work" ? "work" : "personal",
          createdAt: safeText(task.createdAt, new Date().toISOString(), 40),
          progress: (task.progress != null && typeof task.progress === "number") ? Math.max(0, Math.min(1, task.progress)) : null,
          showProgress: typeof task.showProgress === "boolean" ? task.showProgress : true,
          cardType: task.cardType === "checklist" ? "checklist" : "standard",
          checklist: safeArray(task.checklist).map((it, i) => {
            const obj = safeObject(it);
            const text = typeof obj.text === "string" ? obj.text.slice(0, 500) : "";
            const rawUrl = typeof obj.url === "string" ? obj.url.trim().slice(0, 1000) : "";
            // Only persist URLs with a safe-ish protocol (or schemeless, which we'll
            // normalise to https:// at click time). Reject javascript:/data:/vbscript:.
            let url = "";
            if (rawUrl) {
              const lower = rawUrl.toLowerCase();
              const bad = ["javascript:", "data:", "vbscript:", "file:"].some(p => lower.startsWith(p));
              if (!bad) url = rawUrl;
            }
            return {
              id: safeText(obj.id, `cl_${i}`, 40),
              text,
              done: !!obj.done,
              url,
            };
          }),
        };
      })
      .filter(Boolean);
  }

  function normalizeKanbanCols(value) {
    return safeArray(value)
      .map((item, index) => {
        const col = safeObject(item);
        const id = safeText(col.id, `col_${index}`, 60);
        const label = safeText(col.label, "", 60);
        if (!id || !label) return null;
        return { id, label, color: safeText(col.color, "#7c86f8", 32) };
      })
      .filter(Boolean);
  }

  function normalizeLabels(value) {
    return safeArray(value)
      .map((item, index) => {
        const label = safeObject(item);
        const id = safeText(label.id, `lbl_${index}`, 40);
        const name = safeText(label.name, "", 60);
        if (!id || !name) return null;
        return {
          id,
          name,
          color: safeText(label.color, "#7c86f8", 32),
          parentId: safeText(label.parentId, "", 40) || undefined,
        };
      })
      .filter(Boolean);
  }

  function readJSON(key, fallback, normalizer) {
    if (typeof localStorage === "undefined") return deepClone(fallback);
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return deepClone(fallback);
      const parsed = JSON.parse(raw);
      return normalizer ? normalizer(parsed) : parsed;
    } catch (_error) {
      return deepClone(fallback);
    }
  }

  function writeJSON(key, value) {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(key, JSON.stringify(value));
  }

  function getSessionId() {
    if (state.sessionId) return state.sessionId;
    if (typeof localStorage === "undefined") {
      state.sessionId = randomId();
      return state.sessionId;
    }
    state.sessionId = localStorage.getItem("zuyu_client_session_id") || randomId();
    localStorage.setItem("zuyu_client_session_id", state.sessionId);
    return state.sessionId;
  }

  function toLogPayload(event, data, level, message) {
    return {
      entries: [
        {
          level: level || "info",
          event,
          message: message || null,
          data: {
            href: hasWindow ? window.location.pathname : "",
            session_id: getSessionId(),
            ...(data || {}),
          },
        },
      ],
    };
  }

  function logClient(event, data, level, message) {
    if (!hasWindow || !navigator.onLine) return;
    const payload = JSON.stringify(toLogPayload(event, data, level, message));
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/client-logs", new Blob([payload], { type: "application/json" }));
        return;
      }
    } catch (_error) {
    }
    fetch("/api/client-logs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      keepalive: true,
    }).catch(() => {});
  }

  function findStatusBar() {
    return hasDocument ? document.getElementById("appStatusBar") : null;
  }

  function setStatus(kind, message) {
    const el = findStatusBar();
    if (!el) return;
    const label = message || (
      kind === "offline" ? "Offline. Changes may not sync until the connection returns."
      : kind === "syncing" ? "Syncing changes..."
      : kind === "error" ? "Some changes failed. You can keep using the app."
      : "All changes synced"
    );
    el.dataset.status = kind;
    el.textContent = label;
  }

  function syncStatus() {
    if (!hasWindow) return;
    if (!navigator.onLine) {
      setStatus("offline");
      return;
    }
    if (state.pendingRequests > 0) {
      setStatus("syncing");
      return;
    }
    setStatus("ready");
  }

  function syncThemeToggle() {
    if (!hasDocument) return;
    const theme = getTheme();
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    const toggle = document.getElementById("themeToggle");
    if (toggle) toggle.classList.toggle("on", theme === "light");
  }

  function setTheme(theme, options) {
    const nextTheme = theme === "light" ? "light" : "dark";
    const opts = options || {};
    if (typeof localStorage !== "undefined") localStorage.setItem("zuyu-theme", nextTheme);
    syncThemeToggle();
    if (!opts.silent) logClient("theme.changed", { theme: nextTheme }, "info");
    return nextTheme;
  }

  function getTheme() {
    if (typeof localStorage === "undefined") return "dark";
    return localStorage.getItem("zuyu-theme") === "light" ? "light" : "dark";
  }

  function focusableElements(container) {
    return [...container.querySelectorAll("a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])")]
      .filter((element) => !element.hasAttribute("hidden") && element.getAttribute("aria-hidden") !== "true" && element.offsetParent !== null);
  }

  function visibleContainer(container) {
    if (!container) return false;
    if (container.id === "panel") return container.classList.contains("open");
    return container.classList.contains("visible") && getComputedStyle(container).display !== "none";
  }

  function dialogSurface(container) {
    if (!container) return null;
    return container.id === "panel" ? container : container.querySelector(".modal") || container;
  }

  function assignDialogMetadata(container) {
    const surface = dialogSurface(container);
    if (!surface) return;
    surface.setAttribute("role", "dialog");
    surface.setAttribute("aria-modal", "true");
    if (!surface.hasAttribute("tabindex")) surface.setAttribute("tabindex", "-1");
    const title = surface.querySelector(".modal-title, .panel-date");
    if (title) {
      if (!title.id) title.id = `${container.id || "dialog"}_title`;
      surface.setAttribute("aria-labelledby", title.id);
    }
  }

  function activateLayer(container) {
    if (!hasDocument || !visibleContainer(container)) return;
    const surface = dialogSurface(container);
    if (!surface) return;
    assignDialogMetadata(container);
    if (state.activeLayer && state.activeLayer.container === container) return;
    if (state.activeLayer) deactivateLayer(state.activeLayer.container, false);
    state.activeLayer = {
      container,
      surface,
      restoreTarget: document.activeElement instanceof HTMLElement ? document.activeElement : null,
    };
    const focusables = focusableElements(surface);
    (focusables[0] || surface).focus();
  }

  function deactivateLayer(container, restoreFocus) {
    if (!state.activeLayer || state.activeLayer.container !== container) return;
    const restore = restoreFocus !== false ? state.activeLayer.restoreTarget : null;
    state.activeLayer = null;
    if (restore && typeof restore.focus === "function") {
      setTimeout(() => restore.focus(), 0);
    }
  }

  function handleLayerKeydown(event) {
    if (!state.activeLayer) return;
    if (event.key === "Escape") {
      const closeButton = state.activeLayer.surface.querySelector(".btn-x, .btn-sec, .btn-del");
      if (closeButton && typeof closeButton.click === "function") {
        closeButton.click();
      }
      return;
    }
    if (event.key !== "Tab") return;
    const focusables = focusableElements(state.activeLayer.surface);
    if (!focusables.length) {
      event.preventDefault();
      state.activeLayer.surface.focus();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function installLayerObserver() {
    if (!hasDocument || state.layerObserver) return;
    const candidates = [
      ...document.querySelectorAll(".modal-wrap"),
      document.getElementById("panel"),
    ].filter(Boolean);
    candidates.forEach(assignDialogMetadata);
    state.layerObserver = new MutationObserver(() => {
      const openContainer = candidates.find((container) => visibleContainer(container));
      if (openContainer) activateLayer(openContainer);
      else if (state.activeLayer) deactivateLayer(state.activeLayer.container);
    });
    candidates.forEach((container) => state.layerObserver.observe(container, { attributes: true, attributeFilter: ["class", "style"] }));
    document.addEventListener("keydown", handleLayerKeydown, true);
  }

  function parseResponseBody(response, text) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      if (!text) return null;
      return JSON.parse(text);
    }
    return text;
  }

  async function executeFetch(url, options, attempt) {
    const start = hasWindow && window.performance ? performance.now() : Date.now();
    const method = (options.method || "GET").toUpperCase();
    const timeoutMs = safeNumber(options.timeoutMs, 15000, { min: 1000, max: 60000 });
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timeoutId = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
    const headers = new Headers(options.headers || {});
    if (!headers.has("Content-Type") && options.body && !(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    headers.set("x-client-session-id", getSessionId());
    state.pendingRequests += 1;
    syncStatus();
    try {
      const response = await fetch(url, { ...options, headers, signal: controller ? controller.signal : undefined });
      const text = await response.text();
      const body = parseResponseBody(response, text);
      const durationMs = Math.round(((hasWindow && window.performance ? performance.now() : Date.now()) - start) * 100) / 100;
      if (!response.ok) {
        const message = body && typeof body === "object"
          ? body.error?.message || body.detail || body.message || `${response.status} ${response.statusText}`
          : (text || `${response.status} ${response.statusText}`);
        logClient("api.error", { url, method, status: response.status, duration_ms: durationMs }, "error", message);
        if (response.status >= 500) setStatus("error", "Server error. The app is still running, but some data may be stale.");
        throw new Error(message);
      }
      if (durationMs > 1200) {
        logClient("api.slow", { url, method, duration_ms: durationMs }, "warning", "Slow API response");
      }
      return body;
    } catch (error) {
      if (method === "GET" && attempt === 0 && navigator.onLine) {
        await new Promise((resolve) => setTimeout(resolve, 250));
        return executeFetch(url, options, 1);
      }
      if (!navigator.onLine) {
        setStatus("offline");
      } else {
        setStatus("error");
      }
      logClient("api.failure", { url, method, attempt }, "error", error instanceof Error ? error.message : String(error));
      throw error;
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
      state.pendingRequests = Math.max(0, state.pendingRequests - 1);
      syncStatus();
    }
  }

  async function api(url, options) {
    return executeFetch(url, options || {}, 0);
  }

  function installGlobalErrorHandlers() {
    if (!hasWindow) return;
    window.addEventListener("error", (event) => {
      logClient("ui.error", { source: event.filename, line: event.lineno, column: event.colno }, "error", event.message);
      setStatus("error");
    });
    window.addEventListener("unhandledrejection", (event) => {
      const message = event.reason instanceof Error ? event.reason.message : String(event.reason);
      logClient("ui.unhandled_rejection", {}, "error", message);
      setStatus("error");
    });
  }

  function installConnectionHandlers() {
    if (!hasWindow) return;
    window.addEventListener("online", () => {
      syncStatus();
      logClient("network.online", {}, "info");
      if (typeof root.toast === "function") root.toast("Back online", "ok");
    });
    window.addEventListener("offline", () => {
      syncStatus();
      logClient("network.offline", {}, "warning");
      if (typeof root.toast === "function") root.toast("Offline mode. Changes may not sync yet.", "err");
    });
  }

  function installToastA11y() {
    if (!hasDocument) return;
    const toasts = document.getElementById("toasts");
    if (!toasts) return;
    toasts.setAttribute("role", "status");
    toasts.setAttribute("aria-live", "polite");
    toasts.setAttribute("aria-atomic", "false");
  }

  function init() {
    if (state.initialized || !hasDocument) return;
    state.initialized = true;
    syncThemeToggle();
    installToastA11y();
    installConnectionHandlers();
    installGlobalErrorHandlers();
    installLayerObserver();
    syncStatus();
    if (hasWindow && window.performance && performance.getEntriesByName("zuyu_bootstrap").length === 0) {
      performance.mark("zuyu_bootstrap_end");
      try {
        performance.measure("zuyu_bootstrap", "zuyu_bootstrap_start", "zuyu_bootstrap_end");
        const entry = performance.getEntriesByName("zuyu_bootstrap")[0];
        if (entry && entry.duration > 1500) {
          logClient("perf.bootstrap", { duration_ms: Math.round(entry.duration) }, "warning", "Slow bootstrap");
        }
      } catch (_error) {
      }
    }
  }

  const runtime = {
    api,
    init,
    logClient,
    setStatus,
    setTheme,
    getTheme,
    syncThemeToggle,
    safeNumber,
    validators: {
      scheduleBlocks: normalizeScheduleBlocks,
      gymSections: normalizeGymSections,
      calendarTasks: normalizeCalendarTasks,
      meals: normalizeMeals,
      kanbanTasks: normalizeKanbanTasks,
      kanbanCols: normalizeKanbanCols,
      labels: normalizeLabels,
    },
    storage: {
      readJSON,
      writeJSON,
    },
  };

  if (hasWindow && window.performance) {
    performance.mark("zuyu_bootstrap_start");
  }
  if (hasDocument) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init, { once: true });
    } else {
      init();
    }
  }
  root.ZUYU_RUNTIME = runtime;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = runtime;
  }
})(typeof window !== "undefined" ? window : globalThis);
