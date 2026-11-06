(() => {
  const $ = (id) => document.getElementById(id);
  const state = { config: null, session: null, user: null, authorName: "", teamKey: "", tasks: [], summary: {}, current: null, labels: [], filter: "", status: "all", heartbeat: null, hfReadOnly: false, previewQueue: null, previewUnit: null };
  const statuses = { queued: "대기", in_progress: "작업 중", completed: "완료", needs_adjudication: "판정 보류" };
  const axes = [
    ["gold_violation", "정책 위반 시도"],
    ["violation_executed", "실제 위반 실행"],
    ["tool_call_error", "도구 호출 오류"],
  ];

  function toast(message) {
    const node = $("toast"); node.textContent = message; node.classList.add("show");
    clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.remove("show"), 3000);
  }
  function show(view) {
    ["setup-state", "author-view", "dashboard-view"].forEach((id) => $(id).classList.toggle("hidden", id !== view));
  }
  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { "content-type": "application/json", ...(state.authorName ? { "x-author-name": state.authorName } : {}), ...(state.teamKey ? { "x-team-access-key": state.teamKey } : {}), ...(options.headers || {}) } });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `요청 오류 (${response.status})`);
    return result;
  }
  function setOnline(online) { $("connection").classList.toggle("online", online); $("connection").innerHTML = `<i></i>${online ? "HF sidecar 연결됨" : "HF 원본 목록 · 읽기 전용"}`; }
  async function loadOctoberReference() {
    const root = $("october-reference-content");
    try {
      const response = await fetch("/october-review-reference.json", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const ref = await response.json();
      const defs = ref.field_definitions || {};
      const labelText = (sample) => [sample.labels.gold_violation, sample.labels.violation_executed, sample.labels.tool_call_error].map((value) => value == null ? "null" : value).join(" / ");
      root.innerHTML = `<div class="reference-summary">${escapeHtml(ref.source)} · 전체 ${Number(ref.summary.total_samples).toLocaleString("ko-KR")}개 중 직접 사람 확인 ${Number(ref.summary.human_confirmed).toLocaleString("ko-KR")}개 · 위임 판정 ${Number(ref.summary.user_delegated).toLocaleString("ko-KR")}개 · 확정 규칙 ${Number(ref.summary.adjudicated_rules).toLocaleString("ko-KR")}개. 아래 사례는 직접 사람 확인 및 보류 기록에서 가져왔습니다.<br/>직접 확인 라벨 분포: ${Object.entries(ref.summary.human_label_tuple_distribution || {}).map(([key,value])=>`${escapeHtml(key)} ${value}`).join(" · ")}<br/>추가 기록: ${Object.entries(ref.summary.human_additional_field_counts || {}).map(([key,value])=>`${escapeHtml(key)} ${value}/${ref.summary.human_confirmed}`).join(" · ")}</div>
        <div class="reference-definitions">${["gold_violation", "violation_executed", "tool_call_error"].map((key) => `<article><strong>${escapeHtml(key)}</strong><p>${escapeHtml(defs[key] || "")}</p></article>`).join("")}</div>
        <h3>사람 검수 사례</h3><div class="reference-examples">${(ref.examples || []).map(({title, sample}) => sample ? `<article class="reference-example"><div class="reference-example-head"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(labelText(sample))}</span></div><div class="reference-meta">${escapeHtml(sample.scenario_id || "")} · step ${sample.step_index} · ${escapeHtml(sample.review_status || "")} · ${escapeHtml(sample.reviewer || "")}</div><p>${escapeHtml(sample.reason || "")}</p>${sample.review_notes?.length ? `<ul>${sample.review_notes.map((note) => `<li><strong>${escapeHtml(note.issue || "검수 메모")}</strong> · ${escapeHtml(note.detail || "")}</li>`).join("")}</ul>` : ""}<details><summary>행동과 근거 증거</summary><pre>${escapeHtml(JSON.stringify({task:sample.task,policy:sample.policy,command:sample.command,result:sample.result,observation:sample.observation,pre_state_evidence:sample.pre_state_evidence},null,2))}</pre></details></article>` : "").join("")}</div>
        <h3>10월에 확정된 판정 규칙</h3><p class="reference-caveat">아래 규칙은 범위 메모가 붙은 사례별 선례입니다. 범위를 벗어난 11월 행동에 자동 적용하지 말고, 새 예외는 근거 메모로 남겨 주세요.</p><div class="reference-rules">${(ref.adjudicated_rules || []).map((rule) => `<details><summary>${escapeHtml(rule.rule_id)}${rule.title ? ` · ${escapeHtml(rule.title)}` : ""}</summary><p>${escapeHtml(rule.description || "")}</p><small>적용 범위: ${escapeHtml(rule.scope_note || "범위 메모 없음")}</small></details>`).join("")}</div>
        ${ref.revision_example ? `<div class="reference-revision"><strong>재검토 기록 예시 · case ${escapeHtml(ref.revision_example.case_number)}</strong><p>이전 라벨 ${escapeHtml(ref.revision_example.previous_label.join("/"))} → 제안 ${escapeHtml(ref.revision_example.proposed_label.join("/"))}. 상태: ${escapeHtml(ref.revision_example.proposal_status)} · 실제 변경 적용: ${ref.revision_example.label_changes_applied ? "예" : "아니오"}.</p><p>${escapeHtml(ref.revision_example.change_reason)}</p></div>` : ""}`;
    } catch (error) {
      root.textContent = `10월 참고 자료를 불러오지 못했습니다: ${error.message}`;
    }
  }
  function setUserChrome() {
    const logged = Boolean(state.authorName);
    $("user-chip").classList.toggle("hidden", !logged); $("signout").classList.toggle("hidden", !logged);
    $("download-sidecar").classList.toggle("hidden", !logged || state.hfReadOnly);
    $("publish-sidecar").classList.add("hidden");
    $("author-chip").classList.toggle("hidden", !state.authorName);
    $("change-author").classList.toggle("hidden", !state.authorName || !logged);
    if (logged) $("user-chip").textContent = state.authorName;
    if (state.authorName) $("author-chip").textContent = `작성자 · ${state.authorName}`;
  }

  async function openWorkspace() {
    setUserChrome();
    if (!state.authorName) { $("author-name").value = ""; $("author-error").textContent = ""; show("author-view"); return; }
    show("dashboard-view"); await loadDashboard();
  }

  async function start() {
    void loadOctoberReference();
    state.authorName = localStorage.getItem("argus-label-author") || "";
    state.teamKey = sessionStorage.getItem("argus-label-team-key") || "";
    if (state.authorName) state.user = { displayName: state.authorName };
    try {
      const configResponse = await fetch("/api/config", { cache: "no-store" });
      if (!configResponse.ok) throw new Error("Shared API is unavailable");
      state.config = await configResponse.json();
      if (!state.config.ready) throw new Error("Vercel에 HF_TOKEN과 DASHBOARD_ACCESS_KEY를 설정해야 공동 작업을 사용할 수 있습니다.");
      setOnline(true); setUserChrome();
      if (state.authorName && state.teamKey) {
        try { await openWorkspace(); }
        catch { state.authorName = ""; state.user = null; setUserChrome(); $("author-name").value = localStorage.getItem("argus-label-author") || ""; $("author-error").textContent = "팀 접근키를 확인할 수 없습니다. 다시 입력해 주세요."; show("author-view"); }
      } else { $("author-name").value = state.authorName; show("author-view"); }
    } catch { await startHFQueue(); }
  }
  async function startHFQueue() {
    state.hfReadOnly = true;
    setOnline(false); setUserChrome();
    try {
      const repo = "hakuuung/gpt-oss-20b-hidden-state-raw-data";
      const response = await fetch(`https://huggingface.co/datasets/${repo}/resolve/main/manifest/units.json?download=true`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HF manifest 요청 실패 (${response.status})`);
      const manifest = await response.json();
      const units = manifest?.units?.scenario_rollout || {};
      const tasks = Object.entries(units)
        .filter(([, unit]) => String(unit.batch || "").slice(0, 8) >= "20261102")
        .map(([unit_id, unit]) => {
          const match = unit_id.match(/^(AV-S\d+)-(C\d+)-s(\d+)-/i);
          return {
            unit_id,
            scenario_id: match?.[1] || unit_id,
            condition: match?.[2] || "unknown",
            seed: Number(match?.[3] || 0),
            batch: unit.batch || "",
            status: "queued",
          };
        })
        .sort((a, b) => a.scenario_id.localeCompare(b.scenario_id) || a.condition.localeCompare(b.condition) || a.seed - b.seed || a.unit_id.localeCompare(b.unit_id));
      state.tasks = tasks;
      state.summary = { total: tasks.length, queued: tasks.length, in_progress: 0, completed: 0, needs_adjudication: 0 };
      $("source-banner").textContent = `Hugging Face 원본 목록 · ${tasks.length.toLocaleString("ko-KR")}개 rollout · 2026-11-02 이후 · ${repo} · 읽기 전용`;
      $("source-banner").classList.remove("hidden");
      show("dashboard-view"); renderStats(); renderQueue();
    } catch (error) {
      $("setup-state").querySelector("h1").textContent = "Hugging Face 자료를 불러오지 못했습니다";
      $("setup-state").querySelector("p").textContent = `${error.message}. 네트워크 연결을 확인한 뒤 새로고침해 주세요.`;
      setOnline(false); show("setup-state");
    }
  }
  async function loadDashboard() {
    const data = await api("/api/dashboard");
    state.hfReadOnly = false; state.tasks = data.tasks || []; state.summary = data.summary || {};
    renderStats(); renderQueue();
  }
  function renderStats() {
    for (const [id, key] of [["stat-total","total"],["stat-queued","queued"],["stat-active","in_progress"],["stat-completed","completed"],["stat-hold","needs_adjudication"]]) $(id).textContent = Number(state.summary[key] || 0).toLocaleString("ko-KR");
  }
  function renderQueue() {
    const list = $("queue-list");
    const filtered = state.tasks.filter((t) => (state.status === "all" || t.status === state.status) && `${t.scenario_id} ${t.unit_id}`.toLowerCase().includes(state.filter.toLowerCase()));
    $("queue-count").textContent = filtered.length.toLocaleString("ko-KR");
    if (!filtered.length) { list.innerHTML = '<div class="empty-state">조건에 맞는 작업이 없습니다.</div>'; return; }
    list.innerHTML = filtered.map((t) => {
      const author = t.status === "in_progress" ? t.assigned_author_name : t.last_labeler_name;
      const dispositionLabel = t.review_disposition === "quarantined" ? "격리 · 조사 필요" : t.review_disposition === "excluded" ? "사용 제외" : null;
      return `<article class="queue-item ${state.current?.unit_id === t.unit_id ? "current" : ""}" data-unit="${escapeAttr(t.unit_id)}"><div><div class="queue-id">${escapeHtml(t.scenario_id)} · ${escapeHtml(t.condition)} · s${t.seed}</div><div class="queue-meta">${escapeHtml(t.unit_id)}${author ? ` · 작성자 ${escapeHtml(author)}` : ""}${dispositionLabel ? `<br/>${escapeHtml(dispositionLabel)} · ${escapeHtml(t.disposition_note || "")}` : ""}</div></div><div class="queue-controls"><span class="state ${dispositionLabel ? "state-needs_adjudication" : `state-${t.status}`}" title="${escapeAttr(dispositionLabel || statuses[t.status] || t.status)}">${escapeHtml(dispositionLabel || statuses[t.status] || t.status)}</span><button class="preview-button" type="button" data-preview-unit="${escapeAttr(t.unit_id)}">미리보기</button></div></article>`;
    }).join("");
  }
  async function previewUnit(unit_id) {
    const dialog = $("preview-dialog"); const content = $("preview-content"); const task = state.tasks.find((item) => item.unit_id === unit_id);
    if (!task || !dialog) return;
    content.innerHTML = '<div class="empty-state">원본 rollout 자료를 읽는 중…</div>';
    $("preview-claim").disabled = true; $("preview-claim").textContent = "불러오는 중…";
    dialog.showModal();
    try {
      let data;
      if (state.hfReadOnly) {
        if (!state.previewQueue) {
          const repo = "hakuuung/gpt-oss-20b-hidden-state-raw-data";
          const response = await fetch(`https://huggingface.co/datasets/${repo}/resolve/main/label_review/ArgusV_november_queue.json?download=true`, { cache: "no-store" });
          if (!response.ok) throw new Error("HF queue sidecar가 아직 준비되지 않았습니다.");
          state.previewQueue = await response.json();
        }
        const sourceEntry = (state.previewQueue.tasks || []).find((item) => item.unit_id === unit_id);
        if (!sourceEntry) throw new Error("이 rollout의 미리보기 자료가 queue sidecar에 없습니다.");
        let source = sourceEntry;
        if (sourceEntry.queue_shard) {
          const shardResponse = await fetch(`https://huggingface.co/datasets/${repo}/resolve/main/${sourceEntry.queue_shard}?download=true`, { cache: "no-store" });
          if (!shardResponse.ok) throw new Error("HF rollout 미리보기 shard를 불러오지 못했습니다.");
          const shard = await shardResponse.json();
          source = (shard.tasks || []).find((item) => item.unit_id === unit_id);
          if (!source) throw new Error("rollout 미리보기 shard에서 작업을 찾을 수 없습니다.");
          source.source_revision ||= sourceEntry.source_revision;
        }
        const { rollout, stepFiles, taskContext } = parseRollout(source);
        data = { unit_id, scenario_id: rollout.scenario_id || source.scenario_id, condition: rollout.condition || source.condition,
          seed: rollout.seed ?? source.seed, source_revision: source.source_revision || state.previewQueue.source_revision,
          status: task.status, review_disposition: task.review_disposition || "normal", assigned_author_name: task.assigned_author_name || "",
          task: rollout.task || rollout.public_context?.goal || taskContext.task || "작업 설명 없음",
          trusted_policy: rollout.trusted_policy || (rollout.public_context?.policy || []).join("\n") || taskContext.trusted_policy || "정책 설명 없음",
          step_count: stepFiles.length, steps: stepFiles.map(({ix,json}) => ({ step_index: ix,
            action_id: json.action_id || null, kind: json.kind || json.tool_name || json.tool || json.action?.tool || null,
            summary: [json.command,json.final_text,json.output?.action_text,json.observation,json.result,json.tool_result].filter(Boolean).map((value)=>typeof value === "string" ? value : JSON.stringify(value)).join("\n\n"),
            policy_label: json.policy_label ?? json.original_policy_label ?? null, evidence: json })) };
      } else data = await api("/api/preview", { method: "POST", body: JSON.stringify({ unit_id }) });
      state.previewUnit = unit_id;
      const canClaim = !state.hfReadOnly && data.review_disposition === "normal" && ["queued", "needs_adjudication"].includes(data.status);
      $("preview-claim").disabled = !canClaim;
      $("preview-claim").textContent = canClaim ? "이 rollout 맡기" : state.hfReadOnly ? "미리보기 전용 · Vercel에서 작업 배정" : data.status === "in_progress" ? `${data.assigned_author_name || "다른 팀원"} 작업 중` : "작업할 수 없음";
      content.innerHTML = `<div class="preview-meta">${escapeHtml(data.scenario_id)} · ${escapeHtml(data.condition)} · seed ${data.seed} · ${escapeHtml(data.unit_id)}<br/>HF source revision · ${escapeHtml(data.source_revision || "미기록")} · ${data.step_count}개 행동</div><div class="preview-context"><h3>Task</h3><p>${escapeHtml(data.task)}</p><h3>Trusted policy</h3><p>${escapeHtml(data.trusted_policy)}</p></div><h3 class="preview-step-heading">행동 자료 미리보기</h3><div class="preview-steps">${(data.steps || []).map((step) => `<article class="preview-step"><header><strong>STEP ${String(step.step_index).padStart(3,"0")}${step.kind ? ` · ${escapeHtml(step.kind)}` : ""}</strong><span>${escapeHtml(step.action_id || "action ID 없음")}</span></header>${step.policy_label != null ? `<div class="source-label">HF 제안 라벨 참고 · ${escapeHtml(step.policy_label)}</div>` : ""}<p>${escapeHtml(step.summary || "요약 텍스트 없음")}</p><details><summary>원본 step JSON 보기</summary><pre>${escapeHtml(JSON.stringify(step.evidence,null,2))}</pre></details></article>`).join("") || '<div class="empty-state">step 자료가 없습니다.</div>'}</div>`;
    } catch (error) { content.innerHTML = `<div class="empty-state">미리보기를 불러오지 못했습니다: ${escapeHtml(error.message)}</div>`; }
  }
  async function claimNext() {
    if (state.hfReadOnly) { toast("HF 원본 목록은 읽기 전용입니다. 작업 저장에는 Vercel API와 HF_TOKEN 설정이 필요합니다."); return; }
    try {
      const data = await api("/api/claim", { method: "POST", body: "{}" });
      if (!data.task) { toast("배정 가능한 작업이 없습니다."); return; }
      state.current = data.task; state.labels = data.labels || []; state.trajectoryLabel = data.trajectory_label || null; renderTask(); await loadDashboard(); startHeartbeat();
    } catch (error) { toast(error.message); }
  }
  async function claimSelected(unit_id) {
    if (state.hfReadOnly) { toast("HF 원본 목록은 읽기 전용입니다. 작업 저장에는 Vercel API와 HF_TOKEN 설정이 필요합니다."); return; }
    try {
      const data = await api("/api/claim", { method: "POST", body: JSON.stringify({ unit_id }) });
      if (!data.task) { toast("이미 다른 팀원이 예약했거나 작업 가능한 상태가 아닙니다."); await loadDashboard(); return false; }
      state.current = data.task; state.labels = data.labels || []; state.trajectoryLabel = data.trajectory_label || null;
      renderTask(); await loadDashboard(); startHeartbeat();
      return true;
    } catch (error) { toast(error.message); return false; }
  }
  async function startHeartbeat() {
    clearInterval(state.heartbeat);
    state.heartbeat = setInterval(async () => {
      if (!state.current) return;
      try { await api("/api/heartbeat", { method: "POST", body: JSON.stringify({ unit_id: state.current.unit_id, lease_token: state.current.lease_token }) }); }
      catch { toast("예약이 만료됐습니다. 초안을 저장하고 다시 배정받아 주세요."); clearInterval(state.heartbeat); }
    }, 180_000);
  }
  function parseRollout(task) {
    const files = task.payload?.files || [];
    const rolloutFile = files.find((f) => f.path === "rollout/rollout.json");
    let rollout = task.payload?.rollout || {};
    if (!Object.keys(rollout).length) { try { rollout = JSON.parse(rolloutFile?.text || "{}"); } catch {} }
    const stepFiles = files.filter((f) => /^rollout\/steps\/step_\d+\/step\.json$/.test(f.path)).map((f) => {
      let json = {}; try { json = JSON.parse(f.text || "{}"); } catch { json = { raw: f.text }; }
      const name = f.path.match(/step_(\d+)\/step\.json$/); const ix = Number(name?.[1] ?? json.step_index ?? json.index ?? 0);
      return { ix, path: f.path, json };
    }).sort((a,b) => a.ix-b.ix);
    const contextFile = files.find((f) => /^rollout\/steps\/step_\d+\/m1-context\.json$/.test(f.path));
    let taskContext = {}; try { taskContext = JSON.parse(contextFile?.text || "{}"); } catch {}
    return { rollout, stepFiles, taskContext };
  }
  function renderTask() {
    if (!state.current) return;
    $("task-empty").classList.add("hidden"); const root = $("task-work"); root.classList.remove("hidden");
    const { task } = state.current; const { rollout, stepFiles, taskContext } = parseRollout(task);
    const byStep = new Map((state.labels || []).map((l) => [Number(l.step_index), l]));
    state.labelDrafts = stepFiles.map(({ ix, json }) => {
      const saved = byStep.get(ix) || {};
      const stableActionId = `${rollout.rollout_id || rollout.trajectory_id || task.unit_id}:${ix}`;
      return { step_index: ix, action_id: saved.action_id || json.action_id || stableActionId,
        gold_violation: saved.gold_violation == null ? "" : String(saved.gold_violation),
        violation_executed: saved.violation_executed == null ? "" : String(saved.violation_executed),
        tool_call_error: saved.tool_call_error == null ? "" : String(saved.tool_call_error),
        error_type: saved.error_type || "",
        review_notes_text: Array.isArray(saved.review_notes) ? saved.review_notes.map((x)=>x.detail||"").filter(Boolean).join("\n") : "",
        exception_notes_text: Array.isArray(saved.exception_notes) ? saved.exception_notes.map((x)=>x.detail||"").filter(Boolean).join("\n") : "",
        pre_state_evidence_text: Array.isArray(saved.pre_state_evidence) ? saved.pre_state_evidence.map((x)=>x.detail||JSON.stringify(x)).filter(Boolean).join("\n") : "",
        computed_check_text: saved.computed_check ? JSON.stringify(saved.computed_check) : "",
        scenario_definition_evidence_text: Array.isArray(saved.scenario_definition_evidence) ? saved.scenario_definition_evidence.map((x)=>x.detail||JSON.stringify(x)).filter(Boolean).join("\n") : "",
        public_contract_text: Array.isArray(saved.public_contract) ? saved.public_contract.map((x)=>x.detail||JSON.stringify(x)).filter(Boolean).join("\n") : "",
        suggested_status: saved.suggested_status || "",
        review_status: saved.review_status || "draft", notes: saved.notes || "", author_name: saved.author_name || "" };
    });
    const trajectorySaved = state.trajectoryLabel || {};
    state.trajectoryDraft = { trajectory_has_violation: trajectorySaved.trajectory_has_violation == null ? "" : String(trajectorySaved.trajectory_has_violation), review_status: trajectorySaved.review_status || "draft", notes: trajectorySaved.notes || "", author_name: trajectorySaved.author_name || "", evidence_action_id: trajectorySaved.evidence_action_id || "" };
    const sc = rollout.scenario_id || task.scenario_id; const cond = rollout.condition || task.condition; const seed = rollout.seed ?? task.seed;
    root.innerHTML = `<div class="task-header"><div><div class="eyebrow">라벨 작성자 · ${escapeHtml(state.authorName)}</div><h2 class="task-title">${escapeHtml(task.unit_id)}</h2><div class="task-subtitle"><span class="condition-tag">${escapeHtml(sc)}</span><span class="condition-tag">${escapeHtml(cond)}</span><span class="condition-tag">seed ${seed}</span></div></div><div class="task-actions"><button id="save-draft" class="button button-quiet">초안 저장</button><button id="quarantine-task" class="button button-quiet">전체 rollout 격리</button><button id="exclude-task" class="button button-quiet">작업에서 제외</button><button id="release-task" class="button button-quiet">반납</button></div></div><div class="disposition-note-wrap"><textarea id="disposition-note" placeholder="전체 rollout 격리/제외 사유 (환경 불일치, 원본 손상 등)"></textarea></div>
      <div class="task-body"><div class="context-grid"><div class="context-card"><h3>Task</h3><p>${escapeHtml(rollout.task || rollout.public_context?.goal || taskContext.task || "작업 설명 없음")}</p></div><div class="context-card"><h3>Trusted policy</h3><p>${escapeHtml(rollout.trusted_policy || (rollout.public_context?.policy || []).join("\n") || taskContext.trusted_policy || "정책 설명 없음")}</p></div></div><div class="steps-title"><h3>행동별 판정</h3><small>${stepFiles.length}개 step · 10월 기준과 동일한 3개 독립 라벨</small></div><div id="action-cards">${stepFiles.map(({ix,json,path}) => renderAction(ix,json,path)).join("") || '<div class="empty-state">step 기록을 찾지 못했습니다. 원본 rollout 파일을 확인해 주세요.</div>'}</div><div class="submit-bar"><span class="lease-note">25분 예약 · 3분마다 자동 연장</span><button id="submit-task" class="button button-primary">검수 제출 <span>→</span></button></div></div>`;
    root.querySelectorAll("[data-label]").forEach((el) => el.addEventListener("change", onLabelChange));
    root.querySelectorAll("[data-note]").forEach((el) => el.addEventListener("input", onLabelChange));
    root.querySelectorAll("[data-review-notes], [data-pre-state], [data-computed-check], [data-scenario-evidence], [data-public-contract]").forEach((el) => el.addEventListener("input", onLabelChange));
    root.querySelectorAll("[data-hold]").forEach((el) => el.addEventListener("change", onLabelChange));
    root.querySelector(".submit-bar").insertAdjacentHTML("beforebegin", `<section class="trajectory-card"><div class="eyebrow">TRAJECTORY SUMMARY · 선택 입력</div>${state.trajectoryDraft.author_name ? `<div class="label-author">라벨 작성자 · ${escapeHtml(state.trajectoryDraft.author_name)}</div>` : ""}<h3>실행 전체 요약을 추가하시겠습니까?</h3><p>10월 sidecar의 필수 라벨은 행동별 세 축입니다. 아래 실행 전체 요약은 추가 정보이며 제출에 필수는 아닙니다. 위반 있음이면 근거 행동 ID를 직접 선택하세요.</p><div class="trajectory-controls"><select id="trajectory-label"><option value="">요약하지 않음</option><option value="0" ${state.trajectoryDraft.trajectory_has_violation==="0"?"selected":""}>0 · 위반 없음</option><option value="1" ${state.trajectoryDraft.trajectory_has_violation==="1"?"selected":""}>1 · 위반 있음</option></select><label><input id="trajectory-hold" type="checkbox" ${state.trajectoryDraft.review_status==="hold"?"checked":""}/> 불명확 · 보류</label></div><select id="trajectory-evidence" class="trajectory-evidence"><option value="">근거 행동 ID 선택 (위반 있음일 때)</option>${state.labelDrafts.filter((d)=>d.gold_violation==="1").map((d)=>`<option value="${escapeAttr(d.action_id)}" ${state.trajectoryDraft.evidence_action_id===d.action_id?"selected":""}>${escapeHtml(d.action_id)} · step ${d.step_index}</option>`).join("")}</select><textarea id="trajectory-notes" class="notes" placeholder="선택한 요약의 근거 / 범위 메모">${escapeHtml(state.trajectoryDraft.notes)}</textarea></section>`);
    $("trajectory-label").onchange = (e) => { state.trajectoryDraft.trajectory_has_violation = e.target.value; state.trajectoryDraft.review_status = e.target.value === "" ? "draft" : "confirmed"; };
    $("trajectory-evidence").onchange = (e) => { state.trajectoryDraft.evidence_action_id = e.target.value; };
    $("trajectory-hold").onchange = (e) => { state.trajectoryDraft.review_status = e.target.checked ? "hold" : "draft"; };
    $("trajectory-notes").oninput = (e) => { state.trajectoryDraft.notes = e.target.value; };
    $("save-draft").onclick = () => saveLabels(false); $("submit-task").onclick = () => saveLabels(true); $("release-task").onclick = releaseTask;
    $("quarantine-task").onclick = () => disposeTask("quarantined"); $("exclude-task").onclick = () => disposeTask("excluded");
  }
  function renderAction(ix, json, path) {
    const d = state.labelDrafts.find((x) => x.step_index === ix);
    const summary = [json.kind && `도구 · ${json.kind}`, json.command, json.final_text, json.output?.action_text, json.observation, json.result, json.tool_result].filter(Boolean).map((x) => typeof x === "string" ? x : JSON.stringify(x)).join("\n\n");
    const sourceLabel = json.policy_label == null ? "" : `<div class="source-label">HF 규칙 라벨 참고값 · ${escapeHtml(json.policy_label)} · 사람 라벨을 대신하지 않습니다</div>`;
    const raw = JSON.stringify(json, null, 2);
    const select = (field, label) => `<div class="label-field"><label>${label}</label><select data-label="${field}" data-step="${ix}"><option value="">선택</option><option value="0" ${d[field]==="0"?"selected":""}>0 · 아니오 / 없음</option><option value="1" ${d[field]==="1"?"selected":""}>1 · 예</option></select></div>`;
    return `<article class="action-card"><div class="action-head"><strong>STEP ${String(ix).padStart(3,"0")}</strong><span class="state ${d.review_status === "hold" ? "state-needs_adjudication" : d.review_status === "confirmed" ? "state-completed" : "state-queued"}">${d.review_status === "hold" ? "보류" : d.review_status === "confirmed" ? "작성됨" : "미작성"}</span></div><div class="step-content">${d.author_name ? `<div class="label-author">라벨 작성자 · ${escapeHtml(d.author_name)}</div>` : ""}<div class="step-summary">${escapeHtml(summary || "행동 상세는 아래 원본 기록에서 확인")}</div>${sourceLabel}<details class="step-json"><summary>전체 step 증거 열기 · ${escapeHtml(path)}</summary><pre>${escapeHtml(raw)}</pre></details><div class="label-grid">${axes.map(([key,label])=>select(key,label)).join("")}</div><div class="label-field error-type-field"><label>오류 유형 · 도구 호출 오류가 1일 때</label><select data-label="error_type" data-step="${ix}"><option value="">선택 안 함</option>${["INVALID_STATUS","NONCANONICAL_PATH","UNKNOWN_TOOL_CALL_FORMAT","UNKNOWN_ARGUMENT","WRONG_TOOL_ARGUMENT_SCHEMA","WRONG_TOOL_SELECTION","TIMESTAMP_FORMAT","OTHER"].map(v=>`<option value="${v}" ${d.error_type===v?"selected":""}>${v}</option>`).join("")}</select></div><textarea class="notes" data-note="1" data-step="${ix}" placeholder="판정 근거 · 10월 sidecar의 reason에 해당">${escapeHtml(d.notes)}</textarea><textarea class="notes" data-review-notes="1" data-step="${ix}" placeholder="추가 검수 메모 · 쟁점과 적용 범위">${escapeHtml(d.review_notes_text)}</textarea><textarea class="notes" data-exception-notes="1" data-step="${ix}" placeholder="예외 메모 · 일반 규칙과 다른 사유 (선택)">${escapeHtml(d.exception_notes_text)}</textarea><textarea class="notes" data-pre-state="1" data-step="${ix}" placeholder="판정 시점까지의 사전 상태 증거 · 앞선 행동/파일/정책 근거">${escapeHtml(d.pre_state_evidence_text)}</textarea><textarea class="notes" data-computed-check="1" data-step="${ix}" placeholder="재현 가능한 계산·검증 결과 (선택)">${escapeHtml(d.computed_check_text)}</textarea><textarea class="notes" data-scenario-evidence="1" data-step="${ix}" placeholder="시나리오 정의상 근거 (선택)">${escapeHtml(d.scenario_definition_evidence_text)}</textarea><textarea class="notes" data-public-contract="1" data-step="${ix}" placeholder="공개 tool/API 계약 근거 (선택)">${escapeHtml(d.public_contract_text)}</textarea><input class="suggested-status" data-label="suggested_status" data-step="${ix}" value="${escapeAttr(d.suggested_status)}" placeholder="예상 상태값 (선택)"/><label class="review-hold"><input type="checkbox" data-hold="1" data-step="${ix}" ${d.review_status === "hold" ? "checked" : ""}/> 불명확 · 2차 판정 보류</label></div></article>`;
  }
  function onLabelChange(event) {
    const ix = Number(event.currentTarget.dataset.step); const draft = state.labelDrafts.find((x) => x.step_index === ix); if (!draft) return;
    if (event.currentTarget.dataset.label) draft[event.currentTarget.dataset.label] = event.currentTarget.value;
    if (event.currentTarget.dataset.note) draft.notes = event.currentTarget.value;
    if (event.currentTarget.dataset.reviewNotes) draft.review_notes_text = event.currentTarget.value;
    if (event.currentTarget.dataset.exceptionNotes) draft.exception_notes_text = event.currentTarget.value;
    if (event.currentTarget.dataset.preState) draft.pre_state_evidence_text = event.currentTarget.value;
    if (event.currentTarget.dataset.computedCheck) draft.computed_check_text = event.currentTarget.value;
    if (event.currentTarget.dataset.scenarioEvidence) draft.scenario_definition_evidence_text = event.currentTarget.value;
    if (event.currentTarget.dataset.publicContract) draft.public_contract_text = event.currentTarget.value;
    if (event.currentTarget.dataset.hold) draft.review_status = event.currentTarget.checked ? "hold" : "draft";
    else if (draft.review_status === "draft" && draft.gold_violation !== "" && draft.violation_executed !== "" && draft.tool_call_error !== "") draft.review_status = "confirmed";
    refreshTrajectoryEvidenceOptions();
  }
  function refreshTrajectoryEvidenceOptions() {
    const select = $("trajectory-evidence");
    if (!select) return;
    const selected = state.trajectoryDraft.evidence_action_id;
    const eligible = state.labelDrafts.filter((d) => d.gold_violation === "1" && d.review_status !== "hold");
    select.innerHTML = `<option value="">근거 행동 ID 선택 (위반 있음일 때)</option>${eligible.map((d)=>`<option value="${escapeAttr(d.action_id)}">${escapeHtml(d.action_id)} · step ${d.step_index}</option>`).join("")}`;
    if (eligible.some((d) => d.action_id === selected)) select.value = selected;
    else state.trajectoryDraft.evidence_action_id = "";
  }
  async function saveLabels(finalize) {
    try {
      if (finalize) {
        if (state.labelDrafts.length === 0) { toast("행동 step을 찾지 못해 제출할 수 없습니다."); return false; }
        const incomplete = state.labelDrafts.filter((d) => d.review_status !== "hold" && [d.gold_violation,d.violation_executed,d.tool_call_error].some((v) => v === ""));
        if (incomplete.length) { toast(`step ${incomplete[0].step_index}의 3개 라벨을 모두 선택하거나 보류하세요.`); return false; }
        const noEvidence = state.labelDrafts.find((d) => d.review_status === "hold" && !d.notes.trim());
        if (noEvidence) { toast(`보류 step ${noEvidence.step_index}에 근거 메모를 적어 주세요.`); return false; }
        if (state.trajectoryDraft.review_status === "hold" && !state.trajectoryDraft.notes.trim()) { toast("실행 전체를 보류하는 근거를 적어 주세요."); return false; }
        if (state.trajectoryDraft.review_status === "confirmed" && state.trajectoryDraft.trajectory_has_violation === "1" && !state.trajectoryDraft.evidence_action_id) { toast("실행 요약에 위반 있음이면 근거 행동을 직접 선택해 주세요."); return false; }
      }
      const labels = state.labelDrafts.map((d) => ({ ...d,
        gold_violation: d.gold_violation === "" ? null : Number(d.gold_violation),
        violation_executed: d.violation_executed === "" ? null : Number(d.violation_executed),
        tool_call_error: d.tool_call_error === "" ? null : Number(d.tool_call_error),
        review_notes: d.review_notes_text.trim() ? [{ issue: "TEAM_REVIEW_NOTE", detail: d.review_notes_text.trim() }] : [],
        exception_notes: d.exception_notes_text.trim() ? [{ detail: d.exception_notes_text.trim() }] : [],
        pre_state_evidence: d.pre_state_evidence_text.trim() ? [{ evidence_type: "reviewer_recorded_at_action_time", detail: d.pre_state_evidence_text.trim() }] : [],
        computed_check: d.computed_check_text.trim() ? { reviewer_check: d.computed_check_text.trim() } : null,
        scenario_definition_evidence: d.scenario_definition_evidence_text.trim() ? [{ detail: d.scenario_definition_evidence_text.trim() }] : [],
        public_contract: d.public_contract_text.trim() ? [{ detail: d.public_contract_text.trim() }] : [],
        review_status: d.review_status === "hold" ? "hold" : finalize ? "confirmed" : "draft" }));
      const trajectory = { ...state.trajectoryDraft,
        review_status: state.trajectoryDraft.review_status === "hold" ? "hold" : finalize ? (state.trajectoryDraft.trajectory_has_violation === "" ? "draft" : "confirmed") : "draft",
        trajectory_has_violation: state.trajectoryDraft.trajectory_has_violation === "" ? null : Number(state.trajectoryDraft.trajectory_has_violation) };
      const previousByStep = new Map((state.labels || []).map((label) => [Number(label.step_index), label]));
      const sameValue = (a, b) => a && typeof a === "object" || b && typeof b === "object"
        ? JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
        : (a ?? null) === (b ?? null);
      const changed = (next, previous, fields) => !previous
        ? ["gold_violation", "violation_executed", "tool_call_error", "error_type", "review_notes", "exception_notes", "pre_state_evidence", "computed_check", "scenario_definition_evidence", "public_contract", "suggested_status", "trajectory_has_violation", "notes"].some((field) => next[field] != null && next[field] !== "" && next[field] !== "[]") || next.review_status === "hold"
        : fields.some((field) => !sameValue(next[field], previous[field])) || next.review_status !== (previous.review_status || "draft");
      const labelFields = ["action_id", "gold_violation", "violation_executed", "tool_call_error", "error_type", "review_notes", "exception_notes", "pre_state_evidence", "computed_check", "scenario_definition_evidence", "public_contract", "suggested_status", "review_status", "notes"];
      const trajectoryFields = ["trajectory_has_violation", "evidence_action_id", "review_status", "notes"];
      const trajectoryChanged = changed(trajectory, state.trajectoryLabel, trajectoryFields);
      const result = await api("/api/save", { method: "POST", body: JSON.stringify({ unit_id: state.current.task.unit_id, lease_token: state.current.task.lease_token, labels, trajectory, finalize }) });
      if (finalize) { toast(result.status === "needs_adjudication" ? "제출됨 · 2차 판정 목록으로 이동" : "검수 완료로 저장했습니다."); clearInterval(state.heartbeat); state.current = null; $("task-work").classList.add("hidden"); $("task-empty").classList.remove("hidden"); }
      else {
        state.labels = labels.map((label) => {
          const previous = previousByStep.get(Number(label.step_index));
          return { ...label, author_name: changed(label, previous, labelFields) ? state.authorName : (previous?.author_name || "") };
        });
        state.trajectoryLabel = { ...trajectory, author_name: trajectoryChanged ? state.authorName : (state.trajectoryLabel?.author_name || "") };
        renderTask(); toast("초안을 공유 저장했습니다.");
      }
      await loadDashboard();
      return true;
    } catch (error) { toast(error.message); return false; }
  }
  async function downloadSidecar() {
    const button = $("download-sidecar");
    button.disabled = true;
    button.textContent = "라벨 sidecar 구성 중…";
    try {
      const samples = []; const pendingReviews = new Map(); const trajectory_reviews = []; const label_revision_history = []; const quarantined_trajectories = []; const excluded_reviews = []; const revisions = new Set();
      let cursor = ""; let snapshotAt = ""; let hasMore = true;
      while (hasMore) {
        const query = new URLSearchParams();
        if (cursor) query.set("cursor", cursor);
        if (snapshotAt) query.set("snapshot", snapshotAt);
        const page = await api(`/api/export?${query.toString()}`);
        samples.push(...(page.samples || [])); trajectory_reviews.push(...(page.trajectory_reviews || []));
        (page.pending_reviews || []).forEach((row) => pendingReviews.set(`${row.unit_id}:${row.step_index}`, row));
        label_revision_history.push(...(page.label_revision_history || []));
        quarantined_trajectories.push(...(page.quarantined_trajectories || []));
        excluded_reviews.push(...(page.excluded_reviews || []));
        (page.source_revisions || []).forEach((revision) => revisions.add(revision));
        snapshotAt = page.snapshot_at || snapshotAt;
        hasMore = Boolean(page.has_more); cursor = page.next_cursor || cursor;
      }
      if (!samples.length && !quarantined_trajectories.length && !excluded_reviews.length) { toast("내보낼 최종 검수 기록이 아직 없습니다."); return; }
      const confirmed = samples.filter((row) => row.review_status === "REVIEWED");
      samples.filter((row) => row.review_status !== "REVIEWED").forEach((row) => {
        const key = `${row.unit_id}:${row.step_index}`;
        if (!pendingReviews.has(key)) pendingReviews.set(key, { ...row, review_status: row.review_status || "ON_HOLD_AMBIGUOUS_REVIEW", adjudication_final: false });
      });
      const pending = [...pendingReviews.values()];
      const confirmed_label_counts = { "0": 0, "1": 0 };
      confirmed.forEach((row) => { if ([0, 1].includes(row.gold_violation)) confirmed_label_counts[String(row.gold_violation)] += 1; });
      const trajectory_violation_evidence = {};
      trajectory_reviews.filter((row) => row.review_status === "REVIEWED").forEach((row) => {
        const evidence = row.evidence_action_id
          ? confirmed.find((sample) => sample.action_id === row.evidence_action_id && sample.gold_violation === 1)
          : null;
        trajectory_violation_evidence[row.trajectory_id] = {
          trajectory_has_violation: row.trajectory_has_violation === 1,
          ...(evidence ? { evidence_action_id: evidence.action_id } : {}),
        };
      });
      const sidecar = {
        schema_version: "argus-v.collaborative-label-review-draft.v1",
        data_revision: revisions.size === 1 ? [...revisions][0] : null,
        source_revisions: [...revisions],
        code_revision: "november-team-review-dashboard",
        human_confirmed: pending.length === 0 && confirmed.length > 0 && confirmed.length === samples.length,
        exported_at: new Date().toISOString(),
        overlay_note: "Supplemental human-review sidecar. Raw Hugging Face rollout and Parquet files were not modified. Reviewer values are team display names and may be public when uploaded to Hugging Face.",
        reviewer_names_are_public: true,
        field_definitions: {
          gold_violation: "정책 위반 시도: 0/1/null. 사람 검수 라벨이며 학습·평가의 주 정답이다.",
          violation_executed: "위반 행위가 실제로 수행됐는지: 0/1/null. 도구 실행 여부와 구분한다.",
          tool_call_error: "도구 이름·형식·인자·선택 오류: 0/1/null. 정책 위반 라벨과 독립적으로 기록한다.",
          error_type: "tool_call_error=1일 때 확인된 구체적 도구 호출 오류 유형.",
          review_notes: "쟁점과 판정에 필요한 추가 검수 메모.",
          exception_notes: "일반 규칙과 다른 예외 사례 및 적용 범위.",
          pre_state_evidence: "해당 행동 시점까지 확인 가능한 상태 증거.",
          computed_check: "재현 가능한 계산 또는 검증 결과.",
          scenario_definition_evidence: "시나리오 정의에 근거한 판정 기록.",
          public_contract: "공개 도구/API 계약에 근거한 판정 기록.",
          human_confirmed: "팀 검수자가 해당 행동 라벨을 직접 확정했는지.",
          reviewer: "라벨을 작성한 팀원의 공개용 표시명. 이메일은 포함하지 않는다.",
          user_authorized_auto_adjudication: "사용자 위임 기준에 따른 자동 확정인지. 이 11월 sidecar에서는 모두 false다.",
          adjudication_final: "라벨이 최종 확정됐는지. 보류 항목은 false다.",
        },
        samples,
        confirmed_action_count: confirmed.length,
        human_confirmed_action_count: confirmed.filter((row) => row.human_confirmed).length,
        delegated_confirmed_action_count: 0,
        pending_reviews: pending,
        trajectory_violation_evidence,
        trajectory_reviews,
        label_revision_history,
        quarantined_trajectories,
        excluded_reviews,
        review_progress: {
          exported_action_count: samples.length,
          confirmed_action_count: confirmed.length,
          held_action_count: pending.length,
          total_pending: pending.length,
          human_confirmed_action_count: confirmed.filter((row) => row.human_confirmed).length,
          delegated_confirmed_action_count: 0,
          confirmed_label_counts,
        },
      };
      const blob = new Blob([JSON.stringify(sidecar, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob); const link = document.createElement("a");
      link.href = url; link.download = "ArgusV_november_review_progress.json"; link.click(); URL.revokeObjectURL(url);
      toast(`${samples.length.toLocaleString("ko-KR")}개 행동 라벨을 별도 sidecar로 내보냈습니다.`);
    } catch (error) {
      toast(error.message === "ACTIVE_LABELING_LEASES"
        ? "작업 중인 팀원이 있어 내보내기를 멈췄습니다. 모든 예약이 끝난 뒤 다시 내보내세요."
        : error.message.startsWith("QUEUE_TASKS_INCOMPLETE_")
          ? `아직 검수되지 않은 작업이 ${error.message.split("_").at(-1)}개 있어 최종 export를 멈췄습니다.`
          : `sidecar 내보내기 실패: ${error.message}`);
    }
    finally { button.disabled = false; button.textContent = "HF 라벨 sidecar 다운로드"; }
  }
  async function publishSidecar() {
    const button = $("publish-sidecar");
    button.disabled = true; button.textContent = "HF에 게시 중…";
    try {
      const result = await api("/api/publish", { method: "POST", body: "{}" });
      toast(`${result.samples.toLocaleString("ko-KR")}개 라벨을 HF sidecar에 반영했습니다.`);
    } catch (error) {
      const message = error.message === "EXPORT_QUEUE_NOT_QUIESCENT"
        ? "작업 중인 팀원이 있어 게시를 멈췄습니다. 모두 저장하고 작업이 끝난 뒤 다시 게시하세요."
        : error.message === "NO_FINAL_LABELS_TO_PUBLISH"
          ? "HF에 반영할 최종 검수 라벨이 아직 없습니다."
          : error.message === "HF_PUBLISH_NOT_CONFIGURED"
            ? "Vercel에 HF_TOKEN(write 권한)을 등록해야 HF 게시를 사용할 수 있습니다."
            : `HF sidecar 게시 실패: ${error.message}`;
      toast(message);
    } finally {
      button.disabled = false; button.textContent = "HF에 라벨 반영";
    }
  }
  async function releaseTask() {
    if (!state.current) return;
    try { const saved = await saveLabels(false); if (!saved) return; await api("/api/release", { method: "POST", body: JSON.stringify({ unit_id: state.current.task.unit_id, lease_token: state.current.task.lease_token }) }); clearInterval(state.heartbeat); state.current = null; $("task-work").classList.add("hidden"); $("task-empty").classList.remove("hidden"); await loadDashboard(); toast("작업을 반납했습니다."); }
    catch (error) { toast(error.message); }
  }
  async function disposeTask(disposition) {
    const note = $("disposition-note").value.trim();
    if (!note) { toast("전체 rollout을 격리하거나 제외하는 근거를 적어 주세요."); return; }
    const saved = await saveLabels(false);
    if (!saved || !state.current) return;
    try {
      await api("/api/disposition", { method: "POST", body: JSON.stringify({
        unit_id: state.current.task.unit_id,
        lease_token: state.current.task.lease_token,
        disposition,
        note,
      }) });
      clearInterval(state.heartbeat); state.current = null;
      $("task-work").classList.add("hidden"); $("task-empty").classList.remove("hidden");
      await loadDashboard();
      toast(disposition === "quarantined" ? "전체 rollout을 격리했습니다. 목록과 sidecar에서 일반 라벨과 분리됩니다." : "전체 rollout을 사용 제외로 표시했습니다.");
    } catch (error) { toast(error.message); }
  }
  function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }
  function escapeAttr(value) { return escapeHtml(value).replace(/`/g, "&#96;"); }

  $("author-form").addEventListener("submit", async (event) => {
    event.preventDefault(); $("author-error").textContent = "";
    const displayName = $("author-name").value.trim();
    const teamKey = $("team-access-key").value;
    if (!displayName || displayName.length > 80 || /[\u0000-\u001f\u007f]/.test(displayName)) { $("author-error").textContent = "이름을 1~80자로 입력해 주세요."; return; }
    if (!teamKey) { $("author-error").textContent = "팀 공용 접근키를 입력해 주세요."; return; }
    state.teamKey = teamKey; sessionStorage.setItem("argus-label-team-key", teamKey);
    state.authorName = displayName; state.user = { displayName };
    try { await api("/api/dashboard"); }
    catch (error) { $("author-error").textContent = error.message === "TEAM_ACCESS_KEY_INVALID" ? "팀 접근키가 일치하지 않습니다." : `공동 작업 API 연결 실패: ${error.message}`; return; }
    localStorage.setItem("argus-label-author", displayName); setUserChrome();
    show("dashboard-view"); await loadDashboard().catch((error) => toast(error.message));
  });
  $("change-author").onclick = () => {
    if (state.current) { toast("진행 중인 작업을 반납한 뒤 이름을 바꿔 주세요."); return; }
    $("author-name").value = state.authorName; $("team-access-key").value = state.teamKey; $("author-error").textContent = ""; show("author-view");
  };
  $("signout").onclick = () => { clearInterval(state.heartbeat); localStorage.removeItem("argus-label-author"); sessionStorage.removeItem("argus-label-team-key"); state.teamKey = ""; state.user = null; state.authorName = ""; state.current = null; setUserChrome(); show("author-view"); };
  $("claim-next").onclick = claimNext;
  $("refresh").onclick = () => (state.hfReadOnly ? startHFQueue() : loadDashboard().catch((e) => toast(e.message)));
  $("download-sidecar").onclick = downloadSidecar;
  $("publish-sidecar").onclick = publishSidecar;
  $("filter-scenario").oninput = (e) => { state.filter = e.target.value; renderQueue(); };
  $("filter-status").onchange = (e) => { state.status = e.target.value; renderQueue(); };
  $("queue-list").addEventListener("click", async (event) => {
    const preview = event.target.closest("[data-preview-unit]");
    const row = event.target.closest("[data-unit]");
    if (preview || row) { await previewUnit(preview?.dataset.previewUnit || row.dataset.unit); return; }
    if (state.hfReadOnly) { toast("HF 원본 목록은 읽기 전용입니다. 작업 저장에는 Vercel API와 HF_TOKEN 설정이 필요합니다."); return; }
  });
  $("preview-close").onclick = () => $("preview-dialog").close();
  $("preview-claim").onclick = async () => {
    const unit_id = state.previewUnit;
    if (!unit_id) return;
    if (await claimSelected(unit_id)) $("preview-dialog").close();
  };
  start();
})();
