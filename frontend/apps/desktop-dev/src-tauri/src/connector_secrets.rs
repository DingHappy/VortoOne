//! Computer credentials belong to this installation and channel, never to a home's backup.
//! macOS uses Security.framework directly. Other systems require an explicit private-file choice.
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
};

pub const CHANNEL: &str = "com.vorto.vortoone.dev";

#[derive(Clone, Copy, serde::Serialize, serde::Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Storage {
    Keychain,
    PrivateFile,
}

#[derive(Clone)]
pub struct SecretRef {
    pub home: String,
    pub connector: String,
}

impl SecretRef {
    fn account(&self) -> Result<String, String> {
        if !identifier(&self.home) || !identifier(&self.connector) {
            return Err("电脑身份无效".into());
        }
        Ok(format!("{}:{}", self.home, self.connector))
    }
    pub fn namespace(&self) -> Result<String, String> {
        Ok(format!("{CHANNEL}:{}", self.account()?))
    }
}

pub fn identifier(text: &str) -> bool {
    text.len() == 32
        && text
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
}

pub trait SecretStore {
    fn put(&self, key: &SecretRef, storage: Storage, secret: &str) -> Result<(), String>;
    fn get(&self, key: &SecretRef, storage: Storage) -> Result<String, String>;
    fn delete(&self, key: &SecretRef, storage: Storage) -> Result<(), String>;
}

pub struct SystemStore {
    pub directory: PathBuf,
}

pub fn private_dir(path: &Path) -> Result<(), String> {
    if let Ok(meta) = fs::symlink_metadata(path) {
        if meta.file_type().is_symlink() || !meta.is_dir() {
            return Err("本机私有目录无效".into());
        }
    } else {
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            let mut builder = fs::DirBuilder::new();
            builder.recursive(true).mode(0o700);
            builder.create(path).map_err(|_| "无法创建本机私有目录")?;
        }
        #[cfg(not(unix))]
        {
            return Err("此系统还没有可验证的私有文件存储".into());
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))
            .map_err(|_| "无法限制本机目录权限")?;
    }
    Ok(())
}

pub fn random_id() -> Result<String, String> {
    let mut bytes = [0u8; 16];
    fs::File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut bytes))
        .map_err(|_| "无法生成电脑身份")?;
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    let hex: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    Ok(hex)
}

