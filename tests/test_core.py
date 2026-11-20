import os
import struct
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from mock_openrgb import MockServer, pack_device
from lightcore import effects as fx
from lightcore.engine import apply
from lightcore.launcher import find_openrgb
from lightcore.openrgb_client import OpenRGBClient, ProtocolError, parse_device

MSI = ("Test Anakart", "MSI", [("JRGB1", 1)], 1)
KEYBOARD = ("Test Klavye", "X", [("Keyboard", 4)], 4, (("Direct", 0),))
RAM = ("Test RAM", "X", [("DRAM", 3)], 3)
STATIC_ONLY = ("Eski Cihaz", "X", [("Z", 2)], 2, (("Static", 1),))
MSI_ARGB = ("Test Anakart", "MSI", [("JRGB1", 1), ("JRAINBOW1", 4, 0, 200), ("Onboard", 2)], 7)
MSI_EMPTY_ARGB = ("Test Anakart", "MSI", [("JRGB1", 1), ("JRAINBOW1", 0, 0, 200),
                                                ("JRAINBOW2", 0, 0, 40)], 1)


def run_effect(devices, effect, seconds, fps=10, brightness=1.0):
    srv = MockServer([pack_device(*d) for d in devices])
    srv.start()
    cl = OpenRGBClient(port=srv.port)
    cl.connect()
    found = cl.list_devices()
    stop = threading.Event()
    t = threading.Thread(target=apply, args=(cl, found, effect, fps, brightness, stop))
    t.start()
    time.sleep(seconds)
    stop.set()
    t.join()
    cl.close()
    return srv


def color_of(payload):
    return tuple(payload[-4:-1])


class ClientTests(unittest.TestCase):
    def test_lists_devices_with_controller_ids_and_skips_stray_packets(self):
        srv = MockServer([pack_device(*MSI), pack_device(*KEYBOARD)])
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        devs = cl.list_devices()
        cl.close()
        self.assertEqual([(d.name, d.led_count, d.controller_id) for d in devs],
                         [("Test Anakart", 1, 10), ("Test Klavye", 4, 11)])
        self.assertEqual(devs[0].zones[0].name, "JRGB1")
        self.assertEqual([m.name for m in devs[0].modes], ["Direct", "Static"])

    def test_wait_for_devices_returns_when_count_is_stable(self):
        srv = MockServer([pack_device(*RAM), pack_device(*KEYBOARD)])
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        seen = []
        devs = cl.wait_for_devices(settle=0.3, min_wait=0.3, progress=seen.append)
        cl.close()
        self.assertEqual((len(devs), seen), (2, [2]))

    def test_truncated_packet_is_detected(self):
        raw = pack_device(*RAM)
        with self.assertRaises(ProtocolError):
            parse_device(0, raw[:-5], 6)
        with self.assertRaises(ProtocolError):
            parse_device(0, raw + b"\0\0", 6)


class EngineTests(unittest.TestCase):
    def test_jrgb_inherits_board_mode_then_static_and_zone_led(self):
        srv = run_effect([MSI], fx.Static((10, 10, 10)), 0.4, brightness=0.5)
        self.assertEqual(struct.unpack_from("<ii", srv.of(1103)[0], 4), (0, -1))
        self.assertEqual([color_of(p) for p in srv.of(1101)], [(5, 5, 5)])
        self.assertEqual([color_of(p) for p in srv.of(1051)], [(5, 5, 5)])
        self.assertEqual(srv.of(1050), [])

    def test_jrainbow_fans_are_driven_with_jrgb(self):
        srv = run_effect([MSI_ARGB], fx.Static((7, 8, 9)), 0.4)
        zone_modes = [struct.unpack_from("<ii", p, 4) for p in srv.of(1103)]
        self.assertEqual(zone_modes, [(0, -1), (1, -1)])  # JRGB1 ve JRAINBOW1, Onboard değil
        leds = {struct.unpack_from("<I", p, 4)[0]: p[10:] for p in srv.of(1051)}
        self.assertEqual(leds, {0: bytes((7, 8, 9, 0)), 1: bytes((7, 8, 9, 0)) * 4})

    def test_empty_argb_zones_are_resized_within_limits(self):
        from lightcore.engine import resize_fan_zones
        srv = MockServer([pack_device(*MSI_EMPTY_ARGB)])
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        devs = cl.list_devices()
        self.assertEqual([(z.leds_min, z.leds_max) for z in devs[0].zones], [(1, 1), (0, 200), (0, 40)])
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            self.assertTrue(resize_fan_zones(cl, devs, 60))
        cl.close()
        self.assertEqual([struct.unpack("<ii", p) for p in srv.of(1000, 10)], [(1, 60), (2, 40)])

    def test_ram_and_keyboard_use_direct_led_writes(self):
        srv = run_effect([RAM, KEYBOARD], fx.Static((1, 2, 3)), 0.3)
        self.assertEqual(len(srv.of(1100)), 2)  # ikisi de Custom/Direct moduna alındı
        self.assertEqual(srv.of(1101), [])
        self.assertEqual(srv.of(1050, 10)[0][6:], bytes((1, 2, 3, 0)) * 3)
        self.assertEqual(srv.of(1050, 11)[0][6:], bytes((1, 2, 3, 0)) * 4)

    def test_static_only_device_falls_back_to_mode_update(self):
        srv = run_effect([STATIC_ONLY], fx.Static((20, 0, 0)), 0.3)
        self.assertEqual([color_of(p) for p in srv.of(1101)], [(20, 0, 0)])
        self.assertEqual(srv.of(1050), [])

    def test_jrgb_is_throttled_and_board_mode_is_written_once(self):
        cycle = fx.HoldCycle([(255, 0, 0), (0, 0, 255)], hold=0, fade=0.5)
        srv = run_effect([MSI, KEYBOARD], cycle, 1.2, fps=50)
        self.assertEqual(len(srv.of(1101, 10)), 1)  # her karede mod yazmak fanları söndürüyordu
        self.assertTrue(5 <= len(srv.of(1051, 10)) <= 14)
        self.assertGreater(len(srv.of(1050, 11)), 25)

    def test_parallel_slow_device_does_not_stall_fast_one(self):
        from lightcore.engine import apply_parallel
        srv = MockServer([pack_device(*RAM), pack_device(*KEYBOARD)], delays={10: 0.15})
        srv.start()
        clients = {}
        for cid in (10, 11):
            clients[cid] = OpenRGBClient(port=srv.port)
            clients[cid].connect()
        found = clients[10].list_devices()
        fans = FakeFans()
        stop = threading.Event()
        cycle = fx.HoldCycle([(255, 0, 0), (0, 0, 255)], hold=0, fade=0.5)
        t = threading.Thread(target=apply_parallel, args=(clients, found, cycle),
                             kwargs={"fps": 50, "stop": stop, "fans": fans})
        t.start()
        time.sleep(1.0)
        stop.set()
        t.join()
        for c in clients.values():
            c.close()
        slow, fast = len(srv.of(1050, 10)), len(srv.of(1050, 11))
        self.assertLessEqual(slow, 9)          # yavaş RAM ~7 kare
        self.assertGreater(fast, 35)           # klavye onu beklemedi
        self.assertGreater(len(fans.calls), 14)  # fanlar da beklemedi (bekleseydi ~7 olurdu)

    def _parallel(self, srv, seconds, errors):
        from lightcore.engine import apply_parallel
        srv.start()
        client = OpenRGBClient(port=srv.port)
        client.connect()
        found = client.list_devices()
        stop = threading.Event()
        cycle = fx.HoldCycle([(255, 0, 0), (0, 0, 255)], hold=0, fade=0.5)
        t = threading.Thread(target=apply_parallel, args=({d.controller_id: client for d in found}, found, cycle),
                             kwargs={"fps": 50, "stop": stop, "on_error": lambda g, e: errors.append(e)})
        t.start()
        time.sleep(seconds)
        stop.set()
        t.join()
        client.close()

    def test_gpu_is_throttled_to_bus_rate(self):
        gpu = bytearray(pack_device("Test GPU", "X", [("GPU", 8)], 8))
        struct.pack_into("<I", gpu, 4, 2)  # tür: ekran kartı
        srv = MockServer([bytes(gpu)])
        self._parallel(srv, 1.0, [])
        self.assertTrue(10 <= len(srv.of(1050)) <= 22, len(srv.of(1050)))

    def test_failing_device_is_retried_not_dropped(self):
        srv = MockServer([pack_device(*KEYBOARD)], fail_first={10: 2})
        errors = []
        self._parallel(srv, 3.0, errors)
        self.assertEqual(len(errors), 2)
        self.assertIn("yeniden deneniyor", errors[0])
        self.assertGreater(len(srv.of(1050)), 20)  # hatadan sonra yazmaya devam etti


class DiagnosticsTests(unittest.TestCase):
    def _idle_and_writer(self):
        srv = MockServer([pack_device(*KEYBOARD)], notify=True)
        srv.start()
        idle, writer = OpenRGBClient(port=srv.port), OpenRGBClient(port=srv.port)
        idle.connect()
        writer.connect(command_timeout=1.0)
        dev = idle.list_devices()[0]
        return idle, writer, dev

    def test_unread_notifications_stall_the_server(self):
        # Gerçek OpenRGB'de ölçülen kilitlenme: boştaki bağlantı bildirimleri okumazsa
        # sunucu ona yazarken takılır ve yazan bağlantı onay alamaz.
        idle, writer, dev = self._idle_and_writer()
        with self.assertRaises(ProtocolError):
            for _ in range(20000):
                writer.set_leds(dev.controller_id, [(1, 2, 3)] * dev.led_count)
        idle.close()
        writer.close()

    def test_drain_keeps_server_alive_and_client_usable(self):
        idle, writer, dev = self._idle_and_writer()
        stop = threading.Event()

        def drainer():
            while not stop.is_set():
                idle.drain()
                time.sleep(0.01)

        t = threading.Thread(target=drainer)
        t.start()
        for _ in range(3000):
            writer.set_leds(dev.controller_id, [(1, 2, 3)] * dev.led_count)
        stop.set()
        t.join()
        self.assertEqual(idle.list_devices()[0].name, "Test Klavye")  # yarım paket kalmadı
        idle.close()
        writer.close()

    def test_old_protocol_waits_for_server_after_each_command(self):
        # Onaysız (eski) protokolde her komuttan sonra cevaplı bir istekle eşitlenir;
        # böylece komutlar OpenRGB'de birikmez. Yavaş cihaz: yazmalar onu bekler.
        srv = MockServer([pack_device(*KEYBOARD)], ack=False, delays={10: 0.1})
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        devs = cl.list_devices()
        cl.proto = 4
        began = time.monotonic()
        for _ in range(3):
            cl.set_leds(devs[0].controller_id, [(1, 2, 3)] * 4)
        took = time.monotonic() - began
        cl.close()
        self.assertGreaterEqual(took, 0.28)  # her yazma sunucuyu bekledi
        self.assertEqual(len(srv.of(1050)), 3)

    def test_missing_ack_names_the_packet(self):
        srv = MockServer([pack_device(*RAM)], ack=False)
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        devs = cl.list_devices()
        cl.sock.settimeout(0.3)
        with self.assertRaises(ProtocolError) as ctx:
            cl.set_custom_mode(devs[0].controller_id)
        cl.close()
        self.assertIn("1100", str(ctx.exception))


