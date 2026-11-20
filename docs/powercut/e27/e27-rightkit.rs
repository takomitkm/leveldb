#![cfg(windows)]

use std::io::Write;
use std::path::Path;
use rightkit_fs::StateDir;

fn main() {
    let root = std::env::args().nth(1).expect("usage: e27-rightkit <dir>");
    let root = Path::new(&root);
    let state = StateDir::create(root).expect("StateDir::create");

    // Creation through rightkit's native NtCreateFile(FILE_DIRECTORY_FILE|FILE_WRITE_THROUGH)
    // path is already done by StateDir::create(root). Create one child directory as a second sample.
    let child = state.child("child").expect("StateDir child create");

    // Create a temp file through rightkit, then publish by its native FWT rename.
    let mut temp = state.create_new_empty("e27_tmp").expect("create_new_empty");
    temp.write_all(&[0x41; 2048]).expect("write temp");
    temp.flush().expect("flush temp");
    drop(temp);
    state.write("e27_final", &[0x42; 2048]).expect("write final");

    // Delete through rightkit's handle-based FWT disposition path.
    let _ = state.remove("e27_tmp").expect("remove tmp");

    // Keep the child handle alive until here so its creation stays in the measured window.
    drop(child);
}
