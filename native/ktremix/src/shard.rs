//! KTREMIX 1.0 wire-format reader and atomic streaming writer.

use crate::{invalid, Columns, Result};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::ops::Range;
use std::path::{Path, PathBuf};
use uuid::Uuid;

const MAGIC: &[u8; 8] = b"KTREMIX\0";
const RECORD_MAGIC: &[u8; 8] = b"KTSAMP\0\0";
const HEADER_SIZE: usize = 128;
const ENTRY_SIZE: usize = 64;
const RECORD_SIZE: usize = 64;
const COLUMN_SIZE: usize = 32;
const MAX_MANIFEST: usize = 1 << 20;
/// Largest uncompressed record [`Shard::open`] accepts. Guards against corrupt or hostile files.
pub const DEFAULT_MAX_RECORD_BYTES: usize = 512 << 20;

const NAMES: [&str; 16] = [
    "token_ids",
    "window_ids",
    "position_ids",
    "query_frontiers",
    "visible_from",
    "visible_until",
    "previous_rows",
    "loss_weights",
    "row_flags",
    "frame_offsets",
    "window_slots",
    "window_open_frontiers",
    "window_close_frontiers",
    "window_activation_frontiers",
    "window_flags",
    "target_rows",
];
const DTYPES: [u8; 16] = [3, 3, 3, 4, 4, 4, 3, 6, 1, 4, 3, 4, 4, 4, 1, 3];

fn align(value: u64) -> Result<u64> {
    value
        .checked_add(63)
        .map(|n| n & !63)
        .ok_or_else(|| invalid("KTREMIX size overflow"))
}

fn numbers(data: &[u8], at: usize) -> u16 {
    u16::from_le_bytes(data[at..at + 2].try_into().unwrap())
}
fn number(data: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(data[at..at + 4].try_into().unwrap())
}
fn wide(data: &[u8], at: usize) -> u64 {
    u64::from_le_bytes(data[at..at + 8].try_into().unwrap())
}

fn valid(condition: bool, message: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(invalid(message))
    }
}

fn put(data: &mut [u8], at: usize, bytes: &[u8]) {
    data[at..at + bytes.len()].copy_from_slice(bytes);
}

fn crc(data: &[u8]) -> u32 {
    crc32fast::hash(data)
}

fn width(dtype: u8) -> Option<u64> {
    match dtype {
        1 => Some(1),
        3 | 6 => Some(4),
        4 => Some(8),
        _ => None,
    }
}

fn extent(offset: u64, size: u64, limit: u64) -> Result<Range<usize>> {
    let end = offset
        .checked_add(size)
        .filter(|&n| n <= limit)
        .ok_or_else(|| invalid("KTREMIX extent exceeds file or record bounds"))?;
    Ok(offset as usize..end as usize)
}

fn dims(rows: usize, frames: usize, windows: usize, targets: usize) -> [u64; 16] {
    let mut lengths = [rows as u64; 16];
    lengths[9] = frames as u64 + 1;
    lengths[10..15].fill(windows as u64);
    lengths[15] = targets as u64;
    lengths
}

fn uuid(value: &Value) -> Result<Uuid> {
    value
        .as_str()
        .and_then(|s| Uuid::parse_str(s).ok())
        .ok_or_else(|| invalid("Invalid KTREMIX identity"))
}

pub(crate) fn compatibility(manifest: &Value) -> Result<Value> {
    let tokenizer = &manifest["tokenizer"];
    let digest = tokenizer["sha256"].as_str().unwrap_or_default();
    let controls = tokenizer["special_token_ids"]
        .as_object()
        .ok_or_else(|| invalid("Missing KTREMIX control token IDs"))?;
    let required: HashSet<_> = ["bos", "eos", "noop"]
        .into_iter()
        .filter_map(|key| controls.get(key).and_then(Value::as_u64))
        .collect();
    valid(
        manifest["format"] == "ktremix"
            && manifest["version"] == "1.0"
            && manifest["array_schema"] == "ktremix.frontier-arrays/1"
            && manifest["execution_contract"]
                .as_str()
                .is_some_and(|s| !s.is_empty())
            && digest.len() == 64
            && digest
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            && required.len() == 3
            && controls
                .values()
                .all(|id| id.as_u64().is_some_and(|n| n <= u32::MAX as u64)),
        "Invalid KTREMIX manifest compatibility fields",
    )?;
    Ok(json!([
        manifest["array_schema"],
        manifest["execution_contract"],
        tokenizer["sha256"],
        tokenizer["special_token_ids"]
    ]))
}

