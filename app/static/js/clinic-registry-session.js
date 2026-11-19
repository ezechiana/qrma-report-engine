/* Registry-only recovery metadata. Cached identity never authorises a request. */
(function (global) {
    'use strict';
    const PREFIX = 'go360.registry.intent.v1:';
    const LOST = 'go360.registry.lost.v1:';
    const SIGNAL = 'go360.registry.session-event';
    const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    const listeners = new Set(), seen = new Set();
    let actor = null, channel = null;
    class StorageFailure extends Error {}
    class RecoveryRequired extends Error {}
    function uuid() {
        if (global.crypto?.randomUUID) return global.crypto.randomUUID();
        const bytes = global.crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
        const hex = Array.from(bytes, n => n.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
    }
    function store() { try { return global.sessionStorage; } catch (_) { throw new StorageFailure('Browser recovery storage is unavailable.'); } }
    function read(key) { try { return store().getItem(key); } catch (_) { throw new StorageFailure('Browser recovery storage cannot be read.'); } }
    function write(key, value) { try { store().setItem(key, value); if (read(key) !== value) throw new Error(); } catch (_) { throw new StorageFailure('Browser recovery storage could not save your request. Nothing was sent.'); } }
    function remove(key) { try { store().removeItem(key); if (read(key) !== null) throw new Error(); } catch (_) { throw new StorageFailure('Browser recovery storage could not be cleared.'); } }
    function keys() { try { return Array.from({length: store().length}, (_, i) => store().key(i)).filter(Boolean); } catch (_) { throw new StorageFailure('Browser recovery storage is unavailable.'); } }
    function probe() { const key = PREFIX + 'probe'; write(key, '1'); remove(key); }
    function purge(except) {
        for (const key of keys()) {
            if (!key.startsWith(PREFIX) || key === PREFIX + except) continue;
            const previous = key.slice(PREFIX.length);
            // Retain only a payload-free warning for that actor after key loss.
            if (UUID.test(previous)) write(LOST + previous, '1');
            remove(key);
        }
    }
    function notify(kind) { for (const fn of listeners) { try { fn(kind); } catch (_) {} } }
    function accept(message) {
        if (!message || !['logout', 'identity'].includes(message.kind) || !UUID.test(message.nonce) || seen.has(message.nonce)) return;
        seen.add(message.nonce); if (seen.size > 100) seen.delete(seen.values().next().value);
        try { purge(null); } catch (_) {} // UI still clears and future storage operations fail closed.
        actor = null; notify(message.kind);
    }
    function signal(kind) {
        const message = {kind, nonce: uuid()};
        accept(message);
        try { channel?.postMessage(message); } catch (_) {}
        try { global.localStorage.setItem(SIGNAL, JSON.stringify(message)); global.localStorage.removeItem(SIGNAL); } catch (_) {}
    }
    try { channel = new BroadcastChannel(SIGNAL); channel.onmessage = e => accept(e.data); } catch (_) {}
    global.addEventListener('storage', e => {
        if (e.key === SIGNAL && e.newValue) { try { accept(JSON.parse(e.newValue)); } catch (_) {} }
        if (e.key === 'current_user') {
            if (!e.newValue) { notify('reauth'); return; }
            try { const id = JSON.parse(e.newValue)?.id; if (actor && id !== actor) signal('identity'); } catch (_) { notify('reauth'); }
        }
    });
    function observeActor(id) {
        if (!UUID.test(id || '')) throw new Error('A fresh authenticated identity is required.');
        if (actor && actor !== id) signal('identity');
        purge(id); actor = id;
    }
    function valid(value, id) {
        if (!value || Object.keys(value).sort().join(',') !== 'actor_id,created_at,key,kind,organisation_id,payload,state,version') return false;
        return value.version === 1 && value.actor_id === id && UUID.test(value.key) &&
            ['organisation', 'clinic'].includes(value.kind) && ['pending', 'unknown', 'blocked'].includes(value.state) &&
            (value.kind === 'clinic' ? UUID.test(value.organisation_id || '') : value.organisation_id === null) &&
            typeof value.created_at === 'string' && Number.isFinite(Date.parse(value.created_at)) &&
            value.payload && Object.keys(value.payload).join(',') === 'display_name' &&
            typeof value.payload.display_name === 'string' && value.payload.display_name === value.payload.display_name.trim() &&
            Array.from(value.payload.display_name).length > 0 && Array.from(value.payload.display_name).length <= 255 &&
            !/[\x00-\x1f]/.test(value.payload.display_name);
    }
    class Journal {
        constructor(id) { if (!UUID.test(id || '')) throw new Error('Fresh identity required'); this.actor = id; this.key = PREFIX + id; }
        checkStorage() { probe(); }
        load() {
            const raw = read(this.key); if (raw === null) return null;
            let value; try { value = JSON.parse(raw); } catch (_) {}
            if (!valid(value, this.actor)) { write(LOST + this.actor, '1'); remove(this.key); throw new RecoveryRequired('An earlier creation cannot be confirmed. Review the list before starting a separate creation.'); }
            return value;
        }
        recoveryNeeded() { return read(LOST + this.actor) !== null; }
        acknowledge() { remove(LOST + this.actor); }
        begin(kind, parent, name) {
            probe();
            if (this.load()) throw new RecoveryRequired('Resume the existing creation before starting another.');
            if (this.recoveryNeeded()) throw new RecoveryRequired('Review the list before starting a separate creation.');
            const value = {version: 1, actor_id: this.actor, kind, organisation_id: parent,
                key: uuid(), payload: {display_name: name.trim()}, state: 'pending', created_at: new Date().toISOString()};
            if (!valid(value, this.actor)) throw new Error('Enter a valid name and organisation.');
            write(this.key, JSON.stringify(value)); return value;
        }
        unknown(key) { const value = this.load(); if (!value || value.key !== key) throw new RecoveryRequired('The saved request changed. Nothing else was sent.'); value.state = 'unknown'; write(this.key, JSON.stringify(value)); return value; }
        block(key) { const value = this.unknown(key); value.state = 'blocked'; write(this.key, JSON.stringify(value)); return value; }
        finish(key) { const value = this.load(); if (value && value.key !== key) throw new RecoveryRequired('The saved request changed.'); remove(this.key); }
        abandon() { remove(this.key); this.acknowledge(); }
    }
    global.GO360ClinicSession = {Journal, StorageFailure, RecoveryRequired, observeActor,
        logout: () => signal('logout'), authenticationLost: () => notify('reauth'),
        onInvalidation: fn => { listeners.add(fn); return () => listeners.delete(fn); }};
})(window);
