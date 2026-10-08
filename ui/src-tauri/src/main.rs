use std::{fs, path::PathBuf, process::Command, time::{SystemTime, UNIX_EPOCH}};

const UI_COMMANDS: &[&str] = &[
    "aim-create", "proposal-create", "proposal-revise", "goal-accept", "attempt-start", "attempt-complete",
    "evidence-record", "evidence-correct", "verification-begin", "verdict-submit",
    "retry-decide", "reverification-decide", "aim-evaluate", "contract-revise",
    "verdict-review-resolve", "aim-evaluation-review-resolve", "aim-closure-invalidate",
    "aim-pause", "aim-resume", "goal-hold-add", "goal-hold-clear", "goal-parent-add",
    "goal-dependency-add", "proposal-close", "goal-stop", "aim-abandon",
    "reference-add", "reference-remove", "aim-parent-add",
];

/// Opening one of these would run it, so the app reveals it in Explorer instead.
const EXECUTABLE_EXTENSIONS: &[&str] = &[
    "exe", "bat", "cmd", "com", "ps1", "psm1", "msi", "msp", "lnk", "vbs", "vbe", "js", "jse", "wsf", "wsh", "scr", "cpl", "hta", "reg", "pif", "appref-ms",
];

/// A journal stored in `<folder>/.goal-loop/` belongs to `<folder>`.
fn project_root_of(database: &str) -> Option<PathBuf> {
    let path = PathBuf::from(database);
    let parent = path.parent()?;
    if parent.file_name()? == ".goal-loop" { parent.parent().map(PathBuf::from) } else { None }
}

fn save_registry(records: &[serde_json::Value]) -> Result<(), String> {
    fs::write(workspace_registry_path()?, serde_json::to_vec_pretty(records).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn pick_folder(app: tauri::AppHandle) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    app.dialog().file().blocking_pick_folder().and_then(|path| path.into_path().ok()).map(|path| path.display().to_string())
}

#[tauri::command]
async fn pick_file(app: tauri::AppHandle, directory: Option<String>) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    let mut dialog = app.dialog().file();
    if let Some(directory) = directory { dialog = dialog.set_directory(directory); }
    dialog.blocking_pick_file().and_then(|path| path.into_path().ok()).map(|path| path.display().to_string())
}

/// Register a project folder. The CLI creates `.goal-loop/` (idempotently); the registry only
/// remembers the folder, so removing the workspace later never deletes project files.
fn workspace_open_project_blocking(name: String, folder: String) -> Result<serde_json::Value, String> {
    let name = name.trim();
    if name.is_empty() || name.chars().count() > 80 {
        return Err("작업 공간 이름은 1~80자로 입력하세요.".into());
    }
    let folder = PathBuf::from(folder.trim());
    if !folder.is_dir() { return Err(format!("프로젝트 폴더를 찾지 못했습니다: {}", folder.display())); }
    let initialized = run_goal_loop("project-init", &[folder.display().to_string()], None)?;
    let database = initialized.get("database").and_then(|value| value.as_str()).ok_or("project-init 결과에 DB 경로가 없습니다.")?.to_owned();
    let project = initialized.get("project_root").and_then(|value| value.as_str()).ok_or("project-init 결과에 프로젝트 경로가 없습니다.")?.to_owned();
    let mut records = workspace_records()?;
    if let Some(existing) = records.iter().find(|item| item.get("database").and_then(|value| value.as_str()) == Some(database.as_str())) {
        return Ok(existing.clone());
    }
    let id = format!("workspace-{}", SystemTime::now().duration_since(UNIX_EPOCH).map_err(|error| error.to_string())?.as_millis());
    let record = serde_json::json!({"id": id, "name": name, "project": project, "database": database});
    records.push(record.clone());
    save_registry(&records)?;
    Ok(record)
}

/// Open a reference or attachment with its default program. Only an existing path or an http(s)
/// URL is accepted, and executables are revealed rather than run.
fn open_location_blocking(location: String) -> Result<(), String> {
    let lower = location.to_lowercase();
    if lower.starts_with("http://") || lower.starts_with("https://") {
        Command::new("rundll32").args(["url.dll,FileProtocolHandler", &location]).spawn().map_err(|error| error.to_string())?;
        return Ok(());
    }
    let path = PathBuf::from(&location);
    if !path.exists() { return Err(format!("파일을 찾지 못했습니다: {location}")); }
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or("").to_lowercase();
    if path.is_file() && EXECUTABLE_EXTENSIONS.contains(&extension.as_str()) {
        Command::new("explorer").arg(format!("/select,{}", path.display())).spawn().map_err(|error| error.to_string())?;
    } else {
        Command::new("explorer").arg(&path).spawn().map_err(|error| error.to_string())?;
    }
    Ok(())
}

