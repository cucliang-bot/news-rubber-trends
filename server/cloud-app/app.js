
// ========== 云端数据地址 ==========
// 候选/最终稿/确认队列：jsonbin bins
const CANDIDATES_BIN_ID = '6ab9da2cac6210605afc9499';
const FINAL_BIN_ID = '6ab9da2dffd5d16053363048';
const CONFIRM_BIN_ID = '6ab9da2effd5d16053363049';

const CANDIDATES_URL = `https://api.jsonbin.io/v3/b/${CANDIDATES_BIN_ID}/latest`;
const FINAL_URL = `https://api.jsonbin.io/v3/b/${FINAL_BIN_ID}/latest`;
const CONFIRM_READ_URL = `https://api.jsonbin.io/v3/b/${CONFIRM_BIN_ID}/latest`;
const CONFIRM_WRITE_URL = `https://api.jsonbin.io/v3/b/${CONFIRM_BIN_ID}`;

let candidates = [];
let finals = [];
let selectedSet = new Set();
let currentDate = '';

function $(id) { return document.getElementById(id); }

function showToast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2200);
}

function esc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function getDate(item) { return item.pub_date || item.date || item.pubDate || ''; }
function getSource(item) { return item.source || item.source_name || ''; }
function getSourceType(item) { return item.source_type || 'website'; }
function getSourceName(item) { return item.source_name || getSource(item); }
function getUrls(item) { return item.source_urls || (item.url ? [item.url] : []); }
function getSummary(item) { return item.summary_cn || item.summary || item.abstract || ''; }
function getTitleEn(item) { return item.title_en || ''; }
function getTitleCn(item) { return item.title_cn || item.title || ''; }

function getSourceBadge(item) {
  if (getSourceType(item) === 'wechat') {
    return '<span class="source-badge wechat">微信公众号</span> ';
  }
  return '';
}

// jsonbin 响应结构：{record: {...数据...}, metadata: {...}}
function unwrap(resp) { return (resp && resp.record) ? resp.record : resp; }

async function fetchJson(url) {
  // 加时间戳参数 + cache no-store，防止 CDN/浏览器缓存旧数据
  const sep = url.includes('?') ? '&' : '?';
  const resp = await fetch(url + sep + 't=' + Date.now(), { cache: 'no-store' });
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  return resp.json();
}

async function loadAll() {
  $('updateTime').textContent = '加载中...';
  let errors = [];
  // 1. 候选数据（优先 Gist，失败或为空时回退到 jsonbin confirm bin 的 candidates_data）
  try {
    const cand = unwrap(await fetchJson(CANDIDATES_URL));
    candidates = cand.data || [];
    currentDate = (cand.updated_at || '').slice(0, 10);
  } catch (e) { errors.push('候选数据'); candidates = []; }
  if (!candidates.length) {
    try {
      const confirmBox = unwrap(await fetchJson(CONFIRM_READ_URL));
      const cd = confirmBox && confirmBox.candidates_data;
      if (cd && cd.data && cd.data.length) {
        candidates = cd.data;
        currentDate = (cd.date || (cd.updated_at || '').slice(0, 10));
        const idx = errors.indexOf('候选数据');
        if (idx > -1) errors.splice(idx, 1);
      }
    } catch (e2) { /* 回退失败也不影响原有错误提示 */ }
  }
  // 2. 最终稿数据
  try {
    const fin = unwrap(await fetchJson(FINAL_URL));
    finals = fin.data || [];
  } catch (e) { errors.push('最终稿数据'); finals = []; }
  renderCandidates();
  renderFinals();
  const t = $('updateTime');
  if (errors.length > 0) {
    t.textContent = '部分数据加载失败(' + errors.join(',') + ')';
  } else {
    t.textContent = currentDate || '刚刚';
  }
}

// ========== 候选选题渲染 ==========
function getFilteredCandidates() {
  const filter = $('sourceFilter') ? $('sourceFilter').value : 'all';
  if (filter === 'all') return candidates;
  return candidates.filter(c => getSourceType(c) === filter);
}

