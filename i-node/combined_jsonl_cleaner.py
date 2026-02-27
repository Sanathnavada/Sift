#!/usr/bin/env python3
"""
combined_jsonl_cleaner.py

Combines:
 - JSONL validator & cleaner (structure, user/assistant presence, logging, backups)
 - Quick-cleaner (remove convos missing user/assistant)
 - Aggressive disjoint deduper (message-hash based)
 - Strict English filter (kanglish blocklist + langdetect)

Usage examples:
  # Validate + write cleaned file (defaults)
  python combined_jsonl_cleaner.py input.jsonl --output cleaned.jsonl

  # Run full pipeline: validate -> repair -> dedupe -> english filter
  python combined_jsonl_cleaner.py input.jsonl --output cleaned.jsonl --pipeline

  # Dry run (no writes)
  python combined_jsonl_cleaner.py input.jsonl --dry-run --pipeline

  # Only run dedupe step
  python combined_jsonl_cleaner.py input.jsonl --output deduped.jsonl --dedupe --dedupe-threshold 0.5

Requirements:
  pip install langdetect
"""

import argparse
import json
import logging
import shutil
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Tuple, Set

import re

# Optional import; if missing, the english filter will notify user
try:
    from langdetect import detect, LangDetectException
    LANGDETECT_AVAILABLE = True
except Exception:
    LANGDETECT_AVAILABLE = False

# --------------------------
# Config: Kanglish blocklist & safe short words (from original english filter)
# --------------------------
KANGLISH_BLOCKLIST = {
    'guru', 'masth', 'sari', 'aithu', 'aythu', 'illa', 'illva', 'ilva',
    'hu', 'huu', 'houdu', 'howdu', 'hange', 'hinge', 'yen', 'yenu',
    'yake', 'yaake', 'heli', 'helu', 'nange', 'ninge', 'namge', 'nam',
    'ninu', 'neenu', 'naanu', 'nanu', 'ba', 'baa', 'banni', 'hogu',
    'beku', 'beda', 'kodi', 'kodu', 'maadi', 'maadu', 'nodi', 'nodu',
    'ivaga', 'avaga', 'yavaga', 'elli', 'hege', 'estu', 'astu', 'kane'
}
SAFE_SHORT_WORDS = {
    'ok', 'okay', 'k', 'kk', 'lol', 'lmao', 'rofl', 'cool', 'yeah', 'yep',
    'yes', 'no', 'nope', 'nah', 'hm', 'hmm', 'hmmm', 'wow', 'nice',
    'thx', 'thanks', 'tysm', 'great', 'good', 'bad', 'why', 'what',
    'who', 'when', 'where', 'how', 'really', 'sure', 'fine', 'done'
}

# --------------------------
# Validation & Stats dataclass (adapted from validate_jsonl.py)
# --------------------------
@dataclass
class ValidationStats:
    total_conversations: int = 0
    valid_conversations: int = 0
    invalid_no_user: int = 0
    invalid_no_assistant: int = 0
    invalid_malformed: int = 0
    invalid_empty_messages: int = 0
    invalid_samples: List[Dict] = field(default_factory=list)

    def record_invalid(self, line_num: int, reason: str, entry: Dict, max_samples: int = 10):
        if len(self.invalid_samples) < max_samples:
            sample = {
                'line': line_num,
                'reason': reason,
                'roles': [msg.get('role') for msg in entry.get('messages', [])] if isinstance(entry, dict) else None,
                'message_count': len(entry.get('messages', [])) if isinstance(entry, dict) else None
            }
            self.invalid_samples.append(sample)

    @property
    def validation_rate(self) -> float:
        return round(self.valid_conversations / max(1, self.total_conversations), 3)

    def to_dict(self):
        return {
            'total_conversations': self.total_conversations,
            'valid_conversations': self.valid_conversations,
            'invalid_conversations': {
                'no_user_message': self.invalid_no_user,
                'no_assistant_message': self.invalid_no_assistant,
                'malformed_structure': self.invalid_malformed,
                'empty_messages': self.invalid_empty_messages,
                'total': (self.invalid_no_user + self.invalid_no_assistant +
                          self.invalid_malformed + self.invalid_empty_messages)
            },
            'validation_rate': self.validation_rate,
            'invalid_samples': self.invalid_samples
        }

