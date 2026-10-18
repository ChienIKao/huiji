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
  data: null,      // {meeting, transcript, notes, has_audio, video}
  tab: "summary",
  query: "",
  recording: null, // {id, elapsed, paused, levels}
  recMode: localStorage.getItem("recMode") || "both",
  recScreen: localStorage.getItem("recScreen") === "1",
  recDir: localStorage.getItem("recDir") || "",
  trEditing: false,
  showVideo: localStorage.getItem("showVideo") !== "0",
  editing: false,
  trFilter: "",
  trSpeaker: "",
  templates: [],
};
const BUSY = ["queued", "transcribing", "summarizing"];
const hasMedia = () => !!(S.data && (S.data.has_audio || S.data.video));

// ---------- 會議列表 ----------
async function loadList() {
  S.meetings = await api.list_meetings(S.query);
  renderList();
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
      <button class="row-more" data-more="${m.id}" title="更多"></button>
    </li>`).join("") || `<li class="muted" style="cursor:default">${S.query ? "沒有符合的會議" : "還沒有會議"}</li>`;
}

$("#meetingList").addEventListener("click", (e) => {
  const more = e.target.closest("[data-more]");
  if (more) {
    e.stopPropagation();
    const id = more.dataset.more;
    popMenu(more, [
      { label: "重新命名", fn: () => renameMeeting(id) },
      { label: "開啟檔案位置", fn: () => api.open_folder(id) },
      { sep: true },
      { label: "移到垃圾桶", danger: true, disabled: S.recording?.id === id, fn: () => deleteMeeting(id) },
    ]);
    return;
  }
  const li = e.target.closest("li[data-id]");
  if (li) select(li.dataset.id);
});

async function renameMeeting(id) {
  const m = S.meetings.find((x) => x.id === id) || S.data?.meeting;
  const t = await promptText("會議標題", m?.title || "", "資料夾名稱也會跟著改。");
  if (t === null || !t.trim()) return;
  await call("update_meeting", id, { title: t.trim() });
  await loadList();
  if (S.selected === id) await select(id, true);
}

async function deleteMeeting(id) {
  if (!await confirmBox("移到垃圾桶？", "之後可以從垃圾桶還原。")) return;
  await call("delete_meeting", id);
  await loadList();
  updateTrashLabel();
  if (S.selected === id) showHome();
}

let searchTimer;
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { S.query = e.target.value; loadList(); }, 250);
});

async function checkEnv() {
  const env = await api.check_environment();
  const item = (ok, label, detail, goto) => `<div class="env-row"><span class="status ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>
    <span class="env-label">${label}</span><span class="muted">${detail}</span><button class="link" data-goto="${goto}">設定</button></div>`;
  const ai = [env.claude && "Claude Code", env.codex && "Codex"].filter(Boolean).join("、");
  $("#envCheck").innerHTML =
    item(env.whisper, "轉錄", env.whisper ? "Faster-Whisper-XXL" : "找不到 Faster-Whisper-XXL，請指定位置", "set-whisper") +
    item(env.opencc, "簡轉繁", env.opencc ? "OpenCC" : "沒有 OpenCC 也能用，但逐字稿會是簡體", "set-whisper") +
    item(env.claude || env.codex, "AI 整理", ai || "請安裝並登入 Claude Code 或 Codex", "set-ai");
}

// ---------- 選取會議 ----------
async function select(id, keepTab = false) {
  const changed = id !== S.selected;
  S.selected = id;
  if (changed) { S.editing = false; S.trEditing = false; S.trFilter = ""; S.trSpeaker = ""; }
  S.data = await api.get_meeting(id);
  if (changed && !keepTab) S.tab = S.data.notes ? "summary" : "transcript";
  showView("detail");
  S.selected = id;
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
  $("#player").classList.toggle("hidden", isRec || !hasMedia());
  $("#videoToggle").classList.toggle("hidden", !S.data.video);
  $("#videoToggle").classList.toggle("on", S.showVideo);
  $("#videoPane").classList.toggle("hidden", isRec || !S.data.video || !S.showVideo);
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

$("#editTitle").addEventListener("click", () => renameMeeting(S.selected));

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
      <div class="spacer"></div>
      ${S.trEditing
        ? `<button class="btn" data-act="tr-cancel">取消</button><button class="btn primary" data-act="tr-save">儲存逐字稿</button>`
        : `<button class="btn" data-act="tr-edit"><span class="ico edit"></span>修改逐字稿</button>`}
    </div>
    ${S.trEditing ? `<p class="hint">直接點文字修改錯字；說話者可以用下拉選單改。存檔後可以選擇要不要用新的逐字稿重新整理會議紀錄。</p>` : ""}
    <div id="trRows" class="${S.trEditing ? "editing" : ""}"></div>`;
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
  const ids = [...new Set(transcript.map((s) => s.speaker).filter(Boolean))].sort();
  const who = (s, i) => S.trEditing
    ? `<select class="who-sel" data-i="${i}">${ids.map((id) => `<option value="${id}" ${id === s.speaker ? "selected" : ""}>${esc(speakerLabel(id, m.speakers))}</option>`).join("")}
        <option value="" ${s.speaker ? "" : "selected"}>（不指定）</option></select>`
    : `<div class="who" style="color:${speakerColor(s.speaker)}">${esc(speakerLabel(s.speaker, m.speakers))}</div>`;
  $("#trRows").innerHTML = rows.map(({ s, i }) => `
    <div class="tr-row" data-i="${i}">
      <button class="ts" data-sec="${s.start}">${fmt(s.start)}</button>
      ${who(s, i)}
      <div class="txt" ${S.trEditing ? `contenteditable="true" data-i="${i}"` : ""}>${S.trEditing ? esc(s.text) : hl(s.text)}</div>
    </div>`).join("") || `<p class="muted">沒有符合的段落</p>`;
  highlightNow(true);
}

