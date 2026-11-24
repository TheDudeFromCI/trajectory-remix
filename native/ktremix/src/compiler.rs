use crate::diagnostics::{analyze, Diagnostic, Graph, Settings, Severity};
use crate::model::{Content, Event, Kind, Mode, Piece, Source, Window};
use crate::runtime::{Columns, Runtime, FOREVER};
use crate::{fingerprint, invalid, Emission, Error, Frame, Result, Token};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashSet, VecDeque};
use std::path::Path;
use std::sync::OnceLock;
use tokenizers::Tokenizer;
use uuid::Uuid;

/// Identifier of this compiler's lowering rules, recorded in every shard manifest.
pub const VERSION: &str = "kidra.graph-compiler/1";
/// Execution contract that readers of the compiled arrays are expected to follow.
pub const CONTRACT: &str = "kidra.progressive-sleep/1";
const CONTROL_NAMES: [&str; 12] = [
    "bos",
    "eos",
    "noop",
    "ctx_begin",
    "ctx_end",
    "tool_begin",
    "tool_end",
    "response_begin",
    "response_end",
    "msg_begin",
    "msg_end",
    "window_ref",
];

/// A tokenizer plus the IDs of the control tokens the compiler inserts.
///
/// Profiles are exported by Trajectory Remix. `special_tokens` must name every
/// control the compiler uses: `bos`, `eos`, `noop`, `ctx_begin`, `ctx_end`,
/// `tool_begin`, `tool_end`, `response_begin`, `response_end`, `msg_begin`,
/// `msg_end` and `window_ref`.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TokenizerProfile {
    /// Human-readable profile name. Must not be empty.
    pub name: String,
    /// Hugging Face `tokenizer.json` content.
    pub definition: Value,
    /// Control name to token ID.
    pub special_tokens: BTreeMap<String, u32>,
}

/// Compilation settings. [`Options::default`] is suitable for most graphs.
#[derive(Clone, Debug, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Options {
    /// Most windows that may be active at once, not counting the spare "available" window.
    pub active_limit: u32,
    /// Integer seed for physical window slot assignment. The same source and seed always compile to the same arrays.
    pub seed: serde_json::Number,
    /// Upper bound on rows per compilation. Larger graphs fail instead of exhausting memory.
    pub max_rows: usize,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            active_limit: 32,
            seed: 0.into(),
            max_rows: 1_000_000,
        }
    }
}

/// Size figures for a successfully scheduled graph.
#[derive(Debug, Serialize)]
pub struct Metrics {
    /// Total rows, including control tokens.
    pub total_tokens: usize,
    /// Rows with positive loss weight.
    pub target_tokens: usize,
    /// Peak number of simultaneously active windows, excluding the available window.
    pub peak_active_windows: usize,
    /// Peak number of rows visible at any single prediction frontier.
    pub peak_visible_tokens: usize,
}

/// Outcome of a dry run: diagnostics plus, when scheduling succeeded, [`Metrics`].
#[derive(Debug, Default, Serialize)]
pub struct Report {
    /// Errors and warnings. Any error prevents compilation.
    pub diagnostics: Vec<Diagnostic>,
    /// Present only when the graph scheduled without errors and a tokenizer was used.
    pub metrics: Option<Metrics>,
}

impl Report {
    /// Returns `true` if any diagnostic is an error (warnings do not count).
    pub fn has_errors(&self) -> bool {
        self.diagnostics
            .iter()
            .any(|d| d.severity == Severity::Error)
    }
}

/// The result of compiling one graph.
#[derive(Debug, Serialize)]
pub struct Compilation {
    /// The source graph's UUID. It becomes the sample ID in a shard.
    pub id: Uuid,
    /// SHA-256 of the graph's canonical, compile-relevant content.
    pub source_hash: String,
    /// Source window name for each physical window instance, in the same order as the `window_*` columns. `None` for unnamed instances such as the spare available window.
    pub logical_windows: Vec<Option<String>>,
    /// The compiled row, frame and window arrays.
    pub columns: Columns,
    /// Diagnostics and metrics produced while compiling.
    pub report: Report,
    /// The graph's free-form `metadata` object, carried into shard sample metadata.
    #[serde(skip)]
    pub source_metadata: Option<Value>,
}

