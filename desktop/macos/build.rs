fn main() {
    let manifest = std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let root = manifest.join("../..").canonicalize().unwrap();
    println!("cargo:rustc-env=QQPET_BUILD_SOURCE_ROOT={}", root.display());
    println!("cargo:rerun-if-env-changed=QQPET_SOURCE_ROOT");
    tauri_build::build()
}
