from __future__ import annotations

import ctypes
import os
from typing import Protocol, runtime_checkable

from ctypes import wintypes

_WINDOWS = os.name == "nt"
_ERROR_CANCELLED = 1223
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_CREDUIWIN_GENERIC = 0x00000001
_CREDUIWIN_SECURE_PROMPT = 0x00010000


class CredentialUnavailable(RuntimeError):
    pass


@runtime_checkable
class CredentialStore(Protocol):
    def set_secret(self, credential_ref: str, secret: str) -> None: ...

    def get_secret(self, credential_ref: str) -> str: ...

    def delete_secret(self, credential_ref: str) -> None: ...

    def prompt_and_set(self, credential_ref: str, display_name: str) -> None: ...


def _unavailable(message: str = "Windows credential unavailable") -> CredentialUnavailable:
    return CredentialUnavailable(message)


def _require_non_empty_secret(secret: str) -> None:
    if not secret:
        raise ValueError("secret cannot be empty")


class MemoryCredentialStore:
    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def set_secret(self, credential_ref: str, secret: str) -> None:
        _require_non_empty_secret(secret)
        self._secrets[credential_ref] = secret

    def get_secret(self, credential_ref: str) -> str:
        try:
            return self._secrets[credential_ref]
        except KeyError as error:
            raise _unavailable() from error

    def delete_secret(self, credential_ref: str) -> None:
        try:
            del self._secrets[credential_ref]
        except KeyError as error:
            raise _unavailable() from error

    def prompt_and_set(self, credential_ref: str, display_name: str) -> None:
        secret = _prompt_windows_secret(display_name)
        if not secret:
            raise _unavailable("Credential prompt was cancelled or empty")
        self.set_secret(credential_ref, secret)