/// Compiles graphs for one tokenizer profile and set of [`Options`].
///
/// A `Compiler` is immutable after construction and can be shared between
/// threads. Build it once and reuse it for many graphs.
pub struct Compiler {
    encoder: Tokenizer,
    pub(crate) literal: Tokenizer,
    pub(crate) controls: BTreeMap<String, u32>,
    control_ids: HashSet<u32>,
    alphabet: OnceLock<[u32; 16]>,
    settings: Option<Settings>,
    pub(crate) options: Options,
    /// SHA-256 of the tokenizer profile, recorded in manifests.
    pub profile_sha256: String,
    /// SHA-256 of the tokenizer definition, recorded in manifests.
    pub tokenizer_sha256: String,
}

impl Compiler {
    /// Builds a compiler from a tokenizer profile.
    ///
    /// `settings` is an optional JSON snapshot of project settings, used to check
    /// tool contracts and reasoning levels. Fails if the options are out of range
    /// (`active_limit`, `max_rows`, `seed`), the profile name is empty, or any control
    /// token is missing from `special_tokens`.
    pub fn new(
        profile: TokenizerProfile,
        settings: Option<&str>,
        options: Options,
    ) -> Result<Self> {
        if options.active_limit == 0 || options.active_limit == u32::MAX {
            return Err(invalid(
                "active_limit must be positive and less than u32::MAX",
            ));
        }
        if options.max_rows == 0 || options.max_rows >= u32::MAX as usize {
            return Err(invalid("max_rows must be positive and less than u32::MAX"));
        }
        if !options
            .seed
            .to_string()
            .trim_start_matches('-')
            .bytes()
            .all(|b| b.is_ascii_digit())
        {
            return Err(invalid("seed must be an integer"));
        }
        if profile.name.is_empty() {
            return Err(invalid("Tokenizer profile name must be nonempty"));
        }
        if CONTROL_NAMES
            .iter()
            .any(|key| !profile.special_tokens.contains_key(*key))
        {
            return Err(invalid("Missing required controls"));
        }
        let control_ids: HashSet<_> = profile.special_tokens.values().copied().collect();
        if control_ids.len() != profile.special_tokens.len() {
            return Err(invalid("Controls require distinct dedicated token IDs"));
        }
        let encoder = Tokenizer::from_bytes(profile.definition.to_string())
            .map_err(|error| invalid(format!("Invalid tokenizer profile: {error}")))?;
        let mut literal = profile.definition.clone();
        literal["added_tokens"] = json!([]);
        literal["post_processor"] = Value::Null;
        // Parallelism is at the document/sample level.
        let literal = Tokenizer::from_bytes(literal.to_string())
            .map_err(|error| invalid(format!("Invalid tokenizer profile: {error}")))?;
        let dedicated = profile.definition["added_tokens"]
            .as_array()
            .ok_or_else(|| invalid("Tokenizer definition requires an added_tokens array"))?;
        for (name, &id) in &profile.special_tokens {
            let spelling = dedicated.iter().find_map(|token| {
                (token["id"].as_u64() == Some(id as u64) && token["special"] == true)
                    .then(|| token["content"].as_str())
                    .flatten()
            });
            if !spelling.is_some_and(|text| {
                encoder.id_to_token(id).as_deref() == Some(text)
                    && encoder.token_to_id(text) == Some(id)
                    && encoder
                        .encode(text, false)
                        .is_ok_and(|encoded| encoded.get_ids() == [id])
            }) {
                return Err(invalid(format!("Incompatible dedicated control: {name}")));
            }
        }
        let tokenizer = encoder
            .to_string(false)
            .map_err(|error| invalid(error.to_string()))?;
        Ok(Self {
            encoder,
            literal,
            control_ids,
            alphabet: OnceLock::new(),
            profile_sha256: fingerprint(&json!(profile)),
            tokenizer_sha256: format!("{:x}", Sha256::digest(tokenizer)),
            controls: profile.special_tokens,
            options,
            settings: parse_settings(settings)?,
        })
    }

    /// Returns the shard manifest fields that describe this compiler: format
    /// version, contract, seed, window limit and tokenizer hashes. Pass it to
    /// [`shard::ShardWriter::new`](crate::shard::ShardWriter::new).
    pub fn manifest(&self) -> Value {
        json!({
            "format": "ktremix",
            "version": "1.0",
            "array_schema": "ktremix.frontier-arrays/1",
            "execution_contract": CONTRACT,
            "compiler": VERSION,
            "seed": self.options.seed,
            "active_window_limit": self.options.active_limit,
            "profile_sha256": self.profile_sha256,
            "tokenizer": {
                "sha256": self.tokenizer_sha256,
                "special_token_ids": self.controls,
            },
        })
    }

