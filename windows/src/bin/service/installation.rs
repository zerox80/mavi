//! A persisted LocalSystem ImagePath must remain safe after installer handles close.

use crate::secure_path::{ensure_trusted_owner, lock_directory_tree, lock_path, TRUSTED_OWNERS};
use anyhow::{bail, Context, Result};
use std::fs::File;
use std::os::windows::io::AsRawHandle;
use std::path::Path;
use std::ptr::null_mut;
use windows_service::service::{
    ServiceAccess, ServiceErrorControl, ServiceInfo, ServiceStartType, ServiceType,
};
use windows_service::service_manager::{ServiceManager, ServiceManagerAccess};
use windows_sys::Win32::Foundation::{LocalFree, GENERIC_ALL, GENERIC_WRITE};
use windows_sys::Win32::Security::Authorization::{
    ConvertSidToStringSidW, GetSecurityInfo, SE_FILE_OBJECT,
};
use windows_sys::Win32::Security::{
    GetAce, IsValidAcl, ACCESS_ALLOWED_ACE, ACE_HEADER, ACL, DACL_SECURITY_INFORMATION,
    INHERIT_ONLY_ACE,
};
use windows_sys::Win32::Storage::FileSystem::{
    DELETE, FILE_APPEND_DATA, FILE_DELETE_CHILD, FILE_WRITE_ATTRIBUTES, FILE_WRITE_DATA,
    FILE_WRITE_EA, WRITE_DAC, WRITE_OWNER,
};
use windows_sys::Win32::System::SystemServices::{ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE};

struct LocalAllocation(*mut std::ffi::c_void);

impl Drop for LocalAllocation {
    fn drop(&mut self) {
        // SAFETY: security APIs allocate these buffers with LocalAlloc.
        unsafe { LocalFree(self.0) };
    }
}

#[derive(Clone, Copy)]
enum ObjectRole {
    Ancestor,
    InstallDirectory,
    Executable,
}

pub(super) fn install(executable: &Path) -> Result<()> {
    with_trusted_executable(executable, || {
        let manager =
            ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CREATE_SERVICE)
                .context("Cannot open Service Control Manager")?;
        let info = ServiceInfo {
            name: super::cli::SERVICE_NAME.into(),
            display_name: super::cli::SERVICE_NAME.into(),
            service_type: ServiceType::OWN_PROCESS,
            start_type: ServiceStartType::AutoStart,
            error_control: ServiceErrorControl::Normal,
            executable_path: executable.to_path_buf(),
            launch_arguments: vec![],
            dependencies: vec![],
            account_name: None, // LocalSystem
            account_password: None,
        };
        // Use the native API to preserve UTF-16 paths and avoid executable search.
        manager
            .create_service(&info, ServiceAccess::QUERY_STATUS)
            .context("Cannot create MaviVPNService")?;
        Ok(())
    })
}

fn with_trusted_executable(executable: &Path, register: impl FnOnce() -> Result<()>) -> Result<()> {
    let parent = executable
        .parent()
        .context("Service executable has no parent")?;
    // Keep the complete tree pinned through registration, including against
    // pre-existing writer handles. Owner checks alone do not validate a DACL.
    let directories = lock_directory_tree(parent, false)?;
    for (index, directory) in directories.iter().enumerate() {
        let role = if index + 1 == directories.len() {
            ObjectRole::InstallDirectory
        } else {
            ObjectRole::Ancestor
        };
        ensure_safe_dacl(directory, role).context("Unsafe service executable directory")?;
    }
    let file = lock_path(executable, false)?;
    ensure_trusted_owner(&file)?;
    ensure_safe_dacl(&file, ObjectRole::Executable).context("Unsafe service executable DACL")?;
    register()
}