# --------------------------
# Conversation Validator (adapted)
# --------------------------
class ConversationValidator:
    def __init__(self, stats: ValidationStats, logger: logging.Logger):
        self.stats = stats
        self.logger = logger

    def validate_entry(self, entry: Dict, line_num: int) -> Tuple[bool, str]:
        if not isinstance(entry, dict):
            self.stats.invalid_malformed += 1
            self.stats.record_invalid(line_num, "entry_not_object", {})
            return False, "entry is not a JSON object"

        if 'messages' not in entry:
            self.stats.invalid_malformed += 1
            self.stats.record_invalid(line_num, "missing_messages_field", entry)
            return False, "missing 'messages' field"

        messages = entry['messages']
        if not isinstance(messages, list):
            self.stats.invalid_malformed += 1
            self.stats.record_invalid(line_num, "messages_not_list", entry)
            return False, "'messages' is not a list"

        if len(messages) < 2:
            self.stats.invalid_empty_messages += 1
            self.stats.record_invalid(line_num, "insufficient_messages", entry)
            return False, f"only {len(messages)} message(s)"

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                self.stats.invalid_malformed += 1
                self.stats.record_invalid(line_num, f"message_{i}_not_dict", entry)
                return False, f"message {i} is not a dict"
            if 'role' not in msg or 'content' not in msg:
                self.stats.invalid_malformed += 1
                self.stats.record_invalid(line_num, f"message_{i}_missing_fields", entry)
                return False, f"message {i} missing role/content"
            if not isinstance(msg['content'], str):
                self.stats.invalid_malformed += 1
                self.stats.record_invalid(line_num, f"message_{i}_invalid_content", entry)
                return False, f"message {i} content not string"

        roles = [msg['role'] for msg in messages]
        if 'user' not in roles:
            self.stats.invalid_no_user += 1
            self.stats.record_invalid(line_num, "no_user_message", entry)
            return False, "no 'user' message found"
        if 'assistant' not in roles:
            self.stats.invalid_no_assistant += 1
            self.stats.record_invalid(line_num, "no_assistant_message", entry)
            return False, "no 'assistant' message found"

        return True, ""

# --------------------------
# Helpers: backup & logging setup
# --------------------------
def create_backup(file_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = file_path.parent / f"{file_path.stem}_backup_{timestamp}{file_path.suffix}"
    shutil.copy2(file_path, backup_path)
    return backup_path

def setup_logging(log_file: Path, verbose: bool) -> logging.Logger:
    logger = logging.getLogger('combined_cleaner')
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    # Avoid adding multiple handlers when called repeatedly
    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        console_formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_formatter)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
    return logger

# --------------------------
# Repair function: fix "assistant as first after system"
# --------------------------
def repair_structure(messages: List[Dict]) -> Tuple[List[Dict], bool]:
    repaired = False
    msgs = list(messages)
    if len(msgs) > 1 and msgs[0].get('role') == 'system' and msgs[1].get('role') == 'assistant':
        msgs.pop(1)
        repaired = True
    return msgs, repaired

# --------------------------
# Dedupe: sliding-window / disjoint cleaning
# --------------------------
def get_message_hash(content: str) -> str:
    clean_text = content.lower().strip()
    return hashlib.md5(clean_text.encode('utf-8')).hexdigest()

def dedupe_rows(entries: List[Dict], threshold: float = 0.5, logger: logging.Logger = None) -> Tuple[List[Dict], Dict]:
    """
    Keeps entries where overlap (old messages fraction) <= threshold.
    threshold is fraction overlap allowed (original used >0.5 to drop).
    """
    global_seen_messages: Set[str] = set()
    kept = []
    stats = {'total': len(entries), 'kept': 0, 'dropped_repetition': 0, 'repaired_start': 0, 'dropped_structure': 0}

    for entry in entries:
        messages = entry.get('messages', [])
        # repair
        msgs_before = len(messages)
        messages, repaired = repair_structure(messages)
        if repaired:
            stats['repaired_start'] += 1

        if len(messages) < 3:
            stats['dropped_structure'] += 1
            continue

        new_content_count = 0
        total_content_count = 0
        row_hashes = []
        for msg in messages:
            if msg.get('role') == 'system':
                continue
            h = get_message_hash(msg.get('content', ''))
            row_hashes.append(h)
            if h not in global_seen_messages:
                new_content_count += 1
            total_content_count += 1

        if total_content_count == 0:
            stats['dropped_repetition'] += 1
            continue

        overlap_ratio = 1.0 - (new_content_count / total_content_count)
        if overlap_ratio > threshold:
            stats['dropped_repetition'] += 1
            continue

        # keep and mark seen
        for h in row_hashes:
            global_seen_messages.add(h)
        kept.append(entry)
        stats['kept'] += 1

    return kept, stats

