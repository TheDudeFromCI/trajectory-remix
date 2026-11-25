//! Native KTREMIX trajectory compilation and training shard I/O.
//!
//! This crate turns Trajectory Remix *graph* trajectories (and raw corpus text)
//! into **KTREMIX 1.0** shards: columnar, optionally compressed training data in
//! which many windows of text advance together, frame by frame.
//!
//! # Where to start
//!
//! | Task | Entry point |
//! | --- | --- |
//! | Validate a graph without a tokenizer | [`Compiler::structural_report`] |
//! | Compile a graph into row columns | [`Compiler::new`], then [`Compiler::compile`] |
//! | Write many compilations into one shard | [`shard::CompileWriter`] |
//! | Convert raw text or JSONL documents | [`Compiler::convert_corpus`] (see [`corpus`]) |
//! | Read samples back | [`shard::Shard`] |
//! | Draw weighted batches across shards | [`dataset::Dataset`] |
//!
//! # Example
//!
//! ```no_run
//! use ktremix::{Compiler, Options, TokenizerProfile};
//!
//! # fn main() -> ktremix::Result<()> {
//! let profile: TokenizerProfile =
//!     serde_json::from_str(&std::fs::read_to_string("profile.json")?)?;
//! let compiler = Compiler::new(profile, None, Options::default())?;
//! let source = std::fs::read_to_string("trajectory.json")?;
//! let report = compiler.report(&source);
//! if !report.has_errors() {
//!     let compilation = compiler.compile(&source)?;
//!     println!("{} rows", compilation.columns.token_ids.len());
//! }
//! # Ok(())
//! # }
//! ```
//!
//! The byte layout of a shard is specified in `docs/KTREMIX.md` in the
//! repository.

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
    Compilation, Compiler, Metrics, Options, Report, TokenizerProfile, CONTRACT, DEFAULT_MAX_ROWS,
    VERSION,
};
pub use diagnostics::{Diagnostic, Severity};
pub use model::{Emission, Frame, Token};
pub use runtime::Columns;

/// Errors returned by this crate.
///
/// Most failures are [`Error::Invalid`], which carries a human-readable message
/// (bad options, malformed graphs, corrupt shards). Compilation can also be
/// stopped by a caller-supplied cancellation check.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// The input, options or shard contents were rejected. The message says why.
    #[error("{0}")]
    Invalid(String),
    /// A tokenizer profile or the tokenizer itself failed.
    #[error("{0}")]
    Tokenizer(String),
    /// Trajectory content could not be represented in the compiled format.
    #[error("{0}")]
    Encoding(String),
    /// The scheduler ran out of active window capacity.
    #[error("Active window capacity exceeded (limit {limit}, excluding Available)")]
    WindowLimitExceeded { limit: usize },
    /// The trajectory exceeded the configured compiled row budget.
    #[error("Compilation row bound exceeded (limit {limit})")]
    RowLimitExceeded { limit: usize },
    /// A cancellation callback asked compilation to stop before it finished.
    #[error("Compilation cancelled")]
    Cancelled,
    /// A graph source or shard manifest was not valid JSON.
    #[error(transparent)]
    Json(#[from] serde_json::Error),
    /// A file could not be read or written.
    #[error(transparent)]
    Io(#[from] std::io::Error),
}

/// Result alias used throughout the crate.
pub type Result<T> = std::result::Result<T, Error>;

fn invalid(message: impl Into<String>) -> Error {
    Error::Invalid(message.into())
}

fn fingerprint(value: &serde_json::Value) -> String {
    use sha2::{Digest, Sha256};
    format!("{:x}", Sha256::digest(value.to_string()))
}
