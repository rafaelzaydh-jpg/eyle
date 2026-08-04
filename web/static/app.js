(() => {
  "use strict";

  const logEl = document.getElementById("log");
  const emptyState = document.getElementById("emptyState");
  const inputEl = document.getElementById("input");
  const sendBtn = document.getElementById("sendBtn");
  const connDot = document.getElementById("connDot");
  const projectInfo = document.getElementById("projectInfo");
  const pipelineEl = document.getElementById("pipeline");
  const jobStateEl = document.getElementById("jobState");

  const CONVERSA_POLL_IDLE = 2200;
  const CONVERSA_POLL_PENDING = 900;
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
      return Array.isArray(dados) ? dados.slice(-20) : [];
    } catch (err) {
      return [];
    }
  }

  function salvarJobsAcompanhados() {
    trackedJobs = trackedJobs.slice(-20);
    sessionStorage.setItem(JOBS_STORAGE_KEY, JSON.stringify(trackedJobs));
  }

  function acompanharJob(id, tipo) {
    const numerico = Number(id);
    if (!Number.isInteger(numerico)) return;
    trackedJobs = trackedJobs.filter((job) => job.id !== numerico);
    trackedJobs.push({ id: numerico, tipo, status: "pending" });
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
    Array.from(logEl.querySelectorAll(".msg")).forEach((el) => {
      const id = Number(el.dataset.id);
      if (!incomingIds.has(id)) el.remove();
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
    const wasPending = pending;
    pending = trackedJobs.some(
      (job) => job.tipo === "pergunta" && ["pending", "processing"].includes(job.status),
    );

    if (pending !== wasPending) {
      renderTypingIndicator();
    }
    renderJobState();
  }

  function renderTypingIndicator() {
    const existing = document.getElementById("typingIndicator");
    if (existing) existing.remove();
    if (!pending) return;
    const el = document.createElement("div");
    el.className = "typing";
    el.id = "typingIndicator";
    el.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
    logEl.appendChild(el);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function renderJobState() {
    const job = trackedJobs.length ? trackedJobs[trackedJobs.length - 1] : null;
    const ativo = job && ["pending", "processing"].includes(job.status);
    pipelineEl.classList.toggle("active", Boolean(ativo));
    if (!job) return;
    const rotulos = {
      pending: "aguardando na fila",
      processing: "em processamento",
      completed: "concluída",
      failed: "falhou",
    };
    jobStateEl.textContent = `job #${job.id} · ${rotulos[job.status] || job.status}`;
  }

  // ---------- network ----------

  function obterApiToken(forcarNovo = false) {
    if (!forcarNovo && apiToken) return apiToken;
    if (tokenPromptCancelado && !forcarNovo) throw new Error("token da API nao informado");
    const informado = window.prompt("Token da API Eyle:\n\n" + 
      "Copie o token do arquivo: context/web_api_token.txt\n" +
      "Ou defina a variavel de ambiente EYLE_API_TOKEN");
    if (!informado || !informado.trim()) {
      tokenPromptCancelado = true;
      throw new Error("token da API nao informado");
    }
    apiToken = informado.trim();
    sessionStorage.setItem("eyleApiToken", apiToken);
    tokenPromptCancelado = false;
    return apiToken;
  }

  async function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    let tentouRenovarToken = false;
    try {
      headers.set("Authorization", `Bearer ${obterApiToken()}`);
    } catch (err) {
      console.error("[apiFetch] Falha ao obter token:", err.message);
      throw err;
    }
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
      apiToken = "";
      tokenPromptCancelado = false;
      sessionStorage.removeItem("eyleApiToken");
      if (!tentouRenovarToken) {
        tentouRenovarToken = true;
        const novoToken = obterApiToken(true);
        if (novoToken) {
          headers.set("Authorization", `Bearer ${novoToken}`);
          const retryRes = await fetch(url, { ...options, headers });
          if (retryRes.status !== 401) {
            return retryRes;
          }
        }
      }
      throw new Error("token da API invalido; verifique context/web_api_token.txt");
    }
    if (res.status === 429) {
      throw new Error("limite de requisicoes; aguarde um pouco");
    }
    return res;
  }

  async function fetchConversa() {
    try {
      await atualizarJobsAcompanhados();
      const res = await apiFetch("/conversa");
      if (!res.ok) throw new Error("status " + res.status);
      const data = await res.json();
      connDot.classList.remove("offline");
      connDot.classList.add("online");
      renderConversa(data);
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
        job.status = "failed";
        job.erro = "tarefa nao encontrada";
        return;
      }
      if (!res.ok) throw new Error("status " + res.status);
      Object.assign(job, await res.json());
    }));
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
      acompanharJob(data.job_id, "pergunta");
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

  // ---------- boot ----------

  fetchConversa();
  fetchStatus();
  setInterval(fetchStatus, STATUS_POLL);
})();