class RunnerTests(unittest.TestCase):
    def test_start_never_blocks_and_jobs_never_overlap(self):
        from lightcore.ui import Runner
        runner = Runner(lambda _text: None)
        log, running = [], []

        def job(name, ignore_stop):
            def run(stop):
                running.append(name)
                self.assertEqual(len(running), 1, "iki iş aynı anda çalıştı")
                log.append(name)
                if ignore_stop:
                    time.sleep(0.5)  # takılı cihaz: durma isteğini hemen duymaz
                else:
                    stop.wait(5)
                running.remove(name)
            return run

        runner.start(job("eski", True))
        time.sleep(0.05)
        began = time.monotonic()
        runner.start(job("ara", False))   # hemen ardından gelen ayar: hiç çalışmamalı
        runner.start(job("yeni", False))
        self.assertLess(time.monotonic() - began, 0.1)  # arayüz beklemedi
        time.sleep(0.8)
        runner.halt(timeout=2)
        self.assertEqual(log, ["eski", "yeni"])


class FakeFans:
    def __init__(self):
        self.calls = []

    def apply_many(self, colors):
        self.calls.append(dict(colors))


class FanHidTests(unittest.TestCase):
    def _run(self, effect, seconds, fps=10, once=False):
        srv = MockServer([pack_device(*MSI), pack_device(*RAM)])
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        found = cl.list_devices()
        fans = FakeFans()
        stop = threading.Event()
        t = threading.Thread(target=apply, args=(cl, found, effect, fps, 1.0, stop),
                             kwargs={"fans": fans, "once": once})
        t.start()
        time.sleep(seconds)
        stop.set()
        t.join()
        cl.close()
        return srv, fans

    def test_static_once_sends_both_headers_in_one_call_and_returns(self):
        srv, fans = self._run(fx.Static((9, 8, 7)), 0.4, once=True)
        self.assertEqual(fans.calls, [{1: (9, 8, 7), 2: (9, 8, 7)}])
        self.assertEqual(srv.of(1101, 10) + srv.of(1103, 10) + srv.of(1051, 10), [])  # OpenRGB MSI'ye dokunmadı
        self.assertEqual(srv.of(1050, 11)[0][6:], bytes((9, 8, 7, 0)) * 3)  # RAM yine LED yolunda

    def test_fan_writes_are_throttled(self):
        cycle = fx.HoldCycle([(255, 0, 0), (0, 0, 255)], hold=0, fade=0.5)
        _, fans = self._run(cycle, 1.2, fps=100)
        self.assertTrue(15 <= len(fans.calls) <= 62, len(fans.calls))  # en fazla 1/FAN_HID_INTERVAL


class DefaultsTests(unittest.TestCase):
    def test_board_is_driven_by_default_and_no_board_skips_it(self):
        from lightcore.__main__ import build_parser
        self.assertTrue(build_parser().parse_args([]).board)
        self.assertTrue(build_parser().parse_args(["--board"]).board)
        self.assertFalse(build_parser().parse_args(["--no-board"]).board)
        srv = MockServer([pack_device(*MSI_EMPTY_ARGB), pack_device(*RAM)])
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        found = cl.list_devices()
        stop = threading.Event()
        t = threading.Thread(target=apply, args=(cl, found, fx.Static((3, 3, 3)), 10, 1.0, stop),
                             kwargs={"skip_board": True})
        t.start()
        time.sleep(0.4)
        stop.set()
        t.join()
        cl.close()
        self.assertEqual([c for c in srv.commands if c[1] == 10], [])  # anakart: hiç komut yok
        self.assertTrue(srv.of(1050, 11))  # RAM sürüldü


class MsiPacketTests(unittest.TestCase):
    def test_config_puts_jrainbow_in_per_led_mode(self):
        from lightcore.msi_packet import build_config
        p = build_config()
        self.assertEqual((len(p), p[0], p[184]), (185, 0x52, 0))
        self.assertEqual((p[0x1F], p[0x2A], p[0x1F + 10], p[0x2A + 10]), (0x25, 0x25, 60, 60))

    def test_config_keeps_base_outside_jrainbow(self):
        from lightcore.msi_packet import build_config, default_packet
        base = bytearray(range(185))
        base[0] = 0x52
        p = build_config(bytes(base))
        changed = {i for i in range(185) if p[i] != base[i]}
        jrainbow = set(range(0x1F, 0x1F + 11)) | set(range(0x2A, 0x2A + 11)) | {184}
        self.assertTrue(changed <= jrainbow, sorted(changed - jrainbow))
        self.assertEqual(p[0x1F + 10], base[0x1F + 10])  # LED sayısı korunur
        self.assertEqual(build_config(b"\x00" * 185), build_config(default_packet()))  # bozuk base yok sayılır

    def test_frame_layout(self):
        from lightcore.msi_packet import build_frame
        f = build_frame(2, (1, 2, 3))
        self.assertEqual(len(f), 725)
        self.assertEqual(tuple(f[:5]), (0x53, 0x25, 0x04, 1, 0))
        self.assertEqual(f[5:5 + 180], bytes((1, 2, 3)) * 60)
        self.assertEqual(f[185:], bytes(725 - 185))

    def test_rejects_bad_input(self):
        from lightcore.msi_packet import build_frame
        with self.assertRaises(ValueError):
            build_frame(3, (0, 0, 0))
        with self.assertRaises(ValueError):
            build_frame(1, (256, 0, 0))


class HueTests(unittest.TestCase):
    def setUp(self):
        from mock_hue import MockHue
        self.mock = MockHue()

    def tearDown(self):
        self.mock.close()

    def bridge(self, username="kullanici"):
        from lightcore.hue import HueBridge
        return HueBridge("127.0.0.1", username, port=self.mock.port)

    def test_color_conversion(self):
        from lightcore.hue import rgb_to_xy_bri
        (x, y), bri = rgb_to_xy_bri((255, 0, 0))
        self.assertAlmostEqual(x, 0.70, delta=0.02)
        self.assertAlmostEqual(y, 0.30, delta=0.02)
        self.assertEqual(bri, 254)
        self.assertEqual(rgb_to_xy_bri((0, 0, 0))[1], 0)
        self.assertEqual(rgb_to_xy_bri((128, 128, 128))[1], 127)

    def test_pairing_needs_the_button(self):
        from lightcore.hue import HueLinkButtonNotPressed
        self.mock.button_pressed = False
        b = self.bridge(None)
        with self.assertRaises(HueLinkButtonNotPressed):
            b.pair()
        self.mock.button_pressed = True
        self.assertEqual(b.pair(), "kullanici")
        b.close()

    def test_lists_only_color_lights_and_rejects_bad_user(self):
        from lightcore.hue import HueError
        b = self.bridge()
        self.assertEqual([l["name"] for l in b.lights()], ["Lamba 1", "Lamba 2"])
        with self.assertRaises(HueError):
            self.bridge("yanlis").lights()
        b.close()

    def test_set_color_and_off(self):
        b = self.bridge()
        b.set_color("1", (0, 0, 255), transition=0.3)
        b.set_color("1", (0, 0, 0))
        b.close()
        (_, on), (_, off) = self.mock.states
        self.assertTrue(on["on"])
        self.assertEqual(on["transitiontime"], 3)
        self.assertEqual(off, {"on": False, "transitiontime": 0})

    def test_sink_respects_bridge_rate(self):
        from lightcore.engine import apply_parallel
        from lightcore.hue import HueSink
        sink = HueSink(self.bridge(), ["1", "2"])
        self.assertEqual(sink.interval, 0.25)  # 2 lamba: 10 komut/sn sınırının altında
        stop = threading.Event()
        rainbow = fx.Rainbow(1.0)
        t = threading.Thread(target=apply_parallel, args=({}, [], rainbow),
                             kwargs={"stop": stop, "sinks": [sink]})
        t.start()
        time.sleep(1.0)
        stop.set()
        t.join()
        sink.bridge.close()
        writes = len(self.mock.states) / 2
        self.assertTrue(3 <= writes <= 5, writes)  # ~4 güncelleme/sn, her biri 2 lamba
        self.assertTrue(all(s["transitiontime"] == 2 or s["transitiontime"] == 3 for _, s in self.mock.states))

    def test_discover_recognizes_bridge(self):
        from lightcore import hue
        self.assertIsNone(hue._is_bridge("127.0.0.1:1"))  # kapalı port
        info = hue._is_bridge(f"127.0.0.1:{self.mock.port}")
        self.assertEqual(info["id"], "001788FFFE000000")


