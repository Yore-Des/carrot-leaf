//! Run a child process line by line, killing it as soon as the job is cancelled.

use std::io::{BufRead, BufReader, Read};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::time::Duration;

pub type Cancel = Arc<AtomicBool>;

pub enum RunError {
    Cancelled,
    Spawn(String),
}

pub struct Finished {
    pub status: ExitStatus,
    pub stderr: String,
}

/// Run `cmd`, handing each stdout line to `on_line`. stderr is collected on its own thread
/// so a full pipe never stalls the child.
pub fn run_lines(mut cmd: Command, cancel: &Cancel, mut on_line: impl FnMut(&str)) -> Result<Finished, RunError> {
    cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| RunError::Spawn(e.to_string()))?;
    let stdout = child.stdout.take().unwrap();
    let mut stderr = child.stderr.take().unwrap();
    let err_thread = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = stderr.read_to_string(&mut s);
        s
    });
    let (tx, rx) = mpsc::channel::<String>();
    std::thread::spawn(move || {
        // yt-dlp ends progress lines with \r or \n; split on both.
        let mut reader = BufReader::new(stdout);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            let mut byte = [0u8; 1];
            let mut got = false;
            loop {
                match reader.read(&mut byte) {
                    Ok(0) => break,
                    Ok(_) => {
                        got = true;
                        if byte[0] == b'\n' || byte[0] == b'\r' {
                            break;
                        }
                        buf.push(byte[0]);
                    }
                    Err(_) => break,
                }
            }
            if !got {
                return;
            }
            if !buf.is_empty() && tx.send(String::from_utf8_lossy(&buf).into_owned()).is_err() {
                return;
            }
            let _ = reader.fill_buf();
        }
    });
    loop {
        if cancel.load(Ordering::Relaxed) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(RunError::Cancelled);
        }
        match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(line) => on_line(&line),
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    let status = loop {
        if cancel.load(Ordering::Relaxed) {
            let _ = child.kill();
            let _ = child.wait();
            return Err(RunError::Cancelled);
        }
        match child.try_wait() {
            Ok(Some(s)) => break s,
            Ok(None) => std::thread::sleep(Duration::from_millis(20)),
            Err(e) => return Err(RunError::Spawn(e.to_string())),
        }
    };
    let stderr = err_thread.join().unwrap_or_default();
    Ok(Finished { status, stderr })
}