async function saveTranscript() {
  const tr = structuredClone(S.data.transcript);
  for (const el of document.querySelectorAll("#trRows .txt[data-i]")) tr[+el.dataset.i].text = el.innerText.replace(/\s+/g, " ").trim();
  for (const el of document.querySelectorAll("#trRows .who-sel")) tr[+el.dataset.i].speaker = el.value || null;
  await call("save_transcript", S.selected, tr);
  S.trEditing = false;
  await select(S.selected, true);
  toast("逐字稿已儲存");
  if (S.data.notes && await confirmBox("要用修改後的逐字稿重新整理會議紀錄嗎？", "目前的會議紀錄會被取代。")) {
    await call("regenerate_notes", S.selected);
    refresh();
  }
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
  else if (act === "tr-edit") { S.trEditing = true; renderContent(); }
  else if (act === "tr-cancel") { S.trEditing = false; renderContent(); }
  else if (act === "tr-save") saveTranscript();
  else if (act === "copy-md") { await navigator.clipboard.writeText(await api.markdown(id, false)); toast("已複製 Markdown"); }
});

// ---------- 匯出、更多 ----------
async function exportAs(kind) {
  const path = await call("export", S.selected, kind);
  if (path) toast(`已匯出：${path}`);
}
$("#exportBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const id = S.selected;
  const d = S.data;
  const items = [
    { title: "會議紀錄" },
    { label: "會議紀錄（.md）", disabled: !d.notes, fn: () => exportAs("notes") },
    { label: "會議紀錄＋逐字稿（.md）", disabled: !d.notes, fn: () => exportAs("all") },
    { label: "逐字稿（.md）", disabled: !d.transcript, fn: () => exportAs("transcript") },
    { label: "複製會議紀錄（Markdown）", disabled: !d.notes, fn: async () => { await navigator.clipboard.writeText(await api.markdown(id, false)); toast("已複製"); } },
    { title: "字幕" },
    { label: "YouTube 用（.srt，不含說話者）", disabled: !d.transcript, fn: () => exportAs("srt_plain") },
    { label: "含說話者名稱（.srt）", disabled: !d.transcript, fn: () => exportAs("srt") },
    { title: "影片與音檔" },
  ];
  if (d.video) items.push({ label: `影片（.${d.video.split(".").pop()}）`, fn: () => exportAs("video") });
  items.push({ label: "音檔（.m4a）", disabled: !d.has_audio, fn: () => exportAs("audio") });
  popMenu(e.currentTarget, items);
});
$("#moreBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const id = S.selected;
  const busy = BUSY.includes(S.data?.meeting.status);
  popMenu(e.currentTarget, [
    { label: "重新命名", fn: () => renameMeeting(id) },
    { label: "重新整理會議紀錄", disabled: busy || !S.data.transcript, fn: async () => {
      if (await confirmBox("重新整理會議紀錄？", "目前的紀錄（包含你編輯過的內容）會被取代。")) { await call("regenerate_notes", id); refresh(); }
    } },
    { label: "重新轉錄", disabled: busy, fn: async () => {
      if (await confirmBox("重新轉錄？", "逐字稿與會議紀錄都會重新產生，你修改過的逐字稿也會被取代。說話者名稱會保留，但編號可能對不上。")) { await call("retranscribe", id); refresh(); }
    } },
    { label: "開啟檔案位置", fn: () => api.open_folder(id) },
    { sep: true },
    { label: "移到垃圾桶", danger: true, fn: () => deleteMeeting(id) },
  ]);
});

