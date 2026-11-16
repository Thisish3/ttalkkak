#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod conn;
mod download;
mod http;

use conn::{Connection, Keychain, Session};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;

const DOCS_URL: &str = "https://github.com/Thisish3/ttalkkak#readme";

fn config_dir(app: &AppHandle) -> Result<std::path::PathBuf, String> {
    app.path().app_config_dir().map_err(|e| e.to_string())
}

#[tauri::command]
fn save_connection(
    app: AppHandle,
    session: tauri::State<Session>,
    url: String,
    token: String,
    allow_cleartext: bool,
) -> Result<(), String> {
    session.save(&config_dir(&app)?, &Keychain, &url, &token, allow_cleartext)
}

#[tauri::command]
fn load_connection(app: AppHandle) -> Option<Connection> {
    conn::load(&config_dir(&app).ok()?)
}

#[tauri::command]
fn forget_connection(app: AppHandle, session: tauri::State<Session>) -> Result<(), String> {
    session.forget(&config_dir(&app)?, &Keychain)
}

/// Connection test for the entered (not yet saved) address.
#[tauri::command]
async fn ping_server(
    url: String,
    token: String,
    allow_cleartext: bool,
) -> Result<serde_json::Value, String> {
    let base = conn::normalize_url(&url)?;
    if conn::cleartext_risk(&base) && !allow_cleartext {
        return Err("암호화되지 않은 주소로 토큰을 보내려면 확인이 필요합니다".into());
    }
    let res = http::send(&base, token.trim(), "GET", "/api/ping", None).await?;
    http::classify_ping(res.status, &res.body)
}

/// Whether the page must ask the operator before sending the token to this address.
#[tauri::command]
fn url_cleartext_risk(url: String) -> bool {
    conn::cleartext_risk(&url)
}

/// Requests to the saved server only; the token never reaches the page.
#[tauri::command]
async fn api_request(
    app: AppHandle,
    session: tauri::State<'_, Session>,
    path: String,
    method: String,
    body: Option<String>,
) -> Result<http::ApiResponse, String> {
    let dir = config_dir(&app)?;
    let (base, token) = session.credentials(&dir, &Keychain)?;
    http::send(&base, &token, &method.to_uppercase(), &path, body).await
}

/// Saves a CSV into the OS Downloads folder and returns the path.
#[tauri::command]
fn save_csv(app: AppHandle, name: String, content: String) -> Result<String, String> {
    let dir = app
        .path()
        .download_dir()
        .map_err(|_| "다운로드 폴더를 찾지 못했습니다".to_string())?;
    download::save_into(&dir, &name, &content).map(|p| p.display().to_string())
}

fn build_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let item = |id: &str, text: &str| MenuItem::with_id(app, id, text, true, None::<&str>);
    let app_menu = Submenu::with_items(
        app,
        "앱",
        true,
        &[
            &PredefinedMenuItem::about(app, Some("ttalkkak 콘솔 정보"), None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::quit(app, Some("종료"))?,
        ],
    )?;
    let edit_menu = Submenu::with_items(
        app,
        "편집",
        true,
        &[
            &PredefinedMenuItem::undo(app, Some("실행 취소"))?,
            &PredefinedMenuItem::redo(app, Some("다시 실행"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, Some("잘라내기"))?,
            &PredefinedMenuItem::copy(app, Some("복사"))?,
            &PredefinedMenuItem::paste(app, Some("붙여넣기"))?,
            &PredefinedMenuItem::select_all(app, Some("모두 선택"))?,
        ],
    )?;
    let conn_menu = Submenu::with_items(
        app,
        "연결",
        true,
        &[
            &item("change_server", "서버 바꾸기")?,
            &item("disconnect", "연결 끊기")?,
        ],
    )?;
    let view_menu = Submenu::with_items(
        app,
        "보기",
        true,
        &[
            &item("reload", "새로 고침")?,
            &PredefinedMenuItem::fullscreen(app, Some("전체 화면"))?,
        ],
    )?;
    let help_menu = Submenu::with_items(app, "도움말", true, &[&item("docs", "문서 열기")?])?;
    Menu::with_items(
        app,
        &[&app_menu, &edit_menu, &conn_menu, &view_menu, &help_menu],
    )
}

fn on_menu(app: &AppHandle, id: &str) {
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    match id {
        "change_server" => {
            let _ = win.eval("location.replace('connect.html')");
        }
        "disconnect" => {
            let result =
                config_dir(app).and_then(|dir| app.state::<Session>().forget(&dir, &Keychain));
            if let Err(e) = result {
                eprintln!("연결 끊기 실패: {e}");
                return;
            }
            let _ = win.eval("location.replace('connect.html')");
        }
        "reload" => {
            let _ = win.eval("location.reload()");
        }
        "docs" => {
            let _ = app.opener().open_url(DOCS_URL, None::<&str>);
        }
        _ => {}
    }
}

fn main() {
    tauri::Builder::default()
        .manage(Session::default())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_opener::init())
        .menu(build_menu)
        .on_menu_event(|app, event| on_menu(app, event.id().as_ref()))
        .invoke_handler(tauri::generate_handler![
            save_connection,
            load_connection,
            forget_connection,
            ping_server,
            url_cleartext_risk,
            api_request,
            save_csv
        ])
        .run(tauri::generate_context!())
        .expect("ttalkkak 콘솔을 시작하지 못했습니다");
}
