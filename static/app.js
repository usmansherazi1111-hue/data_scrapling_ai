let currentJob = null, pollTimer = null;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[m]));
const isUrl = v => /^https?:\/\//.test(v);
const link = (u, label) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(label ?? u.replace(/^https?:\/\/(www\.)?/, ''))}</a>`;
const chip = x => `<span class="chip">${isUrl(x) ? link(x) : esc(x)}</span>`;
const nf = '<span class="nf">Not found</span>';

async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...opts});
  if (r.status === 401) { location.href = '/login'; throw new Error('Not signed in'); }
  if (!r.ok) {
    let msg = await r.text();
    try { msg = JSON.parse(msg).detail || msg; } catch {}
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return r.json();
}

function fmtVal(v) {
  if (Array.isArray(v)) return v.length ? `<div class="chips">${v.map(chip).join('')}</div>` : nf;
  if (v === 'Not Found' || v == null || v === '') return nf;
  if (typeof v === 'object') return esc(JSON.stringify(v));
  if (isUrl(v)) return link(v, v);
  if (/^[^@\s]+@[^@\s]+$/.test(v)) return `<a href="mailto:${esc(v)}">${esc(v)}</a>`;
  return esc(v);
}

function setState(cls, text) { $('state').className = 'pill ' + cls; $('state').textContent = text; }

// ------------------------------------------------------------------ crawl
async function start() {
  const url = $('url').value.trim();
  if (!url) return alert('Enter a URL');
  $('start').disabled = true; setState('running', 'CRAWLING');
  $('progress').classList.remove('hidden'); $('results').classList.add('hidden'); $('pages').innerHTML = '';
  const body = {
    url, mode: $('mode').value, max_pages: +$('max_pages').value, depth: +$('depth').value, concurrency: +$('concurrency').value,
    browser_pages: +$('browser_pages').value, timeout: +$('timeout').value, adaptive: $('adaptive').checked, network_idle: $('network_idle').checked,
    disable_resources: $('disable_resources').checked, capture_xhr: $('capture_xhr').checked, ai_enrichment: $('ai').checked, registry_lookup: $('registry_lookup').checked, enrich: $('enrich').checked,
    escalate: $('escalate').checked, respect_robots: $('respect_robots').checked, delay_ms: +$('delay_ms').value, proxies: $('proxies').value,
    profile: $('profile').value || null,
  };
  try {
    currentJob = (await api('/api/jobs', {method: 'POST', body: JSON.stringify(body)})).job_id;
    poll();
  } catch (e) { setState('error', 'ERROR'); alert(e.message); $('start').disabled = false; }
}

async function poll() {
  clearTimeout(pollTimer);
  const d = await api('/api/jobs/' + currentJob);
  const pages = d.pages || [], total = (d.config && d.config.max_pages) || +$('max_pages').value;
  $('bar').style.width = Math.min(100, Math.round(pages.length / total * 100)) + '%';
  $('progressText').textContent = pages.length + ' pages';
  $('pages').innerHTML = pages.map(p => `<div class="page"><b>${esc(p.category)}</b><small title="${esc(p.url)}">${esc(p.url)}</small><small>${esc(p.mode)}</small><span class="${p.challenge ? 'warn' : p.error ? 'err' : ''}">${p.challenge ? esc(p.challenge.toUpperCase()) : p.error ? 'ERROR' : (p.status || '')}</span></div>`).join('');
  const en = d.enrichment;
  const enriching = en && en.status === 'running';
  $('enrichStep').classList.toggle('hidden', !enriching);
  if (enriching) { setState('running', 'ENRICHING'); $('enrichStep').textContent = 'Enrichment: ' + (en.step || '…'); }
  if (d.status === 'running' || enriching) { pollTimer = setTimeout(poll, 800); if (enriching && !$('results').classList.contains('hidden')) render(d); return; }
  $('start').disabled = false;
  setState(d.status === 'completed' ? 'idle' : 'error', d.status.toUpperCase());
  render(d); loadHistory();
}

// ------------------------------------------------------------------ rendering
function render(d) {
  currentJob = d.job_id;
  $('results').classList.remove('hidden');
  const s = d.stats || {};
  $('resultTitle').textContent = (d.fields?.company_name?.value && d.fields.company_name.value !== 'Not Found') ? d.fields.company_name.value : d.seed_url;
  $('stats').innerHTML = [['Pages', s.pages_crawled || 0], ['Successful', s.pages_succeeded || 0], ['Blocked', s.pages_blocked || 0], ['Failed', s.pages_failed || 0], ['Elapsed', ((s.elapsed_ms || 0) / 1000).toFixed(1) + 's']]
    .map(x => `<div class="stat"><b>${esc(x[1])}</b><span>${esc(x[0])}</span></div>`).join('');
  renderBlocked(d);
  $('data').innerHTML = Object.entries(d.fields || {}).map(([k, v]) => `<tr><td><b>${esc(k.replaceAll('_', ' '))}</b></td><td>${fmtVal(v.value)}</td><td class="conf">${Math.round(Math.min(1, v.confidence || 0) * 100)}%</td><td class="source">${esc(v.source_url || '—')}<br>${esc(v.source_type || '')}</td></tr>`).join('');
  $('sources').innerHTML = (d.pages || []).map(p => `<div class="history-row src"><b>${esc(p.category)}</b><small>${link(p.url, p.url)}</small><small>${p.status || '—'} · ${esc(p.mode)} · ${p.elapsed_ms}ms</small><small class="${p.error ? 'err' : ''}">${p.error ? esc(p.error) : p.text_chars + ' chars'}</small></div>`).join('');
  $('raw').textContent = JSON.stringify({...d, raw_pages: Object.fromEntries(Object.entries(d.raw_pages || {}).map(([u, p]) => [u, {...p, text: (p.text || '').slice(0, 2000), markdown: (p.markdown || '').slice(0, 2000)}]))}, null, 2);
  for (const k of ['xlsx', 'csv', 'json']) $(k).href = `/api/jobs/${d.job_id}/export/${k}`;
  renderEnriched(d);
}

function renderBlocked(d) {
  const ch = (d.stats || {}).challenges || [];
  const box = $('blocked');
  if (!ch.length) { box.classList.add('hidden'); return; }
  const types = [...new Set(ch.map(c => c.type))].join(', ');
  const robots = ch.every(c => c.type === 'robots');
  const host = new URL(d.seed_url).hostname.replace(/^www\./, '');
  box.classList.remove('hidden');
  box.innerHTML = robots
    ? `<b>${ch.length} page(s) skipped by robots.txt.</b> The site asks crawlers not to fetch them. Untick <i>Respect robots.txt</i> only if you have permission to crawl them.`
    : `<b>${ch.length} page(s) were blocked (${esc(types)}).</b> Stealth escalation did not get through. Open the site in a real browser, solve the check or log in yourself, then re-run with the saved profile.
       <div class="btns"><button id="solveBtn">Open browser to solve</button>
       <button class="ghost" id="rerunBtn">Re-run with profile “${esc(host)}”</button></div>`;
  if (!robots) {
    const blockedUrl = ch.find(c => c.type !== 'robots')?.url || d.seed_url;
    $('solveBtn').onclick = () => solveInBrowser(host, blockedUrl);
    $('rerunBtn').onclick = () => rerunWithProfile(host);
  }
}

function card(title, body, cls = '') { return `<div class="ecard ${cls}"><h3>${title}</h3>${body}</div>`; }
function kv(rows) {
  const r = rows.filter(([, v]) => v !== undefined);
  return `<dl class="kv">${r.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v == null || v === '' || (Array.isArray(v) && !v.length) ? nf : v && v.__html ? v.__html : fmtVal(v)}</dd>`).join('')}</dl>`;
}
const H = html => ({__html: html});
const yes = b => H(b ? '<span class="ok">✓ yes</span>' : '<span class="nf">no</span>');