/// Compression applied to each sample record.
#[derive(Clone, Copy, Debug, Default)]
pub enum Codec {
    /// Store records raw.
    None,
    /// LZ4 block compression (default). Fast to read.
    #[default]
    Lz4,
    /// Zlib compression. Smaller files, slower to read.
    Zlib,
}

impl std::str::FromStr for Codec {
    type Err = crate::Error;

    fn from_str(name: &str) -> Result<Self> {
        match name {
            "none" => Ok(Self::None),
            "lz4" => Ok(Self::Lz4),
            "zlib" => Ok(Self::Zlib),
            _ => Err(invalid("Unknown KTREMIX codec")),
        }
    }
}

#[derive(Clone, Copy)]
struct Entry {
    id: Uuid,
    offset: u64,
    stored: u32,
    raw: u32,
    rows: u32,
    frames: u32,
    windows: u32,
    checksum: u32,
    codec: u8,
    targets: u32,
}

impl Entry {
    fn decode(data: &[u8], limit: usize, start: u64, file_size: u64) -> Result<Self> {
        let entry = Self {
            id: Uuid::from_bytes(data[..16].try_into().unwrap()),
            offset: wide(data, 16),
            stored: number(data, 24),
            raw: number(data, 28),
            rows: number(data, 32),
            frames: number(data, 36),
            windows: number(data, 40),
            checksum: number(data, 44),
            codec: data[48],
            targets: number(data, 52),
        };
        valid(
            data[49..52] == [0; 3]
                && data[56..64] == [0; 8]
                && entry.codec <= 2
                && (entry.codec != 0 || entry.stored == entry.raw)
                && entry.offset >= start
                && entry.offset.is_multiple_of(64)
                && entry.stored > 0
                && entry.stored as usize <= limit
                && (RECORD_SIZE..=limit).contains(&(entry.raw as usize))
                && (1..=entry.rows).contains(&entry.frames)
                && (1..=entry.rows).contains(&entry.windows)
                && entry.targets <= entry.rows,
            "Invalid KTREMIX index entry or resource limit",
        )?;
        extent(entry.offset, entry.stored as u64, file_size)?;
        Ok(entry)
    }

    fn bytes(self) -> [u8; ENTRY_SIZE] {
        let mut data = [0; ENTRY_SIZE];
        put(&mut data, 0, self.id.as_bytes());
        put(&mut data, 16, &self.offset.to_le_bytes());
        for (index, n) in [
            self.stored,
            self.raw,
            self.rows,
            self.frames,
            self.windows,
            self.checksum,
        ]
        .into_iter()
        .enumerate()
        {
            put(&mut data, 24 + index * 4, &n.to_le_bytes());
        }
        data[48] = self.codec;
        put(&mut data, 52, &self.targets.to_le_bytes());
        data
    }
}

/// An owned sample whose columns can be borrowed directly as little-endian bytes.
pub struct Sample {
    /// Sample UUID.
    pub id: Uuid,
    /// Number of rows.
    pub rows: u32,
    /// Number of frames.
    pub frames: u32,
    /// Number of window instances.
    pub windows: u32,
    /// Number of rows with positive loss weight.
    pub targets: u32,
    data: Vec<u8>,
    columns: [Range<usize>; 16],
    metadata: Range<usize>,
}