    /// Tokenizes `text` as plain literal text, without adding any control tokens.
    pub fn literal_ids(&self, text: &str) -> Result<Vec<u32>> {
        Ok(self
            .text(text)?
            .into_iter()
            .map(|token| token.id.unwrap())
            .collect())
    }

    /// Serializes the tokenizer definition back to Hugging Face JSON.
    pub fn tokenizer_json(&self) -> Result<String> {
        self.encoder
            .to_string(false)
            .map_err(|error| invalid(error.to_string()))
    }

    /// Structural dry run. No tokenizer is needed to validate graph shape,
    /// lifecycle, references, and configured tool contracts.
    pub fn structural_report(source: &str, settings: Option<&str>) -> Result<Report> {
        let settings = parse_settings(settings)?;
        Ok(structural(source, settings.as_ref()))
    }

    /// Inspect a bounded batch of graphs with a shared settings snapshot.
    pub fn structural_reports(
        sources: &[String],
        settings: Option<&str>,
        workers: usize,
    ) -> Result<Vec<Report>> {
        let settings = parse_settings(settings)?;
        batch(sources, workers, |source| {
            structural(source, settings.as_ref())
        })
    }

    /// Inspect a bounded batch with the configured tokenizer.
    pub fn reports(&self, sources: &[String], workers: usize) -> Result<Vec<Report>> {
        batch(sources, workers, |source| self.report(source))
    }

    /// Full dry run with the configured tokenizer, using the same scheduler as compilation.
    pub fn report(&self, source: &str) -> Report {
        match self.process(source, || false) {
            Ok((report, _)) => report,
            Err(error) => Report {
                diagnostics: vec![Diagnostic::general("compile_error", error.to_string())],
                metrics: None,
            },
        }
    }

    /// Compiles one graph JSON document.
    ///
    /// Fails with [`Error::Invalid`] if the report contains errors, with the first
    /// error messages as the reason.
    pub fn compile(&self, source: &str) -> Result<Compilation> {
        self.compile_cancellable(source, || false)
    }

    /// Like [`Compiler::compile`], but polls `cancelled` while scheduling and returns
    /// [`Error::Cancelled`] once it returns `true`.
    pub fn compile_cancellable(
        &self,
        input: &str,
        cancelled: impl Fn() -> bool,
    ) -> Result<Compilation> {
        let (report, output) = self.process(input, cancelled)?;
        let mut output = output.ok_or_else(|| diagnostic_error(&report.diagnostics))?;
        output.report = report;
        Ok(output)
    }

    fn process(
        &self,
        input: &str,
        cancelled: impl Fn() -> bool,
    ) -> Result<(Report, Option<Compilation>)> {
        if cancelled() {
            return Err(Error::Cancelled);
        }
        let source = match parse_source(input) {
            Ok(source) => source,
            Err(error) => {
                return Ok((
                    Report {
                        diagnostics: vec![error],
                        metrics: None,
                    },
                    None,
                ))
            }
        };
        let (graph, diagnostics) = analyze(&source, self.settings.as_ref());
        let mut report = Report {
            diagnostics,
            metrics: None,
        };
        if report.has_errors() {
            return Ok((report, None));
        }
        let mut runtime = self.runtime(&source)?;
        let result = self
            .schedule(&source, &graph, &mut runtime, cancelled, |_| false)
            .and_then(|_| {
                if runtime.has_references() {
                    runtime.resolve_references(&self.reference_alphabet()?)?;
                }
                Ok(())
            });
        match result {
            Ok(()) => {}
            Err(Error::Cancelled) => return Err(Error::Cancelled),
            Err(error) => {
                report
                    .diagnostics
                    .push(Diagnostic::general("compile_error", error.to_string()));
                return Ok((report, None));
            }
        }
        let peak = runtime.peak;
        let (columns, names) = runtime.finish();
        report.metrics = Some(metrics(&columns, peak));
        Ok((
            report,
            Some(Compilation {
                id: source.id,
                source_hash: fingerprint(&source.intrinsic()),
                columns,
                logical_windows: names,
                report: Report::default(),
                source_metadata: source.metadata.map(|metadata| json!(metadata)),
            }),
        ))
    }