// ---------- 播放器 ----------
const audio = $("#audio");
function setupPlayer() {
  if (!hasMedia()) { audio.removeAttribute("src"); return; }
  audio.src = `/media/${S.selected}/${S.data.video || "audio.m4a"}`;
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
  if (!hasMedia()) return;
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
$("#videoToggle").addEventListener("click", () => {
  S.showVideo = !S.showVideo;
  localStorage.setItem("showVideo", S.showVideo ? "1" : "0");
  renderDetail(false);
});

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
$("#recModeBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const items = Object.entries(MODES).map(([k, v]) => ({ label: (S.recMode === k ? "✓ " : "　") + v, fn: () => {
    S.recMode = k; localStorage.setItem("recMode", k); toast(`錄音來源：${v}`); renderHomeMode();
  } }));
  items.push({ sep: true }, { label: (S.recScreen ? "✓ " : "　") + "同時錄製螢幕", fn: () => {
    S.recScreen = !S.recScreen; localStorage.setItem("recScreen", S.recScreen ? "1" : "0");
    $("#homeScreen").checked = S.recScreen; $("#homeSource").disabled = !S.recScreen;
    toast(S.recScreen ? "錄音時會同時錄製螢幕（可以在首頁選擇要錄哪個螢幕或視窗）" : "只錄聲音");
    updateRecTooltip();
  } });
  popMenu(e.currentTarget, items);
});

async function startRecording() {
  if (S.recording) return;
  const m = await call("start_recording", S.recMode, S.recScreen, S.recDir);
  S.recording = { id: m.id, elapsed: 0, paused: false, levels: {} };
  recNotesLoaded = null;
  $("#recGroup").classList.add("hidden");
  $("#recLive").classList.remove("hidden");
  await loadList();
  await select(m.id);
}

async function stopRecording() {
  if (!S.recording || S.stopping) return;
  const id = S.recording.id;
  S.stopping = true;
  try {
    await saveRecNotes();
    await call("stop_recording", true);
    await recordingEnded(id);
  } finally {
    S.stopping = false;
  }
}
// 錄音結束（在主視窗或小視窗按停止）：回到這場會議的逐字稿
async function recordingEnded(id) {
  S.recording = null;
  $("#recGroup").classList.remove("hidden");
  $("#recLive").classList.add("hidden");
  S.tab = "transcript";
  await loadList();
  await select(id, true);
}
$("#miniBtn").addEventListener("click", () => api.show_mini());
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
function updateRecTooltip() {
  $("#recBtn").title = `開始錄音${S.recScreen ? "＋螢幕" : ""}（Ctrl+Shift+R）`;
}
updateRecTooltip();

