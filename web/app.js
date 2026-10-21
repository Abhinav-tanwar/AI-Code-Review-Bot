const $ = (id) => document.getElementById(id);
let mode = "diff",
  current = null,
  token = "",
  health = {},
  busy = false;
const showError = (message) => {
  $("error").textContent = message;
  $("error").hidden = !message;
};
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "The request could not be completed. Check the inputs and try again.",
    );
  return body;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
async function refreshHealth() {
  try {
    health = await api("/api/health");
    $("connection").textContent = health.openai_configured
      ? "● AI connected"
      : "● Setup needed";
    $("model-label").textContent =
      `${health.model.toUpperCase()} · STRUCTURED REVIEWS`;
    $("settings-status").replaceChildren(
      element(
        "div",
        `OpenAI: ${health.openai_configured ? "key configured" : "not configured"}`,
      ),
      element(
        "div",
        `GitHub: ${health.github_configured ? "token configured" : "public previews only"}`,
      ),
      element(
        "div",
        `Automatic publication: ${health.auto_publish ? "enabled for allowlisted repositories" : "off"}`,
      ),
    );
  } catch {
    $("connection").textContent = "● Backend offline";
  }
}
function switchMode(next) {
  if (busy) return;
  mode = next;
  $("diff-input").hidden = next !== "diff";
  $("pr-input").hidden = next !== "pr";
  $("diff-tab").classList.toggle("active", next === "diff");
  $("pr-tab").classList.toggle("active", next === "pr");
  $("input-counter").textContent =
    next === "diff"
      ? `${$("diff").value.length.toLocaleString()} characters`
      : "GITHUB API · READ ONLY UNTIL PUBLISHED";
}
function findings() {
  const selected = $("severity").value;
  const list = current.review.findings.filter(
    (f) => selected === "all" || f.severity === selected,
  );
  $("findings").replaceChildren();
  if (!list.length)
    $("findings").append(
      element(
        "p",
        current.review.findings.length
          ? "No findings match this filter."
          : "No validated findings were returned for the reviewed patch. This does not establish that the change is defect-free.",
        "no-findings",
      ),
    );
  for (const finding of list) {
    const card = element("article", undefined, "finding");
    const top = element("div", undefined, "finding-top");
    top.append(
      element("span", finding.severity.toUpperCase()),
      element(
        "small",
        `${finding.category} · ${Math.round(finding.confidence * 100)}% confidence`,
      ),
    );
    card.append(
      top,
      element("h3", finding.title),
      element("code", `${finding.path}:${finding.line}`),
      element("p", finding.explanation),
      element("p", finding.recommendation, "recommendation"),
    );
    $("findings").append(card);
  }
}
function render(result) {
  current = result;
  $("empty-result").hidden = true;
  $("busy-result").hidden = true;
  $("result").hidden = false;
  $("result-state").textContent = "REVIEW COMPLETE";
  $("summary-count").textContent =
    `${result.review.findings.length} validated finding${result.review.findings.length === 1 ? "" : "s"}`;
  $("summary").textContent = result.review.summary;
  $("stats").replaceChildren(
    element("span", `${result.files.length} files reviewed`),
    element(
      "span",
      `${result.files.reduce((n, f) => n + f.added, 0)} added lines`,
    ),
    element("span", result.model),
  );
  $("limitations").replaceChildren(
    ...result.review.limitations.map((x) => element("li", x)),
  );
  $("source-label").textContent = result.source
    ? `${result.source.repository} #${result.source.pull_number} · ${result.source.head_sha.slice(0, 8)}`
    : "Local diff · preview only";
  $("publish-button").hidden = !result.source || !health.github_configured;
  findings();
}
async function selectView(name) {
  if (busy) return;
  for (const view of ["review", "history", "jobs"])
    $(`${view}-view`).hidden = view !== name;
  document
    .querySelectorAll("[data-view]")
    .forEach((node) =>
      node.classList.toggle("active", node.dataset.view === name),
    );
  document.querySelector(".tabs").hidden = name !== "review";
  showError("");
  try {
    if (name === "history") {
      const list = await api("/api/reviews");
      $("history-list").replaceChildren();
      if (!list.length)
        $("history-list").append(
          element("p", "Your first completed review will appear here."),
        );
      for (const item of list) {
        const button = element("button", undefined, "history-card");
        button.append(
          element("strong", item.summary),
          element(
            "small",
            `${new Date(item.created * 1000).toLocaleString()} · ${item.findings} findings`,
          ),
        );
        button.onclick = async () => {
          try {
            const result = await api(`/api/reviews/${item.id}`);
            await selectView("review");
            render(result);
          } catch (e) {
            showError(e.message);
          }
        };
        $("history-list").append(button);
      }
    }
    if (name === "jobs") {
      const jobs = await api("/api/jobs");
      $("jobs-list").replaceChildren();
      if (!jobs.length)
        $("jobs-list").append(
          element("p", "No signed webhook events have been received."),
        );
      for (const job of jobs) {
        const card = element("article", undefined, "job");
        card.append(
          element("strong", `${job.repo} #${job.number}`),
          element("p", job.head),
          element("span", `${job.status}${job.error ? ` · ${job.error}` : ""}`),
        );
        if (job.status === "failed" && job.attempts < 3) {
          const retry = element("button", "Retry review", "secondary");
          retry.onclick = async () => {
            try {
              await api(`/api/jobs/${job.id}/retry`, { method: "POST" });
              await selectView("jobs");
            } catch (e) {
              showError(e.message);
            }
          };
          card.append(retry);
        }
        $("jobs-list").append(card);
      }
    }
  } catch (e) {
    showError(e.message);
  }
}
$("diff-tab").onclick = () => switchMode("diff");
$("pr-tab").onclick = () => switchMode("pr");
$("diff").oninput = () => {
  $("input-counter").textContent =
    `${$("diff").value.length.toLocaleString()} characters`;
};
$("sample-button").onclick = async () => {
  if (busy) return;
  try {
    const sample = await api("/api/example");
    switchMode("diff");
    $("diff").value = sample.diff;
    $("diff").dispatchEvent(new Event("input"));
  } catch (e) {
    showError(e.message);
  }
};
$("review-button").onclick = async () => {
  if (busy) return;
  showError("");
  const payload =
    mode === "diff"
      ? { diff: $("diff").value }
      : {
          repository: $("repository").value.trim(),
          pull_number: Number($("pull-number").value),
        };
  if (mode === "diff" && !payload.diff.trim()) {
    showError("Paste a unified diff or load the sample first.");
    return;
  }
  busy = true;
  $("review-button").disabled = true;
  $("sample-button").disabled = true;
  $("empty-result").hidden = true;
  $("result").hidden = true;
  $("busy-result").hidden = false;
  $("result-state").textContent = "IN PROGRESS";
  try {
    render(
      await api("/api/reviews", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    );
  } catch (e) {
    showError(e.message);
    $("busy-result").hidden = true;
    $("empty-result").hidden = !!current;
    $("result").hidden = !current;
    $("result-state").textContent = current
      ? "PREVIOUS REVIEW"
      : "REQUEST FAILED";
  } finally {
    busy = false;
    $("review-button").disabled = false;
    $("sample-button").disabled = false;
  }
};
$("severity").onchange = findings;
$("download-button").onclick = () => {
  if (!current) return;
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(current, null, 2)], { type: "application/json" }),
  );
  const anchor = element("a");
  anchor.href = url;
  anchor.download = "code-review.json";
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$("publish-button").onclick = async () => {
  if (!current?.source || busy) return;
  const target = `${current.source.repository} #${current.source.pull_number}`;
  if (
    !confirm(
      `Post this advisory review and its inline findings to ${target}? This notifies pull request participants.`,
    )
  )
    return;
  $("publish-button").disabled = true;
  try {
    const result = await api(`/api/reviews/${current.id}/publish`, {
      method: "POST",
    });
    $("source-label").textContent =
      `${result.duplicate ? "Existing review retained" : "Review published"} · ${target}`;
  } catch (e) {
    showError(e.message);
  } finally {
    $("publish-button").disabled = false;
  }
};
document.querySelectorAll("[data-view]").forEach((node) => {
  node.onclick = () => selectView(node.dataset.view);
});
$("refresh-jobs").onclick = () => selectView("jobs");
$("settings-button").onclick = () => {
  refreshHealth();
  $("settings").showModal();
};
$("save-settings").onclick = () => {
  token = $("api-token").value.trim();
  $("api-token").value = "";
  $("settings").close();
  refreshHealth();
};
refreshHealth();
