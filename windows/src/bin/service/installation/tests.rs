use super::*;
use windows_sys::Win32::Security::Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW;
use windows_sys::Win32::Security::GetSecurityDescriptorDacl;

fn check(sddl: &str, role: ObjectRole) -> Result<()> {
    let sddl: Vec<u16> = sddl.encode_utf16().chain(Some(0)).collect();
    let mut descriptor = null_mut();
    // SAFETY: input is NUL-terminated; output points to writable storage.
    assert_ne!(
        unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut descriptor,
                null_mut(),
            )
        },
        0
    );
    let _descriptor = LocalAllocation(descriptor);
    let mut dacl = null_mut();
    let mut present = 0;
    let mut defaulted = 0;
    // SAFETY: descriptor is live and output pointers are writable.
    assert_ne!(
        unsafe { GetSecurityDescriptorDacl(descriptor, &mut present, &mut dacl, &mut defaulted) },
        0
    );
    // SAFETY: the DACL belongs to the descriptor kept alive above.
    unsafe { validate_dacl(dacl, role) }
}

#[test]
fn protected_installation_allows_admin_writes_and_user_reads() {
    let dacl = "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FRFX;;;BU)(A;;FRFX;;;AC)";
    for role in [
        ObjectRole::Ancestor,
        ObjectRole::InstallDirectory,
        ObjectRole::Executable,
    ] {
        check(dacl, role).unwrap();
    }
    let trusted_installer = format!("D:P(A;;FA;;;{})", TRUSTED_OWNERS[2]);
    check(&trusted_installer, ObjectRole::Executable).unwrap();
}

#[test]
fn all_untrusted_executable_mutation_grants_are_rejected() {
    // Include direct content writes, replacement, ACL/owner changes and generic
    // rights; unknown individual accounts matter just as much as broad groups.
    for sid in ["WD", "AU", "BU", "S-1-5-21-101-202-303-1001"] {
        for right in [
            "GA", "GW", "FW", "0x2", "0x4", "0x10", "0x100", "SD", "WD", "WO",
        ] {
            let dacl = format!("D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;{right};;;{sid})");
            assert!(check(&dacl, ObjectRole::Executable).is_err(), "{dacl}");
        }
    }
}

#[test]
fn ancestor_replacement_is_rejected_even_with_a_protected_executable() {
    for right in ["0x40", "SD", "WD", "WO", "GW", "GA"] {
        let dacl = format!("D:P(A;;FA;;;BA)(A;;{right};;;AU)");
        assert!(check(&dacl, ObjectRole::Ancestor).is_err(), "{dacl}");
        assert!(
            check(&dacl, ObjectRole::InstallDirectory).is_err(),
            "{dacl}"
        );
    }
}

#[test]
fn root_can_allow_unrelated_children_but_install_directory_cannot() {
    for right in ["0x2", "0x4"] {
        let dacl = format!("D:P(A;;FA;;;BA)(A;;{right};;;AU)");
        check(&dacl, ObjectRole::Ancestor).unwrap();
        assert!(check(&dacl, ObjectRole::InstallDirectory).is_err());
    }
}

#[test]
fn inherit_only_grants_are_checked_at_the_descendant_where_they_apply() {
    check("D:P(A;;FA;;;BA)(A;OICIIO;GA;;;CO)", ObjectRole::Ancestor).unwrap();
    check("D:P(A;;FA;;;BA)(A;OICIIO;GA;;;AU)", ObjectRole::Ancestor).unwrap();
    assert!(check("D:P(A;;FA;;;BA)(A;ID;GA;;;AU)", ObjectRole::Executable).is_err());
}

#[test]
fn null_dacl_and_conditional_allow_aces_fail_closed() {
    assert!(check("D:NO_ACCESS_CONTROL", ObjectRole::Executable).is_err());
    assert!(check(
        "D:P(XA;;GA;;;WD;(Exists WIN://SYSAPPID))",
        ObjectRole::Executable,
    )
    .is_err());
    check("D:P", ObjectRole::Executable).unwrap();
}

#[test]
fn deny_aces_do_not_conceal_untrusted_broad_allow_grants() {
    assert!(check("D:P(D;;GA;;;BU)(A;;GA;;;WD)", ObjectRole::Executable).is_err());
    check("D:P(D;;GW;;;BU)(A;;FA;;;BA)", ObjectRole::Executable).unwrap();
}

#[test]
fn file_handle_query_detects_untrusted_write_permissions() {
    let temp = tempfile::tempdir().unwrap();
    let executable = temp.path().join("service.exe");
    std::fs::write(&executable, b"unchanged").unwrap();
    let guard = lock_path(&executable, false).unwrap();
    crate::secure_path::replace_dacl(&executable, "D:P(A;;FA;;;WD)").unwrap();
    assert!(ensure_safe_dacl(&guard, ObjectRole::Executable).is_err());
    assert_eq!(std::fs::read(&executable).unwrap(), b"unchanged");
}

#[test]
fn unsafe_path_never_reaches_service_registration() {
    let temp = tempfile::tempdir().unwrap();
    let executable = temp.path().join("service.exe");
    std::fs::write(&executable, b"unchanged").unwrap();
    crate::secure_path::replace_dacl(temp.path(), "D:P(A;OICI;FA;;;WD)").unwrap();
    let mut called = false;
    assert!(with_trusted_executable(&executable, || {
        called = true;
        Ok(())
    })
    .is_err());
    assert!(!called);
    assert_eq!(std::fs::read(&executable).unwrap(), b"unchanged");
}

#[test]
fn standard_program_files_ancestors_pass_without_modification() {
    let path = std::env::var_os("ProgramFiles").unwrap();
    let directories = lock_directory_tree(Path::new(&path), false).unwrap();
    assert!(directories.len() >= 2);
    for directory in directories {
        ensure_safe_dacl(&directory, ObjectRole::Ancestor).unwrap();
    }
}
