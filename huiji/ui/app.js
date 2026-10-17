"use strict";

// ---------- 小工具 ----------
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const WEEK = "日一二三四五六";
let api = null;

function fmt(sec) {
  const t = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}
function parseTime(text) {
  const first = String(text || "").replace(/[\[\]()（）]/g, "").replace("：", ":").split(/[–—~～\s-]/)[0];
  const parts = first.split(":").map(Number);
  if (!parts.length || parts.length > 3 || parts.some(isNaN)) return null;
  return parts.reduce((a, b) => a * 60 + b, 0);
}
function speakerLabel(id, names) {
  if (!id) return "";
  if (names && names[id]) return names[id];
  const n = parseInt(String(id).split("_").pop(), 10);
  return isNaN(n) ? id : `說話者 ${n + 1}`;
}
function speakerColor(id) {
  const n = parseInt(String(id).split("_").pop(), 10);
  return `var(--sp${(isNaN(n) ? 0 : n) % 8 + 1})`;
}
function toast(msg, ms = 2600) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => el.classList.add("hidden"), ms);
}
async function call(name, ...args) {
  try {
    return await api[name](...args);
  } catch (e) {
    toast(`錯誤：${e?.message || e}`, 5000);
    throw e;
  }
}

// ---------- 狀態 ----------
const S = {
  meetings: [],
  selected: null,
  data: null,      // {meeting, transcript, notes, has_audio}
  tab: "summary",
  query: "",
  recording: null, // {id, elapsed, paused, levels}
  recMode: localStorage.getItem("recMode") || "both",
  editing: false,
  trFilter: "",
  trSpeaker: "",
  templates: [],
};
const BUSY = ["queued", "transcribing", "summarizing"];

// ---------- 會議列表 ----------
async function loadList() {
  S.meetings = await api.list_meetings(S.query);
  renderList();
  if (!S.selected && S.meetings.length) select(S.meetings[0].id);
  if (!S.meetings.length && !S.recording) showEmpty();
}

function statusText(m) {
  if (S.recording && S.recording.id === m.id) return `<span class="st-rec">${S.recording.paused ? "已暫停" : "錄音中"}</span>`;
  if (m.status === "error") return `<span class="st-err">失敗</span>`;
  if (BUSY.includes(m.status)) return `<span class="st-work">${esc(m.progress || "處理中")}</span>`;
  if (m.review_count) return `<span class="st-review">待確認 ${m.review_count} 題</span>`;
  return "";
}

function renderList() {
  const ul = $("#meetingList");
  ul.innerHTML = S.meetings.map((m) => `
    <li data-id="${m.id}" class="${m.id === S.selected ? "active" : ""}">
      <div class="t">${esc(m.title)}</div>
      <div class="s"><span>${m.date.slice(5).replace("-", "/")}</span>${statusText(m)}</div>
    </li>`).join("") || `<li class="muted" style="cursor:default">${S.query ? "沒有符合的會議" : "還沒有會議"}</li>`;
}

$("#meetingList").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) select(li.dataset.id);
});

let searchTimer;
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { S.query = e.target.value; loadList(); }, 250);
});

function showEmpty() {
  S.selected = null; S.data = null;
  $("#detail").classList.add("hidden");
  $("#empty").classList.remove("hidden");
  checkEnv();
}

async function checkEnv() {
  const env = await api.check_environment();
  const item = (ok, label, fix) => `<div class="${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"} ${label}${ok ? "" : `　<span class="muted">${fix}</span>`}</div>`;
  $("#envCheck").innerHTML =
    item(env.whisper, "Faster-Whisper-XXL", "到設定指定 exe 位置") +
    item(env.opencc, "OpenCC 簡轉繁", "沒有也能用，但會是簡體") +
    item(env.claude || env.codex, `AI 整理（${[env.claude && "claude", env.codex && "codex"].filter(Boolean).join("、") || "未安裝"}）`, "安裝並登入 Claude Code 或 Codex");
}

// ---------- 選取會議 ----------
async function select(id, keepTab = false) {
  const changed = id !== S.selected;
  S.selected = id;
  if (changed) { S.editing = false; S.trFilter = ""; S.trSpeaker = ""; }
  S.data = await api.get_meeting(id);
  if (changed && !keepTab) S.tab = S.data.notes ? "summary" : "transcript";
  $("#empty").classList.add("hidden");
  $("#detail").classList.remove("hidden");
  renderList();
  renderDetail(changed);
}

function renderDetail(changed) {
  const { meeting: m } = S.data;
  const isRec = S.recording && S.recording.id === m.id;
  $("#crumb").textContent = m.title;
  $("#title").textContent = m.title;
  renderMeta();
  renderTemplatePicker();

  $("#tabs").classList.toggle("hidden", isRec);
  $("#recPanel").classList.toggle("hidden", !isRec);
  $("#content").classList.toggle("hidden", isRec);
  $("#player").classList.toggle("hidden", isRec || !S.data.has_audio);
  if (isRec) { renderRecPanel(changed); return; }

  const review = S.data.notes?.reviewItems?.length || 0;
  for (const b of document.querySelectorAll("#tabs button")) {
    b.classList.toggle("active", b.dataset.tab === S.tab);
    if (b.dataset.tab === "review") b.innerHTML = `待確認${review ? `<span class="badge">${review}</span>` : ""}`;
  }
  if (changed) setupPlayer();
  renderMarks();
  renderContent();
}

