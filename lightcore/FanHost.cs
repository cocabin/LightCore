// FanHost: LibreHardwareMonitor ile fan kontrolü. LightCore tarafından yönetici olarak
// başlatılır; stdin'den JSON komut okur, stdout'a 3 saniyede bir JSON durum yazar.
//
// GÜVENLİK KURALLARI (hepsi burada, arayüz ne gönderirse göndersin geçerlidir):
//  - Yazılımla sürülen hiçbir fan MinPercent'in (pompa PumpMinPercent'in) altına inmez.
//  - CPU >= CpuLimit, GPU >= GpuLimit ya da GPU sıcak nokta >= HotspotLimit olursa
//    yazılımla sürülen bütün fanlar %100'e çıkar (acil durum).
//  - HeartbeatSeconds boyunca komut gelmezse (arayüz donmuş/çökmüş) bütün fanlar
//    BIOS'un kendi kontrolüne geri verilir. stdin kapanırsa da aynı şey olur ve çıkılır.
//  - Çıkışta (normal, hata, Ctrl+C, süreç sonu) bütün fanlar BIOS'a geri verilir.
//
// Komutlar: {"cmd":"set","values":{"<kontrol id>": 40, "<id>": null}}  (null = BIOS)
//           {"cmd":"quit"}
// Derleme: lightcore/fans.py, Windows'un kendi csc.exe'si ile (indirme gerekmez).
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.ExceptionServices;
using System.Security;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using LibreHardwareMonitor.Hardware;

static class Program
{
    static string libDir;

    static int Main(string[] args)
    {
        libDir = args.Length > 0 ? args[0] : AppDomain.CurrentDomain.BaseDirectory;
        // LibreHardwareMonitorLib ve bağımlılıkları verilen klasörden (programın 'fan' klasörü) yüklenir.
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            string path = Path.Combine(libDir, new AssemblyName(e.Name).Name + ".dll");
            return File.Exists(path) ? Assembly.LoadFrom(path) : null;
        };
        return Start();
    }

    // Ayrı metot: LibreHardwareMonitor türleri ancak çözümleyici kurulduktan sonra yüklensin.
    [MethodImpl(MethodImplOptions.NoInlining)]
    static int Start()
    {
        return new Host().Run();
    }
}

class Host
{
    const double MinPercent = 25, PumpMinPercent = 60;
    const double CpuLimit = 90, GpuLimit = 85, HotspotLimit = 100;
    const double EmergencyClear = 5;    // acil durum sınırın bu kadar altına inince biter (sınırda gidip gelmesin)
    // Arayüz her durum mesajına (3 sn) yanıt verir; kısa takılmalarda fanlar BIOS'a düşmesin diye 10 sn.
    const double HeartbeatSeconds = 10;
    const int LoopMilliseconds = 3000;  // sensör okuma en pahalı iş; fan için 3 sn yeterli, sinyal süresi 5 sn
    const double RewriteSeconds = 30;   // değişmeyen hız yazılmaz, ama uyku sonrası için ara ara tazelenir

    readonly object gate = new object();
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly HashSet<string> software = new HashSet<string>();  // yazılımla sürülen kontroller
    Dictionary<string, double?> wanted = new Dictionary<string, double?>();
    DateTime lastCommand = DateTime.UtcNow;
    volatile bool quit;
    bool restored, wasEmergency;
    Computer pc;
    readonly List<IHardware> hardware = new List<IHardware>();
    readonly List<ISensor> controls = new List<ISensor>(), temps = new List<ISensor>(), fans = new List<ISensor>();
    readonly List<ISensor> cpuTemps = new List<ISensor>(), gpuCores = new List<ISensor>(), gpuHot = new List<ISensor>();
    readonly Dictionary<ISensor, string> ids = new Dictionary<ISensor, string>();
    readonly Dictionary<string, double> written = new Dictionary<string, double>();  // son yazılan hız
    DateTime lastRewrite = DateTime.UtcNow;

