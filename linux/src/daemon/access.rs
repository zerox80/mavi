//! Grant named control-group members access without requiring a new desktop login.
// Linux exposes POSIX ACLs through its xattr ABI; the narrow FFI calls below
// keep this independent of optional setfacl executables or libacl packages.
#![allow(unsafe_code)]
use anyhow::{bail, Context, Result};
use nix::unistd::{Group, User};
use std::ffi::CString;
use std::os::unix::{ffi::OsStrExt, fs::MetadataExt};
use std::path::Path;

pub(crate) fn grant_group_access(path: &Path) -> Result<()> {
    let metadata = std::fs::symlink_metadata(path)?;
    let Some(group) = Group::from_gid(nix::unistd::Gid::from_raw(metadata.gid()))? else {
        return Ok(());
    };
    if group.name != "mavivpn" {
        return Ok(());
    }
    let mut users = Vec::new();
    for name in group.mem {
        if let Some(user) = User::from_name(&name)? {
            users.push(user.uid.as_raw());
        }
    }
    users.sort_unstable();
    users.dedup();
    if !users.is_empty() {
        set_member_acl(path, metadata.mode(), &users)?;
    }
    Ok(())
}

fn set_member_acl(path: &Path, mode: u32, users: &[u32]) -> Result<()> {
    if mode & 0o007 != 0 {
        bail!("Refusing to grant access to a world-accessible IPC file");
    }
    // Linux POSIX ACL v2: owner, sorted named users, group, mask, other.
    // The mask never grants more than the daemon's existing group permissions.
    let group_permissions = u16::try_from((mode >> 3) & 7)?;
    let mut acl = 2_u32.to_le_bytes().to_vec();
    let mut entry = |tag: u16, permissions: u16, id: u32| {
        acl.extend_from_slice(&tag.to_le_bytes());
        acl.extend_from_slice(&permissions.to_le_bytes());
        acl.extend_from_slice(&id.to_le_bytes());
    };
    entry(1, u16::try_from((mode >> 6) & 7)?, u32::MAX);
    for &uid in users {
        entry(2, group_permissions, uid);
    }
    entry(4, group_permissions, u32::MAX);
    entry(16, group_permissions, u32::MAX);
    entry(32, 0, u32::MAX);
    let path = CString::new(path.as_os_str().as_bytes())?;
    // SAFETY: both C strings and the ACL byte slice remain alive for this call.
    let result = unsafe {
        libc::setxattr(
            path.as_ptr(),
            c"system.posix_acl_access".as_ptr(),
            acl.as_ptr().cast(),
            acl.len(),
            0,
        )
    };
    if result != 0 {
        return Err(std::io::Error::last_os_error()).context("Could not grant desktop IPC access");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn kernel_accepts_read_only_token_acl_without_widening_other_access() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ipc.token");
        std::fs::write(&path, "test-token").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o640)).unwrap();
        set_member_acl(&path, 0o640, &[65534]).unwrap();
        assert_eq!(std::fs::metadata(&path).unwrap().mode() & 0o777, 0o640);
        let path = CString::new(path.as_os_str().as_bytes()).unwrap();
        let mut acl = [0_u8; 128];
        // SAFETY: the strings and writable buffer are valid for the call.
        let size = unsafe {
            libc::getxattr(
                path.as_ptr(),
                c"system.posix_acl_access".as_ptr(),
                acl.as_mut_ptr().cast(),
                acl.len(),
            )
        };
        assert_eq!(size, 44);
        assert_eq!(&acl[12..20], &[2, 0, 4, 0, 254, 255, 0, 0]);
        assert_eq!(&acl[36..44], &[32, 0, 0, 0, 255, 255, 255, 255]);
    }

    #[test]
    fn refuses_world_accessible_ipc_files() {
        assert!(set_member_acl(Path::new("/not-used"), 0o644, &[1000]).is_err());
    }
}