function renderMeta() {
  const { meeting: m, transcript } = S.data;
  const d = new Date(m.date + "T00:00");
  const parts = [`<span>${m.date} 星期${WEEK[d.getDay()]}</span>`];
  if (m.duration) parts.push(`<span>${Math.max(1, Math.round(m.duration / 60))} 分鐘</span>`);
  const ids = [...new Set((transcript || []).map((s) => s.speaker).filter(Boolean))].sort();
  for (const id of ids)
    parts.push(`<button class="chip" data-sp="${id}" style="color:${speakerColor(id)}" title="點一下改名">${esc(speakerLabel(id, m.speakers))}</button>`);
  $("#meta").innerHTML = parts.join("");
}

$("#meta").addEventListener("click", (e) => {
  const b = e.target.closest("[data-sp]");
  if (b) renameSpeaker(b.dataset.sp);
});

async function renameSpeaker(id) {
  const m = S.data.meeting;
  const name = await promptText("說話者名稱", speakerLabel(id, m.speakers), "改完後，逐字稿和匯出都會使用新名稱。重新整理會議紀錄時 AI 也會用這個名字。");
  if (name === null) return;
  const speakers = { ...m.speakers, [id]: name.trim() };
  if (!name.trim()) delete speakers[id];
  S.data.meeting = await call("update_meeting", m.id, { speakers });
  renderMeta();
  renderContent();
}

function renderTemplatePicker() {
  const sel = $("#templateSel");
  sel.innerHTML = S.templates.map((t) => `<option value="${t.id}" title="${esc(t.description)}">${esc(t.name)}</option>`).join("");
  sel.value = S.data.meeting.template || "general";
}

$("#templateSel").addEventListener("change", async (e) => {
  const m = S.data.meeting;
  S.data.meeting = await call("update_meeting", m.id, { template: e.target.value });
  if (S.data.notes && !BUSY.includes(m.status)) {
    const ok = await confirmBox("用新範本重新整理？", "會用新的範本重新產生會議紀錄，目前的紀錄會被取代。");
    if (ok) { await call("regenerate_notes", m.id); refresh(); }
  }
});

$("#editTitle").addEventListener("click", async () => {
  const m = S.data.meeting;
  const t = await promptText("會議標題", m.title);
  if (t === null || !t.trim()) return;
  S.data.meeting = await call("update_meeting", m.id, { title: t.trim() });
  $("#title").textContent = $("#crumb").textContent = t.trim();
  loadList();
});

// ---------- 分頁 ----------
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  S.tab = b.dataset.tab;
  S.editing = false;
  renderDetail(false);
});

function tsBtn(time) {
  if (!time) return "";
  return `<button class="ts" data-t="${esc(time)}">${esc(time)}</button>`;
}

function editable(text, path) {
  return S.editing ? `<span contenteditable="true" data-path="${path}">${esc(text)}</span>` : esc(text);
}

function itemList(items, path) {
  if (!items?.length) return "";
  return `<ul class="items">${items.map((it, i) => `<li>${editable(it.text, `${path}.${i}.text`)}${tsBtn(it.time)}</li>`).join("")}</ul>`;
}