class FanPlannerTests(unittest.TestCase):
    HELLO = {
        "controls": [{"id": "/lpc/0/control/0", "name": "CPU Fan", "hardware": "Test SuperIO", "kind": "SuperIO",
                      "min": 25, "max": 100, "pump": False},
                     {"id": "/gpu/0/control/1", "name": "GPU Fan 1", "hardware": "RTX", "kind": "GpuNvidia",
                      "min": 30, "max": 100, "pump": False}],
        "temps": [{"id": "/cpu/t/0", "name": "Core (Tctl/Tdie)", "hardware": "Test CPU", "kind": "Cpu"},
                  {"id": "/igpu/t/0", "name": "GPU Core", "hardware": "AMD Radeon(TM) Graphics", "kind": "GpuAmd"},
                  {"id": "/gpu/t/0", "name": "GPU Core", "hardware": "RTX", "kind": "GpuNvidia"}],
    }

    def test_curve_interpolates_and_clamps(self):
        from lightcore.fans import curve_value
        pts = [(40, 30), (60, 50), (80, 100)]
        self.assertEqual((curve_value(pts, 20), curve_value(pts, 50), curve_value(pts, 70), curve_value(pts, 95)),
                         (30.0, 40.0, 75.0, 100.0))

    def test_sources_prefer_discrete_gpu_and_tctl(self):
        from lightcore.fans import FanPlanner
        p = FanPlanner(self.HELLO)
        self.assertEqual(p.sources, {"cpu": "/cpu/t/0", "gpu": "/gpu/t/0"})

    CPU, GPU = "/lpc/0/control/0", "/gpu/0/control/1"

    def plan(self, curves, channels, temps, now=0, planner=None):
        from lightcore.fans import FanPlanner
        p = planner or FanPlanner(self.HELLO)
        return p, p.desired({"curves": curves, "channels": channels}, temps, now=now)

    def test_fixed_auto_and_builtin_curve(self):
        from lightcore.fans import CURVES, curve_value
        _, v = self.plan([], {self.CPU: {"mode": "curve", "curve": "Dengeli", "source": "cpu"},
                              self.GPU: {"mode": "fixed", "value": 45}}, {"/cpu/t/0": 62.0})
        self.assertEqual(v[self.GPU], 45.0)
        self.assertAlmostEqual(v[self.CPU], curve_value(CURVES["Dengeli"], 62.0), places=1)
        _, v = self.plan([], {}, {})
        self.assertEqual(v, {self.CPU: None, self.GPU: None})

    def test_missing_temperature_means_full_speed(self):
        _, v = self.plan([], {self.CPU: {"mode": "curve", "curve": "Dengeli", "source": "cpu"}}, {"/cpu/t/0": None})
        self.assertEqual(v[self.CPU], 100.0)

    def test_curve_ramps_down_slowly_but_up_at_once(self):
        from lightcore.fans import RAMP_DOWN_PER_SECOND
        ch = {self.CPU: {"mode": "curve", "curve": "Dengeli", "source": "cpu"}}
        p, hot = self.plan([], ch, {"/cpu/t/0": 80.0}, now=0)
        _, cooler = self.plan([], ch, {"/cpu/t/0": 40.0}, now=1, planner=p)
        _, again = self.plan([], ch, {"/cpu/t/0": 80.0}, now=2, planner=p)
        self.assertEqual((hot[self.CPU], cooler[self.CPU], again[self.CPU]), (100.0, 100.0 - RAMP_DOWN_PER_SECOND, 100.0))

    def test_linear_flat_trigger_and_mix(self):
        curves = [{"type": "linear", "name": "L", "source": "cpu", "min_temp": 40, "max_temp": 80,
                   "min_value": 20, "max_value": 100},
                  {"type": "flat", "name": "F", "value": 35},
                  {"type": "trigger", "name": "T", "source": "gpu", "idle_temp": 50, "load_temp": 70,
                   "idle_value": 30, "load_value": 90},
                  {"type": "mix", "name": "M", "curves": ["L", "F"], "function": "max"}]
        ch = lambda c: {self.CPU: {"mode": "curve", "curve": c, "step_down": 100}}
        temps = {"/cpu/t/0": 60.0, "/gpu/t/0": 60.0}
        self.assertEqual(self.plan(curves, ch("L"), temps)[1][self.CPU], 60.0)
        self.assertEqual(self.plan(curves, ch("F"), temps)[1][self.CPU], 35.0)
        self.assertEqual(self.plan(curves, ch("M"), temps)[1][self.CPU], 60.0)
        p, v = self.plan(curves, ch("T"), temps)              # 60°C: henüz yük yok
        self.assertEqual(v[self.CPU], 30.0)
        _, v = self.plan(curves, ch("T"), dict(temps, **{"/gpu/t/0": 72.0}), now=1, planner=p)
        self.assertEqual(v[self.CPU], 90.0)
        _, v = self.plan(curves, ch("T"), temps, now=2, planner=p)  # 60°C: boşta sıcaklığına inmedi, yükte kalır
        self.assertEqual(v[self.CPU], 90.0)

    def test_sync_follows_other_fan_with_offset_in_same_tick(self):
        curves = [{"type": "flat", "name": "Temel", "value": 40}, {"type": "sync", "name": "İz", "control": self.CPU,
                                                                    "offset": 10}]
        _, v = self.plan(curves, {self.CPU: {"mode": "curve", "curve": "Temel"},
                                  self.GPU: {"mode": "curve", "curve": "İz"}}, {})
        self.assertEqual((v[self.CPU], v[self.GPU]), (40.0, 50.0))

    def test_missing_sensor_falls_back_and_is_reported(self):
        curve = [{"type": "flat", "name": "x", "value": 1},
                 {"type": "linear", "name": "L", "source": "/amdcpu/0/temperature/99", "min_temp": 40, "max_temp": 80,
                  "min_value": 0, "max_value": 80}]
        p, v = self.plan(curve, {self.CPU: {"mode": "curve", "curve": "L"}}, {"/cpu/t/0": 60.0})
        self.assertEqual(v[self.CPU], 40.0)
        self.assertEqual(p.missing_sources, {"/amdcpu/0/temperature/99"})

    def test_no_temperature_at_all_is_still_full_speed(self):
        curve = [{"type": "linear", "name": "L", "source": "/amdcpu/0/temperature/99"}]
        _, v = self.plan(curve, {self.CPU: {"mode": "curve", "curve": "L"}}, {})
        self.assertEqual(v[self.CPU], 100.0)

    def test_self_referencing_mix_is_safe(self):
        curves = [{"type": "mix", "name": "A", "curves": ["A"]}]
        _, v = self.plan(curves, {self.CPU: {"mode": "curve", "curve": "A"}}, {})
        self.assertEqual(v[self.CPU], 100.0)  # hesaplanamayan eğri: güvenli taraf

    def test_hysteresis_waits_for_threshold_and_response_time(self):
        curves = [{"type": "linear", "name": "L", "source": "cpu", "min_temp": 40, "max_temp": 80, "min_value": 0,
                   "max_value": 80, "hysteresis": {"up": 4, "down": 4, "response_up": 2, "response_down": 0}}]
        ch = {self.CPU: {"mode": "curve", "curve": "L", "step_down": 100}}
        p, v = self.plan(curves, ch, {"/cpu/t/0": 60.0}, now=0)
        self.assertEqual(v[self.CPU], 40.0)
        _, v = self.plan(curves, ch, {"/cpu/t/0": 62.0}, now=1, planner=p)  # eşik (4°C) altında: değişmez
        self.assertEqual(v[self.CPU], 40.0)
        _, v = self.plan(curves, ch, {"/cpu/t/0": 66.0}, now=2, planner=p)  # eşik geçti, 2 sn bekle
        self.assertEqual(v[self.CPU], 40.0)
        _, v = self.plan(curves, ch, {"/cpu/t/0": 66.0}, now=4, planner=p)
        self.assertEqual(v[self.CPU], 52.0)

    def test_offset_minimum_stop_and_step_up(self):
        curves = [{"type": "flat", "name": "F", "value": 30}]
        _, v = self.plan(curves, {self.CPU: {"mode": "curve", "curve": "F", "offset": 10, "minimum": 50}}, {})
        self.assertEqual(v[self.CPU], 50.0)
        _, v = self.plan(curves, {self.CPU: {"mode": "curve", "curve": "F", "stop": 35}}, {})
        self.assertEqual(v[self.CPU], 0.0)  # FanHost bunu en düşük güvenli hıza çeker
        ch = {self.CPU: {"mode": "curve", "curve": "F", "step_up": 4}}
        p, _ = self.plan([{"type": "flat", "name": "F", "value": 30}], ch, {}, now=0)
        _, v = self.plan([{"type": "flat", "name": "F", "value": 60}], ch, {}, now=1, planner=p)
        self.assertEqual(v[self.CPU], 34.0)


FANCONTROL_SAMPLE = {
    "__VERSION__": "284",
    "FanControl": {
        "Controls": [
            {"NickName": "Control 1 - Test GPU", "Identifier": "NVApiWrapper/0-TEST/control/0",
             "Enable": False, "SelectedFanCurve": None},
            {"NickName": "CPU Fan", "Identifier": "/lpc/superio/control/0", "Enable": True,
             "SelectedFanCurve": {"Name": "Temel"}, "SelectedOffset": 0, "SelectedStart": 30, "SelectedStop": 10,
             "MinimumPercent": 0, "SelectedCommandStepUp": 5, "SelectedCommandStepDown": 3, "ManualControl": False},
            {"NickName": "Arka fan", "Identifier": "/lpc/superio/control/3", "Enable": True,
             "SelectedFanCurve": {"Name": "Takip"}, "SelectedCommandStepUp": 2, "SelectedCommandStepDown": 1},
            {"NickName": "Elle", "Identifier": "/lpc/superio/control/4", "Enable": True,
             "ManualControl": True, "ManualControlValue": 55},
            {"NickName": "yok", "Identifier": "/lpc/baska/control/9", "Enable": True},
        ],
        "FanCurves": [
            {"Name": "Takip", "SelectedControl": {"Identifier": "/lpc/superio/control/0"}, "SelectedOffset": 5,
             "Proportional": False},
            {"Name": "Temel", "SelectedTempSource": {"Identifier": "/amdcpu/0/temperature/99"},
             "Points": ["35,25", "55,35", "90,100"],
             "HysteresisConfig": {"ResponseTimeUp": 5, "ResponseTimeDown": 15, "HysteresisValueUp": 3,
                                  "HysteresisValueDown": 3, "IgnoreHysteresisAtLimits": True}},
            {"Name": "Karma", "SelectedFanCurves": [{"Name": "Temel"}, {"Name": "Takip"}], "SelectedMixFunction": 0},
        ],
    },
}