fn workspace_registry_path() -> Result<PathBuf, String> {
    let root = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .ok_or("LOCALAPPDATA is not available; cannot locate Goal Loop workspaces.")?
        .join("GoalLoopTool");
    fs::create_dir_all(&root).map_err(|error| error.to_string())?;
    Ok(root.join("workspaces.json"))
}

fn workspace_records() -> Result<Vec<serde_json::Value>, String> {
    let path = workspace_registry_path()?;
    let root = path.parent().ok_or("Workspace registry has no parent directory.")?;
    let mut records = match fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str::<Vec<serde_json::Value>>(&text)
            .map_err(|error| format!("Cannot read workspace registry {path:?}: {error}"))?,
        // Preserve the former shared default database as a deletable first workspace during the
        // one-time UI migration. Fresh installations with no database start with no workspace.
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let legacy = root.join("goal-loop.sqlite3");
            if legacy.is_file() { vec![serde_json::json!({"id": "workspace-0", "name": "기존 작업 공간", "database": legacy})] } else { Vec::new() }
        },
        Err(error) => return Err(error.to_string()),
    };
    for record in &mut records {
        if record.get("id") == Some(&serde_json::Value::String("default".into())) {
            *record = serde_json::json!({"id": "workspace-0", "name": record.get("name").and_then(|value| value.as_str()).unwrap_or("기존 작업 공간"), "database": root.join("goal-loop.sqlite3")});
        }
    }
    Ok(records)
}

fn workspaces_blocking() -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({"workspaces": workspace_records()?}))
}

/// A workspace is a separate SQLite journal, not a new Goal Loop domain record.  The registry
/// contains only local display names and paths; selecting it causes the existing CLI to initialize
/// the database with its current schema on first read.
fn workspace_create_blocking(name: String) -> Result<serde_json::Value, String> {
    let name = name.trim();
    if name.is_empty() || name.chars().count() > 80 {
        return Err("작업 공간 이름은 1~80자로 입력하세요.".into());
    }
    let id = format!("workspace-{}", SystemTime::now().duration_since(UNIX_EPOCH)
        .map_err(|error| error.to_string())?.as_millis());
    let registry = workspace_registry_path()?;
    let database = registry.parent().ok_or("Workspace registry has no parent directory.")?
        .join("workspaces").join(format!("{id}.sqlite3"));
    let mut records = workspace_records()?;
    let record = serde_json::json!({"id": id, "name": name, "database": database});
    records.push(record.clone());
    fs::write(&registry, serde_json::to_vec_pretty(&records).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())?;
    Ok(record)
}

fn workspace_rename_blocking(workspace_id: String, name: String) -> Result<serde_json::Value, String> {
    let name = name.trim();
    if name.is_empty() || name.chars().count() > 80 {
        return Err("작업 공간 이름은 1~80자로 입력하세요.".into());
    }
    let registry = workspace_registry_path()?;
    let mut records = workspace_records()?;
    let record = records.iter_mut().find(|item| item.get("id") == Some(&serde_json::Value::String(workspace_id.clone())))
        .ok_or("이름을 바꿀 작업 공간을 찾지 못했습니다.")?;
    record["name"] = serde_json::Value::String(name.to_owned());
    let renamed = record.clone();
    fs::write(&registry, serde_json::to_vec_pretty(&records).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())?;
    Ok(renamed)
}

