(function () {
    'use strict';
    const $ = id => document.getElementById(id), root = $('clinic-registry');
    if (!root) return;
    const session = window.GO360ClinicSession, UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    const s = {epoch: 0, selection: 0, actor: null, orgs: [], clinics: [], selected: null, ready: false,
        loading: false, loaded: false, pending: null, journal: null, storageError: '', recovery: false,
        busy: false, confirmed: null, kind: null, opener: null, admin: false, initializing: false, observing: false};
    let mutation = null;
    class HttpError extends Error { constructor(status) { super('Request unavailable'); this.status = status; } }
    function el(tag, text, attrs = {}) { const node = document.createElement(tag); if (text !== null) node.textContent = text; for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); return node; }
    function button(text, action, secondary = true) { const node = el('button', text, {type: 'button', class: 'btn' + (secondary ? ' secondary' : '')}); node.addEventListener('click', action); return node; }
    function row(value, parent) { return value && UUID.test(value.id || '') && typeof value.display_name === 'string' && value.display_name.length > 0 && (parent === undefined || value.organisation_id === parent); }
    function rows(value, parent) { if (!Array.isArray(value) || !value.every(v => row(v, parent)) || new Set(value.map(v => v.id)).size !== value.length) throw new HttpError(502); return value; }
    function ref(id, list) { let n = 8; while (n < 36 && list.some(v => v.id !== id && v.id.slice(0, n) === id.slice(0, n))) n += 4; return id.slice(0, n).toUpperCase(); }
    function notice(text = '', variant = '', action) { const box = $('cr-message'); box.replaceChildren(); box.hidden = !text; box.className = 'cr-notice ' + variant; if (text) box.append(el('span', text)); if (action) { box.append(el('br', null), button(action[0], action[1])); } }
    async function request(url, options = {}) {
        const abort = new AbortController(), relay = () => abort.abort(), timer = setTimeout(relay, 15000);
        options.signal?.addEventListener('abort', relay, {once: true});
        try {
            if (options.signal?.aborted) abort.abort();
            const response = await fetch(url, {...options, credentials: 'same-origin', cache: 'no-store', signal: abort.signal});
            if (!response.ok) throw new HttpError(response.status);
            const data = await response.json().catch(() => { throw new HttpError(502); });
            return {data, status: response.status};
        } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', relay); }
    }
    function login() { window.location.assign('/login?next=' + encodeURIComponent(window.location.pathname + window.location.search)); }
    function clearDialog() { if ($('cr-dialog').open) $('cr-dialog').close(); $('cr-name').value = ''; $('cr-parent').replaceChildren(); }
    function invalidate(reason, text) {
        ++s.epoch; ++s.selection; mutation?.abort(); s.actor = null; s.ready = false; s.orgs = []; s.clinics = []; s.pending = null; s.confirmed = null; s.journal = null; s.busy = false;
        clearDialog(); root.inert = false; root.style.visibility = ''; $('cr-dialog').inert = false; $('cr-dialog').style.visibility = '';
        $('cr-create-org').hidden = true; $('cr-recovery').hidden = true; $('cr-content').replaceChildren();
        notice(text || (reason === 'logout' ? 'You have signed out.' : 'Your account or access changed. Load organisations again to continue.'), 'warning', ['Load organisations', initialise]);
    }
    function denied(error) {
        if (error.status === 401) { session?.authenticationLost(); invalidate('reauth', 'Your session has ended. Sign in to continue.'); login(); return true; }
        if ([403, 404].includes(error.status)) { invalidate('denied', error.status === 404 ? 'This organisation is unavailable. Choose from your authorised organisations.' : 'You no longer have access to manage organisations and clinics.'); return true; }
        return false;
    }
    if (!session) { notice('Clinic recovery tools could not load. Reload the page before creating a record.', 'error', ['Reload', () => location.reload()]); return; }
    session.onInvalidation(reason => { if (!s.observing) invalidate(reason); });
    function storageProblem(error) { s.storageError = error.message || 'Browser recovery storage is unavailable.'; notice(s.storageError + ' Creation is paused; no new request will be sent.', 'error', ['Check again', initialise]); }
    function renderRecovery() {
        const panel = $('cr-recovery'); panel.hidden = !(s.pending || s.recovery || s.confirmed);
        $('cr-resume').hidden = !s.pending || !!s.confirmed || s.pending.state === 'blocked' || (s.pending.kind === 'clinic' && !s.orgs.some(o => o.id === s.pending.organisation_id));
        $('cr-separate').hidden = !(s.pending || s.recovery) || !!s.confirmed;
        $('cr-cleanup').hidden = !s.confirmed;
        $('cr-recovery-text').textContent = s.confirmed ? 'The record was saved, but browser recovery storage could not be cleared. Clear it before starting another creation.' : 'An earlier creation cannot be confirmed. Review this list before retrying the same request or explicitly starting a separate creation. The earlier request may already have created a record.';
        $('cr-resume').disabled = !!s.storageError || !s.ready || (s.pending?.kind === 'clinic' && (s.loading || !s.loaded)); $('cr-separate').disabled = !!s.storageError || !s.ready;
    }
    function render() {
        const content = $('cr-content'); content.replaceChildren();
        const createOrg = $('cr-create-org'); createOrg.hidden = !s.ready; createOrg.disabled = !!s.storageError || !!s.pending || s.recovery || !!s.confirmed || s.busy;
        renderRecovery();
        if (!s.ready) return;
        if (!s.orgs.length) {
            const card = el('section', null, {class: 'card cr-empty'}); card.append(el('h2', 'No organisations yet'), el('p', 'Create an organisation, then add its clinics.'));
            const first = button('Create your first organisation', e => openForm('organisation', e.currentTarget), false); first.id = 'cr-first-org'; first.disabled = createOrg.disabled; card.append(first); content.append(card); return;
        }
        const context = el('section', null, {class: 'card', 'aria-label': 'Organisation context'}), label = el('label', 'Organisation', {for: 'cr-organisation'});
        const select = el('select', null, {id: 'cr-organisation', class: 'cr-select'}); select.disabled = !!s.pending || s.busy || !!s.confirmed;
        select.append(el('option', 'Choose an organisation', {value: ''}));
        for (const org of s.orgs) { const option = el('option', ref(org.id, s.orgs) + ' · ' + org.display_name, {value: org.id}); option.selected = org.id === s.selected; select.append(option); }
        select.addEventListener('change', () => { s.selected = select.value || null; updateUrl(); loadClinics(false).then(() => $('cr-organisation')?.focus()); });
        context.append(label, select);
        const org = s.orgs.find(o => o.id === s.selected);
        if (org) context.append(el('h2', org.display_name, {class: 'cr-org-heading'}), el('div', 'Organisation reference ' + ref(org.id, s.orgs), {class: 'cr-ref'}));
        else context.append(el('p', 'Choose an organisation to view its clinics.', {class: 'cr-help'}));
        if (s.admin) context.append(el('span', 'Platform access', {class: 'cr-badge'})); content.append(context);
        if (!org) return;
        const card = el('section', null, {class: 'card', 'aria-labelledby': 'cr-clinics-title'}), head = el('div', null, {class: 'cr-section-head'}), heading = el('div', null);
        heading.append(el('h2', 'Clinics', {id: 'cr-clinics-title'}), el('p', s.loading ? 'Loading clinics…' : !s.loaded ? 'Clinic list unavailable' : `${s.clinics.length} ${s.clinics.length === 1 ? 'clinic' : 'clinics'} in this organisation`));
        const create = button('Create clinic', e => openForm('clinic', e.currentTarget), false); create.id = 'cr-create-clinic'; create.disabled = createOrg.disabled || s.loading || !s.loaded;
        head.append(heading, create); card.append(head);
        if (s.loading && !s.loaded) card.append(el('p', 'Loading clinics…', {role: 'status', 'aria-busy': 'true'}));
        else if (!s.loaded) card.append(button('Retry loading clinics', () => loadClinics(false)));
        else if (!s.clinics.length) { const empty = el('div', null, {class: 'cr-empty'}); empty.append(el('h3', 'No clinics in this organisation'), el('p', 'Add your first clinic to keep its administrative details together.')); card.append(empty); }
        else {
            const grid = el('div', null, {class: 'cr-grid'});
            for (const clinic of s.clinics) {
                const entry = el('article', null, {class: 'cr-clinic', 'data-clinic': clinic.id}), details = el('details', null);
                entry.append(el('h3', clinic.display_name, {id: 'cr-record-' + clinic.id, tabindex: '-1'}), el('div', 'Clinic reference ' + ref(clinic.id, s.clinics), {class: 'cr-ref'}));
                entry.append(el('a', 'Manage members', {href: '/app/clinics/' + encodeURIComponent(clinic.id) + '/members', class: 'btn secondary'}));
                details.append(el('summary', 'Record details'), el('span', 'Read-only clinic reference'), el('code', clinic.id), el('p', 'Organisation: ' + org.display_name + ' · ' + ref(org.id, s.orgs), {class: 'cr-help'})); entry.append(details); grid.append(entry);
            }
            card.append(grid);
        }
        card.append(el('p', 'This page manages clinic registration. Patient records and clinical permissions stay in their existing workspace.', {class: 'cr-help'})); content.append(card);
    }
    function updateUrl() { const url = new URL(location.href); if (s.selected) url.searchParams.set('organisation', s.selected); else url.searchParams.delete('organisation'); history.replaceState(null, '', url.pathname + url.search); }
    async function loadClinics(retain, quiet = false) {
        const epoch = s.epoch, ticket = ++s.selection, selected = s.selected; s.loading = true;
        if (!retain) { s.clinics = []; s.loaded = false; } if (!quiet) notice(); render();
        if (!selected) { s.loading = false; render(); return; }
        try {
            const result = await request(`/api/organisations/${selected}/clinics`);
            if (epoch !== s.epoch || ticket !== s.selection || selected !== s.selected) return;
            s.clinics = rows(result.data, selected); s.loaded = true;
        } catch (error) {
            if (epoch !== s.epoch || ticket !== s.selection || selected !== s.selected) return;
            if (denied(error)) return;
            notice(retain ? 'We couldn’t refresh the list. Previously loaded and confirmed records are kept below.' : 'We couldn’t load clinics for this organisation.', 'warning', ['Refresh list', () => loadClinics(true)]);
        } finally { if (epoch === s.epoch && ticket === s.selection) { s.loading = false; render(); } }
    }
    async function identity() { const value = (await request('/auth/me')).data?.user; if (!value || !UUID.test(value.id || '')) throw new HttpError(502); return value; }
    async function initialise() {
        s.initializing = true;
        const epoch = ++s.epoch; ++s.selection; mutation?.abort(); clearDialog(); s.ready = false; s.orgs = []; s.clinics = []; s.pending = null; s.confirmed = null; s.recovery = false; s.storageError = ''; s.busy = false;
        $('cr-create-org').hidden = true; $('cr-recovery').hidden = true; $('cr-content').replaceChildren(el('div', 'Loading organisations…', {class: 'card', role: 'status'})); notice();
        try {
            const user = await identity(); if (epoch !== s.epoch) return;
            s.observing = true; try { session.observeActor(user.id); } catch (error) { s.storageError = error.message; } finally { s.observing = false; }
            s.actor = user.id; s.journal = new session.Journal(user.id);
            const access = (await request('/api/organisations/access')).data;
            if (access.can_manage !== true) throw new HttpError(403);
            const result = await request('/api/organisations'); if (epoch !== s.epoch) return;
            s.admin = access.is_platform_admin === true; s.orgs = rows(result.data);
            if (!s.storageError) { try { s.journal.checkStorage(); s.pending = s.journal.load(); s.recovery = s.journal.recoveryNeeded(); } catch (error) { if (error instanceof session.RecoveryRequired) s.recovery = true; else s.storageError = error.message; } }
            const wanted = new URL(location.href).searchParams.get('organisation');
            const pendingParent = s.pending?.kind === 'clinic' ? s.pending.organisation_id : null;
            s.selected = pendingParent && s.orgs.some(o => o.id === pendingParent) ? pendingParent : wanted && s.orgs.some(o => o.id === wanted) ? wanted : !wanted && s.orgs.length === 1 ? s.orgs[0].id : null;
            s.ready = true; render(); document.getElementById('account-clinic-registry-link')?.removeAttribute('hidden'); if (s.storageError) storageProblem(new Error(s.storageError));
            if (wanted && !s.orgs.some(o => o.id === wanted)) notice('This organisation is unavailable. Choose from your authorised organisations.', 'warning');
            await loadClinics(false, true);
        } catch (error) { if (epoch !== s.epoch) return; if (!denied(error)) notice('We couldn’t load organisations. Try loading them again.', 'error', ['Retry', initialise]); }
        finally { if (epoch === s.epoch) s.initializing = false; }
    }
    function dialogBusy(value) { s.busy = value; $('cr-form').setAttribute('aria-busy', String(value)); $('cr-save').disabled = value || !!s.storageError || s.pending?.state === 'blocked'; $('cr-cancel').disabled = value; $('cr-close').disabled = value; $('cr-name').readOnly = value || !!s.pending; $('cr-save-status').hidden = !value; $('cr-save-status').textContent = value ? 'Checking and saving…' : ''; }
    function formError(text) { $('cr-save-error').textContent = text; $('cr-save-error').hidden = false; $('cr-save-error').focus(); }
    function openForm(kind, opener) {
        if (!s.ready || s.busy || s.storageError || s.confirmed) return;
        if (s.pending && s.pending.kind !== kind) return;
        if (kind === 'clinic' && (!s.selected || !s.loaded || (s.pending && s.pending.organisation_id !== s.selected))) return;
        s.kind = kind; s.opener = opener; $('cr-name').value = s.pending?.payload.display_name || ''; $('cr-name').removeAttribute('aria-invalid'); $('cr-field-error').hidden = true; $('cr-save-error').hidden = true;
        $('cr-dialog-title').textContent = 'Create ' + kind; $('cr-name-label').textContent = kind === 'clinic' ? 'Clinic name' : 'Organisation name';
        $('cr-dialog-description').textContent = kind === 'clinic' ? 'Add a clinic to this organisation.' : 'Create an organisation, then add its clinics.';
        $('cr-parent').replaceChildren(); $('cr-parent').hidden = kind !== 'clinic';
        if (kind === 'clinic') { const org = s.orgs.find(o => o.id === s.selected); $('cr-parent').append(el('strong', org.display_name), el('div', 'Organisation reference ' + ref(org.id, s.orgs), {class: 'cr-ref'})); }
        $('cr-save').textContent = s.pending ? 'Retry confirmation' : 'Create ' + kind; $('cr-cancel').textContent = s.pending ? 'Close for now' : 'Cancel'; dialogBusy(false);
        $('cr-dialog').showModal(); if (s.pending) { formError(s.pending.state === 'blocked' ? 'This earlier submission needs review. Close the form and review the list before starting a separate creation.' : 'We couldn’t confirm whether it was saved. Retry to check the outcome using the same details.'); } else $('cr-name').focus();
    }
    function closeForm() { if (s.busy) return; clearDialog(); render(); const target = s.opener?.isConnected ? s.opener : $('cr-create-clinic') || $('cr-create-org'); target?.focus(); }
    async function preflight(epoch, actor, kind, parent) {
        const current = await identity(); if (epoch !== s.epoch) throw new Error('Session changed');
        if (current.id !== actor) { try { session.observeActor(current.id); } finally { invalidate('identity'); } throw new Error('Session changed'); }
        await request(kind === 'clinic' ? `/api/organisations/${parent}/clinics` : '/api/organisations/access');
        if (epoch !== s.epoch) throw new Error('Session changed');
    }
    $('cr-form').addEventListener('submit', async event => {
        event.preventDefault(); if (s.busy || !s.ready || s.storageError || s.confirmed || s.pending?.state === 'blocked') return;
        const name = $('cr-name').value.trim(); $('cr-save-error').hidden = true; $('cr-field-error').hidden = true;
        if (!name || Array.from(name).length > 255 || /[\x00-\x1f]/.test(name)) { $('cr-field-error').textContent = 'Enter a name using 1–255 characters, without control characters.'; $('cr-field-error').hidden = false; $('cr-name').setAttribute('aria-invalid', 'true'); $('cr-name').focus(); return; }
        const epoch = s.epoch, actor = s.actor, parent = s.kind === 'clinic' ? s.selected : null, kind = s.kind, hadPrior = !!s.pending; let attempted = false, intent, savedId;
        dialogBusy(true); mutation = new AbortController();
        try {
            await preflight(epoch, actor, kind, parent);
            intent = s.pending || s.journal.begin(kind, parent, name);
            const saved = s.journal.load();
            if (!saved || saved.key !== intent.key || saved.kind !== kind || saved.organisation_id !== parent || saved.payload.display_name !== intent.payload.display_name) throw new session.RecoveryRequired('The saved request changed. Review the list before continuing.');
            intent = s.journal.unknown(intent.key); s.pending = intent; attempted = true;
            const result = await request(kind === 'clinic' ? `/api/organisations/${parent}/clinics` : '/api/organisations', {method: 'POST', signal: mutation.signal, headers: {'Content-Type': 'application/json', 'Idempotency-Key': intent.key}, body: JSON.stringify(intent.payload)});
            if (epoch !== s.epoch) return;
            if (![200, 201].includes(result.status) || !row(result.data, kind === 'clinic' ? parent : undefined) || result.data.display_name !== intent.payload.display_name) throw new HttpError(502);
            const value = result.data; savedId = value.id; ++s.selection;
            if (kind === 'organisation') { s.orgs = [...s.orgs.filter(o => o.id !== value.id), value]; s.selected = value.id; s.clinics = []; s.loaded = false; updateUrl(); }
            else s.clinics = [...s.clinics.filter(c => c.id !== value.id), value];
            s.confirmed = {key: intent.key, row: value};
            try { s.journal.finish(intent.key); s.pending = null; s.confirmed = null; } catch (error) { s.storageError = error.message; }
            clearDialog(); render(); notice((kind === 'clinic' ? 'Clinic' : 'Organisation') + (result.status === 200 ? ' saved.' : ' created.'), 'success');
            if (kind === 'clinic') $('cr-record-' + value.id)?.focus(); else $('cr-organisation')?.focus();
            if (!s.confirmed) await loadClinics(true, true);
        } catch (error) {
            if (epoch !== s.epoch) return;
            if (denied(error)) return;
            if (error instanceof session.StorageFailure) { storageProblem(error); formError('Browser recovery storage is unavailable. No further request will be sent until it is working.'); }
            else if (error.status === 422 && attempted && !hadPrior) {
                try { s.journal.finish(intent.key); s.pending = null; $('cr-name').readOnly = false; $('cr-field-error').textContent = 'This name was not accepted. Check it and try again.'; $('cr-field-error').hidden = false; $('cr-name').setAttribute('aria-invalid', 'true'); $('cr-name').focus(); } catch (storage) { storageProblem(storage); formError('The request was rejected, but browser recovery storage could not be cleared.'); }
            } else {
                if (attempted && [409, 422].includes(error.status)) { try { s.pending = s.journal.block(intent.key); } catch (storage) { storageProblem(storage); } }
                formError(attempted ? ([409, 422].includes(error.status) ? 'This submission conflicts with an earlier request or cannot be retried. Close the form and review the list before starting a separate creation.' : 'We couldn’t confirm whether it was saved. Your details are kept. Retry to check the outcome.') : 'We could not verify your access or recovery record. No creation request was sent in this attempt. Try loading organisations again.');
            }
        } finally { if (epoch === s.epoch) { dialogBusy(false); $('cr-save').textContent = s.pending ? 'Retry confirmation' : 'Create ' + kind; $('cr-cancel').textContent = s.pending ? 'Close for now' : 'Cancel'; render(); if (savedId) (kind === 'clinic' ? $('cr-record-' + savedId) : $('cr-organisation'))?.focus(); } }
    });
    $('cr-create-org').addEventListener('click', event => openForm('organisation', event.currentTarget));
    $('cr-resume').addEventListener('click', event => openForm(s.pending.kind, event.currentTarget));
    $('cr-separate').addEventListener('click', () => { if (!s.ready || s.busy) return; try { s.journal.abandon(); s.pending = null; s.recovery = false; render(); notice('You can start a separate creation. The earlier request may already have created a record.', 'warning'); } catch (error) { storageProblem(error); } });
    $('cr-cleanup').addEventListener('click', () => { try { s.journal.finish(s.confirmed.key); s.journal.acknowledge(); s.pending = null; s.confirmed = null; s.recovery = false; s.storageError = ''; render(); notice('Recovery storage cleared. The saved record is retained.', 'success'); } catch (error) { storageProblem(error); } });
    $('cr-cancel').addEventListener('click', closeForm); $('cr-close').addEventListener('click', closeForm);
    $('cr-dialog').addEventListener('cancel', event => { event.preventDefault(); closeForm(); });
    $('cr-dialog').addEventListener('keydown', event => { if (event.key !== 'Tab') return; const focus = [...$('cr-dialog').querySelectorAll('button:not(:disabled),input:not(:disabled)')], first = focus[0], last = focus.at(-1); if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } });
    $('cr-name').addEventListener('input', () => { if (!s.pending) { $('cr-field-error').hidden = true; $('cr-name').removeAttribute('aria-invalid'); } });
    async function revalidate() {
        if (!s.ready || s.initializing) return; const epoch = s.epoch, actor = s.actor, focus = document.activeElement;
        root.style.visibility = 'hidden'; root.inert = true; $('cr-dialog').style.visibility = 'hidden'; $('cr-dialog').inert = true;
        try { await preflight(epoch, actor, 'organisation', null); if (epoch === s.epoch && s.selected) await loadClinics(true, true); }
        catch (error) { if (epoch === s.epoch && !denied(error)) invalidate('reauth', 'We could not recheck your account. Load organisations again before continuing.'); }
        finally { if (epoch === s.epoch) { root.style.visibility = ''; root.inert = false; $('cr-dialog').style.visibility = ''; $('cr-dialog').inert = false; if (focus?.isConnected) focus.focus(); } }
    }
    window.addEventListener('pageshow', event => { if (event.persisted) revalidate(); });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') revalidate(); });
    initialise();
})();
