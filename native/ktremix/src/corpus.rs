//! Native raw-text lowering for causal, interleaved language-model samples.

use crate::compiler::{metrics, Compiler, VERSION};
use crate::diagnostics::valid_weight;
use crate::model::Window;
use crate::runtime::Runtime;
use crate::shard::{Codec, Shard, ShardWriter};
use crate::{fingerprint, invalid, Columns, Emission, Error, Metrics, Result, Token};
use rand::{rngs::StdRng, Rng, SeedableRng};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use uuid::Uuid;

#[derive(Debug, Deserialize)]
pub struct Document {
    pub text: String,
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub provenance: Value,
}

/// One UTF-8 document per file, or a stream of JSONL objects with a `text` field.
#[derive(Clone, Copy)]
pub enum Input {
    Text,
    JsonLines,
}

pub struct Documents<P, I> {
    paths: I,
    input: Input,
    limit: usize,
    current: Option<(P, BufReader<std::fs::File>, usize)>,
}

impl<P: AsRef<Path>, I: Iterator<Item = P>> Documents<P, I> {
    pub fn new(paths: I, input: Input, max_document_bytes: usize) -> Self {
        Self {
            paths,
            input,
            limit: max_document_bytes,
            current: None,
        }
    }
}

impl<P: AsRef<Path>, I: Iterator<Item = P>> Iterator for Documents<P, I> {
    type Item = Result<Document>;