function updateRecUI() {
  const r = S.recording;
  if (!r) return;
  const badge = $("#screenBadge");
  badge.classList.toggle("hidden", !r.screen);
  badge.classList.toggle("bad", r.screen && !r.screen_ok);
  badge.textContent = r.screen && !r.screen_ok ? "螢幕錄影中斷" : r.paused ? "螢幕錄影已暫停" : "螢幕錄影中";
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

// ---------- 輪詢 ----------
let tick = 0;
async function poll() {
  try {
    const st = await api.state();
    if (st.recording) {
      S.recording = st.recording;
      updateRecUI();
    } else if (S.recording && !S.stopping) {
      await recordingEnded(S.recording.id);
    }
    if (st.meter) { setMeter("#homeMicMeter", st.meter.mic); setMeter("#homeSysMeter", st.meter.system); }
    if (++tick % 4 === 0) {
      const before = JSON.stringify(S.meetings.map((m) => [m.id, m.status, m.progress, m.title, m.review_count]));
      S.meetings = await api.list_meetings(S.query);
      const after = JSON.stringify(S.meetings.map((m) => [m.id, m.status, m.progress, m.title, m.review_count]));
      if (before !== after) {
        renderList();
        if (!$("#home").classList.contains("hidden")) renderHomeRecent();
        const cur = S.meetings.find((m) => m.id === S.selected);
        if (cur && S.data && !(S.recording && S.recording.id === cur.id) &&
            (cur.status !== S.data.meeting.status || cur.progress !== S.data.meeting.progress || cur.title !== S.data.meeting.title)) {
          const wasBusy = BUSY.includes(S.data.meeting.status);
          S.data = await api.get_meeting(S.selected);
          if (wasBusy && S.data.meeting.status === "done" && S.data.notes && S.tab === "transcript" && !audio.currentTime) S.tab = "summary";
          if (document.activeElement?.closest?.("#content") && S.tab !== "transcript") renderMeta();
          else renderDetail(false);
          const want = S.data.video || (S.data.has_audio ? "audio.m4a" : "");
          if (want && !audio.src.endsWith("/" + want)) setupPlayer();
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

// ---------- 頁面切換 ----------
function showView(name) {
  for (const [id, v] of [["#home", "home"], ["#settingsView", "settings"], ["#detail", "detail"]])
    $(id).classList.toggle("hidden", v !== name);
  $("#settingsBtn").classList.toggle("active", name === "settings");
  $("#homeBtn").classList.toggle("active", name === "home");
  if (name !== "home") { api.stop_meter(); clearInterval(S.thumbTimer); }
  if (name !== "detail") {
    S.selected = null; S.data = null;
    if (!audio.paused) audio.pause();
    renderList();
  }
}

// ---------- 首頁 ----------
async function showHome() {
  showView("home");
  renderHomeMode();
  $("#homeScreen").checked = S.recScreen;
  $("#homeSource").disabled = !S.recScreen;
  for (const id of ["#homeMicMeter", "#homeSysMeter"]) if (!$(id).children.length) $(id).innerHTML = "<i></i>".repeat(24);
  api.start_meter();
  renderHomeRecent();
  renderHomeDir();
  checkEnv();
  const { settings: s, screen_source_name: name } = await api.get_settings();
  S.screenSource = s.screen_source;
  $("#homeSourceName").textContent = name;
  refreshHomeThumb();
  clearInterval(S.thumbTimer);
  S.thumbTimer = setInterval(refreshHomeThumb, 3000);
}
async function refreshHomeThumb() {
  if ($("#home").classList.contains("hidden") || !S.screenSource) return;
  if (!S.recScreen) { $("#homeSourceThumb").classList.add("hidden"); return; }
  const url = await api.source_thumbnail(S.screenSource, 160);
  $("#homeSourceThumb").src = url || "";
  $("#homeSourceThumb").classList.toggle("hidden", !url);
}
async function renderHomeDir() {
  const def = await api.default_record_dir();
  $("#homeDir").textContent = S.recDir || def;
  $("#homeDir").title = S.recDir || def;
  $("#homeDirReset").classList.toggle("hidden", !S.recDir);
}
$("#homeDirPick").addEventListener("click", async () => {
  const dir = await call("pick_folder", S.recDir);
  if (!dir) return;
  S.recDir = dir; localStorage.setItem("recDir", dir);
  renderHomeDir();
});
$("#homeDirReset").addEventListener("click", () => {
  S.recDir = ""; localStorage.removeItem("recDir");
  renderHomeDir();
});
$("#homeSource").addEventListener("click", async () => {
  const picked = await pickSource();
  if (picked) { $("#homeSourceName").textContent = picked; refreshHomeThumb(); }
});
function renderHomeRecent() {
  const recent = S.meetings.slice(0, 5);
  $("#homeRecent").innerHTML = recent.length ? recent.map((m) => `
    <button class="recent-item" data-id="${m.id}"><span class="t">${esc(m.title)}</span>
      <span class="s">${m.date.slice(5).replace("-", "/")}${m.duration ? `・${Math.max(1, Math.round(m.duration / 60))} 分鐘` : ""}　${statusText(m)}</span></button>`).join("")
    : `<p class="muted">還沒有會議。按「開始錄音」，或匯入以前錄好的音檔、影片。</p>`;
}
function renderHomeMode() {
  for (const b of document.querySelectorAll("#homeMode button")) b.classList.toggle("active", b.dataset.mode === S.recMode);
}
$("#homeBtn").addEventListener("click", showHome);
$("#homeMode").addEventListener("click", (e) => {
  const b = e.target.closest("[data-mode]");
  if (!b) return;
  S.recMode = b.dataset.mode; localStorage.setItem("recMode", S.recMode);
  renderHomeMode();
});
$("#homeScreen").addEventListener("change", (e) => {
  S.recScreen = e.target.checked; localStorage.setItem("recScreen", S.recScreen ? "1" : "0");
  $("#homeSource").disabled = !S.recScreen;
  updateRecTooltip();
  refreshHomeThumb();
});

// ---------- 選擇錄影來源（像 OBS 一樣先看畫面再選） ----------
function pickSource() {
  return new Promise(async (resolve) => {
    const { monitors, windows } = await call("screen_sources");
    const cur = S.screenSource || { kind: "monitor", index: 0 };
    const sources = [
      ...monitors.map((m) => ({ kind: "monitor", index: m.index, name: m.name, sub: `${m.width}×${m.height}` })),
      ...windows.map((w) => ({ kind: "window", hwnd: w.hwnd, title: w.title, exe: w.exe, name: w.title,
                               sub: w.exe.replace(/\.exe$/i, "") + (w.minimized ? "・已縮到最小，請先把它打開" : "") })),
    ];
    let sel = sources.findIndex((x) => x.kind === cur.kind && (x.kind === "monitor" ? x.index === cur.index : x.exe === cur.exe && x.title === cur.title));
    if (sel < 0) sel = sources.findIndex((x) => x.kind === cur.kind && x.kind === "window" && x.exe === cur.exe);
    if (sel < 0) sel = 0;
    let tab = sources[sel].kind;
    const card = (x, i) => `<button class="pick-card ${i === sel ? "active" : ""}" data-i="${i}">
        <div class="pick-thumb"><img data-thumb="${i}" alt=""></div>
        <div class="pick-name" title="${esc(x.name)}">${esc(x.name)}</div><div class="pick-sub">${esc(x.sub)}</div></button>`;
    openModal(`<h3>選擇錄影來源</h3>
      <div class="pick-layout">
        <div class="pick-preview"><img id="pickPreview" alt=""><div class="pick-preview-name" id="pickPreviewName"></div></div>
        <div>
          <div class="tabs pick-tabs"><button data-tab="monitor">整個螢幕</button><button data-tab="window">單一視窗</button></div>
          <p class="help" id="pickHelp"></p>
          <div class="pick-grid" id="pickGrid"></div>
        </div>
      </div>
      <div class="actions"><button class="btn" id="pickCancel">取消</button><button class="btn primary" id="pickOk">使用這個來源</button></div>`, "wide");
    const renderGrid = () => {
      for (const b of document.querySelectorAll(".pick-tabs button")) b.classList.toggle("active", b.dataset.tab === tab);
      $("#pickHelp").textContent = tab === "monitor"
        ? "錄下整個螢幕的畫面，包括所有視窗與滑鼠。"
        : "只錄這個視窗，被其他視窗蓋住也照樣錄得到；視窗縮到最小時會停在最後一個畫面。";
      const list = sources.map((x, i) => ({ x, i })).filter(({ x }) => x.kind === tab);
      $("#pickGrid").innerHTML = list.map(({ x, i }) => card(x, i)).join("") || `<p class="muted">沒有可以錄的視窗</p>`;
      (async () => {
        for (const { i } of list) {
          const img = document.querySelector(`[data-thumb="${i}"]`);
          if (!img) return;
          img.src = (await api.source_thumbnail(sources[i], 240)) || "";
        }
      })();
    };
    const renderPreview = async () => {
      const x = sources[sel];
      $("#pickPreviewName").textContent = x.name;
      const url = await api.source_thumbnail(x, 720);
      if ($("#pickPreview")) $("#pickPreview").src = url || "";
    };
    renderGrid();
    renderPreview();
    S.pickerTimer = setInterval(renderPreview, 1000);
    const done = async (ok) => {
      clearInterval(S.pickerTimer);
      closeModal();
      if (!ok) return resolve(null);
      const x = sources[sel];
      const src = x.kind === "monitor" ? { kind: "monitor", index: x.index } : { kind: "window", hwnd: x.hwnd, title: x.title, exe: x.exe };
      S.screenSource = src;
      resolve(await call("set_screen_source", src));
    };
    $("#modalBox").onclick = (e) => {
      const t = e.target.closest(".pick-tabs button");
      if (t) { tab = t.dataset.tab; renderGrid(); return; }
      const c = e.target.closest(".pick-card");
      if (c) {
        sel = +c.dataset.i;
        document.querySelectorAll(".pick-card").forEach((b) => b.classList.toggle("active", b === c));
        renderPreview();
      }
      if (e.target.closest("#pickOk")) done(true);
      if (e.target.closest("#pickCancel")) done(false);
    };
    S.pickerClose = () => done(false);
  });
}
$("#homeRec").addEventListener("click", startRecording);
$("#homeImport").addEventListener("click", importFiles);
$("#homeRecent").addEventListener("click", (e) => {
  const b = e.target.closest("[data-id]");
  if (b) select(b.dataset.id);
});
$("#envCheck").addEventListener("click", (e) => {
  const b = e.target.closest("[data-goto]");
  if (b) showSettings(b.dataset.goto);
});

// ---------- 設定 ----------
$("#settingsBtn").addEventListener("click", () => showSettings());
async function showSettings(focus) {
  showView("settings");
  const { settings: s, devices, backends, version, screen_source_name: sourceName } = await api.get_settings();
  const env = await api.check_environment();
  const opt = (list, cur, def) => `<option value="-1">${def}</option>` + list.map((d) => `<option value="${d.index}" ${d.index === cur ? "selected" : ""}>${esc(d.name)}</option>`).join("");
  const sel = (key, options, cur) => `<select data-key="${key}" data-type="${typeof cur === "number" ? "int" : "str"}">${options.map(([v, label]) =>
    `<option value="${v}" ${String(v) === String(cur) ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
  const text = (key, val, ph = "") => `<input type="text" data-key="${key}" value="${esc(val)}" placeholder="${esc(ph)}">`;
  const num = (key, val) => `<input type="number" min="0" data-key="${key}" data-type="int" value="${val}">`;
  const check = (key, val, label) => `<label class="switch"><input type="checkbox" data-key="${key}" data-type="bool" ${val ? "checked" : ""}><span></span>${label}</label>`;
  const status = (ok, okText, badText) => `<span class="status ${ok ? "ok" : "bad"}">${ok ? "✓ " + okText : "✗ " + badText}</span>`;

  $("#settingsBody").innerHTML = `
    <h1 class="page-title">設定</h1>
    <p class="page-sub">修改後會自動儲存。　v${esc(version)}</p>

    <section class="card" id="set-ai"><h3>AI 整理會議紀錄</h3>
      <div class="grid2">
        <div class="field"><label class="lbl">使用</label>${sel("llm_backend", [
          ["claude", `Claude Code${backends.claude ? "" : "（未安裝）"}`],
          ["codex", `Codex${backends.codex ? "" : "（未安裝）"}`],
          ["none", "不整理，只要逐字稿"]], s.llm_backend)}</div>
        <div class="field"><label class="lbl">模型</label>${text("llm_model", s.llm_model, "留空用預設模型")}</div>
      </div>
      <p class="help">使用你電腦上已經登入的 CLI。只會送出逐字稿文字，不會上傳音檔。</p>
      <div class="field"><label class="lbl">詞彙表（一行一個：人名、產品名、專有名詞）</label>
        <textarea data-key="glossary" data-type="list" rows="4" placeholder="例如：&#10;王小明&#10;Breeze ASR&#10;PayLink">${esc((s.glossary || []).join("\n"))}</textarea>
        <p class="help">轉錄時當作提示，AI 整理時也會照這裡的寫法校正。</p></div>
    </section>

    <section class="card" id="set-rec"><h3>錄音</h3>
      <div class="grid2">
        <div class="field"><label class="lbl">麥克風</label><select data-key="mic_device" data-type="int">${opt(devices.mics, s.mic_device, "系統預設")}</select></div>
        <div class="field"><label class="lbl">電腦聲音來源</label><select data-key="loopback_device" data-type="int">${opt(devices.loopbacks, s.loopback_device, "預設喇叭")}</select></div>
      </div>
    </section>

    <section class="card" id="set-screen"><h3>螢幕錄影</h3>
      <div class="grid2">
        <div class="field"><label class="lbl">要錄的畫面</label><button class="source-btn" id="setSource"><span id="setSourceName">${esc(sourceName)}</span><span class="link">變更</span></button></div>
        <div class="field"><label class="lbl">影格率</label>${sel("screen_fps", [[10, "10 fps（檔案最小）"], [15, "15 fps（建議）"], [30, "30 fps（畫面較流暢）"]], s.screen_fps)}</div>
      </div>
      <p class="help">在首頁或錄音按鈕旁的選單勾選「同時錄製螢幕」才會錄影。</p>
    </section>

    <section class="card" id="set-whisper"><h3>轉錄</h3>
      <div class="field"><label class="lbl">Faster-Whisper-XXL 位置 ${status(env.whisper, "找到了", "找不到")}</label>${text("whisper_exe", s.whisper_exe)}</div>
      <div class="grid2">
        <div class="field"><label class="lbl">模型</label>${text("whisper_model", s.whisper_model)}</div>
        <div class="field"><label class="lbl">語言（留空自動偵測）</label>${text("whisper_language", s.whisper_language)}</div>
      </div>
      <div class="field">${check("multilingual", s.multilingual, "逐段偵測語言（中英夾雜的會議建議開啟，不會漏掉英文句子）")}</div>
      <div class="grid3">
        <div class="field">${check("diarize", s.diarize, "分辨說話者")}</div>
        <div class="field"><label class="lbl">最少人數（0＝自動）</label>${num("min_speakers", s.min_speakers)}</div>
        <div class="field"><label class="lbl">最多人數（0＝自動）</label>${num("max_speakers", s.max_speakers)}</div>
      </div>
      <div class="field"><label class="lbl">OpenCC 位置（簡轉繁，選用） ${status(env.opencc, "找到了", "找不到，逐字稿會是簡體")}</label>${text("opencc_exe", s.opencc_exe)}</div>
    </section>

    <section class="card" id="set-data"><h3>資料</h3>
      <div class="field"><label class="lbl">會議預設存放的資料夾</label>
        <div class="input-row">${text("data_dir", s.data_dir)}<button class="btn" id="openDataDir">開啟</button></div>
        <p class="help">每場會議一個資料夾，名稱是「日期 標題」。開始錄音前也可以在首頁改存到其他地方。</p></div>
      <div class="field">${check("check_updates", s.check_updates, "開啟時檢查 GitHub 上有沒有新版本")}</div>
    </section>`;

  if (focus) {
    const el = $(`#${focus}`);
    el?.scrollIntoView({ block: "start" });
    el?.classList.add("flash");
  }
  $("#openDataDir").onclick = () => api.open_data_dir();
  $("#setSource").onclick = async () => {
    S.screenSource = s.screen_source;
    const name = await pickSource();
    if (name) $("#setSourceName").textContent = name;
  };
}

$("#settingsBody").addEventListener("change", async (e) => {
  const el = e.target.closest("[data-key]");
  if (!el) return;
  const type = el.dataset.type || "str";
  let value = el.type === "checkbox" ? el.checked : el.value;
  if (type === "int") value = parseInt(value, 10) || 0;
  else if (type === "list") value = value.split("\n").map((x) => x.trim()).filter(Boolean);
  else if (type === "str") value = value.trim();
  await call("save_settings", { [el.dataset.key]: value });
  toast("已儲存", 1200);
  if (["whisper_exe", "opencc_exe", "llm_backend"].includes(el.dataset.key)) {
    const y = $("#settingsView").scrollTop;
    await showSettings();
    $("#settingsView").scrollTop = y;
  }
  if (el.dataset.key === "data_dir") { await loadList(); updateTrashLabel(); }
});

// ---------- 對話框 ----------
function openModal(html, size = "") {
  $("#modalBox").className = `modal-box ${size}`;
  $("#modalBox").innerHTML = html;
  $("#modalBox").onclick = null;
  $("#modal").classList.remove("hidden");
  $("#modalBox").querySelector("input,textarea,select")?.focus();
}
function closeModal() {
  $("#modal").classList.add("hidden");
  clearInterval(S.pickerTimer);
}
$("#modal").addEventListener("click", (e) => {
  if (e.target.id === "modal" || e.target.closest("[data-close]")) {
    const pending = S.pickerClose;
    S.pickerClose = null;
    if (pending) pending(); else closeModal();
  }
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
    if (it.sep) { menu.appendChild(document.createElement("hr")); return; }
    if (it.title) {
      const h = document.createElement("div");
      h.className = "menu-title";
      h.textContent = it.title;
      menu.appendChild(h);
      return;
    }
    const b = document.createElement("button");
    b.textContent = it.label;
    b.disabled = !!it.disabled;
    if (it.danger) b.className = "danger";
    b.onclick = () => { menu.remove(); it.fn(); };
    menu.appendChild(b);
  });
  document.body.appendChild(menu);
  const mr = menu.getBoundingClientRect();
  if (mr.right > window.innerWidth - 8) menu.style.left = `${Math.max(8, r.right - mr.width)}px`;
  if (mr.bottom > window.innerHeight - 8) menu.style.top = `${Math.max(8, r.top - mr.height - 4)}px`;
  setTimeout(() => document.addEventListener("click", () => menu.remove(), { once: true }));
}

// ---------- 快捷鍵 ----------
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "r") { e.preventDefault(); S.recording ? stopRecording() : startRecording(); }
  else if (e.ctrlKey && e.key.toLowerCase() === "b" && S.recording) { e.preventDefault(); addMark(); }
  else if (e.key === "Escape") { if (S.pickerClose) { const f = S.pickerClose; S.pickerClose = null; f(); } else closeModal(); }
  else if (e.code === "Space" && !e.target.closest("input,textarea,[contenteditable=true],select,button") && hasMedia() && !S.recording) {
    e.preventDefault(); audio.paused ? audio.play() : audio.pause();
  }
});

// ---------- 錄音中的小視窗 ----------
async function initMini() {
  document.body.classList.add("mini-mode");
  $("#app").classList.add("hidden");
  $("#mini").classList.remove("hidden");
  $("#miniMeter").innerHTML = "<i></i>".repeat(10);
  let paused = false;
  const tickMini = async () => {
    const st = await api.state();
    const r = st.recording;
    if (!r) return;
    paused = r.paused;
    $("#miniTimer").textContent = fmt(r.elapsed);
    $("#mini").classList.toggle("paused", r.paused);
    $("#miniPause").classList.toggle("is-paused", r.paused);
    setMeter("#miniMeter", Math.max(r.levels?.mic || 0, r.levels?.system || 0));
  };
  $("#miniPause").onclick = async () => { await api[paused ? "resume_recording" : "pause_recording"](); tickMini(); };
  $("#miniMark").onclick = async () => {
    await api.add_bookmark("");
    $("#miniMark").classList.add("flash-once");
    setTimeout(() => $("#miniMark").classList.remove("flash-once"), 600);
  };
  $("#miniStop").onclick = () => api.stop_recording(true);
  $("#miniExpand").onclick = () => api.close_mini();
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === "b") { e.preventDefault(); $("#miniMark").click(); }
  });
  tickMini();
  setInterval(tickMini, 500);
}

