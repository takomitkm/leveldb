#![cfg(windows)]
use std::path::Path;
use rightkit_fs::StateDir;

fn main() {
    let target = std::env::args().nth(1).expect("usage: <exe> <dir>");
    let target = Path::new(&target);
    let _ = StateDir::create(target).expect("create dir");
}