    fn next(&mut self) -> Option<Self::Item> {
        use std::io::Read;
        loop {
            if self.current.is_none() {
                let path = self.paths.next()?;
                let file = match std::fs::File::open(path.as_ref()) {
                    Ok(file) => file,
                    Err(error) => return Some(Err(error.into())),
                };
                self.current = Some((path, BufReader::new(file), 0));
            }
            let (path, source, line) = self.current.as_mut().unwrap();
            *line += 1;
            let id = format!("{}:{}", path.as_ref().display(), *line);
            let mut bytes = Vec::new();
            let mut limited = source.take((self.limit as u64).saturating_add(1));
            let read = match self.input {
                Input::Text => limited.read_to_end(&mut bytes),
                Input::JsonLines => limited.read_until(b'\n', &mut bytes),
            };
            let read = match read {
                Ok(read) => read,
                Err(error) => return Some(Err(error.into())),
            };
            if read > self.limit {
                return Some(Err(invalid(format!(
                    "Corpus document exceeds byte limit: {id}"
                ))));
            }
            if read == 0 || matches!(self.input, Input::Text) {
                self.current = None;
            }
            if read == 0 {
                continue;
            }
            if matches!(self.input, Input::JsonLines) && bytes.iter().all(u8::is_ascii_whitespace) {
                continue;
            }
            return Some(match self.input {
                Input::Text => String::from_utf8(bytes)
                    .map(|text| Document {
                        text,
                        id: Some(id.clone()),
                        provenance: Value::Null,
                    })
                    .map_err(|error| invalid(format!("Invalid UTF-8 in {id}: {error}"))),
                Input::JsonLines => serde_json::from_slice::<Document>(&bytes)
                    .map(|mut document| {
                        document.id.get_or_insert(id.clone());
                        document
                    })
                    .map_err(|error| invalid(format!("Invalid corpus JSON in {id}: {error}"))),
            });
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct CorpusOptions {
    pub seed: u64,
    pub documents_per_sample: usize,
    pub min_section_tokens: usize,
    pub max_section_tokens: usize,
    pub max_tokens_per_document_part: usize,
    pub single_window_fraction: f64,
    pub retained_sections: usize,
    pub max_tick_offset: u64,
    pub control_loss_weight: f64,
    pub noop_loss_weight: f64,
    pub samples_per_shard: usize,
    pub workers: usize,
    pub queue_size: usize,
    pub resume: bool,
}

impl Default for CorpusOptions {
    fn default() -> Self {
        Self {
            seed: 0,
            documents_per_sample: 2,
            min_section_tokens: 64,
            max_section_tokens: 256,
            max_tokens_per_document_part: 4096,
            single_window_fraction: 0.25,
            retained_sections: 2,
            max_tick_offset: 16,
            control_loss_weight: 0.0,
            noop_loss_weight: 0.0,
            samples_per_shard: 128,
            workers: 1,
            queue_size: 16,
            resume: false,
        }
    }
}

impl CorpusOptions {
    fn validate(&self, limit: u32) -> Result<()> {
        let capacity = self
            .documents_per_sample
            .checked_mul(self.retained_sections.saturating_add(1));
        if self.documents_per_sample == 0
            || self.min_section_tokens == 0
            || self.max_section_tokens < self.min_section_tokens
            || self.max_tokens_per_document_part < self.max_section_tokens
            || !(0.0..=1.0).contains(&self.single_window_fraction)
            || self.max_tick_offset > 1_000_000
            || !valid_weight(self.control_loss_weight)
            || !valid_weight(self.noop_loss_weight)
            || self.samples_per_shard == 0
            || self.workers == 0
            || self.queue_size == 0
            || capacity.is_none_or(|n| n > limit as usize)
        {
            return Err(invalid(
                "Invalid corpus options or active window limit too small for retention",
            ));
        }
        Ok(())
    }

    fn identity(&self) -> Value {
        let mut value = json!(self);
        let values = value.as_object_mut().unwrap();
        for name in ["resume", "workers", "queue_size"] {
            values.remove(name);
        }
        value
    }
}

struct Part {
    id: String,
    ordinal: usize,
    paragraphs: Vec<Vec<u32>>,
    provenance: Value,
    hash: String,
    source_hash: String,
}

impl Compiler {
    /// Convert caller-provided documents into atomic KTREMIX shards. Input order
    /// determines sample IDs and output order regardless of worker count.
    pub fn convert_corpus(
        &self,
        input: impl IntoIterator<Item = Result<Document>>,
        directory: impl AsRef<Path>,
        options: &CorpusOptions,
        provenance: Value,
        cancelled: impl Fn() -> bool + Sync,
    ) -> Result<Vec<PathBuf>> {
        options.validate(self.options.active_limit)?;
        let configuration = options.identity();
        let dataset_key = fingerprint(&json!([
            VERSION,
            self.profile_sha256,
            self.options.active_limit,
            self.options.seed,
            configuration,
            provenance
        ]));
        let mut dataset_id = None;
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(options.workers)
            .build()
            .map_err(|error| invalid(error.to_string()))?;
        let directory = directory.as_ref();
        let mut input = input.into_iter();
        let mut next_part = VecDeque::new();
        let mut paths = Vec::new();
        let mut sample_index = 0usize;
        let mut document_index = 0usize;
        let mut input_exhausted = false;
        loop {
            let mut shard = Vec::<(usize, Vec<Part>, bool)>::new();
            while shard.len() < options.samples_per_shard {
                check(&cancelled)?;
                let mut rng = rng(options.seed, sample_index);
                let single = rng.gen_bool(options.single_window_fraction);
                let group_size = if single {
                    1
                } else {
                    options.documents_per_sample
                };
                while next_part.len() < group_size && !input_exhausted {
                    let documents: Vec<_> = input
                        .by_ref()
                        .take(options.queue_size)
                        .collect::<Result<_>>()?;
                    input_exhausted = documents.len() < options.queue_size;
                    let ready: Result<Vec<_>> = pool.install(|| {
                        documents
                            .into_par_iter()
                            .enumerate()
                            .map(|(index, document)| {
                                check(&cancelled)?;
                                self.prepare(document, document_index + index, options)
                            })
                            .collect()
                    });
                    let ready = ready?;
                    document_index += ready.len();
                    next_part.extend(ready.into_iter().flatten());
                }
                if next_part.is_empty() {
                    break;
                }
                // Avoid using later parts of the same document as unrelated distractors.
                let mut group = Vec::new();
                for _ in 0..group_size {
                    let Some(part) = next_part.front() else { break };
                    if group.iter().any(|previous: &Part| previous.id == part.id) {
                        break;
                    }
                    group.push(next_part.pop_front().unwrap());
                }
                shard.push((sample_index, group, single));
                sample_index += 1;
            }
            let shard_index = paths.len();
            let path = directory.join(format!("corpus-{shard_index:06}.ktremix"));
            if shard.is_empty() {
                if options.resume && path.exists() {
                    return Err(invalid(format!(
                        "Existing corpus has extra shards starting at {}",
                        path.display()
                    )));
                }
                return Ok(paths);
            }

            let source_hash = fingerprint(&json!(shard
                .iter()
                .flat_map(|(_, parts, _)| {
                    parts.iter().map(|part| {
                        (
                            &part.id,
                            part.ordinal,
                            &part.hash,
                            &part.source_hash,
                            &part.provenance,
                        )
                    })
                })
                .collect::<Vec<_>>()));
            let dataset_id = *dataset_id.get_or_insert_with(|| {
                Uuid::new_v5(
                    &Uuid::NAMESPACE_OID,
                    format!("{dataset_key}:{source_hash}").as_bytes(),
                )
            });
            let shard_id = Uuid::new_v5(&dataset_id, &(shard_index as u64).to_le_bytes());
            let mut manifest = self.manifest();
            manifest["dataset_id"] = json!(dataset_id);
            manifest["shard_id"] = json!(shard_id);
            manifest["shard_index"] = json!(shard_index);
            manifest["corpus"] = json!({"options": configuration, "provenance": provenance,
                                         "source_sha256": source_hash});
            if options.resume && path.exists() {
                let existing = Shard::open(&path)?;
                if existing.shard_id != shard_id
                    || existing.len() != shard.len()
                    || existing.manifest["corpus"] != manifest["corpus"]
                {
                    return Err(invalid(format!(
                        "Existing corpus shard differs: {}",
                        path.display()
                    )));
                }
            } else {
                let mut writer = ShardWriter::new(&path, manifest, shard.len(), Codec::Lz4)?;
                for batch in shard.chunks(options.queue_size) {
                    let compiled: Result<Vec<_>> = pool.install(|| {
                        batch
                            .par_iter()
                            .map(|(index, parts, single)| {
                                check(&cancelled)?;
                                self.compile_corpus(
                                    parts, *single, *index, dataset_id, options, &cancelled,
                                )
                            })
                            .collect()
                    });
                    for (id, columns, metadata) in compiled? {
                        check(&cancelled)?;
                        writer.push(id, &columns, Some(&metadata))?;
                    }
                }
                writer.finish()?;
            }
            paths.push(path);
        }
    }

    fn prepare(
        &self,
        document: Document,
        index: usize,
        options: &CorpusOptions,
    ) -> Result<Vec<Part>> {
        if document.text.trim().is_empty() {
            return Ok(Vec::new());
        }
        let source_hash = format!("{:x}", Sha256::digest(document.text.as_bytes()));
        let id = document.id.unwrap_or_else(|| index.to_string());
        let cap = options.max_tokens_per_document_part;
        let mut current = Vec::new();
        let mut parts = Vec::new();
        let mut size = 0;
        let mut flush = |paragraphs: &mut Vec<Vec<u32>>| {
            let mut hash = Sha256::new();
            for paragraph in &*paragraphs {
                hash.update((paragraph.len() as u64).to_le_bytes());
                for token in paragraph {
                    hash.update(token.to_le_bytes());
                }
            }
            parts.push(Part {
                id: id.clone(),
                ordinal: parts.len(),
                paragraphs: std::mem::take(paragraphs),
                provenance: document.provenance.clone(),
                hash: format!("{:x}", hash.finalize()),
                source_hash: source_hash.clone(),
            });
        };
        // Encode in document context, then use offsets to split the original
        // token stream at paragraph boundaries without changing tokenization.
        let encoding = self.encode_literal(&document.text)?;
        let ids = encoding.get_ids();
        let offsets = encoding.get_offsets();
        let mut byte_end = 0;
        let mut start = 0;
        for paragraph in Paragraphs(&document.text) {
            byte_end += paragraph.len();
            let end = if byte_end == document.text.len() {
                ids.len()
            } else {
                start + offsets[start..].partition_point(|&(offset, _)| offset < byte_end)
            };
            for mut remaining in ids[start..end]
                .chunks(cap)
                .map(|slice| slice.iter().copied())
            {
                while remaining.len() > 0 {
                    let count = (cap - size).min(remaining.len());
                    current.push(remaining.by_ref().take(count).collect());
                    size += count;
                    if size == cap {
                        flush(&mut current);
                        size = 0;
                    }
                }
            }
            start = end;
        }
        if !current.is_empty() {
            flush(&mut current);
        }
        Ok(parts)
    }

    fn compile_corpus(
        &self,
        parts: &[Part],
        single: bool,
        index: usize,
        dataset: Uuid,
        options: &CorpusOptions,
        cancelled: impl Fn() -> bool,
    ) -> Result<(Uuid, Columns, Value)> {
        let id = Uuid::new_v5(&dataset, &(index as u64).to_le_bytes());
        let mut rng = rng(options.seed, index);
        let mut runtime = Runtime::new(
            self.options.active_limit,
            self.options.max_rows,
            self.controls["bos"],
            &id.to_string(),
        )?;
        let mut tracks: Vec<_> = parts
            .iter()
            .map(|part| {
                let sections = if single {
                    VecDeque::from([part.paragraphs.concat()])
                } else {
                    sections(part, options, &mut rng)
                };
                Track {
                    sections,
                    body: None,
                    completed: VecDeque::new(),
                    ready: rng.gen_range(0..=options.max_tick_offset),
                    next: 0,
                }
            })
            .collect();
        let mut tick = 0;
        let spec = Window {
            purpose: "raw_corpus".into(),
            agent_writable: true,
            reasoning_effort: None,
            intentionally_left_open: false,
        };
        let mut control_rows = 0usize;
        let mut natural_rows = 0usize;
        let mut noop_rows = 0usize;
        while tracks.iter().any(Track::pending) {
            check(&cancelled)?;
            let mut outputs = Vec::new();
            let mut opening_used = false;
            for (document, track) in tracks.iter_mut().enumerate() {
                if track.ready > tick {
                    continue;
                }
                if track.body.is_none()
                    && (track.completed.len() > options.retained_sections
                        || track.sections.is_empty())
                {
                    if let Some(window) = track.completed.pop_front() {
                        outputs.push(output(
                            window,
                            self.control("eos", false),
                            options.control_loss_weight,
                            false,
                        ));
                        control_rows += 1;
                        continue;
                    }
                }
                let opening = track.body.is_none()
                    && !opening_used
                    && !track.sections.is_empty()
                    && track.completed.len() <= options.retained_sections;
                if opening {
                    opening_used = true;
                    let name = format!("corpus-{document}-{}", track.next);
                    track.next += 1;
                    let slot = runtime.open(&name, true, true)?;
                    let header = self.header(&spec, slot)?.into();
                    let tokens = track.sections.pop_front().unwrap().into_iter();
                    track.body = Some((name, header, tokens));
                }
                if let Some((name, header, body)) = &mut track.body {
                    let (token, weight) = if let Some(token) = header.pop_front() {
                        control_rows += 1;
                        (token, options.control_loss_weight)
                    } else if let Some(id) = body.next() {
                        natural_rows += 1;
                        (Token::literal(id), 1.0)
                    } else {
                        return Err(invalid("Empty corpus section"));
                    };
                    outputs.push(output(name.clone(), token, weight, opening));
                    if header.is_empty() && body.len() == 0 {
                        track.completed.push_back(name.clone());
                        track.body = None;
                        track.ready = tick + 1 + rng.gen_range(0..=options.max_tick_offset);
                    }
                }
            }
            let existing = outputs
                .iter()
                .map(|emission| emission.window.as_str())
                .collect::<std::collections::HashSet<_>>();
            let mut noops = Vec::new();
            for name in runtime.live.keys() {
                if !existing.contains(name.as_str()) {
                    noops.push(output(
                        name.clone(),
                        self.control("noop", false),
                        options.noop_loss_weight,
                        false,
                    ));
                }
            }
            if !opening_used {
                noops.push(output(
                    "@available".into(),
                    self.control("noop", false),
                    options.noop_loss_weight,
                    false,
                ));
            }
            noop_rows += noops.len();
            outputs.extend(noops);
            runtime.output(&outputs)?;
            tick += 1;
        }
        runtime.output(&[output(
            "@available".into(),
            self.control("noop", false),
            options.noop_loss_weight,
            false,
        )])?;
        noop_rows += 1;
        let peak = runtime.peak;
        let (columns, _) = runtime.finish();
        let metrics: Metrics = metrics(&columns, peak);
        let metadata = json!({
            "corpus": {
                "sample_index": index, "single_window": single, "text_tokens": natural_rows,
                "noop_tokens": noop_rows, "control_and_framing_rows": metrics.total_tokens - natural_rows - noop_rows,
                "predicted_control_and_framing_rows": control_rows,
                "documents": parts.iter().map(|part| json!({"id": part.id, "part": part.ordinal,
                    "token_sha256": part.hash, "source_sha256": part.source_hash, "provenance": part.provenance})).collect::<Vec<_>>(),
            },
            "metrics": metrics,
        });
        Ok((id, columns, metadata))
    }
}

fn check(cancelled: impl Fn() -> bool) -> Result<()> {
    if cancelled() {
        Err(Error::Cancelled)
    } else {
        Ok(())
    }
}

fn rng(seed: u64, sample: usize) -> StdRng {
    let mut hash = Sha256::new();
    hash.update(seed.to_le_bytes());
    hash.update((sample as u64).to_le_bytes());
    StdRng::from_seed(hash.finalize().into())
}

struct Paragraphs<'a>(&'a str);

impl<'a> Iterator for Paragraphs<'a> {
    type Item = &'a str;

    fn next(&mut self) -> Option<Self::Item> {
        if self.0.is_empty() {
            return None;
        }
        let bytes = self.0.as_bytes();
        let end = self
            .0
            .match_indices('\n')
            .find_map(|(index, _)| {
                let next = &bytes[index + 1..];
                if next.starts_with(b"\n") {
                    Some(index + 2)
                } else if index > 0 && bytes[index - 1] == b'\r' && next.starts_with(b"\r\n") {
                    Some(index + 3)
                } else {
                    None
                }
            })
            .unwrap_or(bytes.len());
        let (paragraph, rest) = self.0.split_at(end);
        self.0 = rest;
        Some(paragraph)
    }
}

fn sections(part: &Part, options: &CorpusOptions, rng: &mut StdRng) -> VecDeque<Vec<u32>> {
    let mut sections = VecDeque::new();
    let mut current = Vec::new();
    let mut target = rng.gen_range(options.min_section_tokens..=options.max_section_tokens);
    for paragraph in &part.paragraphs {
        if !current.is_empty() && current.len() + paragraph.len() > target {
            sections.push_back(std::mem::take(&mut current));
            target = rng.gen_range(options.min_section_tokens..=options.max_section_tokens);
        }
        let mut words = paragraph.iter().copied();
        while words.len() > 0 {
            let count = (target - current.len()).min(words.len());
            current.extend(words.by_ref().take(count));
            if current.len() >= target {
                sections.push_back(std::mem::take(&mut current));
                target = rng.gen_range(options.min_section_tokens..=options.max_section_tokens);
            }
        }
    }
    if !current.is_empty() {
        sections.push_back(current);
    }
    sections
}

struct Track {
    sections: VecDeque<Vec<u32>>,
    body: Option<(String, VecDeque<Token>, std::vec::IntoIter<u32>)>,
    completed: VecDeque<String>,
    ready: u64,
    next: usize,
}

impl Track {
    fn pending(&self) -> bool {
        !self.sections.is_empty() || self.body.is_some() || !self.completed.is_empty()
    }
}

fn output(window: String, token: Token, weight: f64, opening: bool) -> Emission {
    Emission {
        event: None,
        window,
        tokens: vec![token],
        model: true,
        weight,
        replace: false,
        opening,
    }
}
