#![cfg(windows)]
use std::path::Path;
use rightkit_fs::rename_exclusive;

fn main() {
    let target = std::env::args().nth(1).expect("usage: rightkit-rename <target>");
    let target = Path::new(&target);
    let parent = target.parent().unwrap_or_else(|| Path::new("."));
    std::fs::create_dir_all(parent).expect("create parent");
    let src = parent.join("src");
    std::fs::write(&src, &[0x52; 2048]).expect("create/write source");
    rename_exclusive(&src, target).expect("rightkit exclusive rename");
}
