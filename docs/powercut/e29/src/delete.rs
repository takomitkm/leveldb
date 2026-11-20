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
    let mut f = state.create_new_empty(&name).expect("create target");
    use std::io::Write;
    f.write_all(&[0x44; 2048]).expect("write target");
    drop(f);
    let _ = state.remove(&name).expect("native FWT delete");
}
