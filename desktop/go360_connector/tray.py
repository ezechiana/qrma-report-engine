"""Native Windows notification-area icon using the Tk window's message loop."""
import ctypes
from ctypes import wintypes as W


class GUID(ctypes.Structure):
    _fields_ = [('a',W.DWORD),('b',W.WORD),('c',W.WORD),('d',ctypes.c_ubyte*8)]


class IconData(ctypes.Structure):
    _fields_ = [('cbSize',W.DWORD),('hWnd',W.HWND),('uID',W.UINT),('uFlags',W.UINT),
                ('uCallbackMessage',W.UINT),('hIcon',W.HICON),('szTip',W.WCHAR*128),
                ('dwState',W.DWORD),('dwStateMask',W.DWORD),('szInfo',W.WCHAR*256),
                ('uVersion',W.UINT),('szInfoTitle',W.WCHAR*64),('dwInfoFlags',W.DWORD),
                ('guidItem',GUID),('hBalloonIcon',W.HICON)]


class Tray:
    def __init__(self, root, show, menu):
        root.update_idletasks()
        self.root, self.show, self.menu = root, show, menu
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.user.GetParent.argtypes = [W.HWND]
        self.user.GetParent.restype = W.HWND
        self.user.LoadIconW.argtypes = [W.HINSTANCE,ctypes.c_void_p]
        self.user.LoadIconW.restype = W.HICON
        self.hwnd = self.user.GetParent(root.winfo_id()) or root.winfo_id()
        self.callback = 0x8001
        self.taskbar = self.user.RegisterWindowMessageW('TaskbarCreated')
        self.proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        self.proc = self.proc_type(self.handle)
        self.user.SetWindowLongPtrW.argtypes = [W.HWND,ctypes.c_int,ctypes.c_void_p]
        self.user.SetWindowLongPtrW.restype = ctypes.c_void_p
        self.user.CallWindowProcW.argtypes = [ctypes.c_void_p,W.HWND,W.UINT,W.WPARAM,W.LPARAM]
        self.user.CallWindowProcW.restype = ctypes.c_ssize_t
        self.old = self.user.SetWindowLongPtrW(self.hwnd,-4,ctypes.cast(self.proc,ctypes.c_void_p))
        if not self.old:
            raise ctypes.WinError(ctypes.get_last_error())
        self.data = IconData()
        self.data.cbSize, self.data.hWnd, self.data.uID = ctypes.sizeof(IconData),self.hwnd,1
        self.data.uFlags, self.data.uCallbackMessage = 1|2|4,self.callback
        self.data.hIcon = self.user.LoadIconW(None,32512)
        self.data.szTip = 'GO360 Desktop Connector DEV'
        self.shell.Shell_NotifyIconW.argtypes = [W.DWORD,ctypes.POINTER(IconData)]
        try:self.add()
        except OSError:
            self.user.SetWindowLongPtrW(self.hwnd,-4,self.old)
            raise

    def add(self):
        if not self.shell.Shell_NotifyIconW(0,ctypes.byref(self.data)):
            raise OSError('System tray is unavailable.')

    def handle(self, hwnd, message, wparam, lparam):
        if message == self.taskbar:
            self.add()
        if message == self.callback:
            if lparam in (0x202,0x203):
                self.root.after(0,self.show)
            elif lparam == 0x205:
                self.root.after(0,lambda:self.menu.tk_popup(self.root.winfo_pointerx(),self.root.winfo_pointery()))
            return 0
        return self.user.CallWindowProcW(self.old,hwnd,message,wparam,lparam)

    def close(self):
        self.shell.Shell_NotifyIconW(2,ctypes.byref(self.data))
        self.user.SetWindowLongPtrW(self.hwnd,-4,self.old)
