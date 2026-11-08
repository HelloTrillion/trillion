//! Windows' stand-ins for process groups and signals (SUR-3c).
//!
//! The server goes in a Job Object that kills everything in it when its last handle
//! closes: when this app quits, crashes or is ended from Task Manager, `uv`, the
//! Python under it and anything they started go with it. It is started suspended and
//! only let run once it is in the job, so nothing it starts can slip out first.

use std::os::windows::io::AsRawHandle;
use std::process::Child;

use windows_sys::Win32::Foundation::{CloseHandle, FALSE, FILETIME, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{
    GetExitCodeProcess, GetProcessTimes, OpenProcess, OpenThread, ResumeThread, TerminateProcess,
    PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE, THREAD_SUSPEND_RESUME,
};

/// Seconds between 1601-01-01 (FILETIME's epoch) and 1970-01-01.
const EPOCH_GAP_S: i64 = 11_644_473_600;
/// What GetExitCodeProcess says of a process still running.
const STILL_ACTIVE: u32 = 259;

/// A handle closed when it is dropped.
struct Owned(HANDLE);

impl Owned {
    fn new(handle: HANDLE) -> Option<Owned> {
        (!handle.is_null() && handle != INVALID_HANDLE_VALUE).then_some(Owned(handle))
    }
}

impl Drop for Owned {
    fn drop(&mut self) {
        // SAFETY: a handle this module opened and nothing else closes.
        unsafe { CloseHandle(self.0) };
    }
}

/// The Job Object a server runs in. Dropping it ends what is still inside.
pub struct Job(Owned);

// SAFETY: a job handle may be used from any thread.
unsafe impl Send for Job {}
// SAFETY: as above; the job's functions are thread-safe.
unsafe impl Sync for Job {}

impl Job {
    /// Puts a child started with CREATE_SUSPENDED in a new job that kills it with
    /// this app, then lets it run.
    pub fn adopt(child: &Child) -> std::io::Result<Job> {
        // SAFETY: no attributes, no name: a new anonymous job.
        let job = Owned::new(unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) })
            .ok_or_else(std::io::Error::last_os_error)?;
        // SAFETY: a plain-data struct; all zero is "no limits".
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: the struct and its size, as the information class expects.
        let set = unsafe {
            SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if set == FALSE {
            return Err(std::io::Error::last_os_error());
        }
        // SAFETY: the child's own process handle, alive while `child` is.
        if unsafe { AssignProcessToJobObject(job.0, child.as_raw_handle() as HANDLE) } == FALSE {
            return Err(std::io::Error::last_os_error());
        }
        resume(child.id())?;
        Ok(Job(job))
    }

    /// Ends every process in the job.
    pub fn terminate(&self) {
        // SAFETY: the job this struct owns.
        unsafe { TerminateJobObject(self.0 .0, 1) };
    }
}

/// Lets every thread of a process started suspended run.
fn resume(pid: u32) -> std::io::Result<()> {
    // SAFETY: a snapshot of every thread on the system, closed when dropped.
    let snapshot = Owned::new(unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) })
        .ok_or_else(std::io::Error::last_os_error)?;
    // SAFETY: a plain-data struct, its size set as Thread32First requires.
    let mut entry: THREADENTRY32 = unsafe { std::mem::zeroed() };
    entry.dwSize = std::mem::size_of::<THREADENTRY32>() as u32;
    let mut resumed = 0;
    // SAFETY: the snapshot and the entry above.
    let mut more = unsafe { Thread32First(snapshot.0, &mut entry) } != FALSE;
    while more {
        if entry.th32OwnerProcessID == pid {
            // SAFETY: a thread of the child this app just started.
            if let Some(thread) =
                Owned::new(unsafe { OpenThread(THREAD_SUSPEND_RESUME, FALSE, entry.th32ThreadID) })
            {
                // SAFETY: the thread opened above.
                if unsafe { ResumeThread(thread.0) } != u32::MAX {
                    resumed += 1;
                }
            }
        }
        // SAFETY: as Thread32First.
        more = unsafe { Thread32Next(snapshot.0, &mut entry) } != FALSE;
    }
    if resumed == 0 {
        return Err(std::io::Error::other(
            "the server's process could not be started",
        ));
    }
    Ok(())
}

fn open(pid: i32, access: u32) -> Option<Owned> {
    let pid = u32::try_from(pid).ok().filter(|p| *p > 0)?;
    // SAFETY: only opens a handle; None when the process is gone or not ours to see.
    Owned::new(unsafe { OpenProcess(access, FALSE, pid) })
}

/// When a process started, in seconds since the epoch.
pub fn start_time(pid: i32) -> Option<i64> {
    let process = open(pid, PROCESS_QUERY_LIMITED_INFORMATION)?;
    let zero = FILETIME {
        dwLowDateTime: 0,
        dwHighDateTime: 0,
    };
    let (mut created, mut exited, mut kernel, mut user) = (zero, zero, zero, zero);
    // SAFETY: four FILETIMEs for the four outputs.
    let ok =
        unsafe { GetProcessTimes(process.0, &mut created, &mut exited, &mut kernel, &mut user) };
    if ok == FALSE {
        return None;
    }
    let ticks = (u64::from(created.dwHighDateTime) << 32) | u64::from(created.dwLowDateTime);
    Some((ticks / 10_000_000) as i64 - EPOCH_GAP_S)
}

/// Whether a process with this id is running.
pub fn alive(pid: i32) -> bool {
    let Some(process) = open(pid, PROCESS_QUERY_LIMITED_INFORMATION) else {
        // Access denied still means it exists.
        return std::io::Error::last_os_error().raw_os_error() == Some(5);
    };
    let mut code = 0u32;
    // SAFETY: the handle opened above.
    let ok = unsafe { GetExitCodeProcess(process.0, &mut code) } != FALSE;
    !ok || code == STILL_ACTIVE
}

/// Ends one process. Windows has no signal to ask first.
pub fn terminate(pid: i32) {
    if let Some(process) = open(pid, PROCESS_TERMINATE) {
        // SAFETY: the handle opened above.
        unsafe { TerminateProcess(process.0, 1) };
    }
}
