#![cfg(windows)]

use std::process::Command;

#[test]
fn install_from_user_writable_directory_exits_nonzero_before_scm_access() {
    let temp = tempfile::tempdir().unwrap();
    let executable = temp.path().join("mavi-vpn-service.exe");
    std::fs::copy(env!("CARGO_BIN_EXE_mavi-vpn-service"), &executable).unwrap();
    let quoted = temp.path().display().to_string().replace('\'', "''");
    // Grant an untrusted principal write access explicitly so even an elevated
    // test runner must refuse this isolated path. No real service is created.
    let setup = Command::new("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            &format!(
                "$ErrorActionPreference = 'Stop'; \
             $acl = [System.IO.Directory]::GetAccessControl('{quoted}'); \
             $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(\
             [System.Security.Principal.SecurityIdentifier]::new('S-1-1-0'), \
             'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'); \
             $acl.AddAccessRule($rule); [System.IO.Directory]::SetAccessControl('{quoted}', $acl)"
            ),
        ])
        .output()
        .unwrap();
    assert!(
        setup.status.success(),
        "{}",
        String::from_utf8_lossy(&setup.stderr)
    );

    let output = Command::new(&executable).arg("install").output().unwrap();
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!output.status.success());
    assert!(stderr.contains("Service installation refused"), "{stderr}");
    assert!(
        !stderr.contains("Cannot open Service Control Manager"),
        "{stderr}"
    );
    assert!(!stderr.contains("Cannot create MaviVPNService"), "{stderr}");
}