    /// Use a bounded queue of file paths and yield results in input order.
    /// Per-file read and compile failures are passed to the consumer.
    pub fn compile_files<P: AsRef<Path> + Send>(
        &self,
        paths: impl IntoIterator<Item = P>,
        workers: usize,
        queue_size: usize,
        mut consume: impl FnMut(P, Result<Compilation>) -> Result<()>,
    ) -> Result<()> {
        if workers == 0 || queue_size == 0 {
            return Err(invalid("workers and queue_size must be positive"));
        }
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(workers)
            .build()
            .map_err(|error| invalid(error.to_string()))?;
        let mut paths = paths.into_iter();
        loop {
            let batch: Vec<_> = paths.by_ref().take(queue_size).collect();
            if batch.is_empty() {
                return Ok(());
            }
            let results: Vec<_> = pool.install(|| {
                batch
                    .into_par_iter()
                    .map(|path| {
                        let result = std::fs::read_to_string(path.as_ref())
                            .map_err(Error::from)
                            .and_then(|source| self.compile(&source));
                        (path, result)
                    })
                    .collect()
            });
            for (path, result) in results {
                consume(path, result)?;
            }
        }
    }

    /// Schedules a graph and returns up to `max_frames` frames of tokens for display,
    /// plus whether the preview was truncated. Used by the studio to show what the
    /// model will see; it does not build arrays.
    pub fn preview(
        &self,
        input: &str,
        max_frames: usize,
        cancelled: impl Fn() -> bool,
    ) -> Result<(Vec<Frame>, bool)> {
        if max_frames == 0 {
            return Err(invalid("max_frames must be positive"));
        }
        let source = parse_source(input).map_err(|error| diagnostic_error(&[error]))?;
        let (graph, diagnostics) = analyze(&source, self.settings.as_ref());
        if diagnostics.iter().any(|d| d.severity == Severity::Error) {
            return Err(diagnostic_error(&diagnostics));
        }
        let mut runtime = self.runtime(&source)?;
        let mut frames = Vec::new();
        let truncated = self.schedule(&source, &graph, &mut runtime, cancelled, |frame| {
            if frames.len() == max_frames {
                return true;
            }
            frames.push(frame);
            false
        })?;
        Ok((frames, truncated))
    }

    fn runtime(&self, source: &Source) -> Result<Runtime> {
        Runtime::new(
            self.options.active_limit,
            self.options.max_rows,
            self.controls["bos"],
            &fingerprint(&json!([self.options.seed, source.id])),
        )
    }

    pub(crate) fn encode_literal(&self, text: &str) -> Result<tokenizers::Encoding> {
        let encoded = self
            .literal
            .encode(text, false)
            .map_err(|error| invalid(format!("Literal text cannot be encoded: {error}")))?;
        let ids = encoded.get_ids();
        if ids.iter().any(|id| self.control_ids.contains(id)) {
            return Err(invalid(
                "Literal text encodes as a control; use a tokenizer with literal coverage",
            ));
        }
        if !text.is_empty() && ids.is_empty() {
            return Err(invalid("Tokenizer discards nonempty literal content"));
        }
        Ok(encoded)
    }

    pub(crate) fn text(&self, text: &str) -> Result<Vec<Token>> {
        Ok(self
            .encode_literal(text)?
            .get_ids()
            .iter()
            .copied()
            .map(Token::literal)
            .collect())
    }

    pub(crate) fn control(&self, name: &'static str, header: bool) -> Token {
        Token::control(self.controls[name], name, header)
    }

