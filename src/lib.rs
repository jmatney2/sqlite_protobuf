use base64::Engine as _;
use once_cell::sync::Lazy;
use prost_reflect::{DescriptorPool, DynamicMessage, MapKey, ReflectMessage, Value};
use sha2::{Digest, Sha256};
use sqlite_loadable::prelude::*;
use sqlite_loadable::{api, define_scalar_function, Error, Result};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

// ---------------------------------------------------------------------------
// Descriptor pool cache
// ---------------------------------------------------------------------------

// Parsed descriptor pools are expensive to build; cache them keyed by a hash of the raw bytes.
static DESCRIPTOR_CACHE: Lazy<Mutex<HashMap<[u8; 32], Arc<DescriptorPool>>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

fn get_or_parse_descriptor(descriptor_bytes: &[u8]) -> Result<Arc<DescriptorPool>> {
    let hash: [u8; 32] = Sha256::digest(descriptor_bytes).into();
    let mut cache = DESCRIPTOR_CACHE.lock().unwrap();
    if let Some(pool) = cache.get(&hash) {
        return Ok(Arc::clone(pool));
    }
    let pool = DescriptorPool::decode(descriptor_bytes)
        .map_err(|e| Error::new_message(&format!("Failed to decode FileDescriptorSet: {e}")))?;
    let pool = Arc::new(pool);
    cache.insert(hash, Arc::clone(&pool));
    Ok(pool)
}

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------

/// Parse a path segment like `"field"` or `"field[2]"` into a field name and optional index.
fn parse_path_segment(segment: &str) -> (&str, Option<usize>) {
    if let Some(bracket_pos) = segment.find('[') {
        let field_name = &segment[..bracket_pos];
        let rest = &segment[bracket_pos + 1..];
        if let Some(close_pos) = rest.find(']') {
            if let Ok(idx) = rest[..close_pos].parse::<usize>() {
                return (field_name, Some(idx));
            }
        }
        (field_name, None)
    } else {
        (segment, None)
    }
}

/// Walk a dot-separated field path, optionally indexing into repeated fields with `field[n]`.
fn get_value_by_path(msg: &DynamicMessage, path: &str) -> Option<Value> {
    let mut current = Value::Message(msg.clone());

    for segment in path.split('.') {
        let (field_name, index) = parse_path_segment(segment);
        let inner_msg = match current {
            Value::Message(m) => m,
            _ => return None,
        };
        let field_desc = inner_msg.descriptor().get_field_by_name(field_name)?;
        let field_value = inner_msg.get_field(&field_desc).into_owned();
        current = match index {
            Some(idx) => match field_value {
                Value::List(list) => list.into_iter().nth(idx)?,
                _ => return None,
            },
            None => field_value,
        };
    }
    Some(current)
}

// ---------------------------------------------------------------------------
// Value conversion helpers
// ---------------------------------------------------------------------------

fn mapkey_to_string(key: &MapKey) -> String {
    match key {
        MapKey::Bool(b) => b.to_string(),
        MapKey::I32(n) => n.to_string(),
        MapKey::I64(n) => n.to_string(),
        MapKey::U32(n) => n.to_string(),
        MapKey::U64(n) => n.to_string(),
        MapKey::String(s) => s.to_string(),
    }
}

/// Recursively convert a prost-reflect Value to a serde_json Value.
/// Used for List and Map values; Message values go through DynamicMessage's Serialize impl.
fn value_to_json(value: &Value) -> serde_json::Value {
    match value {
        Value::Bool(b) => serde_json::Value::Bool(*b),
        Value::I32(n) => (*n).into(),
        Value::I64(n) => (*n).into(),
        Value::U32(n) => (*n).into(),
        Value::U64(n) => (*n).into(),
        Value::F32(f) => serde_json::Number::from_f64(*f as f64)
            .map(serde_json::Value::Number)
            .unwrap_or(serde_json::Value::Null),
        Value::F64(f) => serde_json::Number::from_f64(*f)
            .map(serde_json::Value::Number)
            .unwrap_or(serde_json::Value::Null),
        Value::String(s) => serde_json::Value::String(s.clone()),
        Value::Bytes(b) => serde_json::Value::String(
            base64::engine::general_purpose::STANDARD.encode(b.as_ref()),
        ),
        Value::EnumNumber(n) => (*n).into(),
        Value::Message(m) => serde_json::to_value(m).unwrap_or(serde_json::Value::Null),
        Value::List(list) => {
            serde_json::Value::Array(list.iter().map(value_to_json).collect())
        }
        Value::Map(map) => {
            let obj: serde_json::Map<String, serde_json::Value> = map
                .iter()
                .map(|(k, v)| (mapkey_to_string(k), value_to_json(v)))
                .collect();
            serde_json::Value::Object(obj)
        }
    }
}

