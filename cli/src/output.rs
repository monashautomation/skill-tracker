use std::io::Write;

use serde_json::Value;

/// Render an API result as a table (default) or pretty JSON (`--json`).
pub fn print_value<W: Write>(out: &mut W, value: &Value, as_json: bool) -> std::io::Result<()> {
    if as_json {
        writeln!(out, "{}", serde_json::to_string_pretty(value).unwrap())?;
        return Ok(());
    }
    match value {
        Value::Array(items) if !items.is_empty() && items.iter().all(Value::is_object) => {
            write_table(out, items)
        }
        Value::Object(obj) => {
            let flat = flatten_scalar(obj);
            if flat.is_empty() {
                writeln!(out, "{}", serde_json::to_string_pretty(value).unwrap())
            } else {
                let rows: Vec<Vec<String>> =
                    flat.iter().map(|(k, v)| vec![k.clone(), v.clone()]).collect();
                write_table_rows(out, &["field".to_string(), "value".to_string()], &rows)
            }
        }
        _ => writeln!(out, "{}", serde_json::to_string_pretty(value).unwrap()),
    }
}

/// Pick display columns: wanted keys first (in wanted order), then any
/// remaining scalar keys. Caps at 8 so the table stays readable.
pub fn pick_columns(items: &[Value], wanted: &[&str]) -> Vec<String> {
    let first = match items.first() {
        Some(v) => v,
        None => return Vec::new(),
    };
    let keys: Vec<String> = first
        .as_object()
        .map(|o| o.keys().cloned().collect())
        .unwrap_or_default();
    let mut cols: Vec<String> = wanted
        .iter()
        .filter(|w| keys.iter().any(|k| k == *w))
        .map(|w| w.to_string())
        .collect();
    for k in &keys {
        if cols.len() >= 8 {
            break;
        }
        if cols.iter().any(|c| c == k) {
            continue;
        }
        if is_scalar(&first[k]) {
            cols.push(k.clone());
        }
    }
    cols
}

fn write_table<W: Write>(out: &mut W, items: &[Value]) -> std::io::Result<()> {
    let cols = pick_columns(items, &[]);
    if cols.is_empty() {
        return writeln!(out, "{}", serde_json::to_string_pretty(&items).unwrap());
    }
    let rows: Vec<Vec<String>> = items
        .iter()
        .map(|it| cols.iter().map(|c| cell(it.get(c))).collect())
        .collect();
    write_table_rows(out, &cols, &rows)
}

fn write_table_rows<W: Write>(
    out: &mut W,
    cols: &[String],
    rows: &[Vec<String>],
) -> std::io::Result<()> {
    let mut widths: Vec<usize> = cols.iter().map(|c| c.len()).collect();
    for row in rows {
        for (i, cell) in row.iter().enumerate() {
            if i < widths.len() && cell.len() > widths[i] {
                widths[i] = cell.len().min(60);
            }
        }
    }
    let header: Vec<String> = cols
        .iter()
        .enumerate()
        .map(|(i, c)| format!("{:<width$}", c, width = widths[i]))
        .collect();
    writeln!(out, "{}", header.join("  "))?;
    let sep: Vec<String> = widths.iter().map(|w| "-".repeat(*w)).collect();
    writeln!(out, "{}", sep.join("  "))?;
    for row in rows {
        let cells: Vec<String> = row
            .iter()
            .enumerate()
            .map(|(i, c)| {
                let c = if c.len() > 60 { format!("{}…", &c[..59]) } else { c.clone() };
                format!("{:<width$}", c, width = widths.get(i).copied().unwrap_or(c.len()))
            })
            .collect();
        writeln!(out, "{}", cells.join("  "))?;
    }
    Ok(())
}

fn is_scalar(v: &Value) -> bool {
    matches!(v, Value::String(_) | Value::Number(_) | Value::Bool(_) | Value::Null)
}

fn flatten_scalar(obj: &serde_json::Map<String, Value>) -> Vec<(String, String)> {
    obj.iter()
        .filter(|(_, v)| is_scalar(v))
        .map(|(k, v)| (k.clone(), cell(Some(v))))
        .collect()
}

fn cell(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "-".into(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::Bool(b)) => b.to_string(),
        Some(Value::Array(items)) => {
            let parts: Vec<String> = items
                .iter()
                .map(|i| match i {
                    Value::Object(o) => o
                        .get("title")
                        .or_else(|| o.get("name"))
                        .map(|v| cell(Some(v)))
                        .unwrap_or_else(|| "{…}".into()),
                    other => cell(Some(other)),
                })
                .collect();
            if parts.is_empty() {
                "-".into()
            } else {
                parts.join(", ")
            }
        }
        Some(Value::Object(o)) => o
            .get("name")
            .or_else(|| o.get("title"))
            .map(|v| cell(Some(v)))
            .unwrap_or_else(|| "{…}".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn table_renders_arrays() {
        let mut out = Vec::new();
        let v = json!([{"discord_id": "111", "points_total": "50.00"}]);
        print_value(&mut out, &v, false).unwrap();
        let s = String::from_utf8(out).unwrap();
        assert!(s.contains("discord_id"));
        assert!(s.contains("111"));
    }

    #[test]
    fn json_passthrough() {
        let mut out = Vec::new();
        print_value(&mut out, &json!({"a": 1}), true).unwrap();
        assert!(String::from_utf8(out).unwrap().contains("\"a\": 1"));
    }
}
