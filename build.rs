use std::path::PathBuf;
use std::process::Command;

fn main() {
    let out_dir = PathBuf::from(std::env::var("OUT_DIR").unwrap());

    let status = Command::new("protoc")
        .args([
            "-Iproto",
            &format!(
                "--descriptor_set_out={}",
                out_dir.join("test_descriptor.bin").display()
            ),
            "--include_imports",
            "proto/test.proto",
        ])
        .status()
        .expect("failed to run protoc — install the Protocol Buffers compiler");

    assert!(status.success(), "protoc exited with a non-zero status");

    println!("cargo:rerun-if-changed=proto/test.proto");
    println!("cargo:rerun-if-changed=build.rs");
}
