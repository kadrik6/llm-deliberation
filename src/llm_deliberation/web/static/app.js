(function () {
  "use strict";

  // Prevent accidental double-submission of the new-deliberation form.
  var newRunForm = document.getElementById("new-run-form");
  if (newRunForm) {
    newRunForm.addEventListener("submit", function () {
      var btn = document.getElementById("submit-btn");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Starting...";
      }
    });
  }

  // Disable retry/resume buttons on click so a slow response can't be
  // triggered twice from the same page.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form && form.classList && form.classList.contains("inline-form")) {
      var btn = form.querySelector("button");
      if (btn) btn.disabled = true;
    }
  });

  // Copy the final synthesis text to the clipboard.
  var copyBtn = document.getElementById("copy-answer");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var el = document.getElementById("final-answer-text");
      if (!el || !navigator.clipboard) return;
      navigator.clipboard.writeText(el.textContent).then(function () {
        var original = copyBtn.textContent;
        copyBtn.textContent = copyBtn.dataset.copiedLabel || "Copied";
        setTimeout(function () {
          copyBtn.textContent = original;
        }, 1500);
      });
    });
  }

  // Live pipeline updates via Server-Sent Events, while a run is in
  // progress. Falls back to nothing (the <noscript> meta-refresh in the
  // template covers no-JS clients; a manual page reload covers the rest).
  var pipelineContainer = document.getElementById("pipeline-container");
  if (pipelineContainer && window.EventSource) {
    var runId = pipelineContainer.getAttribute("data-run-id");
    var source = new EventSource("/runs/" + runId + "/events");

    source.addEventListener("pipeline", function (event) {
      pipelineContainer.innerHTML = event.data;
    });

    source.addEventListener("done", function () {
      source.close();
      window.location.reload();
    });

    source.onerror = function () {
      source.close();
    };
  }
})();