function renderContent() {
  const el = $("#content");
  const { meeting: m, notes, transcript } = S.data;

  if (BUSY.includes(m.status) && !(S.tab === "transcript" && transcript) && !(S.tab === "notes")) {
    el.innerHTML = `<div class="status-card"><div class="spinner"></div><h3>${esc(m.progress || "處理中")}</h3>
      <p>${m.status === "transcribing" ? "轉錄需要一點時間，可以先去做別的事。" : m.status === "queued" ? "等前一場會議處理完就會開始。" : "AI 正在整理會議紀錄，大約需要 30 秒到 2 分鐘。"}</p>
      <button class="btn" data-act="cancel">取消</button></div>`;
    return;
  }
  if (m.status === "error" && S.tab !== "notes" && !(S.tab === "transcript" && transcript)) {
    el.innerHTML = `<div class="status-card"><h3>處理失敗</h3><pre>${esc(m.error)}</pre>
      <button class="btn primary" data-act="retry">重試</button></div>`;
    return;
  }

  if (S.tab === "transcript") return renderTranscript();
  if (S.tab === "notes") return renderNotesTab();

  if (transcript && !transcript.length) {
    el.innerHTML = `<div class="status-card"><h3>沒有辨識到說話內容</h3>
      <p>這段錄音裡沒有偵測到人聲。請確認錄音來源（麥克風／電腦聲音）是否正確。</p></div>`;
    return;
  }
  if (!notes) {
    el.innerHTML = `<div class="status-card"><h3>還沒有會議紀錄</h3>
      <p>${transcript ? (m.error ? esc(m.error) : "逐字稿已完成，可以請 AI 整理成會議紀錄。") : "還沒有逐字稿。"}</p>
      <button class="btn primary" data-act="${transcript ? "renotes" : "retry"}">${transcript ? "整理會議紀錄" : "開始轉錄"}</button></div>`;
    return;
  }

  const tools = `<div class="content-tools">${S.editing
    ? `<button class="link" data-act="cancel-edit">取消</button><button class="link" data-act="save-edit">儲存</button>`
    : `<button class="link" data-act="copy-md">複製 Markdown</button><button class="link" data-act="edit"><span class="ico edit"></span> 編輯</button>`}</div>`;

  if (S.tab === "summary") {
    let html = tools;
    if (notes.summary?.length) html += `<h2>摘要</h2>${itemList(notes.summary, "summary")}`;
    if (notes.decisions?.length) html += `<h2>決議</h2>${itemList(notes.decisions, "decisions")}`;
    if (notes.actionItems?.length) {
      html += `<h2>待辦</h2><table class="actions"><tr><th>負責人</th><th>事項</th><th>期限</th></tr>${notes.actionItems.map((a, i) => `
        <tr><td class="owner">${editable(a.owner, `actionItems.${i}.owner`) || '<span class="muted">—</span>'}</td>
        <td>${editable(a.task, `actionItems.${i}.task`)}${tsBtn(a.time)}</td>
        <td class="due">${editable(a.due, `actionItems.${i}.due`) || '<span class="muted">—</span>'}</td></tr>`).join("")}</table>`;
    }
    if (notes.openQuestions?.length) html += `<h2>未決問題</h2>${itemList(notes.openQuestions, "openQuestions")}`;
    if (html === tools) html += `<p class="muted">這個範本沒有摘要段落，請看「詳細」。</p>`;
    el.innerHTML = html;
  } else if (S.tab === "details") {
    let html = tools;
    for (const [ci, c] of (notes.custom || []).entries())
      if (c.items?.length) html += `<h2>${esc(c.title)}</h2>${itemList(c.items, `custom.${ci}.items`)}`;
    if (notes.topics?.length) {
      html += `<h2>議題詳細</h2>`;
      for (const [ti, t] of notes.topics.entries())
        html += `<h3>${editable(t.title, `topics.${ti}.title`)}${tsBtn(t.start)}</h3>${itemList(t.points, `topics.${ti}.points`)}`;
    }
    if (html === tools) html += `<p class="muted">這個範本沒有詳細段落。</p>`;
    el.innerHTML = html;
  } else if (S.tab === "review") {
    renderReview();
  }
}

// ---------- 逐字稿 ----------
function renderTranscript() {
  const el = $("#content");
  const { meeting: m, transcript } = S.data;
  if (transcript && !transcript.length && !S.trFilter) {
    el.innerHTML = `<div class="status-card"><h3>沒有辨識到說話內容</h3><p>這段錄音裡沒有偵測到人聲。</p></div>`;
    return;
  }
  if (!transcript) {
    el.innerHTML = `<div class="status-card"><h3>還沒有逐字稿</h3><button class="btn primary" data-act="retry">開始轉錄</button></div>`;
    return;
  }
  const ids = [...new Set(transcript.map((s) => s.speaker).filter(Boolean))].sort();
  el.innerHTML = `
    <div class="tr-tools">
      <div class="search"><span class="search-icon"></span><input id="trSearch" placeholder="搜尋逐字稿" value="${esc(S.trFilter)}"></div>
      ${ids.length ? `<select id="trSpeaker"><option value="">所有說話者</option>${ids.map((id) =>
        `<option value="${id}" ${S.trSpeaker === id ? "selected" : ""}>${esc(speakerLabel(id, m.speakers))}</option>`).join("")}</select>` : ""}
    </div>
    <div id="trRows"></div>`;
  renderTranscriptRows();
  const input = $("#trSearch");
  input.addEventListener("input", () => { S.trFilter = input.value; renderTranscriptRows(); });
  $("#trSpeaker")?.addEventListener("change", (e) => { S.trSpeaker = e.target.value; renderTranscriptRows(); });
}

function renderTranscriptRows() {
  const { meeting: m, transcript } = S.data;
  const q = S.trFilter.trim().toLowerCase();
  const hl = (text) => {
    const safe = esc(text);
    if (!q) return safe;
    const re = new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    return safe.replace(re, (x) => `<mark>${x}</mark>`);
  };
  const rows = transcript.map((s, i) => ({ s, i }))
    .filter(({ s }) => (!S.trSpeaker || s.speaker === S.trSpeaker) && (!q || s.text.toLowerCase().includes(q)));
  $("#trRows").innerHTML = rows.map(({ s, i }) => `
    <div class="tr-row" data-i="${i}">
      <button class="ts" data-sec="${s.start}">${fmt(s.start)}</button>
      <div class="who" style="color:${speakerColor(s.speaker)}">${esc(speakerLabel(s.speaker, m.speakers))}</div>
      <div class="txt">${hl(s.text)}</div>
    </div>`).join("") || `<p class="muted">沒有符合的段落</p>`;
  highlightNow(true);
}