// ---------------------------------------------------------------------------
// Business logic (pure Rust, no SQLite types — these are the unit-testable core)
// ---------------------------------------------------------------------------

/// Decode a message and extract the value at `field_path`.
/// Returns `Ok(None)` when the path resolves to nothing (field not present).
/// Returns `Err` when the message type is unknown or the bytes cannot be decoded.
fn extract_field(
    pool: &DescriptorPool,
    message_type: &str,
    data: &[u8],
    field_path: &str,
) -> Result<Option<Value>> {
    let msg_desc = pool
        .get_message_by_name(message_type)
        .ok_or_else(|| Error::new_message(&format!("Unknown message type: {message_type}")))?;
    let msg = DynamicMessage::decode(msg_desc, data)
        .map_err(|e| Error::new_message(&format!("Failed to decode message: {e}")))?;
    Ok(get_value_by_path(&msg, field_path))
}

/// Return `true` when `data` is a valid encoding of the given message type.
/// Returns `Err` when the message type is unknown; returns `Ok(false)` when the
/// bytes are not a valid protobuf encoding.
fn check_valid(pool: &DescriptorPool, message_type: &str, data: &[u8]) -> Result<bool> {
    let msg_desc = pool
        .get_message_by_name(message_type)
        .ok_or_else(|| Error::new_message(&format!("Unknown message type: {message_type}")))?;
    Ok(DynamicMessage::decode(msg_desc, data).is_ok())
}

/// Encode a message as its canonical proto3 JSON representation.
/// Returns `Err` when the message type is unknown or the bytes cannot be decoded.
fn to_json(
    pool: &DescriptorPool,
    message_type: &str,
    data: &[u8],
) -> Result<serde_json::Value> {
    let msg_desc = pool
        .get_message_by_name(message_type)
        .ok_or_else(|| Error::new_message(&format!("Unknown message type: {message_type}")))?;
    let msg = DynamicMessage::decode(msg_desc, data)
        .map_err(|e| Error::new_message(&format!("Failed to decode message: {e}")))?;
    serde_json::to_value(&msg)
        .map_err(|e| Error::new_message(&format!("JSON serialization error: {e}")))
}

// ---------------------------------------------------------------------------
// SQLite result helper
// ---------------------------------------------------------------------------

/// Map a prost-reflect Value onto an sqlite3_context result.
fn set_result_from_value(context: *mut sqlite3_context, value: &Value) -> Result<()> {
    match value {
        Value::Bool(b) => api::result_bool(context, *b),
        Value::I32(n) => api::result_int(context, *n),
        Value::I64(n) => api::result_int64(context, *n),
        Value::U32(n) => api::result_int64(context, *n as i64),
        Value::U64(n) => api::result_int64(context, *n as i64),
        Value::F32(f) => api::result_double(context, *f as f64),
        Value::F64(f) => api::result_double(context, *f),
        Value::String(s) => api::result_text(context, s.as_str())?,
        Value::Bytes(b) => api::result_blob(context, b.as_ref()),
        Value::EnumNumber(n) => api::result_int(context, *n),
        Value::Message(m) => {
            let json_val = serde_json::to_value(m)
                .map_err(|e| Error::new_message(&format!("JSON serialization error: {e}")))?;
            api::result_json(context, json_val)?;
        }
        Value::List(_) | Value::Map(_) => {
            api::result_json(context, value_to_json(value))?;
        }
    }
    Ok(())
}

fn is_null(value: &*mut sqlite3_value) -> bool {
    api::value_type(value) == api::ValueType::Null
}

// ---------------------------------------------------------------------------
// SQLite adapter functions (thin wrappers around the business logic above)
// ---------------------------------------------------------------------------