pub fn atomic_private_write(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path.parent().ok_or("本机配置目录无效")?;
    private_dir(parent)?;
    let temporary = parent.join(format!(".{}.tmp", random_id()?));
    let result = (|| -> std::io::Result<()> {
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let mut file = options.open(&temporary)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&temporary, path)?;
        fs::File::open(parent)?.sync_all()
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result.map_err(|_| "无法保存本机电脑配置".into())
}

fn valid_secret(secret: &str) -> bool {
    (32..=512).contains(&secret.len()) && secret.bytes().all(|c| c.is_ascii_graphic())
}

impl SystemStore {
    fn file(&self, key: &SecretRef) -> Result<PathBuf, String> {
        Ok(self.directory.join(format!("{}.secret", key.namespace()?)))
    }
}

impl SecretStore for SystemStore {
    fn put(&self, key: &SecretRef, storage: Storage, secret: &str) -> Result<(), String> {
        if !valid_secret(secret) {
            return Err("电脑凭据无效".into());
        }
        match storage {
            Storage::Keychain => keychain::put(&key.account()?, secret),
            Storage::PrivateFile => atomic_private_write(&self.file(key)?, secret.as_bytes()),
        }
    }
    fn get(&self, key: &SecretRef, storage: Storage) -> Result<String, String> {
        let secret = match storage {
            Storage::Keychain => keychain::get(&key.account()?)?,
            Storage::PrivateFile => {
                let path = self.file(key)?;
                let mut options = fs::OpenOptions::new();
                options.read(true);
                #[cfg(unix)]
                {
                    use std::os::unix::fs::OpenOptionsExt;
                    options.custom_flags(libc::O_NOFOLLOW);
                }
                let mut file = options.open(path).map_err(|_| "无法读取电脑凭据")?;
                let meta = file.metadata().map_err(|_| "无法核对电脑凭据")?;
                if !meta.is_file() || meta.len() > 512 {
                    return Err("电脑凭据文件无效".into());
                }
                #[cfg(unix)]
                {
                    use std::os::unix::fs::MetadataExt;
                    if meta.mode() & 0o777 != 0o600 || meta.nlink() != 1 {
                        return Err("电脑凭据权限无效".into());
                    }
                }
                let mut text = String::new();
                file.read_to_string(&mut text)
                    .map_err(|_| "无法读取电脑凭据")?;
                text
            }
        };
        if valid_secret(&secret) {
            Ok(secret)
        } else {
            Err("电脑凭据无效".into())
        }
    }
    fn delete(&self, key: &SecretRef, storage: Storage) -> Result<(), String> {
        match storage {
            Storage::Keychain => keychain::delete(&key.account()?),
            Storage::PrivateFile => match fs::remove_file(self.file(key)?) {
                Ok(()) => fs::File::open(&self.directory)
                    .and_then(|file| file.sync_all())
                    .map_err(|_| "无法确认电脑凭据已删除".into()),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
                Err(_) => Err("电脑连接已停用，但凭据尚未删除，请重试移除".into()),
            },
        }
    }
}

#[cfg(not(target_os = "macos"))]
mod keychain {
    pub fn put(_: &str, _: &str) -> Result<(), String> {
        Err("此系统的秘密存储尚未就绪".into())
    }
    pub fn get(_: &str) -> Result<String, String> {
        Err("此系统的秘密存储尚未就绪".into())
    }
    pub fn delete(_: &str) -> Result<(), String> {
        Err("此系统的秘密存储尚未就绪".into())
    }
}

#[cfg(target_os = "macos")]
mod keychain {
    use std::{ffi::c_void, ptr};
    type Ref = *const c_void;
    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        fn CFStringCreateWithBytes(
            allocator: Ref,
            bytes: *const u8,
            count: isize,
            encoding: u32,
            external: u8,
        ) -> Ref;
        fn CFDataCreate(allocator: Ref, bytes: *const u8, count: isize) -> Ref;
        fn CFDataGetLength(data: Ref) -> isize;
        fn CFDataGetBytePtr(data: Ref) -> *const u8;
        fn CFDictionaryCreateMutable(
            allocator: Ref,
            count: isize,
            keys: Ref,
            values: Ref,
        ) -> *mut c_void;
        fn CFDictionarySetValue(dictionary: *mut c_void, key: Ref, value: Ref);
        #[cfg(test)]
        fn CFDictionaryGetValue(dictionary: Ref, key: Ref) -> Ref;
        #[cfg(test)]
        fn CFEqual(left: Ref, right: Ref) -> u8;
        fn CFRelease(value: Ref);
        static kCFTypeDictionaryKeyCallBacks: c_void;
        static kCFTypeDictionaryValueCallBacks: c_void;
        static kCFBooleanTrue: Ref;
        static kCFBooleanFalse: Ref;
    }
    #[link(name = "Security", kind = "framework")]
    extern "C" {
        fn SecItemAdd(attributes: Ref, result: *mut Ref) -> i32;
        fn SecItemUpdate(query: Ref, attributes: Ref) -> i32;
        fn SecItemCopyMatching(query: Ref, result: *mut Ref) -> i32;
        fn SecItemDelete(query: Ref) -> i32;
        static kSecClass: Ref;
        static kSecClassGenericPassword: Ref;
        static kSecAttrService: Ref;
        static kSecAttrAccount: Ref;
        static kSecAttrSynchronizable: Ref;
        static kSecValueData: Ref;
        static kSecReturnData: Ref;
        static kSecMatchLimit: Ref;
        static kSecMatchLimitOne: Ref;
    }
    struct Owned(Ref);
    impl Drop for Owned {
        fn drop(&mut self) {
            if !self.0.is_null() {
                unsafe {
                    CFRelease(self.0);
                }
            }
        }
    }
    fn string(text: &str) -> Owned {
        Owned(unsafe {
            CFStringCreateWithBytes(
                ptr::null(),
                text.as_ptr(),
                text.len() as isize,
                0x08000100,
                0,
            )
        })
    }
    fn dict() -> Owned {
        Owned(unsafe {
            CFDictionaryCreateMutable(
                ptr::null(),
                0,
                &kCFTypeDictionaryKeyCallBacks,
                &kCFTypeDictionaryValueCallBacks,
            )
        })
    }
    fn set(dict: &Owned, key: Ref, value: Ref) {
        unsafe {
            CFDictionarySetValue(dict.0 as *mut c_void, key, value);
        }
    }
    fn query(account: &str) -> Owned {
        let query = dict();
        let service = string(&format!("{}.computer-connector", super::CHANNEL));
        let account = string(account);
        unsafe {
            set(&query, kSecClass, kSecClassGenericPassword);
            set(&query, kSecAttrService, service.0);
            set(&query, kSecAttrAccount, account.0);
            set(&query, kSecAttrSynchronizable, kCFBooleanFalse);
        }
        query
    }
    pub fn put(account: &str, secret: &str) -> Result<(), String> {
        let query = query(account);
        let data =
            Owned(unsafe { CFDataCreate(ptr::null(), secret.as_ptr(), secret.len() as isize) });
        unsafe {
            set(&query, kSecValueData, data.0);
            // macOS login keychain works for this independently signed Dev shell.
            // kSecAttrAccessible is a Data Protection/synchronizable-keychain
            // attribute on macOS; do not silently require unavailable entitlements.
            let status = SecItemAdd(query.0, ptr::null_mut());
            if status == -25299 {
                let attrs = dict();
                set(&attrs, kSecValueData, data.0);
                return if SecItemUpdate(self::query(account).0, attrs.0) == 0 {
                    Ok(())
                } else {
                    Err("无法更新系统钥匙串中的电脑凭据".into())
                };
            }
            if status == 0 {
                Ok(())
            } else {
                Err("无法将电脑凭据保存到系统钥匙串".into())
            }
        }
    }
    pub fn get(account: &str) -> Result<String, String> {
        let query = query(account);
        let mut raw = ptr::null();
        unsafe {
            set(&query, kSecReturnData, kCFBooleanTrue);
            set(&query, kSecMatchLimit, kSecMatchLimitOne);
            if SecItemCopyMatching(query.0, &mut raw) != 0 || raw.is_null() {
                return Err("无法读取系统钥匙串中的电脑凭据".into());
            }
            let data = Owned(raw);
            let size = CFDataGetLength(data.0);
            if !(32..=512).contains(&size) {
                return Err("系统钥匙串中的电脑凭据无效".into());
            }
            String::from_utf8(
                std::slice::from_raw_parts(CFDataGetBytePtr(data.0), size as usize).to_vec(),
            )
            .map_err(|_| "系统钥匙串中的电脑凭据无效".into())
        }
    }
    pub fn delete(account: &str) -> Result<(), String> {
        let status = unsafe { SecItemDelete(query(account).0) };
        if status == 0 || status == -25300 {
            Ok(())
        } else {
            Err("电脑连接已停用，但钥匙串凭据尚未删除，请重试移除".into())
        }
    }
    #[test]
    fn native_query_is_channel_scoped_and_not_synchronizable_without_accessing_keychain() {
        let account = "11111111111141118111111111111111:22222222222242228222222222222222";
        let query = query(account);
        let expected_service = string("com.vorto.vortoone.dev.computer-connector");
        let expected_account = string(account);
        unsafe {
            assert_ne!(
                CFEqual(
                    CFDictionaryGetValue(query.0, kSecClass),
                    kSecClassGenericPassword
                ),
                0
            );
            assert_ne!(
                CFEqual(
                    CFDictionaryGetValue(query.0, kSecAttrService),
                    expected_service.0
                ),
                0
            );
            assert_ne!(
                CFEqual(
                    CFDictionaryGetValue(query.0, kSecAttrAccount),
                    expected_account.0
                ),
                0
            );
            assert_ne!(
                CFEqual(
                    CFDictionaryGetValue(query.0, kSecAttrSynchronizable),
                    kCFBooleanFalse
                ),
                0
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn key() -> SecretRef {
        SecretRef {
            home: "12345678123442348234123456789abc".into(),
            connector: "aaaaaaaa123442348234123456789abc".into(),
        }
    }
    #[test]
    fn namespaces_include_channel_and_both_identities() {
        let key = key();
        assert_eq!(
            key.namespace().unwrap(),
            format!("com.vorto.vortoone.dev:{}:{}", key.home, key.connector)
        );
        assert!(!key.namespace().unwrap().starts_with("com.vorto.vortoone:"));
        assert!(SecretRef {
            home: "../outside".into(),
            ..key
        }
        .account()
        .is_err());
    }
    #[test]
    fn explicitly_selected_private_files_round_trip_and_reject_links() {
        let root =
            std::env::temp_dir().join(format!("vortoone-computer-secret-{}", random_id().unwrap()));
        let store = SystemStore {
            directory: root.clone(),
        };
        let secret = "test-only-computer-secret-0123456789";
        store.put(&key(), Storage::PrivateFile, secret).unwrap();
        assert_eq!(store.get(&key(), Storage::PrivateFile).unwrap(), secret);
        #[cfg(unix)]
        {
            use std::os::unix::fs::{symlink, MetadataExt, PermissionsExt};
            assert_eq!(fs::metadata(&root).unwrap().mode() & 0o777, 0o700);
            let path = store.file(&key()).unwrap();
            assert_eq!(fs::metadata(&path).unwrap().mode() & 0o777, 0o600);
            fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
            assert!(store.get(&key(), Storage::PrivateFile).is_err());
            fs::remove_file(&path).unwrap();
            symlink(root.join("absent"), &path).unwrap();
            assert!(store.get(&key(), Storage::PrivateFile).is_err());
        }
        store.delete(&key(), Storage::PrivateFile).unwrap();
        store.delete(&key(), Storage::PrivateFile).unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}