// ---------- 待確認 ----------
function renderReview() {
  const items = S.data.notes.reviewItems || [];
  if (!items.length) {
    $("#content").innerHTML = `<div class="status-card"><h3>沒有待確認的問題</h3><p>AI 對這份紀錄沒有疑問。</p></div>`;
    return;
  }
  $("#content").innerHTML = `<div class="review">
    <p class="hint" style="margin-top:18px">逐字稿是語音辨識產生的，AI 不確定的地方列在這裡。回答後按「依回答修正」，AI 會修正整份紀錄。不需要回答的題目可以留空。</p>
    ${items.map((r, i) => `<div class="review-item"><div class="q">${esc(r.question)}${tsBtn(r.time)}</div>
      <input data-i="${i}" placeholder="你的回答（留空表示略過）"></div>`).join("")}
    <div style="display:flex;justify-content:flex-end;margin-top:14px"><button class="btn primary" data-act="answer">依回答修正</button></div></div>`;
}

async function submitReview() {
  const items = S.data.notes.reviewItems;
  const answers = [...document.querySelectorAll(".review-item input")]
    .map((inp) => ({ ...items[+inp.dataset.i], answer: inp.value.trim() }))
    .filter((a) => a.answer);
  if (!answers.length) return toast("請至少回答一題");
  const btn = document.querySelector('[data-act="answer"]');
  btn.disabled = true; btn.textContent = "修正中…";
  try {
    await call("answer_review", S.selected, answers);
    toast("已依回答修正");
  } finally {
    await select(S.selected, true);
    loadList();
  }
}

// ---------- 筆記分頁 ----------
function renderNotesTab() {
  const m = S.data.meeting;
  const marks = (m.bookmarks || []).map((b) => `<li><span class="ico star"></span><button class="ts" data-sec="${b.time}" style="padding:0">${fmt(b.time)}</button>${esc(b.label || "")}</li>`).join("");
  $("#content").innerHTML = `<div class="notes-tab">
    <h2>我的筆記</h2><p class="hint">這裡的內容在整理會議紀錄時會優先納入。修改後按「重新整理會議紀錄」才會反映到紀錄裡。</p>
    <textarea id="userNotes" spellcheck="false">${esc(m.user_notes || "")}</textarea>
    ${marks ? `<h2>重點標記</h2><ul class="marks">${marks}</ul>` : ""}</div>`;
  $("#userNotes").addEventListener("change", async (e) => {
    S.data.meeting = await call("update_meeting", m.id, { user_notes: e.target.value });
    toast("筆記已儲存");
  });
}

// ---------- 編輯會議紀錄 ----------
async function saveEdits() {
  const notes = structuredClone(S.data.notes);
  for (const el of document.querySelectorAll("[data-path]")) {
    const keys = el.dataset.path.split(".");
    let obj = notes;
    for (const k of keys.slice(0, -1)) obj = obj[k];
    obj[keys.at(-1)] = el.innerText.trim();
  }
  await call("save_notes", S.selected, notes);
  S.data.notes = notes;
  S.editing = false;
  renderContent();
  toast("已儲存");
}

// ---------- 內容區的按鈕 ----------
$("#content").addEventListener("click", async (e) => {
  const ts = e.target.closest(".ts");
  if (ts) {
    const sec = ts.dataset.sec !== undefined ? +ts.dataset.sec : parseTime(ts.dataset.t);
    if (sec !== null) seek(sec, true);
    return;
  }
  const act = e.target.closest("[data-act]")?.dataset.act;
  const id = S.selected;
  if (act === "cancel") { await call("cancel", id); refresh(); }
  else if (act === "retry") { await call("process", id); refresh(); }
  else if (act === "renotes") { await call("regenerate_notes", id); refresh(); }
  else if (act === "edit") { S.editing = true; renderContent(); }
  else if (act === "cancel-edit") { S.editing = false; renderContent(); }
  else if (act === "save-edit") saveEdits();
  else if (act === "answer") submitReview();
  else if (act === "copy-md") { await navigator.clipboard.writeText(await api.markdown(id, false)); toast("已複製 Markdown"); }
});