/// Deletion is intentionally limited to a workspace generated by this app. It never accepts an
/// arbitrary database path, never removes a directory, and also clears SQLite sidecars.
fn workspace_delete_blocking(workspace_id: String) -> Result<serde_json::Value, String> {
    if !workspace_id.strip_prefix("workspace-").is_some_and(|value| !value.is_empty() && value.chars().all(|ch| ch.is_ascii_digit())) {
        return Err("삭제할 작업 공간 ID가 올바르지 않습니다.".into());
    }
    let registry = workspace_registry_path()?;
    let root = registry.parent().ok_or("Workspace registry has no parent directory.")?.join("workspaces");
    let mut records = workspace_records()?;
    let position = records.iter().position(|item| item.get("id") == Some(&serde_json::Value::String(workspace_id.clone())))
        .ok_or("삭제할 작업 공간을 찾지 못했습니다.")?;
    let record = &records[position];
    // A project folder belongs to the user: removing it from the app only forgets the registration.
    if record.get("project").is_some() {
        records.remove(position);
        save_registry(&records)?;
        return Ok(serde_json::json!({"deleted_workspace_id": workspace_id, "unregistered_only": true, "workspaces": records}));
    }
    let database = record.get("database").and_then(|value| value.as_str()).ok_or("작업 공간 DB 경로가 없습니다.")?;
    let database = PathBuf::from(database);
    let expected = if workspace_id == "workspace-0" { registry.parent().ok_or("Workspace registry has no parent directory.")?.join("goal-loop.sqlite3") } else { root.join(format!("{workspace_id}.sqlite3")) };
    if database != expected {
        return Err("안전을 위해 앱이 만든 작업 공간 DB만 삭제할 수 있습니다.".into());
    }
    for suffix in ["", "-wal", "-shm"] {
        let candidate = PathBuf::from(format!("{}{}", expected.display(), suffix));
        match fs::remove_file(&candidate) {
            Ok(()) => {},
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {},
            Err(error) => return Err(format!("작업 공간 DB를 삭제하지 못했습니다 ({candidate:?}): {error}")),
        }
    }
    records.remove(position);
    fs::write(&registry, serde_json::to_vec_pretty(&records).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())?;
    Ok(serde_json::json!({"deleted_workspace_id": workspace_id, "workspaces": records}))
}

/// Resolve the CLI: GOAL_LOOP_EXECUTABLE, else `goal-loop` on PATH (where pipx or pip installs it).
/// Only a development build (`tauri dev`) falls back to this checkout's .venv; a release build must
/// not carry the build machine's source path or prefer that checkout over the installed CLI.
fn goal_loop_executable() -> PathBuf {
    if let Some(value) = std::env::var_os("GOAL_LOOP_EXECUTABLE") {
        return PathBuf::from(value);
    }
    #[cfg(debug_assertions)]
    {
        let project_venv = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .join(".venv")
            .join("Scripts")
            .join("goal-loop.exe");
        if project_venv.is_file() {
            return project_venv;
        }
    }
    PathBuf::from("goal-loop")
}

fn run_goal_loop(command_name: &str, args: &[String], database: Option<String>) -> Result<serde_json::Value, String> {
    let executable = goal_loop_executable();
    let mut command = Command::new(&executable);
    if let Some(path) = database {
        // A relative reference typed in the app means "inside this project", as it does in a shell there.
        if let Some(root) = project_root_of(&path) { command.current_dir(root); }
        command.args(["--database", &path]);
    }
    let output = command.arg(command_name).args(args).output().map_err(|error| format!(
        "Goal Loop CLI를 시작하지 못했습니다 ({executable:?}): {error}. 개발 환경에서는 프로젝트 .venv를 준비하거나 GOAL_LOOP_EXECUTABLE을 설정하세요."
    ))?;
    if !output.status.success() { return Err(String::from_utf8_lossy(&output.stderr).trim().to_string()); }
    serde_json::from_slice(&output.stdout).map_err(|error| error.to_string())
}

/// `activity` intentionally emits one JSON card per line for terminal streaming. Convert that
/// read-only stream to an array for the GUI without changing the CLI contract.
fn run_goal_loop_records(command_name: &str, database: Option<String>) -> Result<Vec<serde_json::Value>, String> {
    let executable = goal_loop_executable();
    let mut command = Command::new(&executable);
    if let Some(path) = database { command.args(["--database", &path]); }
    let output = command.arg(command_name).output().map_err(|error| format!(
        "Goal Loop CLI를 시작하지 못했습니다 ({executable:?}): {error}. 개발 환경에서는 프로젝트 .venv를 준비하거나 GOAL_LOOP_EXECUTABLE을 설정하세요."
    ))?;
    if !output.status.success() { return Err(String::from_utf8_lossy(&output.stderr).trim().to_string()); }
    String::from_utf8_lossy(&output.stdout).lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).map_err(|error| error.to_string()))
        .collect()
}

fn ui_projection_blocking(database: Option<String>) -> Result<serde_json::Value, String> { run_goal_loop("ui-projection", &[], database) }

fn next_actions_blocking(database: Option<String>) -> Result<serde_json::Value, String> { run_goal_loop("next", &[], database) }