function renderCandidates() {
  const filtered = getFilteredCandidates();
  const box = $('candidatesList');
  const cnt = $('candCount');
  if (!filtered.length) {
    box.innerHTML = '<div class="empty">暂无候选选题，请等待选题生成。</div>';
    cnt.style.display = 'none';
    return;
  }
  cnt.textContent = filtered.length;
  cnt.style.display = 'inline-block';
  box.innerHTML = filtered.map((item, idx) => {
    const id = item.id || idx + 1;
    const en = getTitleEn(item);
    const urls = getUrls(item);
    const links = urls.map(u => `<a href="${esc(u)}" target="_blank" rel="noopener">原文链接</a>`).join('');
    const img = item.image_url ? `<img class="card-img" src="${esc(item.image_url)}" alt="" loading="lazy" onerror="this.style.display='none'">` : '';
    const badge = getSourceBadge(item);
    return `
    <div class="card ${selectedSet.has(id) ? 'selected' : ''}" data-id="${id}">
      <input type="checkbox" class="checkbox" ${selectedSet.has(id) ? 'checked' : ''}
        onchange="toggleSelect(${id}, this.checked)">
      ${img ? `<div class="card-img-wrap">${img}</div>` : ''}
      <div class="content">
        <div class="title-cn">${esc(getTitleCn(item))}</div>
        ${en ? `<div class="title-en">${esc(en)}</div>` : ''}
        <div class="meta">
          ${badge}<span class="source">${esc(getSource(item))}</span>
          <span class="date">${esc(getDate(item))}</span>
        </div>
        <div class="summary">${esc(getSummary(item))}</div>
        ${links ? `<div class="links">${links}</div>` : ''}
      </div>
    </div>`;
  }).join('');
  updateSelectedInfo();
}

function toggleSelect(id, checked) {
  if (checked) selectedSet.add(id); else selectedSet.delete(id);
  const card = document.querySelector(`.card[data-id="${id}"]`);
  if (card) card.classList.toggle('selected', checked);
  updateSelectedInfo();
}

function selectAll(flag) {
  candidates.forEach(item => {
    const id = item.id;
    if (flag) selectedSet.add(id); else selectedSet.delete(id);
  });
  document.querySelectorAll('.card').forEach(card => {
    const id = parseInt(card.dataset.id);
    card.classList.toggle('selected', selectedSet.has(id));
    const cb = card.querySelector('.checkbox');
    if (cb) cb.checked = selectedSet.has(id);
  });
  updateSelectedInfo();
}

function updateSelectedInfo() {
  $('selectedInfo').textContent = `已选 ${selectedSet.size} 条`;
}

function getSelected() {
  return candidates.filter(item => selectedSet.has(item.id));
}

// ========== CSV ==========
function downloadCsv() {
  const items = getSelected();
  if (!items.length) { showToast('请先勾选要下载的选题'); return; }
  downloadCsvFile(items, 'candidates_selected.csv');
}

function downloadFinalCsv() {
  if (!finals.length) { showToast('暂无最终稿件'); return; }
  downloadCsvFile(finals, 'final_articles.csv', true);
}