class BleTests(unittest.TestCase):
    def test_packets_and_color_correction(self):
        from lightcore import ble
        self.assertEqual(ble.color_packet((255, 255, 0)).hex(" "), "7e 00 05 03 ff ff 00 00 ef")
        self.assertEqual(ble.correct((255, 255, 0), ble.DEFAULT_GAINS), (255, 128, 0))
        self.assertEqual(ble.correct((10, 10, 10), (2.0, 0.0, 1.0)), (20, 0, 10))
        # eğri en güçlü kanalı korur, karışımdaki zayıf kanalları kısar
        self.assertEqual(ble.correct((244, 63, 94), (1, 1, 1), 2.0), (244, 16, 36))
        self.assertEqual(ble.correct((0, 0, 0), (1, 1, 1), 2.6), (0, 0, 0))
        self.assertEqual(ble.power_packet(True).hex(" "), "7e 00 04 f0 00 01 ff 00 ef")

    def test_power_command_once_per_connection_and_after_idle(self):
        from lightcore.ble import POWER_IDLE, BleStrip, color_packet, power_packet
        s = BleStrip("AA:BB", gains=(1, 1, 1), gamma=1.0)
        self.assertEqual(s._packets((0, 0, 0), 0.0), [color_packet((0, 0, 0))])
        self.assertEqual(s._packets((9, 0, 0), 0.0), [power_packet(True), color_packet((9, 0, 0))])
        s._powered, s._last_write = True, 100.0
        self.assertEqual(s._packets((9, 0, 0), 101.0), [color_packet((9, 0, 0))])  # efekt sürerken yanıp sönmesin
        self.assertEqual(s._packets((9, 0, 0), 100.0 + POWER_IDLE), [power_packet(True), color_packet((9, 0, 0))])

    def _failing_strip(self, **kw):
        from lightcore.ble import BleStrip
        s = BleStrip("AA:BB", **kw)
        s.calls, s.ok = [], False

        async def send(packets):
            s.calls.append(packets)
            if not s.ok:
                raise OSError("yok")

        s._send = send
        return s

    def test_unreachable_strip_gives_up_fast_and_close_does_not_hang(self):
        import time
        from lightcore.ble import BleError
        s = self._failing_strip()
        with self.assertRaises(BleError):
            s.write((1, 2, 3))
        began = time.monotonic()
        with self.assertRaises(BleError):
            s.write((1, 2, 3))  # az önce bağlanamadı: denemeden vazgeçer
        self.assertEqual(len(s.calls), 1)
        self.assertLess(time.monotonic() - began, 0.5)
        s._lock.acquire()  # başka bir yazma takılı kalmış gibi
        began = time.monotonic()
        s.close(timeout=0.2)
        with self.assertRaises(BleError):
            s.write((1, 2, 3), timeout=0.2)
        self.assertLess(time.monotonic() - began, 1.0)
        s._lock.release()
        s.close()

    def test_unreachable_strip_is_retried_in_background_with_last_color(self):
        import time
        from lightcore import ble
        from lightcore.ble import BleError, color_packet
        states = []
        old = ble.RETRY_FIRST
        ble.RETRY_FIRST = 0.05
        try:
            s = self._failing_strip(gains=(1, 1, 1), gamma=1.0, on_change=lambda strip, ok: states.append(ok))
            with self.assertRaises(BleError):
                s.write((1, 2, 3))
            with self.assertRaises(BleError):
                s.write((7, 8, 9))  # hızlı vazgeçer ama son istenen renk bu olur
            s.ok = True
            deadline = time.monotonic() + 3
            while states != [False, True] and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(states, [False, True])
            self.assertEqual(s.calls[-1][-1], color_packet((7, 8, 9)))
            self.assertEqual(s._fails, 0)
            s.close()
        finally:
            ble.RETRY_FIRST = old

    def test_closed_strip_refuses_late_writes(self):
        from lightcore.ble import BleError
        s = self._failing_strip()
        s.ok = True
        s.write((1, 2, 3))
        s.close()
        with self.assertRaises(BleError):
            s.write((4, 5, 6))  # bırakılan şeride geç kalan yazma yeniden bağlanmamalı
        self.assertEqual(len(s.calls), 1)

    def test_retry_delay_backs_off_and_nudge_resets(self):
        from lightcore.ble import RETRY_FIRST, RETRY_MAX, BleStrip
        s = BleStrip("AA:BB")
        s._fails = 1
        self.assertEqual(s.retry_delay(), RETRY_FIRST)
        s._fails = 2
        self.assertEqual(s.retry_delay(), RETRY_FIRST * 2)
        s._fails = 30
        self.assertEqual(s.retry_delay(), RETRY_MAX)
        s.nudge()
        self.assertEqual(s.retry_delay(), RETRY_FIRST)

    def test_sink_swallows_unreachable_strip_and_uses_own_key(self):
        from lightcore.ble import BleSink
        s = self._failing_strip()
        sink = BleSink(s, fixed=(0, 0, 0))
        sink.write((5, 5, 5))  # hata motoru durdurmaz
        self.assertEqual(sink.key, "ble:AA:BB")
        self.assertIsNone(sink._last)
        s.close()

    def test_fixed_sink_writes_own_color_once(self):
        from lightcore.ble import BleSink

        class Fake:
            address = "X"

            def __init__(self):
                self.colors = []

            def nudge(self):
                pass

            def write(self, rgb):
                self.colors.append(rgb)

        strip = Fake()
        sink = BleSink(strip, fixed=(0, 0, 0))
        for rgb in ((1, 1, 1), (2, 2, 2), (3, 3, 3)):
            sink.write(rgb)
        self.assertEqual(strip.colors, [(0, 0, 0)])
        sink = BleSink(strip)
        sink.write((5, 5, 5))
        sink.write((5, 5, 5))
        self.assertEqual(strip.colors[1:], [(5, 5, 5)])

    def test_sink_drives_strip_through_engine(self):
        import threading
        from lightcore import effects as fx
        from lightcore.ble import BleSink
        from lightcore.engine import apply_parallel

        class Fake:
            address = "X"

            def __init__(self):
                self.colors = []

            def nudge(self):
                pass

            def write(self, rgb):
                self.colors.append(rgb)

        strip = Fake()
        apply_parallel({}, [], fx.Static((200, 100, 0)), stop=threading.Event(), sinks=[BleSink(strip)])
        self.assertEqual(strip.colors, [(200, 100, 0)])


class RunnerAlwaysTests(unittest.TestCase):
    def test_cleanup_job_runs_even_when_superseded(self):
        import threading
        from lightcore.ui import Runner
        r = Runner(lambda text: None)
        gate, ran = threading.Event(), []
        r.start(lambda stop: gate.wait(5))
        r.start(lambda stop: ran.append("normal"))
        r.start(lambda stop: ran.append("temizlik"), always=True)
        r.start(lambda stop: ran.append("son"))
        gate.set()
        r.thread.join(5)
        self.assertEqual(ran, ["temizlik", "son"])


class ConfigMigrationTests(unittest.TestCase):
    def test_single_strip_becomes_list_and_keys_follow(self):
        from lightcore.ui import CONFIG_VERSION, migrate_config
        cfg = migrate_config({"version": 1, "disabled": ["ble", "fans"], "device_colors": {"ble": "FF0000"},
                              "ble": {"address": "AA:BB", "name": "ELK", "gains": [1, 0.5, 0.4], "gamma": 2.6}})
        self.assertEqual(cfg["ble"], {"strips": [{"address": "AA:BB", "name": "ELK", "gains": [1, 0.5, 0.4], "gamma": 2.6}]})
        self.assertEqual(cfg["disabled"], ["ble:AA:BB", "fans"])
        self.assertEqual(cfg["device_colors"], {"ble:AA:BB": "FF0000"})
        self.assertEqual(cfg["version"], CONFIG_VERSION)
        self.assertEqual(migrate_config(dict(cfg)), cfg)  # ikinci kez dokunmaz

    def test_report_hides_bluetooth_address(self):
        from lightcore.ui import scrub_report
        self.assertEqual(scrub_report("Device with address AA:BB:CC:00:11:22 was not found, saat 21:45:28"),
                         "Device with address <adres> was not found, saat 21:45:28")


class PawnIOTests(unittest.TestCase):
    def test_missing_driver_key_is_reported(self):
        from lightcore.fans import pawnio_installed
        self.assertFalse(pawnio_installed(r"SYSTEM\CurrentControlSet\Services\LightCoreYokSurucu"))
        self.assertTrue(pawnio_installed(r"SYSTEM\CurrentControlSet\Services\Tcpip"))  # her Windows'ta var


class RgbRecoveryTests(unittest.TestCase):
    def test_recovery_rules(self):
        from lightcore.ui import RGB_RECOVERY_SECONDS, may_recover_rgb
        self.assertTrue(may_recover_rgb(None, 5.0, False, True))
        self.assertFalse(may_recover_rgb(None, 5.0, True, True))    # oyunda dokunulmaz
        self.assertFalse(may_recover_rgb(None, 5.0, False, False))  # kendi OpenRGB'miz değil
        self.assertFalse(may_recover_rgb(0.0, RGB_RECOVERY_SECONDS - 1, False, True))
        self.assertTrue(may_recover_rgb(0.0, RGB_RECOVERY_SECONDS, False, True))


class FanControlImportTests(unittest.TestCase):
    HELLO = {"controls": [{"id": f"/lpc/superio/0/control/{i}", "name": n} for i, n in
                          enumerate(["CPU Fan", "Pump Fan", "System Fan #1", "System Fan #2", "System Fan #3"])]
             + [{"id": "/gpu-nvidia/0/control/1", "name": "GPU Fan 1"}, {"id": "/gpu-nvidia/0/control/2",
                                                                          "name": "GPU Fan 2"}],
             "temps": [{"id": "/amdcpu/0/temperature/2", "name": "Core (Tctl/Tdie)", "kind": "Cpu"}]}

    def test_user_style_file_converts(self):
        from lightcore.fancontrol_import import import_config
        r = import_config(FANCONTROL_SAMPLE, self.HELLO)
        curves = {c["name"]: c for c in r.curves}
        self.assertEqual(curves["Temel"]["points"], [[35.0, 25.0], [55.0, 35.0], [90.0, 100.0]])
        self.assertEqual(curves["Temel"]["source"], "cpu")  # birebir yok: işlemci kısayolu
        self.assertEqual(curves["Temel"]["hysteresis"]["response_down"], 15)
        self.assertEqual(curves["Takip"], {"type": "sync", "name": "Takip", "control": "/lpc/superio/0/control/0",
                                         "offset": 5})
        self.assertEqual(curves["Karma"]["curves"], ["Temel", "Takip"])
        ch = r.channels
        self.assertEqual(ch["/lpc/superio/0/control/0"]["curve"], "Temel")
        self.assertEqual((ch["/lpc/superio/0/control/0"]["step_up"], ch["/lpc/superio/0/control/0"]["stop"]), (5, 10))
        self.assertEqual(ch["/lpc/superio/0/control/3"]["name"], "Arka fan")
        self.assertEqual((ch["/lpc/superio/0/control/4"]["mode"], ch["/lpc/superio/0/control/4"]["value"]),
                         ("fixed", 55.0))
        self.assertEqual(ch["/gpu-nvidia/0/control/1"]["mode"], "auto")  # NVApi control/0 -> GPU Fan 1
        self.assertTrue(any("yok" in w for w in r.warnings))
        self.assertTrue(any("temperature/99" in w for w in r.warnings))

    def test_imported_settings_drive_the_planner(self):
        from lightcore.fancontrol_import import import_config
        from lightcore.fans import FanPlanner
        r = import_config(FANCONTROL_SAMPLE, self.HELLO)
        hello = dict(self.HELLO, controls=[dict(c, kind="SuperIO", hardware="x") for c in self.HELLO["controls"]])
        v = FanPlanner(hello).desired({"curves": r.curves, "channels": r.channels},
                                      {"/amdcpu/0/temperature/2": 55.0}, now=0)
        self.assertEqual(v["/lpc/superio/0/control/0"], 35.0)   # Temel @55°C
        self.assertEqual(v["/lpc/superio/0/control/3"], 40.0)   # Takip = CPU Fan + 5
        self.assertEqual(v["/lpc/superio/0/control/4"], 55.0)   # Elle

    def test_own_backup_round_trip_and_bad_file(self):
        from lightcore.fancontrol_import import export_config, import_config
        cfg = {"curves": [{"type": "flat", "name": "F", "value": 40}],
               "channels": {"/lpc/superio/0/control/0": {"mode": "curve", "curve": "F"}, "/yok": {"mode": "auto"}}}
        r = import_config(export_config(cfg), self.HELLO)
        self.assertEqual(r.curves, cfg["curves"])
        self.assertEqual(list(r.channels), ["/lpc/superio/0/control/0"])
        with self.assertRaises(ValueError):
            import_config({"bir": "şey"}, self.HELLO)


