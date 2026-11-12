# audio-default.ps1: what plays where on skippy-pc, and who moves the default. Windows PLAYBACK devices, no elevation needed (2026-11-04,
# found with it: Sunshine held the default on "Steam Streaming Speakers" for as long as the bedroom kiosk's stream lived, core/README.md).
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/windows/audio-default.ps1 <action> [arg] [-Seconds N]
#   list [all]      every playback endpoint: state, volume, mute, which roles it is the default for (C console/games, M multimedia, V communications)
#   get             the default for each role
#   set <name|id>   make the ONE active endpoint whose name contains <name> (or whose id is <id>) the default (-Role all|console|multimedia|comm)
#   watch           print a line whenever the default changes (-Seconds N, -IntervalMs N): the way to catch who moves it
#   peaks           the loudest peak each endpoint showed over -Seconds N (is anything reaching that device?). It reads the signal BEFORE the
#                   device's volume: at 20% volume the meter still reads full, while Sunshine's loopback (the TV) gets 24 dB less
#   sessions        every program with an audio session: which device it plays to, its own volume/mute, its peak over -Seconds N
#                   (a game that opened its sound while the default was elsewhere shows up here, silent on the wrong device)
#   volume <name>   read an active device's master volume; with -Percent N set it (says before and after)
param(
  [Parameter(Position = 0)][ValidateSet('list', 'get', 'set', 'watch', 'peaks', 'sessions', 'volume')][string]$Action = 'list',
  [Parameter(Position = 1)][string]$Arg = '',
  [int]$Seconds = 10,
  [int]$IntervalMs = 250,
  [ValidateSet('all', 'console', 'multimedia', 'comm')][string]$Role = 'all',
  [int]$Percent = -1
)
$ErrorActionPreference = 'Stop'