    pub(crate) fn header(&self, window: &Window, slot: u32) -> Result<Vec<Token>> {
        #[derive(Serialize)]
        struct Header<'a> {
            slot: u32,
            purpose: &'a str,
            reasoning_effort: Option<&'a str>,
        }
        let payload = serde_json::to_string(&Header {
            slot,
            purpose: &window.purpose,
            reasoning_effort: window.reasoning_effort.as_deref(),
        })?;
        let mut tokens = vec![self.control("ctx_begin", true)];
        tokens.extend(self.text(&payload)?.into_iter().map(|token| Token {
            header: true,
            ..token
        }));
        tokens.push(self.control("ctx_end", true));
        Ok(tokens)
    }

    fn reference_alphabet(&self) -> Result<[u32; 16]> {
        if let Some(&alphabet) = self.alphabet.get() {
            return Ok(alphabet);
        }
        let mut alphabet = [0; 16];
        for (index, ch) in "0123456789abcdef".chars().enumerate() {
            let tokens = self.text(&ch.to_string())?;
            if tokens.len() != 1 {
                return Err(invalid(
                    "Window references require one ordinary token per hex digit",
                ));
            }
            alphabet[index] = tokens[0].id.unwrap();
        }
        let _ = self.alphabet.set(alphabet);
        Ok(alphabet)
    }

    fn event_tokens(&self, event: &Event) -> Result<Vec<Token>> {
        let mut tokens = Vec::new();
        if event.kind == Kind::ToolCall && event.insert_tool_begin {
            tokens.push(self.control("tool_begin", false));
            tokens.extend(self.text(&ascii_quote(event.tool.as_deref().unwrap()))?);
            tokens.extend(self.text(":")?);
        }
        if event.kind == Kind::ToolResponse && event.insert_response_begin {
            tokens.push(self.control("response_begin", false));
        }
        if event.kind == Kind::ChatMessage {
            tokens.push(self.control("msg_begin", false));
            tokens.extend(self.text(&ascii_quote(&event.chat.as_ref().unwrap().speaker))?);
            tokens.extend(self.text(":")?);
        }
        match &event.content {
            Content::String(text) => tokens.extend(self.text(text)?),
            Content::Pieces(pieces) => {
                for piece in pieces {
                    match piece {
                        Piece::Text { text } => tokens.extend(self.text(text)?),
                        Piece::WindowRef { window } => {
                            self.reference_alphabet()?;
                            tokens.push(self.control("window_ref", false));
                            tokens.extend((0..8).map(|digit| Token {
                                id: None,
                                control: None,
                                header: false,
                                reference: Some((window.clone(), digit)),
                            }));
                        }
                    }
                }
            }
        }
        if event.kind == Kind::ToolCall && event.insert_tool_end {
            tokens.push(self.control("tool_end", false));
        }
        if event.kind == Kind::ToolResponse && event.insert_response_end {
            tokens.push(self.control("response_end", false));
        }
        if event.kind == Kind::ChatMessage {
            tokens.push(self.control("msg_end", false));
        }
        if event.close_window {
            tokens.push(self.control("eos", false));
        }
        Ok(tokens)
    }

    fn schedule(
        &self,
        source: &Source,
        graph: &Graph,
        runtime: &mut Runtime,
        cancelled: impl Fn() -> bool,
        mut visit: impl FnMut(Frame) -> bool,
    ) -> Result<bool> {
        let mut events = Schedule::new(self, source, graph);
        let mut last_noop = false;
        let mut tick = 0;
        while events.remaining > 0 {
            if cancelled() {
                return Err(Error::Cancelled);
            }
            events.advance();
            let inputs = events.inputs(runtime)?;
            let mut outputs = events.outputs(runtime)?;
            events.noops(runtime, &mut outputs);
            runtime.output(&outputs)?;
            last_noop = outputs.iter().all(|emission| {
                emission
                    .tokens
                    .iter()
                    .all(|token| token.control == Some("noop"))
            });
            if visit(Frame {
                tick,
                inputs,
                outputs,
            }) {
                return Ok(true);
            }
            tick += 1;
        }
        if !last_noop {
            if cancelled() {
                return Err(Error::Cancelled);
            }
            let mut outputs = Vec::new();
            events.noops(runtime, &mut outputs);
            runtime.output(&outputs)?;
            return Ok(visit(Frame {
                tick,
                inputs: Vec::new(),
                outputs,
            }));
        }
        Ok(false)
    }
}

pub(crate) fn metrics(columns: &Columns, peak: usize) -> Metrics {
    let mut deltas = vec![0i64; columns.query_frontiers.last().copied().unwrap_or(0) as usize + 2];
    for (&start, &end) in columns.visible_from.iter().zip(&columns.visible_until) {
        if start < end {
            deltas[start as usize] += 1;
            if end != FOREVER && (end as usize) < deltas.len() {
                deltas[end as usize] -= 1;
            }
        }
    }
    let mut count = 0;
    let mut visible = 0;
    let mut cursor = 0;
    for &query in &columns.query_frontiers {
        let end = query as usize + 1;
        for delta in &deltas[cursor..end] {
            count += delta;
        }
        cursor = end;
        visible = visible.max(count);
    }
    Metrics {
        total_tokens: columns.token_ids.len(),
        target_tokens: columns.target_rows.len(),
        peak_active_windows: peak,
        peak_visible_tokens: visible as usize,
    }
}