/// Read-only operational projections. They remain separate from `ui-projection` so the
/// versioned graph contract stays small and can also be consumed by an Obsidian shell.
fn reviews_blocking(database: Option<String>) -> Result<serde_json::Value, String> { run_goal_loop("reviews", &[], database) }

fn admission_blocking(database: Option<String>) -> Result<serde_json::Value, String> { run_goal_loop("admission", &[], database) }

fn runtime_requests_blocking(database: Option<String>) -> Result<serde_json::Value, String> { run_goal_loop("runtime-requests", &[], database) }

/// Read-only AO run state (AO restructure S3): AO Goal, last AO session, waiting human input, next commands.
fn ao_status_blocking(database: Option<String>) -> Result<serde_json::Value, String> { run_goal_loop("ao-status", &[], database) }

fn activity_blocking(database: Option<String>) -> Result<Vec<serde_json::Value>, String> { run_goal_loop_records("activity", database) }

/// Explicit allow-list; values are passed directly to Command, never through a shell.
fn goal_loop_command_blocking(command_name: String, args: Vec<String>, database: Option<String>) -> Result<serde_json::Value, String> {
    if !UI_COMMANDS.contains(&command_name.as_str()) { return Err(format!("Unsupported UI lifecycle command: {command_name}")); }
    run_goal_loop(&command_name, &args, database)
}

/// Tauri v2 runs synchronous commands on the main thread. Every command below starts goal-loop
/// processes (or touches files), so running them there froze the window -- painting and dragging --
/// until they finished, each time the window regained focus and reloaded. They run on the blocking
/// pool instead; their names, arguments and results are unchanged.
async fn off_main<T: Send + 'static>(work: impl FnOnce() -> Result<T, String> + Send + 'static) -> Result<T, String> {
    tauri::async_runtime::spawn_blocking(work).await.map_err(|error| format!("명령 실행 스레드 오류: {error}"))?
}

#[tauri::command]
async fn workspace_open_project(name: String, folder: String) -> Result<serde_json::Value, String> { off_main(move || workspace_open_project_blocking(name, folder)).await }
#[tauri::command]
async fn open_location(location: String) -> Result<(), String> { off_main(move || open_location_blocking(location)).await }
#[tauri::command]
async fn workspaces() -> Result<serde_json::Value, String> { off_main(move || workspaces_blocking()).await }
#[tauri::command]
async fn workspace_create(name: String) -> Result<serde_json::Value, String> { off_main(move || workspace_create_blocking(name)).await }
#[tauri::command]
async fn workspace_rename(workspace_id: String, name: String) -> Result<serde_json::Value, String> { off_main(move || workspace_rename_blocking(workspace_id, name)).await }
#[tauri::command]
async fn workspace_delete(workspace_id: String) -> Result<serde_json::Value, String> { off_main(move || workspace_delete_blocking(workspace_id)).await }
#[tauri::command]
async fn ui_projection(database: Option<String>) -> Result<serde_json::Value, String> { off_main(move || ui_projection_blocking(database)).await }
#[tauri::command]
async fn next_actions(database: Option<String>) -> Result<serde_json::Value, String> { off_main(move || next_actions_blocking(database)).await }
#[tauri::command]
async fn reviews(database: Option<String>) -> Result<serde_json::Value, String> { off_main(move || reviews_blocking(database)).await }
#[tauri::command]
async fn admission(database: Option<String>) -> Result<serde_json::Value, String> { off_main(move || admission_blocking(database)).await }
#[tauri::command]
async fn runtime_requests(database: Option<String>) -> Result<serde_json::Value, String> { off_main(move || runtime_requests_blocking(database)).await }
#[tauri::command]
async fn ao_status(database: Option<String>) -> Result<serde_json::Value, String> { off_main(move || ao_status_blocking(database)).await }
#[tauri::command]
async fn activity(database: Option<String>) -> Result<Vec<serde_json::Value>, String> { off_main(move || activity_blocking(database)).await }
#[tauri::command]
async fn goal_loop_command(command_name: String, args: Vec<String>, database: Option<String>) -> Result<serde_json::Value, String> { off_main(move || goal_loop_command_blocking(command_name, args, database)).await }

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![workspaces, workspace_create, workspace_rename, workspace_delete, workspace_open_project, pick_folder, pick_file, open_location, ui_projection, next_actions, reviews, admission, runtime_requests, ao_status, activity, goal_loop_command])
        .run(tauri::generate_context!())
        .expect("Tauri error");
}