$src = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace SkAudio
{
    [StructLayout(LayoutKind.Sequential)]
    public struct PropertyKey { public Guid fmtid; public int pid; }

    [StructLayout(LayoutKind.Explicit, Size = 32)]
    public struct PropVariant
    {
        [FieldOffset(0)] public short vt;
        [FieldOffset(8)] public IntPtr ptr;
    }

    [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    public class MMDeviceEnumeratorCom { }

    [ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
    public class PolicyConfigClient { }

    [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IMMDeviceEnumerator
    {
        [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
        [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
        [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
        [PreserveSig] int RegisterEndpointNotificationCallback(IntPtr client);
        [PreserveSig] int UnregisterEndpointNotificationCallback(IntPtr client);
    }

    [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IMMDeviceCollection
    {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int Item(int index, out IMMDevice device);
    }

    [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IMMDevice
    {
        [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object iface);
        [PreserveSig] int OpenPropertyStore(int access, out IPropertyStore props);
        [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
        [PreserveSig] int GetState(out int state);
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore
    {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int GetAt(int index, out PropertyKey key);
        [PreserveSig] int GetValue(ref PropertyKey key, out PropVariant value);
        [PreserveSig] int SetValue(ref PropertyKey key, ref PropVariant value);
        [PreserveSig] int Commit();
    }

    [ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAudioEndpointVolume
    {
        [PreserveSig] int RegisterControlChangeNotify(IntPtr notify);
        [PreserveSig] int UnregisterControlChangeNotify(IntPtr notify);
        [PreserveSig] int GetChannelCount(out int count);
        [PreserveSig] int SetMasterVolumeLevel(float level, ref Guid ctx);
        [PreserveSig] int SetMasterVolumeLevelScalar(float level, ref Guid ctx);
        [PreserveSig] int GetMasterVolumeLevel(out float level);
        [PreserveSig] int GetMasterVolumeLevelScalar(out float level);
        [PreserveSig] int SetChannelVolumeLevel(int channel, float level, ref Guid ctx);
        [PreserveSig] int SetChannelVolumeLevelScalar(int channel, float level, ref Guid ctx);
        [PreserveSig] int GetChannelVolumeLevel(int channel, out float level);
        [PreserveSig] int GetChannelVolumeLevelScalar(int channel, out float level);
        [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid ctx);
        [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
    }

    [ComImport, Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAudioMeterInformation
    {
        [PreserveSig] int GetPeakValue(out float peak);
        [PreserveSig] int GetMeteringChannelCount(out int count);
    }

    [ComImport, Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAudioSessionManager2
    {
        [PreserveSig] int GetAudioSessionControl(IntPtr audioSessionGuid, int streamFlags, out IAudioSessionControl2 sessionControl);
        [PreserveSig] int GetSimpleAudioVolume(IntPtr audioSessionGuid, int streamFlags, out IntPtr audioVolume);
        [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator sessionEnum);
        [PreserveSig] int RegisterSessionNotification(IntPtr newSession);
        [PreserveSig] int UnregisterSessionNotification(IntPtr newSession);
        [PreserveSig] int RegisterDuckNotification(IntPtr sessionId, IntPtr duckNotification);
        [PreserveSig] int UnregisterDuckNotification(IntPtr duckNotification);
    }

    [ComImport, Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAudioSessionEnumerator
    {
        [PreserveSig] int GetCount(out int count);
        [PreserveSig] int GetSession(int index, out IAudioSessionControl2 session);
    }

    [ComImport, Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IAudioSessionControl2
    {
        [PreserveSig] int GetState(out int state);
        [PreserveSig] int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        [PreserveSig] int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string name, ref Guid ctx);
        [PreserveSig] int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string path);
        [PreserveSig] int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string path, ref Guid ctx);
        [PreserveSig] int GetGroupingParam(out Guid g);
        [PreserveSig] int SetGroupingParam(ref Guid g, ref Guid ctx);
        [PreserveSig] int RegisterAudioSessionNotification(IntPtr n);
        [PreserveSig] int UnregisterAudioSessionNotification(IntPtr n);
        [PreserveSig] int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string id);
        [PreserveSig] int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string id);
        [PreserveSig] int GetProcessId(out int pid);
        [PreserveSig] int IsSystemSoundsSession();
        [PreserveSig] int SetDuckingPreference([MarshalAs(UnmanagedType.Bool)] bool optOut);
    }

    [ComImport, Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface ISimpleAudioVolume
    {
        [PreserveSig] int SetMasterVolume(float level, ref Guid ctx);
        [PreserveSig] int GetMasterVolume(out float level);
        [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, ref Guid ctx);
        [PreserveSig] int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
    }

    [ComImport, Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPolicyConfig
    {
        [PreserveSig] int GetMixFormat([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr ppFormat);
        [PreserveSig] int GetDeviceFormat([MarshalAs(UnmanagedType.LPWStr)] string name, int bDefault, IntPtr ppFormat);
        [PreserveSig] int ResetDeviceFormat([MarshalAs(UnmanagedType.LPWStr)] string name);
        [PreserveSig] int SetDeviceFormat([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr endpointFormat, IntPtr mixFormat);
        [PreserveSig] int GetProcessingPeriod([MarshalAs(UnmanagedType.LPWStr)] string name, int bDefault, IntPtr defaultPeriod, IntPtr minimumPeriod);
        [PreserveSig] int SetProcessingPeriod([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr period);
        [PreserveSig] int GetShareMode([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr mode);
        [PreserveSig] int SetShareMode([MarshalAs(UnmanagedType.LPWStr)] string name, IntPtr mode);
        [PreserveSig] int GetPropertyValue([MarshalAs(UnmanagedType.LPWStr)] string name, int bFx, ref PropertyKey key, IntPtr pv);
        [PreserveSig] int SetPropertyValue([MarshalAs(UnmanagedType.LPWStr)] string name, int bFx, ref PropertyKey key, IntPtr pv);
        [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string name, int role);
        [PreserveSig] int SetEndpointVisibility([MarshalAs(UnmanagedType.LPWStr)] string name, int bVisible);
    }

    public class Endpoint
    {
        public string Id; public string Name; public int State;
        public bool IsConsole; public bool IsMultimedia; public bool IsComm;
        public float Volume = -1; public bool Muted;
    }

    public static class Audio
    {
        [DllImport("ole32.dll")] static extern int PropVariantClear(ref PropVariant pv);
        static readonly Guid IID_Volume = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
        static readonly Guid IID_Meter = new Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064");
        const int CLSCTX_ALL = 23;

        static IMMDeviceEnumerator NewEnum() { return (IMMDeviceEnumerator)new MMDeviceEnumeratorCom(); }

        static string StateName(int s)
        {
            if (s == 1) return "active";
            if (s == 2) return "disabled";
            if (s == 4) return "notpresent";
            if (s == 8) return "unplugged";
            return "state" + s;
        }

        static string FriendlyName(IMMDevice dev)
        {
            IPropertyStore ps;
            if (dev.OpenPropertyStore(0, out ps) != 0) return "?";
            PropertyKey k = new PropertyKey();
            k.fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0");
            k.pid = 14;
            PropVariant pv;
            if (ps.GetValue(ref k, out pv) != 0) return "?";
            string s = pv.vt == 31 ? Marshal.PtrToStringUni(pv.ptr) : "?";
            PropVariantClear(ref pv);
            return s;
        }

        static string DefaultId(IMMDeviceEnumerator en, int role)
        {
            IMMDevice d;
            if (en.GetDefaultAudioEndpoint(0, role, out d) != 0 || d == null) return "";
            string id; d.GetId(out id); return id;
        }

        public static List<Endpoint> Endpoints(bool allStates)
        {
            IMMDeviceEnumerator en = NewEnum();
            IMMDeviceCollection col;
            int hr = en.EnumAudioEndpoints(0, allStates ? 0xF : 1, out col);
            if (hr != 0) throw new Exception("EnumAudioEndpoints failed 0x" + hr.ToString("X"));
            int n; col.GetCount(out n);
            string d0 = DefaultId(en, 0), d1 = DefaultId(en, 1), d2 = DefaultId(en, 2);
            List<Endpoint> res = new List<Endpoint>();
            for (int i = 0; i < n; i++)
            {
                IMMDevice dev; col.Item(i, out dev);
                Endpoint e = new Endpoint();
                dev.GetId(out e.Id);
                dev.GetState(out e.State);
                e.Name = FriendlyName(dev);
                e.IsConsole = e.Id == d0; e.IsMultimedia = e.Id == d1; e.IsComm = e.Id == d2;
                if (e.State == 1)
                {
                    try
                    {
                        Guid iid = IID_Volume; object o;
                        if (dev.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out o) == 0)
                        {
                            IAudioEndpointVolume v = (IAudioEndpointVolume)o;
                            float f; v.GetMasterVolumeLevelScalar(out f); e.Volume = f;
                            bool m; v.GetMute(out m); e.Muted = m;
                        }
                    }
                    catch (Exception) { }
                }
                res.Add(e);
            }
            return res;
        }

        public static string List(bool showAll)
        {
            StringBuilder sb = new StringBuilder();
            foreach (Endpoint e in Endpoints(true))
            {
                if (e.State == 4 && !showAll) continue;
                string roles = (e.IsConsole ? "C" : "-") + (e.IsMultimedia ? "M" : "-") + (e.IsComm ? "V" : "-");
                string vol = e.Volume < 0 ? "    " : ((int)Math.Round(e.Volume * 100)).ToString().PadLeft(3) + "%";
                sb.AppendLine(roles + "  " + StateName(e.State).PadRight(10) + vol + (e.Muted ? " MUTED" : "      ") + "  " + e.Name + "   " + e.Id);
            }
            sb.Append("(C = default for console/games, M = multimedia, V = communications)");
            return sb.ToString();
        }

        static string NameOf(IMMDeviceEnumerator en, int role)
        {
            IMMDevice d;
            if (en.GetDefaultAudioEndpoint(0, role, out d) != 0 || d == null) return "(none)";
            return FriendlyName(d);
        }

        public static string Defaults()
        {
            IMMDeviceEnumerator en = NewEnum();
            return "console=" + NameOf(en, 0) + " | multimedia=" + NameOf(en, 1) + " | communications=" + NameOf(en, 2);
        }

        public static string SetDefault(string idOrName, string which)
        {
            Endpoint pick = null; int hits = 0;
            foreach (Endpoint e in Endpoints(false))
            {
                if (string.Equals(e.Id, idOrName, StringComparison.OrdinalIgnoreCase)) { pick = e; hits = 1; break; }
                if (e.Name.IndexOf(idOrName, StringComparison.OrdinalIgnoreCase) >= 0) { pick = e; hits++; }
            }
            if (hits == 0) throw new Exception("no ACTIVE playback device matches '" + idOrName + "'");
            if (hits > 1) throw new Exception("more than one active playback device matches '" + idOrName + "': be more specific");
            IPolicyConfig pc = (IPolicyConfig)new PolicyConfigClient();
            for (int role = 0; role < 3; role++)
            {
                if (which != "all" && !((which == "console" && role == 0) || (which == "multimedia" && role == 1) || (which == "comm" && role == 2))) continue;
                int hr = pc.SetDefaultEndpoint(pick.Id, role);
                if (hr != 0) throw new Exception("SetDefaultEndpoint role " + role + " failed 0x" + hr.ToString("X"));
            }
            return pick.Name;
        }

        // read (percent < 0) or set the master volume of the ONE active playback device matching <idOrName>; says before and after
        public static string Volume(string idOrName, int percent)
        {
            IMMDeviceEnumerator en = NewEnum();
            IMMDeviceCollection col;
            en.EnumAudioEndpoints(0, 1, out col);
            int n; col.GetCount(out n);
            IMMDevice found = null; string fname = null; int hits = 0;
            for (int i = 0; i < n; i++)
            {
                IMMDevice dev; col.Item(i, out dev);
                string id; dev.GetId(out id);
                string name = FriendlyName(dev);
                if (string.Equals(id, idOrName, StringComparison.OrdinalIgnoreCase)) { found = dev; fname = name; hits = 1; break; }
                if (name.IndexOf(idOrName, StringComparison.OrdinalIgnoreCase) >= 0) { found = dev; fname = name; hits++; }
            }
            if (hits == 0) throw new Exception("no ACTIVE playback device matches '" + idOrName + "'");
            if (hits > 1) throw new Exception("more than one active playback device matches '" + idOrName + "': be more specific");
            Guid iid = IID_Volume; object o;
            if (found.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out o) != 0) throw new Exception("cannot open the endpoint volume");
            IAudioEndpointVolume v = (IAudioEndpointVolume)o;
            float before; v.GetMasterVolumeLevelScalar(out before);
            if (percent >= 0)
            {
                Guid g = Guid.Empty;
                int hr = v.SetMasterVolumeLevelScalar(percent / 100f, ref g);
                if (hr != 0) throw new Exception("SetMasterVolumeLevelScalar failed 0x" + hr.ToString("X"));
            }
            float after; v.GetMasterVolumeLevelScalar(out after);
            return fname + ": volume " + ((int)Math.Round(before * 100)) + "% -> " + ((int)Math.Round(after * 100)) + "%";
        }

        public static void Watch(int seconds, int intervalMs)
        {
            IMMDeviceEnumerator en = NewEnum();
            string last = null;
            DateTime end = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < end)
            {
                string cur = "console=" + NameOf(en, 0) + " | multimedia=" + NameOf(en, 1) + " | communications=" + NameOf(en, 2);
                if (cur != last)
                {
                    Console.WriteLine(DateTime.Now.ToString("HH:mm:ss.fff") + "  " + cur);
                    Console.Out.Flush();
                    last = cur;
                }
                Thread.Sleep(intervalMs);
            }
        }

        // every program that has an audio session on an active playback device: which device, its own volume/mute, and the loudest peak over <seconds>
        public static string Sessions(int seconds)
        {
            IMMDeviceEnumerator en = NewEnum();
            IMMDeviceCollection col;
            en.EnumAudioEndpoints(0, 1, out col);
            int n; col.GetCount(out n);
            Guid iidMgr = new Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F");
            List<string> label = new List<string>();
            List<IAudioMeterInformation> meters = new List<IAudioMeterInformation>();
            for (int i = 0; i < n; i++)
            {
                IMMDevice dev; col.Item(i, out dev);
                string dname = FriendlyName(dev);
                object o;
                if (dev.Activate(ref iidMgr, CLSCTX_ALL, IntPtr.Zero, out o) != 0) continue;
                IAudioSessionManager2 mgr = (IAudioSessionManager2)o;
                IAudioSessionEnumerator se;
                if (mgr.GetSessionEnumerator(out se) != 0) continue;
                int sc; se.GetCount(out sc);
                for (int j = 0; j < sc; j++)
                {
                    IAudioSessionControl2 ctl;
                    if (se.GetSession(j, out ctl) != 0) continue;
                    int pid; ctl.GetProcessId(out pid);
                    int st; ctl.GetState(out st);
                    string pname = pid == 0 ? "SystemSounds" : "pid" + pid;
                    try { if (pid != 0) pname = System.Diagnostics.Process.GetProcessById(pid).ProcessName + "(" + pid + ")"; } catch (Exception) { }
                    float v = -1; bool m = false;
                    ISimpleAudioVolume sv = ctl as ISimpleAudioVolume;
                    if (sv != null) { sv.GetMasterVolume(out v); sv.GetMute(out m); }
                    string vol = v < 0 ? "?" : ((int)Math.Round(v * 100)).ToString() + "%";
                    label.Add(pname + "  state=" + (st == 1 ? "ACTIVE" : (st == 0 ? "inactive" : "expired")) + "  app-vol=" + vol + (m ? " MUTED" : "") + "   on  " + dname);
                    meters.Add(ctl as IAudioMeterInformation);
                }
            }
            float[] max = new float[meters.Count];
            DateTime end = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < end)
            {
                for (int i = 0; i < meters.Count; i++)
                {
                    float p;
                    if (meters[i] != null && meters[i].GetPeakValue(out p) == 0 && p > max[i]) max[i] = p;
                }
                Thread.Sleep(40);
            }
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < meters.Count; i++)
                sb.AppendLine("peak " + max[i].ToString("0.000") + (max[i] > 0.001 ? " <== SOUND" : "          ") + "  " + label[i]);
            if (meters.Count == 0) sb.AppendLine("(no audio sessions on any active playback device)");
            return sb.ToString().TrimEnd();
        }

        public static string Peaks(int seconds, int intervalMs)
        {
            IMMDeviceEnumerator en = NewEnum();
            IMMDeviceCollection col;
            en.EnumAudioEndpoints(0, 1, out col);
            int n; col.GetCount(out n);
            List<string> names = new List<string>();
            List<IAudioMeterInformation> meters = new List<IAudioMeterInformation>();
            for (int i = 0; i < n; i++)
            {
                IMMDevice dev; col.Item(i, out dev);
                Guid iid = IID_Meter; object o;
                if (dev.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out o) == 0)
                {
                    names.Add(FriendlyName(dev));
                    meters.Add((IAudioMeterInformation)o);
                }
            }
            float[] max = new float[meters.Count];
            DateTime end = DateTime.UtcNow.AddSeconds(seconds);
            while (DateTime.UtcNow < end)
            {
                for (int i = 0; i < meters.Count; i++)
                {
                    float p; if (meters[i].GetPeakValue(out p) == 0 && p > max[i]) max[i] = p;
                }
                Thread.Sleep(intervalMs);
            }
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < meters.Count; i++)
                sb.AppendLine("peak " + max[i].ToString("0.000") + (max[i] > 0.001 ? "  <== SOUND" : "") + "   " + names[i]);
            return sb.ToString().TrimEnd();
        }
    }
}
'@

Add-Type -TypeDefinition $src -Language CSharp

switch ($Action) {
  'list'  { [SkAudio.Audio]::List($Arg -eq 'all') }
  'get'   { [SkAudio.Audio]::Defaults() }
  'set'   {
    if (-not $Arg) { throw 'set needs a device name (or id)' }
    'default (' + $Role + ') is now: ' + [SkAudio.Audio]::SetDefault($Arg, $Role)
    [SkAudio.Audio]::Defaults()
  }
  'watch' { [SkAudio.Audio]::Watch($Seconds, $IntervalMs) }
  'peaks' { [SkAudio.Audio]::Peaks($Seconds, 40) }
  'sessions' { [SkAudio.Audio]::Sessions($Seconds) }
  'volume' {
    if (-not $Arg) { throw 'volume needs a device name (or id); add -Percent N to set it' }
    [SkAudio.Audio]::Volume($Arg, $Percent)
  }
}