class AutostartTests(unittest.TestCase):
    def test_task_xml(self):
        import xml.dom.minidom
        from lightcore.autostart import task_xml
        xml_text = task_xml(r"D:\Program\rgb & core", elevated=True, user="PC\\Kullanici")
        doc = xml.dom.minidom.parseString(xml_text.replace('encoding="UTF-16"', 'encoding="UTF-8"').encode())
        get = lambda tag: doc.getElementsByTagName(tag)[0].firstChild.nodeValue
        self.assertEqual(get("RunLevel"), "HighestAvailable")
        self.assertEqual(get("ExecutionTimeLimit"), "PT0S")       # 3 günlük sınır kapalı
        self.assertEqual(get("DisallowStartIfOnBatteries"), "false")
        self.assertEqual(get("WorkingDirectory"), r"D:\Program\rgb & core")  # & doğru kaçırıldı
        self.assertIn("app.py", get("Arguments"))
        self.assertIn("LeastPrivilege", task_xml("C:\\x", elevated=False, user="u"))


class ConfigTests(unittest.TestCase):
    def test_existing_config_is_used_as_is(self):
        import json
        from lightcore.ui import load_config
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lightcore_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"hue": {"ip": "1.2.3.4"}}, f)
            self.assertEqual(load_config(path), {"hue": {"ip": "1.2.3.4"}})

    def test_screenshots_saved_in_same_second_get_own_files(self):
        import queue
        from types import SimpleNamespace
        from unittest import mock
        from lightcore.ui_shots import ShotsPage
        with tempfile.TemporaryDirectory() as d:
            events = queue.Queue()
            page = SimpleNamespace(last=SimpleNamespace(png=lambda: b"png"), folder=lambda: d,
                                   app=SimpleNamespace(events=events))
            with mock.patch("lightcore.ui_shots.time.strftime", return_value="Ekran 2026-01-01 10-00-00"),                     mock.patch("lightcore.ui_shots.threading.Thread",
                               lambda target, daemon: SimpleNamespace(start=target)):  # iş aynı iş parçacığında
                for _ in range(3):
                    ShotsPage.save_last(page)
            self.assertEqual(sorted(os.listdir(d)), ["Ekran 2026-01-01 10-00-00 (2).png", "Ekran 2026-01-01 10-00-00 (3).png",
                                                     "Ekran 2026-01-01 10-00-00.png"])

    def test_broken_config_is_kept_aside_not_overwritten(self):
        from lightcore.ui import load_config
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lightcore_config.json")
            self.assertEqual(load_config(path), {})  # ilk açılış
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"fans": {"curves": [1, 2')  # yarıda kesilmiş yazma
            self.assertEqual(load_config(path), {})
            self.assertFalse(os.path.exists(path))
            with open(path + ".bozuk", encoding="utf-8") as f:
                self.assertIn('"fans"', f.read())  # kullanıcının ayarları kaybolmadı

    def test_save_replaces_file_in_one_step(self):
        import json
        from types import SimpleNamespace
        from lightcore.ui import App
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lightcore_config.json")
            app = SimpleNamespace(config={"a": 1}, config_path=path, _saved_text=None, _status=lambda *a: None)
            App._save_config(app)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"a": 1})
            self.assertEqual(os.listdir(d), ["lightcore_config.json"])  # .tmp kalmadı


class LauncherTests(unittest.TestCase):
    def test_find_openrgb_nested_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                find_openrgb(d, fallbacks=())
            sub = os.path.join(d, "OpenRGB Windows 64-bit")
            os.makedirs(sub)
            exe = os.path.join(sub, "OpenRGB.exe")
            open(exe, "w").close()
            self.assertEqual(find_openrgb(d, fallbacks=()), exe)

    def test_find_openrgb_in_parent_when_zip_was_nested(self):
        with tempfile.TemporaryDirectory() as d:
            inner = os.path.join(d, "lightcore_proje", "lightcore_proje")
            os.makedirs(inner)
            os.makedirs(os.path.join(d, "lightcore_proje", "openrgb"))
            exe = os.path.join(d, "lightcore_proje", "openrgb", "OpenRGB.exe")
            open(exe, "w").close()
            self.assertEqual(find_openrgb(os.path.join(inner, "openrgb")), exe)


class EffectTests(unittest.TestCase):
    def test_hold_cycle_holds_then_fades(self):
        c = fx.HoldCycle([(255, 0, 0), (0, 0, 255)], hold=2, fade=1)
        self.assertEqual((c.at(0), c.at(1.9), c.at(3), c.at(4.9)),
                         ((255, 0, 0), (255, 0, 0), (0, 0, 255), (0, 0, 255)))
        self.assertEqual(c.at(2.5), (128, 0, 128))  # geçişin ortası
        self.assertEqual(c.at(6), (255, 0, 0))      # başa döner

    def test_fade_moves_from_old_color_and_becomes_static(self):
        f = fx.Fade((0, 0, 0), fx.Static((200, 100, 0)), brightness=0.5, seconds=1.0)
        self.assertEqual(f.at(0), (0, 0, 0))
        self.assertFalse(f.is_static)
        self.assertEqual(f.at(1.0), (100, 50, 0))
        self.assertTrue(f.is_static)

    def test_soft_effects_stay_in_range(self):
        for eff in (fx.Pulse((255, 0, 0)), fx.Candle((255, 120, 0)),
                    fx.Breathe((0, 0, 255), 2, [(0, 0, 255), (0, 255, 0)]), fx.Rainbow(4, 0.5)):
            for i in range(200):
                self.assertTrue(all(0 <= c <= 255 for c in eff.at(i * 0.05)))

    def test_cycle_needs_two_colors(self):
        with self.assertRaises(ValueError):
            fx.HoldCycle([(1, 2, 3)])

    def test_hex_and_scale(self):
        self.assertEqual(fx.parse_hex("#8B7CFF"), (139, 124, 255))
        self.assertEqual(fx.scale((200, 100, 50), 0.5), (100, 50, 25))
        with self.assertRaises(ValueError):
            fx.parse_hex("12")


