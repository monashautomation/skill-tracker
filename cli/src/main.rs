mod api;
mod config;
mod output;

use std::process::ExitCode;

use clap::{Parser, Subcommand};
use serde_json::{json, Value};

use api::{body, ids, Client};
use config::{config_path, load_config, normalize_url, resolve, save_config, Config};
use output::print_value;

/// mastcli — Monash Automation skill-tracker CLI.
///
/// Read AND write. Auth is the shared service token; calls that act on
/// behalf of a member pass --member/--caller (X-Caller-Member header).
#[derive(Parser)]
#[command(name = "mastcli", version, about, long_about = None)]
struct Cli {
    /// Server URL (e.g. skills.monashautomation.com)
    #[arg(short = 'u', long = "url", global = true)]
    url: Option<String>,
    /// Service bearer token
    #[arg(short = 't', long = "token", global = true)]
    token: Option<String>,
    /// Config file path (default: ~/.config/mastcli/config.json)
    #[arg(long = "config", global = true)]
    config: Option<String>,
    /// Emit raw JSON instead of tables
    #[arg(long = "json", global = true)]
    json: bool,

    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Save server URL + token to the config file
    Auth {
        #[command(subcommand)]
        op: AuthOp,
    },
    /// Check API health
    Health,
    /// Members
    Members {
        #[command(subcommand)]
        op: MembersOp,
    },
    /// Points ledger
    Points {
        #[command(subcommand)]
        op: PointsOp,
    },
    /// Leaderboard (points ranking)
    Leaderboard,
    /// Skill ratings (admin writes)
    Skills {
        #[command(subcommand)]
        op: SkillsOp,
    },
    /// Training catalog (equipment groups + lessons)
    Catalog,
    /// Lessons
    Lessons {
        #[command(subcommand)]
        op: LessonsOp,
    },
    /// Induction workflow
    Inductions {
        #[command(subcommand)]
        op: InductionsOp,
    },
    /// Action items (meeting follow-ups)
    ActionItems {
        #[command(subcommand)]
        op: ActionItemsOp,
    },
    /// Points -> Discord role rules (admin writes)
    RoleRules {
        #[command(subcommand)]
        op: RoleRulesOp,
    },
}

#[derive(Subcommand)]
enum AuthOp {
    /// Store url + token
    Login {
        #[arg(short = 'u', long)]
        url: String,
        #[arg(short = 't', long)]
        token: String,
    },
    /// Show the effective config (token masked)
    Status,
}

#[derive(Subcommand)]
enum MembersOp {
    /// Get a member profile (skills, lessons, inductions, points)
    Get { id: String },
    /// Upsert a member by discord id
    Upsert {
        id: String,
        #[arg(long)]
        username: Option<String>,
        #[arg(long)]
        display: Option<String>,
        #[arg(long)]
        email: Option<String>,
    },
}

#[derive(Subcommand)]
enum PointsOp {
    /// Award points to a member
    Add {
        id: String,
        points: f64,
        #[arg(long, default_value = "manual")]
        source: String,
        #[arg(long, default_value = "")]
        description: String,
        /// dedup key, e.g. bounty:42 — repeat awards are ignored
        #[arg(long)]
        external_ref: Option<String>,
    },
}

#[derive(Subcommand)]
enum SkillsOp {
    /// Set skill ratings (0-10, admin only)
    Set {
        /// member discord id
        id: String,
        /// admin discord id acting (defaults to the member)
        #[arg(long)]
        caller: Option<String>,
        #[arg(long)]
        leadership: Option<u8>,
        #[arg(long)]
        engineering: Option<u8>,
        #[arg(long)]
        software: Option<u8>,
        #[arg(long)]
        electronics: Option<u8>,
        #[arg(long)]
        project_mgmt: Option<u8>,
        #[arg(long)]
        communication: Option<u8>,
    },
}

