(() => {
  "use strict";

  const logEl = document.getElementById("log");
  const emptyState = document.getElementById("emptyState");
  const inputEl = document.getElementById("input");
  const sendBtn = document.getElementById("sendBtn");
  const connDot = document.getElementById("connDot");
  const tokenBtn = document.getElementById("tokenBtn");
  const projectInfo = document.getElementById("projectInfo");
  const pipelineEl = document.getElementById("pipeline");
  const jobStateEl = document.getElementById("jobState");

  // Cada ciclo ativo pode consultar ate dois jobs + /conversa. Com 1,2 s,
  // o proprio painel permanece abaixo do limite padrao de 180 req/min.
  const CONVERSA_POLL_IDLE = 2500;
  const CONVERSA_POLL_PENDING = 1200;
  const STATUS_POLL = 6000;
  const MAX_JOBS_PER_POLL = 2;
  const JOBS_STORAGE_KEY = "eyleTrackedJobs";

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
  let trackedJobs = carregarJobsAcompanhados();

  function carregarJobsAcompanhados() {
    try {
      const dados = JSON.parse(sessionStorage.getItem(JOBS_STORAGE_KEY) || "[]");
      if (!Array.isArray(dados)) return [];

      // Preserve jobs ativos e conclusoes que ja possuem resumo operacional.
      // Falhas antigas continuam descartadas para nao reaparecerem sem contexto.
      const ativos = dados.filter((job) =>
        job && Number.isInteger(Number(job.id)) && (
          ["pending", "processing"].includes(job.status) ||
          (job.status === "completed" && job.work_summary)
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
    const anterior = trackedJobs.find((job) => job.id === numerico) || {};
    trackedJobs = trackedJobs.filter((job) => job.id !== numerico);
    trackedJobs.push({
      ...anterior,
      id: numerico,
      tipo,
      status: anterior.status || "pending",
      ...metadados,
    });
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

  function syncDeleteState(wrap, msg) {
    const del = wrap.querySelector(".msg-del");
    if (!del) return;
    const pendente = Boolean(msg.pending_delete);
    del.disabled = pendente;
    del.textContent = pendente ? "removendo após resposta" : "remover";
    wrap.classList.toggle("pending-delete", pendente);
  }

  function buildMessageEl(msg) {
    const wrap = document.createElement("div");
    wrap.className = `msg ${msg.role === "user" ? "user" : "assistant"}`;
    wrap.dataset.id = msg.id;

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.textContent = msg.text;
    wrap.appendChild(bubble);

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

    if (atBottom) {
      logEl.scrollTop = logEl.scrollHeight;
    }
  }

  function updatePendingState() {
    pending = trackedJobs.some(
      (job) => job.tipo === "pergunta" && ["pending", "processing"].includes(job.status),
    );
    renderLiveProgress();
    renderWorkSummaries();
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
    bubble.textContent = textoParcial || `◆ ${mensagem}`;
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

  function formatDuration(totalSeconds) {
    const total = Math.max(0, Math.round(Number(totalSeconds) || 0));
    const horas = Math.floor(total / 3600);
    const minutos = Math.floor((total % 3600) / 60);
    const segundos = total % 60;
    if (horas > 0) {
      return `${horas}h${String(minutos).padStart(2, "0")}m${String(segundos).padStart(2, "0")}s`;
    }
    if (minutos > 0) {
      return `${minutos}m${String(segundos).padStart(2, "0")}s`;
    }
    return `${segundos}s`;
  }

  function buildWorkSummaryEl(job) {
    const resumo = job.work_summary || {};
    const details = document.createElement("details");
    details.className = "work-summary";
    details.id = `workSummary-${job.id}`;
    details.dataset.jobId = String(job.id);

    const summary = document.createElement("summary");
    summary.className = "work-summary-title";
    summary.textContent = `${resumo.title || "Trabalho concluído"} em ${formatDuration(resumo.duration_seconds)}`;
    details.appendChild(summary);

    const body = document.createElement("div");
    body.className = "work-summary-body";
    (Array.isArray(resumo.steps) ? resumo.steps : []).forEach((step) => {
      const section = document.createElement("section");
      section.className = "work-summary-step";

      const title = document.createElement("div");
      title.className = "work-summary-step-title";
      title.textContent = `Etapa ${step.number} — ${step.title}`;
      section.appendChild(title);

      (Array.isArray(step.fields) ? step.fields : []).forEach((field) => {
        const line = document.createElement("div");
        line.className = "work-summary-field";

        const label = document.createElement("span");
        label.className = "work-summary-label";
        label.textContent = `${field.label}: `;
        line.appendChild(label);

        const value = document.createElement("span");
        value.className = "work-summary-value";
        value.textContent = field.value;
        line.appendChild(value);
        section.appendChild(line);
      });
      body.appendChild(section);
    });
    details.appendChild(body);
    return details;
  }

  function renderWorkSummaries() {
    const concluidos = trackedJobs.filter((job) =>
      job.tipo === "pergunta" && job.status === "completed" && job.work_summary
    );
    const idsValidos = new Set(concluidos.map((job) => `workSummary-${job.id}`));

    Array.from(logEl.querySelectorAll(".work-summary[data-job-id]")).forEach((el) => {
      const job = concluidos.find((item) => `workSummary-${item.id}` === el.id);
      const origem = job && Number.isInteger(Number(job.mensagem_id))
        ? logEl.querySelector(`.msg[data-id="${Number(job.mensagem_id)}"]`)
        : null;
      if (!idsValidos.has(el.id) || !origem) el.remove();
    });

    concluidos.forEach((job) => {
      if (document.getElementById(`workSummary-${job.id}`)) return;
      const mensagemId = Number(job.mensagem_id);
      const origem = Number.isInteger(mensagemId)
        ? logEl.querySelector(`.msg[data-id="${mensagemId}"]`)
        : null;
      if (!origem) return;
      origem.insertAdjacentElement("afterend", buildWorkSummaryEl(job));
    });
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
        meta.textContent = `job #${job.id}${job.error_code ? ` · ${job.error_code}` : ""}`;
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
    pipelineEl.classList.toggle("active", Boolean(ativo));
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
      renderWorkSummaries();
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
    const p = data.projeto;
    if (!p) {
      projectInfo.innerHTML = '<span class="pi-line">nenhum projeto indexado</span>';
      return;
    }
    const nome = p.projeto || "projeto";
    const tokens = p.tokens_estimados_totais;
    const arquivos = p.arquivos;
    const fila = data.eventos_na_fila || 0;
    let html = `<span class="pi-line"><b>${escapeHtml(nome)}</b></span>`;
    if (tokens !== undefined) {
      const arqTxt = arquivos !== undefined ? ` · ${arquivos} arq.` : "";
      html += `<span class="pi-line">${tokens.toLocaleString("pt-br")} tokens${arqTxt}</span>`;
    }
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
    sendBtn.disabled = true;

    try {
      const res = await apiFetch("/enviar", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto }),
      });
      if (!res.ok) throw new Error("falha ao enviar");
      const data = await res.json();
      acompanharJob(data.job_id, "pergunta", {
        mensagem_id: data.mensagem_id,
        texto_resumo: texto,
      });
    } catch (err) {
      // devolve o texto pro usuário tentar de novo
      inputEl.value = texto;
    } finally {
      sendBtn.disabled = false;
      inputEl.focus();
      clearTimeout(conversaTimer);
      fetchConversa();
    }
  }

  async function deleteMessage(id) {
    const el = logEl.querySelector(`.msg[data-id="${id}"]`);
    const botao = el ? el.querySelector(".msg-del") : null;
    if (botao) {
      botao.disabled = true;
      botao.textContent = "removendo...";
    }
    try {
      const res = await apiFetch(`/mensagem/${id}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok && res.status !== 404) throw new Error("falha ao remover mensagem");

      if (data.removed && el) {
        el.remove();
        renderedIds.delete(id);
      } else if (data.status === "deferred" && botao) {
        botao.textContent = "removendo após resposta";
      }
    } catch (err) {
      if (botao) {
        botao.disabled = false;
        botao.textContent = "remover";
      }
    } finally {
      clearTimeout(conversaTimer);
      fetchConversa();
    }
  }

  // ---------- composer UX ----------

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

  // ---------- boot ----------

  fetchConversa();
  fetchStatus();
})();
