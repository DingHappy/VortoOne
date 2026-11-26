//! Install a verified stable DEB using the desktop session's system authorization.

use std::fs::{OpenOptions, Permissions};
use std::io::{self, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum InstallOutcome {
    Installed,
    Cancelled,
}

pub(crate) fn is_deb_bundle() -> bool {
    cfg!(target_arch = "x86_64")
        && matches!(
            tauri::utils::platform::bundle_type(),
            Some(tauri::utils::config::BundleType::Deb)
        )
}

/// Run on a blocking thread with bytes returned by the signed updater download.
pub(crate) fn install_verified_deb(
    bytes: &[u8],
    version: &str,
    before_install: impl FnOnce(),
) -> Result<InstallOutcome, &'static str> {
    let (_directory, package) =
        stage_package(bytes).map_err(|_| "无法保存更新安装包，请检查磁盘空间后重试。")?;
    let metadata = Command::new("/usr/bin/dpkg-deb")
        .arg(r"--showformat=${Package}\t${Version}\t${Architecture}\n")
        .arg("--show")
        .arg(&package)
        .stdin(Stdio::null())
        .output()
        .map_err(|_| "无法检查 Ubuntu 安装包，请从下载页手动安装。")?;
    if !metadata.status.success() || !package_matches(&metadata.stdout, version) {
        return Err("更新包与 VortoOne 的版本或架构不匹配，请从下载页手动安装。");
    }

    before_install();
    let status = install_command(&package)
        .status()
        .map_err(|_| "无法打开 Ubuntu 的系统授权，请从下载页手动安装。")?;
    install_outcome(status)
}

fn stage_package(bytes: &[u8]) -> io::Result<(tempfile::TempDir, PathBuf)> {
    let directory = tempfile::Builder::new()
        .prefix("vortoone-update-")
        .permissions(Permissions::from_mode(0o700))
        .tempdir_in("/tmp")?;
    let package = directory.path().join("vorto-one.deb");
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&package)?
        .write_all(bytes)?;
    Ok((directory, package))
}

fn package_matches(metadata: &[u8], version: &str) -> bool {
    metadata == format!("vorto-one\t{version}\tamd64\n").as_bytes()
}

fn install_command(package: &Path) -> Command {
    let mut command = Command::new("/usr/bin/pkexec");
    command
        .args([
            "--disable-internal-agent",
            "/usr/bin/dpkg",
            "--refuse-downgrade",
            "--install",
        ])
        .arg(package)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    command
}

fn install_outcome(status: ExitStatus) -> Result<InstallOutcome, &'static str> {
    match status.code() {
        Some(0) => Ok(InstallOutcome::Installed),
        Some(126) => Ok(InstallOutcome::Cancelled),
        Some(127) => Err("未取得 Ubuntu 安装授权，请检查管理员权限或手动安装。"),
        _ => Err("Ubuntu 安装失败，请稍后重试或从下载页手动安装。"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::process::ExitStatusExt;

    #[test]
    fn requires_the_stable_package_architecture_and_version() {
        assert!(package_matches(b"vorto-one\t0.3.2\tamd64\n", "0.3.2"));
        assert!(!package_matches(b"vorto-one-dev\t0.3.2\tamd64\n", "0.3.2"));
        assert!(!package_matches(b"vorto-one\t0.3.1\tamd64\n", "0.3.2"));
        assert!(!package_matches(b"vorto-one\t0.3.2\tarm64\n", "0.3.2"));
        assert!(!package_matches(b"vorto-one\t0.3.2\t\n", "0.3.2"));
    }

    #[test]
    fn stages_bytes_privately_and_removes_them_when_the_directory_is_dropped() {
        let (directory, file) = stage_package(b"verified payload").unwrap();
        assert_eq!(std::fs::read(&file).unwrap(), b"verified payload");
        assert_eq!(file.metadata().unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(
            directory.path().metadata().unwrap().permissions().mode() & 0o777,
            0o700,
        );
        drop(directory);
        assert!(!file.exists());
    }

    #[test]
    fn invokes_only_dpkg_with_one_package_and_desktop_authorization() {
        let package = "/tmp/test update/vorto-one.deb";
        let command = install_command(Path::new(package));
        assert_eq!(command.get_program(), "/usr/bin/pkexec");
        assert_eq!(
            command.get_args().collect::<Vec<_>>(),
            [
                "--disable-internal-agent",
                "/usr/bin/dpkg",
                "--refuse-downgrade",
                "--install",
                package,
            ]
        );
    }

    #[test]
    fn distinguishes_success_cancellation_denial_and_failure() {
        assert_eq!(
            install_outcome(ExitStatus::from_raw(0)),
            Ok(InstallOutcome::Installed),
        );
        assert_eq!(
            install_outcome(ExitStatus::from_raw(126 << 8)),
            Ok(InstallOutcome::Cancelled),
        );
        assert!(install_outcome(ExitStatus::from_raw(127 << 8)).is_err());
        assert!(install_outcome(ExitStatus::from_raw(1 << 8)).is_err());
        assert!(install_outcome(ExitStatus::from_raw(9)).is_err());
    }
}