function renderEnriched(d) {
  const e = d.enrichment, box = $('enriched');
  const rerun = `<button class="ghost" onclick="runEnrichment()">${e ? 'Re-run enrichment' : 'Run enrichment'}</button>`;
  if (!e) { box.innerHTML = `<div class="card empty">This crawl has not been enriched yet. ${rerun}</div>`; return; }
  if (e.status === 'running') { box.innerHTML = `<div class="card empty"><span class="spinner"></span> Enriching… ${esc(e.step || '')}</div>`; return; }
  if (e.status === 'failed') { box.innerHTML = `<div class="card empty err">Enrichment failed: ${esc(e.error)} ${rerun}</div>`; return; }

  const f = e.firmographics || {}, dom = e.domain || {}, dns = e.dns || {}, ls = e.lead_score || {}, a = e.ai, c = e.company || {};
  const scoreCls = ls.score >= 80 ? 'good' : ls.score >= 60 ? 'mid' : 'low';
  const top = (e.people || [])[0];

  const scoreCard = `<div class="ecard score ${scoreCls}"><div class="score-head"><div><div class="big">${ls.score ?? '—'}<small>/100</small></div><div class="muted">Lead score · grade ${esc(ls.grade || '')}</div></div>${rerun}</div>
    <ul class="checks-list">${(ls.checks || []).map(x => `<li class="${x.ok ? 'ok' : 'miss'}">${x.ok ? '✓' : '✗'} ${esc(x.label)} <small>+${x.weight}</small></li>`).join('')}</ul></div>`;

  const companyCard = card('Company', kv([
    ['Name', c.company_name], ['Website', e.domain_name ? H(link('https://' + e.domain_name, e.domain_name)) : null], ['Founded', f.founded_year ? H(`${f.founded_year} <small class="muted">(${f.company_age_years} yrs)</small>`) : null],
    ['Employees', f.employees ? H(`${f.employees.toLocaleString()} <small class="muted">(${esc(f.employee_range)})${f.evidence?.employees ? ' · “' + esc(f.evidence.employees.text) + '”' : ''}</small>`) : null],
    ['Headquarters', f.headquarters], ['Decision maker', top ? H(`<b>${esc(top.name)}</b> <small class="muted">${esc(top.title || '')}</small>`) : null],
  ]));

  const domainCard = card('Domain &amp; email infrastructure', kv([
    ['Registered', dom.created ? H(`${esc(dom.created.slice(0, 10))} <small class="muted">(${dom.age_years ?? '?'} yrs)</small>`) : null], ['Expires', dom.expires ? dom.expires.slice(0, 10) : null],
    ['Registrar', dom.registrar], ['Mail provider', dns.email_provider], ['Sends mail via', dns.email_senders], ['Accepts email', yes(dns.accepts_email)],
    ['SPF', H(dns.spf ? '<span class="ok">✓ present</span>' : '<span class="nf">missing</span>')], ['DMARC policy', dns.dmarc_policy || (dns.dmarc ? 'present' : null)],
    ['Verified services', dns.txt_verifications], ['Email pattern', e.email_pattern ? H(`<code>${esc(e.email_pattern)}@${esc(e.domain_name)}</code>`) : null],
  ]));

  const people = (e.people || []).map(p => `<tr><td><b>${esc(p.name)}</b>${p.context && p.context !== 'website' ? `<br><small class="${p.context === 'team' ? 'ok' : 'muted'}">${esc(p.context)}</small>` : ''}</td><td>${esc(p.title || '')}</td>
    <td>${p.email ? `<a href="mailto:${esc(p.email)}">${esc(p.email)}</a> <small class="ok">found</small>` : p.email_guess ? `<code>${esc(p.email_guess)}</code><br><small class="muted" title="${esc((p.email_guess_alternatives || []).join(', '))}">guess · ${esc(p.email_guess_confidence)}</small>` : nf}</td>
    <td class="source">${p.source_url ? link(p.source_url) : esc(p.source)}</td></tr>`).join('');
  const peopleCard = card(`People <small class="muted">${(e.people || []).length}</small>`, people ? `<table class="mini"><thead><tr><th>Name</th><th>Title</th><th>Email</th><th>Source</th></tr></thead><tbody>${people}</tbody></table>` : `<p class="muted">No people found on the crawled pages. Include the team/leadership page (raise max pages).</p>`, 'wide');

  const emails = (e.contacts?.emails || []).map(x => `<tr><td><a href="mailto:${esc(x.email)}">${esc(x.email)}</a></td><td>${esc(x.type)}</td><td>${yes(x.on_company_domain)}</td><td>${x.domain_accepts_mail ? '<span class="ok">✓ MX</span>' : '<span class="err">no MX</span>'}${x.disposable ? ' <span class="err">disposable</span>' : ''}${x.free_provider ? ' <span class="muted">free mail</span>' : ''}</td></tr>`).join('');
  const phones = (e.contacts?.phones || []).map(x => `<tr><td>${esc(x.international || x.raw)}</td><td>${x.valid ? '<span class="ok">✓ valid</span>' : '<span class="err">invalid</span>'}</td><td>${esc(x.country || '')}${x.location ? ' · ' + esc(x.location) : ''}</td><td>${esc(x.type || '')}</td></tr>`).join('');
  const contactsCard = card('Verified contacts', `${emails ? `<table class="mini"><thead><tr><th>Email</th><th>Type</th><th>Own domain</th><th>Deliverability</th></tr></thead><tbody>${emails}</tbody></table>` : '<p class="muted">No emails found.</p>'}
    ${phones ? `<table class="mini"><thead><tr><th>Phone</th><th>Valid</th><th>Country</th><th>Line</th></tr></thead><tbody>${phones}</tbody></table>` : ''}`, 'wide');

  const byCat = {};
  for (const t of e.tech_stack || []) (byCat[t.category] ||= []).push(t);
  const techCard = card(`Tech stack <small class="muted">${(e.tech_stack || []).length}</small>`, Object.keys(byCat).length ? `<dl class="kv">${Object.entries(byCat).map(([cat, ts]) => `<dt>${esc(cat)}</dt><dd><div class="chips">${ts.map(t => `<span class="chip" title="${esc(t.evidence)} · ${t.pages} page(s)">${esc(t.name)}</span>`).join('')}</div></dd>`).join('')}</dl>` : '<p class="muted">Nothing detected.</p>');

  const socials = Object.entries(e.social || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${link(v.url)}${v.public_repos != null ? ` <small class="muted">· ${v.public_repos} repos · ${v.followers} followers</small>` : ''}</dd>`).join('');
  const socialCard = card('Social profiles', socials ? `<dl class="kv">${socials}</dl>` : '<p class="muted">None linked from the site.</p>');

  let aiCard;
  if (a) aiCard = card('AI analysis', `${a.summary ? `<p>${esc(a.summary)}</p>` : ''}${kv([['Industry', a.industry], ['Sub-industries', a.sub_industries], ['Business model', a.business_model], ['Target customers', a.target_customers], ['Key offerings', a.key_offerings], ['Notable clients', a.notable_clients], ['Value proposition', a.value_proposition], ['Size estimate', a.company_size_estimate], ['Competitors mentioned', a.competitors_mentioned]])}`, 'wide');
  else aiCard = card('AI analysis', `<p class="muted">${e.providers?.ai_configured ? 'AI analysis was switched off for this run.' : 'Not configured. Add <code>AI_API_KEY</code> (any OpenAI-compatible endpoint) to <code>.env</code> and restart to get an industry, summary, target customers and offerings.'}</p>`, 'wide');

  const g = e.registry;
  let registryCard;
  if (g) {
    const officers = (g.officers || []).filter(o => o.current);
    registryCard = card(`Company registry <small class="muted">${esc(SRC_NAMES[g.source] || 'OpenCorporates')} · match ${Math.round((g.match_confidence || 0) * 100)}%</small>`, kv([
      ['Legal name', g.name], ['Company number', g.company_number], ...(g.lei ? [['LEI', g.lei]] : []), ['Jurisdiction', g.jurisdiction_code], ['Status', g.current_status], ['Type', g.company_type],
      ['Incorporated', g.incorporation_date], ['Registered address', g.registered_address], ['Industry codes', g.industry_codes], ['Previous names', g.previous_names],
      ['Current officers', officers.length ? H(officers.map(o => `${esc(o.name)} <small class="muted">${esc(o.position || '')}${o.start_date ? ' · since ' + esc(o.start_date) : ''}</small>`).join('<br>')) : null],
      ['Parent company', g.controlling_company?.name],
    ]) + `<p class="muted small">${attribution(g)}${g.source_publisher && g.source !== 'gleif' ? ' · register: ' + esc(g.source_publisher) : ''}${g.registry_url && g.registry_url !== g.record_url ? ' · ' + link(g.registry_url, 'official record') : ''}</p>`, 'wide');
  } else {
    const tried = (e.providers?.registry_sources_tried || []).map(x => SRC_NAMES[x] || x).join(', ');
    registryCard = card('Company registry', `<p class="muted">${tried ? `No confident match for this company name in ${esc(tried)}.` : 'The registry lookup was switched off for this run.'}${e.providers?.opencorporates_configured || tried.includes('Companies House') ? '' : ' UK companies: add a free <code>COMPANIES_HOUSE_API_KEY</code> to <code>.env</code> to get directors.'}</p>`, 'wide');
  }

  const errs = Object.entries(e.errors || {});
  const provider = `<p class="muted small">Sources: website crawl, DNS-over-HTTPS, RDAP registry, phone metadata${e.providers?.hunter ? ', Hunter.io' : e.providers?.hunter_configured ? '' : ' · add <code>HUNTER_API_KEY</code> for verified staff emails'}${e.providers?.ai ? ', LLM' : ''}${e.providers?.registry ? ', ' + esc(SRC_NAMES[e.providers.registry_source] || 'registry') : ''} · enriched in ${((e.elapsed_ms || 0) / 1000).toFixed(1)}s${errs.length ? ' · <span class="err">' + errs.map(([k, v]) => esc(k + ': ' + v)).join('; ') + '</span>' : ''}</p>`;
  box.innerHTML = `<div class="egrid">${scoreCard}${companyCard}${domainCard}${peopleCard}${contactsCard}${techCard}${socialCard}${registryCard}${aiCard}</div>${provider}`;
}

async function runEnrichment() {
  try {
    await api(`/api/jobs/${currentJob}/enrich?ai_enrichment=${$('ai').checked}&registry_lookup=${$('registry_lookup').checked}`, {method: 'POST'});
    $('progress').classList.remove('hidden'); poll();
  } catch (e) { alert(e.message); }
}

// ------------------------------------------------------------------ history
async function loadHistory() {
  const rows = await api('/api/history');
  $('historyRows').innerHTML = rows.map(x => `<div class="history-row"><small>${esc(x.created_at.replace('T', ' ').slice(0, 19))}</small><b>${esc(x.seed_url)}</b><small>${esc(x.status)}</small><button class="ghost" onclick="openJob('${esc(x.id)}')">Open</button></div>`).join('') || '<p class="muted">No crawls yet.</p>';
}
async function openJob(id) { currentJob = id; render(await api('/api/jobs/' + id)); $('results').scrollIntoView({behavior: 'smooth'}); }

// ------------------------------------------------------------------ auth profiles
async function loadProfiles() {
  const list = await api('/api/profiles');
  const sel = $('profile'), cur = sel.value;
  sel.innerHTML = '<option value="">None</option>' + list.map(p => `<option value="${esc(p.name)}">${esc(p.name)} (${p.cookies} cookies)</option>`).join('');
  sel.value = list.some(p => p.name === cur) ? cur : '';
  $('profileRows').innerHTML = list.length ? `<table class="mini"><thead><tr><th>Profile</th><th>Cookies</th><th>Domains</th><th>Updated</th><th></th></tr></thead><tbody>${list.map(p => `<tr>
      <td><b>${esc(p.name)}</b>${p.browser_open ? ' <span class="warn">browser open</span>' : ''}${p.useragent ? '<br><small class="muted" title="' + esc(p.useragent) + '">custom user-agent</small>' : ''}</td>
      <td>${p.cookies}</td><td><small>${esc(p.cookie_domains.join(', '))}</small></td><td><small>${p.updated ? new Date(p.updated * 1000).toLocaleString() : ''}</small></td>
      <td class="btns"><button class="ghost" onclick="useProfile('${esc(p.name)}')">Use</button><button class="ghost danger" onclick="deleteProfile('${esc(p.name)}')">Delete</button></td></tr>`).join('')}</tbody></table>` : '<p class="muted">No profiles yet.</p>';
  return list;
}
function useProfile(name) { $('profile').value = name; document.querySelector('.access-opts').open = true; window.scrollTo({top: 0, behavior: 'smooth'}); }
async function deleteProfile(name) { if (!confirm(`Delete profile "${name}" and its cookies?`)) return; await api('/api/profiles/' + encodeURIComponent(name), {method: 'DELETE'}); loadProfiles(); }

async function openBrowser() {
  const name = $('bName').value.trim(), url = $('bUrl').value.trim();
  if (!name || !url) return alert('Enter a profile name and a start URL');
  $('bStatus').textContent = 'Opening browser…';
  try {
    await api(`/api/profiles/${encodeURIComponent(name)}/browser`, {method: 'POST', body: JSON.stringify({url})});
    $('bStatus').innerHTML = 'A browser window is open on your desktop. Log in / complete the check there, then click <b>Finish &amp; save</b>. Cookies are also saved automatically every few seconds.';
    loadProfiles();
  } catch (e) { $('bStatus').textContent = 'Error: ' + e.message; }
}
async function finishBrowser() {
  const name = $('bName').value.trim();
  if (!name) return;
  const r = await api(`/api/profiles/${encodeURIComponent(name)}/browser/finish`, {method: 'POST'});
  $('bStatus').textContent = r.profile ? `Saved “${name}” with ${r.profile.cookies} cookies. Select it under Access options and crawl.` : 'Browser closed.';
  await loadProfiles(); $('profile').value = name;
}
function solveInBrowser(host, url) {
  $('bName').value = host; $('bUrl').value = url;
  $('access').scrollIntoView({behavior: 'smooth'}); openBrowser();
}
async function rerunWithProfile(host) {
  const list = await loadProfiles();
  if (!list.some(p => p.name === host)) return alert(`Profile "${host}" does not exist yet. Use "Open browser to solve" first.`);
  $('profile').value = host; $('escalate').checked = true; start();
}
async function saveCookies() {
  const body = {name: $('cName').value.trim(), domain: $('cDomain').value.trim(), cookies: $('cCookies').value, headers: $('cHeaders').value, useragent: $('cUa').value.trim()};
  if (!body.name) return alert('Enter a profile name');
  try { await api('/api/profiles', {method: 'POST', body: JSON.stringify(body)}); $('cCookies').value = ''; await loadProfiles(); $('profile').value = body.name; alert('Profile saved'); }
  catch (e) { alert(e.message); }
}

// ------------------------------------------------------------------ company registries (GLEIF, Companies House, OpenCorporates)
const SRC_NAMES = {gleif: 'GLEIF', companieshouse: 'Companies House', opencorporates: 'OpenCorporates', directory: 'OpenStreetMap + Wikidata', osm: 'OpenStreetMap', wikidata: 'Wikidata'};
// ------------------------------------------------------------------ find companies: industry + country -> list -> automatic crawl -> Excel
let fcId = null, fcTimer = null, fcShown = 25;
const srcTag = (c, f) => { const s = (c.best_source || {})[f]; return s && s !== 'website' ? ` <span class="tag">${esc(s)}</span>` : ''; };
const fcSite0 = c => c.website ? link(/^https?:/i.test(c.website) ? c.website : 'http://' + c.website, c.website.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, '').slice(0, 40)) : '<span class="muted">no website listed</span>';
const fcSite = c => { const b = c.best || {}, w = b.website || c.website; return w ? link(/^https?:/i.test(w) ? w : 'http://' + w, w.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, '').slice(0, 40)) + srcTag(c, 'website') : '<span class="muted">no website found</span>'; };
function fcDetails(c) {
  const d = c.details || {}, out = [];
  if (d.decision_maker) out.push(`${esc(d.decision_maker)}${d.decision_maker_title ? ' (' + esc(d.decision_maker_title) + ')' : ''}`);
  const emails = (d.all_emails || '').split(',').map(x => x.trim()).filter(Boolean);
  if (emails.length) out.push('✉ ' + esc(emails.slice(0, 3).join(', ')) + (emails.length > 3 ? ` +${emails.length - 3}` : ''));
  if (d.phones_e164) out.push('☎ ' + esc(d.phones_e164.split(',')[0]));
  const soc = Object.keys(d).filter(k => k.startsWith('social_')).map(k => link(d[k], k.slice(7)));
  if (soc.length) out.push(soc.join(' · '));
  if (d.lead_score != null) out.push(`score ${esc(d.lead_score)}${d.lead_grade ? ' (' + esc(d.lead_grade) + ')' : ''}`);
  return out.join('<br>');
}
async function fcSearch(category) {
  const body = {industry: $('fcInd').value.trim(), country: $('fcCountry').value.trim(), name: $('fcName').value.trim(), limit: +$('fcLimit').value, category: typeof category === 'string' ? category : ''};
  try { fcId = (await api('/api/companies/search', {method: 'POST', body: JSON.stringify(body)})).id; fcShown = 25; fcPoll(); } catch (e) { alert(e.message); }
}
async function fcPoll() {
  clearTimeout(fcTimer);
  const r = await api('/api/companies/' + fcId);
  fcRender(r);
  if (r.status === 'running' || r.crawl?.status === 'running') fcTimer = setTimeout(fcPoll, 2000); else fcLoadLists();
}
function fcRender(r) {
  $('fcResult').classList.remove('hidden');
  const cs = r.companies, cr = r.crawl || {}, n = cs.length;
  $('fcTitle').textContent = r.label;
  $('fcNotes').innerHTML = (r.notes || []).map(x => `<p class="muted small">${esc(x)}</p>`).join('') + (r.error ? `<p class="err small">${esc(r.error)}</p>` : '');
  const crawling = cr.status === 'running';
  $('fcProgress').innerHTML = r.status === 'running' ? `<span class="spinner"></span> ${esc(r.progress)}`
    : crawling ? `<span class="spinner"></span> Collecting contact details (website, OpenStreetMap, official register): ${cr.done} of ${cr.total}${cr.current ? ' (' + esc(cr.current) + ')' : ''}`
    : `${n} companies` + (cr.total ? ` · ${cr.done} companies processed${cr.failed ? `, ${cr.failed} website(s) could not be read` : ''}` : '');
  $('fcBar').style.width = cr.total ? Math.round(100 * cr.done / cr.total) + '%' : '0%';
  $('fcExports').classList.toggle('hidden', !n);
  for (const k of ['Xlsx', 'Csv']) $('fc' + k).href = `/api/companies/${r.id}/export/${k.toLowerCase()}`;
  const facets = r.facets || [];
  $('fcFacets').innerHTML = facets.length > 1 ? '<span class="muted small">Narrow by category: </span>' + facets.map(f => `<button class="chip-btn" onclick="fcSearch('${esc(f.category).replace(/'/g, "\\'")}')">${esc(f.category)} <small>${f.count}</small></button>`).join('') : '';
  $('fcRows').innerHTML = cs.slice(0, fcShown).map((c, i) => { const b = c.best || {};
    const email = b.email || c.email, phone = b.phone || c.phone, miss = (c.missing || []).filter(x => c.crawl_status !== 'running' && x !== 'contact_person');
    return `<tr><td>${i + 1}</td>
    <td><b>${esc(c.name)}</b><br><small class="muted">${esc([c.category, c.city].filter(Boolean).join(' · '))}</small>${c.match && c.match !== 'category' ? `<br><small class="warn">${esc(c.match)}</small>` : ''}</td>
    <td><small>${fcSite(c)}${phone ? `<br>${esc(phone)}${srcTag(c, 'phone')}` : ''}${email ? `<br>${esc(email)}${srcTag(c, 'email')}` : ''}${miss.length ? `<br><span class="muted">missing: ${esc(miss.join(', '))}</span>` : ''}</small></td>
    <td><small>${fcDetails(c)}${c.registry?.name ? `<br><span class="muted">registered: ${esc(c.registry.name)}${c.registry.current_status ? ' · ' + esc(c.registry.current_status) : ''}</span>` : ''}${c.crawl_status === 'running' ? '<span class="spinner"></span>' : c.crawl_status === 'failed' ? `<span class="err">${esc(c.crawl_error || 'website could not be read')}</span>` : ''}</small></td>
    <td class="btns">${c.job_id && c.crawl_status === 'completed' ? `<button class="ghost" onclick="openJob('${esc(c.job_id)}')">Full profile</button>` : ''}</td></tr>`; }).join('');
  $('fcMore').classList.toggle('hidden', fcShown >= n);
  $('fcMore').textContent = `Show next 25 (${n - fcShown} more)`;
  $('fcAttribution').textContent = (cs[0]?.source === 'gleif') ? 'GLEIF LEI data: CC0.' : 'Company data: (c) Overture Maps Foundation, CDLA Permissive 2.0.';
}
async function fcLoadLists() {
  const rows = await api('/api/companies');
  $('fcLists').innerHTML = rows.map(x => `<div class="history-row"><small>${esc(x.created_at.replace('T', ' ').slice(0, 19))}</small><b>${esc(x.label)}</b><small>${esc(x.status)} · ${x.count}</small><button class="ghost" onclick="fcOpen('${esc(x.id)}')">Open</button></div>`).join('') || '<p class="muted">No searches yet.</p>';
}
async function fcOpen(id) { fcId = id; fcShown = 25; await fcPoll(); $('fcResult').scrollIntoView({behavior: 'smooth', block: 'start'}); }
document.addEventListener('click', e => {
  if (!e.target.matches('.tab')) return;
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  e.target.classList.add('active');
  document.querySelectorAll('.tabpane').forEach(p => p.classList.toggle('hidden', p.id !== e.target.dataset.tab));
});
$('start').onclick = start; $('refresh').onclick = loadHistory; $('refreshProfiles').onclick = loadProfiles;
$('bOpen').onclick = openBrowser; $('bFinish').onclick = finishBrowser; $('cSave').onclick = saveCookies;
$('fcSearch').onclick = () => fcSearch(); ['fcInd', 'fcCountry', 'fcName'].forEach(id => $(id).addEventListener('keydown', e => { if (e.key === 'Enter') fcSearch(); })); $('fcMore').onclick = () => { fcShown += 25; fcPoll(); };
api('/api/status').then(s => {
  if (s.version) $('appVersion').textContent = 'v' + s.version;
  $('providers').innerHTML = `<div class="prov"><span class="${s.ai ? 'ok' : 'nf'}">●</span> AI ${s.ai ? esc(s.ai_model) : 'off'}</div><div class="prov"><span class="${s.hunter ? 'ok' : 'nf'}">●</span> Hunter.io ${s.hunter ? 'on' : 'off'}</div>${(s.registries || []).map(x => `<div class="prov"><span class="${x.configured ? 'ok' : 'nf'}">●</span> ${esc(SRC_NAMES[x.id])} ${x.configured ? 'on' : 'off'}</div>`).join('')}`;
  if (!s.ai) $('aiNote').textContent = '(needs AI_API_KEY)';
});
loadHistory(); loadProfiles(); fcLoadLists();