impl Sample {
    fn decode(data: Vec<u8>, entry: Entry) -> Result<Self> {
        valid(
            data.len() == entry.raw as usize,
            "KTREMIX record size mismatch",
        )?;
        valid(
            &data[..8] == RECORD_MAGIC
                && number(&data, 8) == 1
                && number(&data, 12) == 64
                && &data[16..32] == entry.id.as_bytes()
                && number(&data, 40) == entry.rows
                && number(&data, 44) == entry.frames
                && number(&data, 48) == entry.windows
                && number(&data, 52) == entry.targets
                && number(&data, 56) == 32
                && number(&data, 60) == 0,
            "Invalid KTREMIX record header or index/record mismatch",
        )?;
        let count = number(&data, 32) as u64;
        let end = RECORD_SIZE as u64 + count * COLUMN_SIZE as u64;
        let metadata = extent(end, number(&data, 36) as u64, data.len() as u64)?;
        let first = align(metadata.end as u64)?;
        let limits = dims(
            entry.rows as usize,
            entry.frames as usize,
            entry.windows as usize,
            entry.targets as usize,
        );
        valid(
            count >= 16 && first <= data.len() as u64,
            "Invalid KTREMIX column directory",
        )?;
        let mut columns = std::array::from_fn(|_| 0..0);
        let mut seen = HashSet::new();
        let mut ranges = Vec::new();
        for ordinal in 0..count as usize {
            let field = RECORD_SIZE + ordinal * COLUMN_SIZE;
            let id = numbers(&data, field) as usize;
            let dtype = data[field + 2];
            let flags = data[field + 3];
            let offset = wide(&data, field + 4);
            let count = wide(&data, field + 12);
            let size = wide(&data, field + 20);
            let range = extent(offset, size, data.len() as u64)?;
            valid(
                seen.insert(id)
                    && flags <= 1
                    && number(&data, field + 28) == 0
                    && offset >= first
                    && offset.is_multiple_of(64)
                    && width(dtype)
                        .map_or(flags == 0, |width| count.checked_mul(width) == Some(size)),
                "Invalid KTREMIX column entry",
            )?;
            if !range.is_empty() {
                ranges.push(range.clone());
            }
            if (1..=16).contains(&id) {
                let index = id - 1;
                valid(
                    dtype == DTYPES[index] && flags == 1 && count == limits[index],
                    "KTREMIX column type or size mismatch",
                )?;
                columns[index] = range;
            } else {
                valid(flags == 0, "Unsupported required KTREMIX column")?;
            }
        }
        valid(
            (1..=16).all(|id| seen.contains(&id)),
            "Missing required KTREMIX columns",
        )?;
        ranges.sort_unstable_by_key(|range| range.start);
        valid(
            ranges.windows(2).all(|pair| pair[0].end <= pair[1].start),
            "Overlapping KTREMIX columns",
        )?;
        Ok(Self {
            id: entry.id,
            rows: entry.rows,
            frames: entry.frames,
            windows: entry.windows,
            targets: entry.targets,
            data,
            columns,
            metadata,
        })
    }

    /// Returns a column as little-endian bytes, or `None` for an unknown name.
    ///
    /// Names match the fields of [`Columns`], for example `"token_ids"`.
    pub fn column(&self, name: &str) -> Option<&[u8]> {
        NAMES
            .iter()
            .position(|&column| column == name)
            .map(|index| &self.data[self.columns[index].clone()])
    }

    /// Iterates over `(name, dtype code, byte range)` for every column. Ranges index into [`Sample::record_bytes`].
    pub fn columns(&self) -> impl Iterator<Item = (&'static str, u8, Range<usize>)> + '_ {
        NAMES
            .into_iter()
            .zip(DTYPES)
            .zip(self.columns.iter().cloned())
            .map(|((name, dtype), range)| (name, dtype, range))
    }

    /// The whole decoded record, including header and metadata.
    pub fn record_bytes(&self) -> &[u8] {
        &self.data
    }

    /// Returns the sample's JSON metadata object (empty if none was stored).
    pub fn metadata(&self) -> Result<Value> {
        if self.metadata.is_empty() {
            return Ok(json!({}));
        }
        let value: Value = serde_json::from_slice(&self.data[self.metadata.clone()])?;
        valid(
            value.is_object(),
            "KTREMIX sample metadata must be an object",
        )?;
        Ok(value)
    }
}

/// An open `.ktremix` file.
///
/// Opening reads only the header, manifest and sample index, so it is cheap even
/// for large shards. Samples are read and decompressed on demand.
pub struct Shard {
    path: PathBuf,
    header_crc: u32,
    /// Shard manifest: format, compiler and tokenizer details.
    pub manifest: Value,
    /// ID shared by every shard of one dataset.
    pub dataset_id: Uuid,
    /// ID of this shard.
    pub shard_id: Uuid,
    entries: Vec<Entry>,
}

impl Shard {
    /// Opens a shard, rejecting records larger than [`DEFAULT_MAX_RECORD_BYTES`].
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        Self::open_with_limit(path, DEFAULT_MAX_RECORD_BYTES)
    }

