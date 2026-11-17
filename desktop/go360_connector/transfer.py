"""HTTPS only; no redirects or credentials in URLs."""
import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from datetime import datetime, timezone
from .core import MAX_HTML


class TransferError(Exception):
    def __init__(self, status=0):
        self.status = status
        super().__init__('Authentication required' if status in (401,403) else 'Transfer could not complete')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, origin):
        url = urlsplit(origin)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            raise ValueError('Enter the HTTPS DEV server address, without a path.')
        self.origin = origin.rstrip('/')
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def request(self, method, path, *, token=None, data=None, headers=None):
        headers = dict(headers or {})
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        request = Request(self.origin+path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(1024*1024+1)
                if len(body) > 1024*1024:
                    raise TransferError()
                return json.loads(body)
        except HTTPError as exc:
            raise TransferError(exc.code) from None
        except (OSError, URLError, ValueError):
            raise TransferError() from None


def transfer(queue, client, session):
    """One serial pass. Every uncertain result is reconciled before upload."""
    connected = True
    for job in queue.jobs(client.origin, session['account_id']):
        if job['next_attempt'] > queue.clock():
            continue
        info = queue.info(job)
        if job['state'] == 'needs_attention' and not info.get('server'):
            continue  # Permanent validation/conflict: manual retry only.
        path = '/api/connector/jobs/'+job['id']
        try:
            try:
                remote = client.request('GET', path, token=session['access_token'])
            except TransferError as error:
                if error.status != 404:
                    raise
                if job['payload'] is None:
                    queue.patch(job, state='needs_attention', error='server_record_missing')
                    continue
                queue.patch(job, state='uploading')
                stamp = datetime.fromtimestamp(info['captured_at'], timezone.utc).isoformat()
                remote = client.request('POST', path, token=session['access_token'],
                    data=queue.protection.decrypt(job['payload']),
                    headers={'Content-Type':'text/html','X-Source-Profile':info['profile'],'X-Captured-At':stamp})
            if not isinstance(remote, dict) or remote.get('status') not in {'awaiting_practitioner','needs_attention','case_created','completed','processing','uploaded'}:
                raise TransferError()
            if remote.get('job_id') != job['id'] or remote.get('connector_id') != session['connector_id']:
                raise TransferError(409)
            queue.patch(job, state=remote['status'], server=remote)
        except TransferError as error:
            if error.status in (401,403):
                raise  # Suspend all transfers; do not rebind or discard jobs.
            connected = False
            permanent = error.status in (400,409,410,413,415,422)
            queue.patch(job, state='needs_attention' if permanent else 'failed_retryable',
                        error='validation_or_conflict' if permanent else 'temporarily_unavailable', retry=not permanent)

    return connected
