//! All server traffic goes through here, so the target is the saved address only.

use serde::Serialize;
use serde_json::Value;
use std::time::Duration;

pub const UNREACHABLE: &str = "주소에 닿지 않음";
pub const BAD_TOKEN: &str = "토큰이 틀림 (401)";
pub const NOT_TTALKKAK: &str = "ttalkkak 서버가 아님";

#[derive(Serialize)]
pub struct ApiResponse {
    pub status: u16,
    pub body: String,
}

/// Only `/api/...` paths on the saved server; nothing that could change host.
pub fn check_path(path: &str) -> Result<(), String> {
    let ok = path.starts_with("/api/")
        && !path.starts_with("/api//")
        && !path.contains(['\\', '@', '#'])
        && !path.contains("..")
        && !path.contains("://");
    if ok {
        Ok(())
    } else {
        Err("허용되지 않은 요청 경로입니다".into())
    }
}

pub fn classify_ping(status: u16, body: &str) -> Result<Value, String> {
    if status == 401 {
        return Err(BAD_TOKEN.into());
    }
    let v: Value = serde_json::from_str(body).map_err(|_| NOT_TTALKKAK.to_string())?;
    if (200..300).contains(&status) && v.get("product").and_then(Value::as_str) == Some("ttalkkak")
    {
        Ok(v)
    } else {
        Err(NOT_TTALKKAK.into())
    }
}

fn client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|e| e.to_string())
}

pub async fn send(
    base: &str,
    token: &str,
    method: &str,
    path: &str,
    body: Option<String>,
) -> Result<ApiResponse, String> {
    check_path(path)?;
    let method = reqwest::Method::from_bytes(method.as_bytes()).map_err(|e| e.to_string())?;
    if !matches!(method.as_str(), "GET" | "POST") {
        return Err("허용되지 않은 요청 방식입니다".into());
    }
    let mut req = client()?
        .request(method, format!("{base}{path}"))
        .bearer_auth(token);
    if let Some(b) = body {
        req = req.header("content-type", "application/json").body(b);
    }
    let res = req.send().await.map_err(|_| UNREACHABLE.to_string())?;
    let status = res.status().as_u16();
    let body = res.text().await.map_err(|_| UNREACHABLE.to_string())?;
    Ok(ApiResponse { status, body })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_is_confined_to_api() {
        assert!(check_path("/api/ping").is_ok());
        assert!(check_path("/api/live?x=1").is_ok());
        for bad in [
            "/",
            "api/ping",
            "//evil.example/api/x",
            "/api//evil",
            "/api/../x",
            "/api/@h",
            "http://evil.example/api/x",
            "/api/a://b",
            "/apix",
        ] {
            assert!(check_path(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn ping_reasons() {
        let ok = r#"{"product":"ttalkkak","version":"1"}"#;
        assert!(classify_ping(200, ok).is_ok());
        assert_eq!(classify_ping(401, "{}").unwrap_err(), BAD_TOKEN);
        assert_eq!(
            classify_ping(200, r#"{"product":"other"}"#).unwrap_err(),
            NOT_TTALKKAK
        );
        assert_eq!(classify_ping(200, "<html>").unwrap_err(), NOT_TTALKKAK);
        assert_eq!(classify_ping(404, ok).unwrap_err(), NOT_TTALKKAK);
    }
}