class UiPartsTests(unittest.TestCase):
    def test_grid_keeps_all_columns_equal_on_wide_windows(self):
        import tkinter as tk
        from lightcore.ui_fans import CardGrid
        root = tk.Tk()
        try:
            from lightcore.dpi import S
            root.geometry(f"{S(2300)}x400")
            grid = CardGrid(root, min_width=200)
            grid.pack(fill="x")
            cards = [grid.add(tk.Frame(grid, height=20, bg="red")) for _ in range(12)]
            root.update()
            # Windows pencereyi ekrana sığdırır (test makinesinin ekranı küçük olabilir): sütun sayısı gerçek genişlikten
            self.assertEqual(grid._cols, grid.winfo_width() // grid.min_width)
            self.assertGreater(grid._cols, 1)
            widths = {c.winfo_width() for c in cards}
            self.assertLessEqual(max(widths) - min(widths), 1)
            detail = tk.Frame(grid, height=30)
            grid.show_detail(detail, cards[0])
            root.update()
            self.assertEqual(int(detail.grid_info()["row"]), 1)  # ilk kart satırının hemen altında
            self.assertLess(detail.winfo_y(), cards[-1].winfo_y())
            grid.close_detail()
            self.assertIsNone(grid.detail)
        finally:
            root.destroy()

    def test_toggle_edges_are_smoothed(self):
        from lightcore.ui import IOS_GREEN, _toggle_rows
        rows = _toggle_rows(60, 34, 1.0, "#202020")
        colors = set(rows.replace("{", "").replace("}", "").split())
        self.assertIn(IOS_GREEN.upper(), colors)
        self.assertIn("#FFFFFF", colors)
        self.assertGreater(len(colors), 12)  # yalnızca iki üç renk değil, ara tonlar var

    def test_usage_counts_children(self):
        import subprocess
        from lightcore.usage import ProcessUsage
        usage = ProcessUsage()
        child = subprocess.Popen([sys.executable, "-c", "while True: pass"])
        try:
            usage.sample({"c": child})
            time.sleep(0.5)
            cpu, mb = usage.sample({"c": child})
        finally:
            child.kill()
            child.wait()
        self.assertGreater(cpu, 0.5)
        self.assertGreater(mb, 1)
        usage.sample({"c": child})  # bitmiş süreç hata vermez


class CalibrationTests(unittest.TestCase):
    def test_steps_down_to_floor_and_reports(self):
        from lightcore.fans import Calibration
        rpm = {100: 1800, 90: 1600, 80: 1400, 70: 1200, 60: 1000, 50: 800, 40: 600, 30: 0, 25: 0}
        c = Calibration(25, 0.0)
        now, seen = 0.0, []
        value = c.percent
        while value is not None and now < 200:
            seen.append(value)
            now += 2
            value = c.tick(now, rpm[c.percent])
        self.assertEqual(sorted(set(seen), reverse=True), [100, 90, 80, 70, 60, 50, 40, 30, 25])
        r = c.result()
        self.assertEqual((r["max_rpm"], r["min_rpm"], r["min_spin"]), (1800, 600, 40))

    def test_no_tach_gives_no_result(self):
        from lightcore.fans import Calibration
        c = Calibration(60, 0.0)
        now = 0.0
        while c.tick(now, None) is not None and now < 100:
            now += 2
        self.assertIsNone(c.result())
        self.assertEqual(c.steps[-1], 60)  # güvenlik sınırının altı denenmez


class NewEffectTests(unittest.TestCase):
    def test_new_effects_stay_in_range_and_move(self):
        red, blue = (255, 0, 0), (0, 0, 255)
        for eff in (fx.Flow([red, blue]), fx.Fire(), fx.Lava(), fx.Aurora(), fx.Ocean(), fx.Neon(red), fx.Twinkle(red),
                    fx.Storm(), fx.Disco([red, blue]), fx.Disco(), fx.Alarm()):
            colors = {eff.at(i * 0.07) for i in range(1000)}
            self.assertGreater(len(colors), 1, type(eff).__name__)
            self.assertTrue(all(0 <= c <= 255 and isinstance(c, int) for rgb in colors for c in rgb), type(eff).__name__)

    def test_every_mode_and_preset_is_known(self):
        from lightcore.ui import MODE_NOTES, MODES, PRESETS
        self.assertEqual({k for k, _ in MODES}, set(MODE_NOTES))
        for name, preset in PRESETS.items():
            if not preset.get("off"):
                self.assertIn(preset["mode"], MODE_NOTES, name)


class BackupTests(unittest.TestCase):
    STATE = dict(selected=["FF0000"], mode="cycle", brightness=50, speed=3, hold=1.0, fade=2.0, off=False)

    def test_backup_has_settings_but_no_secret(self):
        from lightcore.ui import check_backup, make_backup
        config = {"hue": {"ip": "1.2.3.4", "username": "GIZLI"}, "fans": {"enabled": True, "curves": []},
                  "custom_presets": {"Benim": dict(self.STATE)}, "shots": {"provider": "catbox"}}
        data = make_backup(config, self.STATE, ["FF0000"])
        self.assertNotIn("GIZLI", str(data))
        self.assertNotIn("enabled", data["fans"])
        self.assertEqual(check_backup(data)["custom_presets"]["Benim"]["mode"], "cycle")

    def test_bad_backups_are_refused(self):
        from lightcore.ui import check_backup
        for bad in ([], {"type": "x"}, {"type": "lightcore-backup"},
                    {"type": "lightcore-backup", "lights": dict(self.STATE, selected=["ZZ"])}):
            with self.assertRaises(ValueError):
                check_backup(bad)


class ScreenshotTests(unittest.TestCase):
    def _shot(self):
        from lightcore.screenshot import Shot
        return Shot(0, 0, 3, 2, bytes((1, 2, 3, 0)) * 3 + bytes((4, 5, 6, 0)) * 3)

    def test_rgb_crop_and_dib(self):
        s = self._shot()
        self.assertEqual(s.rgb()[:3], bytes((3, 2, 1)))
        c = s.crop(1, 1, 3, 2)
        self.assertEqual((c.x, c.y, c.w, c.h, c.bgra), (1, 1, 2, 1, bytes((4, 5, 6, 0)) * 2))
        dib = s.dib()
        self.assertEqual(struct.unpack_from("<iih", dib, 4), (3, 2, 1))
        self.assertEqual(struct.unpack_from("<h", dib, 14)[0], 24)
        self.assertEqual(dib[40:52], bytes((4, 5, 6)) * 3 + bytes(3))  # alttan üste, 4 bayta tamamlanmış

    def test_png_is_valid(self):
        import zlib
        png = self._shot().png()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        start = png.index(b"IDAT") + 4
        size = struct.unpack(">I", png[start - 8:start - 4])[0]
        self.assertEqual(zlib.decompress(png[start:start + size])[:4], bytes((0, 3, 2, 1)))

    def test_text_changes_pixels(self):
        from lightcore.screenshot import Shot, draw_annotations
        s = Shot(0, 0, 200, 60, bytes(200 * 60 * 4))
        drawn = draw_annotations(s, [("text", 4, 4, "Deneme", "#FF0000", 30)])
        red = sum(1 for i in range(2, len(drawn.bgra), 4) if drawn.bgra[i] > 200)
        self.assertGreater(red, 50)
        self.assertIs(draw_annotations(s, []), s)

    def test_arrow_and_pen_are_drawn_smooth(self):
        from lightcore.screenshot import Shot, draw_annotations
        s = Shot(0, 0, 120, 120, bytes(120 * 120 * 4))
        for a in (("arrow", 10, 10, 100, 80, "#00FF00", 4), ("pen", [(10, 100), (40, 60), (90, 110)], "#00FF00", 4)):
            greens = {s2 for s2 in draw_annotations(s, [a]).bgra[1::4]}
            self.assertIn(255, greens)
            self.assertGreater(len(greens), 5, a[0])  # ara tonlar: kenar yumuşatılmış
        moved = draw_annotations(s, [("pen", [(50, 50), (60, 50)], "#FF0000", 4)], dx=-50, dy=-50)
        self.assertGreater(max(moved.bgra[2:4 * 120 * 4:4]), 200)  # kırpılmış parçada kaydırma doğru

    def test_gap_between_screens_is_transparent_in_png_and_gray_in_dib(self):
        import zlib
        from lightcore.screenshot import Shot
        s = Shot(0, 0, 4, 2, bytes((9, 9, 9, 0)) * 8, areas=[(0, 0, 2, 2), (2, 0, 4, 1)])  # sağ altta boşluk
        png = s.png()
        self.assertEqual(png[25], 6)  # renk türü: saydamlıklı
        start = png.index(b"IDAT") + 4
        size = struct.unpack(">I", png[start - 8:start - 4])[0]
        raw = zlib.decompress(png[start:start + size])
        rows = [raw[i * 17 + 1:(i + 1) * 17] for i in range(2)]
        self.assertEqual([rows[0][3 + i * 4] for i in range(4)], [255, 255, 255, 255])
        self.assertEqual([rows[1][3 + i * 4] for i in range(4)], [255, 255, 0, 0])
        dib = s.dib()
        self.assertEqual(dib[40:52], bytes((9, 9, 9)) * 2 + bytes((0x20, 0x20, 0x20)) * 2)  # alt satır önce gelir
        c = s.crop(1, 1, 4, 2)
        self.assertEqual(c.areas, [(0, 0, 1, 1)])

    def test_pixelate_hides_detail_in_box_only(self):
        from lightcore.screenshot import Shot, draw_annotations
        pixels = bytes((x % 256, 0, 0, 0)[i] for _y in range(40) for x in range(40) for i in range(4))
        s = Shot(0, 0, 40, 40, pixels)
        out = draw_annotations(s, [("blur", 0, 0, 18, 18)])
        self.assertEqual(len({out.bgra[i * 4] for i in range(18)}), 1)  # kare tek renk
        self.assertEqual(out.bgra[4 * 20:4 * 21], pixels[4 * 20:4 * 21])  # dışına dokunmaz

    def test_key_names(self):
        from lightcore import screenshot as ss
        self.assertTrue(ss.key_name(ss.MOD_CONTROL | ss.MOD_SHIFT, 0x31) == "Ctrl+Shift+1")
        self.assertEqual(ss.key_name(0, ss.VK_SNAPSHOT), "PrtScn")

    def test_upload_form_and_link(self):
        import http.server
        from lightcore import screenshot as ss
        got = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                got["type"] = self.headers["Content-Type"]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"https://ornek.test/abc.png\n")

            def log_message(self, *_a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.handle_request, daemon=True).start()
        link = ss.upload(b"PNGDATA", "custom", {"url": f"http://127.0.0.1:{srv.server_port}/", "field": "dosya"})
        self.assertEqual(link, "https://ornek.test/abc.png")
        self.assertIn(b'name="dosya"', got["body"])
        self.assertIn(b"PNGDATA", got["body"])
        with self.assertRaises(ss.ScreenshotError):
            ss.upload(b"x", "custom", {"url": "ftp://x"})

    def test_upload_reports_server_refusal_and_reads_json_links(self):
        import http.server
        from lightcore import screenshot as ss
        replies = [(412, b"No file!"), (200, b'{"url":"https:\\/\\/ornek.test\\/a.png"}')]

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                code, body = replies.pop(0)
                self.send_response(code)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=lambda: (srv.handle_request(), srv.handle_request()), daemon=True).start()
        custom = {"url": f"http://127.0.0.1:{srv.server_port}/", "field": "f"}
        with self.assertRaises(ss.ScreenshotError) as ctx:
            ss.upload(b"x", "custom", custom)
        self.assertIn("No file!", str(ctx.exception))
        self.assertEqual(ss.upload(b"x", "custom", custom), "https://ornek.test/a.png")

    def test_providers_use_documented_fields(self):
        from lightcore.screenshot import PROVIDERS
        self.assertEqual(set(PROVIDERS), {"tmpfiles", "uguu"})
        self.assertEqual((PROVIDERS["tmpfiles"]["field"], PROVIDERS["uguu"]["field"]), ("file", "files[]"))

    def test_removed_provider_falls_back_to_default(self):
        from lightcore.screenshot import DEFAULT_PROVIDER, provider_key
        self.assertEqual(provider_key("catbox"), DEFAULT_PROVIDER)
        self.assertEqual(provider_key(None), DEFAULT_PROVIDER)
        self.assertEqual((provider_key("uguu"), provider_key("custom")), ("uguu", "custom"))