    /// Opens a shard with a custom cap on uncompressed record size.
    pub fn open_with_limit(path: impl AsRef<Path>, max_record_bytes: usize) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        let mut file = File::open(&path)?;
        let mut header = [0; HEADER_SIZE];
        file.read_exact(&mut header)?;
        let size = file.metadata()?.len();
        let checksum = number(&header, 112);
        valid(
            &header[..8] == MAGIC
                && number(&header, 8) == 1
                && number(&header, 12) == 128
                && wide(&header, 16) == 0
                && wide(&header, 24) == size
                && wide(&header, 32) == 128
                && number(&header, 64) == 64
                && number(&header, 68) == 64
                && number(&header, 124) == 0,
            "Unsupported KTREMIX header/version/features",
        )?;
        header[112..116].fill(0);
        valid(crc(&header) == checksum, "KTREMIX header checksum mismatch")?;
        let manifest_size = wide(&header, 40);
        let count = wide(&header, 56);
        let index = wide(&header, 48);
        let start = wide(&header, 72);
        let index_size = count
            .checked_mul(64)
            .ok_or_else(|| invalid("KTREMIX index size overflow"))?;
        valid(
            (1..=MAX_MANIFEST as u64).contains(&manifest_size)
                && index == align(128 + manifest_size)?
                && index.checked_add(index_size) == Some(start)
                && start <= size
                && index_size <= usize::MAX as u64,
            "Invalid KTREMIX manifest/index layout",
        )?;
        let mut bytes = vec![0; manifest_size as usize];
        file.read_exact(&mut bytes)?;
        valid(
            crc(&bytes) == number(&header, 116),
            "KTREMIX manifest checksum mismatch",
        )?;
        let manifest: Value = serde_json::from_slice(&bytes)?;
        compatibility(&manifest)?;
        let dataset_id = Uuid::from_bytes(header[80..96].try_into().unwrap());
        let shard_id = Uuid::from_bytes(header[96..112].try_into().unwrap());
        valid(
            uuid(&manifest["dataset_id"])? == dataset_id
                && uuid(&manifest["shard_id"])? == shard_id
                && manifest["sample_count"].as_u64() == Some(count),
            "KTREMIX manifest/header disagree",
        )?;
        file.seek(SeekFrom::Start(index))?;
        let mut reader = std::io::BufReader::new(file);
        let mut entries = Vec::new();
        let mut hasher = crc32fast::Hasher::new();
        let mut buffer = [0; ENTRY_SIZE];
        for _ in 0..count {
            reader.read_exact(&mut buffer)?;
            hasher.update(&buffer);
            entries.push(Entry::decode(&buffer, max_record_bytes, start, size)?);
        }
        valid(
            hasher.finalize() == number(&header, 120),
            "KTREMIX index checksum mismatch",
        )?;
        Ok(Self {
            path,
            header_crc: checksum,
            manifest,
            dataset_id,
            shard_id,
            entries,
        })
    }

    /// Number of samples in the shard.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Returns `true` if the shard holds no samples.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// UUID of sample `index`, or `None` if out of range.
    pub fn sample_id(&self, index: usize) -> Option<Uuid> {
        self.entries.get(index).map(|entry| entry.id)
    }

    /// Reads, decompresses and validates sample `index`.
    pub fn read(&self, index: usize) -> Result<Sample> {
        self.read_block(index, &mut self.open_file()?)?.decode()
    }

    pub(crate) fn open_file(&self) -> Result<File> {
        let mut file = File::open(&self.path)?;
        file.seek(SeekFrom::Start(112))?;
        let mut bytes = [0; 4];
        file.read_exact(&mut bytes)?;
        valid(
            u32::from_le_bytes(bytes) == self.header_crc,
            "KTREMIX shard changed after opening",
        )?;
        Ok(file)
    }

    pub(crate) fn read_block(&self, index: usize, file: &mut File) -> Result<Block> {
        let entry = *self
            .entries
            .get(index)
            .ok_or_else(|| invalid("KTREMIX sample index out of range"))?;
        file.seek(SeekFrom::Start(entry.offset))?;
        let mut bytes = vec![0; entry.stored as usize];
        file.read_exact(&mut bytes)?;
        Ok(Block { entry, bytes })
    }
}

