  const SIDEBAR_HTML = `
    <div class="sb-brand"><span class="sb-brand-text">spr<span>ntly</span></span></div>
    <div class="sb-section-title">Intelligence</div>
    <a class="sb-item" data-goto="brief"><span class="sb-icon">✦</span>Weekly brief<span class="sb-count">4</span></a>
    <a class="sb-item" data-goto="ondemand"><span class="sb-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></span>Ask Sprntly<span class="sb-count">12</span></a>
    <a class="sb-item" data-goto="shipped"><span class="sb-icon">✓</span>Shipped</a>
    <div class="sb-spacer"></div>
    <div class="sb-section-title">Workspace</div>
    <a class="sb-item" data-goto="connectors"><span class="sb-icon">⊞</span>Connectors</a>
    <a class="sb-item" data-goto="team"><span class="sb-icon">○</span>Team</a>
    <a class="sb-item" data-goto="settings"><span class="sb-icon">⚙</span>Settings</a>
    <div class="sb-footer">
      <div class="sb-user">
        <div class="sb-avatar">KA</div>
        <div class="sb-user-info">
          <div class="sb-user-name">Kwame</div>
          <div class="sb-user-email">kwame@sprntly.ai</div>
        </div>
      </div>
    </div>
  `;
  document.querySelectorAll('.sidebar').forEach(sb => { sb.innerHTML = SIDEBAR_HTML; });

  const AI_CONTEXTS = {
    chat:     { path: '/ home', suggest: ['Open this week\'s brief', 'Help me prioritize my roadmap', 'What should I focus on today?'] },
    brief:    { path: '/ weekly brief', suggest: ['Why is #01 ranked higher than #02?', 'Show the raw signals behind the SMS issue', 'Compare this brief to last week\'s'] },
    detail:   { path: '/ evidence', suggest: ['Run a sensitivity analysis on the revenue model', 'Pull more similar tickets', 'Who has context on SMS verification?'] },
    prd:      { path: '/ PRD', suggest: ['Make the test plan more rigorous', 'Add rollback criteria', 'Who should own this?'] },
    ondemand: { path: '/ ask sprntly', suggest: ['Generate a Q3 strategy', 'Draft a PRD for team folder permissions', 'Compare retention across our top 3 segments'] },
    past:     { path: '/ past briefs', suggest: ['Which finding type ships most?', 'Any declined findings worth reconsidering?'] },
    shipped:  { path: '/ shipped', suggest: ['What moved our core metric most?', 'Which shipped items underperformed estimates?'] },
    settings: { path: '/ settings', suggest: ['Recommend a delivery cadence for my role', 'Should I upgrade to Growth?'] },
    team:     { path: '/ team', suggest: ['Who opens the brief most often?', 'Suggest who to invite from Slack'] },
    connectors:{ path: '/ connectors', suggest: ['Which unconnected source would help most?', 'What would Mixpanel add?'] }
  };
  const APP_SCREENS = Object.keys(AI_CONTEXTS);

  function updateAIBar(screen) {
    const wrap = document.getElementById('aiBarWrap');
    if (!APP_SCREENS.includes(screen)) { wrap.style.display = 'none'; return; }
    wrap.style.display = 'block';
    const cfg = AI_CONTEXTS[screen];
    document.getElementById('aiCtxPath').textContent = cfg.path;
    document.getElementById('aiCtxExtra').innerHTML = '';
    const sugg = document.getElementById('aiBarSuggest');
    sugg.innerHTML = cfg.suggest.map(s => `<button class="ai-bar-chip">${s}</button>`).join('');
  }

  function showScreen(id) {
    document.querySelectorAll('.screen').forEach(s => s.classList.remove('visible'));
    const el = document.getElementById(id);
    if (el) el.classList.add('visible');
    document.querySelectorAll('.picker-btn').forEach(b => b.classList.toggle('active', b.dataset.screen === id));
    document.querySelectorAll('.sb-item').forEach(item => item.classList.toggle('active', item.dataset.goto === id));
    closeDrawers(); closeApproveModal(); closeShareMenu();
    updateAIBar(id);
    window.scrollTo({ top: 0, behavior: 'instant' });
  }

  // Drawers
  function openDrawer(id) {
    document.getElementById('drawerOverlay').classList.add('open');
    document.getElementById(id).classList.add('open');
  }
  function closeDrawers() {
    document.getElementById('drawerOverlay').classList.remove('open');
    document.querySelectorAll('.drawer').forEach(d => d.classList.remove('open'));
  }
  document.getElementById('drawerOverlay').addEventListener('click', closeDrawers);

  // Modal
  function openApproveModal() { document.getElementById('approveModal').classList.add('open'); }
  function closeApproveModal() { document.getElementById('approveModal').classList.remove('open'); }
  document.getElementById('approveModal').addEventListener('click', (e) => {
    if (e.target.id === 'approveModal') closeApproveModal();
  });

  // Review past dropdown
  function toggleReviewPast(e) {
    e.stopPropagation();
    document.getElementById('reviewPastMenu').classList.toggle('open');
  }
  function closeReviewPast() { document.getElementById('reviewPastMenu').classList.remove('open'); }
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.review-past-menu') && !e.target.closest('[onclick*="toggleReviewPast"]')) {
      closeReviewPast();
    }
  });
  // Review past filter clicks
  document.addEventListener('click', (e) => {
    const rpFilter = e.target.closest('.review-past-filter');
    if (rpFilter) {
      rpFilter.parentElement.querySelectorAll('.review-past-filter').forEach(f => f.classList.remove('active'));
      rpFilter.classList.add('active');
    }
  });

  // Invite modal
  function openInviteModal() { document.getElementById('inviteModal').classList.add('open'); }
  function closeInviteModal() { document.getElementById('inviteModal').classList.remove('open'); }
  document.getElementById('inviteModal').addEventListener('click', (e) => {
    if (e.target.id === 'inviteModal') closeInviteModal();
  });
  function addInviteRow() {
    const rows = document.getElementById('inviteRows');
    const row = document.createElement('div');
    row.className = 'invite-email-row';
    row.innerHTML = `
      <input type="email" class="input" placeholder="teammate@company.com" />
      <select class="ticket-select"><option>Admin</option><option selected>Viewer</option></select>
      <button class="invite-remove-btn" onclick="removeInviteRow(this)">×</button>
    `;
    rows.appendChild(row);
    row.querySelector('input').focus();
  }
  function removeInviteRow(btn) {
    const rows = document.getElementById('inviteRows');
    if (rows.children.length > 1) btn.closest('.invite-email-row').remove();
  }
  function sendInvites() {
    const rows = document.querySelectorAll('#inviteRows .invite-email-row');
    const count = Array.from(rows).filter(r => r.querySelector('input').value.trim()).length || rows.length;
    closeInviteModal();
    showToast(`${count} invite${count === 1 ? '' : 's'} sent`, 'They\'ll get an email with a sign-up link. Expires in 7 days.', 'View pending →');
  }

  // Share dropdown
  function toggleShare(e) { e.stopPropagation(); document.getElementById('shareMenu').classList.toggle('open'); }
  function closeShareMenu() { document.getElementById('shareMenu').classList.remove('open'); }
  function shareVia(type) {
    closeShareMenu();
    const cfg = {
      email: { icon: '✉', title: 'Opening email draft', sub: 'Your email client will open with the PRD attached.' },
      slack: { icon: 'Sl', title: 'Posted to Slack', sub: 'PRD shared in #product. Your team can react &amp; comment inline.' },
      link: { icon: '⎘', title: 'Link copied', sub: 'Anyone at sprntly.ai with the link can view this PRD.' }
    }[type];
    showToast(cfg.title, cfg.sub);
  }
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.share-menu') && !e.target.closest('button[onclick*="toggleShare"]')) closeShareMenu();
  });

  // Send to Claude / create ticket confirmations
  function sendToClaude() {
    closeDrawers();
    showToast('Sent to Claude Code', 'Claude is scoping the work — we\'ll ping Slack when the PR opens.', 'Track progress →');
  }
  function createTicket() {
    closeDrawers();
    showToast('Ticket created in Linear', 'SPR-412 · Assigned to Lena · High priority. We\'ll fold impact into Shipped when closed.', 'Open ticket →');
  }

  // Toast
  let toastTimer;
  function showToast(title, sub, link) {
    document.getElementById('toastTitle').textContent = title;
    document.getElementById('toastSub').innerHTML = sub + (link ? ` <a class="toast-link" href="#">${link}</a>` : '');
    document.getElementById('toast').classList.add('visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, 5500);
  }
  function hideToast() { document.getElementById('toast').classList.remove('visible'); }

  // Delegated click
  document.addEventListener('click', (e) => {
    const goto = e.target.closest('[data-goto]');
    if (goto) { e.preventDefault(); showScreen(goto.dataset.goto); return; }
    const picker = e.target.closest('.picker-btn');
    if (picker) { showScreen(picker.dataset.screen); return; }
    const role = e.target.closest('.role-card');
    if (role) { role.parentElement.querySelectorAll('.role-card').forEach(r => r.classList.remove('selected')); role.classList.add('selected'); return; }
    const chip = e.target.closest('.metric-chip');
    if (chip && !chip.textContent.includes('Edit')) { chip.classList.toggle('selected'); return; }
    const conn = e.target.closest('.conn-card');
    if (conn) { conn.classList.toggle('connected'); updateConnProgress(); return; }
    const toggle = e.target.closest('.toggle');
    if (toggle) { toggle.classList.toggle('on'); return; }
    const assignee = e.target.closest('.ticket-assignee-chip');
    if (assignee) { assignee.classList.toggle('selected'); return; }

    // Ask AI button — stuff context into the AI bar
    const askAI = e.target.closest('.ask-ai-btn');
    if (askAI) {
      const q = askAI.dataset.ask || '';
      const ta = document.querySelector('.ai-bar-textarea');
      if (ta) { ta.value = q; ta.focus(); ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'; }
      return;
    }

    // Connector stage pills
    const stagePill = e.target.closest('.conn-stage-pill');
    if (stagePill) { switchConnStage(stagePill.dataset.stage); return; }

    // Shipped range tabs (30/60/90)
    const rangeTab = e.target.closest('.shipped-range-tab');
    if (rangeTab) {
      rangeTab.parentElement.querySelectorAll('.shipped-range-tab').forEach(t => t.classList.remove('active'));
      rangeTab.classList.add('active');
      return;
    }

    // Past briefs filter tabs
    const pastTab = e.target.closest('.past-tab');
    if (pastTab) {
      pastTab.parentElement.querySelectorAll('.past-tab').forEach(t => t.classList.remove('active'));
      pastTab.classList.add('active');
      return;
    }

    // On-demand rail tabs
    const odRailTab = e.target.closest('.od-rail-tab');
    if (odRailTab) {
      odRailTab.parentElement.querySelectorAll('.od-rail-tab').forEach(t => t.classList.remove('active'));
      odRailTab.classList.add('active');
      return;
    }

    // Chat suggestion cards -> stuff into AI bar
    const chatSug = e.target.closest('.chat-suggestion');
    if (chatSug) {
      const ta = document.querySelector('.ai-bar-textarea');
      const title = chatSug.querySelector('.chat-suggestion-title');
      if (ta && title) { ta.value = title.textContent; ta.focus(); ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 140) + 'px'; }
      return;
    }

    // Close share menu on outside click
    if (!e.target.closest('.share-menu') && !e.target.closest('[onclick*="toggleShare"]')) {
      const sm = document.getElementById('shareMenu');
      if (sm) sm.classList.remove('open');
    }

    // AI bar chip
    const aiChip = e.target.closest('.ai-bar-chip');
    if (aiChip) {
      const ta = document.querySelector('.ai-bar-textarea');
      ta.value = aiChip.textContent; ta.focus(); return;
    }
  });

  // Connector staging logic
  const STAGES = ['analytics','feedback','calls','revenue','reviews','pm','code'];
  const STAGE_LABELS = { analytics: 'Product analytics', feedback: 'Customer feedback', calls: 'Calls & conversations', revenue: 'Revenue & CRM', reviews: 'Reviews & store', pm: 'Project management', code: 'Code' };
  let currentStage = 'analytics';
  const doneStages = new Set();

  function switchConnStage(id) {
    currentStage = id;
    document.querySelectorAll('.conn-stage-body').forEach(b => b.style.display = 'none');
    document.getElementById('stage-' + id).style.display = 'block';
    document.querySelectorAll('.conn-stage-pill').forEach(p => {
      p.classList.toggle('active', p.dataset.stage === id);
      p.classList.toggle('done', doneStages.has(p.dataset.stage) && p.dataset.stage !== id);
    });
    const idx = STAGES.indexOf(id);
    const nextBtn = document.getElementById('connNext');
    const prevBtn = document.getElementById('connPrev');
    if (idx < STAGES.length - 1) { nextBtn.textContent = 'Next: ' + STAGE_LABELS[STAGES[idx + 1]] + ' →'; nextBtn.onclick = () => { doneStages.add(id); switchConnStage(STAGES[idx + 1]); }; }
    else { nextBtn.textContent = 'Finish — Continue to Slack →'; nextBtn.onclick = () => { showScreen('ob-7'); }; }
    prevBtn.style.visibility = idx === 0 ? 'hidden' : 'visible';
    prevBtn.onclick = () => { if (idx > 0) switchConnStage(STAGES[idx - 1]); };
    // Update hero progress
    const heroProg = document.getElementById('heroProgress');
    if (heroProg) heroProg.innerHTML = `Step ${idx+1} of 7 — <span style="color: var(--accent-2);">${STAGE_LABELS[id]}</span>.`;
    updateConnProgress();
  }
  function updateConnProgress() {
    const count = document.querySelectorAll('#ob-6 .conn-card.connected').length;
    const prog = document.getElementById('connProgress');
    if (prog) prog.textContent = count + ' connected';
  }
  document.addEventListener('DOMContentLoaded', () => setTimeout(() => switchConnStage('analytics'), 0));

  // Selection-to-ask
  document.addEventListener('mouseup', (e) => {
    setTimeout(() => {
      const sel = window.getSelection();
      const text = sel ? sel.toString().trim() : '';
      const pill = document.getElementById('selectionAsk');
      if (text.length > 3 && text.length < 400 && !e.target.closest('.ai-bar-wrap') && !e.target.closest('.picker') && !e.target.closest('.drawer') && !e.target.closest('.modal')) {
        const range = sel.getRangeAt(0);
        const rect = range.getBoundingClientRect();
        pill.style.top = (window.scrollY + rect.top - 36) + 'px';
        pill.style.left = (rect.left + rect.width / 2 - 80) + 'px';
        pill.classList.add('visible');
      } else { pill.classList.remove('visible'); }
    }, 10);
  });
  document.getElementById('selectionAsk').addEventListener('click', () => {
    const sel = window.getSelection().toString().trim();
    const ta = document.querySelector('.ai-bar-textarea');
    if (ta) { ta.value = `About "${sel.slice(0, 80)}${sel.length > 80 ? '…' : ''}" — `; ta.focus(); }
    document.getElementById('selectionAsk').classList.remove('visible');
    window.getSelection().removeAllRanges();
  });

  // Cmd/Ctrl+K
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      const ta = document.querySelector('.ai-bar-textarea');
      if (ta && ta.offsetParent !== null) { e.preventDefault(); ta.focus(); }
    }
    if (e.key === 'Escape') { closeDrawers(); closeApproveModal(); closeShareMenu(); }
  });

  // Auto-grow textarea
  document.addEventListener('input', (e) => {
    if (e.target.classList.contains('ai-bar-textarea')) {
      e.target.style.height = 'auto';
      e.target.style.height = Math.min(e.target.scrollHeight, 140) + 'px';
    }
  });

  function expandOdRail() {
    const layout = document.getElementById('odLayout');
    if (layout) layout.classList.add('rail-expanded');
  }
  function collapseOdRail() {
    const layout = document.getElementById('odLayout');
    if (layout) layout.classList.remove('rail-expanded');
  }
  function newConversation() {
    // Clear any active state and focus the AI bar textarea
    document.querySelectorAll('.od-conv-item').forEach(i => i.classList.remove('active'));
    const first = document.querySelector('.od-conv-item');
    if (first) first.classList.add('active');
    const ta = document.querySelector('.ai-bar-textarea');
    if (ta) { ta.focus(); }
  }

  showScreen('ob-1');
