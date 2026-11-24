//! Native KTREMIX trajectory compilation and training shard I/O.

mod compiler;
pub mod corpus;
pub mod dataset;
mod diagnostics;
mod model;
mod runtime;
pub mod shard;
mod slots;

pub use compiler::{
    Compilation, Compiler, Metrics, Options, Report, TokenizerProfile, CONTRACT, VERSION,
};
pub use diagnostics::{Diagnostic, Severity};
pub use model::{Emission, Frame, Token};
pub use runtime::Columns;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("{0}")]
    Invalid(String),
    #[error("Compilation cancelled")]
    Cancelled,
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

pub type Result<T> = std::result::Result<T, Error>;

fn invalid(message: impl Into<String>) -> Error {
    Error::Invalid(message.into())
}

fn fingerprint(value: &serde_json::Value) -> String {
    use sha2::{Digest, Sha256};
    format!("{:x}", Sha256::digest(value.to_string()))
}