if _WINDOWS:

    class _CREDENTIAL_ATTRIBUTE_W(ctypes.Structure):
        _fields_ = [
            ("Keyword", wintypes.LPWSTR),
            ("Flags", wintypes.DWORD),
            ("ValueSize", wintypes.DWORD),
            ("Value", ctypes.c_void_p),
        ]


    class _CREDENTIAL_W(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.c_void_p),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.POINTER(_CREDENTIAL_ATTRIBUTE_W)),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]


    class _CREDUI_INFO_W(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hwndParent", wintypes.HWND),
            ("pszMessageText", wintypes.LPWSTR),
            ("pszCaptionText", wintypes.LPWSTR),
            ("hbmBanner", wintypes.HANDLE),
        ]


    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _credui = ctypes.WinDLL("credui", use_last_error=True)
    _ole32 = ctypes.OleDLL("ole32", use_last_error=True)
    _advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL_W), wintypes.DWORD]
    _advapi32.CredWriteW.restype = wintypes.BOOL

    _advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIAL_W)),
    ]
    _advapi32.CredReadW.restype = wintypes.BOOL

    _advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi32.CredDeleteW.restype = wintypes.BOOL

    _advapi32.CredFree.argtypes = [ctypes.c_void_p]
    _advapi32.CredFree.restype = None

    _credui.CredUIPromptForWindowsCredentialsW.argtypes = [
        ctypes.POINTER(_CREDUI_INFO_W),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.BOOL),
        wintypes.DWORD,
    ]
    _credui.CredUIPromptForWindowsCredentialsW.restype = wintypes.DWORD

    _credui.CredUnPackAuthenticationBufferW.argtypes = [
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _credui.CredUnPackAuthenticationBufferW.restype = wintypes.BOOL

    _ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    _ole32.CoTaskMemFree.restype = None

    def _secure_zero(pointer: ctypes.c_void_p, size: int) -> None:
        if pointer and size > 0:
            ctypes.memset(pointer, 0, size)


    def _prompt_windows_secret(display_name: str) -> str | None:
        info = _CREDUI_INFO_W(
            cbSize=ctypes.sizeof(_CREDUI_INFO_W),
            hwndParent=None,
            pszMessageText=f"Enter the API key for {display_name}",
            pszCaptionText="Configure credential",
            hbmBanner=None,
        )
        auth_package = wintypes.DWORD(0)
        out_auth_buffer = ctypes.c_void_p()
        out_auth_buffer_size = wintypes.DWORD(0)
        save = wintypes.BOOL(False)
        result = _credui.CredUIPromptForWindowsCredentialsW(
            ctypes.byref(info),
            0,
            ctypes.byref(auth_package),
            None,
            0,
            ctypes.byref(out_auth_buffer),
            ctypes.byref(out_auth_buffer_size),
            ctypes.byref(save),
            _CREDUIWIN_GENERIC | _CREDUIWIN_SECURE_PROMPT,
        )
        if result == _ERROR_CANCELLED:
            return None
        if result != 0:
            raise _unavailable()

        try:
            username_size = wintypes.DWORD(0)
            password_size = wintypes.DWORD(0)
            _credui.CredUnPackAuthenticationBufferW(
                0,
                out_auth_buffer,
                out_auth_buffer_size.value,
                None,
                ctypes.byref(username_size),
                None,
                ctypes.byref(password_size),
            )

            username_buffer = ctypes.create_unicode_buffer(username_size.value or 1)
            password_buffer = ctypes.create_unicode_buffer(password_size.value or 1)
            success = _credui.CredUnPackAuthenticationBufferW(
                0,
                out_auth_buffer,
                out_auth_buffer_size.value,
                username_buffer,
                ctypes.byref(username_size),
                password_buffer,
                ctypes.byref(password_size),
            )
            if not success:
                raise _unavailable()

            password = password_buffer.value
            _secure_zero(ctypes.addressof(username_buffer), ctypes.sizeof(username_buffer))
            _secure_zero(ctypes.addressof(password_buffer), ctypes.sizeof(password_buffer))
            if not password.strip():
                return None
            return password
        finally:
            _secure_zero(out_auth_buffer, out_auth_buffer_size.value)
            if out_auth_buffer:
                _ole32.CoTaskMemFree(out_auth_buffer)


    class WindowsCredentialStore:
        def set_secret(self, credential_ref: str, secret: str) -> None:
            _require_non_empty_secret(secret)
            blob = secret.encode("utf-8")
            blob_buffer = ctypes.create_string_buffer(blob)
            credential = _CREDENTIAL_W(
                Flags=0,
                Type=_CRED_TYPE_GENERIC,
                TargetName=ctypes.c_wchar_p(credential_ref),
                Comment=None,
                LastWritten=wintypes.FILETIME(),
                CredentialBlobSize=len(blob),
                CredentialBlob=ctypes.cast(blob_buffer, ctypes.c_void_p),
                Persist=_CRED_PERSIST_LOCAL_MACHINE,
                AttributeCount=0,
                Attributes=None,
                TargetAlias=None,
                UserName=ctypes.c_wchar_p(credential_ref),
            )
            if not _advapi32.CredWriteW(ctypes.byref(credential), 0):
                raise _unavailable()
            _secure_zero(ctypes.addressof(blob_buffer), len(blob_buffer))

        def get_secret(self, credential_ref: str) -> str:
            credential_ptr = ctypes.POINTER(_CREDENTIAL_W)()
            if not _advapi32.CredReadW(
                credential_ref,
                _CRED_TYPE_GENERIC,
                0,
                ctypes.byref(credential_ptr),
            ):
                raise _unavailable()
            try:
                credential = credential_ptr.contents
                if not credential.CredentialBlob or credential.CredentialBlobSize == 0:
                    raise _unavailable()
                blob = ctypes.string_at(
                    credential.CredentialBlob,
                    credential.CredentialBlobSize,
                )
                try:
                    return blob.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise _unavailable() from error
            finally:
                if credential_ptr:
                    _secure_zero(
                        credential_ptr.contents.CredentialBlob,
                        credential_ptr.contents.CredentialBlobSize,
                    )
                    _advapi32.CredFree(credential_ptr)

        def delete_secret(self, credential_ref: str) -> None:
            if not _advapi32.CredDeleteW(credential_ref, _CRED_TYPE_GENERIC, 0):
                raise _unavailable()

        def prompt_and_set(self, credential_ref: str, display_name: str) -> None:
            secret = _prompt_windows_secret(display_name)
            if not secret:
                raise _unavailable("Credential prompt was cancelled or empty")
            self.set_secret(credential_ref, secret)

else:

    def _prompt_windows_secret(display_name: str) -> str | None:
        raise _unavailable()


    class WindowsCredentialStore:
        def set_secret(self, credential_ref: str, secret: str) -> None:
            raise _unavailable()

        def get_secret(self, credential_ref: str) -> str:
            raise _unavailable()

        def delete_secret(self, credential_ref: str) -> None:
            raise _unavailable()

        def prompt_and_set(self, credential_ref: str, display_name: str) -> None:
            raise _unavailable()


__all__ = [
    "CredentialStore",
    "CredentialUnavailable",
    "MemoryCredentialStore",
    "WindowsCredentialStore",
]
