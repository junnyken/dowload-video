//! Spawning a sidecar so that the WHOLE process tree can be killed.
//!
//! yt-dlp.exe is a PyInstaller one-file build: the bootloader starts a second
//! Python process, which in turn starts ffmpeg to merge streams. Killing only
//! the PID we spawned would leave ffmpeg running and holding the output file
//! open. So:
//! - Windows: the child is put in a Job Object with
//!   JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. Every process it starts later joins
//!   the same job automatically; TerminateJobObject kills them all. Because of
//!   KILL_ON_JOB_CLOSE, the tree also dies if the app exits or crashes (the OS
//!   closes our job handle).
//! - Unix (dev only): the child leads a new process group and we killpg it.

use std::io;
use std::process::{Child, Command};

pub struct ProcessTree {
    #[cfg(windows)]
    job: win::Job,
    #[cfg(unix)]
    pgid: i32,
}

impl ProcessTree {
    /// Kill every process in the tree. Safe to call more than once.
    pub fn kill(&self) -> io::Result<()> {
        #[cfg(windows)]
        return self.job.terminate();
        #[cfg(unix)]
        {
            // Negative pid = the whole process group.
            if unsafe { libc::kill(-self.pgid, libc::SIGKILL) } == 0 {
                Ok(())
            } else {
                let err = io::Error::last_os_error();
                // ESRCH: the group is already gone, which is what we wanted.
                if err.raw_os_error() == Some(libc::ESRCH) { Ok(()) } else { Err(err) }
            }
        }
    }
}

#[cfg(unix)]
pub fn spawn_tree(mut cmd: Command) -> io::Result<(Child, ProcessTree)> {
    use std::os::unix::process::CommandExt;
    cmd.process_group(0); // child becomes leader of a new group (pgid = its pid)
    let child = cmd.spawn()?;
    let pgid = child.id() as i32;
    Ok((child, ProcessTree { pgid }))
}

#[cfg(windows)]
pub fn spawn_tree(mut cmd: Command) -> io::Result<(Child, ProcessTree)> {
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::process::CommandExt;
    use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;

    // The job is created and configured BEFORE spawning so a failure leaves
    // nothing running.
    let job = win::Job::new_kill_on_close()?;
    cmd.creation_flags(CREATE_NO_WINDOW); // no console window flashing up
    let mut child = cmd.spawn()?;
    // There is a tiny window between spawn and assign in which the child could
    // start a grandchild outside the job. yt-dlp takes far longer than that to
    // reach ffmpeg, so this is acceptable for C0; spawning suspended
    // (CREATE_SUSPENDED + ResumeThread) would close it completely.
    if let Err(e) = job.assign(child.as_raw_handle()) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(e);
    }
    Ok((child, ProcessTree { job }))
}

#[cfg(windows)]
mod win {
    use std::ffi::c_void;
    use std::io;
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    /// Owned Job Object handle. Dropping it closes the handle, which (with
    /// KILL_ON_JOB_CLOSE) kills anything still running in the job.
    pub struct Job(HANDLE);

    // A kernel handle can be used from any thread.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn new_kill_on_close() -> io::Result<Job> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let job = Job(handle);
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    job.0,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(job)
            }
        }

        pub fn assign(&self, process: std::os::windows::io::RawHandle) -> io::Result<()> {
            if unsafe { AssignProcessToJobObject(self.0, process as HANDLE) } == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        }

        pub fn terminate(&self) -> io::Result<()> {
            if unsafe { TerminateJobObject(self.0, 1) } == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe { CloseHandle(self.0) };
        }
    }
}