#[derive(Subcommand)]
enum LessonsOp {
    /// Lesson detail (body markdown, quiz meta)
    Get { id: u32 },
    /// Complete a lesson (server enforces induction + quiz gates)
    Complete {
        id: u32,
        /// member discord id acting as
        #[arg(long)]
        member: String,
    },
    /// Take a lesson quiz
    Quiz {
        id: u32,
        #[arg(long)]
        member: String,
        /// answer indexes, comma separated (e.g. 0,1,2)
        #[arg(long)]
        answers: String,
    },
}

#[derive(Subcommand)]
enum InductionsOp {
    /// List inductions (admins see all, members see their own)
    List {
        #[arg(long)]
        caller: String,
    },
    /// Request an induction
    Request {
        #[arg(long)]
        member: String,
        #[arg(long)]
        induction_type: u32,
    },
    /// Approve or reject an induction (admin)
    Review {
        id: u32,
        /// approved | rejected
        decision: String,
        #[arg(long)]
        caller: String,
    },
}

#[derive(Subcommand)]
enum ActionItemsOp {
    /// List action items
    List {
        /// member discord id acting (admins see all)
        #[arg(long)]
        caller: Option<String>,
        /// open | done
        #[arg(long)]
        status: Option<String>,
    },
    /// Create an action item
    Create {
        title: String,
        /// admin discord id acting
        #[arg(long)]
        caller: String,
        #[arg(long)]
        member: Option<String>,
        #[arg(long)]
        points: Option<f64>,
        /// due date YYYY-MM-DD
        #[arg(long)]
        due: Option<String>,
    },
    /// Mark done (awards its points once)
    Done {
        id: u32,
        #[arg(long)]
        caller: String,
    },
}

#[derive(Subcommand)]
enum RoleRulesOp {
    /// List role rules
    List,
    /// Upsert a points->Discord role rule (admin)
    Set {
        #[arg(long)]
        role_id: String,
        #[arg(long)]
        role_name: String,
        #[arg(long)]
        threshold: f64,
    },
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let cfg_file = config_path(cli.config.as_deref());
    let file = cfg_file.as_ref().map(load_config).unwrap_or_default();

    // auth login needs no client
    if let Command::Auth { op: AuthOp::Login { url, token } } = &cli.command {
        let Some(path) = cfg_file else {
            eprintln!("error: cannot determine config path");
            return ExitCode::FAILURE;
        };
        let Some(url) = normalize_url(url) else {
            eprintln!("error: invalid URL: {url}");
            return ExitCode::FAILURE;
        };
        let cfg = Config { url: url.clone(), token: token.clone() };
        if let Err(e) = save_config(&path, &cfg) {
            eprintln!("error: {e}");
            return ExitCode::FAILURE;
        }
        println!("saved {path:?} for {url}");
        return ExitCode::SUCCESS;
    }

    let (url, token) = match resolve(cli.url.as_deref(), cli.token.as_deref(), &file) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::FAILURE;
        }
    };
    let client = Client::new(url.clone(), token.clone());

    let result = run(&cli, &client, &url, &token);
    match result {
        Ok(v) => {
            let mut out = std::io::stdout();
            if let Err(e) = print_value(&mut out, &v, cli.json) {
                eprintln!("error: {e}");
                return ExitCode::FAILURE;
            }
            ExitCode::SUCCESS
        }
        Err(api::ApiError::Unauthorized(m)) => {
            eprintln!("error: {m}");
            ExitCode::from(2)
        }
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}