// ---------- 更多選單 ----------
$("#moreBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $("#moreMenu");
  const busy = BUSY.includes(S.data?.meeting.status);
  menu.innerHTML = `
    <button data-m="export-notes">匯出會議紀錄（.md）</button>
    <button data-m="export-all">匯出紀錄＋逐字稿（.md）</button>
    <button data-m="export-transcript">匯出逐字稿（.md）</button>
    <button data-m="export-srt">匯出字幕（.srt）</button>
    <button data-m="export-audio">匯出音檔（.m4a）</button>
    <button data-m="copy-all">複製紀錄＋逐字稿</button>
    <hr>
    <button data-m="renotes" ${busy ? "disabled" : ""}>重新整理會議紀錄</button>
    <button data-m="retranscribe" ${busy ? "disabled" : ""}>重新轉錄</button>
    <button data-m="folder">開啟檔案位置</button>
    <hr>
    <button data-m="delete" class="danger">移到垃圾桶</button>`;
  menu.classList.toggle("hidden");
});
document.addEventListener("click", () => $("#moreMenu").classList.add("hidden"));
$("#moreMenu").addEventListener("click", async (e) => {
  const m = e.target.closest("[data-m]")?.dataset.m;
  if (!m) return;
  const id = S.selected;
  $("#moreMenu").classList.add("hidden");
  if (m.startsWith("export-")) {
    const kind = { "export-notes": "notes", "export-all": "all", "export-transcript": "transcript", "export-srt": "srt", "export-audio": "audio" }[m];
    const path = await call("export", id, kind);
    if (path) toast(`已匯出：${path}`);
  } else if (m === "copy-all") {
    await navigator.clipboard.writeText(await api.markdown(id, true)); toast("已複製");
  } else if (m === "renotes") {
    if (await confirmBox("重新整理會議紀錄？", "目前的紀錄（包含你編輯過的內容）會被取代。")) { await call("regenerate_notes", id); refresh(); }
  } else if (m === "retranscribe") {
    if (await confirmBox("重新轉錄？", "逐字稿與會議紀錄都會重新產生。說話者名稱會保留，但編號可能對不上。")) { await call("retranscribe", id); refresh(); }
  } else if (m === "folder") {
    api.open_folder(id);
  } else if (m === "delete") {
    if (await confirmBox("移到垃圾桶？", "之後可以從垃圾桶還原。")) {
      await call("delete_meeting", id);
      S.selected = null;
      await loadList();
      updateTrashLabel();
    }
  }
});

// ---------- 播放器 ----------
const audio = $("#audio");
function setupPlayer() {
  if (!S.data.has_audio) { audio.removeAttribute("src"); return; }
  audio.src = `/media/${S.selected}/audio.m4a`;
  audio.playbackRate = +$("#rateSel").value;
  $("#trackFill").style.width = "0";
  $("#curTime").textContent = "00:00";
  $("#durTime").textContent = fmt(S.data.meeting.duration);
}
function renderMarks() {
  const dur = S.data.meeting.duration || audio.duration || 0;
  $("#trackMarks").innerHTML = dur ? (S.data.meeting.bookmarks || [])
    .map((b) => `<i style="left:${(b.time / dur) * 100}%"></i>`).join("") : "";
}
function seek(sec, play) {
  if (!S.data?.has_audio) return;
  audio.currentTime = sec;
  if (play) audio.play();
}
$("#playBtn").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
audio.addEventListener("play", () => $("#playBtn").classList.add("playing"));
audio.addEventListener("pause", () => $("#playBtn").classList.remove("playing"));
audio.addEventListener("loadedmetadata", () => { $("#durTime").textContent = fmt(audio.duration); renderMarks(); });
audio.addEventListener("timeupdate", () => {
  const d = audio.duration || S.data?.meeting.duration || 1;
  $("#curTime").textContent = fmt(audio.currentTime);
  $("#trackFill").style.width = `${(audio.currentTime / d) * 100}%`;
  highlightNow(false);
});
$("#track").addEventListener("click", (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  const d = audio.duration || S.data.meeting.duration;
  if (d) seek(((e.clientX - r.left) / r.width) * d, false);
});
$("#rateSel").addEventListener("change", (e) => { audio.playbackRate = +e.target.value; });

let lastNow = -1;
function highlightNow(force) {
  if (S.tab !== "transcript" || !S.data?.transcript) return;
  const t = audio.currentTime;
  const tr = S.data.transcript;
  let idx = -1;
  for (let i = 0; i < tr.length; i++) { if (tr[i].start <= t + 0.05) idx = i; else break; }
  if (idx === lastNow && !force) return;
  lastNow = idx;
  document.querySelectorAll(".tr-row.now").forEach((r) => r.classList.remove("now"));
  const row = document.querySelector(`.tr-row[data-i="${idx}"]`);
  if (row && !audio.paused) {
    row.classList.add("now");
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } else if (row) row.classList.add("now");
}

// ---------- 錄音 ----------
const MODES = { both: "麥克風＋電腦聲音（線上會議）", mic: "只錄麥克風（實體會議）", system: "只錄電腦聲音" };

$("#recBtn").addEventListener("click", startRecording);
$("#emptyRec").addEventListener("click", startRecording);
$("#recModeBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  popMenu(e.currentTarget, Object.entries(MODES).map(([k, v]) => ({ label: (S.recMode === k ? "✓ " : "　") + v, fn: () => {
    S.recMode = k; localStorage.setItem("recMode", k); toast(`錄音來源：${v}`);
  } })));
});

async function startRecording() {
  if (S.recording) return;
  const m = await call("start_recording", S.recMode);
  S.recording = { id: m.id, elapsed: 0, paused: false, levels: {} };
  recNotesLoaded = null;
  $("#recGroup").classList.add("hidden");
  $("#recLive").classList.remove("hidden");
  await loadList();
  await select(m.id);
}

