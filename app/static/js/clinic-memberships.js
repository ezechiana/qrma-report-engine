(() => {
  const root = document.getElementById('clinic-memberships');
  if (!root) return;
  const el = id => document.getElementById(id);
  const own = root.dataset.ownView === 'true', clinic = root.dataset.clinicId;
  const base = `/api/clinics/${encodeURIComponent(clinic)}/memberships`;
  const dialog = el('cm-confirm');
  let actor = null, canEdit = false, loaded = false, busy = false, pending = null, focusReturn = null;
  const storageKey = () => `go360.membership.intent.v1:${actor}:${clinic}`;
  function persist() {
    try { const {name, ...stored} = pending || {}; pending ? sessionStorage.setItem(storageKey(), JSON.stringify(stored)) : sessionStorage.removeItem(storageKey()); }
    catch (_) { /* Server uniqueness still prevents duplicate membership creation. */ }
    el('cm-pending').hidden = !pending;
  }
  function error(message, inDialog = false) {
    const box = el(inDialog ? 'cm-confirm-error' : 'cm-error');
    box.textContent = message; box.hidden = false; box.focus();
  }
  function controls() {
    if (el('cm-add-fields')) el('cm-add-fields').disabled = !loaded || !canEdit || busy || !!pending;
    root.querySelectorAll('[data-change]').forEach(b => { b.disabled = !loaded || !canEdit || busy || !!pending; });
    el('cm-submit').disabled = !pending || busy || !canEdit || !loaded;
    el('cm-cancel').disabled = busy;
    el('cm-cancel').textContent = pending ? 'Cancel' : 'Close';
    el('cm-reload').disabled = busy;
    el('cm-retry').disabled = busy || !canEdit || !loaded;
  }
  function text(tag, value) { const node = document.createElement(tag); node.textContent = value; return node; }
  function render(items) {
    const list = el('cm-list'); list.replaceChildren();
    if (!items.length) list.append(text('li', 'No memberships yet.'));
    for (const row of items) {
      const item = document.createElement('li'); item.dataset.membershipId = row.id;
      item.append(text('h3', row.full_name), text('p', `Practitioner ID: ${row.practitioner_user_id}`));
      if (own) item.append(text('p', `Clinic: ${row.clinic_name}`));
      item.append(text('p', `Membership: ${row.status}`), text('p', 'Clinical approval: not granted · Clinical seat: unassigned'));
      if (!own && canEdit) {
        const actions = document.createElement('div'); actions.className = 'cm-actions';
        for (const [status, label] of [['active','Reactivate'], ['suspended','Suspend'], ['revoked','Revoke']]) {
          if (row.status === status || (row.status === 'revoked' && status === 'suspended')) continue;
          const button = text('button', label); button.type = 'button'; button.className = 'btn secondary'; button.dataset.change = status;
          button.addEventListener('click', () => review({method:'PATCH', id:row.id, status, revision:row.revision,
            practitioner_user_id:row.practitioner_user_id, name:row.full_name}, button));
          actions.append(button);
        }
        item.append(actions);
      }
      list.append(item);
    }
    controls();
  }
  async function load() {
    busy = true; loaded = false; controls(); el('cm-error').hidden = true;
    el('cm-status').textContent = 'Loading memberships…';
    try {
      const response = await apiFetch(own ? '/api/practitioner/memberships/me' : base);
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not load memberships.');
      loaded = true; canEdit = data.can_edit; el('cm-support').hidden = own || canEdit;
      if (!own) el('cm-clinic-name').textContent = data.clinic_name;
      if (pending?.submitted && data.items.some(row => pending.method === 'POST' ?
        row.practitioner_user_id === pending.practitioner_user_id : row.id === pending.id && row.status === pending.status)) {
        pending = null; persist();
      }
      render(data.items); el('cm-status').textContent = '';
    } catch (e) { canEdit = false; render([]); error(e.message || 'Could not load memberships. Please retry.'); el('cm-status').textContent = ''; }
    finally { busy = false; controls(); }
  }
  function review(command, button) {
    if (busy || !canEdit || !loaded) return;
    pending = command; persist(); focusReturn = button;
    const label = command.method === 'POST' ? 'Add' : ({active:'Reactivate',suspended:'Suspend',revoked:'Revoke'}[command.status]);
    el('cm-confirm-description').textContent = `${label} membership for ${command.name || command.practitioner_user_id} in ${el('cm-clinic-name').textContent}? Only this clinic is affected. This does not grant clinical approval or patient access.`;
    el('cm-confirm-error').hidden = true; controls(); dialog.showModal(); el('cm-submit').focus();
  }
  function close() {
    dialog.close();
    if (focusReturn && document.contains(focusReturn)) focusReturn.focus();
    else el('cm-reload').focus();
  }
  function cancel() { if (busy) return; if (!pending?.submitted) pending = null; persist(); controls(); close(); }
  dialog.addEventListener('cancel', e => { e.preventDefault(); cancel(); });
  el('cm-cancel').addEventListener('click', cancel);
  el('cm-reload').addEventListener('click', load);
  el('cm-retry').addEventListener('click', () => { if (pending) review(pending, el('cm-retry')); });
  el('cm-add-form')?.addEventListener('submit', e => {
    e.preventDefault(); if (busy || pending || !canEdit || !loaded) return;
    const id = el('cm-practitioner').value.trim();
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
      error('Enter the practitioner’s valid UUID.'); el('cm-practitioner').setAttribute('aria-invalid','true'); return;
    }
    el('cm-practitioner').removeAttribute('aria-invalid');
    review({method:'POST', practitioner_user_id:id, key:crypto.randomUUID()}, el('cm-add'));
  });
  el('cm-submit').addEventListener('click', async () => {
    if (!pending || busy || !loaded || !canEdit) return;
    const command = pending; pending.submitted = true; persist(); busy = true; controls(); el('cm-confirm-error').hidden = true;
    try {
      const headers = {'Content-Type':'application/json', 'X-Expected-Actor':actor};
      if (command.method === 'POST') headers['Idempotency-Key'] = command.key;
      const body = command.method === 'POST' ? {practitioner_user_id:command.practitioner_user_id} : {status:command.status,revision:command.revision};
      const response = await apiFetch(command.method === 'POST' ? base : `${base}/${encodeURIComponent(command.id)}`,
        {method:command.method,headers,body:JSON.stringify(body)});
      const data = await response.json();
      if (!response.ok) {
        if (response.status < 500) {
          pending = null; persist();
          if ([403,404].includes(response.status)) canEdit = false;
        }
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save the membership. Reload and retry.');
      }
      pending = null; persist(); busy = false; close(); await load();
      el('cm-status').textContent = `Membership ${data.status}. Clinical approval and paid seats are unchanged.`;
      el('cm-reload').focus();
    } catch (e) { error(e.message || 'Could not confirm the change. Retry the same change or reload to check.', true); }
    finally { busy = false; controls(); }
  });
  (async () => {
    try {
      const response = await apiFetch('/auth/me'); if (!response.ok) return;
      actor = (await response.json()).user.id;
      // Clean journals from another account in this tab; never restore its intent.
      for (const key of Object.keys(sessionStorage)) if (key.startsWith('go360.membership.intent.v1:') && !key.startsWith(`go360.membership.intent.v1:${actor}:`)) sessionStorage.removeItem(key);
      try {
        const value = JSON.parse(sessionStorage.getItem(storageKey()) || 'null');
        if (value && ['POST','PATCH'].includes(value.method)) pending = value;
      } catch (_) {}
      persist(); await load();
    } catch (_) { error('Could not load memberships. Please reload.'); }
  })();
})();
