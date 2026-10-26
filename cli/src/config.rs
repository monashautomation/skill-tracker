use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

/// Persisted CLI configuration (url + service token).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Config {
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub token: String,
}

/// Normalise a user-supplied host/URL into a base URL.
/// Bare hosts get `https://`; trailing slashes are stripped.
/// Returns None for empty or non-http(s) inputs.
pub fn normalize_url(raw: &str) -> Option<String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return None;
    }
    // Any explicit scheme that isn't http(s) is rejected outright —
    // otherwise `ftp://x` would silently become `https://ftp://x`.
    if let Some(idx) = raw.find("://") {
        let scheme = &raw[..idx];
        if scheme != "http" && scheme != "https" {
            return None;
        }
    }
    let with_scheme = if raw.starts_with("http://") || raw.starts_with("https://") {
        raw.to_string()
    } else {
        format!("https://{raw}")
    };
    let url = urlencoding::decode(&with_scheme).ok()?;
    if !url.starts_with("http://") && !url.starts_with("https://") {
        return None;
    }
    // no second scheme hiding in the host part
    let rest = url.split_once("://").map(|(_, r)| r).unwrap_or("");
    if rest.contains("://") {
        return None;
    }
    Some(url.trim_end_matches('/').to_string())
}

/// Config file location: `$MASTCLI_CONFIG`, or `$MASTCLI_CONFIG_DIR/config.json`,
/// or `$XDG_CONFIG_HOME/mastcli/config.json`, or `~/.config/mastcli/config.json`.
pub fn config_path(flag: Option<&str>) -> Option<PathBuf> {
    if let Some(p) = flag {
        return Some(PathBuf::from(p));
    }
    if let Ok(p) = std::env::var("MASTCLI_CONFIG") {
        if !p.is_empty() {
            return Some(PathBuf::from(p));
        }
    }
    let dir = match std::env::var("MASTCLI_CONFIG_DIR") {
        Ok(d) if !d.is_empty() => PathBuf::from(d),
        _ => {
            let xdg = match std::env::var("XDG_CONFIG_HOME") {
                Ok(x) if !x.is_empty() => PathBuf::from(x),
                _ => match std::env::var("HOME") {
                    Ok(h) if !h.is_empty() => PathBuf::from(h).join(".config"),
                    _ => return None,
                },
            };
            xdg.join("mastcli")
        }
    };
    Some(dir.join("config.json"))
}

pub fn load_config(path: &PathBuf) -> Config {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

/// Write the config file with owner-only permissions (best effort on unix).
pub fn save_config(path: &PathBuf, cfg: &Config) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let json = serde_json::to_string_pretty(cfg).unwrap_or_default();
    fs::write(path, json)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

/// Resolve the effective (url, token) with precedence:
/// flags > MASTCLI_URL / MASTCLI_TOKEN env > config file.
pub fn resolve(
    flag_url: Option<&str>,
    flag_token: Option<&str>,
    file: &Config,
) -> Result<(String, String), String> {
    let raw_url = flag_url
        .filter(|s| !s.trim().is_empty())
        .map(|s| s.to_string())
        .or_else(|| std::env::var("MASTCLI_URL").ok().filter(|s| !s.trim().is_empty()))
        .or_else(|| {
            if file.url.trim().is_empty() {
                None
            } else {
                Some(file.url.clone())
            }
        });
    let token = flag_token
        .filter(|s| !s.trim().is_empty())
        .map(|s| s.to_string())
        .or_else(|| std::env::var("MASTCLI_TOKEN").ok().filter(|s| !s.trim().is_empty()))
        .or_else(|| {
            if file.token.trim().is_empty() {
                None
            } else {
                Some(file.token.clone())
            }
        })
        .unwrap_or_default();

    let url = raw_url
        .as_deref()
        .and_then(normalize_url)
        .ok_or_else(|| {
            "no server URL configured — run `mastcli auth login -u <url> -t <token>` or set MASTCLI_URL"
                .to_string()
        })?;
    Ok((url, token.trim().to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bare_host_gets_https() {
        assert_eq!(
            normalize_url("skills.monashautomation.com").as_deref(),
            Some("https://skills.monashautomation.com")
        );
    }

    #[test]
    fn trailing_slash_stripped() {
        assert_eq!(
            normalize_url("http://127.0.0.1:8731/").as_deref(),
            Some("http://127.0.0.1:8731")
        );
    }

    #[test]
    fn bad_scheme_rejected() {
        assert_eq!(normalize_url("ftp://x"), None);
        assert_eq!(normalize_url("https://a://b"), None);
        assert_eq!(normalize_url("   "), None);
    }

    #[test]
    fn flag_beats_env_and_file() {
        let cfg = Config {
            url: "http://file".into(),
            token: "filetok".into(),
        };
        let (url, tok) = resolve(Some("http://flag"), Some("flagtok"), &cfg).unwrap();
        assert_eq!(url, "http://flag");
        assert_eq!(tok, "flagtok");
    }

    #[test]
    fn file_used_when_no_flag_or_env() {
        // env vars are not set in the test process for these names
        let cfg = Config {
            url: "http://file".into(),
            token: "filetok".into(),
        };
        let (url, tok) = resolve(None, None, &cfg).unwrap();
        assert_eq!(url, "http://file");
        assert_eq!(tok, "filetok");
    }
}
