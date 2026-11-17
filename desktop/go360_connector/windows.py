"""Windows-only protection and read-only capture primitives."""
import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import threading


class Blob(ctypes.Structure):
    _fields_ = [('size', W.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


class DPAPI:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('Windows 10/11 is required.')
        self.crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p
        for name in ('CryptProtectData', 'CryptUnprotectData'):
            fn = getattr(self.crypt, name)
            fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, W.DWORD, ctypes.POINTER(Blob)]
            fn.restype = W.BOOL

    def _run(self, raw, decrypt):
        buffer = ctypes.create_string_buffer(raw)
        source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = Blob()
        fn = self.crypt.CryptUnprotectData if decrypt else self.crypt.CryptProtectData
        if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(target.data, target.size)
        finally:
            self.kernel.LocalFree(target.data)

    def encrypt(self, raw):
        return self._run(raw, False)

    def decrypt(self, raw):
        return self._run(raw, True)


def private_directory(path):
    path = Path(path).resolve()
    if path.exists() and path.is_symlink():
        raise RuntimeError('Choose a local private data directory.')
    path.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    # Use SID rather than localised account/group names.
    sid_output = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], creationflags=flags).decode(errors='replace')
    import csv
    sid = next(csv.reader([sid_output.strip()]))[1]
    subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', '*'+sid+':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    return path


def kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    api.CreateFileW.restype = W.HANDLE
    api.CloseHandle.argtypes = [W.HANDLE]
    api.ReadFile.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
    return api


def locked_read(path, limit=8*1024*1024):
    """FILE_SHARE_READ permits readers but excludes active/new writers."""
    api = kernel()
    handle = api.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError('Source unavailable or still being written.')
    try:
        buffer = ctypes.create_string_buffer(limit + 1)
        count = W.DWORD()
        if not api.ReadFile(handle, buffer, limit + 1, ctypes.byref(count), None):
            raise OSError('Could not read source.')
        if count.value > limit:
            raise ValueError('Source exceeds 8 MiB.')
        return buffer.raw[:count.value]
    finally:
        api.CloseHandle(handle)


class DirectoryNotifications:
    def __init__(self, folder, changed):
        self.api = kernel()
        self.changed = changed
        self.closed = threading.Event()
        self.handle = self.api.CreateFileW(str(folder), 1, 7, None, 3, 0x02000000, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise OSError('Folder notifications unavailable.')
        self.api.ReadDirectoryChangesW.argtypes = [W.HANDLE, ctypes.c_void_p, W.DWORD, W.BOOL,
            W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p, ctypes.c_void_p]
        self.api.CancelIoEx.argtypes = [W.HANDLE, ctypes.c_void_p]
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        buffer = ctypes.create_string_buffer(32768)
        count = W.DWORD()
        try:
            while not self.closed.is_set():
                ok = self.api.ReadDirectoryChangesW(self.handle, buffer, len(buffer), False,
                     0x1 | 0x8 | 0x10, ctypes.byref(count), None, None)
                self.changed.set()  # Includes overflow/error: reconcile full target.
                if not ok:
                    break
        finally:
            self.api.CloseHandle(self.handle)

    def close(self):
        self.closed.set()
        self.api.CancelIoEx(self.handle, None)
        self.thread.join(timeout=2)
