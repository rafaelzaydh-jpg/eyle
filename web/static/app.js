(() => {
  "use strict";

  const logEl = document.getElementById("log");
  const emptyState = document.getElementById("emptyState");
  const inputEl = document.getElementById("input");
  const sendBtn = document.getElementById("sendBtn");
  const connDot = document.getElementById("connDot");
  const tokenBtn = document.getElementById("tokenBtn");
  const clearConversationBtn = document.getElementById("clearConversationBtn");
  const projectInfo = document.getElementById("projectInfo");
  const activityEl = document.getElementById("activity");
  const jobStateEl = document.getElementById("jobState");

  // Cada ciclo ativo pode consultar ate dois jobs + /conversa. Com 1,2 s,
  // o proprio painel permanece abaixo do limite padrao de 180 req/min.
  const CONVERSA_POLL_IDLE = 2500;
  const CONVERSA_POLL_PENDING = 1200;
  const STATUS_POLL = 6000;
  const MAX_JOBS_PER_POLL = 2;
  const JOBS_STORAGE_KEY = "eyleTrackedJobs";
  const INSTANCE_STORAGE_KEY = "eyleQueueInstanceId";

  let renderedIds = new Set();
  let pending = false;
  let conversaTimer = null;
  let statusTimer = null;
  let conversaEmAndamento = false;
  let statusEmAndamento = false;
  let rateLimitUntil = 0;
  let jobPollCursor = 0;
  let apiToken = sessionStorage.getItem("eyleApiToken") || "";
  let tokenPromptCancelado = false;
  let queueInstanceId = sessionStorage.getItem(INSTANCE_STORAGE_KEY) || "";
  let trackedJobs = carregarJobsAcompanhados();
  let activeConfirmation = null;

  function carregarJobsAcompanhados() {
    try {
      // Revisões anteriores não guardavam a identidade do SQLite. Descartar
      // esse estado uma vez evita colar um job #1 antigo em um banco recriado.
      if (!sessionStorage.getItem(INSTANCE_STORAGE_KEY)) {
        sessionStorage.removeItem(JOBS_STORAGE_KEY);
        return [];
      }
      const dados = JSON.parse(sessionStorage.getItem(JOBS_STORAGE_KEY) || "[]");
      if (!Array.isArray(dados)) return [];

      // Preserve jobs ativos e conclusoes que ja possuem resumo operacional.
      // Falhas antigas continuam descartadas para nao reaparecerem sem contexto.
      const ativos = dados.filter((job) =>
        job && Number.isInteger(Number(job.id)) && (
          ["pending", "processing"].includes(job.status) ||
          job.status === "completed"
        )
      ).slice(-20);
      sessionStorage.setItem(JOBS_STORAGE_KEY, JSON.stringify(ativos));
      return ativos;
    } catch (err) {
      sessionStorage.removeItem(JOBS_STORAGE_KEY);
      return [];
    }
  }

  function salvarJobsAcompanhados() {
    trackedJobs = trackedJobs.slice(-20);
    sessionStorage.setItem(JOBS_STORAGE_KEY, JSON.stringify(trackedJobs));
  }

  function acompanharJob(id, tipo, metadados = {}) {
    const numerico = Number(id);
    if (!Number.isInteger(numerico)) return;
    // Uma nova resposta de /enviar sempre nasce limpa. Nunca herda status,
    // progresso de outro job que reutilizou o mesmo número.
    trackedJobs = trackedJobs.filter((job) => job.id !== numerico);
    trackedJobs.push({
      id: numerico,
      tipo,
      status: "pending",
      ...metadados,
    });
    salvarJobsAcompanhados();
    updatePendingState();
  }

  function sincronizarInstanciaFila(novaInstancia) {
    const normalizada = String(novaInstancia || "").trim();
    if (!normalizada) return;
    if (queueInstanceId && queueInstanceId !== normalizada) {
      trackedJobs = [];
      sessionStorage.removeItem(JOBS_STORAGE_KEY);
      Array.from(logEl.querySelectorAll(".job-notice, .live-response")).forEach((el) => el.remove());
      renderedIds = new Set();
    }
    queueInstanceId = normalizada;
    sessionStorage.setItem(INSTANCE_STORAGE_KEY, normalizada);
    salvarJobsAcompanhados();
    updatePendingState();
  }

  // ---------- render ----------

  function formatTime(ts) {
    if (!ts) return "";
    // ts vem como "YYYY-MM-DDTHH:MM:SS"
    const t = ts.split("T")[1];
    return t ? t.slice(0, 5) : "";
  }

  function appendInlineMarkdown(parent, text) {
    const source = String(text || "");
    const pattern = /(\*\*([^*\n]+)\*\*|`([^`\n]+)`)/g;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      if (match.index > cursor) {
        parent.appendChild(document.createTextNode(source.slice(cursor, match.index)));
      }
      if (match[2] !== undefined) {
        const strong = document.createElement("strong");
        strong.textContent = match[2];
        parent.appendChild(strong);
      } else {
        const code = document.createElement("code");
        code.textContent = match[3];
        parent.appendChild(code);
      }
      cursor = pattern.lastIndex;
    }
    if (cursor < source.length) {
      parent.appendChild(document.createTextNode(source.slice(cursor)));
    }
  }

  function renderMarkdownSafe(target, value) {
    target.replaceChildren();
    const lines = String(value || "").split("\n");
    let codeLines = null;

    function flushCode() {
      if (codeLines === null) return;
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = codeLines.join("\n");
      pre.appendChild(code);
      target.appendChild(pre);
      codeLines = null;
    }

    lines.forEach((line, index) => {
      if (/^\s*```/.test(line)) {
        if (codeLines === null) codeLines = [];
        else flushCode();
        return;
      }
      if (codeLines !== null) {
        codeLines.push(line);
        return;
      }
      appendInlineMarkdown(target, line);
      if (index < lines.length - 1) target.appendChild(document.createElement("br"));
    });
    flushCode();
  }

  function historyJsonBlock(value) {
    const pre = document.createElement("pre");
    pre.className = "history-json";
    pre.textContent = JSON.stringify(value || {}, null, 2);
    return pre;
  }

  function historyLine(label, value) {
    if (value === undefined || value === null || value === "") return null;
    const line = document.createElement("div");
    line.className = "history-line";
    const key = document.createElement("span");
    key.className = "history-key";
    key.textContent = `${label}:`;
    const val = document.createElement("span");
    val.textContent = String(value);
    line.append(key, val);
    return line;
  }

  function historySection(title) {
    const section = document.createElement("section");
    section.className = "history-section";
    const heading = document.createElement("div");
    heading.className = "history-section-title";
    heading.textContent = title;
    section.appendChild(heading);
    return section;
  }

  function renderExecutionHistory(panel, history) {
    panel.replaceChildren();

    const head = document.createElement("div");
    head.className = "history-head";
    const title = document.createElement("strong");
    title.textContent = `histórico · job #${history.job_id}`;
    const status = document.createElement("span");
    status.className = `history-status ${history.status || ""}`;
    status.textContent = history.status || "desconhecido";
    const headActions = document.createElement("div");
    headActions.className = "history-head-actions";
    const expandAll = document.createElement("button");
    expandAll.type = "button";
    expandAll.className = "history-expand-all";
    expandAll.textContent = "expandir tudo";
    expandAll.addEventListener("click", () => {
      const items = Array.from(panel.querySelectorAll("details.history-item"));
      const shouldOpen = items.some((item) => !item.open);
      items.forEach((item) => { item.open = shouldOpen; });
      expandAll.textContent = shouldOpen ? "recolher tudo" : "expandir tudo";
    });
    headActions.append(expandAll, status);
    head.append(title, headActions);
    panel.appendChild(head);

    const agent = history.agent || {};
    const summary = historySection("Execução ECC");
    [
      historyLine("turnos", agent.turns),
      historyLine("operações físicas", agent.physical_capability_calls),
      historyLine("replays compactos", agent.operation_replays),
      historyLine("objetivo", agent.objective_present === true ? "presente" : (agent.objective_present === false ? "nenhum" : null)),
      historyLine("objetivo · filhos", agent.objective_present === true ? agent.objective_children : null),
      historyLine("memória · nós", agent.memory_nodes),
      historyLine("memória · arestas", agent.memory_edges),
      historyLine("memória · fresh", agent.memory_fresh_nodes),
      historyLine("memória · degradada", agent.memory_degraded_nodes),
      historyLine("evidências", agent.evidence_items),
      historyLine("groundings", agent.grounding_count_total),
      historyLine("observation ledger", agent.observation_ledger_size),
      historyLine("reality epoch", agent.reality_epoch),
      historyLine("duração", history.duration_seconds != null ? `${history.duration_seconds}s` : null),
      historyLine("falha", agent.failure_code),
    ].filter(Boolean).forEach((line) => summary.appendChild(line));
    panel.appendChild(summary);

    const tokens = history.tokens || {};
    if (Object.keys(tokens).length) {
      const tokenSection = historySection("Tokens");
      const labels = {
        prompt_total: "prompt total",
        prompt_cached: "cacheados",
        prompt_new: "novos",
        prompt_effective: "prompt efetivo",
        completion: "saída",
        reasoning: "reasoning reportado",
        effective_total: "total efetivo",
        physical_estimated_total: "total físico estimado",
        physical_remaining: "budget físico restante",
        physical_limit: "budget físico máximo",
      };
      Object.entries(labels).forEach(([key, label]) => {
        const line = historyLine(label, tokens[key]);
        if (line) tokenSection.appendChild(line);
      });
      panel.appendChild(tokenSection);
    }

    const accounting = history.prompt_accounting || {};
    if (Object.keys(accounting).length) {
      const summaryData = accounting.summary || {};
      const diagnostics = accounting.diagnostics || {};
      const costSection = historySection("Contabilidade de prompt");
      [
        historyLine("estimativa local total", summaryData.local_total_estimated_tokens),
        historyLine("provider prompt total", summaryData.provider_prompt_tokens),
        historyLine("provider/local", summaryData.provider_to_local_estimate_ratio),
        historyLine("imposto fixo repetido", summaryData.fixed_repeat_tax_estimated_tokens),
        historyLine("resultados atuais", summaryData.current_runtime_results_estimated_tokens),
        historyLine("memória projetada", summaryData.memory_state_estimated_tokens),
        historyLine("navegação física", summaryData.physical_navigation_estimated_tokens),
        historyLine("background", summaryData.background_context_estimated_tokens),
        historyLine("operações ECC", summaryData.ecc_contract_estimated_tokens),
        historyLine("observações físicas", diagnostics.physical_observations),
        historyLine("operações físicas", diagnostics.physical_capability_calls),
        historyLine("replays compactos", diagnostics.operation_replays),
        historyLine("memória · nós", diagnostics.memory_nodes),
        historyLine("memória · arestas", diagnostics.memory_edges),
        historyLine("memória · fresh", diagnostics.memory_fresh_nodes),
        historyLine("memória · degradada", diagnostics.memory_degraded_nodes),
        historyLine("memória · semântica", diagnostics.memory_semantic_nodes),
        historyLine("evidências", diagnostics.evidence_items),
        historyLine("grounding por observação", diagnostics.grounding_per_observation),
        historyLine("taxa de replay", diagnostics.replay_operation_rate),
      ].filter(Boolean).forEach((line) => costSection.appendChild(line));

      const details = document.createElement("details");
      details.className = "history-item";
      const summaryEl = document.createElement("summary");
      summaryEl.textContent = "componentes acumulados e diagnósticos";
      details.appendChild(summaryEl);
      details.appendChild(historyJsonBlock({
        categories: accounting.categories || {},
        component_totals: accounting.component_totals || {},
        diagnostics,
        interpretation: accounting.interpretation,
      }));
      costSection.appendChild(details);
      panel.appendChild(costSection);
    }

    const llmCalls = Array.isArray(history.llm_calls) ? history.llm_calls : [];
    if (llmCalls.length) {
      const llmMeta = history.llm || {};
      const sent = llmMeta.requests_sent != null ? llmMeta.requests_sent : llmCalls.filter((item) => item.request_status !== "preflight_blocked").length;
      const blocked = llmMeta.preflight_blocked != null ? llmMeta.preflight_blocked : llmCalls.filter((item) => item.request_status === "preflight_blocked").length;
      const blockedText = blocked ? ` · ${blocked} bloqueada(s) no preflight` : "";
      const llmSection = historySection(`LLM · ${sent} enviada(s)${blockedText}`);
      llmCalls.forEach((call) => {
        const details = document.createElement("details");
        details.className = "history-item";
        const summaryEl = document.createElement("summary");
        const prompt = call.prompt_tokens != null ? ` · ${call.prompt_tokens} prompt` : "";
        const cached = call.cached_prompt_tokens != null ? ` · ${call.cached_prompt_tokens} cache` : "";
        const requestState = call.request_status === "preflight_blocked" ? " · preflight bloqueado" : "";
        summaryEl.textContent = `LLM #${call.call}${prompt}${cached}${requestState}`;
        details.appendChild(summaryEl);
        const body = { ...call };
        delete body.call;
        details.appendChild(historyJsonBlock(body));
        llmSection.appendChild(details);
      });
      panel.appendChild(llmSection);
    }

    const decisions = Array.isArray(history.decisions) ? history.decisions : [];
    if (decisions.length) {
      const decisionSection = historySection(`Decisões · ${decisions.length} ação(ões)`);
      decisions.forEach((item) => {
        const details = document.createElement("details");
        details.className = "history-item";
        const summaryEl = document.createElement("summary");
        const outcome = item.outcome ? ` · ${item.outcome}` : "";
        summaryEl.textContent = `turno ${item.turn || item.call} · ${item.decision || "decisão"}${outcome}`;
        details.appendChild(summaryEl);
        const body = { ...item };
        delete body.call;
        details.appendChild(historyJsonBlock(body));
        decisionSection.appendChild(details);
      });
      panel.appendChild(decisionSection);
    }

    const capabilities = Array.isArray(history.capabilities) ? history.capabilities : [];
    if (capabilities.length) {
      const capabilitySection = historySection(`Capabilities · ${capabilities.length} ação(ões)`);
      capabilities.forEach((call) => {
        const details = document.createElement("details");
        details.className = "history-item";
        const summaryEl = document.createElement("summary");
        const statusText = call.status ? ` · ${call.status}` : "";
        summaryEl.textContent = `${call.call}. ${call.capability || "capability"}${statusText}`;
        details.appendChild(summaryEl);
        const capabilityNameLine = historyLine("capability", call.capability || "unknown_capability");
        if (capabilityNameLine) details.appendChild(capabilityNameLine);

        const argsTitle = document.createElement("div");
        argsTitle.className = "history-subtitle";
        argsTitle.textContent = "argumentos observáveis";
        details.append(argsTitle, historyJsonBlock(call.arguments || {}));
        const resultTitle = document.createElement("div");
        resultTitle.className = "history-subtitle";
        resultTitle.textContent = "resultado resumido";
        details.append(resultTitle, historyJsonBlock(call.result || {}));
        capabilitySection.appendChild(details);
      });
      panel.appendChild(capabilitySection);
    }

    const validation = history.write_validation || {};
    if (Object.keys(validation).length) {
      const writeSection = historySection("Validação pós-escrita");
      Object.entries(validation).forEach(([stage, data]) => {
        const details = document.createElement("details");
        details.className = "history-item";
        const summaryEl = document.createElement("summary");
        const ok = data && data.ok === true ? "ok" : data && data.ok === false ? "falhou" : "informativo";
        summaryEl.textContent = `${stage} · ${ok}`;
        details.append(summaryEl, historyJsonBlock(data || {}));
        writeSection.appendChild(details);
      });
      panel.appendChild(writeSection);
    }

    if (history.execution_failure) {
      const failSection = historySection("Falha de execução");
      failSection.appendChild(historyJsonBlock(history.execution_failure));
      panel.appendChild(failSection);
    }

    const privacy = document.createElement("div");
    privacy.className = "history-privacy";
    privacy.textContent = "Mostra ações observáveis do runtime. Não exibe chain-of-thought, prompts brutos, respostas brutas do modelo ou conteúdo-fonte.";
    panel.appendChild(privacy);
  }

  async function toggleJobHistory(jobId, wrap, button) {
    const numeric = Number(jobId);
    if (!Number.isInteger(numeric)) return;
    let panel = wrap.querySelector(`.execution-history[data-job-id="${numeric}"]`);
    if (panel) {
      const nowHidden = !panel.hidden;
      panel.hidden = nowHidden;
      button.textContent = nowHidden ? "histórico" : "ocultar histórico";
      return;
    }

    button.disabled = true;
    button.textContent = "carregando…";
    try {
      const res = await apiFetch(`/jobs/${numeric}/history`);
      if (!res.ok) throw new Error(`status ${res.status}`);
      const history = await res.json();
      panel = document.createElement("div");
      panel.className = "execution-history";
      panel.dataset.jobId = numeric;
      renderExecutionHistory(panel, history);
      wrap.appendChild(panel);
      button.textContent = "ocultar histórico";
    } catch (err) {
      button.textContent = "histórico indisponível";
    } finally {
      button.disabled = false;
    }
  }

  function addHistoryButton(meta, wrap, jobId) {
    const numeric = Number(jobId);
    if (!Number.isInteger(numeric)) return;
    const button = document.createElement("button");
    button.className = "msg-history";
    button.type = "button";
    button.textContent = "histórico";
    button.addEventListener("click", () => toggleJobHistory(numeric, wrap, button));
    meta.appendChild(button);
  }

  function syncDeleteState(wrap, msg) {
    const del = wrap.querySelector(".msg-del");
    if (!del) return;
    const pendente = Boolean(msg.pending_delete);
    del.disabled = pendente;
    del.textContent = pendente ? "removendo após resposta" : "remover";
    wrap.classList.toggle("pending-delete", pendente);
  }

  function syncAwaitUserPanels() {
    Array.from(logEl.querySelectorAll(".await-user-panel[data-pending-id]")).forEach((panel) => {
      const active = Boolean(activeConfirmation && String(activeConfirmation.id) === panel.dataset.pendingId);
      panel.classList.toggle("inactive", !active);
      panel.querySelectorAll("button, input").forEach((control) => {
        control.disabled = !active;
      });
    });
  }

  async function submitText(texto) {
    const value = String(texto || "").trim();
    if (!value) return false;
    sendBtn.disabled = true;
    try {
      const res = await apiFetch("/enviar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto: value }),
      });
      if (!res.ok) throw new Error("falha ao enviar");
      const data = await res.json();
      acompanharJob(data.job_id, "pergunta", {
        mensagem_id: data.mensagem_id,
        texto_resumo: value,
      });
      activeConfirmation = null;
      syncAwaitUserPanels();
      return true;
    } finally {
      sendBtn.disabled = false;
      clearTimeout(conversaTimer);
      fetchConversa();
    }
  }

  function buildConfirmationPanel(confirmation) {
    if (!confirmation || !confirmation.id) return null;
    const panel = document.createElement("div");
    panel.className = "await-user-panel";
    panel.dataset.pendingId = String(confirmation.id);

    const choices = document.createElement("div");
    choices.className = "await-user-choices";
    (Array.isArray(confirmation.options) ? confirmation.options : []).forEach((option, index) => {
      const label = String(option && option.label || "").trim();
      if (!label) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "await-user-option";
      button.textContent = `${index + 1}. ${label}`;
      button.addEventListener("click", async () => {
        try { await submitText(label); } catch (err) { /* permanece disponível */ }
      });
      choices.appendChild(button);
    });
    panel.appendChild(choices);

    const custom = document.createElement("div");
    custom.className = "await-user-custom";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Outra instrução…";
    const send = document.createElement("button");
    send.type = "button";
    send.textContent = "Enviar";
    const submitCustom = async () => {
      const value = input.value.trim();
      if (!value) return;
      try {
        if (await submitText(value)) input.value = "";
      } catch (err) { /* mantém o texto */ }
    };
    send.addEventListener("click", submitCustom);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); submitCustom(); }
    });
    custom.appendChild(input);
    custom.appendChild(send);
    panel.appendChild(custom);

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "await-user-cancel";
    cancel.textContent = "Cancelar tarefa";
    cancel.addEventListener("click", async () => {
      try { await submitText(`cancelar ${confirmation.id}`); } catch (err) { /* permanece disponível */ }
    });
    panel.appendChild(cancel);
    return panel;
  }

  function buildMessageEl(msg) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${msg.role === "user" ? "user" : "assistant"}`;
    wrap.dataset.id = msg.id;

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    renderMarkdownSafe(bubble, msg.text);
    wrap.appendChild(bubble);

    if (msg.role === "assistant" && msg.confirmation) {
      const panel = buildConfirmationPanel(msg.confirmation);
      if (panel) wrap.appendChild(panel);
    }

    const meta = document.createElement("div");
    meta.className = "msg-meta";

    const role = document.createElement("span");
    role.className = "msg-role";
    role.textContent = msg.role === "user" ? "você" : "eyle";
    meta.appendChild(role);

    if (msg.timestamp) {
      const time = document.createElement("span");
      time.textContent = formatTime(msg.timestamp);
      meta.appendChild(time);
    }

    if (msg.role === "assistant" && msg.source_job_id) {
      addHistoryButton(meta, wrap, msg.source_job_id);
    }

    const del = document.createElement("button");
    del.className = "msg-del";
    del.type = "button";
    del.textContent = "remover";
    del.addEventListener("click", () => deleteMessage(msg.id));
    meta.appendChild(del);

    wrap.appendChild(meta);
    syncDeleteState(wrap, msg);
    return wrap;
  }

  function renderConversa(mensagens) {
    const atBottom =
      logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 80;

    if (mensagens.length === 0) {
      logEl.innerHTML = "";
      logEl.appendChild(emptyState);
      renderedIds = new Set();
      return;
    }

    if (emptyState.parentNode === logEl) {
      logEl.removeChild(emptyState);
    }

    const incomingIds = new Set(mensagens.map((m) => m.id));

    // remove mensagens que sumiram (ex: DELETE feito em outra aba)
    Array.from(logEl.querySelectorAll(".msg[data-id]")).forEach((el) => {
      const id = Number(el.dataset.id);
      if (!incomingIds.has(id)) {
        el.remove();
        renderedIds.delete(id);
      }
    });

    mensagens.forEach((msg) => {
      const existente = logEl.querySelector(`.msg[data-id="${msg.id}"]`);
      if (existente) {
        syncDeleteState(existente, msg);
        return;
      }
      logEl.appendChild(buildMessageEl(msg));
      renderedIds.add(msg.id);
    });

    syncAwaitUserPanels();
    if (atBottom) {
      logEl.scrollTop = logEl.scrollHeight;
    }
  }

  function updatePendingState() {
    pending = trackedJobs.some(
      (job) => job.tipo === "pergunta" && ["pending", "processing"].includes(job.status),
    );
    renderLiveProgress();
    renderJobState();
  }

  function jobPerguntaMaisRecente() {
    return [...trackedJobs].reverse().find((job) => job.tipo === "pergunta") || null;
  }

  function formatMetric(value, digits = 1) {
    const numero = Number(value);
    if (!Number.isFinite(numero)) return null;
    return numero.toLocaleString("pt-BR", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function renderLiveProgress() {
    const job = jobPerguntaMaisRecente();
    Array.from(logEl.querySelectorAll(".live-response")).forEach((el) => {
      if (!job || el.id !== `liveJob-${job.id}` || !["pending", "processing"].includes(job.status)) {
        el.remove();
      }
    });
    if (!job || !["pending", "processing"].includes(job.status)) return;

    if (emptyState.parentNode === logEl) logEl.removeChild(emptyState);
    const progresso = job.progresso || {};
    const textoParcial = String(progresso.partial_text || "");
    const mensagem = String(progresso.message || (
      job.status === "pending" ? "Aguardando na fila" : "Processando a tarefa"
    ));
    let wrap = document.getElementById(`liveJob-${job.id}`);
    const pertoDoFim = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 110;
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "msg assistant live-response";
      wrap.id = `liveJob-${job.id}`;
      const bubble = document.createElement("div");
      bubble.className = "msg-bubble live-bubble";
      wrap.appendChild(bubble);
      const meta = document.createElement("div");
      meta.className = "msg-meta live-meta";
      wrap.appendChild(meta);
      logEl.appendChild(wrap);
    }

    const bubble = wrap.querySelector(".msg-bubble");
    renderMarkdownSafe(bubble, textoParcial || `◆ ${mensagem}`);
    bubble.classList.toggle("status-only", !textoParcial);

    const metricas = [];
    if (textoParcial) metricas.push(mensagem);
    const tps = formatMetric(progresso.tokens_per_second, 1);
    if (tps !== null) metricas.push(`${tps} tok/s`);
    const tokens = Number(progresso.estimated_tokens);
    if (Number.isFinite(tokens) && tokens > 0) metricas.push(`${Math.round(tokens)} tokens`);
    const tempo = formatMetric(progresso.elapsed_seconds, 1);
    if (tempo !== null) metricas.push(`${tempo}s`);
    metricas.push(`job #${job.id}`);
    wrap.querySelector(".msg-meta").textContent = metricas.join(" · ");

    if (pertoDoFim) logEl.scrollTop = logEl.scrollHeight;
  }

  function renderJobFailures() {
    trackedJobs
      .filter((job) => job.tipo === "pergunta" && job.status === "failed")
      .forEach((job) => {
        const elementId = `jobFailure-${job.id}`;
        if (document.getElementById(elementId)) return;

        const wrap = document.createElement("div");
        wrap.className = "job-notice";
        wrap.id = elementId;

        const bubble = document.createElement("div");
        bubble.className = "job-notice-bubble";
        const detalhe = job.mensagem || job.erro || "A tarefa falhou sem diagnóstico.";
        const origem = String(job.texto_resumo || job.texto || "").trim();
        const trecho = origem.length > 90 ? `${origem.slice(0, 87)}...` : origem;
        bubble.textContent = trecho
          ? `Falha ao processar “${trecho}”. ${detalhe}`
          : detalhe;
        wrap.appendChild(bubble);

        const meta = document.createElement("div");
        meta.className = "job-notice-meta";
        const metaText = document.createElement("span");
        metaText.textContent = `job #${job.id}${job.error_code ? ` · ${job.error_code}` : ""}`;
        meta.appendChild(metaText);
        addHistoryButton(meta, wrap, job.id);
        wrap.appendChild(meta);

        const mensagemId = Number(job.mensagem_id);
        const origemEl = Number.isInteger(mensagemId)
          ? logEl.querySelector(`.msg[data-id="${mensagemId}"]`)
          : null;
        if (origemEl) origemEl.insertAdjacentElement("afterend", wrap);
        else logEl.appendChild(wrap);
      });
  }

  function renderJobState() {
    const job = jobPerguntaMaisRecente() || (trackedJobs.length ? trackedJobs[trackedJobs.length - 1] : null);
    const ativo = job && ["pending", "processing"].includes(job.status);
    activityEl.classList.toggle("active", Boolean(ativo));
    if (!job) return;
    const rotulos = {
      pending: "aguardando na fila",
      processing: "em processamento",
      completed: "concluída",
      failed: "falhou",
      cancelled: "cancelada",
    };
    const progresso = job.progresso || {};
    const partes = [`job #${job.id}`, progresso.message || rotulos[job.status] || job.status];
    const tps = formatMetric(progresso.tokens_per_second, 1);
    if (tps !== null && ativo) partes.push(`${tps} tok/s`);
    jobStateEl.textContent = partes.join(" · ");
  }


  // ---------- network ----------

  function obterApiToken() {
    if (apiToken) return apiToken;
    if (tokenPromptCancelado) throw new Error("token da API nao informado");
    const informado = window.prompt(
      "Cole o token da API Eyle.\n\n" +
      "Onde encontrar:\n" +
      "• no terminal que executou `python main.py serve` (ou `python web/routes.py`), na linha `[main] Token da API`/`[web] Token da API`\n" +
      "• ou em `context/web_api_token.txt` (quando o token não veio de variável/configuração)\n\n" +
      "Você pode clicar no botão ‘token’ no topo para tentar novamente."
    );
    if (!informado || !informado.trim()) {
      tokenPromptCancelado = true;
      throw new Error("token da API nao informado");
    }
    apiToken = informado.trim();
    tokenPromptCancelado = false;
    sessionStorage.setItem("eyleApiToken", apiToken);
    return apiToken;
  }

  function solicitarNovoToken() {
    apiToken = "";
    tokenPromptCancelado = false;
    sessionStorage.removeItem("eyleApiToken");
    fetchConversa();
    fetchStatus();
  }

  async function limparConversaPreservandoMemoria() {
    if (pending) {
      window.alert("Existe uma tarefa em andamento. Aguarde a conclusao antes de limpar a conversa.");
      return;
    }
    const confirmado = window.confirm(
      "Limpar todas as mensagens desta conversa?\n\n" +
      "A Memory Graph sera preservada. Jobs e historico operacional tambem nao serao apagados."
    );
    if (!confirmado) return;

    clearConversationBtn.disabled = true;
    try {
      const res = await apiFetch("/conversa", { method: "DELETE" });
      const dados = await res.json().catch(() => ({}));
      if (res.status === 409) {
        window.alert("Ainda existe uma tarefa ativa. Aguarde e tente novamente.");
        return;
      }
      if (!res.ok) throw new Error(dados.motivo || dados.error_code || "falha ao limpar conversa");

      trackedJobs = [];
      sessionStorage.removeItem(JOBS_STORAGE_KEY);
      renderedIds = new Set();
      activeConfirmation = null;
      Array.from(logEl.querySelectorAll(".msg, .job-notice, .live-response, .execution-history")).forEach((el) => el.remove());
      await fetchConversa();
      updatePendingState();
      inputEl.focus();
    } catch (err) {
      window.alert(`Nao foi possivel limpar a conversa: ${err.message || err}`);
    } finally {
      clearConversationBtn.disabled = false;
    }
  }

  class RateLimitError extends Error {
    constructor(retryMs) {
      super("limite de requisicoes; aguardando Retry-After");
      this.name = "RateLimitError";
      this.retryMs = retryMs;
    }
  }

  function backoffRestanteMs() {
    return Math.max(0, rateLimitUntil - Date.now());
  }

  async function apiFetch(url, options = {}) {
    const restante = backoffRestanteMs();
    if (restante > 0) throw new RateLimitError(restante);

    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${obterApiToken()}`);
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
      apiToken = "";
      tokenPromptCancelado = true;
      sessionStorage.removeItem("eyleApiToken");
      projectInfo.innerHTML = '<span class="pi-line">token inválido · clique em “token”</span>';
      throw new Error("token da API invalido; clique no botao token para tentar novamente");
    }
    if (res.status === 429) {
      const retrySeconds = Math.max(1, Number(res.headers.get("Retry-After")) || 1);
      const retryMs = retrySeconds * 1000;
      rateLimitUntil = Math.max(rateLimitUntil, Date.now() + retryMs);
      throw new RateLimitError(retryMs);
    }
    return res;
  }

  async function fetchConversa() {
    if (conversaEmAndamento) return;
    conversaEmAndamento = true;
    try {
      // Uma falha temporaria em /jobs nao pode bloquear a leitura da conversa.
      try {
        await atualizarJobsAcompanhados();
      } catch (jobErr) {
        if (!(jobErr instanceof RateLimitError)) {
          // Mantem o job ativo e tenta novamente no proximo ciclo.
        }
      }
      const res = await apiFetch("/conversa");
      if (!res.ok) throw new Error("status " + res.status);
      const data = await res.json();
      connDot.classList.remove("offline");
      connDot.classList.add("online");
      renderConversa(data);
      renderLiveProgress();
        renderJobFailures();
    } catch (err) {
      if (!(err instanceof RateLimitError)) {
        connDot.classList.remove("online");
        connDot.classList.add("offline");
      }
    } finally {
      conversaEmAndamento = false;
      scheduleConversaPoll();
    }
  }

  async function atualizarJobsAcompanhados() {
    const ativos = trackedJobs.filter((job) => ["pending", "processing"].includes(job.status));
    if (!ativos.length) {
      updatePendingState();
      return;
    }

    // Com varias mensagens enfileiradas, consulta um lote rotativo em vez de
    // disparar N requests por ciclo e estourar o rate limit local.
    const quantidade = Math.min(MAX_JOBS_PER_POLL, ativos.length);
    const lote = [];
    for (let i = 0; i < quantidade; i += 1) {
      lote.push(ativos[(jobPollCursor + i) % ativos.length]);
    }
    jobPollCursor = (jobPollCursor + quantidade) % ativos.length;

    await Promise.all(lote.map(async (job) => {
      const res = await apiFetch(`/jobs/${job.id}`);
      if (res.status === 404) {
        job._descartar = true;
        return;
      }
      if (!res.ok) throw new Error("status " + res.status);
      const remoto = await res.json();
      if (
        job.mensagem_id && remoto.mensagem_id &&
        Number(job.mensagem_id) !== Number(remoto.mensagem_id)
      ) {
        job._descartar = true;
        return;
      }
      Object.assign(job, remoto);
    }));
    trackedJobs = trackedJobs.filter((job) => !job._descartar);
    salvarJobsAcompanhados();
    updatePendingState();
  }

  function scheduleConversaPoll() {
    clearTimeout(conversaTimer);
    const normal = pending ? CONVERSA_POLL_PENDING : CONVERSA_POLL_IDLE;
    conversaTimer = setTimeout(fetchConversa, Math.max(normal, backoffRestanteMs()));
  }

  function scheduleStatusPoll() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(fetchStatus, Math.max(STATUS_POLL, backoffRestanteMs()));
  }

  async function fetchStatus() {
    if (statusEmAndamento) return;
    statusEmAndamento = true;
    try {
      const res = await apiFetch("/status");
      if (!res.ok) throw new Error("status " + res.status);
      const data = await res.json();
      sincronizarInstanciaFila(data.queue_instance_id);
      renderProjectInfo(data);
    } catch (err) {
      if (!(err instanceof RateLimitError)) {
        projectInfo.innerHTML = '<span class="pi-line">worker offline</span>';
      }
    } finally {
      statusEmAndamento = false;
      scheduleStatusPoll();
    }
  }

  function renderProjectInfo(data) {
    activeConfirmation = data && data.confirmation ? data.confirmation : null;
    syncAwaitUserPanels();
    const p = data.projeto;
    if (!p || !p.disponivel) {
      projectInfo.innerHTML = '<span class="pi-line">nenhum workspace aberto</span>';
      return;
    }
    const nome = p.nome || "workspace";
    const fila = data.eventos_na_fila || 0;
    let html = `<span class="pi-line"><b>${escapeHtml(nome)}</b></span>`;
    if (fila > 0) {
      html += `<span class="pi-line">${fila} na fila</span>`;
    }
    const ultimoJob = trackedJobs.length ? trackedJobs[trackedJobs.length - 1] : null;
    if (ultimoJob && ultimoJob.status === "failed") {
      html += `<span class="pi-line">job #${ultimoJob.id} falhou</span>`;
    }
    projectInfo.innerHTML = html;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  async function sendMessage() {
    const texto = inputEl.value.trim();
    if (!texto) return;

    inputEl.value = "";
    autoGrow();
    try {
      await submitText(texto);
    } catch (err) {
      inputEl.value = texto;
    } finally {
      inputEl.focus();
    }
  }

  function autoGrow() {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
  }

  inputEl.addEventListener("input", autoGrow);

  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  sendBtn.addEventListener("click", sendMessage);
  tokenBtn.addEventListener("click", solicitarNovoToken);
  clearConversationBtn.addEventListener("click", limparConversaPreservandoMemoria);

  // ---------- boot ----------

  fetchConversa();
  fetchStatus();
})();
