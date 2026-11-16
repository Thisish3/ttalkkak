//! Saved server connection: address in `connection.json`, token in the OS keychain.

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const ACCOUNT: &str = "operator-token";
const FILE: &str = "connection.json";

pub trait SecretStore {
    fn set(&self, secret: &str) -> Result<(), String>;
    fn get(&self) -> Result<Option<String>, String>;
    fn delete(&self) -> Result<(), String>;
}

pub struct Keychain;

impl Keychain {
    fn entry() -> Result<keyring::Entry, String> {
        keyring::Entry::new("io.github.thisish3.ttalkkak", ACCOUNT).map_err(|e| e.to_string())
    }
}

impl SecretStore for Keychain {
    fn set(&self, secret: &str) -> Result<(), String> {
        Self::entry()?
            .set_password(secret)
            .map_err(|e| e.to_string())
    }
    fn get(&self) -> Result<Option<String>, String> {
        match Self::entry()?.get_password() {
            Ok(s) => Ok(Some(s)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(e) => Err(e.to_string()),
        }
    }
    fn delete(&self) -> Result<(), String> {
        match Self::entry()?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(e.to_string()),
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum TokenStorage {
    Keychain,
    File,
}

#[derive(Serialize, Deserialize, Clone, PartialEq)]
struct Stored {
    url: String,
    token_storage: TokenStorage,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    token: Option<String>,
    #[serde(default)]
    cleartext_ok: bool,
}

impl std::fmt::Debug for Stored {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Stored")
            .field("url", &self.url)
            .field("token_storage", &self.token_storage)
            .field("token", &self.token.as_ref().map(|_| "<redacted>"))
            .field("cleartext_ok", &self.cleartext_ok)
            .finish()
    }
}

/// What the page may see: never the token itself.
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Connection {
    pub url: String,
    pub token_storage: TokenStorage,
    pub cleartext_ok: bool,
}

pub fn normalize_url(raw: &str) -> Result<String, String> {
    let u = url::Url::parse(raw.trim())
        .map_err(|_| "서버 주소 형식이 올바르지 않습니다".to_string())?;
    if !matches!(u.scheme(), "http" | "https") || u.host_str().is_none() {
        return Err("서버 주소는 http:// 또는 https:// 로 시작해야 합니다".into());
    }
    if u.path() != "/" || u.query().is_some() || u.fragment().is_some() {
        return Err("서버 주소에는 경로를 넣지 마세요 (예: http://100.64.0.1:8787)".into());
    }
    Ok(u.as_str().trim_end_matches('/').to_string())
}

fn path(dir: &Path) -> PathBuf {
    dir.join(FILE)
}

fn make_dir(dir: &Path) -> std::io::Result<()> {
    let mut b = fs::DirBuilder::new();
    b.recursive(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        b.mode(0o700);
    }
    b.create(dir)
}

fn tmp_name(file: &Path) -> PathBuf {
    use std::sync::atomic::{AtomicU32, Ordering};
    static N: AtomicU32 = AtomicU32::new(0);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.subsec_nanos());
    let n = N.fetch_add(1, Ordering::Relaxed);
    let name = file
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("connection.json");
    file.with_file_name(format!(
        ".{name}.{:x}{nanos:x}{n:x}.tmp",
        std::process::id()
    ))
}

/// Same-dir 0600 temp file (O_EXCL, O_NOFOLLOW), fsync, rename; temp removed on failure.
fn write_private(file: &Path, data: &[u8]) -> Result<(), String> {
    let err = |e: std::io::Error| format!("연결 정보를 저장하지 못했습니다: {e}");
    let tmp = tmp_name(file);
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600).custom_flags(libc::O_NOFOLLOW);
    }
    let mut f = opts.open(&tmp).map_err(err)?;
    let done = f
        .write_all(data)
        .and_then(|()| f.sync_all())
        .and_then(|()| fs::rename(&tmp, file));
    if let Err(e) = done {
        let _ = fs::remove_file(&tmp);
        return Err(err(e));
    }
    Ok(())
}

const CLEARTEXT_REFUSED: &str =
    "암호화되지 않은 주소(http://)로 토큰을 보내려면 위험을 확인했다는 동의가 필요합니다";