pub(crate) struct Block {
    entry: Entry,
    bytes: Vec<u8>,
}

impl Block {
    pub fn decode(self) -> Result<Sample> {
        let Self { entry, bytes } = self;
        valid(
            crc(&bytes) == entry.checksum,
            "KTREMIX record checksum mismatch",
        )?;
        let data = match entry.codec {
            0 => bytes,
            1 => lz4_flex::block::decompress(&bytes, entry.raw as usize)
                .map_err(|error| invalid(format!("Invalid LZ4 record: {error}")))?,
            _ => {
                let mut decoder = flate2::Decompress::new(true);
                let mut data = vec![0; entry.raw as usize + 1];
                let status = decoder
                    .decompress(&bytes, &mut data, flate2::FlushDecompress::Finish)
                    .map_err(|error| invalid(format!("Invalid zlib record: {error}")))?;
                valid(
                    status == flate2::Status::StreamEnd
                        && decoder.total_in() == bytes.len() as u64
                        && decoder.total_out() == entry.raw as u64,
                    "Invalid bounded zlib record",
                )?;
                data.truncate(entry.raw as usize);
                data
            }
        };
        Sample::decode(data, entry)
    }
}

/// Writes an exact number of samples before atomically publishing a shard.
pub struct ShardWriter {
    path: PathBuf,
    file: tempfile::NamedTempFile,
    count: usize,
    ids: HashSet<Uuid>,
    header: [u8; HEADER_SIZE],
    index_offset: u64,
    data_offset: u64,
    end: u64,
    index_crc: crc32fast::Hasher,
    codec: Codec,
    compatibility: Value,
}

