use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

const MAX_STEM: usize = 60;
const MAX_CONTENT: usize = 20 * 1024 * 1024;

/// A plain `<stem>.csv` file name: no separators, no dots in the stem.
pub fn sanitize_name(name: &str) -> String {
    let stem = name.rsplit(['/', '\\']).next().unwrap_or("");
    let stem = stem.strip_suffix(".csv").unwrap_or(stem);
    let cleaned: String = stem
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .take(MAX_STEM)
        .collect();
    let cleaned = cleaned.trim_matches('_');
    let stem = if cleaned.is_empty() {
        "export"
    } else {
        cleaned
    };
    format!("{stem}.csv")
}

/// Writes `content` under a sanitized name; never overwrites an existing file.
pub fn save_into(dir: &Path, name: &str, content: &str) -> Result<PathBuf, String> {
    if content.len() > MAX_CONTENT {
        return Err("CSV가 너무 커서 저장하지 못했습니다".into());
    }
    let err = |e: std::io::Error| format!("CSV를 저장하지 못했습니다: {e}");
    fs::create_dir_all(dir).map_err(err)?;
    let clean = sanitize_name(name);
    let stem = clean.trim_end_matches(".csv");
    for n in 0..1000 {
        let file = if n == 0 {
            clean.clone()
        } else {
            format!("{stem}-{n}.csv")
        };
        let path = dir.join(file);
        match fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
        {
            Ok(mut f) => {
                f.write_all(content.as_bytes()).map_err(err)?;
                return Ok(path);
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(err(e)),
        }
    }
    Err("같은 이름의 파일이 너무 많아 저장하지 못했습니다".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_have_no_separators_or_dots_and_end_in_csv() {
        for bad in [
            "../../etc/passwd",
            "..\\..\\x",
            "a/b/c.csv",
            "..",
            "",
            "/",
            ".csv",
            "a b\u{0}c",
            "x.csv.csv",
        ] {
            let n = sanitize_name(bad);
            assert!(n.ends_with(".csv"), "{bad:?} -> {n:?}");
            let stem = &n[..n.len() - 4];
            assert!(
                !stem.contains(['/', '\\', '.', '\u{0}']) && !stem.is_empty(),
                "{bad:?} -> {n:?}"
            );
        }
        assert_eq!(sanitize_name("invoice-2026_11.csv"), "invoice-2026_11.csv");
        assert!(sanitize_name(&"a".repeat(500)).len() <= MAX_STEM + 4);
    }

    #[test]
    fn existing_files_are_never_overwritten() {
        let d = tempfile::tempdir().unwrap();
        let a = save_into(d.path(), "../invoice.csv", "one").unwrap();
        let b = save_into(d.path(), "invoice.csv", "two").unwrap();
        assert_ne!(a, b);
        assert_eq!(a.parent().unwrap(), d.path());
        assert_eq!(fs::read_to_string(&a).unwrap(), "one");
        assert_eq!(fs::read_to_string(&b).unwrap(), "two");
    }

    #[test]
    fn oversized_content_is_refused() {
        let d = tempfile::tempdir().unwrap();
        assert!(save_into(d.path(), "x", &"a".repeat(MAX_CONTENT + 1)).is_err());
    }
}