function downloadCsvFile(items, filename, isFinal) {
  const headers = ['序号', '中文标题', '英文标题', '来源网站', '发布时间', '中文摘要', '原文链接'];
  const rows = items.map((item, i) => {
    const urls = getUrls(item);
    return [
      item.id || i + 1,
      getTitleCn(item),
      getTitleEn(item),
      getSource(item),
      getDate(item),
      getSummary(item).replace(/[\r\n]+/g, ' '),
      urls.join('; ')
    ];
  });
  const csv = [headers, ...rows].map(row => row.map(cell => {
    const s = String(cell == null ? '' : cell);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }).join(',')).join('\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
  showToast('CSV 已下载');
}

// ========== 确认提交（写入云端队列，自动触发写作） ==========
async function submitConfirm() {
  const items = getSelected();
  if (!items.length) { showToast('请先勾选要确认的选题'); return; }
  const date = currentDate || new Date().toISOString().slice(0, 10);
  const ids = items.map(i => i.id);
  const titles = items.map(i => `${i.id}.${getTitleCn(i)}`).join(' ');

  if (!confirm(`确定提交以下 ${items.length} 条选题？\n\n${titles}`)) return;

  const btn = document.querySelector('.toolbar .btn-primary');
  btn.disabled = true; btn.textContent = '提交中...';

  try {
    // 1. 读取当前确认队列（jsonbin 公开 bin，防缓存）
    let box = unwrap(await fetchJson(CONFIRM_READ_URL));
    if (!box.queue) box.queue = [];

    // 2. 防重复：同日期同 ids 且未完成
    const dup = box.queue.find(q =>
      q.date === date && q.status !== 'done' &&
      Array.isArray(q.ids) && q.ids.length === ids.length &&
      q.ids.every((v, i) => v === ids[i])
    );
    if (dup) {
      showToast('这批选题已提交过，正在处理中，请勿重复提交');
      btn.disabled = false; btn.textContent = '✓ 确认提交';
      return;
    }

    // 3. 追加新确认
    const entry = {
      id: Date.now(),
      date: date,
      ids: ids,
      submitted_at: new Date().toLocaleString('zh-CN', { hour12: false }),
      status: 'pending'
    };
    box.queue.push(entry);

    // 4. 写回 jsonbin 公开 bin（无需认证）
    let wresp = await fetch(CONFIRM_WRITE_URL, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(box)
    });
    if (!wresp.ok) throw new Error('提交失败(HTTP ' + wresp.status + ')');

    // 5. 成功提示
    $('confirmStatus').style.display = 'block';
    $('confirmStatus').innerHTML =
      `✅ <b>已确认 ${items.length} 条选题（${date}）</b>，正在处理中。<br>
      完成后「最终稿件」Tab 会显示成稿，请稍后刷新查看并直接下载。<br>
      <span style="opacity:0.7">提交时间：${entry.submitted_at}</span>`;
    $('confirmStatus').scrollIntoView({ behavior: 'smooth', block: 'center' });
    selectedSet.clear();
    updateSelectedInfo();
    renderCandidates();
    showToast('确认提交成功，正在处理中');
  } catch (e) {
    showToast('提交失败：' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = '✓ 确认提交';
  }
}

// ========== 确认文本（备选：手动复制） ==========
function genConfirm() {
  const items = getSelected();
  if (!items.length) { showToast('请先勾选要确认的选题'); return; }
  const date = currentDate || new Date().toISOString().slice(0, 10);
  const lines = items.map(item => `${item.id}.${getTitleCn(item)}`);
  const text = `确认选题：${lines.join(' ')}\n日期：${date}\n共${items.length}条`;
  $('confirmText').value = text;
  $('confirmModal').classList.add('show');
}

function closeModal() { $('confirmModal').classList.remove('show'); }

function copyConfirm() {
  const ta = $('confirmText');
  ta.select();
  document.execCommand('copy');
  showToast('已复制到剪贴板');
}

// ========== 最终稿件渲染 ==========
function renderFinals() {
  const box = $('finalList');
  const cnt = $('finalCount');
  if (!finals.length) {
    box.innerHTML = '<div class="empty">暂无最终稿件，选题确认后将自动同步。</div>';
    cnt.style.display = 'none';
    return;
  }
  cnt.textContent = finals.length;
  cnt.style.display = 'inline-block';
  box.innerHTML = finals.map((item, idx) => {
    const en = getTitleEn(item);
    const urls = getUrls(item);
    const links = urls.map(u => `<a href="${esc(u)}" target="_blank" rel="noopener">原文链接</a>`).join('');
    const sim = item.similarity_score;
    const simBadge = sim != null
      ? (sim > 50 ? `<span class="sim-badge sim-high">重复率 ${sim}%</span>` : `<span class="sim-badge sim-ok">重复率 ${sim}%</span>`)
      : '';
    const img = item.image_url ? `<img class="article-img" src="${esc(item.image_url)}" alt="" loading="lazy" onerror="this.style.display='none'">` : '';
    const badge = getSourceBadge(item);
    return `
    <div class="article">
      <h2>${idx + 1}. ${esc(getTitleCn(item))}</h2>
      ${en ? `<div class="article-en">${esc(en)}</div>` : ''}
      ${img ? `<div class="article-img-wrap">${img}</div>` : ''}
      <div class="article-meta">
        ${badge}<span>来源：${esc(getSource(item))}</span>
        <span>日期：${esc(getDate(item))}</span>
        ${simBadge}
      </div>
      <div class="source-line">来源：${esc(getSource(item))}</div>
      <div class="article-summary">${esc(getSummary(item))}</div>
      <div class="article-body">${esc(item.full_text_cn || item.full_text || item.content || '')}</div>
      ${links ? `<div class="links">${links}</div>` : ''}
    </div>`;
  }).join('');
}

// ========== Tab 切换 ==========
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  $('candidatesTab').style.display = tab === 'candidates' ? '' : 'none';
  $('finalTab').style.display = tab === 'final' ? '' : 'none';
}

// ========== 自动刷新（每 60 秒） ==========
setInterval(() => { loadAll(); }, 60000);

// 初始加载
loadAll();