impl ShardWriter {
    /// Starts a shard at `path` that will hold exactly `count` samples.
    ///
    /// Fails if `path` already exists. Nothing is visible at `path` until
    /// [`ShardWriter::finish`] succeeds.
    pub fn new(
        path: impl AsRef<Path>,
        mut manifest: Value,
        count: usize,
        codec: Codec,
    ) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        if path.exists() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::AlreadyExists,
                "KTREMIX output already exists",
            )
            .into());
        }
        valid(manifest.is_object(), "KTREMIX manifest must be an object")?;
        manifest["format"] = json!("ktremix");
        manifest["version"] = json!("1.0");
        manifest["array_schema"] = json!("ktremix.frontier-arrays/1");
        manifest["sample_count"] = json!(count);
        let mut identities = Vec::new();
        for key in ["dataset_id", "shard_id"] {
            let id = if manifest.get(key).is_none() {
                Uuid::new_v4()
            } else {
                uuid(&manifest[key])?
            };
            manifest[key] = json!(id);
            identities.push(id);
        }
        let compatibility = compatibility(&manifest)?;
        let bytes = serde_json::to_vec(&manifest)?;
        valid(
            bytes.len() <= MAX_MANIFEST,
            "KTREMIX manifest exceeds 1 MiB",
        )?;
        let index_offset = align(128 + bytes.len() as u64)?;
        let data_offset = (count as u64)
            .checked_mul(64)
            .and_then(|size| index_offset.checked_add(size))
            .ok_or_else(|| invalid("KTREMIX index size overflow"))?;
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        std::fs::create_dir_all(parent)?;
        let mut file = tempfile::NamedTempFile::new_in(parent)?;
        file.as_file_mut().seek(SeekFrom::Start(128))?;
        file.as_file_mut().write_all(&bytes)?;
        let mut header = [0; HEADER_SIZE];
        put(&mut header, 0, MAGIC);
        put(&mut header, 8, &1u32.to_le_bytes());
        put(&mut header, 12, &128u32.to_le_bytes());
        for (offset, value) in [
            (32, 128),
            (40, bytes.len() as u64),
            (48, index_offset),
            (56, count as u64),
            (72, data_offset),
        ] {
            put(&mut header, offset, &value.to_le_bytes());
        }
        put(&mut header, 64, &64u32.to_le_bytes());
        put(&mut header, 68, &64u32.to_le_bytes());
        put(&mut header, 80, identities[0].as_bytes());
        put(&mut header, 96, identities[1].as_bytes());
        put(&mut header, 116, &crc(&bytes).to_le_bytes());
        Ok(Self {
            path,
            file,
            count,
            ids: HashSet::new(),
            header,
            index_offset,
            data_offset,
            end: data_offset,
            index_crc: crc32fast::Hasher::new(),
            codec,
            compatibility,
        })
    }

    /// Encodes and appends one sample. Fails on a duplicate `id` or when more than `count` samples are pushed.
    pub fn push(&mut self, id: Uuid, columns: &Columns, metadata: Option<&Value>) -> Result<()> {
        valid(
            self.ids.len() < self.count,
            "More trajectories than sample_count",
        )?;
        valid(
            !self.ids.contains(&id),
            "Duplicate trajectory UUID in shard",
        )?;
        let (raw, mut entry) = encode(id, columns, metadata)?;
        let (codec, compressed) = match self.codec {
            Codec::None => (0, Vec::new()),
            Codec::Lz4 => (1, lz4_flex::block::compress(&raw)),
            Codec::Zlib => {
                let mut encoder =
                    flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::fast());
                encoder.write_all(&raw)?;
                (2, encoder.finish()?)
            }
        };
        let bytes = if codec != 0 && compressed.len() < raw.len() {
            entry.codec = codec;
            compressed
        } else {
            raw
        };
        entry.checksum = crc(&bytes);
        entry.offset = self.end;
        entry.stored = bytes.len() as u32;
        let stream = self.file.as_file_mut();
        stream.seek(SeekFrom::Start(self.end))?;
        stream.write_all(&bytes)?;
        let end = align(self.end + bytes.len() as u64)?;
        stream.seek(SeekFrom::Start(
            self.index_offset + self.ids.len() as u64 * 64,
        ))?;
        let index = entry.bytes();
        stream.write_all(&index)?;
        self.index_crc.update(&index);
        self.end = end;
        self.ids.insert(id);
        Ok(())
    }

    /// Verifies that exactly `count` samples were pushed, then atomically publishes the shard.
    pub fn finish(mut self) -> Result<()> {
        valid(
            self.ids.len() == self.count,
            "Fewer trajectories than sample_count",
        )?;
        let file = self.file.as_file_mut();
        let end = self.end.max(self.data_offset);
        file.set_len(end)?;
        put(&mut self.header, 24, &end.to_le_bytes());
        put(
            &mut self.header,
            120,
            &self.index_crc.finalize().to_le_bytes(),
        );
        let checksum = crc(&self.header);
        put(&mut self.header, 112, &checksum.to_le_bytes());
        file.seek(SeekFrom::Start(0))?;
        file.write_all(&self.header)?;
        file.sync_all()?;
        self.file
            .persist_noclobber(self.path)
            .map_err(|error| error.error)?;
        Ok(())
    }
}

