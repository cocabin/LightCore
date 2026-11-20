// FanHost'un güvenlik kararları. Donanıma bağlı değil: testler bu dosyayı tek başına derleyip dener
// (tests/test_core.py FanRulesTests). FanHost.cs ile birlikte derlenir.
using System;

public static class FanRules
{
    public const double MinPercent = 25, PumpMinPercent = 60;
    public const double CpuLimit = 90, GpuLimit = 85, HotspotLimit = 100;
    public const double EmergencyClear = 5;    // acil durum sınırın bu kadar altına inince biter (sınırda gidip gelmesin)
    // Arayüz her durum mesajına (3 sn) yanıt verir; kısa takılmalarda fanlar BIOS'a düşmesin diye 10 sn.
    public const double HeartbeatSeconds = 10;

    // Okunamayan sıcaklık NaN gelir ve acil durum saymaz.
    public static bool Emergency(bool wasEmergency, double cpu, double gpu, double hot)
    {
        double clear = wasEmergency ? EmergencyClear : 0;
        return cpu >= CpuLimit - clear || gpu >= GpuLimit - clear || hot >= HotspotLimit - clear;
    }

    // Fana yazılacak yüzde: en az MinPercent (pompa PumpMinPercent), en fazla 100 ve kanalın sınırları içinde.
    public static double Clamp(double wanted, double minSoftware, double maxSoftware, bool pump)
    {
        double floor = Math.Max(minSoftware, pump ? PumpMinPercent : MinPercent);
        return Math.Min(Math.Min(100, maxSoftware), Math.Max(floor, wanted));
    }

    public static bool Stale(double secondsSinceCommand)
    {
        return secondsSinceCommand > HeartbeatSeconds;
    }
}