async function stopRecording() {
  if (!S.recording) return;
  const id = S.recording.id;
  await saveRecNotes();
  await call("stop_recording", true);
  S.recording = null;
  $("#recGroup").classList.remove("hidden");
  $("#recLive").classList.add("hidden");
  S.tab = "transcript";
  await loadList();
  await select(id, true);
}
$("#stopBtn").addEventListener("click", stopRecording);
$("#sideStop").addEventListener("click", stopRecording);

$("#pauseBtn").addEventListener("click", async () => {
  if (!S.recording) return;
  if (S.recording.paused) await call("resume_recording"); else await call("pause_recording");
  S.recording.paused = !S.recording.paused;
  updateRecUI();
});

async function addMark() {
  if (!S.recording) return;
  const t = await call("add_bookmark", "");
  toast(`已標記 ${fmt(t)}`);
  S.data = await api.get_meeting(S.recording.id);
  renderRecMarks();
}
$("#markBtn").addEventListener("click", addMark);

let recNotesLoaded = null;
function renderRecPanel(changed) {
  const mode = S.data.meeting.mode;
  $("#micMeterRow").classList.toggle("hidden", mode === "system");
  $("#sysMeterRow").classList.toggle("hidden", mode === "mic");
  for (const id of ["#micMeter", "#sysMeter"])
    if (!$(id).children.length) $(id).innerHTML = "<i></i>".repeat(16);
  if (recNotesLoaded !== S.data.meeting.id) {
    $("#recNotes").value = S.data.meeting.user_notes || "";
    recNotesLoaded = S.data.meeting.id;
  }
  renderRecMarks();
  updateRecUI();
}
function renderRecMarks() {
  const marks = S.data.meeting.bookmarks || [];
  $("#recMarks").innerHTML = marks.length
    ? marks.map((b) => `<li><span class="ico star"></span><span class="ts" style="padding:0">${fmt(b.time)}</span></li>`).join("")
    : `<li class="muted">還沒有標記</li>`;
}
function updateRecUI() {
  const r = S.recording;
  if (!r) return;
  const t = fmt(r.elapsed);
  $("#sideTimer").textContent = t;
  $("#recLive").classList.toggle("paused", r.paused);
  if (S.selected !== r.id) return;
  $("#recTimer").textContent = t;
  $("#recState").textContent = r.paused ? "已暫停" : "錄音中";
  $("#recPanel").classList.toggle("paused", r.paused);
  $("#pauseBtn").innerHTML = r.paused ? `<span class="ico play"></span>繼續` : `<span class="ico pause"></span>暫停`;
  setMeter("#micMeter", r.levels?.mic);
  setMeter("#sysMeter", r.levels?.system);
}
function setMeter(sel, level) {
  const bars = $(sel).children;
  const on = Math.round((level || 0) * bars.length);
  for (let i = 0; i < bars.length; i++) {
    bars[i].classList.toggle("on", i < on);
    bars[i].classList.toggle("hot", i >= bars.length - 3);
  }
}

// 錄音筆記：換行時自動加上錄音時間
$("#recNotes").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !S.recording || e.isComposing) return;
  e.preventDefault();
  const ta = e.target;
  const stamp = `\n[${fmt(S.recording.elapsed)}] `;
  ta.setRangeText(stamp, ta.selectionStart, ta.selectionEnd, "end");
});
$("#recNotes").addEventListener("focus", (e) => {
  if (!e.target.value && S.recording) e.target.value = `[${fmt(S.recording.elapsed)}] `;
});
let notesTimer;
$("#recNotes").addEventListener("input", () => { clearTimeout(notesTimer); notesTimer = setTimeout(saveRecNotes, 800); });
async function saveRecNotes() {
  clearTimeout(notesTimer);
  if (!S.recording) return;
  await api.update_meeting(S.recording.id, { user_notes: $("#recNotes").value });
}

// ---------- 匯入 ----------
async function importFiles() {
  const ids = await call("import_files");
  if (ids?.length) { toast(`已匯入 ${ids.length} 個檔案，開始轉錄`); await loadList(); select(ids[0]); }
}
$("#importBtn").addEventListener("click", importFiles);
$("#emptyImport").addEventListener("click", importFiles);

