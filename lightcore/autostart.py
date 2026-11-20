"""Windows oturumu açılınca programı başlatma (Görev Zamanlayıcı).

Başlangıç klasörü ya da Run kaydı yerine görev kullanılır: fan kontrolü yönetici yetkisi
ister ve görev "en yüksek ayrıcalıkla" her açılışta UAC sorusu çıkarmadan başlayabilir.
Görevi oluşturmak için programın kendisi yönetici olarak açılmış olmalıdır; değilse görev
yetkisiz kurulur (ışıklar çalışır, fan kontrolü çalışmaz) ve bu söylenir.

Görev ayarları: 3 günlük süre sınırı kapalı (varsayılanda Windows görevi 72 saat sonra
durdurur), pilde de çalışır, ikinci kopya açılmaz, oturum açıldıktan 10 sn sonra başlar.
"""
import os
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

TASK_NAME = "LightCore"
OLD_TASK_NAME = "RGB Kontrol"  # önceki sürüm: yeni görev kurulunca silinir
CREATE_NO_WINDOW = 0x08000000


def _run(args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace",
                          timeout=30, creationflags=CREATE_NO_WINDOW)


def launch_command(program_dir):
    """(komut, argümanlar): konsolsuz pythonw + app.py; açılışta simge tepsisinde başlar.
    -OO -S: açıklama metinleri ve eklenti taraması yüklenmez, daha az bellek ve daha hızlı açılış."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--minimized"
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(pythonw):
        exe = pythonw
    return exe, f'-OO -S "{os.path.join(program_dir, "app.py")}" --minimized'


def task_xml(program_dir, elevated, user=None):
    user = user or f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
    command, args = launch_command(program_dir)
    level = "HighestAvailable" if elevated else "LeastPrivilege"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>LightCore: oturum açılınca ışıkları ve fanları başlatır.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId><Delay>PT10S</Delay></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>{level}</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(command)}</Command><Arguments>{escape(args)}</Arguments><WorkingDirectory>{escape(program_dir)}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def status(program_dir):
    """(açık mı, bu klasörü mü başlatıyor)."""
    r = _run(["/Query", "/TN", TASK_NAME, "/XML"])
    if r.returncode:
        return _run(["/Query", "/TN", OLD_TASK_NAME]).returncode == 0, False
    return True, program_dir.lower() in r.stdout.lower()


def enable(program_dir, elevated):
    path = os.path.join(tempfile.gettempdir(), "lightcore_task.xml")
    with open(path, "w", encoding="utf-16") as f:
        f.write(task_xml(program_dir, elevated))
    try:
        r = _run(["/Create", "/TN", TASK_NAME, "/XML", path, "/F"])
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip() or "Görev oluşturulamadı.")
    _run(["/Delete", "/TN", OLD_TASK_NAME, "/F"])


def disable():
    _run(["/Delete", "/TN", OLD_TASK_NAME, "/F"])
    r = _run(["/Delete", "/TN", TASK_NAME, "/F"])
    if r.returncode and "bulunamıyor" not in (r.stderr + r.stdout).lower() \
            and "cannot find" not in (r.stderr + r.stdout).lower():
        raise RuntimeError((r.stderr or r.stdout).strip() or "Görev silinemedi.")