pub fn save(
    dir: &Path,
    store: &dyn SecretStore,
    url: &str,
    token: &str,
    allow_cleartext: bool,
) -> Result<(), String> {
    let url = normalize_url(url)?;
    if cleartext_risk(&url) && !allow_cleartext {
        return Err(CLEARTEXT_REFUSED.into());
    }
    let token = token.trim();
    if token.is_empty() {
        return Err("운영자 토큰이 비어 있습니다".into());
    }
    make_dir(dir).map_err(|e| format!("설정 폴더를 만들지 못했습니다: {e}"))?;
    let stored = match store.set(token) {
        Ok(()) => Stored {
            url,
            token_storage: TokenStorage::Keychain,
            token: None,
            cleartext_ok: allow_cleartext,
        },
        Err(e) => {
            eprintln!("경고: OS 키체인에 토큰을 저장하지 못해 설정 파일에 저장합니다 ({e})");
            Stored {
                url,
                token_storage: TokenStorage::File,
                token: Some(token.to_string()),
                cleartext_ok: allow_cleartext,
            }
        }
    };
    let json = serde_json::to_vec_pretty(&stored).map_err(|e| e.to_string())?;
    write_private(&path(dir), &json)
}

fn read(dir: &Path) -> Option<Stored> {
    serde_json::from_slice(&fs::read(path(dir)).ok()?).ok()
}

pub fn load(dir: &Path) -> Option<Connection> {
    read(dir).map(|s| Connection {
        url: s.url,
        token_storage: s.token_storage,
        cleartext_ok: s.cleartext_ok,
    })
}

/// Address and token for Rust-side HTTP calls; refuses an unconsented risky http:// address.
pub fn load_with_token(dir: &Path, store: &dyn SecretStore) -> Result<(String, String), String> {
    let s = read(dir).ok_or_else(|| "저장된 서버 연결이 없습니다".to_string())?;
    if cleartext_risk(&s.url) && !s.cleartext_ok {
        return Err(CLEARTEXT_REFUSED.into());
    }
    let token = match s.token_storage {
        TokenStorage::File => s.token,
        TokenStorage::Keychain => store.get().ok().flatten(),
    };
    let token = token.ok_or_else(|| "저장된 토큰을 읽지 못했습니다".to_string())?;
    Ok((s.url, token))
}

pub fn forget(dir: &Path, store: &dyn SecretStore) -> Result<(), String> {
    let keychain_mode = read(dir).map_or(true, |s| s.token_storage == TokenStorage::Keychain);
    match fs::remove_file(path(dir)) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(format!("연결 정보를 지우지 못했습니다: {e}")),
    }
    match store.delete() {
        Err(e) if keychain_mode => Err(e),
        _ => Ok(()),
    }
}

/// Process-memory token cache so polling does not hit the keychain every time.
#[derive(Default)]
pub struct Session {
    cache: Mutex<Option<(String, String)>>,
}

impl Session {
    /// The lock is held across the change, so a concurrent read cannot re-cache the old token.
    fn locked(&self) -> std::sync::MutexGuard<'_, Option<(String, String)>> {
        self.cache.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn save(
        &self,
        dir: &Path,
        store: &dyn SecretStore,
        url: &str,
        token: &str,
        allow_cleartext: bool,
    ) -> Result<(), String> {
        let mut c = self.locked();
        *c = None;
        let r = save(dir, store, url, token, allow_cleartext);
        *c = None;
        r
    }

    pub fn forget(&self, dir: &Path, store: &dyn SecretStore) -> Result<(), String> {
        let mut c = self.locked();
        *c = None;
        let r = forget(dir, store);
        *c = None;
        r
    }

    pub fn credentials(
        &self,
        dir: &Path,
        store: &dyn SecretStore,
    ) -> Result<(String, String), String> {
        let mut c = self.locked();
        if c.is_none() {
            *c = Some(load_with_token(dir, store)?);
        }
        let (url, token) = c.clone().expect("cache just filled");
        if cleartext_risk(&url) && !load(dir).is_some_and(|x| x.cleartext_ok) {
            *c = None;
            return Err(CLEARTEXT_REFUSED.into());
        }
        Ok((url, token))
    }
}