// ---------- 輪詢 ----------
let tick = 0;
async function poll() {
  try {
    const st = await api.state();
    if (st.recording) {
      S.recording = st.recording;
      updateRecUI();
    }
    if (++tick % 4 === 0) {
      const before = JSON.stringify(S.meetings.map((m) => [m.id, m.status, m.progress, m.title, m.review_count]));
      S.meetings = await api.list_meetings(S.query);
      const after = JSON.stringify(S.meetings.map((m) => [m.id, m.status, m.progress, m.title, m.review_count]));
      if (before !== after) {
        renderList();
        const cur = S.meetings.find((m) => m.id === S.selected);
        if (cur && S.data && !(S.recording && S.recording.id === cur.id) &&
            (cur.status !== S.data.meeting.status || cur.progress !== S.data.meeting.progress || cur.title !== S.data.meeting.title)) {
          const wasBusy = BUSY.includes(S.data.meeting.status);
          S.data = await api.get_meeting(S.selected);
          if (wasBusy && S.data.meeting.status === "done" && S.data.notes && S.tab === "transcript" && !audio.currentTime) S.tab = "summary";
          if (document.activeElement?.closest?.("#content") && S.tab !== "transcript") renderMeta();
          else renderDetail(false);
          if (!audio.src && S.data.has_audio) setupPlayer();
        }
      }
    }
  } finally {
    setTimeout(poll, 500);
  }
}
async function refresh() {
  await loadList();
  if (S.selected) await select(S.selected, true);
}

// ---------- 垃圾桶 ----------
async function updateTrashLabel() {
  const items = await api.trash();
  $("#trashLabel").textContent = `垃圾桶 (${items.length})`;
}
$("#trashBtn").addEventListener("click", async () => {
  const items = await api.trash();
  openModal(`<h3>垃圾桶</h3>
    ${items.length ? `<ul class="trash-list">${items.map((m) => `<li><span>${esc(m.title)}<br><small class="muted">${m.date}</small></span>
      <button class="btn" data-restore="${m.id}">還原</button></li>`).join("")}</ul>` : `<p class="muted">垃圾桶是空的</p>`}
    <div class="actions">${items.length ? `<button class="btn" id="emptyTrash" style="color:var(--danger)">清空垃圾桶</button>` : ""}<button class="btn primary" data-close>關閉</button></div>`);
  $("#modalBox").onclick = async (e) => {
    const r = e.target.closest("[data-restore]");
    if (r) { await call("restore_meeting", r.dataset.restore); closeModal(); await loadList(); updateTrashLabel(); select(r.dataset.restore); }
    if (e.target.id === "emptyTrash" && await confirmBox("清空垃圾桶？", "裡面的會議會永久刪除，無法復原。")) {
      await call("empty_trash"); updateTrashLabel();
    }
  };
});

// ---------- 設定 ----------
$("#settingsBtn").addEventListener("click", openSettings);
async function openSettings() {
  const { settings: s, devices, backends, version } = await api.get_settings();
  const opt = (list, cur, def) => `<option value="-1">${def}</option>` + list.map((d) => `<option value="${d.index}" ${d.index === cur ? "selected" : ""}>${esc(d.name)}</option>`).join("");
  openModal(`<h3>設定 <small class="muted" style="font-weight:400;font-size:13px">v${esc(version)}</small></h3>
    <div class="row"><label class="lbl">AI 整理</label>
      <div class="inline">
        <select id="sBackend">
          <option value="claude" ${s.llm_backend === "claude" ? "selected" : ""}>Claude Code${backends.claude ? "" : "（未安裝）"}</option>
          <option value="codex" ${s.llm_backend === "codex" ? "selected" : ""}>Codex${backends.codex ? "" : "（未安裝）"}</option>
          <option value="none" ${s.llm_backend === "none" ? "selected" : ""}>不整理，只要逐字稿</option>
        </select>
        <input type="text" id="sLlmModel" value="${esc(s.llm_model)}" placeholder="模型（留空用預設）">
      </div>
      <div class="help">使用你已經登入的 CLI，只會送出逐字稿文字，不會上傳音檔。</div></div>
    <div class="row"><label class="lbl">詞彙表（一行一個：人名、產品名、專有名詞）</label>
      <textarea id="sGlossary" rows="4">${esc((s.glossary || []).join("\n"))}</textarea>
      <div class="help">轉錄時當作提示，AI 整理時也會依這裡的寫法校正。</div></div>
    <div class="row"><label class="lbl">麥克風</label><select id="sMic">${opt(devices.mics, s.mic_device, "系統預設")}</select></div>
    <div class="row"><label class="lbl">電腦聲音來源</label><select id="sLoop">${opt(devices.loopbacks, s.loopback_device, "預設喇叭")}</select></div>
    <div class="row"><label class="lbl">Faster-Whisper-XXL 位置</label><input type="text" id="sWhisper" value="${esc(s.whisper_exe)}"></div>
    <div class="row inline">
      <div><label class="lbl">模型</label><input type="text" id="sModel" value="${esc(s.whisper_model)}"></div>
      <div><label class="lbl">語言（留空自動偵測）</label><input type="text" id="sLang" value="${esc(s.whisper_language)}"></div>
    </div>
    <div class="row"><label class="check"><input type="checkbox" id="sMulti" ${s.multilingual ? "checked" : ""}> 逐段偵測語言（中英夾雜的會議建議開啟）</label></div>
    <div class="row inline">
      <label class="check"><input type="checkbox" id="sDiarize" ${s.diarize ? "checked" : ""}> 分辨說話者</label>
      <div><label class="lbl">最少人數（0＝自動）</label><input type="number" min="0" id="sMin" value="${s.min_speakers}"></div>
      <div><label class="lbl">最多人數（0＝自動）</label><input type="number" min="0" id="sMax" value="${s.max_speakers}"></div>
    </div>
    <div class="row"><label class="lbl">OpenCC 位置（簡轉繁）</label><input type="text" id="sOpencc" value="${esc(s.opencc_exe)}"></div>
    <div class="row"><label class="lbl">資料夾</label><input type="text" id="sData" value="${esc(s.data_dir)}"></div>
    <div class="actions"><button class="btn" data-close>取消</button><button class="btn primary" id="sSave">儲存</button></div>`);
  $("#sSave").onclick = async () => {
    await call("save_settings", {
      llm_backend: $("#sBackend").value, llm_model: $("#sLlmModel").value.trim(),
      glossary: $("#sGlossary").value.split("\n").map((x) => x.trim()).filter(Boolean),
      mic_device: +$("#sMic").value, loopback_device: +$("#sLoop").value,
      whisper_exe: $("#sWhisper").value.trim(), whisper_model: $("#sModel").value.trim(), whisper_language: $("#sLang").value.trim(), multilingual: $("#sMulti").checked,
      diarize: $("#sDiarize").checked, min_speakers: +$("#sMin").value || 0, max_speakers: +$("#sMax").value || 0,
      opencc_exe: $("#sOpencc").value.trim(), data_dir: $("#sData").value.trim(),
    });
    closeModal();
    toast("設定已儲存");
    S.selected = null;
    loadList();
  };
}