# --------------------------
# English detection / filter
# --------------------------
def is_pure_english(text: str) -> bool:
    clean_text = (text or "").lower().strip()
    words = re.findall(r'\b\w+\b', clean_text)
    if not words:
        return False
    if any(w in KANGLISH_BLOCKLIST for w in words):
        return False
    if len(words) <= 3:
        if any(w in SAFE_SHORT_WORDS for w in words):
            return True
    if not LANGDETECT_AVAILABLE:
        # fallback: if langdetect not installed, be conservative and keep
        return True
    try:
        if len(clean_text) < 3:
            return True
        lang = detect(clean_text)
        if lang != 'en':
            return False
    except LangDetectException:
        return True
    return True

def english_filter(entries: List[Dict], drop_row_on_non_english: bool = True, logger: logging.Logger = None) -> Tuple[List[Dict], Dict]:
    kept = []
    stats = {'total': len(entries), 'kept': 0, 'dropped_kanglish': 0, 'dropped_empty': 0}
    for entry in entries:
        messages = entry.get('messages', [])
        new_msgs = []
        if messages and messages[0].get('role') == 'system':
            new_msgs.append(messages[0])
        valid_conversation = True
        start_idx = 1 if (messages and messages[0].get('role') == 'system') else 0
        temp_convo_msgs = []
        for i in range(start_idx, len(messages)):
            msg = messages[i]
            content = msg.get('content', '')
            if not content.strip():
                continue
            if is_pure_english(content):
                temp_convo_msgs.append(msg)
            else:
                if drop_row_on_non_english:
                    valid_conversation = False
                    break
                # else: skip this message (strip it)
        if valid_conversation and len(temp_convo_msgs) >= 2:
            new_msgs.extend(temp_convo_msgs)
            # ensure system not followed by assistant
            if len(new_msgs) > 1 and new_msgs[1].get('role') == 'assistant':
                new_msgs.pop(1)
            if len(new_msgs) >= 2:
                kept.append({'messages': new_msgs})
                stats['kept'] += 1
            else:
                stats['dropped_empty'] += 1
        else:
            stats['dropped_kanglish'] += 1
    return kept, stats