class GameTests(unittest.TestCase):
    def test_detector_needs_fullscreen_and_load_with_delays(self):
        from lightcore.game import GameDetector
        d = GameDetector()
        self.assertFalse(d.update(0, True, 10))    # tam ekran film: yük düşük
        self.assertFalse(d.update(1, False, 99))   # pencereli yük
        self.assertFalse(d.update(2, True, 80))
        self.assertFalse(d.update(9, True, 80))
        self.assertTrue(d.update(12.5, True, 80))  # 10 sn sonra
        self.assertTrue(d.update(20, False, 0))    # Alt+Tab: hemen kapanmaz
        self.assertTrue(d.update(30, True, 80))    # döndü, sayaç sıfırlandı
        self.assertTrue(d.update(40, False, 0))
        self.assertTrue(d.update(60, False, 0))
        self.assertFalse(d.update(70.5, False, 0))

    def test_game_curve_reaches_full_before_limit(self):
        from lightcore.fans import GAME_CURVE, curve_value
        self.assertEqual(curve_value(GAME_CURVE, 40), 25)
        self.assertEqual(curve_value(GAME_CURVE, 65), 35)
        self.assertLessEqual(curve_value(GAME_CURVE, 80), 60)  # oyunda fanlar uçuşa geçmesin
        self.assertEqual(curve_value(GAME_CURVE, 90), 100)

    def _planner(self):
        from lightcore.fans import FanPlanner
        temps = [{"id": "c", "name": "Core (Tctl/Tdie)", "hardware": "h", "kind": "Cpu"},
                 {"id": "g", "name": "GPU Core", "hardware": "h", "kind": "GpuNvidia"}]
        controls = [{"id": i, "name": n, "hardware": "h", "kind": "SuperIO", "min": 25, "max": 100, "pump": False}
                    for i, n in (("cpu", "CPU Fan"), ("kasa", "System Fan #1"), ("kasa2", "System Fan #2"), ("bios", "Pump Fan"))]
        return FanPlanner({"temps": temps, "controls": controls})

    def test_game_keeps_offsets_and_bios_fans_and_case_follows_hotter(self):
        p = self._planner()
        cfg = {"curves": [{"type": "sync", "name": "Takip", "control": "cpu", "offset": 5},
                          {"type": "flat", "name": "F", "value": 30}],
               "channels": {"cpu": {"mode": "curve", "curve": "F", "name": "CPU Fan"},
                            "kasa": {"mode": "curve", "curve": "F", "name": "Fan A"},
                            "kasa2": {"mode": "curve", "curve": "Takip", "name": "Fan B"},
                            "bios": {"mode": "auto"}}}
        temps = {"c": 40, "g": 72}
        normal = p.desired(cfg, temps, now=0.0)
        self.assertEqual(normal["kasa"], 30.0)
        game = self._planner().desired(cfg, temps, now=0.0, game=True)
        self.assertEqual(game["cpu"], 25.0)   # işlemci fanı işlemciye bakar
        self.assertEqual(game["kasa"], 43.0)  # kasa fanı sıcak ekran kartını izler
        self.assertEqual(game["kasa2"], 48.0) # kendi farkı korunur
        self.assertIsNone(game["bios"])

    def test_game_mode_channel_works_without_game_mode(self):
        p = self._planner()
        cfg = {"channels": {"kasa": {"mode": "game"}}}
        self.assertEqual(p.desired(cfg, {"c": 65, "g": 40}, now=0.0)["kasa"], 35.0)

    def test_sync_follows_real_speed_of_bios_fan(self):
        p = self._planner()
        cfg = {"curves": [{"type": "sync", "name": "Takip", "control": "cpu", "offset": 5}],
               "channels": {"cpu": {"mode": "auto"}, "kasa": {"mode": "curve", "curve": "Takip"}}}
        current = {"cpu": 38.0, "kasa": 50.0}
        self.assertEqual(p.desired(cfg, {"c": 45, "g": 40}, now=0.0, current=current)["kasa"], 43.0)  # %100 değil
        self.assertEqual(self._planner().desired(cfg, {"c": 45, "g": 40}, now=0.0)["kasa"], 100.0)  # hız bilinmiyorsa güvenli

    def test_custom_game_curve_is_used_and_bad_one_falls_back(self):
        from lightcore.fans import GAME_CURVE, game_curve_of
        cfg = {"channels": {"kasa": {"mode": "game"}},
               "game_curve": {"points": [[40, 20], [80, 40]], "hysteresis": {"up": 9}}}
        self.assertEqual(self._planner().desired(cfg, {"c": 60, "g": 40}, now=0.0)["kasa"], 30.0)
        curve = game_curve_of(cfg["game_curve"])
        self.assertEqual(curve["hysteresis"]["up"], 9)
        self.assertIn("response_up", curve["hysteresis"])  # eksikler varsayılandan
        for bad in (None, {}, {"points": [[50, 50]]}, {"points": [["x", 1], [2, 3]]}):
            self.assertEqual(game_curve_of(bad)["points"], [tuple(map(float, p)) for p in GAME_CURVE])

    def test_fan_library_only_from_given_folders(self):
        import tempfile
        from lightcore import fans
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(fans.find_lhm([d]))  # FanControl'ün klasörüne bakılmaz
            open(os.path.join(d, fans.LHM_DLL), "wb").close()
            self.assertEqual(fans.find_lhm([d]), d)

    def test_pawnio_install_reports_result(self):
        import shutil
        import types
        from unittest import mock
        from lightcore import fans
        ok = types.SimpleNamespace(returncode=0, stdout="Successfully installed", stderr="")
        with mock.patch.object(shutil, "which", return_value="winget"), \
                mock.patch.object(fans.subprocess, "run", return_value=ok) as run, \
                mock.patch.object(fans, "pawnio_installed", return_value=True):
            self.assertEqual(fans.install_pawnio(), (True, "PawnIO kuruldu."))
            self.assertIn(fans.PAWNIO_WINGET_ID, run.call_args[0][0])
        bad = types.SimpleNamespace(returncode=5, stdout="", stderr="Access denied")
        with mock.patch.object(shutil, "which", return_value="winget"), \
                mock.patch.object(fans.subprocess, "run", return_value=bad), \
                mock.patch.object(fans, "pawnio_installed", return_value=False):
            done, message = fans.install_pawnio()
            self.assertFalse(done)
            self.assertIn("Access denied", message)

    def test_game_curve_survives_export_import(self):
        from lightcore import fancontrol_import as fcimp
        hello = {"controls": [{"id": "kasa", "name": "System Fan #1"}], "temps": []}
        cfg = {"curves": [], "channels": {"kasa": {"mode": "game"}}, "game_curve": {"points": [[40, 20], [80, 40]]}}
        result = fcimp.import_config(fcimp.export_config(cfg), hello)
        self.assertEqual(result.game_curve, cfg["game_curve"])
        self.assertIsNone(fcimp.import_config(fcimp.export_config({"curves": [], "channels": {}}), hello).game_curve)


class OverrideTests(unittest.TestCase):
    def test_device_keeps_own_color_while_others_follow(self):
        srv = MockServer([pack_device(*RAM), pack_device(*KEYBOARD)])
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        found = cl.list_devices()
        apply(cl, found, fx.Static((1, 2, 3)), once=True, overrides={11: (9, 9, 9)})
        cl.close()
        self.assertEqual(srv.of(1050, 10)[-1][6:10], bytes((1, 2, 3, 0)))
        self.assertEqual(srv.of(1050, 11)[-1][6:10], bytes((9, 9, 9, 0)))


class ReportTests(unittest.TestCase):
    def test_scrub_removes_private_values(self):
        from lightcore.ui import scrub_report
        home = os.path.expanduser("~")
        text = scrub_report(f"{home}\\x 192.0.2.5 anahtar abcdefghijklmnopqrstuvwxyz0123456789 tamam")
        self.assertNotIn(home, text)
        self.assertNotIn("192.0.2.5", text)
        self.assertNotIn("abcdefghijklmnop", text)
        self.assertIn("tamam", text)


@unittest.skipUnless(os.name == "nt", "Windows'un C# derleyicisi gerekir")
class FanRulesTests(unittest.TestCase):
    """FanHost'un güvenlik kuralları (FanRules.cs) donanımsız derlenip denenir."""

    def test_safety_rules(self):
        import subprocess
        from lightcore import fans
        probe = r"""
using System;
static class Probe {
    static void Main() {
        double n = double.NaN;
        Console.WriteLine(string.Join(" ", new object[] {
            FanRules.Emergency(false, 89, 50, 70), FanRules.Emergency(false, 90, 50, 70),
            FanRules.Emergency(false, 50, 85, 70), FanRules.Emergency(false, 50, 50, 100),
            FanRules.Emergency(true, 86, 50, 70), FanRules.Emergency(true, 84.9, 79.9, 94.9),
            FanRules.Emergency(false, n, n, n),
            FanRules.Clamp(10, 0, 100, false), FanRules.Clamp(10, 0, 100, true), FanRules.Clamp(150, 0, 100, false),
            FanRules.Clamp(50, 30, 80, false), FanRules.Clamp(20, 30, 100, false),
            FanRules.Stale(9.9), FanRules.Stale(10.1) }));
    }
}"""
        with tempfile.TemporaryDirectory() as d:
            src, exe = os.path.join(d, "probe.cs"), os.path.join(d, "probe.exe")
            with open(src, "w", encoding="utf-8") as f:
                f.write(probe)
            rules = os.path.join(os.path.dirname(fans.__file__), "FanRules.cs")
            subprocess.run([fans._csc(), "/nologo", f"/out:{exe}", src, rules], check=True, capture_output=True)
            out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(out, ["False", "True",            # işlemci 90°C'de acil durum
                               "True", "True",             # ekran kartı 85°C, sıcak nokta 100°C
                               "True", "False",            # çıkış 5°C aşağıdan: 86'da sürer, sınırların altında biter
                               "False",                    # okunamayan sıcaklık acil durum saymaz
                               "25", "60", "100",          # en az %25, pompa %60, en fazla %100
                               "50", "30",                 # kanalın kendi sınırları
                               "False", "True"])           # 10 sn sinyal yoksa BIOS


class LangTests(unittest.TestCase):
    def test_translation_table_and_patterns(self):
        from lightcore import lang
        self.assertEqual(lang.T("Fanlar"), "Fanlar")  # kapalıyken dokunmaz
        lang._active = True
        try:
            self.assertEqual(lang.T("Fanlar"), "Fans")
            self.assertEqual(lang.T("3 cihaz hazır."), "3 devices ready.")
            self.assertEqual(lang.T("çevirisi olmayan metin"), "çevirisi olmayan metin")
            self.assertEqual(lang.T("54°C → %44"), "54°C → 44%")  # yüzde İngilizce yazımla
            self.assertEqual(lang.T("Program %0.4 · Fan %1.2"), "Program 0.4% · Fan 1.2%")
            self.assertEqual(lang.T("Bağlanılamadı: OpenRGB'ye bağlanılamadı"), "Could not connect: OpenRGB did not respond")
        finally:
            lang._active = False


class DynamicLangTests(unittest.TestCase):
    def test_dynamic_status_messages(self):
        from lightcore import lang
        active = lang._active
        lang._active = True
        try:
            self.assertEqual(lang.T("Açık · 3 fan program tarafından kontrol ediliyor, 2 fan BIOS kontrolünde"),
                             "On · 3 fans controlled by the program, 2 fans controlled by the BIOS")
            self.assertEqual(lang.T("Klavye yanıt vermiyor, OpenRGB yeniden başlatılıyor..."),
                             "Klavye is not responding, restarting OpenRGB...")
            self.assertEqual(lang.T("Sunucu reddetti (HTTP 412): No file!. Başka bir sağlayıcı dene."),
                             "Server refused (HTTP 412): No file!. Try another provider.")
        finally:
            lang._active = active


class IconTests(unittest.TestCase):
    def test_all_icons_render(self):
        from lightcore import icons
        for name in icons.NAMES:
            data = icons.rgba(name, 24)
            self.assertEqual(len(data), 24 * 24 * 4)
            self.assertEqual(data[3], 0)  # köşe saydam
        self.assertEqual(icons.ico_bytes("lightcore", (16,))[:4], bytes((0, 0, 1, 0)))

    def test_mono_and_device_icons_render(self):
        from lightcore import icons
        for data in ([icons.mono_rgba(n, 20, (1, 2, 3)) for n in icons.NAMES]
                     + [icons.device_rgba(k, 20, (1, 2, 3)) for k in icons.DEVICES]):
            self.assertEqual(len(data), 20 * 20 * 4)
            self.assertEqual(data[:4], bytes((1, 2, 3, 0)))  # zeminsiz: köşe saydam, renk verilen renk
            self.assertTrue(any(data[i] > 200 for i in range(3, len(data), 4)))  # bir şey çizilmiş