fn structural(input: &str, settings: Option<&Settings>) -> Report {
    let diagnostics = match parse_source(input) {
        Ok(source) => analyze(&source, settings).1,
        Err(error) => vec![error],
    };
    Report {
        diagnostics,
        metrics: None,
    }
}

fn batch(
    sources: &[String],
    workers: usize,
    visit: impl Fn(&str) -> Report + Sync,
) -> Result<Vec<Report>> {
    match workers {
        0 => Err(invalid("workers must be positive")),
        1 => Ok(sources.iter().map(|source| visit(source)).collect()),
        _ => {
            let pool = rayon::ThreadPoolBuilder::new()
                .num_threads(workers)
                .build()
                .map_err(|error| invalid(error.to_string()))?;
            Ok(pool.install(|| sources.par_iter().map(|source| visit(source)).collect()))
        }
    }
}

fn parse_source(input: &str) -> std::result::Result<Source, Diagnostic> {
    serde_json::from_str(input)
        .map_err(|error| Diagnostic::general("invalid_source", error.to_string()))
}

fn parse_settings(settings: Option<&str>) -> Result<Option<Settings>> {
    settings
        .map(serde_json::from_str)
        .transpose()
        .map_err(Error::from)
}

fn diagnostic_error(diagnostics: &[Diagnostic]) -> Error {
    invalid(
        diagnostics
            .iter()
            .filter(|d| d.severity == Severity::Error)
            .map(|d| format!("{}: {}", d.code, d.message))
            .collect::<Vec<_>>()
            .join("; "),
    )
}

fn ascii_quote(text: &str) -> String {
    use std::fmt::Write;
    let quoted = serde_json::to_string(text).unwrap();
    let mut output = String::new();
    for ch in quoted.chars() {
        if ch.is_ascii() && ch != '\x7f' {
            output.push(ch);
        } else {
            for unit in ch.encode_utf16(&mut [0; 2]) {
                write!(output, "\\u{unit:04x}").unwrap();
            }
        }
    }
    output
}

struct Running {
    tokens: VecDeque<Token>,
    header: Vec<Token>,
    opening: bool,
    started: bool,
}

struct Schedule<'a> {
    compiler: &'a Compiler,
    source: &'a Source,
    graph: &'a Graph<'a>,
    events: Vec<&'a Event>,
    remaining: usize,
    degrees: Vec<usize>,
    external_ready: BTreeSet<usize>,
    model_ready: BTreeSet<usize>,
    delayed: Vec<usize>,
    running: BTreeMap<usize, Running>,
}

impl<'a> Schedule<'a> {
    fn new(compiler: &'a Compiler, source: &'a Source, graph: &'a Graph<'a>) -> Self {
        let mut state = Self {
            compiler,
            source,
            graph,
            events: graph
                .names
                .iter()
                .map(|&name| &source.events[name])
                .collect(),
            remaining: graph.names.len(),
            degrees: graph.dependencies.iter().map(Vec::len).collect(),
            external_ready: BTreeSet::new(),
            model_ready: BTreeSet::new(),
            delayed: Vec::new(),
            running: BTreeMap::new(),
        };
        state
            .delayed
            .extend((0..state.degrees.len()).filter(|&id| state.degrees[id] == 0));
        state
    }

    fn advance(&mut self) {
        for id in std::mem::take(&mut self.delayed) {
            self.ready(id);
        }
    }

    fn ready(&mut self, id: usize) {
        if self.events[id].model() {
            self.model_ready.insert(id);
        } else {
            self.external_ready.insert(id);
        }
    }

    fn complete(&mut self, id: usize) {
        self.remaining -= 1;
        for &child in &self.graph.children[id] {
            self.degrees[child] -= 1;
            if self.degrees[child] == 0 {
                if !self.events[id].model()
                    && (self.events[child].model() || self.events[child].delivery.chain_immediately)
                {
                    self.ready(child);
                } else {
                    self.delayed.push(child);
                }
            }
        }
    }

