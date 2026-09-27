(function () {
  "use strict";

  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function jobPanel() { return qs("#job-panel"); }
  function jobLog() { return qs("#job-log"); }
  function jobStatusText() { return qs("#job-status-text"); }

  function appendLog(line) {
    var log = jobLog();
    if (!log) return;
    log.hidden = false;
    log.textContent += line + "\n";
    log.scrollTop = log.scrollHeight;
  }

  function setButtonsDisabled(disabled) {
    qsa(".job-form button").forEach(function (btn) { btn.disabled = disabled; });
  }

  function watchStream() {
    if (!jobPanel()) return;
    var es = new EventSource("/jobs/stream");
    es.onmessage = function (ev) { appendLog(ev.data); };
    es.addEventListener("done", function (ev) {
      es.close();
      var payload = {};
      try { payload = JSON.parse(ev.data); } catch (err) { /* ignore */ }
      setButtonsDisabled(false);
      if (payload.idle) return;
      if (jobStatusText()) jobStatusText().textContent = "kein Job aktiv";
      if (payload.report) {
        appendLog("--- " + JSON.stringify(payload.report) + " ---");
      }
      // kurze Pause, damit die letzte Logzeile noch lesbar ist, bevor die
      // Seite neu lädt und aktualisierte Zählwerte/Listen zeigt
      setTimeout(function () { window.location.reload(); }, 1200);
    });
    es.onerror = function () {
      es.close();
      setButtonsDisabled(false);
    };
  }

  function bindJobForms() {
    qsa(".job-form").forEach(function (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        setButtonsDisabled(true);
        if (jobLog()) { jobLog().textContent = ""; }
        if (jobStatusText()) jobStatusText().textContent = "startet …";
        fetch(form.action, { method: "POST", body: new FormData(form) })
          .then(function (resp) { return resp.json(); })
          .then(function (data) {
            if (data && data.ok === false) {
              if (jobStatusText()) jobStatusText().textContent = data.error || "Start fehlgeschlagen";
              setButtonsDisabled(false);
              return;
            }
            if (jobStatusText()) jobStatusText().textContent = "läuft …";
            watchStream();
          })
          .catch(function () {
            if (jobStatusText()) jobStatusText().textContent = "Start fehlgeschlagen";
            setButtonsDisabled(false);
          });
      });
    });
  }

  function bindTitleFilter() {
    var input = qs("#title-filter");
    var table = qs("#transcripts-table");
    if (!input || !table) return;
    input.addEventListener("input", function () {
      var needle = input.value.trim().toLowerCase();
      qsa("tbody tr", table).forEach(function (row) {
        var title = row.querySelector(".title-cell").textContent.toLowerCase();
        row.hidden = needle.length > 0 && title.indexOf(needle) === -1;
      });
    });
  }

  function reconnectIfRunning() {
    var panel = jobPanel();
    if (!panel || panel.dataset.running !== "true") return;
    setButtonsDisabled(true);
    watchStream();
  }

  function bindToTop() {
    var btn = qs("#to-top");
    if (!btn) return;
    var toggle = function () { btn.hidden = window.scrollY < 400; };
    window.addEventListener("scroll", toggle, { passive: true });
    toggle();
    btn.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindJobForms();
    bindToTop();
    bindTitleFilter();
    reconnectIfRunning();
  });
})();