# --------------------------
# High-level pipeline orchestrator
# --------------------------
def run_pipeline(input_path: Path, output_path: Path, *,
                 do_validate: bool = True,
                 do_backup: bool = True,
                 do_repair: bool = True,
                 do_dedupe: bool = True,
                 dedupe_threshold: float = 0.5,
                 do_english_filter: bool = True,
                 english_drop_row: bool = True,
                 dry_run: bool = False,
                 logger: logging.Logger = None) -> Dict:
    if logger is None:
        logger = logging.getLogger('combined_cleaner')

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if do_backup and not dry_run:
        backup_path = create_backup(input_path)
        logger.info(f"Backup created at: {backup_path}")

    # Step 0: read input and validate
    raw_entries = []
    stats = ValidationStats()
    validator = ConversationValidator(stats, logger)

    with open(input_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            stats.total_conversations += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                stats.invalid_malformed += 1
                logger.warning(f"Line {line_num}: JSON decode error - {e}")
                continue

            if do_validate:
                is_valid, reason = validator.validate_entry(entry, line_num)
                if is_valid:
                    stats.valid_conversations += 1
                    raw_entries.append(entry)
                else:
                    logger.warning(f"Line {line_num}: INVALID - {reason}")
            else:
                raw_entries.append(entry)

    logger.info(f"Read {len(raw_entries)} valid-ish entries after validation step (total input lines: {stats.total_conversations})")

    # Optionally repair structure for all entries
    if do_repair:
        repaired_count = 0
        for e in raw_entries:
            msgs, repaired = repair_structure(e.get('messages', []))
            if repaired:
                e['messages'] = msgs
                repaired_count += 1
        logger.info(f"Repaired structure in {repaired_count} rows")

    # Dedupe
    processed_entries = raw_entries
    dedupe_stats = {}
    if do_dedupe:
        processed_entries, dedupe_stats = dedupe_rows(processed_entries, threshold=dedupe_threshold, logger=logger)
        logger.info(f"Dedupe: kept {dedupe_stats.get('kept',0)} / {dedupe_stats.get('total',0)}")

    # English filter
    english_stats = {}
    if do_english_filter:
        processed_entries, english_stats = english_filter(processed_entries, drop_row_on_non_english=english_drop_row, logger=logger)
        logger.info(f"English filter: kept {english_stats.get('kept',0)} / {english_stats.get('total',0)}")

    # Final write
    if dry_run:
        logger.info("Dry run; not writing output file.")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f_out:
            for row in processed_entries:
                f_out.write(json.dumps(row, ensure_ascii=False) + '\n')
        logger.info(f"Wrote {len(processed_entries)} rows to {output_path}")

    # collate stats
    results = {
        'validation_stats': stats.to_dict(),
        'dedupe_stats': dedupe_stats,
        'english_stats': english_stats,
        'final_count': len(processed_entries)
    }
    return results

# --------------------------
# CLI
# --------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Combined JSONL Validation & Cleaning Pipeline")
    p.add_argument('input', type=Path, help='Input JSONL file')
    p.add_argument('--output', type=Path, help='Output JSONL file (default: <input>_cleaned.jsonl)')
    p.add_argument('--log', type=Path, default=Path('combined_cleaner.log'), help='Log file path')
    p.add_argument('--dry-run', action='store_true', help="Validate / simulate but do not write outputs or create backups")
    p.add_argument('--no-backup', dest='backup', action='store_false', help='Do not create a backup')
    # steps
    p.add_argument('--no-validate', dest='validate', action='store_false', help='Skip validation step')
    p.add_argument('--no-repair', dest='repair', action='store_false', help='Skip automatic structure repair')
    p.add_argument('--no-dedupe', dest='dedupe', action='store_false', help='Skip deduplication step')
    p.add_argument('--dedupe-threshold', type=float, default=0.5, help='Overlap threshold for dedupe (default 0.5)')
    p.add_argument('--no-english', dest='english', action='store_false', help='Skip english filter')
    p.add_argument('--english-strip-instead', dest='english_strip', action='store_true',
                   help='Instead of dropping rows with non-English messages, remove those messages (if possible)')
    p.add_argument('--pipeline', action='store_true', help='Run default full pipeline (validate, repair, dedupe, english)')
    p.add_argument('--verbose', action='store_true', help='Verbose logging')
    return p.parse_args()

def main():
    args = parse_args()
    input_path: Path = args.input
    output_path: Path = args.output or input_path.parent / f"{input_path.stem}_cleaned{input_path.suffix}"
    logger = setup_logging(args.log, args.verbose)

    # If user asked for pipeline, enable steps (overrides some flags)
    if args.pipeline:
        do_validate = True
        do_repair = True
        do_dedupe = True
        do_english = True
    else:
        do_validate = args.validate
        do_repair = args.repair
        do_dedupe = args.dedupe
        do_english = args.english

    if do_english and not LANGDETECT_AVAILABLE:
        logger.warning("langdetect not installed — english filtering will use lightweight heuristics only. "
                       "Install with: pip install langdetect for better accuracy.")

    logger.info("Starting pipeline...")
    try:
        results = run_pipeline(
            input_path,
            output_path,
            do_validate=do_validate,
            do_backup=(args.backup and not args.dry_run),
            do_repair=do_repair,
            do_dedupe=do_dedupe,
            dedupe_threshold=args.dedupe_threshold,
            do_english_filter=do_english,
            english_drop_row=not args.english_strip,
            dry_run=args.dry_run,
            logger=logger
        )
        # Save results/stats
        stats_file = output_path.parent / f"{output_path.stem}_stats.json"
        if not args.dry_run:
            with open(stats_file, 'w', encoding='utf-8') as sf:
                json.dump(results, sf, indent=2, ensure_ascii=False)
            logger.info(f"Saved stats to {stats_file}")
        else:
            logger.info("Dry run — no stats file written.")

        logger.info("Done.")
    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        raise

if __name__ == '__main__':
    main()