    fn begin(&mut self, id: usize, runtime: &mut Runtime) -> Result<()> {
        let event = self.events[id];
        let window = &self.source.windows[&event.window];
        let opening = !runtime.live.contains_key(&event.window);
        let mut header = if opening {
            let slot = runtime.open(&event.window, event.model(), window.agent_writable)?;
            self.compiler.header(window, slot)?
        } else {
            Vec::new()
        };
        let body = self.compiler.event_tokens(event)?;
        if event.replace_body && body.iter().all(|token| token.control == Some("eos")) {
            return Err(invalid("Replacement must encode nonempty content"));
        }
        let tokens = if event.model() {
            let mut tokens = std::mem::take(&mut header);
            tokens.extend(body);
            tokens
        } else {
            body
        };
        self.running.insert(
            id,
            Running {
                tokens: tokens.into(),
                header,
                opening,
                started: false,
            },
        );
        self.external_ready.remove(&id);
        self.model_ready.remove(&id);
        Ok(())
    }

    fn inputs(&mut self, runtime: &mut Runtime) -> Result<Vec<Emission>> {
        let mut inputs = Vec::new();
        let mut busy = HashSet::new();
        loop {
            let running = self
                .running
                .keys()
                .copied()
                .filter(|&id| !self.events[id].model());
            let ordered: BTreeSet<_> = running.chain(self.external_ready.iter().copied()).collect();
            let before = inputs.len();
            for id in ordered {
                let event = self.events[id];
                if busy.contains(event.window.as_str()) {
                    continue;
                }
                if !self.running.contains_key(&id) {
                    self.begin(id, runtime)?;
                }
                let state = self.running.get_mut(&id).unwrap();
                let mut tokens = std::mem::take(&mut state.header);
                match event.delivery.mode {
                    Mode::Instant => tokens.extend(state.tokens.drain(..)),
                    Mode::Streamed => tokens.extend(state.tokens.pop_front()),
                }
                let emission = Emission {
                    event: Some(self.graph.names[id].into()),
                    window: event.window.clone(),
                    tokens,
                    model: false,
                    weight: event.weight(),
                    replace: event.replace_body && !state.started,
                    opening: std::mem::take(&mut state.opening),
                };
                runtime.input(&emission, self.source.windows[&event.window].agent_writable)?;
                inputs.push(emission);
                state.started = true;
                if state.tokens.is_empty() {
                    self.running.remove(&id);
                    self.complete(id);
                } else {
                    busy.insert(event.window.as_str());
                }
            }
            if inputs.len() == before {
                break;
            }
        }
        Ok(inputs)
    }

    fn outputs(&mut self, runtime: &mut Runtime) -> Result<Vec<Emission>> {
        let mut outputs = Vec::new();
        let mut used_available = false;
        for id in self.model_ready.iter().copied().collect::<Vec<_>>() {
            let event = self.events[id];
            if !runtime.live.contains_key(&event.window) {
                if used_available {
                    continue;
                }
                used_available = true;
            }
            self.begin(id, runtime)?;
        }
        let mut busy = HashSet::new();
        for id in self
            .running
            .keys()
            .copied()
            .filter(|&id| self.events[id].model())
            .collect::<Vec<_>>()
        {
            let event = self.events[id];
            if busy.contains(event.window.as_str()) {
                continue;
            }
            let state = self.running.get_mut(&id).unwrap();
            if let Some(token) = state.tokens.pop_front() {
                busy.insert(event.window.as_str());
                outputs.push(Emission {
                    event: Some(self.graph.names[id].into()),
                    window: event.window.clone(),
                    tokens: vec![token],
                    model: true,
                    weight: event.weight(),
                    replace: false,
                    opening: state.opening,
                });
                state.opening = false;
            }
            if state.tokens.is_empty() {
                self.running.remove(&id);
                self.complete(id);
            }
        }
        Ok(outputs)
    }

    fn noops(&self, runtime: &Runtime, outputs: &mut Vec<Emission>) {
        let busy: HashSet<_> = outputs
            .iter()
            .map(|emission| emission.window.as_str())
            .collect();
        let mut noops = Vec::new();
        for (window, &index) in &runtime.live {
            if runtime.writable(index) && !busy.contains(window.as_str()) {
                noops.push(self.noop(window));
            }
        }
        if !outputs.iter().any(|emission| emission.opening) {
            noops.push(self.noop("@available"));
        }
        outputs.extend(noops);
    }

    fn noop(&self, window: &str) -> Emission {
        Emission {
            event: None,
            window: window.into(),
            tokens: vec![self.compiler.control("noop", false)],
            model: true,
            weight: self.source.defaults.noop_loss_weight,
            replace: false,
            opening: false,
        }
    }
}
