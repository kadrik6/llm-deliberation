(function () {
  "use strict";

  // Prevent accidental double-submission of the new-deliberation form.
  var newRunForm = document.getElementById("new-run-form");
  if (newRunForm) {
    newRunForm.addEventListener("submit", function () {
      var btn = document.getElementById("submit-btn");
      if (btn) {
        btn.disabled = true;
        btn.textContent = btn.dataset.startingLabel || "Starting...";
      }
    });
  }

  // Live character counter + non-blocking soft/hard length guidance for
  // the Question field -- a UX nudge toward using the separate Context
  // field for background info, never a submission block. The server
  // (service.MAX_QUESTION_LENGTH) is the authoritative check; this is
  // display-only and reads its thresholds/translated text from data-*
  // attributes the template already rendered (see index.html), so no
  // string or number here is hard-coded/duplicated from the template.
  var questionField = document.getElementById("question");
  if (questionField) {
    var questionCount = document.getElementById("question-char-count");
    var questionWarning = document.getElementById("question-warning");
    var recommendedLength = parseInt(questionField.dataset.recommendedLength, 10) || 0;
    var warnThreshold = parseInt(questionField.dataset.warnThreshold, 10) || 0;

    var updateQuestionGuidance = function () {
      var length = questionField.value.length;
      if (questionCount) questionCount.textContent = length;
      if (!questionWarning) return;
      if (length > recommendedLength) {
        questionWarning.textContent = questionField.dataset.warningHard || "";
        questionWarning.hidden = false;
      } else if (length > warnThreshold) {
        questionWarning.textContent = questionField.dataset.warningSoft || "";
        questionWarning.hidden = false;
      } else {
        questionWarning.hidden = true;
        questionWarning.textContent = "";
      }
    };

    questionField.addEventListener("input", updateQuestionGuidance);
    updateQuestionGuidance(); // reflect a value already present (e.g. after a validation error round-trip)
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
  //
  // #pipeline-container is only ever rendered by the server for a
  // non-terminal run (see run_detail.html / presenter.TERMINAL_RUN_STATUSES)
  // -- an already-succeeded or already-failed run uses a different element
  // id specifically so this code never matches it and never opens a
  // connection for a run that is already done. That is the primary guard;
  // the "done" handler below is a second, independent one: even if this
  // ever ran against a run that finished while being watched live, only a
  // "succeeded" transition needs a full reload (to switch from this
  // pipeline view to the result-page layout). A "failed" transition does
  // not: the "pipeline" event just above already replaced this container
  // with the final failed-state markup (error, cost, retry/resume/skip
  // controls), so reloading would do nothing but re-run this exact logic
  // again -- which is precisely how a stuck failed run used to reload
  // itself in an unbounded loop.
  var pipelineContainer = document.getElementById("pipeline-container");
  if (pipelineContainer && window.EventSource) {
    var runId = pipelineContainer.getAttribute("data-run-id");
    var source = new EventSource("/runs/" + runId + "/events");

    source.addEventListener("pipeline", function (event) {
      pipelineContainer.innerHTML = event.data;
    });

    source.addEventListener("done", function (event) {
      source.close();
      if (event.data === "succeeded") {
        window.location.reload();
      }
      // Any other terminal status (currently just "failed"): stay put.
      // The DOM is already up to date and the connection is closed --
      // nothing left to do, and nothing left running.
    });

    source.onerror = function () {
      source.close();
    };
  }
})();