async function checkUpdate() {
  const u = await api.check_update();
  if (!u) return;
  const b = $("#updateBanner");
  b.innerHTML = `<strong>有新版本 v${esc(u.version)}</strong><span>按這裡下載</span>`;
  b.classList.remove("hidden");
  b.onclick = () => api.open_url(u.url);
}

// ---------- 啟動 ----------
async function init() {
  api = window.pywebview.api;
  if (new URLSearchParams(location.search).has("mini")) return initMini();
  if (new URLSearchParams(location.search).has("dev")) $("#miniBtn").classList.add("hidden");
  S.templates = await api.templates();
  const st = await api.state();
  if (st.recording) {
    S.recording = st.recording;
    $("#recGroup").classList.add("hidden");
    $("#recLive").classList.remove("hidden");
  }
  await loadList();
  updateTrashLabel();
  // 網址參數（開發與截圖用）：?m=<會議 id>&tab=summary 或 ?view=settings
  const q = new URLSearchParams(location.search);
  if (q.get("video") === "0") S.showVideo = false;
  if (S.recording) await select(S.recording.id);
  else if (q.get("view") === "settings") await showSettings();
  else if (q.get("m")) { S.tab = q.get("tab") || "summary"; await select(q.get("m"), true); }
  else await showHome();
  poll();
  checkUpdate();
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
