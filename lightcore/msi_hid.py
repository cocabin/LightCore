"""MSI PRO X670-P WIFI (MS-7D67) JRAINBOW başlıkları için doğrudan Windows HID yolu.

Yalnızca anakartın 0x1462:0x7D67 kimliğini
ve en az 725 baytlık Feature tamponunu doğrularsa komut yollar; başka hiçbir HID
aygıtına yazılmaz. Raporlar (0x52 ayar, 0x53 LED) msi_packet.py'de kurulur ve
Windows'un ilan edilen tampon boyuna (761) sıfırla tamamlanarak gönderilir.
"""
import ctypes
import os
import time
from ctypes import wintypes

from .msi_packet import (JRAINBOW_OFFSETS, PER_LED_REPORT_SIZE, REPORT_ID, REPORT_SIZE,
                         build_config, build_frame, build_hold)

VID_MSI_BOARD = 0x1462
PID_MSI_PRO_X670_P_WIFI = 0x7D67
EXPECTED_BOARD = "MS-7D67"


class MsiHidError(RuntimeError):
    pass


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", _GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_size_t)]


class _HID_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.DWORD), ("VendorID", wintypes.WORD),
                ("ProductID", wintypes.WORD), ("VersionNumber", wintypes.WORD)]


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.WORD), ("UsagePage", wintypes.WORD),
        ("InputReportByteLength", wintypes.WORD),
        ("OutputReportByteLength", wintypes.WORD),
        ("FeatureReportByteLength", wintypes.WORD),
        ("Reserved", wintypes.WORD * 17),
        ("NumberLinkCollectionNodes", wintypes.WORD),
        ("NumberInputButtonCaps", wintypes.WORD),
        ("NumberInputValueCaps", wintypes.WORD),
        ("NumberInputDataIndices", wintypes.WORD),
        ("NumberOutputButtonCaps", wintypes.WORD),
        ("NumberOutputValueCaps", wintypes.WORD),
        ("NumberOutputDataIndices", wintypes.WORD),
        ("NumberFeatureButtonCaps", wintypes.WORD),
        ("NumberFeatureValueCaps", wintypes.WORD),
        ("NumberFeatureDataIndices", wintypes.WORD),
    ]