fn encode(id: Uuid, a: &Columns, metadata: Option<&Value>) -> Result<(Vec<u8>, Entry)> {
    let rows = a.token_ids.len();
    let frames = a.frame_offsets.len().saturating_sub(1);
    let windows = a.window_slots.len();
    let targets = a.target_rows.len();
    valid(
        (1..=rows).contains(&frames)
            && (1..=rows).contains(&windows)
            && targets <= rows
            && u32::try_from(rows).is_ok()
            && metadata.is_none_or(Value::is_object),
        "Invalid KTREMIX record dimensions or metadata",
    )?;
    let metadata = metadata
        .map(serde_json::to_vec)
        .transpose()?
        .unwrap_or_default();
    let directory_end = RECORD_SIZE + DTYPES.len() * COLUMN_SIZE;
    let mut data = vec![0; align((directory_end + metadata.len()) as u64)? as usize];
    put(&mut data, 0, RECORD_MAGIC);
    put(&mut data, 8, &1u32.to_le_bytes());
    put(&mut data, 12, &64u32.to_le_bytes());
    put(&mut data, 16, id.as_bytes());
    for (index, n) in [16, metadata.len(), rows, frames, windows, targets, 32, 0]
        .into_iter()
        .enumerate()
    {
        let n = u32::try_from(n).map_err(|_| invalid("KTREMIX record exceeds u32 limit"))?;
        put(&mut data, 32 + index * 4, &n.to_le_bytes());
    }
    put(&mut data, directory_end, &metadata);
    let counts = dims(rows, frames, windows, targets);
    macro_rules! column {
        ($index:expr, $values:expr, $bytes:expr) => {{
            let index = $index;
            let values = $values;
            let dtype = DTYPES[index];
            let count = counts[index];
            valid(
                values.len() as u64 == count,
                "Invalid KTREMIX column length",
            )?;
            let offset = align(data.len() as u64)?;
            data.resize(offset as usize, 0);
            let directory = RECORD_SIZE + index * COLUMN_SIZE;
            put(&mut data, directory, &((index + 1) as u16).to_le_bytes());
            data[directory + 2] = dtype;
            data[directory + 3] = 1;
            for (field, value) in [
                (4, offset),
                (12, count),
                (20, count * width(dtype).unwrap()),
            ] {
                put(&mut data, directory + field, &value.to_le_bytes());
            }
            data.extend(values.iter().copied().flat_map($bytes));
        }};
    }
    column!(0, &a.token_ids, u32::to_le_bytes);
    column!(1, &a.window_ids, u32::to_le_bytes);
    column!(2, &a.position_ids, u32::to_le_bytes);
    column!(3, &a.query_frontiers, u64::to_le_bytes);
    column!(4, &a.visible_from, u64::to_le_bytes);
    column!(5, &a.visible_until, u64::to_le_bytes);
    column!(6, &a.previous_rows, u32::to_le_bytes);
    column!(7, &a.loss_weights, |value| (value as f32).to_le_bytes());
    column!(8, &a.row_flags, |value| [value]);
    column!(9, &a.frame_offsets, u64::to_le_bytes);
    column!(10, &a.window_slots, u32::to_le_bytes);
    column!(11, &a.window_open_frontiers, u64::to_le_bytes);
    column!(12, &a.window_close_frontiers, u64::to_le_bytes);
    column!(13, &a.window_activation_frontiers, u64::to_le_bytes);
    column!(14, &a.window_flags, |value| [value]);
    column!(15, &a.target_rows, u32::to_le_bytes);
    data.resize(align(data.len() as u64)? as usize, 0);
    let raw =
        u32::try_from(data.len()).map_err(|_| invalid("KTREMIX record exceeds u32 size limit"))?;
    Ok((
        data,
        Entry {
            id,
            offset: 0,
            stored: raw,
            raw,
            rows: rows as u32,
            frames: frames as u32,
            windows: windows as u32,
            checksum: 0,
            codec: 0,
            targets: targets as u32,
        },
    ))
}

/// Compile bounded batches on native threads and publish only a complete shard.
pub struct CompileWriter {
    writer: Option<ShardWriter>,
    workers: rayon::ThreadPool,
}

impl CompileWriter {
    /// Creates a writer for a shard of exactly `count` samples, compiled by `workers` threads.
    ///
    /// See [`ShardWriter::new`] for the meaning of the other arguments.
    pub fn new(
        path: impl AsRef<Path>,
        manifest: Value,
        count: usize,
        codec: Codec,
        workers: usize,
    ) -> Result<Self> {
        valid(workers > 0, "workers must be positive")?;
        let workers = rayon::ThreadPoolBuilder::new()
            .num_threads(workers)
            .build()
            .map_err(|error| invalid(error.to_string()))?;
        Ok(Self {
            writer: Some(ShardWriter::new(path, manifest, count, codec)?),
            workers,
        })
    }

    /// Compiles a batch of graph sources in parallel and appends them in input order.
    ///
    /// The caller controls the memory bound through the size of each input batch.
    /// A source that fails to compile aborts the shard; `cancelled` is polled
    /// during scheduling.
    pub fn append(
        &mut self,
        compiler: &crate::Compiler,
        sources: Vec<String>,
        cancelled: impl Fn() -> bool + Sync,
    ) -> Result<()> {
        self.append_with(compiler, sources, Ok, cancelled)
    }