/// `protobuf_extract(data, descriptor, message_type, field_path)`
///
/// Extracts a single field from a binary protobuf blob and returns it as the
/// appropriate SQLite type.  Nested messages, repeated fields, and maps are
/// returned as JSON text (queryable with SQLite's json_extract).  Returns NULL
/// when `data` is NULL or the path does not resolve to a value.
pub fn protobuf_extract(
    context: *mut sqlite3_context,
    values: &[*mut sqlite3_value],
) -> Result<()> {
    if values.len() != 4 {
        return Err(Error::new_message(
            "protobuf_extract requires 4 arguments: data, descriptor, message_type, field_path",
        ));
    }
    if is_null(&values[0]) {
        api::result_null(context);
        return Ok(());
    }

    let data = api::value_blob(&values[0]);
    let descriptor_bytes = api::value_blob(&values[1]);
    let message_type = api::value_text(&values[2])?;
    let field_path = api::value_text(&values[3])?;

    let pool = get_or_parse_descriptor(descriptor_bytes)?;
    match extract_field(&pool, message_type, data, field_path)? {
        Some(value) => set_result_from_value(context, &value)?,
        None => api::result_null(context),
    }
    Ok(())
}

/// `protobuf_valid(data, descriptor, message_type)`
///
/// Returns 1 if `data` is a structurally valid encoding of the given message
/// type, 0 otherwise.  Returns NULL when `data` is NULL.
pub fn protobuf_valid(
    context: *mut sqlite3_context,
    values: &[*mut sqlite3_value],
) -> Result<()> {
    if values.len() != 3 {
        return Err(Error::new_message(
            "protobuf_valid requires 3 arguments: data, descriptor, message_type",
        ));
    }
    if is_null(&values[0]) {
        api::result_null(context);
        return Ok(());
    }

    let data = api::value_blob(&values[0]);
    let descriptor_bytes = api::value_blob(&values[1]);
    let message_type = api::value_text(&values[2])?;

    let pool = get_or_parse_descriptor(descriptor_bytes)?;
    let valid = check_valid(&pool, message_type, data)?;
    api::result_bool(context, valid);
    Ok(())
}

/// `protobuf_to_json(data, descriptor, message_type)`
///
/// Converts a binary protobuf blob to its canonical proto3 JSON representation.
/// The result is returned with the JSON subtype set, making it compatible with
/// SQLite's json_extract() and other JSON functions.  Returns NULL when `data`
/// is NULL.
pub fn protobuf_to_json(
    context: *mut sqlite3_context,
    values: &[*mut sqlite3_value],
) -> Result<()> {
    if values.len() != 3 {
        return Err(Error::new_message(
            "protobuf_to_json requires 3 arguments: data, descriptor, message_type",
        ));
    }
    if is_null(&values[0]) {
        api::result_null(context);
        return Ok(());
    }

    let data = api::value_blob(&values[0]);
    let descriptor_bytes = api::value_blob(&values[1]);
    let message_type = api::value_text(&values[2])?;

    let pool = get_or_parse_descriptor(descriptor_bytes)?;
    let json_val = to_json(&pool, message_type, data)?;
    api::result_json(context, json_val)?;
    Ok(())
}