fn ensure_safe_dacl(file: &File, role: ObjectRole) -> Result<()> {
    let mut descriptor = null_mut();
    let mut dacl = null_mut();
    // SAFETY: file is pinned and the returned DACL lives in descriptor.
    let status = unsafe {
        GetSecurityInfo(
            file.as_raw_handle(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            null_mut(),
            null_mut(),
            &mut dacl,
            null_mut(),
            &mut descriptor,
        )
    };
    if status != 0 {
        return Err(std::io::Error::from_raw_os_error(status as i32))
            .context("Cannot inspect service executable permissions");
    }
    let _descriptor = LocalAllocation(descriptor);
    // SAFETY: GetSecurityInfo returned this DACL in the live descriptor.
    unsafe { validate_dacl(dacl, role) }
}

/// `dacl` must be null or point to a live Windows security descriptor's DACL.
unsafe fn validate_dacl(dacl: *const ACL, role: ObjectRole) -> Result<()> {
    // A null DACL permits everyone full access; an empty DACL grants nothing.
    if dacl.is_null() || unsafe { IsValidAcl(dacl) } == 0 {
        bail!("Service path has a missing or invalid DACL");
    }
    let mut mutation = GENERIC_ALL
        | GENERIC_WRITE
        | DELETE
        | WRITE_DAC
        | WRITE_OWNER
        | FILE_WRITE_EA
        | FILE_WRITE_ATTRIBUTES
        | FILE_DELETE_CHILD;
    if !matches!(role, ObjectRole::Ancestor) {
        mutation |= FILE_WRITE_DATA | FILE_APPEND_DATA;
    }
    // Root directories can permit creating unrelated children. They must not
    // permit replacing existing children. The install directory must also deny
    // creating sibling executables/DLLs to ordinary users.
    for index in 0..unsafe { (*dacl).AceCount } {
        let mut ace = null_mut();
        // SAFETY: dacl is valid and index is within its ACE count.
        if unsafe { GetAce(dacl, u32::from(index), &mut ace) } == 0 {
            return Err(std::io::Error::last_os_error()).context("Cannot inspect service path ACE");
        }
        // SAFETY: GetAce returns an ACE with a header in the live DACL.
        let header = unsafe { &*ace.cast::<ACE_HEADER>() };
        if u32::from(header.AceFlags) & INHERIT_ONLY_ACE != 0 {
            continue; // Each descendant's effective DACL is checked separately.
        }
        match u32::from(header.AceType) {
            ACCESS_DENIED_ACE_TYPE => continue,
            ACCESS_ALLOWED_ACE_TYPE => {}
            _ => bail!("Unsupported ACE type in service path DACL"),
        }
        if usize::from(header.AceSize) < std::mem::size_of::<ACCESS_ALLOWED_ACE>() {
            bail!("Invalid allow ACE in service path DACL");
        }
        // SAFETY: the ACE type and minimum size were checked above.
        let allowed = unsafe { &*ace.cast::<ACCESS_ALLOWED_ACE>() };
        if allowed.Mask & mutation == 0 {
            continue;
        }
        let mut sid = null_mut();
        // SAFETY: SidStart is the variable-length SID in the valid allow ACE.
        if unsafe {
            ConvertSidToStringSidW((&raw const allowed.SidStart).cast_mut().cast(), &mut sid)
        } == 0
        {
            bail!("Cannot inspect service path trustee");
        }
        let _sid = LocalAllocation(sid.cast());
        let mut len = 0;
        // SAFETY: ConvertSidToStringSidW returns a NUL-terminated UTF-16 buffer.
        let trustee = unsafe {
            while *sid.add(len) != 0 {
                len += 1;
            }
            String::from_utf16_lossy(std::slice::from_raw_parts(sid, len))
        };
        // Fail closed even if a deny ACE might narrow a broad allow grant; do
        // not approximate arbitrary group membership or conditional ACEs.
        if !TRUSTED_OWNERS.contains(&trustee.as_str()) {
            bail!("Service path grants mutation rights to untrusted trustee {trustee}");
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "installation/tests.rs"]
mod tests;
