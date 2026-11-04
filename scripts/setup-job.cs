using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

// Only explicitly attached installer children (and their future children)
// belong to this job. Never enumerate processes by name or parent PID.
public sealed class IvrSetupJob : IDisposable {
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long perProcess, perJob;
        public uint flags;
        public UIntPtr minWorking, maxWorking;
        public uint activeProcesses;
        public UIntPtr affinity;
        public uint priority, scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct Io {
        public ulong readOps, writeOps, otherOps, readBytes, writeBytes, otherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct Extended {
        public Basic basic; public Io io;
        public UIntPtr processMemory, jobMemory, peakProcess, peakJob;
    }
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref Extended info, uint size);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    IntPtr handle;
    public IvrSetupJob() {
        handle=CreateJobObject(IntPtr.Zero,null);
        if(handle==IntPtr.Zero) throw new Win32Exception();
        var info=new Extended(); info.basic.flags=0x2000; // KILL_ON_JOB_CLOSE
        if(!SetInformationJobObject(handle,9,ref info,(uint)Marshal.SizeOf(info))) {
            int error=Marshal.GetLastWin32Error();Dispose();throw new Win32Exception(error);
        }
    }
    public void Add(IntPtr process) {
        if(!AssignProcessToJobObject(handle,process)) throw new Win32Exception();
    }
    public void Dispose() { if(handle!=IntPtr.Zero){CloseHandle(handle);handle=IntPtr.Zero;} }
}
