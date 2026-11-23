// E33.1 user-mode ETW marker provider for absolute timestamp correlation.
// Compiled inline by run-e33.ps1 via Add-Type.  A single fixed GUID is used
// so the marker events can be enabled by the wpr profile (.wprp).
using System;
using System.Runtime.InteropServices;

public static class E33Marker
{
    // {E33C0DE1-0000-4000-8000-00E33C0DE100}
    private static Guid _providerId =
        new Guid("e33c0de1-0000-4000-8000-00e33c0de100");

    [StructLayout(LayoutKind.Sequential)]
    private struct EVENT_DESCRIPTOR
    {
        public ushort Id; public byte Version; public byte Channel;
        public byte Level; public byte Opcode; public ushort Task; public ulong Keyword;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct EVENT_DATA_DESCRIPTOR
    {
        public ulong Ptr; public uint Size; public uint Reserved;
    }

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode)]
    private static extern uint EventRegister(ref Guid providerId, IntPtr enableCallback, IntPtr callbackContext, out ulong registrationHandle);

    [DllImport("advapi32.dll")]
    private static extern uint EventUnregister(ulong registrationHandle);

    [DllImport("advapi32.dll")]
    private static extern uint EventWrite(ulong registrationHandle, ref EVENT_DESCRIPTOR eventDescriptor, uint userDataCount, EVENT_DATA_DESCRIPTOR[] userData);

    [DllImport("kernel32.dll")]
    public static extern void GetSystemTimePreciseAsFileTime(out long fileTime);

    private static ulong _reg;
    private static bool _registered;

    public static bool Register()
    {
        if (_registered) return true;
        ulong h;
        uint rc = EventRegister(ref _providerId, IntPtr.Zero, IntPtr.Zero, out h);
        if (rc != 0) return false;
        _reg = h;
        _registered = true;
        return true;
    }

    public static void Unregister()
    {
        if (_registered) { EventUnregister(_reg); _registered = false; }
    }

    /// <summary>Emit a marker with op id, two 64-bit payload values (FILETIME + thread id) and a string token.</summary>
    public static long WriteMarker(ushort id, string token)
    {
        long ft;
        GetSystemTimePreciseAsFileTime(out ft);

        if (!_registered) return ft;
        var desc = new EVENT_DESCRIPTOR {
            Id = id, Version = 1, Channel = 11, Level = 5, Opcode = 0,
            Task = 1, Keyword = 0x8000000000000000UL
        };
        var tokenBytes = System.Text.Encoding.Unicode.GetBytes(token ?? "");
        GCHandle pinToken = GCHandle.Alloc(tokenBytes, GCHandleType.Pinned);
        try
        {
            var data = new EVENT_DATA_DESCRIPTOR[3];
            data[0].Ptr = (ulong)pinToken.AddrOfPinnedObject().ToInt64();
            data[0].Size = (uint)tokenBytes.Length;
            data[1].Ptr = (ulong)Marshal.UnsafeAddrOfPinnedArrayElement(BitConverter.GetBytes(ft), 0).ToInt64();
            data[1].Size = 8;
            long tid = System.Threading.Thread.CurrentThread.ManagedThreadId;
            var tidBytes = BitConverter.GetBytes(tid);
            GCHandle pinTid = GCHandle.Alloc(tidBytes, GCHandleType.Pinned);
            try
            {
                data[2].Ptr = (ulong)pinTid.AddrOfPinnedObject().ToInt64();
                data[2].Size = 8;
                EventWrite(_reg, ref desc, 3, data);
            }
            finally { pinTid.Free(); }
        }
        finally { pinToken.Free(); }
        return ft;
    }
}
