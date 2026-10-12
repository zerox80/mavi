use super::*;

#[test]
fn linked_log_files_are_rejected_before_writing_or_rotation() {
    let directory = tempfile::tempdir().unwrap();
    let target = directory.path().join("protected.txt");
    let log = directory.path().join("mavi-vpn-service.log");
    fs::write(&target, b"unchanged").unwrap();
    fs::hard_link(&target, &log).unwrap();
    assert!(open_append_file(&log).is_err());
    assert_eq!(fs::read(&target).unwrap(), b"unchanged");
}

#[test]
fn redirected_log_directory_does_not_create_or_rotate_target_files() {
    let directory = tempfile::tempdir().unwrap();
    let target = directory.path().join("target");
    let junction = directory.path().join("logs");
    fs::create_dir(&target).unwrap();
    let log = target.join("mavi-vpn-service.log");
    let old_log = target.join("mavi-vpn-service.log.old");
    fs::write(&log, b"unchanged").unwrap();
    fs::write(&old_log, b"previous").unwrap();
    let quote = |path: &Path| path.display().to_string().replace('\'', "''");
    let script = format!(
        "$ErrorActionPreference = 'Stop'; New-Item -ItemType Junction -Path '{}' -Target '{}' | Out-Null",
        quote(&junction), quote(&target)
    );
    let output = std::process::Command::new("powershell")
        .args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(open_log_file(&junction.join("mavi-vpn-service.log")).is_err());
    assert_eq!(fs::read(&log).unwrap(), b"unchanged");
    assert_eq!(fs::read(&old_log).unwrap(), b"previous");
    fs::remove_dir(junction).unwrap();
}
