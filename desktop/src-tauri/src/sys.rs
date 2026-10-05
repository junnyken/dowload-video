//! OS calls: free disk space, "show in folder", "open with default app".
//! No shell anywhere: explorer.exe gets an argv, files open via ShellExecuteW
//! with the "open" verb on a path that paths::check_produced already vetted.

use std::io;
use std::path::Path;

/// Free bytes available to the current user on the volume holding `path`.
pub fn disk_free(path: &Path) -> io::Result<u64> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
        let mut avail: u64 = 0;
        let ok = unsafe { GetDiskFreeSpaceExW(wide.as_ptr(), &mut avail, std::ptr::null_mut(), std::ptr::null_mut()) };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(avail)
    }
    #[cfg(unix)]
    {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        let c = CString::new(path.as_os_str().as_bytes()).map_err(|_| io::Error::from(io::ErrorKind::InvalidInput))?;
        let mut st: libc::statvfs = unsafe { std::mem::zeroed() };
        if unsafe { libc::statvfs(c.as_ptr(), &mut st) } != 0 {
            return Err(io::Error::last_os_error());
        }
        #[allow(clippy::unnecessary_cast)]
        Ok(st.f_bavail as u64 * st.f_frsize as u64)
    }
}

/// Opens Explorer with the file selected.
pub fn reveal(path: &Path) -> io::Result<()> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let s = path.to_string_lossy();
        // `"` cannot occur in a Windows path, so the quoting below cannot be
        // broken out of. raw_arg is needed because explorer parses
        // `/select,"<path>"` itself and does not follow the MSVC argv rules
        // std uses for normal args.
        if s.contains('"') {
            return Err(io::Error::from(io::ErrorKind::InvalidInput));
        }
        let windir = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
        let explorer = Path::new(&windir).join("explorer.exe");
        std::process::Command::new(explorer).raw_arg(format!("/select,\"{s}\"")).spawn()?;
        Ok(())
    }
    #[cfg(not(windows))]
    {
        // Dev only: open the containing folder.
        let dir = path.parent().ok_or_else(|| io::Error::from(io::ErrorKind::InvalidInput))?;
        std::process::Command::new("xdg-open").arg(dir).spawn()?;
        Ok(())
    }
}

/// Opens a file with its default application.
pub fn open_default(path: &Path) -> io::Result<()> {
    shell_open(path.as_os_str())
}

/// Opens an https URL (already checked by validate::open_url) in the
/// default browser.
pub fn open_url(url: &str) -> io::Result<()> {
    shell_open(std::ffi::OsStr::new(url))
}

fn shell_open(target: &std::ffi::OsStr) -> io::Result<()> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::System::Com::{CoInitializeEx, CoUninitialize, COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE};
        use windows_sys::Win32::UI::Shell::ShellExecuteW;
        use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

        let wide = |s: &std::ffi::OsStr| s.encode_wide().chain(std::iter::once(0)).collect::<Vec<u16>>();
        let file = wide(target);
        let verb = wide(std::ffi::OsStr::new("open"));
        // ShellExecute may use COM (shell extensions); Microsoft asks callers
        // to initialise it. Done on a fresh thread so the Tauri threads'
        // COM state is never touched.
        let r = std::thread::spawn(move || unsafe {
            let hr = CoInitializeEx(std::ptr::null(), (COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE) as u32);
            let h = ShellExecuteW(
                std::ptr::null_mut(),
                verb.as_ptr(),
                file.as_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                SW_SHOWNORMAL,
            );
            if hr >= 0 {
                CoUninitialize();
            }
            h as isize
        })
        .join()
        .map_err(|_| io::Error::other("open thread panicked"))?;
        // Per the docs, a value > 32 means success.
        if r > 32 {
            Ok(())
        } else {
            Err(io::Error::other(format!("ShellExecuteW failed ({r})")))
        }
    }
    #[cfg(not(windows))]
    {
        std::process::Command::new("xdg-open").arg(target).spawn()?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn disk_free_reports_bytes() {
        let n = super::disk_free(&std::env::temp_dir()).unwrap();
        assert!(n > 0);
        assert!(super::disk_free(std::path::Path::new("/definitely/not/here")).is_err());
    }
}
