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

  const CONVERSA_POLL_IDLE = 2200;
  const CONVERSA_POLL_PENDING = 400;
  const STATUS_POLL = 5000;
  const JOBS_STORAGE_KEY = "eyleTrackedJobs";

  let renderedIds = new Set();
  let pending = false;
  let conversaTimer = null;
  let apiToken = sessionStorage.getItem("eyleApiToken") || "";
  let tokenPromptCancelado = false;
  let trackedJobs = carregarJobsAcompanhados();

  function carregarJobsAcompanhados() {
    try {
      const dados = JSON.parse(sessionStorage.getItem(JOBS_STORAGE_KEY) || "[]");
      if (!Array.isArray(dados)) return [];

      // Jobs concluidos pertencem a uma execucao anterior da pagina. Manter
      // "failed" no sessionStorage fazia o painel abrir exibindo, por exemplo,
      // "job #4 falhou" sem um novo POST /enviar. Preserve somente trabalhos
      // ainda ativos para que eles possam ser reconciliados com /jobs/<id>.
      const ativos = dados.filter((job) =>
        job && Number.isInteger(Number(job.id)) &&
        ["pending", "processing"].includes(job.status)
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
      if (!renderedIds.has(msg.id)) {
        logEl.appendChild(buildMessageEl(msg));
        renderedIds.add(msg.id);
      }
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

  async function apiFetch(url, options = {}) {
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
      throw new Error("limite de requisicoes; aguarde um pouco");
    }
    return res;
  }

  async function fetchConversa() {
    try {
      // Uma falha temporaria em /jobs nao pode bloquear a leitura da conversa.
      // Antes, qualquer erro de polling escondia ate respostas ja persistidas.
      try {
        await atualizarJobsAcompanhados();
      } catch (jobErr) {
        // Mantem o job ativo e tenta novamente no proximo ciclo.
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
      connDot.classList.remove("online");
      connDot.classList.add("offline");
    } finally {
      scheduleConversaPoll();
    }
  }

  async function atualizarJobsAcompanhados() {
    const ativos = trackedJobs.filter((job) => ["pending", "processing"].includes(job.status));
    if (!ativos.length) {
      updatePendingState();
      return;
    }
    await Promise.all(ativos.map(async (job) => {
      const res = await apiFetch(`/jobs/${job.id}`);
      if (res.status === 404) {
        // Pode ser um job de outra execucao da Eyle usando a mesma aba/origem.
        // Nao transforme lixo de sessionStorage em uma falha nova na tela.
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
    conversaTimer = setTimeout(fetchConversa, pending ? CONVERSA_POLL_PENDING : CONVERSA_POLL_IDLE);
  }

  async function fetchStatus() {
    try {
      const res = await apiFetch("/status");
      if (!res.ok) throw new Error("status " + res.status);
      const data = await res.json();
      renderProjectInfo(data);
    } catch (err) {
      projectInfo.innerHTML = '<span class="pi-line">worker offline</span>';
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
    if (el) el.remove();
    renderedIds.delete(id);
    try {
      const res = await apiFetch(`/mensagem/${id}`, { method: "DELETE" });
      if (res.ok) {
        const data = await res.json();
        acompanharJob(data.job_id, "remover");
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
  setInterval(fetchStatus, STATUS_POLL);
})();