fn run(cli: &Cli, c: &Client, url: &str, token: &str) -> Result<Value, api::ApiError> {
    match &cli.command {
        Command::Auth { op: AuthOp::Status } => {
            let masked = if token.is_empty() { "(none)".to_string() }
                else { format!("{}…", &token[..token.len().min(4)]) };
            Ok(json!({ "url": url, "token": masked }))
        }
        // handled in main() before the client is built
        Command::Auth { op: AuthOp::Login { .. } } => Ok(Value::Null),
        Command::Health => c.get("/api/health/"),
        Command::Leaderboard => c.get("/api/leaderboard/"),
        Command::Catalog => c.get("/api/catalog/"),
        Command::Members { op } => match op {
            MembersOp::Get { id } => c.get(&format!("/api/members/{id}/")),
            MembersOp::Upsert { id, username, display, email } => c.post(
                "/api/members/",
                Some(&body(&[
                    ("discord_id", Some(json!(id))),
                    ("discord_username", username.as_ref().map(|v| json!(v))),
                    ("display_name", display.as_ref().map(|v| json!(v))),
                    ("email", email.as_ref().map(|v| json!(v))),
                ])),
            ),
        },
        Command::Points { op } => match op {
            PointsOp::Add { id, points, source, description, external_ref } => c.post(
                &format!("/api/members/{id}/points/"),
                Some(&body(&[
                    ("points", Some(json!(points))),
                    ("source", Some(json!(source))),
                    ("description", Some(json!(description))),
                    ("external_ref", external_ref.as_ref().map(|v| json!(v))),
                ])),
            ),
        },
        Command::Skills { op } => match op {
            SkillsOp::Set { id, caller, leadership, engineering, software, electronics, project_mgmt, communication } => {
                let b = body(&[
                    ("leadership", leadership.map(|v| json!(v))),
                    ("engineering", engineering.map(|v| json!(v))),
                    ("software", software.map(|v| json!(v))),
                    ("electronics", electronics.map(|v| json!(v))),
                    ("project_mgmt", project_mgmt.map(|v| json!(v))),
                    ("communication", communication.map(|v| json!(v))),
                ]);
                c.put_as(&format!("/api/members/{id}/skills/"), Some(&b),
                    caller.as_deref().unwrap_or(id))
            }
        },
        Command::Lessons { op } => match op {
            LessonsOp::Get { id } => c.get(&format!("/api/lessons/{id}/")),
            LessonsOp::Complete { id, member } => {
                c.post_as(&format!("/api/lessons/{id}/complete/"), Some(&json!({})), member)
            }
            LessonsOp::Quiz { id, member, answers } => {
                let ans = ids(answers)?;
                c.post_as(&format!("/api/lessons/{id}/quiz/attempt/"),
                    Some(&json!({ "answers": ans })), member)
            }
        },
        Command::Inductions { op } => match op {
            InductionsOp::List { caller } => c.get_as("/api/inductions/list/", caller),
            InductionsOp::Request { member, induction_type } => c.post_as(
                "/api/inductions/", Some(&json!({ "induction_type_id": induction_type })), member),
            InductionsOp::Review { id, decision, caller } => c.patch_as(
                &format!("/api/inductions/{id}/review/"),
                Some(&json!({ "decision": decision })), caller),
        },
        Command::ActionItems { op } => match op {
            ActionItemsOp::List { caller, status } => {
                let path = format!("/api/action-items/{}", api::query(&[("status", status.clone())]));
                match caller {
                    Some(caller_id) => c.get_as(&path, caller_id),
                    None => c.get(&path),
                }
            }
            ActionItemsOp::Create { title, caller, member, points, due } => c.post_as(
                "/api/action-items/",
                Some(&body(&[
                    ("title", Some(json!(title))),
                    ("member_discord_id", member.as_ref().map(|v| json!(v))),
                    ("points", points.map(|v| json!(v))),
                    ("due_date", due.as_ref().map(|v| json!(v))),
                ])),
                caller,
            ),
            ActionItemsOp::Done { id, caller } => c.patch_as(
                &format!("/api/action-items/{id}/"), Some(&json!({ "done": true })), caller),
        },
        Command::RoleRules { op } => match op {
            RoleRulesOp::List => c.get("/api/role-rules/"),
            RoleRulesOp::Set { role_id, role_name, threshold } => c.post(
                "/api/role-rules/",
                Some(&json!({
                    "discord_role_id": role_id,
                    "discord_role_name": role_name,
                    "threshold_points": threshold,
                })),
            ),
        },
    }
}