#[sqlite_entrypoint]
pub fn sqlite3_extension_init(db: *mut sqlite3) -> Result<()> {
    define_scalar_function(
        db,
        "protobuf_extract",
        4,
        protobuf_extract,
        FunctionFlags::DETERMINISTIC,
    )?;
    define_scalar_function(
        db,
        "protobuf_valid",
        3,
        protobuf_valid,
        FunctionFlags::DETERMINISTIC,
    )?;
    define_scalar_function(
        db,
        "protobuf_to_json",
        3,
        protobuf_to_json,
        FunctionFlags::DETERMINISTIC,
    )?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use prost::Message as _;
    use prost_reflect::bytes::Bytes;

    // Descriptor compiled from proto/test.proto by build.rs.
    const TEST_DESCRIPTOR: &[u8] =
        include_bytes!(concat!(env!("OUT_DIR"), "/test_descriptor.bin"));

    fn test_pool() -> Arc<DescriptorPool> {
        get_or_parse_descriptor(TEST_DESCRIPTOR).expect("test descriptor should be valid")
    }

    /// Encode a DynamicMessage to bytes.
    fn encode(msg: &DynamicMessage) -> Vec<u8> {
        msg.encode_to_vec()
    }

    /// Build a minimal Person message with just a name and age set.
    fn person(name: &str, age: i32) -> Vec<u8> {
        let pool = test_pool();
        let desc = pool.get_message_by_name("test.Person").unwrap();
        let mut msg = DynamicMessage::new(desc.clone());
        msg.set_field(&desc.get_field_by_name("name").unwrap(), Value::String(name.to_string()));
        msg.set_field(&desc.get_field_by_name("age").unwrap(), Value::I32(age));
        encode(&msg)
    }

    // -----------------------------------------------------------------------
    // parse_path_segment
    // -----------------------------------------------------------------------

    #[test]
    fn test_parse_segment_simple() {
        assert_eq!(parse_path_segment("field"), ("field", None));
    }

    #[test]
    fn test_parse_segment_indexed() {
        assert_eq!(parse_path_segment("tags[0]"), ("tags", Some(0)));
        assert_eq!(parse_path_segment("items[42]"), ("items", Some(42)));
    }

    #[test]
    fn test_parse_segment_malformed_index() {
        // Malformed brackets — treated as field name with no index
        assert_eq!(parse_path_segment("tags[abc]").1, None);
        assert_eq!(parse_path_segment("tags[]").1, None);
    }

    // -----------------------------------------------------------------------
    // get_or_parse_descriptor
    // -----------------------------------------------------------------------

    #[test]
    fn test_descriptor_parses_successfully() {
        let pool = test_pool();
        assert!(pool.get_message_by_name("test.Person").is_some());
        assert!(pool.get_message_by_name("test.Address").is_some());
    }

    #[test]
    fn test_descriptor_bad_bytes_returns_error() {
        let result = get_or_parse_descriptor(b"not a valid descriptor");
        assert!(result.is_err());
    }

    #[test]
    fn test_descriptor_caches_on_second_call() {
        let pool1 = test_pool();
        let pool2 = test_pool();
        // Same Arc — pointer equality confirms the cache was hit
        assert!(Arc::ptr_eq(&pool1, &pool2));
    }

    // -----------------------------------------------------------------------
    // extract_field
    // -----------------------------------------------------------------------

    #[test]
    fn test_extract_string() {
        let pool = test_pool();
        let data = person("Alice", 30);
        let val = extract_field(&pool, "test.Person", &data, "name")
            .unwrap()
            .unwrap();
        assert_eq!(val, Value::String("Alice".to_string()));
    }

    #[test]
    fn test_extract_int32() {
        let pool = test_pool();
        let data = person("Alice", 30);
        let val = extract_field(&pool, "test.Person", &data, "age")
            .unwrap()
            .unwrap();
        assert_eq!(val, Value::I32(30));
    }

    #[test]
    fn test_extract_bool() {
        let pool = test_pool();
        let desc = pool.get_message_by_name("test.Person").unwrap();
        let mut msg = DynamicMessage::new(desc.clone());
        msg.set_field(&desc.get_field_by_name("active").unwrap(), Value::Bool(true));
        let val = extract_field(&pool, "test.Person", &encode(&msg), "active")
            .unwrap()
            .unwrap();
        assert_eq!(val, Value::Bool(true));
    }

    #[test]
    fn test_extract_double() {
        let pool = test_pool();
        let desc = pool.get_message_by_name("test.Person").unwrap();
        let mut msg = DynamicMessage::new(desc.clone());
        msg.set_field(&desc.get_field_by_name("score").unwrap(), Value::F64(9.5));
        let val = extract_field(&pool, "test.Person", &encode(&msg), "score")
            .unwrap()
            .unwrap();
        assert_eq!(val, Value::F64(9.5));
    }

    #[test]
    fn test_extract_bytes() {
        let pool = test_pool();
        let desc = pool.get_message_by_name("test.Person").unwrap();
        let mut msg = DynamicMessage::new(desc.clone());
        let raw = Bytes::from(vec![0xde, 0xad, 0xbe, 0xef]);
        msg.set_field(&desc.get_field_by_name("avatar").unwrap(), Value::Bytes(raw.clone()));
        let val = extract_field(&pool, "test.Person", &encode(&msg), "avatar")
            .unwrap()
            .unwrap();
        assert_eq!(val, Value::Bytes(raw));
    }

    #[test]
    fn test_extract_nested_field() {
        let pool = test_pool();
        let person_desc = pool.get_message_by_name("test.Person").unwrap();
        let addr_desc = pool.get_message_by_name("test.Address").unwrap();
        let mut addr = DynamicMessage::new(addr_desc.clone());
        addr.set_field(&addr_desc.get_field_by_name("city").unwrap(), Value::String("Springfield".to_string()));
        let mut msg = DynamicMessage::new(person_desc.clone());
        msg.set_field(&person_desc.get_field_by_name("address").unwrap(), Value::Message(addr));
        let val = extract_field(&pool, "test.Person", &encode(&msg), "address.city")
            .unwrap()
            .unwrap();
        assert_eq!(val, Value::String("Springfield".to_string()));
    }

    #[test]
    fn test_extract_repeated_by_index() {
        let pool = test_pool();
        let desc = pool.get_message_by_name("test.Person").unwrap();
        let mut msg = DynamicMessage::new(desc.clone());
        msg.set_field(
            &desc.get_field_by_name("tags").unwrap(),
            Value::List(vec![
                Value::String("alpha".to_string()),
                Value::String("beta".to_string()),
            ]),
        );
        let val = extract_field(&pool, "test.Person", &encode(&msg), "tags[1]")
            .unwrap()
            .unwrap();
        assert_eq!(val, Value::String("beta".to_string()));
    }

    #[test]
    fn test_extract_repeated_out_of_bounds_returns_none() {
        let pool = test_pool();
        let desc = pool.get_message_by_name("test.Person").unwrap();
        let mut msg = DynamicMessage::new(desc.clone());
        msg.set_field(
            &desc.get_field_by_name("tags").unwrap(),
            Value::List(vec![Value::String("only".to_string())]),
        );
        let result = extract_field(&pool, "test.Person", &encode(&msg), "tags[99]").unwrap();
        assert!(result.is_none());
    }

    #[test]
    fn test_extract_unknown_message_type_is_error() {
        let pool = test_pool();
        let result = extract_field(&pool, "test.DoesNotExist", b"", "name");
        assert!(result.is_err());
    }

    #[test]
    fn test_extract_invalid_bytes_is_error() {
        let pool = test_pool();
        let result = extract_field(&pool, "test.Person", b"\xff\xff\xff", "name");
        assert!(result.is_err());
    }

    #[test]
    fn test_extract_missing_path_returns_none() {
        let pool = test_pool();
        let data = person("Alice", 30);
        let result = extract_field(&pool, "test.Person", &data, "no_such_field").unwrap();
        assert!(result.is_none());
    }

    // -----------------------------------------------------------------------
    // check_valid
    // -----------------------------------------------------------------------

    #[test]
    fn test_valid_returns_true_for_valid_message() {
        let pool = test_pool();
        let data = person("Alice", 30);
        assert_eq!(check_valid(&pool, "test.Person", &data).unwrap(), true);
    }

    #[test]
    fn test_valid_returns_true_for_empty_bytes() {
        // An all-defaults proto3 message encodes to zero bytes.
        let pool = test_pool();
        assert_eq!(check_valid(&pool, "test.Person", b"").unwrap(), true);
    }

    #[test]
    fn test_valid_returns_false_for_garbage() {
        let pool = test_pool();
        assert_eq!(check_valid(&pool, "test.Person", b"\xff\xff\xff").unwrap(), false);
    }

    #[test]
    fn test_valid_unknown_type_is_error() {
        let pool = test_pool();
        let result = check_valid(&pool, "test.DoesNotExist", b"");
        assert!(result.is_err());
        // Error message should identify the bad type
        let msg = result.unwrap_err().result_error_message();
        assert!(msg.contains("test.DoesNotExist"), "got: {msg}");
    }

    // -----------------------------------------------------------------------
    // to_json
    // -----------------------------------------------------------------------

    #[test]
    fn test_to_json_scalar_fields() {
        let pool = test_pool();
        let data = person("Alice", 30);
        let json = to_json(&pool, "test.Person", &data).unwrap();
        assert_eq!(json["name"], "Alice");
        assert_eq!(json["age"], 30);
    }

    #[test]
    fn test_to_json_unknown_type_is_error() {
        let pool = test_pool();
        assert!(to_json(&pool, "test.NoSuch", b"").is_err());
    }

    // -----------------------------------------------------------------------
    // value_to_json
    // -----------------------------------------------------------------------

    #[test]
    fn test_value_to_json_primitives() {
        assert_eq!(value_to_json(&Value::Bool(true)), serde_json::Value::Bool(true));
        assert_eq!(value_to_json(&Value::I32(42)), serde_json::json!(42));
        assert_eq!(value_to_json(&Value::F64(3.14)), serde_json::json!(3.14));
        assert_eq!(
            value_to_json(&Value::String("hello".to_string())),
            serde_json::json!("hello")
        );
    }

    #[test]
    fn test_value_to_json_bytes_is_base64() {
        let b = Bytes::from(vec![0xde, 0xad, 0xbe, 0xef]);
        let json = value_to_json(&Value::Bytes(b));
        assert_eq!(json, serde_json::json!("3q2+7w=="));
    }

    #[test]
    fn test_value_to_json_list() {
        let list = Value::List(vec![Value::I32(1), Value::I32(2), Value::I32(3)]);
        assert_eq!(value_to_json(&list), serde_json::json!([1, 2, 3]));
    }
}
