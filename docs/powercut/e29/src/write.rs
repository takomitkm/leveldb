#![cfg(windows)]
use std::path::Path;
use rightkit_fs::StateDir;

fn main() {
    let target = std::env::args().nth(1).expect("usage: <exe> <dir>");
    let target = Path::new(&target);
    let parent = target.parent().unwrap_or_else(|| Path::new("."));
    std::fs::create_dir_all(parent).expect("create parent");
    let state = StateDir::create(parent).expect("open/create state dir");
    let name = target.file_name().unwrap().to_string_lossy();
    state.write(&name, &[0x57; 2048]).expect("rightkit durable write");
}
