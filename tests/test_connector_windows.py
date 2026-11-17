"""Native Windows checks using synthetic data and private temporary paths."""
import os
from pathlib import Path
import sys
import threading
import time
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'desktop'))
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows native APIs')


def test_dpapi_real_queue_restart_and_no_plaintext(tmp_path):
    from go360_connector.windows import DPAPI
    from go360_connector.core import Queue
    secret=b'<html>synthetic private assessment</html>'
    protection=DPAPI()
    cipher=protection.encrypt(secret)
    assert cipher!=secret and secret not in cipher
    assert protection.decrypt(cipher)==secret
    q=Queue(tmp_path/'queue.sqlite3',protection)
    q.capture('https://dev.invalid','synthetic-account','source',secret)
    recovered=Queue(q.path,DPAPI())
    row=recovered.jobs('https://dev.invalid','synthetic-account')[0]
    assert recovered.protection.decrypt(row['payload'])==secret
    for path in tmp_path.iterdir():
        if path.is_file():assert secret not in path.read_bytes()


def test_native_notifications_and_writer_lock(tmp_path):
    import ctypes
    from go360_connector.windows import kernel,locked_read,DirectoryNotifications
    source=tmp_path/'ResultOne.htm';source.write_bytes(b'<html>synthetic</html>')
    assert locked_read(source)==source.read_bytes()
    api=kernel()
    writer=api.CreateFileW(str(source),0x40000000,1,None,3,0x80,None)
    assert writer!=ctypes.c_void_p(-1).value
    try:
        with pytest.raises(OSError):locked_read(source)
    finally:api.CloseHandle(writer)
    changed=threading.Event();watcher=DirectoryNotifications(tmp_path,changed)
    try:
        time.sleep(0.1);source.write_bytes(b'<html>new synthetic</html>')
        assert changed.wait(5)
    finally:watcher.close()


def test_native_tray_add_remove():
    import tkinter as tk
    from go360_connector.tray import Tray
    window=tk.Tk();window.withdraw();window.update()
    try:
        try:tray=Tray(window,lambda:None,tk.Menu(window))
        except OSError:pytest.skip('Notification area access denied/unavailable in this execution session; manual Windows check remains required')
        window.update();tray.close()
    finally:window.destroy()