class MsiJRainbow:
    """Opens only the verified board HID identity and sends 185-byte reports."""

    def __init__(self):
        if os.name != "nt":
            raise MsiHidError("MSI doğrudan HID kontrolü yalnızca Windows'ta kullanılabilir.")
        self._hid = ctypes.WinDLL("hid.dll", use_last_error=True)
        self._setup = ctypes.WinDLL("setupapi.dll", use_last_error=True)
        self._kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        self._configure_apis()
        self.handle = None
        self.base = None
        self.configured = False
        self.board = self._read_board_id()
        self.detection_info = ""
        self.feature_report_ok = False
        self.feature_report_size = 0
        if EXPECTED_BOARD.casefold() not in self.board.casefold():
            raise MsiHidError(f"Bu MSI yolu MS-7D67 içindir; bulunan anakart: {self.board}")
        self._connect()

    @staticmethod
    def _read_board_id():
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\BIOS") as key:
                return str(winreg.QueryValueEx(key, "BaseBoardProduct")[0])
        except OSError as exc:
            raise MsiHidError(f"Anakart modeli Windows kayıt defterinden okunamadı: {exc}") from exc

    def _configure_apis(self):
        h, s, k = self._hid, self._setup, self._kernel
        h.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
        h.HidD_GetHidGuid.restype = None
        h.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HID_ATTRIBUTES)]
        h.HidD_GetAttributes.restype = wintypes.BOOLEAN
        h.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        h.HidD_GetPreparsedData.restype = wintypes.BOOLEAN
        h.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
        h.HidD_FreePreparsedData.restype = wintypes.BOOLEAN
        h.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
        h.HidP_GetCaps.restype = ctypes.c_long
        h.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
        h.HidD_SetFeature.restype = wintypes.BOOLEAN
        h.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
        h.HidD_GetFeature.restype = wintypes.BOOLEAN
        s.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), wintypes.LPCWSTR,
                                           wintypes.HWND, wintypes.DWORD]
        s.SetupDiGetClassDevsW.restype = wintypes.HANDLE
        s.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                                  ctypes.POINTER(_GUID), wintypes.DWORD,
                                                  ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
        s.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOLEAN
        s.SetupDiGetDeviceInterfaceDetailW.argtypes = [wintypes.HANDLE,
                                                       ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
                                                       ctypes.c_void_p, wintypes.DWORD,
                                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        s.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOLEAN
        s.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
        s.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOLEAN
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOLEAN

    def _connect(self):
        guid = _GUID()
        self._hid.HidD_GetHidGuid(ctypes.byref(guid))
        info = self._setup.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x02 | 0x10)
        invalid = ctypes.c_void_p(-1).value
        if info == invalid:
            raise self._last_error("HID aygıt listesi alınamadı")

        rejected = []
        try:
            index = 0
            while True:
                item = _SP_DEVICE_INTERFACE_DATA()
                item.cbSize = ctypes.sizeof(item)
                if not self._setup.SetupDiEnumDeviceInterfaces(
                        info, None, ctypes.byref(guid), index, ctypes.byref(item)):
                    if ctypes.get_last_error() == 259:
                        break
                    raise self._last_error("HID arayüzleri listelenemedi")
                index += 1
                needed = wintypes.DWORD()
                self._setup.SetupDiGetDeviceInterfaceDetailW(
                    info, ctypes.byref(item), None, 0, ctypes.byref(needed), None)
                if not needed.value:
                    continue
                detail = ctypes.create_string_buffer(needed.value)
                ctypes.cast(detail, ctypes.POINTER(wintypes.DWORD))[0] = (
                    8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
                if not self._setup.SetupDiGetDeviceInterfaceDetailW(
                        info, ctypes.byref(item), detail, needed.value,
                        ctypes.byref(needed), None):
                    continue
                path = ctypes.wstring_at(ctypes.addressof(detail) + 4)
                handle = self._kernel.CreateFileW(
                    path, 0x80000000 | 0x40000000, 0x1 | 0x2,
                    None, 3, 0x80, None)
                if handle == invalid:
                    continue
                attrs = _HID_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(attrs)
                if not self._hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
                    self._kernel.CloseHandle(handle)
                    continue
                if (attrs.VendorID, attrs.ProductID) != (VID_MSI_BOARD, PID_MSI_PRO_X670_P_WIFI):
                    self._kernel.CloseHandle(handle)
                    continue

                preparsed = ctypes.c_void_p()
                if not self._hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
                    self._kernel.CloseHandle(handle)
                    rejected.append("MSI 1462:7D67 HID arayüzünün descriptor'ı okunamadı")
                    continue
                try:
                    caps = _HIDP_CAPS()
                    status = self._hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
                finally:
                    self._hid.HidD_FreePreparsedData(preparsed)

                if status != 0x00110000:
                    self._kernel.CloseHandle(handle)
                    rejected.append(f"HID descriptor okunamadı (HidP status 0x{status & 0xFFFFFFFF:08X})")
                    continue
                if caps.FeatureReportByteLength < PER_LED_REPORT_SIZE:
                    self._kernel.CloseHandle(handle)
                    rejected.append(
                        f"MSI 1462:7D67 arayüzünde Feature tamponu {caps.FeatureReportByteLength} byte; "
                        f"LED raporu için {PER_LED_REPORT_SIZE} byte gerekli")
                    continue

                self.handle = handle
                self.feature_report_size = caps.FeatureReportByteLength
                self._report = (ctypes.c_ubyte * caps.FeatureReportByteLength)()
                self.feature_report_ok = True
                self.base = self._read_state()
                state = ("mevcut ışık durumu okundu, kasa/kart üstü LED'ler korunacak"
                         if self.base else "mevcut durum okunamadı, varsayılan rapor kullanılacak")
                self.detection_info = (
                    f"MSI HID 1462:7D67 bulundu; Feature raporu "
                    f"{caps.FeatureReportByteLength} byte; {state}.")
                return
        finally:
            self._setup.SetupDiDestroyDeviceInfoList(info)

        detail = "; ".join(dict.fromkeys(rejected))
        if detail:
            raise MsiHidError("MSI anakart HID'i bulundu ama uygun Feature arayüzü yok: " + detail)
        raise MsiHidError(
            "MS-7D67 anakart doğrulandı ancak HID tarafında VID 1462 / PID 7D67 bulunamadı. "
            "0DB0:3130 USB Audio arayüzüne renk komutu gönderilmeyecek.")

    @staticmethod
    def _last_error(message):
        return MsiHidError(f"{message} (Windows hata {ctypes.get_last_error()})")

    def _read_state(self):
        """Anakartın mevcut 0x52 raporunu okur; okunamazsa None. Windows, koleksiyonun
        ilan ettiği tampon boyunu (761) istediği için o boyda okunur."""
        buf = (ctypes.c_ubyte * self.feature_report_size)()
        buf[0] = REPORT_ID
        if not self._hid.HidD_GetFeature(self.handle, buf, self.feature_report_size):
            return None
        data = bytes(buf)[:REPORT_SIZE]
        return data if data[0] == REPORT_ID else None

    def _send_feature(self, packet, label):
        """Raporu Windows'un istediği Feature tampon boyuna sıfırla tamamlayıp yollar."""
        report = self._report  # her kare için yeni tampon ayrılmaz
        ctypes.memset(report, 0, self.feature_report_size)
        ctypes.memmove(report, bytes(packet), len(packet))
        if not self._hid.HidD_SetFeature(self.handle, report, self.feature_report_size):
            raise self._last_error(f"MSI {label} raporu gönderilemedi")

    def apply_many(self, colors):
        """colors: {1: rgb, 2: rgb}. İlk çağrıda 0x52 ile başlıklar LED başına moda
        alınır; sonra her çağrıda yalnızca başlıkların 0x53 LED raporu gider.
        Verilmeyen JRAINBOW başlığı siyah olur."""
        if not self.handle or not self.feature_report_ok:
            raise MsiHidError("Doğrulanmış MSI Feature HID bağlantısı yok; komut gönderilmedi.")
        frames = [build_frame(h, colors.get(h, (0, 0, 0))) for h in JRAINBOW_OFFSETS]
        if not self.configured:
            self._send_feature(build_config(self.base), "0x52 ayar")
            self.configured = True
            time.sleep(0.02)
        for frame in frames:
            self._send_feature(frame, "0x53 LED")

    def hold(self, rgb, save=True):
        """Program kapanırken: başlıklar anakartın kendi Static modunda bu renkte kalır."""
        if not self.handle or not self.feature_report_ok:
            raise MsiHidError("Doğrulanmış MSI Feature HID bağlantısı yok; komut gönderilmedi.")
        self._send_feature(build_hold(self.base, rgb, save=save), "0x52 sabit renk")
        self.configured = False

    def close(self):
        if self.handle:
            self._kernel.CloseHandle(self.handle)
            self.handle = None