    public int Run()
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        Console.InputEncoding = new UTF8Encoding(false);
        AppDomain.CurrentDomain.ProcessExit += (s, e) => RestoreAll();
        Console.CancelKeyPress += (s, e) => { quit = true; e.Cancel = true; };
        try
        {
            pc = new Computer { IsCpuEnabled = true, IsGpuEnabled = true, IsMotherboardEnabled = true, IsControllerEnabled = true };
            pc.Open();
            foreach (IHardware h in pc.Hardware)
            {
                hardware.Add(h);
                foreach (IHardware sub in h.SubHardware) hardware.Add(sub);
            }
            UpdateAll();
            foreach (IHardware h in hardware)
                foreach (ISensor s in h.Sensors)
                {
                    if (s.SensorType == SensorType.Control && s.Control != null) controls.Add(s);
                    else if (s.SensorType == SensorType.Temperature) temps.Add(s);
                    else if (s.SensorType == SensorType.Fan) fans.Add(s);
                }
            // Sensörü kullanılmayan donanım (ör. denetleyicisi olmayanlar) hiç okunmaz.
            hardware.RemoveAll(h => !Array.Exists(h.Sensors, s => s.SensorType == SensorType.Control
                || s.SensorType == SensorType.Temperature || s.SensorType == SensorType.Fan));
            foreach (ISensor s in temps)
            {
                if (s.Hardware.HardwareType == HardwareType.Cpu) cpuTemps.Add(s);
                else if (IsGpu(s) && s.Name == "GPU Core") gpuCores.Add(s);
                else if (IsGpu(s) && s.Name == "GPU Hot Spot") gpuHot.Add(s);
            }
            foreach (ISensor s in controls) ids[s] = s.Identifier.ToString();
            foreach (ISensor s in temps) ids[s] = s.Identifier.ToString();
            foreach (ISensor s in fans) ids[s] = s.Identifier.ToString();
            EmitHello();
            new Thread(ReadCommands) { IsBackground = true }.Start();
            while (!quit)
            {
                UpdateAll();
                Apply();
                EmitState();
                for (int i = 0; i < LoopMilliseconds / 250 && !quit; i++) Thread.Sleep(250);
            }
            return 0;
        }
        catch (Exception ex)
        {
            Emit(new Dictionary<string, object> { { "type", "error" }, { "message", ex.ToString() } });
            return 1;
        }
        finally
        {
            RestoreAll();
            if (pc != null) pc.Close();
        }
    }

    readonly HashSet<IHardware> broken = new HashSet<IHardware>();

    // Bir donanımın okuması sürücü içinde patlarsa (AccessViolation dahil) bütün süreç düşmesin:
    // o donanım bir daha okunmaz, diğer fanlar sürülmeye devam eder.
    [HandleProcessCorruptedStateExceptions, SecurityCritical]
    void UpdateAll()
    {
        foreach (IHardware h in hardware)
        {
            if (broken.Contains(h)) continue;
            try { h.Update(); }
            catch (Exception ex)
            {
                broken.Add(h);
                Emit(new Dictionary<string, object> { { "type", "error" },
                    { "message", h.Name + " okunamadı, atlanıyor: " + ex.GetType().Name + ": " + ex.Message } });
            }
        }
    }

    void ReadCommands()
    {
        string line;
        try
        {
            while ((line = Console.In.ReadLine()) != null)
            {
                var cmd = json.DeserializeObject(line) as Dictionary<string, object>;
                if (cmd == null || !cmd.ContainsKey("cmd")) continue;
                string name = cmd["cmd"] as string;
                if (name == "quit") break;
                if (name == "set")
                {
                    var values = new Dictionary<string, double?>();
                    var given = cmd.ContainsKey("values") ? cmd["values"] as Dictionary<string, object> : null;
                    if (given != null)
                        foreach (var kv in given)
                            values[kv.Key] = kv.Value == null ? (double?)null : Convert.ToDouble(kv.Value);
                    lock (gate) { wanted = values; lastCommand = DateTime.UtcNow; }
                }
            }
        }
        catch (Exception) { }
        quit = true;  // arayüz gitti: döngü biter, finally fanları BIOS'a verir
    }

    static double Max(List<ISensor> sensors)
    {
        double best = double.NaN;
        foreach (ISensor s in sensors)
            if (s.Value.HasValue && (double.IsNaN(best) || s.Value.Value > best)) best = s.Value.Value;
        return best;
    }

    static bool IsGpu(ISensor s)
    {
        return s.Hardware.HardwareType == HardwareType.GpuNvidia || s.Hardware.HardwareType == HardwareType.GpuAmd
            || s.Hardware.HardwareType == HardwareType.GpuIntel;
    }

    static bool IsPump(ISensor s)
    {
        return s.Name.IndexOf("pump", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    void Apply()
    {
        double cpu = Max(cpuTemps);
        double gpu = Max(gpuCores);
        double hot = Max(gpuHot);
        bool emergency = wasEmergency
            ? cpu >= CpuLimit - EmergencyClear || gpu >= GpuLimit - EmergencyClear || hot >= HotspotLimit - EmergencyClear
            : cpu >= CpuLimit || gpu >= GpuLimit || hot >= HotspotLimit;
        if (emergency != wasEmergency)
        {
            wasEmergency = emergency;
            Emit(new Dictionary<string, object> { { "type", "emergency" }, { "active", emergency },
                { "cpu", Num(cpu) }, { "gpu", Num(gpu) }, { "hotspot", Num(hot) } });
        }
        Dictionary<string, double?> want;
        bool stale;
        lock (gate)
        {
            want = wanted;
            stale = (DateTime.UtcNow - lastCommand).TotalSeconds > HeartbeatSeconds;
        }
        bool rewrite = (DateTime.UtcNow - lastRewrite).TotalSeconds >= RewriteSeconds;
        if (rewrite) lastRewrite = DateTime.UtcNow;
        foreach (ISensor c in controls)
        {
            string id = ids[c];
            double? value = null;
            if (!stale && want.ContainsKey(id)) value = want[id];
            if (emergency && (value.HasValue || software.Contains(id))) value = 100;
            try
            {
                if (!value.HasValue)
                {
                    if (software.Remove(id)) c.Control.SetDefault();
                    written.Remove(id);
                    continue;
                }
                double floor = Math.Max(c.Control.MinSoftwareValue, IsPump(c) ? PumpMinPercent : MinPercent);
                double v = Math.Min(Math.Min(100, c.Control.MaxSoftwareValue), Math.Max(floor, value.Value));
                double last;
                if (rewrite || !written.TryGetValue(id, out last) || last != v)
                {
                    c.Control.SetSoftware((float)v);
                    written[id] = v;
                }
                software.Add(id);
            }
            catch (Exception ex)
            {
                Emit(new Dictionary<string, object> { { "type", "error" }, { "message", c.Name + ": " + ex.Message } });
            }
        }
    }

    void RestoreAll()
    {
        lock (gate)
        {
            if (restored) return;
            restored = true;
        }
        foreach (ISensor c in controls)
        {
            if (!software.Contains(c.Identifier.ToString())) continue;
            try { c.Control.SetDefault(); } catch (Exception) { }
        }
        software.Clear();
    }

    static object Num(double v)
    {
        return double.IsNaN(v) ? null : (object)Math.Round(v, 1);
    }

    static object Value(ISensor s)
    {
        return s.Value.HasValue ? (object)Math.Round(s.Value.Value, 1) : null;
    }

    static Dictionary<string, object> Describe(ISensor s)
    {
        return new Dictionary<string, object> { { "id", s.Identifier.ToString() }, { "name", s.Name },
            { "hardware", s.Hardware.Name }, { "kind", s.Hardware.HardwareType.ToString() } };
    }

    void EmitHello()
    {
        var cs = new List<object>();
        foreach (ISensor c in controls)
        {
            var d = Describe(c);
            d["min"] = Math.Max(c.Control.MinSoftwareValue, IsPump(c) ? PumpMinPercent : MinPercent);
            d["max"] = c.Control.MaxSoftwareValue;
            d["pump"] = IsPump(c);
            cs.Add(d);
        }
        var ts = new List<object>();
        foreach (ISensor t in temps) ts.Add(Describe(t));
        var fs = new List<object>();
        foreach (ISensor f in fans) fs.Add(Describe(f));
        Emit(new Dictionary<string, object> { { "type", "hello" }, { "controls", cs }, { "temps", ts }, { "fans", fs },
            { "limits", new Dictionary<string, object> { { "min", MinPercent }, { "pump_min", PumpMinPercent },
                { "cpu", CpuLimit }, { "gpu", GpuLimit }, { "hotspot", HotspotLimit }, { "heartbeat", HeartbeatSeconds } } } });
    }

    void EmitState()
    {
        var ts = new Dictionary<string, object>();
        foreach (ISensor t in temps) ts[ids[t]] = Value(t);
        var fs = new Dictionary<string, object>();
        foreach (ISensor f in fans) fs[ids[f]] = Value(f);
        var cs = new Dictionary<string, object>();
        foreach (ISensor c in controls)
            cs[ids[c]] = new Dictionary<string, object> { { "value", Value(c) },
                { "software", software.Contains(ids[c]) } };
        bool stale;
        lock (gate) stale = (DateTime.UtcNow - lastCommand).TotalSeconds > HeartbeatSeconds;
        Emit(new Dictionary<string, object> { { "type", "state" }, { "temps", ts }, { "fans", fs }, { "controls", cs },
            { "emergency", wasEmergency }, { "stale", stale } });
    }

    readonly object outGate = new object();

    void Emit(Dictionary<string, object> message)
    {
        string line = json.Serialize(message);
        lock (outGate)
        {
            Console.Out.WriteLine(line);
            Console.Out.Flush();
        }
    }
}