/// True when an http:// address is outside loopback and the tailnet, so the token
/// would cross the network unencrypted.
pub fn cleartext_risk(raw: &str) -> bool {
    use url::Host;
    let Ok(u) = url::Url::parse(raw.trim()) else {
        return false;
    };
    if u.scheme() != "http" {
        return false;
    }
    match u.host() {
        Some(Host::Ipv4(ip)) => {
            let o = ip.octets();
            !(ip.is_loopback() || (o[0] == 100 && (64..=127).contains(&o[1])))
        }
        Some(Host::Ipv6(ip)) => {
            let s = ip.segments();
            !(ip.is_loopback() || (s[0] == 0xfd7a && s[1] == 0x115c && s[2] == 0xa1e0))
        }
        Some(Host::Domain(d)) => {
            let d = d.to_ascii_lowercase();
            !(d == "localhost" || d.ends_with(".ts.net"))
        }
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[derive(Default)]
    struct Fake {
        secret: RefCell<Option<String>>,
        broken: bool,
    }
    impl SecretStore for Fake {
        fn set(&self, s: &str) -> Result<(), String> {
            if self.broken {
                return Err("no keychain".into());
            }
            *self.secret.borrow_mut() = Some(s.into());
            Ok(())
        }
        fn get(&self) -> Result<Option<String>, String> {
            Ok(self.secret.borrow().clone())
        }
        fn delete(&self) -> Result<(), String> {
            *self.secret.borrow_mut() = None;
            Ok(())
        }
    }

    #[test]
    fn keychain_roundtrip_keeps_token_out_of_file() {
        let d = tempfile::tempdir().unwrap();
        let k = Fake::default();
        save(d.path(), &k, "http://100.64.0.1:8787/", " ttk_op_x ", false).unwrap();
        let raw = fs::read_to_string(d.path().join("connection.json")).unwrap();
        assert!(!raw.contains("ttk_op_x"));
        let c = load(d.path()).unwrap();
        assert_eq!(c.url, "http://100.64.0.1:8787");
        assert_eq!(c.token_storage, TokenStorage::Keychain);
        assert_eq!(
            load_with_token(d.path(), &k),
            Ok(("http://100.64.0.1:8787".into(), "ttk_op_x".into()))
        );
    }

    #[test]
    fn falls_back_to_file_when_keychain_fails() {
        let d = tempfile::tempdir().unwrap();
        let k = Fake {
            broken: true,
            ..Default::default()
        };
        save(d.path(), &k, "https://h.example:9", "ttk_op_y", true).unwrap();
        assert_eq!(load(d.path()).unwrap().token_storage, TokenStorage::File);
        assert_eq!(load_with_token(d.path(), &k).unwrap().1, "ttk_op_y");
    }

    #[cfg(unix)]
    #[test]
    fn file_is_private() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let k = Fake {
            broken: true,
            ..Default::default()
        };
        save(d.path(), &k, "http://h:1", "t", true).unwrap();
        let mode = fs::metadata(d.path().join("connection.json"))
            .unwrap()
            .permissions()
            .mode();
        assert_eq!(mode & 0o777, 0o600);
    }

    #[test]
    fn forget_removes_file_and_secret() {
        let d = tempfile::tempdir().unwrap();
        let k = Fake::default();
        save(d.path(), &k, "http://h:1", "t", true).unwrap();
        forget(d.path(), &k).unwrap();
        assert!(load(d.path()).is_none());
        assert!(k.secret.borrow().is_none());
        forget(d.path(), &k).unwrap();
    }

    #[test]
    fn rejects_bad_input() {
        let d = tempfile::tempdir().unwrap();
        let k = Fake::default();
        assert!(save(d.path(), &k, "ftp://h", "t", true).is_err());
        assert!(save(d.path(), &k, "http://h:1/api", "t", true).is_err());
        assert!(save(d.path(), &k, "http://h:1", "  ", true).is_err());
        assert!(load(d.path()).is_none());
    }

    #[cfg(unix)]
    #[test]
    fn write_does_not_follow_symlinked_tmp_and_keeps_0600() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let d = tempfile::tempdir().unwrap();
        let victim = d.path().join("victim");
        fs::write(&victim, "keep").unwrap();
        symlink(&victim, d.path().join("connection.json.tmp")).unwrap();
        symlink(&victim, d.path().join("connection.json")).unwrap();
        let k = Fake {
            broken: true,
            ..Default::default()
        };
        save(d.path(), &k, "http://h:1", "tok", true).unwrap();
        assert_eq!(fs::read_to_string(&victim).unwrap(), "keep");
        let meta = fs::symlink_metadata(d.path().join("connection.json")).unwrap();
        assert!(meta.file_type().is_file());
        assert_eq!(meta.permissions().mode() & 0o777, 0o600);
        let leftovers: Vec<_> = fs::read_dir(d.path())
            .unwrap()
            .map(|e| e.unwrap().file_name().into_string().unwrap())
            .filter(|n| n.ends_with(".tmp") && n != "connection.json.tmp")
            .collect();
        assert!(leftovers.is_empty(), "{leftovers:?}");
    }

    #[cfg(unix)]
    #[test]
    fn created_config_dir_is_0700() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let sub = d.path().join("cfg");
        save(&sub, &Fake::default(), "http://h:1", "t", true).unwrap();
        assert_eq!(
            fs::metadata(&sub).unwrap().permissions().mode() & 0o777,
            0o700
        );
    }

    #[test]
    fn forget_clears_keychain_even_when_file_mode() {
        let d = tempfile::tempdir().unwrap();
        let k = Fake::default();
        *k.secret.borrow_mut() = Some("stale".into());
        let broken = Fake {
            broken: true,
            ..Default::default()
        };
        save(d.path(), &broken, "http://h:1", "t", true).unwrap();
        forget(d.path(), &k).unwrap();
        assert!(k.secret.borrow().is_none());
        assert!(load(d.path()).is_none());
    }

    #[test]
    fn debug_redacts_token() {
        let s = Stored {
            url: "http://h:1".into(),
            token_storage: TokenStorage::File,
            token: Some("ttk_op_secret".into()),
            cleartext_ok: false,
        };
        assert!(!format!("{s:?}").contains("ttk_op_secret"));
    }

    #[test]
    fn normalize_url_rejects_query_and_fragment() {
        assert!(normalize_url("http://h:1/?a=1").is_err());
        assert!(normalize_url("http://h:1/#x").is_err());
        assert!(normalize_url("not a url").is_err());
        assert_eq!(
            normalize_url(" https://h.example:9/ ").unwrap(),
            "https://h.example:9"
        );
    }

    #[test]
    fn session_caches_token_until_save_or_forget() {
        use std::cell::Cell;
        struct Counting(Fake, Cell<u32>);
        impl SecretStore for Counting {
            fn set(&self, s: &str) -> Result<(), String> {
                self.0.set(s)
            }
            fn get(&self) -> Result<Option<String>, String> {
                self.1.set(self.1.get() + 1);
                self.0.get()
            }
            fn delete(&self) -> Result<(), String> {
                self.0.delete()
            }
        }
        let d = tempfile::tempdir().unwrap();
        let k = Counting(Fake::default(), Cell::new(0));
        let s = Session::default();
        s.save(d.path(), &k, "http://h:1", "one", true).unwrap();
        assert_eq!(s.credentials(d.path(), &k).unwrap().1, "one");
        assert_eq!(s.credentials(d.path(), &k).unwrap().1, "one");
        assert_eq!(k.1.get(), 1);
        s.save(d.path(), &k, "http://h:1", "two", true).unwrap();
        assert_eq!(s.credentials(d.path(), &k).unwrap().1, "two");
        s.forget(d.path(), &k).unwrap();
        assert!(s.credentials(d.path(), &k).is_err());
    }

    #[test]
    fn cleartext_risk_only_outside_loopback_and_tailnet() {
        for ok in [
            "https://example.com",
            "http://127.0.0.1:8787",
            "http://localhost:1",
            "http://[::1]:1",
            "http://100.64.0.1:8787",
            "http://100.127.255.254",
            "http://host.tail1234.ts.net:8787",
            "http://[fd7a:115c:a1e0::1]:1",
        ] {
            assert!(!cleartext_risk(ok), "{ok}");
        }
        for risky in [
            "http://192.168.1.5:8787",
            "http://100.128.0.1",
            "http://100.63.0.1",
            "http://example.com",
            "http://evil.ts.net.example.com",
            "http://10.0.0.1",
        ] {
            assert!(cleartext_risk(risky), "{risky}");
        }
    }

    #[test]
    fn save_refuses_risky_http_without_consent_and_stores_consent() {
        let d = tempfile::tempdir().unwrap();
        let k = Fake::default();
        let err = save(d.path(), &k, "http://192.168.1.5:8787", "t", false).unwrap_err();
        assert!(err.contains("암호화"), "{err}");
        assert!(load(d.path()).is_none());
        assert!(k.secret.borrow().is_none());
        save(d.path(), &k, "http://192.168.1.5:8787", "t", true).unwrap();
        let raw = fs::read_to_string(d.path().join("connection.json")).unwrap();
        assert!(raw.contains("\"cleartext_ok\": true"), "{raw}");
        assert!(load_with_token(d.path(), &k).is_ok());
        save(d.path(), &k, "https://h.example", "t", false).unwrap();
        assert!(load_with_token(d.path(), &k).is_ok());
    }

    #[test]
    fn hand_edited_risky_url_without_consent_is_refused_on_every_call() {
        let d = tempfile::tempdir().unwrap();
        let k = Fake::default();
        save(d.path(), &k, "http://100.64.0.1:8787", "t", false).unwrap();
        fs::write(
            d.path().join("connection.json"),
            r#"{"url":"http://192.168.1.5:8787","token_storage":"keychain"}"#,
        )
        .unwrap();
        let err = load_with_token(d.path(), &k).unwrap_err();
        assert!(err.contains("암호화"), "{err}");
        let s = Session::default();
        assert!(s.credentials(d.path(), &k).is_err());
        assert!(s.credentials(d.path(), &k).is_err());
    }

    #[test]
    fn save_racing_a_credentials_read_never_leaves_the_old_token_cached() {
        use std::sync::mpsc::channel;
        use std::sync::Mutex;
        use std::time::Duration;
        struct Gated {
            secret: Mutex<Option<String>>,
            entered: Mutex<Option<std::sync::mpsc::Sender<()>>>,
            release: Mutex<Option<std::sync::mpsc::Receiver<()>>>,
        }
        impl SecretStore for Gated {
            fn set(&self, s: &str) -> Result<(), String> {
                if let Some(tx) = self.entered.lock().unwrap().take() {
                    tx.send(()).unwrap();
                    let rx = self.release.lock().unwrap().take().unwrap();
                    rx.recv().unwrap();
                }
                *self.secret.lock().unwrap() = Some(s.into());
                Ok(())
            }
            fn get(&self) -> Result<Option<String>, String> {
                Ok(self.secret.lock().unwrap().clone())
            }
            fn delete(&self) -> Result<(), String> {
                *self.secret.lock().unwrap() = None;
                Ok(())
            }
        }
        let d = tempfile::tempdir().unwrap();
        let (entered_tx, entered_rx) = channel();
        let (release_tx, release_rx) = channel();
        let store = Gated {
            secret: Mutex::new(None),
            entered: Mutex::new(None),
            release: Mutex::new(None),
        };
        let s = Session::default();
        s.save(d.path(), &store, "http://h:1", "one", true).unwrap();
        *store.entered.lock().unwrap() = Some(entered_tx);
        *store.release.lock().unwrap() = Some(release_rx);
        std::thread::scope(|sc| {
            let saver = sc.spawn(|| s.save(d.path(), &store, "http://h:1", "two", true));
            entered_rx.recv().unwrap();
            let reader = sc.spawn(|| s.credentials(d.path(), &store));
            std::thread::sleep(Duration::from_millis(100));
            release_tx.send(()).unwrap();
            saver.join().unwrap().unwrap();
            let _ = reader.join().unwrap();
        });
        assert_eq!(s.credentials(d.path(), &store).unwrap().1, "two");
    }
}