class SinkPacingTests(unittest.TestCase):
    def test_sink_writes_are_rate_limited_and_settled_color_is_resent(self):
        from lightcore import effects as fx
        from lightcore.engine import _drive_sink

        class Sink:
            interval, key = 0.05, "ble:test"

            def __init__(self):
                self.writes, self.resent = [], []

            def write(self, rgb):
                self.writes.append((time.monotonic(), rgb))

            def resend(self):
                self.resent.append(self.writes[-1][1])

        sink, stop = Sink(), threading.Event()
        worker = threading.Thread(target=_drive_sink, args=(sink, fx.HoldCycle([(255, 0, 0), (0, 0, 255)], 0.3, 0.4), 1.0, stop, None))
        worker.start()
        time.sleep(1.0)
        stop.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        times = [t for t, _rgb in sink.writes]
        self.assertGreaterEqual(len(times), 5)  # 0,4 sn'lik geçişte aralıklı yazmalar
        self.assertTrue(all(b - a >= sink.interval * 0.45 for a, b in zip(times, times[1:])))  # sınırdan sık yazılmaz
        self.assertIn((0, 0, 255), sink.resent)  # geçiş bitince durulan renk yinelenir

    def test_static_color_is_written_once(self):
        from lightcore import effects as fx
        from lightcore.engine import _drive_sink
        calls = []
        sink = type("Sink", (), {"interval": 0.05, "key": "ble:test", "write": lambda s, rgb: calls.append(rgb),
                                 "resend": lambda s: calls.append("resend")})()
        _drive_sink(sink, fx.Static((1, 2, 3)), 1.0, threading.Event(), None)
        self.assertEqual(calls, [(1, 2, 3)])


class DashTests(unittest.TestCase):
    def test_curve_points_by_type(self):
        from lightcore.ui_dash import curve_points
        self.assertEqual(curve_points({"type": "graph", "points": [(40, 30), (80, 100)]}), [(40.0, 30.0), (80.0, 100.0)])
        self.assertEqual(curve_points({"type": "linear", "min_temp": 35, "min_value": 20, "max_temp": 75, "max_value": 90}),
                         [(35.0, 20.0), (75.0, 90.0)])
        self.assertEqual(curve_points({"type": "flat", "value": 60}), [(30.0, 60.0), (100.0, 60.0)])
        self.assertEqual(len(curve_points({"type": "trigger"})), 4)
        self.assertIsNone(curve_points({"type": "sync"}))  # çizilemeyen tür
        self.assertIsNone(curve_points({"type": "graph", "points": []}))

    def test_tail_events_newest_first_and_skips_markers(self):
        import tempfile
        from lightcore.ui_dash import tail_events
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "log.txt")
            self.assertEqual(tail_events(path), [])  # dosya yok
            with open(path, "w", encoding="utf-8") as f:
                f.write("09:00:01 --- LightCore açıldı ---\n09:00:05 ilk\nbozuk satır\n09:01:07 ikinci " + "x" * 200 + "\n")
            events = tail_events(path)
            self.assertEqual([stamp for stamp, _text in events], ["09:01", "09:00"])
            self.assertEqual(events[1][1], "ilk")
            self.assertEqual(len(events[0][1]), 90)  # uzun kayıt kısaltılır
            self.assertEqual(len(tail_events(path, count=1)), 1)

    def test_device_kind(self):
        from types import SimpleNamespace
        from lightcore.ui_dash import device_kind
        self.assertEqual(device_kind({"kind": "fans"}), "fan")
        self.assertEqual(device_kind({"kind": "openrgb", "title": "Kasa Fanları", "device": SimpleNamespace(kind=0)}), "fan")
        self.assertEqual(device_kind({"kind": "hue"}), "bulb")
        self.assertEqual(device_kind({"kind": "ble"}), "strip")
        self.assertEqual(device_kind({"kind": "openrgb", "device": SimpleNamespace(kind=2)}), "gpu")
        self.assertEqual(device_kind({"kind": "openrgb", "device": SimpleNamespace(kind=99)}), "generic")
        self.assertEqual(device_kind({"kind": "openrgb", "device": None}), "generic")

    def test_dashboard_texts_translate(self):
        from lightcore import lang
        active = lang._active
        lang._active = True
        try:
            self.assertEqual(lang.T("Renk geçişi · parlaklık %80 · 6 cihaz yanıyor · oyun modu açık"),
                             "Color cycle · brightness 80% · 6 devices lit · game mode on")
            self.assertEqual(lang.T("Nefes · parlaklık %90"), "Breathe · brightness 90%")
            self.assertEqual(lang.T("Koruma sınırı 85°C · 24°C pay var"), "Safety limit 85°C · 24°C headroom")
            self.assertEqual(lang.T("Pano"), "Dashboard")
        finally:
            lang._active = active


class QuietEngineTests(unittest.TestCase):
    def test_small_changes_are_skipped_but_final_color_is_exact(self):
        slow = fx.HoldCycle([(100, 100, 100), (104, 104, 104)], hold=0.4, fade=0.6)
        srv = run_effect([KEYBOARD], fx.Fade((100, 100, 100), slow, 1.0, 0.01), 1.3, fps=30)
        colors = [tuple(p[6:9]) for p in srv.of(1050)]
        self.assertLess(len(colors), 8)  # 4'lük fark için onlarca kare yazılmaz
        self.assertIn((104, 104, 104), colors)  # bekleme başında tam renk yazıldı

    def test_tray_starts_and_stops(self):
        from lightcore.tray import Tray
        got = []
        tray = Tray(got.append, tip="LightCore test")
        self.assertTrue(tray.hwnd)
        import ctypes
        ctypes.windll.user32.PostMessageW(tray.hwnd, 0x8001, 1, 0x0202)
        time.sleep(0.2)
        self.assertEqual(got, ["show"])
        ctypes.windll.user32.PostMessageW(tray.hwnd, 0x0218, 0x12, 0)
        time.sleep(0.2)
        self.assertEqual(got, ["show", "resumed"])
        tray.stop()
        self.assertFalse(tray._thread.is_alive())


class DpiTests(unittest.TestCase):
    def test_scaling_helper(self):
        from lightcore import dpi
        self.assertEqual(dpi.S((8, 40)), (round(8 * dpi.SCALE), round(40 * dpi.SCALE)))
        self.assertEqual(dpi._scaled({"padx": 10, "fill": "x"}, ("padx",)), {"padx": dpi.S(10), "fill": "x"})


class OptimizationTests(unittest.TestCase):
    def test_uniform_and_mixed_led_packets_are_correct(self):
        from lightcore.openrgb_client import _led_bytes
        self.assertEqual(_led_bytes([(1, 2, 3)] * 3), bytes((1, 2, 3, 0)) * 3)
        self.assertEqual(_led_bytes([(1, 2, 3), (4, 5, 6)]), bytes((1, 2, 3, 0, 4, 5, 6, 0)))
        self.assertEqual(_led_bytes([]), b"")

    def test_static_job_ends_by_itself(self):
        # Sabit renk her yere gidince iş parçacıkları biter: program hiç CPU harcamaz.
        from lightcore.engine import apply_parallel
        srv = MockServer([pack_device(*RAM), pack_device(*KEYBOARD)])
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        found = cl.list_devices()
        fans = FakeFans()
        written = []
        sink = type("Sink", (), {"interval": 0.05, "key": "hue", "write": lambda s, rgb: written.append(rgb)})()
        t = threading.Thread(target=apply_parallel, args=({d.controller_id: cl for d in found}, found,
                                                          fx.Fade((0, 0, 0), fx.Static((9, 8, 7)), 1.0, 0.2)),
                             kwargs={"fps": 30, "fans": fans, "sinks": [sink]}, daemon=True)
        t.start()
        t.join(3)
        self.assertFalse(t.is_alive())
        cl.close()
        self.assertEqual(srv.of(1050, 11)[-1][6:], bytes((9, 8, 7, 0)) * 4)
        self.assertEqual(srv.of(1050, 10)[-1][6:], bytes((9, 8, 7, 0)) * 3)
        self.assertEqual(fans.calls[-1], {1: (9, 8, 7), 2: (9, 8, 7)})
        self.assertEqual(written[-1], (9, 8, 7))

    def test_hold_phase_sends_nothing_and_wakes_for_fade(self):
        c = fx.HoldCycle([(255, 0, 0), (0, 0, 255)], hold=2, fade=1)
        self.assertAlmostEqual(c.steady_for(0.5), 1.5)
        self.assertEqual(c.steady_for(2.5), 0.0)
        self.assertEqual(fx.Rainbow(4).steady_for(1), 0.0)
        fade = fx.Fade((0, 0, 0), c, 1.0, 0.6)
        self.assertEqual(fade.steady_for(0.1), 0.0)  # önce geçiş
        self.assertAlmostEqual(fade.steady_for(1.0), 1.0)
        srv = run_effect([KEYBOARD], c, 0.5, fps=30)
        self.assertEqual(len(srv.of(1050)), 1)  # 0.5 sn beklemede tek yazma

    def test_hold_color_uses_static_mode_and_saves_only_if_supported(self):
        from lightcore.engine import hold_color
        from lightcore.openrgb_client import MODE_FLAG_HAS_MODE_SPECIFIC_COLOR as COLOR, MODE_FLAG_MANUAL_SAVE as SAVE
        srv = MockServer([pack_device(*RAM)])
        srv.start()
        cl = OpenRGBClient(port=srv.port)
        cl.connect()
        d = cl.list_devices()[0]
        static = next(m for m in d.modes if m.name == "Static")
        self.assertFalse(hold_color(cl, d, (1, 2, 3), save=True))  # bayrak yok: Direct'te kalır
        self.assertEqual(srv.of(1101), [])
        static.flags = COLOR
        self.assertFalse(hold_color(cl, d, (1, 2, 3), save=True))
        self.assertEqual((len(srv.of(1101)), srv.of(1102)), (1, []))
        static.flags = COLOR | SAVE
        self.assertFalse(hold_color(cl, d, (1, 2, 3), save=False))  # renk aynı: belleğe yazılmaz
        self.assertEqual(srv.of(1102), [])
        self.assertTrue(hold_color(cl, d, (4, 5, 6), save=True))
        cl.close()
        self.assertEqual(color_of(srv.of(1102)[0]), (4, 5, 6))

    def test_msi_hold_report(self):
        from lightcore.msi_packet import build_config, build_hold
        p = build_hold(None, (1, 2, 3))
        self.assertEqual((len(p), p[0], p[184]), (185, 0x52, 1))
        for off in (0x1F, 0x2A):
            self.assertEqual(tuple(p[off:off + 4]), (0x01, 1, 2, 3))
            self.assertEqual(p[off + 10], 60)
        self.assertEqual(build_hold(None, (1, 2, 3), save=False)[184], 0)
        base = bytes(build_config())
        q = build_hold(base, (9, 9, 9))
        changed = {i for i in range(185) if q[i] != base[i]}
        self.assertTrue(changed <= set(range(0x1F, 0x1F + 11)) | set(range(0x2A, 0x2A + 11)) | {184})

    def test_autostart_starts_minimized(self):
        from lightcore.autostart import launch_command
        self.assertIn("--minimized", launch_command("C:\\x")[1])


if __name__ == "__main__":
    unittest.main()
