use std::io::Read;

use serde_json::Value;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ApiError {
    /// 401 or missing token — exit code 2 in the CLI.
    #[error("{0}")]
    Unauthorized(String),
    /// 403 with the server's message (e.g. "admin only").
    #[error("{0}")]
    Forbidden(String),
    #[error("HTTP {status}: {message}")]
    Http { status: u16, message: String },
    #[error("network error: {0}")]
    Network(String),
    #[error("invalid response: {0}")]
    BadResponse(String),
}

/// Thin REST client for the skill-tracker API.
///
/// The API returns bare JSON objects (no `{success, data}` envelope) and
/// reports failures as `{"error": "..."}` with a 4xx status; this client
/// turns those into ApiError. Auth is a shared bearer token; write calls
/// that act for a specific member pass `X-Caller-Member: <discord_id>`.
pub struct Client {
    base: String,
    token: String,
}

impl Client {
    pub fn new(base: String, token: String) -> Self {
        Self { base, token }
    }

    pub fn get(&self, path: &str) -> Result<Value, ApiError> {
        self.request("GET", path, None, None)
    }

    pub fn get_as(&self, path: &str, caller: &str) -> Result<Value, ApiError> {
        self.request("GET", path, None, Some(caller))
    }

    pub fn post(&self, path: &str, body: Option<&Value>) -> Result<Value, ApiError> {
        self.request("POST", path, body, None)
    }

    pub fn post_as(&self, path: &str, body: Option<&Value>, caller: &str) -> Result<Value, ApiError> {
        self.request("POST", path, body, Some(caller))
    }

    pub fn patch_as(&self, path: &str, body: Option<&Value>, caller: &str) -> Result<Value, ApiError> {
        self.request("PATCH", path, body, Some(caller))
    }

    pub fn put_as(&self, path: &str, body: Option<&Value>, caller: &str) -> Result<Value, ApiError> {
        self.request("PUT", path, body, Some(caller))
    }

    fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
        caller: Option<&str>,
    ) -> Result<Value, ApiError> {
        if self.token.is_empty() {
            return Err(ApiError::Unauthorized(
                "no token — run `mastcli auth login -u <url> -t <token>`".into(),
            ));
        }
        let url = format!("{}{}", self.base, path);
        let mut req = ureq::request(method, &url)
            .set("authorization", &format!("Bearer {}", self.token));
        if let Some(c) = caller {
            req = req.set("x-caller-member", c);
        }
        if body.is_some() {
            req = req.set("content-type", "application/json");
        }
        let result = match body {
            Some(b) => req.send_json(b.clone()),
            None => req.call(),
        };

        match result {
            Ok(resp) => {
                let mut text = String::new();
                resp.into_reader()
                    .read_to_string(&mut text)
                    .map_err(|e| ApiError::BadResponse(e.to_string()))?;
                parse_body(&text)
            }
            Err(ureq::Error::Status(status, resp)) => {
                let mut text = String::new();
                let _ = resp.into_reader().read_to_string(&mut text);
                let message = serde_json::from_str::<Value>(&text)
                    .ok()
                    .and_then(|v| {
                        v.get("error").map(|e| match e {
                            Value::String(s) => s.clone(),
                            other => other.to_string(),
                        })
                    })
                    .unwrap_or_else(|| {
                        if text.trim().is_empty() {
                            status_canonical(status).to_string()
                        } else {
                            text.chars().take(200).collect()
                        }
                    });
                match status {
                    401 => Err(ApiError::Unauthorized(message)),
                    403 => Err(ApiError::Forbidden(message)),
                    _ => Err(ApiError::Http { status, message }),
                }
            }
            Err(e) => Err(ApiError::Network(e.to_string())),
        }
    }
}

fn status_canonical(s: u16) -> &'static str {
    match s {
        400 => "Bad request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not found",
        405 => "Method not allowed",
        409 => "Conflict",
        429 => "Too many requests",
        _ => "Request failed",
    }
}

fn parse_body(text: &str) -> Result<Value, ApiError> {
    if text.trim().is_empty() {
        return Ok(Value::Null);
    }
    serde_json::from_str(text).map_err(|e| {
        ApiError::BadResponse(format!("{e}: {}", text.chars().take(200).collect::<String>()))
    })
}

/// Merge present (key, Option<Value>) pairs into a JSON object.
pub fn body(pairs: &[(&str, Option<Value>)]) -> Value {
    let mut map = serde_json::Map::new();
    for (k, v) in pairs {
        if let Some(v) = v {
            map.insert((*k).to_string(), v.clone());
        }
    }
    Value::Object(map)
}

/// Build a query string from present (key, Option<value>) pairs.
pub fn query(pairs: &[(&str, Option<String>)]) -> String {
    let parts: Vec<String> = pairs
        .iter()
        .filter_map(|(k, v)| v.as_ref().map(|v| format!("{}={}", k, urlencoding::encode(v))))
        .collect();
    if parts.is_empty() {
        String::new()
    } else {
        format!("?{}", parts.join("&"))
    }
}

/// Parse a comma-separated list of integers (quiz answers).
pub fn ids(csv: &str) -> Result<Vec<u32>, ApiError> {
    csv.split(',')
        .map(|s| {
            s.trim()
                .parse::<u32>()
                .map_err(|_| ApiError::BadResponse(format!("not an integer: {s:?}")))
        })
        .collect()
}
