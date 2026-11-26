"""Check staging API access using the current Windows user's stored token."""

import ctypes
import re
import sys
from ctypes import wintypes
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


CREDENTIAL_NAME = "AnTir.PythonAnywhere.Staging"
API_ROOT = "https://www.pythonanywhere.com/api/v0/user/feradach"
CRED_TYPE_GENERIC = 1


class Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def read_generic_credential(target):
    advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    read = advapi.CredReadW
    read.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(Credential)),
    ]
    read.restype = wintypes.BOOL
    free = advapi.CredFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None

    result = ctypes.POINTER(Credential)()
    if not read(target, CRED_TYPE_GENERIC, 0, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = result.contents
        return ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize).decode("utf-16-le")
    finally:
        free(result)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    if sys.platform != "win32":
        print("This check uses Windows Credential Manager.", file=sys.stderr)
        return 1

    try:
        token = read_generic_credential(CREDENTIAL_NAME)
    except (OSError, UnicodeError):
        print(f"Could not read the generic credential {CREDENTIAL_NAME}.", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[A-Za-z0-9._~-]+", token):
        print("The stored value is not a PythonAnywhere API token.", file=sys.stderr)
        return 1

    opener = build_opener(ProxyHandler({}), NoRedirect())
    for endpoint in ("webapps/feradach.pythonanywhere.com/", "consoles/"):
        request = Request(f"{API_ROOT}/{endpoint}", headers={"Authorization": f"Token {token}"})
        try:
            with opener.open(request, timeout=15) as response:
                if response.status != 200:
                    print(f"PythonAnywhere returned HTTP {response.status}.", file=sys.stderr)
                    return 1
        except HTTPError as error:
            print(f"PythonAnywhere returned HTTP {error.code}.", file=sys.stderr)
            return 1
        except URLError:
            print("Could not connect to the PythonAnywhere API.", file=sys.stderr)
            return 1

    print("PythonAnywhere staging web app and console API access verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
