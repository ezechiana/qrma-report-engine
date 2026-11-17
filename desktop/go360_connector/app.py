"""Standard-library Windows tray prototype; no OEM writes or patient logging."""
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import queue as messages
import secrets
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import uuid
import webbrowser

from .core import Queue, DEFAULT_FOLDER, PROFILE, stable_capture, validate_folder, envelope, unpack
from .windows import DPAPI, private_directory, locked_read, DirectoryNotifications
from .transfer import Client, TransferError, transfer


class Application:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('GO360 Desktop Connector — DEV setup')
        self.root.geometry('800x620')
        self.root.withdraw() if '--background' in sys.argv else None
        self.protection = DPAPI()
        self.folder = private_directory(Path(os.environ['LOCALAPPDATA'])/'GO360'/'Connector-DEV')
        import msvcrt
        self.instance = (self.folder/'instance.lock').open('a+b')
        self.instance.seek(0)
        self.instance.write(b'0'); self.instance.flush(); self.instance.seek(0)
        try:
            msvcrt.locking(self.instance.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            messagebox.showinfo('GO360','The connector is already running. Open it from the system tray.')
            self.root.destroy(); raise SystemExit(0)
        self.statefile = self.folder/'settings.bin'
        self.lock = threading.RLock()
        self.events = messages.Queue()
        self.stopped, self.changed = threading.Event(), threading.Event()
        self.auth_suspended = False
        if self.statefile.exists():
            self.config = unpack(self.protection,self.statefile.read_bytes())
        else:
            self.config = dict(connector_id=str(uuid.uuid4()),origin='',folder=DEFAULT_FOLDER,
                               profile=PROFILE,monitoring=False,session=None,last_capture=None,last_upload=None)
            self.save()
        self.queue = Queue(self.folder/'queue.sqlite3',self.protection)
        self.build_ui()
        from .tray import Tray
        menu = tk.Menu(self.root,tearoff=False)
        menu.add_command(label='Open GO360 Connector',command=self.show)
        menu.add_command(label='Open in GO360',command=self.open_portal)
        menu.add_separator(); menu.add_command(label='Exit',command=self.exit)
        try:self.tray = Tray(self.root,self.show,menu)
        except OSError:
            self.tray=None
            self.root.deiconify()
            self.status.set('System tray is unavailable in this environment. Keep this window open.')
        self.root.protocol('WM_DELETE_WINDOW',self.root.withdraw if self.tray else self.exit)
        threading.Thread(target=self.capture_loop,daemon=True).start()
        threading.Thread(target=self.transfer_loop,daemon=True).start()
        self.root.after(500,self.tick)

    def save(self):
        with self.lock:
            temporary = self.statefile.with_suffix('.new')
            with temporary.open('wb') as stream:
                stream.write(envelope(self.protection,self.config));stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.statefile)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.config)

    def build_ui(self):
        frame = ttk.Frame(self.root,padding=16);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='GO360 Desktop Connector',font=('Segoe UI',18)).pack(anchor='w')
        ttk.Label(frame,text='DEV prototype · Set up sign-in, validate the source, then start monitoring.').pack(anchor='w',pady=8)
        self.status = tk.StringVar(value='Ready for setup')
        ttk.Label(frame,textvariable=self.status,wraplength=750).pack(anchor='w',pady=8)
        self.origin = tk.StringVar(value=self.config['origin'])
        ttk.Label(frame,text='1. HTTPS DEV server address').pack(anchor='w')
        ttk.Entry(frame,textvariable=self.origin,width=75).pack(fill='x')
        auth = ttk.Frame(frame);auth.pack(anchor='w',pady=8)
        ttk.Button(auth,text='Sign in with browser',command=self.signin).pack(side='left')
        ttk.Button(auth,text='Sign out',command=self.signout).pack(side='left',padx=8)
        self.pair_code = tk.StringVar()
        ttk.Label(frame,textvariable=self.pair_code).pack(anchor='w')
        ttk.Label(frame,text='2. Validated profile: Go360 Wellness Intelligence').pack(anchor='w',pady=(12,0))
        self.source = tk.StringVar(value=self.config['folder'])
        ttk.Entry(frame,textvariable=self.source,width=75).pack(fill='x')
        row = ttk.Frame(frame);row.pack(anchor='w',pady=8)
        ttk.Button(row,text='Choose folder',command=self.choose).pack(side='left')
        ttk.Button(row,text='Validate folder',command=self.validate).pack(side='left',padx=8)
        ttk.Button(row,text='3. Start monitoring',command=self.start).pack(side='left')
        ttk.Button(row,text='Pause',command=self.pause).pack(side='left',padx=8)
        self.startup = tk.BooleanVar(value=self.startup_enabled())
        ttk.Checkbutton(frame,text='Launch at Windows sign-in',variable=self.startup,command=self.set_startup).pack(anchor='w')
        self.details = tk.StringVar()
        ttk.Label(frame,textvariable=self.details,justify='left',wraplength=750).pack(anchor='w',pady=10)
        self.jobs = ttk.Treeview(frame,columns=('state','time'),show='headings',height=6)
        self.jobs.heading('state',text='Job state');self.jobs.heading('time',text='Captured')
        self.jobs.pack(fill='both',expand=True)
        actions = ttk.Frame(frame);actions.pack(anchor='w',pady=8)
        ttk.Button(actions,text='Retry recoverable jobs',command=self.retry).pack(side='left')
        ttk.Button(actions,text='Open selected in GO360',command=self.open_portal).pack(side='left',padx=8)
        ttk.Button(actions,text='Diagnostics',command=self.diagnostics).pack(side='left')

    def show(self):
        self.root.deiconify();self.root.lift()

    def choose(self):
        path = filedialog.askdirectory(initialdir=self.source.get())
        if path:self.source.set(path)

    def validate(self):
        try:
            validate_folder(self.source.get())
            self.status.set('Folder structure validated. Only the GO360 profile is supported.')
            return True
        except (OSError,ValueError) as error:
            self.status.set(str(error));return False

    def start(self):
        if not self.config.get('session'):
            self.status.set('Sign in before enabling monitoring.');return
        if not self.validate():return
        with self.lock:
            self.config['folder'],self.config['monitoring'] = self.source.get(),True
            self.save()
        self.changed.set()

    def pause(self):
        with self.lock:self.config['monitoring']=False;self.save()
        self.changed.set()

    def signin(self):
        try:client=Client(self.origin.get().strip())
        except ValueError as error:self.status.set(str(error));return
        if getattr(self,'pairing',False):return
        self.pause();self.pairing=True
        self.status.set('Opening secure browser sign-in…')
        def pair():
            try:
                verifier=secrets.token_urlsafe(48)
                response=client.request('POST','/api/connector/pairings',data={
                    'connector_id':self.config['connector_id'],'challenge':hashlib.sha256(verifier.encode()).hexdigest()})
                # Never follow a server-supplied authentication URL to another host.
                expected=client.origin+'/app/connector/approve?pairing_id='+str(uuid.UUID(response['pairing_id']))
                if response['verification_uri']!=expected:raise TransferError(400)
                self.events.put(('code','Connector code: '+response['user_code']+' — enter it in the browser.'))
                webbrowser.open(expected)
                for _ in range(120):
                    if self.stopped.wait(5):return
                    try:
                        session=client.request('POST','/api/connector/pairings/token',data={'pairing_id':response['pairing_id'],'verifier':verifier})
                        session['connector_id']=self.config['connector_id']
                        session['account_id']=str(uuid.UUID(session['account_id']))
                        with self.lock:
                            self.config['session'],self.config['origin']=session,client.origin
                            self.config['monitoring']=False
                            self.auth_suspended=False;self.save()
                        self.events.put(('status','Signed in. Validate the folder and start monitoring.'));return
                    except TransferError as error:
                        if error.status not in (0,428,429):raise
                self.events.put(('status','Browser approval expired. Please sign in again.'))
            except Exception:
                self.events.put(('status','Sign-in could not complete. Check the HTTPS DEV server and try again.'))
            finally:self.pairing=False
        threading.Thread(target=pair,daemon=True).start()

    def signout(self):
        config=self.snapshot();session=config.get('session')
        self.pause()
        with self.lock:self.config['session']=None;self.save()
        self.auth_suspended=True
        def revoke():
            try:
                if session:Client(config['origin']).request('DELETE','/api/connector/session',token=session['access_token'])
                self.events.put(('status','Signed out. Queued assessments are retained for their original account.'))
            except Exception:
                self.events.put(('status','Signed out locally. Server revocation is unconfirmed; revoke the device in GO360 when online.'))
        threading.Thread(target=revoke,daemon=True).start()

    def capture_loop(self):
        watcher=None;watched=None
        try:
            while not self.stopped.is_set():
                config=self.snapshot();session=config.get('session')
                if config['monitoring'] and session:
                    try:
                        source=validate_folder(config['folder'],config['profile'])
                        if watched!=source:
                            if watcher:watcher.close()
                            try:watcher=DirectoryNotifications(source.parent,self.changed);watched=source
                            except OSError:watcher=None;watched=source  # Periodic capture remains active.
                        raw=stable_capture(source,locked_read)
                        job=self.queue.capture(config['origin'],session['account_id'],source,raw,config['profile'])
                        if job:
                            with self.lock:self.config['last_capture']=time.time();self.save()
                    except (OSError,ValueError):
                        self.events.put(('status','Source unavailable, changing or unsupported. Capture will retry.'))
                elif watcher:
                    watcher.close();watcher=None;watched=None
                self.changed.wait(5);self.changed.clear()
        except Exception:
            self.events.put(('status','Capture stopped. Check private storage and restart the connector.'))
        finally:
            if watcher:watcher.close()

    def transfer_loop(self):
        while not self.stopped.is_set():
            config=self.snapshot();session=config.get('session')
            try:
                if session and not self.auth_suspended:
                    connected=transfer(self.queue,Client(config['origin']),session)
                    jobs=self.queue.jobs(config['origin'],session['account_id'])
                    accepted=[job['accepted'] for job in jobs if job['accepted']]
                    if accepted:
                        with self.lock:self.config['last_upload']=max(accepted);self.save()
                    self.events.put(('connection','Connected to GO360' if connected else 'Transfer pending; temporary failure or job needs attention.'))
                self.queue.cleanup()
            except TransferError as error:
                if error.status in (401,403):
                    self.auth_suspended=True
                    self.events.put(('connection','Sign-in expired, revoked or access suspended. Capture is retained; sign in again.'))
                else:self.events.put(('connection','Offline or server unavailable; pending assessments are retained.'))
            except Exception:
                self.events.put(('connection','Storage or transfer needs attention. Restart after checking free disk space.'))
            self.stopped.wait(5)

    def retry(self):
        config=self.snapshot();session=config.get('session')
        if session:self.queue.retry(config['origin'],session['account_id'])
        self.status.set('Recoverable jobs scheduled for reconciliation and retry.')

    def open_portal(self):
        config=self.snapshot()
        if not config['origin']:return
        path='/app/cases/incoming'
        selected=self.jobs.selection()
        if selected and config.get('session'):
            for job in self.queue.jobs(config['origin'],config['session']['account_id']):
                if job['id']==selected[0]:
                    remote=self.queue.info(job).get('server')
                    if remote:path='/app/cases/new?incoming_assessment_id='+str(uuid.UUID(remote['assessment_id']))
        webbrowser.open(Client(config['origin']).origin+path)

    def tick(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='code':self.pair_code.set(value)
                elif kind=='connection':self.connection=value
                else:self.status.set(value)
        except messages.Empty:pass
        config=self.snapshot();session=config.get('session')
        jobs=self.queue.jobs(config['origin'],session['account_id']) if session else []
        pending=sum(j['accepted'] is None for j in jobs)
        failed=sum(j['state'] in ('needs_attention','failed_retryable') for j in jobs)
        def stamp(value):return datetime.fromtimestamp(value).strftime('%d %b %H:%M') if value else 'None'
        self.details.set(f"Account: {session['display_name'] if session else 'Signed out'}\n"
            f"Connection: {getattr(self,'connection','Not connected')} · Monitoring: {'enabled' if config['monitoring'] else 'paused'}\n"
            f"Profile: Go360 Wellness Intelligence\nFolder: {config['folder']}\n"
            f"Last capture: {stamp(config['last_capture'])} · Last upload: {stamp(config['last_upload'])}\nPending: {pending} · Needs attention / retrying: {failed}")
        selected=self.jobs.selection()
        self.jobs.delete(*self.jobs.get_children())
        for job in jobs[-100:]:self.jobs.insert('',0,iid=job['id'],values=(job['state'],stamp(job['created'])))
        if selected and self.jobs.exists(selected[0]):self.jobs.selection_set(selected[0])
        if not self.stopped.is_set():self.root.after(2000,self.tick)

    def diagnostics(self):
        config=self.snapshot()
        messagebox.showinfo('GO360 diagnostics',f"DEV prototype v0.4\nConnector ID: {config['connector_id']}\n"
            'No patient content or tokens are written to routine logs.\n'
            'Repeated saves faster than stable capture may overwrite an uncaptured source.\n'
            'Shared Windows logins are not a security boundary; use separate Windows accounts.')

    @staticmethod
    def startup_enabled():
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
                winreg.QueryValueEx(key,'GO360ConnectorDEV');return True
        except OSError:return False

    def set_startup(self):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
            if self.startup.get():
                exe=Path(sys.executable).with_name('pythonw.exe')
                launcher=Path(__file__).resolve().parents[1]/'launch.pyw'
                winreg.SetValueEx(key,'GO360ConnectorDEV',0,winreg.REG_SZ,f'"{exe}" "{launcher}" --background')
            else:
                try:winreg.DeleteValue(key,'GO360ConnectorDEV')
                except FileNotFoundError:pass

    def exit(self):
        self.stopped.set();self.changed.set()
        if self.tray:self.tray.close()
        self.root.destroy()

    def run(self):self.root.mainloop()


def main():
    if os.name!='nt':raise SystemExit('Windows 10/11 is required.')
    Application().run()