    /// Like [`CompileWriter::append`], but each worker reads its own file, so
    /// source text is never held by the caller.
    pub fn append_files<P: AsRef<Path> + Send>(
        &mut self,
        compiler: &crate::Compiler,
        paths: Vec<P>,
        cancelled: impl Fn() -> bool + Sync,
    ) -> Result<()> {
        self.append_with(
            compiler,
            paths,
            |path| Ok(std::fs::read_to_string(path)?),
            cancelled,
        )
    }

    fn append_with<T: Send>(
        &mut self,
        compiler: &crate::Compiler,
        items: Vec<T>,
        load: impl Fn(T) -> Result<String> + Sync,
        cancelled: impl Fn() -> bool + Sync,
    ) -> Result<()> {
        let mut writer = self
            .writer
            .take()
            .ok_or_else(|| invalid("KTREMIX writer is closed"))?;
        valid(
            writer.compatibility == compatibility(&compiler.manifest())?,
            "Shard and compiler tokenizer/control IDs/execution contract disagree",
        )?;
        use rayon::prelude::*;
        let compiled: Result<Vec<_>> = self.workers.install(|| {
            items
                .into_par_iter()
                .map(|item| {
                    if cancelled() {
                        return Err(crate::Error::Cancelled);
                    }
                    let source = load(item)?;
                    let result = compiler.compile_cancellable(&source, &cancelled)?;
                    Ok((result.id, result.columns, result.source_metadata))
                })
                .collect()
        });
        for (id, columns, metadata) in compiled? {
            if cancelled() {
                return Err(crate::Error::Cancelled);
            }
            writer.push(id, &columns, metadata.as_ref())?;
        }
        self.writer = Some(writer);
        Ok(())
    }

    /// Publishes the shard once every expected sample has been appended.
    pub fn finish(self) -> Result<()> {
        self.writer
            .ok_or_else(|| invalid("KTREMIX writer is closed"))?
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::runtime::{BOS, DURABLE, FOREVER, INITIALLY_AVAILABLE, MODEL, NONE, NOOP};

    #[test]
    fn write_atomic_shards_and_read_each_codec() -> Result<()> {
        let temporary = tempfile::tempdir()?;
        let manifest = json!({
            "execution_contract": crate::CONTRACT,
            "tokenizer": {
                "sha256": "0".repeat(64),
                "special_token_ids": {"bos": 1, "eos": 2, "noop": 3},
            },
        });
        let columns = Columns {
            token_ids: vec![1, 3],
            window_ids: vec![0, 0],
            position_ids: vec![0, 1],
            query_frontiers: vec![0, 1],
            visible_from: vec![1, 0],
            visible_until: vec![FOREVER, 0],
            previous_rows: vec![NONE, 0],
            loss_weights: vec![0.0, 1.0],
            row_flags: vec![BOS | DURABLE, NOOP | MODEL],
            frame_offsets: vec![0, 2],
            window_slots: vec![0],
            window_open_frontiers: vec![0],
            window_close_frontiers: vec![FOREVER],
            window_activation_frontiers: vec![FOREVER],
            window_flags: vec![INITIALLY_AVAILABLE],
            target_rows: vec![1],
        };
        for (ordinal, codec) in [Codec::None, Codec::Lz4, Codec::Zlib]
            .into_iter()
            .enumerate()
        {
            let path = temporary.path().join(format!("{ordinal}.ktremix"));
            let mut writer = ShardWriter::new(&path, manifest.clone(), 1, codec)?;
            let id = Uuid::new_v4();
            writer.push(id, &columns, Some(&json!({"ordinal": ordinal})))?;
            assert!(!path.exists());
            writer.finish()?;
            let reader = Shard::open(&path)?;
            let sample = reader.read(0)?;
            assert_eq!(sample.id, id);
            assert_eq!(sample.metadata()?, json!({"ordinal": ordinal}));
            assert_eq!(
                sample.column("token_ids"),
                Some(&[1, 0, 0, 0, 3, 0, 0, 0][..])
            );
            assert!(ShardWriter::new(&path, manifest.clone(), 1, codec).is_err());
        }
        Ok(())
    }
}
