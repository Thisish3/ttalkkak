//! Saved server connection: address in `connection.json`, token in the OS keychain.

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

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

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
struct Stored {
    url: String,
    token_storage: TokenStorage,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    token: Option<String>,
}

/// What the page may see: never the token itself.
#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Connection {
    pub url: String,
    pub token_storage: TokenStorage,
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

fn write_private(file: &Path, data: &[u8]) -> Result<(), String> {
    let tmp = file.with_extension("json.tmp");
    let err = |e: std::io::Error| format!("연결 정보를 저장하지 못했습니다: {e}");
    let mut opts = fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut f = opts.open(&tmp).map_err(err)?;
    f.write_all(data).map_err(err)?;
    f.sync_all().map_err(err)?;
    fs::rename(&tmp, file).map_err(err)
}

pub fn save(dir: &Path, store: &dyn SecretStore, url: &str, token: &str) -> Result<(), String> {
    let url = normalize_url(url)?;
    let token = token.trim();
    if token.is_empty() {
        return Err("운영자 토큰이 비어 있습니다".into());
    }
    fs::create_dir_all(dir).map_err(|e| format!("설정 폴더를 만들지 못했습니다: {e}"))?;
    let stored = match store.set(token) {
        Ok(()) => Stored {
            url,
            token_storage: TokenStorage::Keychain,
            token: None,
        },
        Err(e) => {
            eprintln!("경고: OS 키체인에 토큰을 저장하지 못해 설정 파일에 저장합니다 ({e})");
            Stored {
                url,
                token_storage: TokenStorage::File,
                token: Some(token.to_string()),
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
    })
}

/// Address and token for Rust-side HTTP calls.
pub fn load_with_token(dir: &Path, store: &dyn SecretStore) -> Option<(String, String)> {
    let s = read(dir)?;
    let token = match s.token_storage {
        TokenStorage::File => s.token?,
        TokenStorage::Keychain => store.get().ok()??,
    };
    Some((s.url, token))
}

pub fn forget(dir: &Path, store: &dyn SecretStore) -> Result<(), String> {
    let stored = read(dir);
    match fs::remove_file(path(dir)) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(format!("연결 정보를 지우지 못했습니다: {e}")),
    }
    if stored.map_or(true, |s| s.token_storage == TokenStorage::Keychain) {
        store.delete()?;
    }
    Ok(())
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
        save(d.path(), &k, "http://100.64.0.1:8787/", " ttk_op_x ").unwrap();
        let raw = fs::read_to_string(d.path().join("connection.json")).unwrap();
        assert!(!raw.contains("ttk_op_x"));
        let c = load(d.path()).unwrap();
        assert_eq!(c.url, "http://100.64.0.1:8787");
        assert_eq!(c.token_storage, TokenStorage::Keychain);
        assert_eq!(
            load_with_token(d.path(), &k),
            Some(("http://100.64.0.1:8787".into(), "ttk_op_x".into()))
        );
    }

    #[test]
    fn falls_back_to_file_when_keychain_fails() {
        let d = tempfile::tempdir().unwrap();
        let k = Fake {
            broken: true,
            ..Default::default()
        };
        save(d.path(), &k, "https://h.example:9", "ttk_op_y").unwrap();
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
        save(d.path(), &k, "http://h:1", "t").unwrap();
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
        save(d.path(), &k, "http://h:1", "t").unwrap();
        forget(d.path(), &k).unwrap();
        assert!(load(d.path()).is_none());
        assert!(k.secret.borrow().is_none());
        forget(d.path(), &k).unwrap();
    }

    #[test]
    fn rejects_bad_input() {
        let d = tempfile::tempdir().unwrap();
        let k = Fake::default();
        assert!(save(d.path(), &k, "ftp://h", "t").is_err());
        assert!(save(d.path(), &k, "http://h:1/api", "t").is_err());
        assert!(save(d.path(), &k, "http://h:1", "  ").is_err());
        assert!(load(d.path()).is_none());
    }
}