// ---------- 對話框 ----------
function openModal(html) {
  $("#modalBox").innerHTML = html;
  $("#modalBox").onclick = null;
  $("#modal").classList.remove("hidden");
  $("#modalBox").querySelector("input,textarea,select")?.focus();
}
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", (e) => {
  if (e.target.id === "modal" || e.target.closest("[data-close]")) closeModal();
});
function promptText(title, value = "", help = "") {
  return new Promise((resolve) => {
    openModal(`<h3>${esc(title)}</h3><input type="text" id="pIn" value="${esc(value)}">${help ? `<div class="help">${esc(help)}</div>` : ""}
      <div class="actions"><button class="btn" id="pNo">取消</button><button class="btn primary" id="pOk">確定</button></div>`);
    const input = $("#pIn");
    input.select();
    const done = (v) => { closeModal(); resolve(v); };
    $("#pOk").onclick = () => done(input.value);
    $("#pNo").onclick = () => done(null);
    input.onkeydown = (e) => { if (e.key === "Enter" && !e.isComposing) done(input.value); if (e.key === "Escape") done(null); };
  });
}
function confirmBox(title, text) {
  return new Promise((resolve) => {
    openModal(`<h3>${esc(title)}</h3><p style="color:var(--text-2)">${esc(text)}</p>
      <div class="actions"><button class="btn" id="cNo">取消</button><button class="btn primary" id="cOk">確定</button></div>`);
    $("#cOk").onclick = () => { closeModal(); resolve(true); };
    $("#cNo").onclick = () => { closeModal(); resolve(false); };
  });
}
function popMenu(anchor, items) {
  document.querySelector(".menu-pop")?.remove();
  const r = anchor.getBoundingClientRect();
  const menu = document.createElement("div");
  menu.className = "menu menu-pop";
  menu.style.left = `${r.left}px`;
  menu.style.top = `${r.bottom + 4}px`;
  items.forEach((it) => {
    const b = document.createElement("button");
    b.textContent = it.label;
    b.onclick = () => { menu.remove(); it.fn(); };
    menu.appendChild(b);
  });
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener("click", () => menu.remove(), { once: true }));
}

// ---------- 快捷鍵 ----------
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "r") { e.preventDefault(); S.recording ? stopRecording() : startRecording(); }
  else if (e.ctrlKey && e.key.toLowerCase() === "b" && S.recording) { e.preventDefault(); addMark(); }
  else if (e.key === "Escape") closeModal();
  else if (e.code === "Space" && !e.target.closest("input,textarea,[contenteditable=true],select,button") && S.data?.has_audio && !S.recording) {
    e.preventDefault(); audio.paused ? audio.play() : audio.pause();
  }
});

// ---------- 啟動 ----------
async function init() {
  api = window.pywebview.api;
  S.templates = await api.templates();
  const st = await api.state();
  if (st.recording) {
    S.recording = st.recording;
    $("#recGroup").classList.add("hidden");
    $("#recLive").classList.remove("hidden");
  }
  await loadList();
  updateTrashLabel();
  poll();
}
// 開發模式（python main.py --dev）：在一般瀏覽器開啟時，透過 HTTP 呼叫同一組 API
function httpApi() {
  return new Proxy({}, { get: (_, name) => async (...args) => {
    const r = await fetch(`/api/${name}`, { method: "POST", body: JSON.stringify(args) });
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    return j.result;
  } });
}
if (window.pywebview?.api) init();
else if (new URLSearchParams(location.search).has("dev")) { window.pywebview = { api: httpApi() }; init(); }
else window.addEventListener("pywebviewready", init);
